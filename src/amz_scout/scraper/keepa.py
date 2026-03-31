"""Keepa API integration for price history data.

Strategy:
- Default mode (1 token/product): basic query → csv[] + monthlySoldHistory + buyBoxSellerIdHistory
  All stats (min/max/avg/current) computed locally from the raw csv arrays.
- Detailed mode (--detailed, ~5 tokens/product): adds offers + stats + buybox parameters
  for seller count, FBA breakdown, and pre-computed stats.
"""

import json as json
import logging
import os
import time
from pathlib import Path

import requests

from amz_scout.models import PriceHistory, Product
from amz_scout.utils import cents_to_price, today_iso

logger = logging.getLogger(__name__)

API_BASE = "https://api.keepa.com"


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
        self._refill_rate = 1
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
        detailed: bool = False,
        raw_dir: Path | None = None,
    ) -> list[PriceHistory]:
        """Fetch price history for products.

        Args:
            detailed: If True, request offers+stats+buybox (~5 tokens/product).
            raw_dir: If provided, save raw Keepa JSON responses to this directory.
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

        mode = "detailed (~5 tok/product)" if detailed else "basic (1 tok/product)"
        logger.info("[%s] Fetching Keepa data for %d products [%s]",
                     site, len(valid_pairs), mode)

        self._check_tokens()
        tokens_per = 5 if detailed else 1
        self._wait_for_tokens(len(valid_pairs) * tokens_per)

        results = []
        for asin, product in valid_pairs:
            history = self._fetch_one(asin, product, site, domain_code, detailed, raw_dir)
            results.append(history)
            time.sleep(0.5)

        logger.info("[%s] Keepa tokens remaining: %d", site, self._tokens_left)
        return results

    def _fetch_one(
        self,
        asin: str,
        product: Product,
        site: str,
        domain_code: int,
        detailed: bool = False,
        raw_dir: Path | None = None,
        max_retries: int = 2,
    ) -> PriceHistory:
        """Fetch one product with retry on 429."""
        params: dict = {"key": self._key, "domain": domain_code, "asin": asin}
        if detailed:
            params.update({"stats": 90, "offers": 20, "buybox": 1})

        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(f"{API_BASE}/product", params=params, timeout=30)

                if resp.status_code == 429:
                    if attempt < max_retries:
                        refill_in = 65
                        try:
                            refill_in = resp.json().get("refillIn", 65000) // 1000 + 5
                        except (ValueError, KeyError):
                            pass
                        logger.info("[%s] 429 for %s, waiting %ds (attempt %d/%d)",
                                     site, asin, refill_in, attempt + 1, max_retries)
                        time.sleep(refill_in)
                        continue
                    return _empty_history(product, site)

                try:
                    data = resp.json()
                except (ValueError, requests.exceptions.JSONDecodeError):
                    logger.warning("[%s] Non-JSON response for %s", site, asin)
                    return _empty_history(product, site)

                self._tokens_left = data.get("tokensLeft", self._tokens_left)

                if resp.status_code != 200 or "error" in data:
                    logger.warning("[%s] Keepa error for %s: %s",
                                    site, asin, data.get("error", resp.status_code))
                    return _empty_history(product, site)

                raw_products = data.get("products", [])
                if not raw_products:
                    return _empty_history(product, site)

                # Save raw JSON
                if raw_dir:
                    _save_raw(raw_dir, site, asin, raw_products[0])

                history = _parse_product(product, site, raw_products[0], detailed)
                logger.debug("[%s] %s: bb=%s amz=%s new=%s sold=%s",
                              site, product.model, history.buybox_current,
                              history.amz_current, history.new_current, history.monthly_sold)
                return history

            except requests.exceptions.RequestException as e:
                logger.error("[%s] Network error for %s: %s", site, asin, e)
                return _empty_history(product, site)
            except Exception as e:
                logger.error("[%s] Unexpected error for %s: %s", site, asin, e)
                return _empty_history(product, site)

        return _empty_history(product, site)


# ─── Raw data storage ───────────────────────────────────────────────────


def _save_raw(raw_dir: Path, site: str, asin: str, product_data: dict) -> None:
    """Save raw Keepa product JSON for future re-analysis."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{site.lower()}_{asin}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(product_data, f, ensure_ascii=False, separators=(",", ":"))
    logger.debug("Saved raw JSON: %s", path)


# ─── Parsing ────────────────────────────────────────────────────────────


def _parse_product(product: Product, site: str, raw: dict, detailed: bool) -> PriceHistory:
    """Parse Keepa product response into PriceHistory.

    In basic mode: compute stats from csv[] arrays.
    In detailed mode: use pre-computed stats from API + offers data.
    """
    csv_data = raw.get("csv", [])

    # ─── Price data ───
    if detailed and raw.get("stats"):
        amazon, new_3p = _prices_from_stats(raw["stats"])
    else:
        amazon, new_3p = _prices_from_csv(csv_data)

    # Buy Box = Amazon if available, else New 3P
    bb = {
        "current": amazon["current"] or new_3p["current"],
        "lowest": amazon["lowest"] or new_3p["lowest"],
        "highest": amazon["highest"] or new_3p["highest"],
        "avg90": amazon["avg90"] or new_3p["avg90"],
    }

    # ─── Sales rank from csv[3] ───
    sales_rank = _latest_value(csv_data, 3, as_int=True)

    # ─── Monthly sold (always available in basic query) ───
    monthly_sold = None
    msh = raw.get("monthlySoldHistory", [])
    if msh and len(msh) >= 2 and msh[-1] != -1:
        monthly_sold = int(msh[-1])

    # ─── Buy Box info ───
    stats = raw.get("stats", {})
    bb_is_amazon = str(stats.get("buyBoxIsAmazon", "")) if stats else ""
    bb_is_fba = str(stats.get("buyBoxIsFBA", "")) if stats else ""
    bb_seller = stats.get("buyBoxSellerId", "") if stats else ""

    # Fallback: extract from buyBoxSellerIdHistory if stats not available
    if not bb_seller:
        bbh = raw.get("buyBoxSellerIdHistory", [])
        if bbh and len(bbh) >= 2:
            bb_seller = bbh[-1] if isinstance(bbh[-1], str) else ""

    # ─── Seller count (detailed mode only, from offers) ───
    seller_count = None
    fba_count = None
    if detailed:
        offers = raw.get("offers", [])
        if offers:
            seller_count = len(offers)
            fba_count = sum(1 for o in offers if o.get("isFBA"))

    return PriceHistory(
        date=today_iso(),
        site=site,
        category=product.category,
        brand=product.brand,
        model=product.model,
        asin=product.asin_for(site),
        buybox_current=bb["current"],
        buybox_lowest=bb["lowest"],
        buybox_highest=bb["highest"],
        buybox_avg90=bb["avg90"],
        amz_current=amazon["current"],
        amz_lowest=amazon["lowest"],
        amz_highest=amazon["highest"],
        amz_avg90=amazon["avg90"],
        new_current=new_3p["current"],
        new_lowest=new_3p["lowest"],
        new_highest=new_3p["highest"],
        new_avg90=new_3p["avg90"],
        sales_rank=sales_rank,
        monthly_sold=monthly_sold,
        buybox_is_amazon=bb_is_amazon,
        buybox_is_fba=bb_is_fba,
        buybox_seller_id=bb_seller or "",
        seller_count=seller_count,
        fba_seller_count=fba_count,
    )


def _prices_from_csv(csv_data: list) -> tuple[dict, dict]:
    """Compute current/lowest/highest/avg90 from raw csv arrays (basic mode)."""
    return _summarize_csv(csv_data, 0), _summarize_csv(csv_data, 1)


def _prices_from_stats(stats: dict) -> tuple[dict, dict]:
    """Extract prices from pre-computed stats (detailed mode)."""
    cur = stats.get("current", [])
    avg90 = stats.get("avg90", [])
    mn = stats.get("min", [])
    mx = stats.get("max", [])

    def extract(idx: int) -> dict[str, float | None]:
        return {
            "current": cents_to_price(cur[idx]) if len(cur) > idx else None,
            "lowest": _stat_price(mn, idx),
            "highest": _stat_price(mx, idx),
            "avg90": cents_to_price(avg90[idx]) if len(avg90) > idx else None,
        }

    return extract(0), extract(1)


def _summarize_csv(csv_data: list, type_index: int) -> dict[str, float | None]:
    """Compute stats from a single csv price type array."""
    empty = {"current": None, "lowest": None, "highest": None, "avg90": None}
    if type_index >= len(csv_data) or not csv_data[type_index]:
        return empty

    arr = csv_data[type_index]
    prices = [arr[i + 1] for i in range(0, len(arr) - 1, 2) if arr[i + 1] != -1]
    if not prices:
        return empty

    # Filter outliers: keep within 0.2x–5x of median
    sorted_p = sorted(prices)
    median = sorted_p[len(sorted_p) // 2]
    filtered = [p for p in prices if median * 0.2 <= p <= median * 5] or prices

    current_cents = arr[-1] if arr[-1] != -1 else None
    return {
        "current": cents_to_price(current_cents),
        "lowest": cents_to_price(min(filtered)),
        "highest": cents_to_price(max(filtered)),
        "avg90": cents_to_price(int(sum(filtered) / len(filtered))),
    }


def _stat_price(stat_array: list, idx: int) -> float | None:
    """Extract price from stats min/max arrays (can be [ts, price] or just price)."""
    if len(stat_array) > idx and stat_array[idx]:
        v = stat_array[idx]
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            return cents_to_price(v[1])
        return cents_to_price(v)
    return None


def _latest_value(csv_data: list, type_index: int, as_int: bool = False):
    """Get the latest value from a csv array."""
    if type_index >= len(csv_data) or not csv_data[type_index]:
        return None
    arr = csv_data[type_index]
    if len(arr) >= 2 and arr[-1] != -1:
        return int(arr[-1]) if as_int else arr[-1]
    return None


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
