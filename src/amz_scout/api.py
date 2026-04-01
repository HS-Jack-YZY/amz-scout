"""Programmatic API for amz-scout.

Every public function takes simple strings/ints and returns a dict envelope::

    {"ok": True,  "data": [...], "error": None, "meta": {...}}
    {"ok": False, "data": [],    "error": "...", "meta": {}}

No exceptions are raised to the caller.  Errors are captured in the envelope.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from amz_scout.config import (
    MarketplaceConfig,
    ProjectConfig,
    load_marketplace_config,
    load_project_config,
)
from amz_scout.db import (
    SERIES_AMAZON,
    SERIES_BUY_BOX_SHIPPING,
    SERIES_COUNT_NEW,
    SERIES_COUNT_REVIEWS,
    SERIES_LISTPRICE,
    SERIES_MONTHLY_SOLD,
    SERIES_NAMES,
    SERIES_NEW,
    SERIES_RATING,
    SERIES_SALES_RANK,
    SERIES_USED,
    open_db,
    query_bsr_ranking,
    query_cross_market,
    query_deals_history,
    query_price_trends,
    query_seller_history,
    resolve_db_path,
)
from amz_scout.db import (
    query_availability as _db_query_availability,
)
from amz_scout.db import (
    query_latest as _db_query_latest,
)
from amz_scout.models import Product

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"

SERIES_MAP: dict[str, int] = {
    "amazon": SERIES_AMAZON,
    "new": SERIES_NEW,
    "used": SERIES_USED,
    "sales_rank": SERIES_SALES_RANK,
    "listprice": SERIES_LISTPRICE,
    "rating": SERIES_RATING,
    "reviews": SERIES_COUNT_REVIEWS,
    "count_new": SERIES_COUNT_NEW,
    "buybox": SERIES_BUY_BOX_SHIPPING,
    "monthly_sold": SERIES_MONTHLY_SOLD,
}

KEEPA_EPOCH = datetime(2011, 1, 1)
KEEPA_TOKEN_MAX = 60
KEEPA_REFILL_RATE = "1/min"


# ─── Internal helpers ────────────────────────────────────────────────


class _ProjectInfo(NamedTuple):
    config: ProjectConfig
    marketplaces: dict[str, MarketplaceConfig]
    products: list[Product]
    db_path: Path
    output_base: Path


def _load_project(project: str) -> _ProjectInfo:
    """Resolve a project string and load all config data.

    Accepts:
      - Project name: ``"BE10000"`` → ``config/BE10000.yaml``
      - Relative path: ``"config/BE10000.yaml"``
      - Absolute path: ``"/abs/path/to/config.yaml"``
    """
    p = Path(project)
    project_path = p if p.exists() else CONFIG_DIR / f"{project}.yaml"

    if not project_path.exists():
        raise FileNotFoundError(f"Project config not found: {project_path}")

    mp_path = project_path.parent / "marketplaces.yaml"
    if not mp_path.exists():
        raise FileNotFoundError(f"Marketplace config not found: {mp_path}")

    config = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    products = [pe.to_product() for pe in config.products]
    db_path = resolve_db_path(config.project.output_dir)
    output_base = Path(config.project.output_dir)

    return _ProjectInfo(config, marketplaces, products, db_path, output_base)


def _resolve_asin(
    products: list[Product],
    query_str: str,
    marketplace: str | None = None,
) -> tuple[str, str, str]:
    """Resolve a product query to (asin, model, source).

    Three-level fallback:
    1. Config products: case-insensitive substring match on model name
    2. ASIN pass-through: if query is a 10-char alphanumeric string
    3. Failure: raises ValueError
    """
    query_lower = query_str.lower()

    # Level 1: config product list (substring match)
    for p in products:
        if query_lower in p.model.lower():
            asin = p.asin_for(marketplace) if marketplace else p.default_asin
            return asin, p.model, "config"

    # Level 2: direct ASIN
    if len(query_str) == 10 and query_str.isascii() and query_str.isalnum():
        return query_str, query_str, "asin"

    raise ValueError(f"Product not found: {query_str}")


def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    **meta: object,
) -> dict:
    """Build the standard response envelope."""
    return {
        "ok": ok,
        "data": data if data is not None else [],
        "error": error,
        "meta": meta,
    }


def _add_dates(rows: list[dict]) -> list[dict]:
    """Return new list with human-readable date field from keepa_ts."""
    return [
        {**r, "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime("%Y-%m-%d %H:%M")}
        if "keepa_ts" in r else r
        for r in rows
    ]


# ─── Public: project discovery ───────────────────────────────────────


def resolve_project(project: str) -> dict:
    """Discover project configuration: products, marketplaces, settings.

    Returns envelope with data containing project name, product list,
    target marketplaces, and their ASINs per marketplace.
    """
    try:
        info = _load_project(project)
    except Exception as e:
        logger.exception("resolve_project failed")
        return _envelope(False, error=str(e))

    products_data = []
    for p in info.products:
        entry = {
            "category": p.category,
            "brand": p.brand,
            "model": p.model,
            "default_asin": p.default_asin,
        }
        for site in info.config.target_marketplaces:
            entry[f"asin_{site}"] = p.asin_for(site)
        products_data.append(entry)

    return _envelope(
        True,
        data={
            "name": info.config.project.name,
            "description": info.config.project.description,
            "target_marketplaces": info.config.target_marketplaces,
            "products": products_data,
            "db_path": str(info.db_path),
            "db_exists": info.db_path.exists(),
        },
    )


def resolve_product(
    project: str,
    query_str: str,
    marketplace: str | None = None,
) -> dict:
    """Resolve a product query string to ASIN and model info.

    Accepts model names (substring match), ASIN strings, or brand+model
    fragments.  Returns the resolved ASIN for the given marketplace.
    """
    try:
        info = _load_project(project)
        asin, model, source = _resolve_asin(info.products, query_str, marketplace)
    except Exception as e:
        logger.exception("resolve_product failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={"asin": asin, "model": model, "source": source},
    )


# ─── Public: query functions ─────────────────────────────────────────


def query_latest(
    project: str,
    marketplace: str | None = None,
    category: str | None = None,
) -> dict:
    """Latest competitive snapshot per product/site."""
    try:
        info = _load_project(project)
        with open_db(info.db_path) as conn:
            rows = _db_query_latest(conn, site=marketplace, category=category)
    except Exception as e:
        logger.exception("query_latest failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


def query_trends(
    project: str,
    product: str,
    marketplace: str = "UK",
    series: str = "new",
    days: int = 90,
) -> dict:
    """Price/data time series for one product on one marketplace."""
    try:
        info = _load_project(project)
        asin, model, _ = _resolve_asin(info.products, product, marketplace)
        series_type = SERIES_MAP.get(series.lower(), SERIES_NEW)
        series_name = SERIES_NAMES.get(series_type, str(series_type))

        with open_db(info.db_path) as conn:
            rows = query_price_trends(conn, asin, marketplace, series_type, days)

        rows = _add_dates(rows)
    except Exception as e:
        logger.exception("query_trends failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True, data=rows,
        asin=asin, model=model, series_name=series_name, count=len(rows),
    )


def query_compare(project: str, product: str) -> dict:
    """Compare one product across all marketplaces (latest snapshot)."""
    try:
        info = _load_project(project)
        with open_db(info.db_path) as conn:
            rows = query_cross_market(conn, product)
    except Exception as e:
        logger.exception("query_compare failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


def query_ranking(
    project: str,
    marketplace: str,
    category: str | None = None,
) -> dict:
    """Products ranked by BSR for a marketplace."""
    try:
        info = _load_project(project)
        with open_db(info.db_path) as conn:
            rows = query_bsr_ranking(conn, marketplace, category)
    except Exception as e:
        logger.exception("query_ranking failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


def query_availability(project: str) -> dict:
    """Availability matrix: all products across all sites."""
    try:
        info = _load_project(project)
        with open_db(info.db_path) as conn:
            rows = _db_query_availability(conn)
    except Exception as e:
        logger.exception("query_availability failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


def query_sellers(
    project: str,
    product: str,
    marketplace: str = "UK",
) -> dict:
    """Buy Box seller history for one product."""
    try:
        info = _load_project(project)
        asin, model, _ = _resolve_asin(info.products, product, marketplace)

        with open_db(info.db_path) as conn:
            rows = query_seller_history(conn, asin, marketplace)

        rows = _add_dates(rows)
    except Exception as e:
        logger.exception("query_sellers failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, asin=asin, model=model, count=len(rows))


def query_deals(project: str, marketplace: str | None = None) -> dict:
    """Deal/promotion history."""
    try:
        info = _load_project(project)
        with open_db(info.db_path) as conn:
            rows = query_deals_history(conn, site=marketplace)
    except Exception as e:
        logger.exception("query_deals failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


# ─── Public: Keepa data management ──────────────────────────────────


def ensure_keepa_data(
    project: str,
    marketplace: str | None = None,
    product: str | None = None,
    strategy: str = "lazy",
    max_age_days: int = 7,
    detailed: bool = False,
) -> dict:
    """Ensure Keepa data exists in the database, fetching if needed.

    Default strategy is ``"lazy"``: use cached data no matter how old,
    fetch only if completely missing.  Pass ``"fresh"`` to force refresh.

    Valid strategies: ``"lazy"``, ``"offline"``, ``"max_age"``, ``"fresh"``.
    """
    try:
        from amz_scout.freshness import FreshnessStrategy
        from amz_scout.keepa_service import get_keepa_data

        strategy_map = {
            "lazy": FreshnessStrategy.LAZY,
            "offline": FreshnessStrategy.OFFLINE,
            "max_age": FreshnessStrategy.MAX_AGE,
            "fresh": FreshnessStrategy.FRESH,
        }
        fs = strategy_map.get(strategy)
        if fs is None:
            return _envelope(False, error=f"Unknown strategy: {strategy}")

        info = _load_project(project)
        sites = [marketplace] if marketplace else info.config.target_marketplaces
        products = info.products

        if product:
            _, model, _ = _resolve_asin(products, product)
            products = [p for p in products if p.model == model]

        with open_db(info.db_path) as conn:
            result = get_keepa_data(
                conn,
                products,
                sites,
                info.marketplaces,
                strategy=fs,
                max_age_days=max_age_days,
                detailed=detailed,
                output_base=info.output_base,
            )

        outcomes = [
            {
                "asin": o.asin,
                "site": o.site,
                "model": o.model,
                "source": o.source,
                "age_days": o.freshness.age_days,
            }
            for o in result.outcomes
        ]
    except ValueError as e:
        # Keepa API key not configured, etc.
        logger.warning("ensure_keepa_data: %s", e)
        return _envelope(False, data={"outcomes": []}, error=str(e))
    except Exception as e:
        logger.exception("ensure_keepa_data failed")
        return _envelope(False, data={"outcomes": []}, error=str(e))

    return _envelope(
        True,
        data={"outcomes": outcomes},
        fetched=result.fetch_count,
        cached=result.cache_count,
        skipped=result.skip_count,
        tokens_used=result.tokens_used,
        tokens_remaining=result.tokens_remaining,
    )


def check_freshness(
    project: str,
    marketplace: str | None = None,
    product: str | None = None,
) -> dict:
    """Check Keepa data freshness without fetching anything."""
    try:
        from amz_scout.freshness import (
            FreshnessStrategy,
            evaluate_freshness,
            format_freshness_matrix,
            query_freshness,
        )

        info = _load_project(project)
        sites = [marketplace] if marketplace else info.config.target_marketplaces
        products = info.products

        if product:
            _, model, _ = _resolve_asin(products, product)
            products = [p for p in products if p.model == model]

        with open_db(info.db_path) as conn:
            fetched_map = query_freshness(conn, products, sites)
            results = evaluate_freshness(
                products, sites, fetched_map, FreshnessStrategy.MAX_AGE,
            )
            matrix = format_freshness_matrix(results, sites)
    except Exception as e:
        logger.exception("check_freshness failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=matrix, sites=sites, count=len(matrix))


def keepa_budget() -> dict:
    """Check Keepa API token balance."""
    try:
        from amz_scout.scraper.keepa import KeepaClient

        kc = KeepaClient()
        tokens = kc.tokens_left
    except ValueError as e:
        return _envelope(False, error=str(e))
    except Exception as e:
        logger.exception("keepa_budget failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={
            "tokens_available": tokens,
            "tokens_max": KEEPA_TOKEN_MAX,
            "refill_rate": KEEPA_REFILL_RATE,
        },
    )
