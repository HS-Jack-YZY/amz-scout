"""Programmatic API for amz-scout.

Every public function takes simple strings/ints and returns a dict envelope::

    {"ok": True,  "data": [...], "error": None, "meta": {...}}
    {"ok": False, "data": [],    "error": "...", "meta": {}}

No exceptions are raised to the caller.  Errors are captured in the envelope.
"""

import logging
import re
import sqlite3
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

BROWSER_QUERY_HINT = "No competitive data found. Run 'amz-scout scrape <config>' to populate."

# Amazon ASIN: exactly 10 uppercase alphanumeric characters.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


# ─── Internal helpers ────────────────────────────────────────────────


class _ProjectInfo(NamedTuple):
    config: ProjectConfig | None
    marketplaces: dict[str, MarketplaceConfig]
    products: list[Product]
    db_path: Path
    output_base: Path
    marketplace_aliases: dict[str, str]  # lowercase alias → canonical code


def _build_marketplace_aliases(marketplaces: dict[str, MarketplaceConfig]) -> dict[str, str]:
    """Build a lowercase alias → canonical code map from marketplace definitions."""
    aliases: dict[str, str] = {}
    for code, mp in marketplaces.items():
        aliases[code.lower()] = code               # "uk" → "UK"
        aliases[mp.keepa_domain.lower()] = code    # "gb" → "UK"
        aliases[mp.amazon_domain.lower()] = code   # "amazon.co.uk" → "UK"
        aliases[mp.currency_code.lower()] = code   # "gbp" → "UK"
    return aliases


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

    return _ProjectInfo(config, marketplaces, products, db_path, output_base,
                        _build_marketplace_aliases(marketplaces))


def _resolve_site(
    marketplace: str | None,
    aliases: dict[str, str],
) -> str | None:
    """Resolve a marketplace query to canonical code, or pass through as-is."""
    if marketplace is None:
        return None
    return aliases.get(marketplace.lower()) or marketplace


def _resolve_context(
    project: str | None = None,
    *,
    category: str | None = None,
    marketplace: str | None = None,
) -> _ProjectInfo:
    """Dual-source context resolver: YAML (legacy) or SQLite (new).

    If *project* is provided, loads from YAML via ``_load_project()``.
    If *project* is None, loads products from the SQLite registry and
    marketplace definitions from ``marketplaces.yaml``.
    """
    if project is not None:
        return _load_project(project)

    # DB-only path
    from amz_scout.db import load_products_from_db

    mp_path = CONFIG_DIR / "marketplaces.yaml"
    if not mp_path.exists():
        raise FileNotFoundError(f"Marketplace config not found: {mp_path}")

    marketplaces = load_marketplace_config(mp_path)
    db_path = resolve_db_path()
    products: list[Product] = []

    if db_path.exists():
        with open_db(db_path) as conn:
            products = load_products_from_db(
                conn, category=category, marketplace=marketplace,
            )

    return _ProjectInfo(
        config=None,
        marketplaces=marketplaces,
        products=products,
        db_path=db_path,
        output_base=Path("output"),
        marketplace_aliases=_build_marketplace_aliases(marketplaces),
    )


def _resolve_asin(
    products: list[Product],
    query_str: str,
    marketplace: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, str, str, list[str]]:
    """Resolve a product query to (asin, model, source, warnings).

    Four-level fallback:
    1. SQLite registry: if conn is provided, query product_asins via find_product()
    2. Config products: case-insensitive substring match on model name
    3. ASIN pass-through: validated format + cross-marketplace awareness
    4. Failure: raises ValueError
    """
    # Level 1: SQLite registry (most authoritative)
    if conn is not None:
        from amz_scout.db import find_product
        row = find_product(conn, query_str, marketplace)
        if row and row.get("asin"):
            return row["asin"], row["model"], "db", []

    # Level 2: config product list (substring match)
    query_lower = query_str.lower()
    for p in products:
        if query_lower in p.model.lower():
            asin = p.asin_for(marketplace) if marketplace else p.default_asin
            return asin, p.model, "config", []

    # Level 3: direct ASIN (with format validation + cross-marketplace check)
    candidate = query_str.upper().strip()
    if _ASIN_RE.match(candidate):
        warnings: list[str] = []

        # Soft warning for non-B-prefix ASINs (likely books/media ISBN)
        if not candidate.startswith("B"):
            warnings.append(
                f"ASIN {candidate} does not start with 'B' — "
                "may be a book/media ISBN, not a physical product."
            )

        # Cross-marketplace awareness: check if ASIN is registered elsewhere
        if conn is not None and marketplace:
            other_rows = conn.execute(
                "SELECT pa.marketplace, p.brand, p.model "
                "FROM product_asins pa JOIN products p ON pa.product_id = p.id "
                "WHERE pa.asin = ? AND pa.marketplace != ?",
                (candidate, marketplace),
            ).fetchall()
            if other_rows:
                markets = ", ".join(r["marketplace"] for r in other_rows)
                product_info = f"{other_rows[0]['brand']} {other_rows[0]['model']}"
                warnings.append(
                    f"ASIN {candidate} is registered for [{markets}] "
                    f"as '{product_info}', but you are querying {marketplace}. "
                    "The same product may use a different ASIN on this marketplace."
                )

        # Warn that this is an unregistered ASIN
        warnings.append(
            f"ASIN {candidate} is not in the product registry. "
            "Data will be fetched but not persisted to registry. "
            "Use add_product() to register if you want to keep tracking it."
        )

        return candidate, candidate, "asin", warnings

    raise ValueError(f"Product not found: {query_str}")


def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    hint_if_empty: str | None = None,
    **meta: object,
) -> dict:
    """Build the standard response envelope."""
    if hint_if_empty and not data:
        meta["hint"] = hint_if_empty
    return {
        "ok": ok,
        "data": data if data is not None else [],
        "error": error,
        "meta": meta,
    }


def _auto_fetch(
    conn: sqlite3.Connection,
    info: _ProjectInfo,
    products: list[Product],
    sites: list[str],
) -> dict:
    """Opportunistic LAZY fetch; failures are logged but never block the query."""
    try:
        from amz_scout.freshness import FreshnessStrategy
        from amz_scout.keepa_service import get_keepa_data

        result = get_keepa_data(
            conn, products, sites, info.marketplaces,
            strategy=FreshnessStrategy.LAZY,
            output_base=info.output_base,
        )
        if result.fetch_count > 0:
            return {
                "auto_fetched": True,
                "tokens_used": result.tokens_used,
                "tokens_remaining": result.tokens_remaining,
            }
        return {"auto_fetched": False}
    except Exception:
        logger.warning("auto_fetch failed, proceeding with cached data")
        return {"auto_fetched": False, "auto_fetch_error": True}


def _try_mark_not_listed(
    conn: sqlite3.Connection, asin: str, site: str,
) -> None:
    """If this ASIN is in the product registry, mark it as not_listed."""
    try:
        from amz_scout.db import update_asin_status

        row = conn.execute(
            "SELECT product_id FROM product_asins WHERE asin = ? AND marketplace = ?",
            (asin, site),
        ).fetchone()
        if row:
            update_asin_status(
                conn, row["product_id"], site, "not_listed",
                notes="Keepa returned no title or price data",
            )
    except Exception:
        logger.warning("Failed to mark ASIN %s/%s as not_listed", asin, site)


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

    assert info.config is not None  # _load_project always provides config
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
    project: str | None = None,
    query_str: str = "",
    marketplace: str | None = None,
) -> dict:
    """Resolve a product query string to ASIN and model info.

    Accepts model names (substring match), ASIN strings, or brand+model
    fragments.  Returns the resolved ASIN for the given marketplace.
    """
    try:
        info = _resolve_context(project, marketplace=marketplace)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        with open_db(info.db_path) as conn:
            asin, model, source, warns = _resolve_asin(
                info.products, query_str, site, conn=conn,
            )
    except Exception as e:
        logger.exception("resolve_product failed")
        return _envelope(False, error=str(e))

    meta: dict = {"source": source}
    if warns:
        meta["warnings"] = warns
    return _envelope(True, data={"asin": asin, "model": model, **meta})


# ─── Public: query functions ─────────────────────────────────────────


def query_latest(
    project: str | None = None,
    marketplace: str | None = None,
    category: str | None = None,
) -> dict:
    """Latest competitive snapshot per product/site."""
    try:
        info = _resolve_context(project, marketplace=marketplace, category=category)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        with open_db(info.db_path) as conn:
            rows = _db_query_latest(conn, site=site, category=category)
    except Exception as e:
        logger.exception("query_latest failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))


def query_trends(
    project: str | None = None,
    product: str = "",
    marketplace: str = "UK",
    series: str = "new",
    days: int = 90,
    auto_fetch: bool = True,
) -> dict:
    """Price/data time series for one product on one marketplace.

    When *auto_fetch* is True (default), missing Keepa data is fetched
    automatically using the LAZY strategy before querying.
    """
    try:
        info = _resolve_context(project)
        site = _resolve_site(marketplace, info.marketplace_aliases) or marketplace
        resolve_warnings: list[str] = []

        with open_db(info.db_path) as conn:
            asin, model, source, resolve_warnings = _resolve_asin(
                info.products, product, site, conn=conn,
            )
            series_type = SERIES_MAP.get(series.lower(), SERIES_NEW)
            series_name = SERIES_NAMES.get(series_type, str(series_type))

            if auto_fetch:
                # For ASIN pass-through, build a temporary Product so fetch works
                if source == "asin":
                    tmp_product = Product(
                        category="", brand="", model=asin,
                        default_asin=asin,
                    )
                    fetch_meta = _auto_fetch(conn, info, [tmp_product], [site])
                else:
                    fetch_meta = _auto_fetch(
                        conn, info,
                        [p for p in info.products if p.model == model],
                        [site],
                    )
            else:
                fetch_meta = {}
            rows = query_price_trends(conn, asin, site, series_type, days)

            # If ASIN pass-through fetched data, attach Keepa title for context
            if source == "asin" and fetch_meta.get("auto_fetched"):
                title_row = conn.execute(
                    "SELECT title FROM keepa_products WHERE asin = ? AND site = ?",
                    (asin, site),
                ).fetchone()
                if title_row and title_row["title"]:
                    resolve_warnings.insert(
                        0, f"Keepa product title: {title_row['title'][:100]}"
                    )

        rows = _add_dates(rows)
    except Exception as e:
        logger.exception("query_trends failed")
        return _envelope(False, error=str(e))

    meta_extra: dict = {}
    if resolve_warnings:
        meta_extra["warnings"] = resolve_warnings
    if source == "asin":
        meta_extra["resolution_level"] = 3

    return _envelope(
        True, data=rows,
        asin=asin, model=model, series_name=series_name, count=len(rows),
        **fetch_meta, **meta_extra,
    )


def query_compare(project: str | None = None, product: str = "") -> dict:
    """Compare one product across all marketplaces (latest snapshot)."""
    try:
        info = _resolve_context(project)
        with open_db(info.db_path) as conn:
            rows = query_cross_market(conn, product)
    except Exception as e:
        logger.exception("query_compare failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))


def query_ranking(
    project: str | None = None,
    marketplace: str = "UK",
    category: str | None = None,
) -> dict:
    """Products ranked by BSR for a marketplace."""
    try:
        info = _resolve_context(project, marketplace=marketplace, category=category)
        site = _resolve_site(marketplace, info.marketplace_aliases) or marketplace
        with open_db(info.db_path) as conn:
            rows = query_bsr_ranking(conn, site, category)
    except Exception as e:
        logger.exception("query_ranking failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))


def query_availability(project: str | None = None) -> dict:
    """Availability matrix: all products across all sites."""
    try:
        info = _resolve_context(project)
        with open_db(info.db_path) as conn:
            rows = _db_query_availability(conn)
    except Exception as e:
        logger.exception("query_availability failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))


def query_sellers(
    project: str | None = None,
    product: str = "",
    marketplace: str = "UK",
    auto_fetch: bool = True,
) -> dict:
    """Buy Box seller history for one product.

    When *auto_fetch* is True (default), missing Keepa data is fetched
    automatically using the LAZY strategy before querying.
    """
    try:
        info = _resolve_context(project)
        site = _resolve_site(marketplace, info.marketplace_aliases) or marketplace

        with open_db(info.db_path) as conn:
            asin, model, source, resolve_warnings = _resolve_asin(
                info.products, product, site, conn=conn,
            )
            if auto_fetch:
                if source == "asin":
                    tmp = Product(category="", brand="", model=asin, default_asin=asin)
                    fetch_meta = _auto_fetch(conn, info, [tmp], [site])
                else:
                    fetch_meta = _auto_fetch(
                        conn, info,
                        [p for p in info.products if p.model == model],
                        [site],
                    )
            else:
                fetch_meta = {}
            rows = query_seller_history(conn, asin, site)

        rows = _add_dates(rows)
    except Exception as e:
        logger.exception("query_sellers failed")
        return _envelope(False, error=str(e))

    meta_extra: dict = {}
    if resolve_warnings:
        meta_extra["warnings"] = resolve_warnings
    return _envelope(True, data=rows, asin=asin, model=model, count=len(rows),
                     **fetch_meta, **meta_extra)


def query_deals(
    project: str | None = None,
    marketplace: str | None = None,
    auto_fetch: bool = True,
) -> dict:
    """Deal/promotion history.

    When *auto_fetch* is True (default), missing Keepa data is fetched
    automatically using the LAZY strategy before querying.
    For deals, auto-fetch targets only the specified marketplace.
    """
    try:
        info = _resolve_context(project, marketplace=marketplace)
        site = _resolve_site(marketplace, info.marketplace_aliases)

        with open_db(info.db_path) as conn:
            if auto_fetch and info.products:
                fetch_sites = [site] if site else list({
                    s for p in info.products for s in p.marketplace_overrides
                })
                fetch_meta = (
                    _auto_fetch(conn, info, info.products, fetch_sites) if fetch_sites else {}
                )
            else:
                fetch_meta = {}
            rows = query_deals_history(conn, site=site)
    except Exception as e:
        logger.exception("query_deals failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows), **fetch_meta)


# ─── Public: Keepa data management ──────────────────────────────────


_BATCH_TOKEN_THRESHOLD = 6  # Require confirmation when estimated tokens >= this


def ensure_keepa_data(
    project: str | None = None,
    marketplace: str | None = None,
    product: str | None = None,
    strategy: str = "lazy",
    max_age_days: int = 7,
    detailed: bool = False,
    confirm: bool = False,
) -> dict:
    """Ensure Keepa data exists in the database, fetching if needed.

    Default strategy is ``"lazy"``: use cached data no matter how old,
    fetch only if completely missing.  Pass ``"fresh"`` to force refresh.

    Valid strategies: ``"lazy"``, ``"offline"``, ``"max_age"``, ``"fresh"``.

    **Batch gate**: when the estimated token cost is ≥ 6, returns
    ``phase="needs_confirmation"`` with a cost preview instead of fetching.
    Pass ``confirm=True`` to proceed.
    """
    try:
        from amz_scout.freshness import (
            FreshnessStrategy,
            evaluate_freshness,
            partition_by_action,
        )
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

        info = _resolve_context(project, marketplace=marketplace)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        sites = [site] if site else list({
            s for p in info.products for s in p.marketplace_overrides
        }) or ["UK"]
        products = info.products

        if product:
            _, model, _, _ = _resolve_asin(products, product)
            products = [p for p in products if p.model == model]

        with open_db(info.db_path) as conn:
            # Pre-flight: estimate token cost before fetching
            if not confirm:
                from amz_scout.freshness import query_freshness as qf

                fetched_map = qf(conn, products, sites)
                requested_mode = "detailed" if detailed else "basic"
                freshness = evaluate_freshness(
                    products, sites, fetched_map, fs, max_age_days,
                    requested_mode=requested_mode,
                )
                _, fetch_list, _ = partition_by_action(freshness)
                token_per = 6 if detailed else 1
                estimated_tokens = len(fetch_list) * token_per

                if estimated_tokens >= _BATCH_TOKEN_THRESHOLD:
                    preview = [
                        {"asin": pf.asin, "site": pf.site, "model": pf.model}
                        for pf in fetch_list
                    ]
                    return _envelope(
                        True,
                        data={"preview": preview},
                        phase="needs_confirmation",
                        message=(
                            f"This operation will fetch {len(fetch_list)} "
                            f"product(s), estimated cost: {estimated_tokens} "
                            f"token(s). Call with confirm=True to proceed."
                        ),
                        estimated_tokens=estimated_tokens,
                        products_to_fetch=len(fetch_list),
                    )

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

            # Post-fetch validation: check for empty/invalid data
            warnings: list[str] = []
            fetched_outcomes = [o for o in result.outcomes if o.source == "fetched"]
            if fetched_outcomes:
                conds = " OR ".join(["(asin = ? AND site = ?)"] * len(fetched_outcomes))
                title_params: list = [v for o in fetched_outcomes for v in (o.asin, o.site)]
                title_rows = conn.execute(
                    f"SELECT asin, site, title FROM keepa_products WHERE {conds}",
                    title_params,
                ).fetchall()
                title_map = {(r["asin"], r["site"]): r["title"] or "" for r in title_rows}

                for o in fetched_outcomes:
                    title = title_map.get((o.asin, o.site), "")
                    has_csv = o.price_history and (
                        o.price_history.buybox_current is not None
                        or o.price_history.new_current is not None
                    )
                    if not title and not has_csv:
                        brand = o.freshness.brand
                        warnings.append(
                            f"{o.model} / {o.site} ({o.asin}): "
                            "ASIN has no data — likely wrong or not listed. "
                            f"Call discover_asin('{brand}', '{o.model}', "
                            f"'{o.site}') to search for the correct ASIN."
                        )
                        _try_mark_not_listed(conn, o.asin, o.site)

    except ValueError as e:
        logger.warning("ensure_keepa_data: %s", e)
        return _envelope(False, data={"outcomes": []}, error=str(e))
    except Exception as e:
        logger.exception("ensure_keepa_data failed")
        return _envelope(False, data={"outcomes": []}, error=str(e))

    meta: dict = {
        "fetched": result.fetch_count,
        "cached": result.cache_count,
        "skipped": result.skip_count,
        "tokens_used": result.tokens_used,
        "tokens_remaining": result.tokens_remaining,
    }
    if warnings:
        meta["warnings"] = warnings
    return _envelope(True, data={"outcomes": outcomes}, **meta)


def check_freshness(
    project: str | None = None,
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

        info = _resolve_context(project, marketplace=marketplace)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        sites = [site] if site else list({
            s for p in info.products for s in p.marketplace_overrides
        }) or ["UK"]
        products = info.products

        if product:
            _, model, _, _ = _resolve_asin(products, product)
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


# ─── Public: product registry management ─────────────────────────────


def _get_db(db_path: Path | str | None = None) -> Path:
    """Get a DB path. Uses the shared default if not specified."""
    if db_path:
        return Path(db_path)
    return resolve_db_path()


def list_products(
    category: str | None = None,
    brand: str | None = None,
    marketplace: str | None = None,
    tag: str | None = None,
    db_path: Path | str | None = None,
) -> dict:
    """List all registered products with optional filters."""
    try:
        from amz_scout.db import list_registered_products

        path = _get_db(db_path)
        with open_db(path) as conn:
            rows = list_registered_products(
                conn,
                category=category,
                brand=brand,
                marketplace=marketplace,
                tag=tag,
            )
    except Exception as e:
        logger.exception("list_products failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, count=len(rows))


def add_product(
    category: str,
    brand: str,
    model: str,
    asins: dict[str, str] | None = None,
    search_keywords: str = "",
    tag: str | None = None,
    db_path: Path | str | None = None,
) -> dict:
    """Register a new product and optionally set ASIN mappings.

    *asins* is a dict mapping marketplace codes to ASINs,
    e.g. ``{"UK": "B0F2MR53D6", "DE": "B0F2MR53D6"}``.
    """
    try:
        from amz_scout.db import register_asin, register_product, tag_product

        path = _get_db(db_path)
        with open_db(path) as conn:
            pid = register_product(conn, category, brand, model, search_keywords)

            if asins:
                for marketplace, asin in asins.items():
                    register_asin(conn, pid, marketplace, asin)

            if tag:
                tag_product(conn, pid, tag)
    except Exception as e:
        logger.exception("add_product failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={"id": pid, "brand": brand, "model": model},
        asins_registered=len(asins) if asins else 0,
    )


def remove_product_by_model(
    brand: str, model: str, db_path: Path | str | None = None,
) -> dict:
    """Remove a product by exact brand + model match."""
    try:
        from amz_scout.db import find_product_exact
        from amz_scout.db import remove_product as _db_remove

        path = _get_db(db_path)
        with open_db(path) as conn:
            row = find_product_exact(conn, brand, model)
            if not row:
                return _envelope(False, error=f"Product not found: {brand} {model}")
            _db_remove(conn, row["id"])
    except Exception as e:
        logger.exception("remove_product_by_model failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data={"brand": brand, "model": model, "removed": True})


def update_product_asin(
    brand: str,
    model: str,
    marketplace: str,
    asin: str,
    status: str = "unverified",
    notes: str = "",
    db_path: Path | str | None = None,
) -> dict:
    """Set or update the ASIN for a product on a specific marketplace."""
    try:
        from amz_scout.db import find_product_exact, register_asin

        path = _get_db(db_path)
        with open_db(path) as conn:
            row = find_product_exact(conn, brand, model)
            if not row:
                return _envelope(False, error=f"Product not found: {brand} {model}")
            register_asin(conn, row["id"], marketplace, asin, status, notes)
    except Exception as e:
        logger.exception("update_product_asin failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={
            "brand": brand,
            "model": model,
            "marketplace": marketplace,
            "asin": asin,
            "status": status,
        },
    )


def import_yaml(
    project_config: str,
    tag: str | None = None,
    db_path: Path | str | None = None,
) -> dict:
    """Import products from a YAML config file into the product registry.

    Each product is registered with ASINs for all target marketplaces.
    If *tag* is not specified, the project name from the YAML is used.
    """
    try:
        from amz_scout.db import register_asin, register_product, tag_product

        info = _load_project(project_config)
        config = info.config
        assert config is not None  # _load_project always provides config
        project_tag = tag or config.project.name
        path = _get_db(db_path)

        imported = 0
        asin_count = 0

        with open_db(path) as conn:
            for product in info.products:
                pid = register_product(
                    conn,
                    product.category,
                    product.brand,
                    product.model,
                    product.search_keywords,
                )
                tag_product(conn, pid, project_tag)

                # Register ASINs for all target marketplaces + override markets
                markets = set(config.target_marketplaces)
                markets.update(product.marketplace_overrides.keys())

                for site in markets:
                    asin = product.asin_for(site)
                    note = product.note_for(site) or ""
                    register_asin(conn, pid, site, asin, notes=note)
                    asin_count += 1

                imported += 1
    except Exception as e:
        logger.exception("import_yaml failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={"tag": project_tag, "products_imported": imported, "asins_registered": asin_count},
    )


def validate_asins(
    marketplace: str | None = None,
    db_path: Path | str | None = None,
) -> dict:
    """Validate unverified ASINs by checking Keepa product title against brand+model.

    For each unverified ASIN, checks if ``keepa_products.title`` exists and
    whether it contains the expected brand/model.  Updates ``product_asins.status``
    to ``verified``, ``wrong_product``, or ``not_listed`` accordingly.

    Does NOT call Keepa API — only checks data already in the DB from prior fetches.
    Run ``ensure_keepa_data`` first to populate ``keepa_products``.
    """
    try:
        from amz_scout.db import update_asin_status

        path = _get_db(db_path)
        results_list: list[dict] = []

        with open_db(path) as conn:
            # Single JOIN: fetch unverified ASINs + their Keepa titles in one query.
            # kp_fetched_at is non-NULL only when a keepa_products row exists, letting
            # us distinguish "no Keepa data" (kp_fetched_at IS NULL) from "Keepa data
            # with empty title" (kp_fetched_at NOT NULL, kp_title IS NULL).
            sql = """
                SELECT p.id AS product_id, p.brand, p.model,
                       pa.marketplace, pa.asin, kp.title AS kp_title,
                       kp.fetched_at AS kp_fetched_at
                FROM products p
                JOIN product_asins pa ON p.id = pa.product_id
                LEFT JOIN keepa_products kp ON pa.asin = kp.asin AND pa.marketplace = kp.site
                WHERE pa.status = 'unverified'
            """
            params: list = []
            if marketplace:
                sql += " AND pa.marketplace = ?"
                params.append(marketplace)
            sql += " ORDER BY p.brand, p.model, pa.marketplace"
            unverified = [dict(r) for r in conn.execute(sql, params)]

            for row in unverified:
                asin = row["asin"]
                site = row["marketplace"]
                brand = row["brand"].lower()
                model = row["model"].lower()
                pid = row["product_id"]
                kp_fetched_at = row["kp_fetched_at"]  # None → no Keepa row at all
                kp_title = row["kp_title"]             # None → row exists, title missing

                if kp_fetched_at is None:
                    # No Keepa data yet — skip, don't change status
                    results_list.append({
                        "brand": row["brand"], "model": row["model"],
                        "marketplace": site, "asin": asin,
                        "status": "unverified", "reason": "no keepa data",
                    })
                    continue

                title = (kp_title or "").lower()

                if not title:
                    # Keepa returned the ASIN but with no title → not listed
                    update_asin_status(conn, pid, site, "not_listed",
                                       notes="Keepa returned no title")
                    results_list.append({
                        "brand": row["brand"], "model": row["model"],
                        "marketplace": site, "asin": asin,
                        "status": "not_listed", "reason": "no title in Keepa",
                    })
                    continue

                # Fuzzy match: check if brand OR significant model tokens appear in title
                brand_match = brand in title
                # Extract significant tokens from model (>2 chars, skip parentheses content)
                model_tokens = [t for t in model.replace("(", " ").replace(")", " ").split()
                                if len(t) > 2]
                model_match = any(t in title for t in model_tokens) if model_tokens else False

                if brand_match or model_match:
                    update_asin_status(conn, pid, site, "verified",
                                       notes=f"title: {kp_title[:80]}")
                    results_list.append({
                        "brand": row["brand"], "model": row["model"],
                        "marketplace": site, "asin": asin,
                        "status": "verified", "reason": "title matches",
                    })
                else:
                    update_asin_status(conn, pid, site, "wrong_product",
                                       notes=f"title: {kp_title[:80]}")
                    results_list.append({
                        "brand": row["brand"], "model": row["model"],
                        "marketplace": site, "asin": asin,
                        "status": "wrong_product",
                        "reason": f"title mismatch: {kp_title[:60]}",
                    })
    except Exception as e:
        logger.exception("validate_asins failed")
        return _envelope(False, error=str(e))

    verified = sum(1 for r in results_list if r["status"] == "verified")
    wrong = sum(1 for r in results_list if r["status"] == "wrong_product")
    not_listed = sum(1 for r in results_list if r["status"] == "not_listed")
    skipped = sum(1 for r in results_list if r["status"] == "unverified")

    return _envelope(
        True, data=results_list,
        verified=verified, wrong_product=wrong, not_listed=not_listed,
        skipped=skipped, total=len(results_list),
    )


def _run_discover_batch(
    candidates: list[dict],
    headed: bool = False,
    db_path: Path | str | None = None,
) -> tuple[list[dict], int, int]:
    """Run discover_asin for each candidate and return (results, found, failed)."""
    results: list[dict] = []
    for c in candidates:
        brand = c.get("brand", "")
        model = c.get("model", "")
        mp = c.get("marketplace", "")
        if not (brand and model and mp):
            results.append({
                "brand": brand, "model": model, "marketplace": mp,
                "ok": False, "error": "Missing brand, model, or marketplace",
            })
            continue

        dr = discover_asin(
            brand=brand, model=model, marketplace=mp,
            headed=headed, db_path=db_path,
        )
        results.append({
            "brand": brand,
            "model": model,
            "marketplace": mp,
            "old_asin": c.get("old_asin", ""),
            "ok": dr["ok"],
            "new_asin": dr["data"].get("asin") if dr["ok"] else None,
            "error": dr.get("error"),
        })

    found = sum(1 for r in results if r["ok"])
    return results, found, len(results) - found


def validate_and_discover(
    marketplace: str | None = None,
    auto_discover: bool = False,
    headed: bool = False,
    db_path: Path | str | None = None,
) -> dict:
    """Validate ASINs and optionally discover replacements for bad ones.

    1. Runs ``validate_asins()`` to check all unverified ASINs.
    2. Collects ``not_listed`` and ``wrong_product`` results as discover candidates.
    3. If ``auto_discover=False`` (default): returns the candidate list for user
       confirmation — no browser launched.
    4. If ``auto_discover=True``: calls ``discover_asin()`` for each candidate
       sequentially (slow, launches browser per marketplace).

    Returns envelope with validation results and discover outcomes.
    """
    val_result = validate_asins(marketplace=marketplace, db_path=db_path)
    if not val_result["ok"]:
        return val_result

    val_meta = val_result["meta"]
    suggestions = [
        {
            "brand": r["brand"],
            "model": r["model"],
            "marketplace": r["marketplace"],
            "old_asin": r["asin"],
            "reason": r["reason"],
        }
        for r in val_result["data"]
        if r["status"] in ("not_listed", "wrong_product")
    ]

    if not suggestions:
        return _envelope(
            True,
            data=val_result["data"],
            phase="validate",
            message="All ASINs verified or pending Keepa data — nothing to discover.",
            **val_meta,
        )

    if not auto_discover:
        return _envelope(
            True,
            data=val_result["data"],
            phase="pending_confirmation",
            message=(
                f"Found {len(suggestions)} ASIN(s) needing discovery. "
                "Call with auto_discover=True or use batch_discover() to proceed."
            ),
            discover_pending=suggestions,
            **val_meta,
        )

    results, found, failed = _run_discover_batch(suggestions, headed, db_path)
    return _envelope(
        True,
        data=results,
        phase="discovered",
        message=f"Discovery complete: {found} found, {failed} failed.",
        discovered=found,
        failed=failed,
        **val_meta,
    )


def batch_discover(
    candidates: list[dict],
    headed: bool = False,
    db_path: Path | str | None = None,
) -> dict:
    """Execute ASIN discovery for a list of candidates.

    Each candidate dict must have: ``brand``, ``model``, ``marketplace``.
    Optional: ``old_asin`` (for tracking).

    This is the "confirm and execute" step after ``validate_and_discover()``
    returns ``phase="pending_confirmation"``.

    Launches a browser for each unique marketplace — slow operation (10-30s each).
    """
    if not candidates:
        return _envelope(False, error="No candidates provided.")

    results, found, failed = _run_discover_batch(candidates, headed, db_path)
    return _envelope(True, data=results, discovered=found, failed=failed)


def discover_asin(
    brand: str,
    model: str,
    marketplace: str,
    search_keywords: str = "",
    headed: bool = False,
    db_path: Path | str | None = None,
) -> dict:
    """Search Amazon to find the correct ASIN for a product on a marketplace.

    This is a **slow** operation (10-30 seconds) that launches a browser.
    Requires ``browser-use`` CLI to be installed.

    On success, writes the found ASIN to ``product_asins`` with status
    ``unverified``.  Call ``validate_asins()`` afterwards to confirm via
    Keepa title matching.

    Returns envelope with the found ASIN, or error if not found.
    """
    try:
        from amz_scout.browser import BrowserSession, check_browser_use_installed
        from amz_scout.marketplace import setup_marketplace

        if not check_browser_use_installed():
            return _envelope(False, error="browser-use CLI not installed. Install: uv tool install browser-use")

        # Load marketplace config
        mp_path = CONFIG_DIR / "marketplaces.yaml"
        if not mp_path.exists():
            return _envelope(False, error=f"Marketplace config not found: {mp_path}")
        marketplaces = load_marketplace_config(mp_path)

        aliases = _build_marketplace_aliases(marketplaces)
        site = aliases.get(marketplace.lower()) or marketplace
        mp_config = marketplaces.get(site)
        if not mp_config:
            return _envelope(False, error=f"Unknown marketplace: {marketplace}")

        # Build a Product object for the search
        keywords = search_keywords or f"{brand} {model}"
        product = Product(
            category="",
            brand=brand,
            model=model,
            default_asin="",
            search_keywords=keywords,
        )

        # Launch browser and search
        browser = BrowserSession(headed=headed, session=f"discover-{site.lower()}")
        try:
            setup_marketplace(browser, site, mp_config)

            from amz_scout.scraper.search import resolve_asin_via_search

            found_asin = resolve_asin_via_search(
                browser, product, site, mp_config,
                config_path=None,  # Don't write to YAML — we write to SQLite
            )
        finally:
            browser.close()

        if not found_asin:
            return _envelope(
                False,
                error=f"No matching product found for {brand} {model} on {site}",
            )

        # Write found ASIN to product registry
        path = _get_db(db_path)
        with open_db(path) as conn:
            from amz_scout.db import find_product_exact, register_asin, register_product

            existing = find_product_exact(conn, brand, model)
            if existing:
                register_asin(conn, existing["id"], site, found_asin, status="unverified",
                              notes="discovered via browser search")
            else:
                pid = register_product(conn, "", brand, model, keywords)
                register_asin(conn, pid, site, found_asin, status="unverified",
                              notes="discovered via browser search")

    except Exception as e:
        logger.exception("discover_asin failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={
            "brand": brand,
            "model": model,
            "marketplace": site,
            "asin": found_asin,
            "status": "unverified",
        },
    )
