"""Cache-first Keepa data retrieval service.

Orchestrates freshness evaluation, selective API fetching, DB storage,
and result assembly. Bridges the gap between config-driven scraping
and database queries.
"""

import json as json_mod
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from amz_scout.config import MarketplaceConfig
from amz_scout.csv_io import merge_price_history, read_price_history, write_price_history
from amz_scout.db import import_from_raw_json
from amz_scout.freshness import (
    FreshnessStrategy,
    ProductFreshness,
    evaluate_freshness,
    partition_by_action,
    query_freshness,
)
from amz_scout.models import PriceHistory, Product
from amz_scout.scraper.keepa import KeepaClient, _parse_product

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeepaProductOutcome:
    """Outcome for one product on one site."""

    asin: str
    site: str
    model: str
    source: str  # "cache" | "fetched" | "skipped"
    price_history: PriceHistory | None
    freshness: ProductFreshness


@dataclass(frozen=True)
class KeepaResult:
    """Aggregate result of a keepa get operation."""

    outcomes: list[KeepaProductOutcome]
    tokens_used: int
    tokens_remaining: int

    @property
    def fetch_count(self) -> int:
        return sum(1 for o in self.outcomes if o.source == "fetched")

    @property
    def cache_count(self) -> int:
        return sum(1 for o in self.outcomes if o.source == "cache")

    @property
    def skip_count(self) -> int:
        return sum(1 for o in self.outcomes if o.source == "skipped")


def get_keepa_data(
    conn: sqlite3.Connection,
    products: list[Product],
    sites: list[str],
    marketplaces: dict[str, MarketplaceConfig],
    strategy: FreshnessStrategy,
    max_age_days: int = 7,
    detailed: bool = False,
    output_base: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> KeepaResult:
    """Cache-first Keepa data retrieval.

    1. Evaluate freshness for all product/site pairs
    2. For 'needs_fetch' products, instantiate KeepaClient and fetch
    3. Store fetched data to DB + raw JSON
    4. Return combined results (from cache + fresh fetch)
    """
    _log = on_progress or (lambda msg: logger.info(msg))

    # Step 1: evaluate freshness
    fetched_at_map = query_freshness(conn, products, sites)
    freshness_results = evaluate_freshness(products, sites, fetched_at_map, strategy, max_age_days)
    cache_list, fetch_list, skip_list = partition_by_action(freshness_results)

    outcomes: list[KeepaProductOutcome] = []
    tokens_before = 0
    tokens_after = 0

    # Build O(1) lookup: (asin, site) → Product
    product_by_asin = {
        (p.asin_for(s), s): p for p in products for s in sites
    }

    # Step 2: read cached data from raw JSON files
    for pf in cache_list:
        product = product_by_asin.get((pf.asin, pf.site))
        mp = marketplaces.get(pf.site)
        raw_dir = _raw_dir(output_base, mp) if output_base and mp else None
        history = _read_from_cache(product, pf.site, raw_dir) if product else None
        outcomes.append(
            KeepaProductOutcome(
                asin=pf.asin,
                site=pf.site,
                model=pf.model,
                source="cache",
                price_history=history,
                freshness=pf,
            )
        )

    # Step 3: fetch missing/stale data from Keepa API
    if fetch_list:
        keepa = KeepaClient()
        tokens_before = keepa.tokens_left

        # Group by site for efficient batch fetching
        by_site: dict[str, list[ProductFreshness]] = {}
        for pf in fetch_list:
            by_site.setdefault(pf.site, []).append(pf)

        for site, pf_items in by_site.items():
            mp = marketplaces.get(site)
            if not mp:
                for pf in pf_items:
                    outcomes.append(
                        KeepaProductOutcome(
                            asin=pf.asin,
                            site=pf.site,
                            model=pf.model,
                            source="skipped",
                            price_history=None,
                            freshness=pf,
                        )
                    )
                continue

            site_products = [p for pf in pf_items if (p := product_by_asin.get((pf.asin, site)))]
            if not site_products:
                continue

            raw_dir = _raw_dir(output_base, mp) if output_base else None
            _log(f"  [{site}] Fetching {len(site_products)} products from Keepa...")

            histories = keepa.fetch_price_history(
                site_products,
                site,
                mp.keepa_domain,
                keepa_domain_code=mp.keepa_domain_code,
                detailed=detailed,
                raw_dir=raw_dir,
            )

            # Store to DB
            if raw_dir:
                _store_fetched_to_db(conn, raw_dir, site_products, site)

            # Build outcomes
            history_map = {h.asin: h for h in histories}
            for pf in pf_items:
                history = history_map.get(pf.asin)
                outcomes.append(
                    KeepaProductOutcome(
                        asin=pf.asin,
                        site=pf.site,
                        model=pf.model,
                        source="fetched",
                        price_history=history,
                        freshness=pf,
                    )
                )

        tokens_after = keepa.tokens_left
    else:
        tokens_after = tokens_before

    # Step 4: add skipped
    for pf in skip_list:
        outcomes.append(
            KeepaProductOutcome(
                asin=pf.asin,
                site=pf.site,
                model=pf.model,
                source="skipped",
                price_history=None,
                freshness=pf,
            )
        )

    # Step 5: write CSV per site — only if we fetched new data
    has_fetched = any(o.source == "fetched" for o in outcomes)
    if output_base and has_fetched:
        _write_csvs(outcomes, marketplaces, output_base)

    return KeepaResult(
        outcomes=outcomes,
        tokens_used=max(0, tokens_before - tokens_after),
        tokens_remaining=tokens_after,
    )


# ─── Internal helpers ────────────────────────────────────────────────


def _raw_dir(output_base: Path, mp: MarketplaceConfig) -> Path:
    """Compute raw JSON directory for a marketplace."""
    return output_base / "data" / mp.region / "raw"


def _read_from_cache(
    product: Product,
    site: str,
    raw_dir: Path | None,
) -> PriceHistory | None:
    """Read cached PriceHistory from raw JSON file.

    Uses the same _parse_product() as the API path, ensuring
    identical output regardless of source.
    """
    if not raw_dir:
        return None
    asin = product.asin_for(site)
    json_path = raw_dir / f"{site.lower()}_{asin}.json"
    if not json_path.exists():
        return None
    try:
        with open(json_path) as f:
            raw = json_mod.load(f)
        return _parse_product(product, site, raw, detailed=False)
    except Exception:
        logger.exception("Failed to read cache for %s/%s", site, asin)
        return None


def _write_csvs(
    outcomes: list[KeepaProductOutcome],
    marketplaces: dict[str, MarketplaceConfig],
    output_base: Path,
) -> None:
    """Write/merge price history CSVs per site from outcomes."""
    by_site: dict[str, list[PriceHistory]] = {}
    for o in outcomes:
        if o.price_history:
            by_site.setdefault(o.site, []).append(o.price_history)

    for site, histories in by_site.items():
        mp = marketplaces.get(site)
        if not mp:
            continue
        data_dir = output_base / "data" / mp.region
        csv_path = data_dir / f"{site.lower()}_price_history.csv"
        merged = merge_price_history(read_price_history(csv_path), histories)
        write_price_history(merged, csv_path)


def _store_fetched_to_db(
    conn: sqlite3.Connection,
    raw_dir: Path,
    products: list[Product],
    site: str,
) -> None:
    """Import freshly fetched raw JSON files to DB."""
    ok, fail = import_from_raw_json(conn, raw_dir, products, site)
    if fail:
        logger.warning("[%s] DB import: %d ok, %d failed", site, ok, fail)
