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
            try:
                resp = requests.get(
                    f"{API_BASE}/product",
                    params={"key": self._key, "domain": domain_code, "asin": asin},
                    timeout=30,
                )
                data = resp.json()
                self._tokens_left = data.get("tokensLeft", self._tokens_left)

                if resp.status_code != 200 or "error" in data:
                    logger.warning("[%s] Keepa error for %s: %s",
                                    site, asin, data.get("error", resp.status_code))
                    results.append(_empty_history(product, site))
                    continue

                raw_products = data.get("products", [])
                if not raw_products:
                    results.append(_empty_history(product, site))
                    continue

                history = _parse_from_csv(product, site, raw_products[0])
                results.append(history)
                logger.debug("[%s] %s: buybox=%s, amz=%s",
                              site, product.model,
                              history.buybox_current, history.amz_current)

            except Exception as e:
                logger.error("[%s] Error fetching %s: %s", site, asin, e)
                results.append(_empty_history(product, site))

            # Brief pause between requests
            time.sleep(0.5)

        logger.info("[%s] Keepa tokens remaining: %d", site, self._tokens_left)
        return results


def _parse_from_csv(product: Product, site: str, raw: dict) -> PriceHistory:
    """Parse price history from the csv arrays (raw time series data).

    Computes current/lowest/highest/avg from the raw data points since
    the free API plan doesn't include pre-computed stats.
    """
    csv_data = raw.get("csv", [])
    if not csv_data:
        return _empty_history(product, site)

    def summarize(type_index: int) -> dict[str, float | None]:
        """Compute current/lowest/highest/avg90 from a csv series."""
        if type_index >= len(csv_data) or not csv_data[type_index]:
            return {"current": None, "lowest": None, "highest": None, "avg90": None}

        arr = csv_data[type_index]
        points = []
        for i in range(0, len(arr) - 1, 2):
            price_cents = arr[i + 1]
            if price_cents == -1:
                continue
            points.append(price_cents)

        if not points:
            return {"current": None, "lowest": None, "highest": None, "avg90": None}

        # Filter extreme outliers (likely data errors): keep within 5x-0.2x of median
        sorted_prices = sorted(points)
        median = sorted_prices[len(sorted_prices) // 2]
        filtered = [p for p in points if median * 0.2 <= p <= median * 5]
        if not filtered:
            filtered = points

        current_cents = arr[-1] if arr[-1] != -1 else None
        return {
            "current": cents_to_price(current_cents),
            "lowest": cents_to_price(min(filtered)),
            "highest": cents_to_price(max(filtered)),
            "avg90": cents_to_price(int(sum(filtered) / len(filtered))),
        }

    # CSV indices: 0=Amazon, 1=New3P, 2=Used, 3=SalesRank, 4=ListPrice
    # Buy Box requires higher API plan (csv[18]), not available on basic plan.
    # We use Amazon + New 3P as the primary price indicators.
    amazon = summarize(0)   # Amazon direct
    new_3p = summarize(1)   # 3rd party new (lowest)

    # Buy Box: try csv[18] if available, otherwise derive from Amazon/New3P
    buybox_data = summarize(18)  # BUY_BOX_SHIPPING (may be empty on basic plan)
    if buybox_data["current"] is None:
        # Fallback: use the lower of Amazon and New 3P as a proxy
        buybox_data = {
            "current": amazon["current"] or new_3p["current"],
            "lowest": _min_none(amazon["lowest"], new_3p["lowest"]),
            "highest": _max_none(amazon["highest"], new_3p["highest"]),
            "avg90": amazon["avg90"] or new_3p["avg90"],
        }

    # Sales rank from csv[3]
    sales_rank = None
    if len(csv_data) > 3 and csv_data[3]:
        rank_arr = csv_data[3]
        if len(rank_arr) >= 2 and rank_arr[-1] != -1:
            sales_rank = int(rank_arr[-1])

    return PriceHistory(
        date=today_iso(),
        site=site,
        category=product.category,
        brand=product.brand,
        model=product.model,
        asin=product.asin_for(site),
        buybox_current=buybox_data["current"],
        buybox_lowest=buybox_data["lowest"],
        buybox_highest=buybox_data["highest"],
        buybox_avg90=buybox_data["avg90"],
        amz_current=amazon["current"],
        amz_lowest=amazon["lowest"],
        amz_highest=amazon["highest"],
        amz_avg90=amazon["avg90"],
        new_current=new_3p["current"],
        new_lowest=new_3p["lowest"],
        new_highest=new_3p["highest"],
        new_avg90=new_3p["avg90"],
        sales_rank=sales_rank,
    )


def _min_none(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _max_none(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


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
