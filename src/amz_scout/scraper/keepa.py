"""Keepa API integration for price history data.

Uses direct HTTP requests instead of the keepa Python library because:
1. The library doesn't support AU (domain=10)
2. The library tries to pass parameters not available on all API plans
3. Direct requests give us full control over the API call
"""

import logging
import os
import time
from pathlib import Path

import requests

from amz_scout.models import PriceHistory, Product
from amz_scout.utils import cents_to_price, today_iso

logger = logging.getLogger(__name__)

API_BASE = "https://api.keepa.com"

# Keepa epoch: minutes offset from Unix epoch
_KEEPA_EPOCH_OFFSET = 21564000


def _load_dotenv() -> None:
    """Load .env file from project root if it exists."""
    for d in [Path.cwd(), Path(__file__).parent.parent.parent.parent]:
        env_file = d / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break


class KeepaClient:
    """Direct HTTP wrapper for the Keepa API."""

    def __init__(self, api_key: str | None = None) -> None:
        _load_dotenv()
        self._key = api_key or os.environ.get("KEEPA_API_KEY", "")
        if not self._key:
            raise ValueError(
                "Keepa API key required. Create a .env file with KEEPA_API_KEY=your_key "
                "or set the KEEPA_API_KEY environment variable."
            )
        self._tokens_left = -1
        self._refill_rate = 1  # tokens per minute
        logger.info("Keepa API initialized (direct HTTP mode)")

    @property
    def tokens_left(self) -> int:
        if self._tokens_left == -1:
            self._check_tokens()
        return self._tokens_left

    def _check_tokens(self) -> None:
        resp = requests.get(f"{API_BASE}/token", params={"key": self._key}, timeout=10)
        data = resp.json()
        self._tokens_left = data.get("tokensLeft", 0)
        self._refill_rate = data.get("refillRate", 1)

    def _wait_for_tokens(self, needed: int) -> None:
        """Wait if we don't have enough tokens."""
        if self._tokens_left >= needed:
            return
        wait_mins = (needed - self._tokens_left) / max(self._refill_rate, 1)
        wait_secs = int(wait_mins * 60) + 5
        logger.info("Waiting %ds for token refill (%d needed, %d available)",
                     wait_secs, needed, self._tokens_left)
        time.sleep(wait_secs)
        self._check_tokens()

    def fetch_price_history(
        self,
        products: list[Product],
        site: str,
        keepa_domain: str,
        keepa_domain_code: int | None = None,
    ) -> list[PriceHistory]:
        """Fetch price history for products via Keepa API.

        Products are queried individually (1 token each) to maximize reliability.
        """
        domain_code = keepa_domain_code
        if domain_code is None:
            logger.error("[%s] No Keepa domain code configured", site)
            return [_empty_history(p, site) for p in products]

        valid_pairs = [
            (p.asin_for(site), p) for p in products
            if p.asin_for(site) and len(p.asin_for(site)) == 10
        ]
        if not valid_pairs:
            return []

        logger.info("[%s] Fetching Keepa data for %d products (domain=%d)",
                     site, len(valid_pairs), domain_code)

        self._check_tokens()
        self._wait_for_tokens(len(valid_pairs))

        results = []
        for asin, product in valid_pairs:
            history = self._fetch_one(asin, product, site, domain_code)
            results.append(history)
            time.sleep(0.5)

        logger.info("[%s] Keepa tokens remaining: %d", site, self._tokens_left)
        return results

    def _fetch_one(
        self, asin: str, product: Product, site: str, domain_code: int, max_retries: int = 2
    ) -> PriceHistory:
        """Fetch one product with retry on 429 and JSON parse protection."""
        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(
                    f"{API_BASE}/product",
                    params={
                        "key": self._key,
                        "domain": domain_code,
                        "asin": asin,
                        "stats": 90,
                        "offers": 20,
                        "buybox": 1,
                    },
                    timeout=30,
                )

                # Handle 429 — wait for refill and retry
                if resp.status_code == 429:
                    if attempt < max_retries:
                        refill_in = 65  # Default wait
                        try:
                            refill_in = resp.json().get("refillIn", 65000) // 1000 + 5
                        except (ValueError, KeyError):
                            pass
                        logger.info("[%s] 429 for %s, waiting %ds (attempt %d/%d)",
                                     site, asin, refill_in, attempt + 1, max_retries)
                        time.sleep(refill_in)
                        continue
                    logger.warning("[%s] 429 for %s after %d retries", site, asin, max_retries)
                    return _empty_history(product, site)

                # Parse JSON safely
                try:
                    data = resp.json()
                except (ValueError, requests.exceptions.JSONDecodeError):
                    logger.warning("[%s] Non-JSON response for %s (status=%d)",
                                    site, asin, resp.status_code)
                    return _empty_history(product, site)

                self._tokens_left = data.get("tokensLeft", self._tokens_left)

                if resp.status_code != 200 or "error" in data:
                    logger.warning("[%s] Keepa error for %s: %s",
                                    site, asin, data.get("error", resp.status_code))
                    return _empty_history(product, site)

                raw_products = data.get("products", [])
                if not raw_products:
                    return _empty_history(product, site)

                history = _parse_product(product, site, raw_products[0])
                logger.debug("[%s] %s: buybox=%s, amz=%s",
                              site, product.model, history.buybox_current, history.amz_current)
                return history

            except requests.exceptions.RequestException as e:
                logger.error("[%s] Network error for %s: %s", site, asin, e)
                return _empty_history(product, site)
            except Exception as e:
                logger.error("[%s] Unexpected error for %s: %s", site, asin, e)
                return _empty_history(product, site)

        return _empty_history(product, site)


def _parse_product(product: Product, site: str, raw: dict) -> PriceHistory:
    """Parse Keepa product response using pre-computed stats (Pro plan).

    Uses stats=90 for min/max/avg, offers for seller count,
    buybox for Buy Box info, and monthlySoldHistory for sales volume.
    """
    stats = raw.get("stats", {})
    csv_data = raw.get("csv", [])

    # ─── Price stats (pre-computed by Keepa, much more reliable than manual calc) ───
    cur = stats.get("current", [])
    avg90 = stats.get("avg90", [])
    mn = stats.get("min", [])
    mx = stats.get("max", [])

    def s_cur(idx: int) -> float | None:
        return cents_to_price(cur[idx]) if len(cur) > idx else None

    def s_avg(idx: int) -> float | None:
        return cents_to_price(avg90[idx]) if len(avg90) > idx else None

    def s_min(idx: int) -> float | None:
        if len(mn) > idx and mn[idx]:
            v = mn[idx]
            return cents_to_price(v[1]) if isinstance(v, (list, tuple)) and len(v) >= 2 else cents_to_price(v)
        return None

    def s_max(idx: int) -> float | None:
        if len(mx) > idx and mx[idx]:
            v = mx[idx]
            return cents_to_price(v[1]) if isinstance(v, (list, tuple)) and len(v) >= 2 else cents_to_price(v)
        return None

    # Index: 0=Amazon, 1=New3P, 3=SalesRank (not a price)
    # Buy Box: derive from Amazon or New 3P
    amz_cur, new_cur = s_cur(0), s_cur(1)
    bb_cur = amz_cur or new_cur

    # ─── Sales rank from csv[3] (more current than stats) ───
    sales_rank = None
    if len(csv_data) > 3 and csv_data[3]:
        rank_arr = csv_data[3]
        if len(rank_arr) >= 2 and rank_arr[-1] != -1:
            sales_rank = int(rank_arr[-1])

    # ─── Monthly sold (Keepa exclusive — precise unit count) ───
    monthly_sold = None
    msh = raw.get("monthlySoldHistory", [])
    if msh and len(msh) >= 2 and msh[-1] != -1:
        monthly_sold = int(msh[-1])

    # ─── Buy Box info (from stats + buybox=1) ───
    bb_is_amazon = str(stats.get("buyBoxIsAmazon", ""))
    bb_is_fba = str(stats.get("buyBoxIsFBA", ""))
    bb_seller = stats.get("buyBoxSellerId", "")

    # ─── Seller count (from offers) ───
    offers = raw.get("offers", [])
    seller_count = len(offers) if offers else None
    fba_count = sum(1 for o in offers if o.get("isFBA")) if offers else None

    return PriceHistory(
        date=today_iso(),
        site=site,
        category=product.category,
        brand=product.brand,
        model=product.model,
        asin=product.asin_for(site),
        buybox_current=bb_cur,
        buybox_lowest=s_min(0) if s_min(0) else s_min(1),
        buybox_highest=s_max(0) if s_max(0) else s_max(1),
        buybox_avg90=s_avg(0) if s_avg(0) else s_avg(1),
        amz_current=amz_cur,
        amz_lowest=s_min(0),
        amz_highest=s_max(0),
        amz_avg90=s_avg(0),
        new_current=new_cur,
        new_lowest=s_min(1),
        new_highest=s_max(1),
        new_avg90=s_avg(1),
        sales_rank=sales_rank,
        monthly_sold=monthly_sold,
        buybox_is_amazon=bb_is_amazon,
        buybox_is_fba=bb_is_fba,
        buybox_seller_id=bb_seller or "",
        seller_count=seller_count,
        fba_seller_count=fba_count,
    )


def _empty_history(product: Product, site: str) -> PriceHistory:
    """Create an empty PriceHistory for a product with no data."""
    return PriceHistory(
        date=today_iso(),
        site=site,
        category=product.category,
        brand=product.brand,
        model=product.model,
        asin=product.asin_for(site),
    )
