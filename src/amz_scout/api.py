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
from typing import Any, NamedTuple, TypedDict

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


class ApiResponse(TypedDict):
    """Standard response envelope for all public API functions."""

    ok: bool
    data: list | dict
    error: str | None
    meta: dict[str, Any]


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
        aliases[code.lower()] = code  # "uk" → "UK"
        aliases[mp.keepa_domain.lower()] = code  # "gb" → "UK"
        aliases[mp.amazon_domain.lower()] = code  # "amazon.co.uk" → "UK"
        # Only map currency code if unique (EUR is shared by DE/FR/IT/ES/NL)
        currency_key = mp.currency_code.lower()
        if currency_key not in aliases:
            aliases[currency_key] = code
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

    return _ProjectInfo(
        config,
        marketplaces,
        products,
        db_path,
        output_base,
        _build_marketplace_aliases(marketplaces),
    )


def _resolve_site(
    marketplace: str | None,
    aliases: dict[str, str],
) -> str | None:
    """Resolve a marketplace query to canonical code.

    Logs a warning when the input does not match any known alias and is
    passed through unchanged — this likely indicates a typo.
    """
    if marketplace is None:
        return None
    resolved = aliases.get(marketplace.lower())
    if resolved is None:
        logger.warning(
            "Unknown marketplace '%s' — passing through as-is. Known aliases: %s",
            marketplace,
            ", ".join(sorted({v for v in aliases.values()})),
        )
        return marketplace
    return resolved


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
                conn,
                category=category,
                marketplace=marketplace,
            )

    return _ProjectInfo(
        config=None,
        marketplaces=marketplaces,
        products=products,
        db_path=db_path,
        output_base=Path("output"),
        marketplace_aliases=_build_marketplace_aliases(marketplaces),
    )


def _check_asin_status_gate(
    conn: sqlite3.Connection, asin: str, marketplace: str
) -> None:
    """Raise ValueError if (asin, marketplace) is registered with a blocking status.

    Blocks 'not_listed' to prevent silent-empty-data queries
    (query-lifecycle-matrix #10). Called from both find_product and
    raw-ASIN branches of _resolve_asin as defense-in-depth over
    load_products_from_db's WHERE filter. Intent-level mismatches are
    not gated here — interactive users self-correct from the Keepa
    title in the response (see v6 decision in
    docs/DEVELOPER.md "ASIN Status Semantics").
    """
    row = conn.execute(
        "SELECT status FROM product_asins WHERE asin = ? AND marketplace = ?",
        (asin, marketplace),
    ).fetchone()
    if row and row["status"] == "not_listed":
        raise ValueError(
            f"ASIN {asin} for {marketplace} is marked 'not_listed' "
            "(observed delisted on Amazon). Run discover_asin() for a "
            "valid ASIN, or update_asin_status() if this was misclassified."
        )


def _resolve_asin(
    products: list[Product],
    query_str: str,
    marketplace: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, str, str, str, list[str]]:
    """Resolve a product query to (asin, model, brand, source, warnings).

    Four-level fallback:
    1. SQLite registry: if conn is provided, query product_asins via find_product()
    2. Config products: case-insensitive substring match on model name
    3. ASIN pass-through: validated format + cross-marketplace awareness
    4. Failure: raises ValueError
    """
    # Level 1: SQLite registry (most authoritative)
    db_warnings: list[str] = []
    if conn is not None:
        from amz_scout.db import find_product

        row = find_product(conn, query_str, marketplace)
        if row and row.get("asin"):
            if row.get("marketplace"):
                _check_asin_status_gate(conn, row["asin"], row["marketplace"])
            return row["asin"], row["model"], row.get("brand", ""), "db", []
        # Product exists but no ASIN for this marketplace — check other markets
        if row and row.get("id") and marketplace:
            others = conn.execute(
                "SELECT asin, marketplace FROM product_asins "
                "WHERE product_id = ? LIMIT 5",
                (row["id"],),
            ).fetchall()
            if others:
                known = ", ".join(
                    f"{r['asin']} ({r['marketplace']})" for r in others
                )
                db_warnings.append(
                    f"Product '{row['model']}' found in registry but has no "
                    f"ASIN for {marketplace}. Known: {known}. "
                    "Use discover_asin() to find the correct ASIN."
                )

    # Level 2: config product list (substring match)
    query_lower = query_str.lower()
    for p in products:
        if query_lower in p.model.lower():
            asin = p.asin_for(marketplace) if marketplace else p.default_asin
            return asin, p.model, p.brand, "config", []

    # Level 3: direct ASIN (with format validation + cross-marketplace check)
    candidate = query_str.upper().strip()
    if _ASIN_RE.match(candidate):
        warnings: list[str] = []

        if conn is not None and marketplace:
            _check_asin_status_gate(conn, candidate, marketplace)

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

        # Note: ASIN will be auto-registered when Keepa data is fetched
        # (if brand and title are available from Keepa).
        warnings.append(
            f"ASIN {candidate} is not yet in the product registry. "
            "It will be auto-registered when Keepa data is fetched."
        )

        return candidate, candidate, "", "asin", warnings

    if db_warnings:
        raise ValueError(f"Product not found: {query_str}. {db_warnings[0]}")
    raise ValueError(f"Product not found: {query_str}")


def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    hint_if_empty: str | None = None,
    **meta: Any,
) -> ApiResponse:
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
) -> dict[str, Any]:
    """Opportunistic LAZY fetch; failures are logged but never block the query."""
    try:
        from amz_scout.freshness import FreshnessStrategy
        from amz_scout.keepa_service import get_keepa_data

        result = get_keepa_data(
            conn,
            products,
            sites,
            info.marketplaces,
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
    except Exception as e:
        logger.exception("auto_fetch failed, proceeding with cached data")
        return {"auto_fetched": False, "auto_fetch_error": True, "auto_fetch_detail": str(e)}


def _auto_fetch_stale_warning(fetch_meta: dict[str, Any]) -> str | None:
    """Return a user-facing warning when ``_auto_fetch`` swallowed a failure.

    ``summarize_for_llm`` forwards only a small allowlist of ``meta`` keys to
    the LLM. ``warnings`` is in that allowlist, but ``auto_fetch_error`` is
    not, so callers append the returned string to their warnings list to
    surface the freshness caveat.
    """
    if not fetch_meta.get("auto_fetch_error"):
        return None
    detail = fetch_meta.get("auto_fetch_detail") or "unknown error"
    return f"Keepa auto-fetch failed ({detail}); results may be stale."


def _record_empty_observation(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> tuple[int, bool, bool]:
    """Record a Keepa "no data" observation for a registered ASIN.

    Returns ``(new_strike_count, flipped_to_not_listed,
    was_active_on_entry)``. Unregistered ASINs are a silent no-op —
    we only gate rows the user has added to the product registry;
    they return ``(0, False, False)``.

    The flip to ``not_listed`` only fires when the row is currently
    ``active`` *and* strikes have reached
    :data:`amz_scout.db.NOT_LISTED_STRIKE_THRESHOLD`; already-``not_listed``
    rows still increment their counter but don't rewrite ``notes``.
    ``was_active_on_entry`` lets callers pick user-facing copy so an
    already-``not_listed`` row gets an observational-log message
    instead of the misleading "strike N/THRESHOLD" progression text
    whose threshold has already been crossed.
    """
    try:
        from amz_scout.db import (
            NOT_LISTED_STRIKE_THRESHOLD,
            increment_not_listed_strikes,
            update_asin_status,
        )

        row = conn.execute(
            "SELECT product_id, status FROM product_asins "
            "WHERE asin = ? AND marketplace = ?",
            (asin, site),
        ).fetchone()
        if not row:
            return (0, False, False)
        was_active = row["status"] == "active"
        strikes = increment_not_listed_strikes(conn, row["product_id"], site)
        if was_active and strikes >= NOT_LISTED_STRIKE_THRESHOLD:
            update_asin_status(
                conn,
                row["product_id"],
                site,
                "not_listed",
                notes=(
                    f"Keepa returned empty data {strikes}x in a row "
                    "(transient-failure guard threshold reached)"
                ),
            )
            return (strikes, True, was_active)
        return (strikes, False, was_active)
    except Exception:
        logger.exception(
            "Failed to record empty observation for ASIN %s/%s", asin, site
        )
        return (0, False, False)


def _record_successful_observation(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> None:
    """Reset the strike counter after a Keepa fetch returned data."""
    try:
        from amz_scout.db import clear_not_listed_strikes

        row = conn.execute(
            "SELECT product_id FROM product_asins "
            "WHERE asin = ? AND marketplace = ?",
            (asin, site),
        ).fetchone()
        if row:
            clear_not_listed_strikes(conn, row["product_id"], site)
    except Exception:
        logger.exception(
            "Failed to clear empty-observation strikes for %s/%s", asin, site
        )


def _add_dates(rows: list[dict]) -> list[dict]:
    """Return new list with human-readable date field from keepa_ts."""
    return [
        {**r, "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime("%Y-%m-%d %H:%M")}
        if "keepa_ts" in r
        else r
        for r in rows
    ]


# ─── Public: project discovery ───────────────────────────────────────


def resolve_project(project: str) -> ApiResponse:
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
) -> ApiResponse:
    """Resolve a product query string to ASIN and model info.

    Accepts model names (substring match), ASIN strings, or brand+model
    fragments.  Returns the resolved ASIN for the given marketplace.
    """
    try:
        info = _resolve_context(project, marketplace=marketplace)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        with open_db(info.db_path) as conn:
            asin, model, brand, source, warns = _resolve_asin(
                info.products,
                query_str,
                site,
                conn=conn,
            )
    except Exception as e:
        logger.exception("resolve_product failed")
        return _envelope(False, error=str(e))

    meta: dict = {"source": source}
    if warns:
        meta["warnings"] = warns
    return _envelope(True, data={"asin": asin, "model": model, "brand": brand, **meta})


# ─── Public: query functions ─────────────────────────────────────────


def query_latest(
    project: str | None = None,
    marketplace: str | None = None,
    category: str | None = None,
) -> ApiResponse:
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
) -> ApiResponse:
    """Price/data time series for one product on one marketplace.

    When *auto_fetch* is True (default), missing Keepa data is fetched
    automatically using the LAZY strategy before querying.
    """
    try:
        info = _resolve_context(project)
        site = _resolve_site(marketplace, info.marketplace_aliases) or marketplace
        resolve_warnings: list[str] = []

        with open_db(info.db_path) as conn:
            asin, model, brand, source, resolve_warnings = _resolve_asin(
                info.products,
                product,
                site,
                conn=conn,
            )
            series_type = SERIES_MAP.get(series.lower(), SERIES_NEW)
            series_name = SERIES_NAMES.get(series_type, str(series_type))

            if auto_fetch:
                # For ASIN pass-through, build a temporary Product so fetch works
                if source == "asin":
                    tmp_product = Product(
                        category="",
                        brand="",
                        model=asin,
                        default_asin=asin,
                    )
                    fetch_meta = _auto_fetch(conn, info, [tmp_product], [site])
                else:
                    fetch_meta = _auto_fetch(
                        conn,
                        info,
                        [p for p in info.products if p.model == model],
                        [site],
                    )
            else:
                fetch_meta = {}
            rows = query_price_trends(conn, asin, site, series_type, days)

            # If ASIN pass-through fetched data, check auto-registration result
            if source == "asin" and fetch_meta.get("auto_fetched"):
                title_row = conn.execute(
                    "SELECT title FROM keepa_products WHERE asin = ? AND site = ?",
                    (asin, site),
                ).fetchone()
                if title_row and title_row["title"]:
                    resolve_warnings.insert(0, f"Keepa product title: {title_row['title'][:100]}")
                # Check if auto-registration happened
                reg_row = conn.execute(
                    "SELECT p.id, p.brand, p.model FROM product_asins pa "
                    "JOIN products p ON pa.product_id = p.id "
                    "WHERE pa.asin = ? AND pa.marketplace = ?",
                    (asin, site),
                ).fetchone()
                if reg_row:
                    fetch_meta["auto_registered"] = True
                    fetch_meta["registered_as"] = {
                        "product_id": reg_row["id"],
                        "brand": reg_row["brand"],
                        "model": reg_row["model"],
                    }
                    # Check if this is a brand-new product (only 1 market registered)
                    market_count = conn.execute(
                        "SELECT COUNT(*) FROM product_asins WHERE product_id = ?",
                        (reg_row["id"],),
                    ).fetchone()[0]
                    if market_count == 1:
                        fetch_meta["new_product"] = True
                    brand = reg_row["brand"]
                    model = reg_row["model"]
                    # Replace the "not yet registered" warning
                    resolve_warnings = [
                        w for w in resolve_warnings if "not yet in the product registry" not in w
                    ]

        rows = _add_dates(rows)
    except Exception as e:
        logger.exception("query_trends failed")
        return _envelope(False, error=str(e))

    stale_warning = _auto_fetch_stale_warning(fetch_meta)
    if stale_warning:
        # Insert at the front so it survives `_truncate_warnings` (MAX_WARNINGS=3);
        # freshness caveat is more actionable than any of the resolve warnings.
        resolve_warnings.insert(0, stale_warning)

    meta_extra: dict = {}
    if resolve_warnings:
        meta_extra["warnings"] = resolve_warnings
    if source == "asin":
        meta_extra["resolution_level"] = 3

    return _envelope(
        True,
        data=rows,
        asin=asin,
        model=model,
        brand=brand,
        series_name=series_name,
        count=len(rows),
        **fetch_meta,
        **meta_extra,
    )


def query_compare(project: str | None = None, product: str = "") -> ApiResponse:
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
) -> ApiResponse:
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


def query_availability(project: str | None = None) -> ApiResponse:
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
) -> ApiResponse:
    """Buy Box seller history for one product.

    When *auto_fetch* is True (default), missing Keepa data is fetched
    automatically using the LAZY strategy before querying.
    """
    try:
        info = _resolve_context(project)
        site = _resolve_site(marketplace, info.marketplace_aliases) or marketplace

        with open_db(info.db_path) as conn:
            asin, model, brand, source, resolve_warnings = _resolve_asin(
                info.products,
                product,
                site,
                conn=conn,
            )
            if auto_fetch:
                if source == "asin":
                    tmp = Product(category="", brand="", model=asin, default_asin=asin)
                    fetch_meta = _auto_fetch(conn, info, [tmp], [site])
                else:
                    fetch_meta = _auto_fetch(
                        conn,
                        info,
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

    stale_warning = _auto_fetch_stale_warning(fetch_meta)
    if stale_warning:
        # Insert at the front so it survives `_truncate_warnings` (MAX_WARNINGS=3);
        # freshness caveat is more actionable than any of the resolve warnings.
        resolve_warnings.insert(0, stale_warning)

    meta_extra: dict = {}
    if resolve_warnings:
        meta_extra["warnings"] = resolve_warnings
    return _envelope(
        True,
        data=rows,
        asin=asin,
        model=model,
        brand=brand,
        count=len(rows),
        **fetch_meta,
        **meta_extra,
    )


def query_deals(
    project: str | None = None,
    marketplace: str | None = None,
    auto_fetch: bool = True,
) -> ApiResponse:
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
                fetch_sites = (
                    [site]
                    if site
                    else list({s for p in info.products for s in p.marketplace_overrides})
                )
                fetch_meta = (
                    _auto_fetch(conn, info, info.products, fetch_sites) if fetch_sites else {}
                )
            else:
                fetch_meta = {}
            rows = query_deals_history(conn, site=site)
    except Exception as e:
        logger.exception("query_deals failed")
        return _envelope(False, error=str(e))

    warnings_list: list[str] = []
    stale_warning = _auto_fetch_stale_warning(fetch_meta)
    if stale_warning:
        warnings_list.append(stale_warning)
    meta_extra: dict = {}
    if warnings_list:
        meta_extra["warnings"] = warnings_list
    return _envelope(True, data=rows, count=len(rows), **fetch_meta, **meta_extra)


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
) -> ApiResponse:
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
        sites = (
            [site]
            if site
            else list({s for p in info.products for s in p.marketplace_overrides}) or ["UK"]
        )
        products = info.products

        with open_db(info.db_path) as conn:
            if product:
                _, model, _, _, _ = _resolve_asin(products, product, conn=conn)
                products = [p for p in products if p.model == model]

            # Pre-flight: estimate token cost before fetching
            if not confirm:
                from amz_scout.freshness import query_freshness as qf

                fetched_map = qf(conn, products, sites)
                requested_mode = "detailed" if detailed else "basic"
                freshness = evaluate_freshness(
                    products,
                    sites,
                    fetched_map,
                    fs,
                    max_age_days,
                    requested_mode=requested_mode,
                )
                _, fetch_list, _ = partition_by_action(freshness)
                token_per = 6 if detailed else 1
                estimated_tokens = len(fetch_list) * token_per

                if estimated_tokens >= _BATCH_TOKEN_THRESHOLD:
                    preview = [
                        {"asin": pf.asin, "site": pf.site, "model": pf.model} for pf in fetch_list
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

            # Post-fetch validation: branch on transient vs genuine empty.
            warnings: list[str] = []
            fetched_outcomes = [o for o in result.outcomes if o.source == "fetched"]
            if fetched_outcomes:
                from amz_scout.db import (
                    NOT_LISTED_STRIKE_THRESHOLD as _STRIKE_THRESHOLD,
                )

                conds = " OR ".join(["(asin = ? AND site = ?)"] * len(fetched_outcomes))
                title_params: list = [v for o in fetched_outcomes for v in (o.asin, o.site)]
                title_rows = conn.execute(
                    f"SELECT asin, site, title FROM keepa_products WHERE {conds}",
                    title_params,
                ).fetchall()
                title_map = {(r["asin"], r["site"]): r["title"] or "" for r in title_rows}

                for o in fetched_outcomes:
                    ph = o.price_history
                    title = title_map.get((o.asin, o.site), "")
                    has_csv = ph and (
                        ph.buybox_current is not None
                        or ph.new_current is not None
                    )

                    # Defensive: scraper.keepa always returns
                    # ``_empty_history(..., fetch_error=...)`` for every
                    # fetched ASIN, but ``keepa_service`` builds outcomes
                    # via ``history_map.get(pf.asin)`` — a future scraper
                    # refactor that drops a record would feed ``None``
                    # here. Treat the missing record as transient (no
                    # strike, no flip) instead of a "Keepa says ASIN is
                    # dead" signal.
                    if ph is None:
                        warnings.append(
                            f"{o.model} / {o.site} ({o.asin}): "
                            "Internal fetch miss (no price_history "
                            "record); status unchanged, treating as "
                            "transient."
                        )
                        continue

                    fetch_error = ph.fetch_error
                    if fetch_error:
                        # Transient Keepa failure (rate_limited, network,
                        # partial JSON, …). Do NOT touch status or strikes —
                        # a blip must not blacklist a live ASIN.
                        warnings.append(
                            f"{o.model} / {o.site} ({o.asin}): "
                            f"Transient Keepa failure ({fetch_error}); "
                            "status unchanged, cached data (if any) "
                            "still valid."
                        )
                        continue

                    if not title and not has_csv:
                        strikes, flipped, was_active = _record_empty_observation(
                            conn, o.asin, o.site
                        )
                        brand = o.freshness.brand
                        if flipped:
                            warnings.append(
                                f"{o.model} / {o.site} ({o.asin}): "
                                f"Marked not_listed after {strikes} "
                                "consecutive empty responses. "
                                f"Run discover_asin('{brand}', '{o.model}', "
                                f"'{o.site}') for a valid ASIN, or restore "
                                "via SQL if re-listed: "
                                "UPDATE product_asins SET status='active' "
                                f"WHERE asin='{o.asin}' AND "
                                f"marketplace='{o.site}'"
                            )
                        elif was_active and strikes > 0:
                            warnings.append(
                                f"{o.model} / {o.site} ({o.asin}): "
                                f"Empty Keepa response (strike {strikes}/"
                                f"{_STRIKE_THRESHOLD}); status unchanged. "
                                f"Will mark not_listed after "
                                f"{_STRIKE_THRESHOLD} consecutive genuine "
                                "empty responses."
                            )
                        elif strikes > 0:
                            # Already not_listed: observational log only —
                            # no "strike N/threshold" progression copy
                            # whose threshold has already been crossed.
                            warnings.append(
                                f"{o.model} / {o.site} ({o.asin}): "
                                "Still observed as not_listed "
                                f"(empty observations: {strikes}). "
                                "Restore via SQL if re-listed: "
                                "UPDATE product_asins SET status='active' "
                                f"WHERE asin='{o.asin}' AND "
                                f"marketplace='{o.site}'. Otherwise run "
                                f"discover_asin('{brand}', '{o.model}', "
                                f"'{o.site}') for the correct ASIN."
                            )
                        else:
                            # Unregistered ASIN — preserve the legacy
                            # actionable warning for operators pasting
                            # bad ASINs directly.
                            warnings.append(
                                f"{o.model} / {o.site} ({o.asin}): "
                                "ASIN has no data — likely wrong or not "
                                "listed. Call discover_asin("
                                f"'{brand}', '{o.model}', '{o.site}') to "
                                "search for the correct ASIN."
                            )
                        continue

                    # Fetch returned data — reset any in-flight strike
                    # streak so one healthy response fully clears history.
                    _record_successful_observation(conn, o.asin, o.site)

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
) -> ApiResponse:
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
        sites = (
            [site]
            if site
            else list({s for p in info.products for s in p.marketplace_overrides}) or ["UK"]
        )
        products = info.products

        with open_db(info.db_path) as conn:
            if product:
                _, model, _, _, _ = _resolve_asin(products, product, conn=conn)
                products = [p for p in products if p.model == model]

            fetched_map = query_freshness(conn, products, sites)
            results = evaluate_freshness(
                products,
                sites,
                fetched_map,
                FreshnessStrategy.MAX_AGE,
            )
            matrix = format_freshness_matrix(results, sites)
    except Exception as e:
        logger.exception("check_freshness failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=matrix, sites=sites, count=len(matrix))


def keepa_budget() -> ApiResponse:
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
) -> ApiResponse:
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
) -> ApiResponse:
    """Register a new product and optionally set ASIN mappings.

    *asins* is a dict mapping marketplace codes to ASINs,
    e.g. ``{"UK": "B0F2MR53D6", "DE": "B0F2MR53D6"}``.
    """
    try:
        from amz_scout.db import register_asin, register_product, tag_product

        path = _get_db(db_path)
        with open_db(path) as conn:
            # is_new=True only on genuine INSERT; False on idempotent re-registration.
            pid, is_new = register_product(conn, category, brand, model, search_keywords)

            if asins:
                for marketplace, asin in asins.items():
                    register_asin(conn, pid, marketplace, asin)

            if tag:
                tag_product(conn, pid, tag)

        # Compute pending markets without a second DB open: the registered set is
        # already known from `asins`. Parse marketplaces.yaml once here.
        registered = set(asins.keys()) if asins else set()
        marketplaces = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        keepa_markets = {c: m for c, m in marketplaces.items() if m.keepa_domain_code is not None}
        pending = sorted(keepa_markets.keys() - registered)
        domains = {c: keepa_markets[c].amazon_domain for c in pending}
        warnings: list[str] = []
    except Exception as e:
        logger.exception("add_product failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={"id": pid, "brand": brand, "model": model},
        asins_registered=len(asins) if asins else 0,
        new_product=is_new,
        pending_markets=pending if is_new else [],
        pending_domains=domains if is_new else {},
        warnings=warnings,
    )


def remove_product_by_model(
    brand: str,
    model: str,
    db_path: Path | str | None = None,
) -> ApiResponse:
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
    status: str = "active",
    notes: str = "",
    db_path: Path | str | None = None,
) -> ApiResponse:
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


def get_pending_markets(
    product_id: int,
    db_path: Path | str | None = None,
) -> ApiResponse:
    """Return Keepa-supported markets where this product has no ASIN registered yet.

    Returns a dict with ``pending`` (list of market codes to search) and
    ``domains`` (mapping of market code to Amazon domain for WebSearch).
    """
    try:
        path = _get_db(db_path)
        marketplaces = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        with open_db(path) as conn:
            rows = conn.execute(
                "SELECT marketplace FROM product_asins WHERE product_id = ?",
                (product_id,),
            ).fetchall()
            registered = {r["marketplace"] for r in rows}

        keepa_markets = {
            code: mp for code, mp in marketplaces.items() if mp.keepa_domain_code is not None
        }
        pending = sorted(keepa_markets.keys() - registered)
        domains = {code: keepa_markets[code].amazon_domain for code in pending}
    except Exception as e:
        logger.exception("get_pending_markets failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={
            "product_id": product_id,
            "pending": pending,
            "domains": domains,
            "registered": sorted(registered),
        },
    )


def register_market_asins(
    product_id: int,
    asins: dict[str, str],
    db_path: Path | str | None = None,
) -> ApiResponse:
    """Batch-register ASINs for multiple marketplaces under an existing product.

    *asins* maps marketplace codes to ASINs,
    e.g. ``{"UK": "B0CT94XNX3", "DE": "B0CKPVMVMT"}``.
    Skips marketplaces where the product already has an ASIN registered
    (avoids overwriting verified ASINs via the upsert in ``register_asin``).
    """
    try:
        path = _get_db(db_path)
        with open_db(path) as conn:
            # Bulk-fetch existing marketplaces in one query
            placeholders = ",".join("?" for _ in asins)
            existing_markets = {
                row["marketplace"]
                for row in conn.execute(
                    f"SELECT marketplace FROM product_asins "
                    f"WHERE product_id = ? AND marketplace IN ({placeholders})",
                    (product_id, *asins.keys()),
                )
            }
            to_insert = [
                (product_id, mp, asin, "active", "")
                for mp, asin in asins.items()
                if mp not in existing_markets
            ]
            with conn:
                conn.executemany(
                    "INSERT INTO product_asins "
                    "(product_id, marketplace, asin, status, notes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    to_insert,
                )
            registered = len(to_insert)
            skipped = len(asins) - registered
    except Exception as e:
        logger.exception("register_market_asins failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data={"product_id": product_id, "registered": registered, "skipped": skipped},
    )


def import_yaml(
    project_config: str,
    tag: str | None = None,
    db_path: Path | str | None = None,
) -> ApiResponse:
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
                pid, _ = register_product(
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


def sync_registry(db_path: Path | str | None = None) -> ApiResponse:
    """Register orphan ASINs from keepa_products into the product registry.

    Scans ``keepa_products`` for entries not yet in ``product_asins`` and
    auto-registers those with a valid brand and title.
    """
    try:
        from amz_scout.db import sync_registry_from_keepa

        path = _get_db(db_path)
        with open_db(path) as conn:
            results = sync_registry_from_keepa(conn)

        registered = [r for r in results if r.get("registered")]
        skipped = [r for r in results if not r.get("registered")]
    except Exception as e:
        logger.exception("sync_registry failed")
        return _envelope(False, error=str(e))

    return _envelope(
        True,
        data=registered,
        registered=len(registered),
        skipped=len(skipped),
        skipped_details=skipped,
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
            results.append(
                {
                    "brand": brand,
                    "model": model,
                    "marketplace": mp,
                    "ok": False,
                    "error": "Missing brand, model, or marketplace",
                }
            )
            continue

        dr = discover_asin(
            brand=brand,
            model=model,
            marketplace=mp,
            headed=headed,
            db_path=db_path,
        )
        dr_data = dr["data"]
        new_asin = (
            dr_data.get("asin")
            if dr["ok"] and isinstance(dr_data, dict)
            else None
        )
        results.append(
            {
                "brand": brand,
                "model": model,
                "marketplace": mp,
                "old_asin": c.get("old_asin", ""),
                "ok": dr["ok"],
                "new_asin": new_asin,
                "error": dr.get("error"),
            }
        )

    found = sum(1 for r in results if r["ok"])
    return results, found, len(results) - found


def batch_discover(
    candidates: list[dict],
    headed: bool = False,
    db_path: Path | str | None = None,
) -> ApiResponse:
    """Execute ASIN discovery for a list of candidates.

    Each candidate dict must have: ``brand``, ``model``, ``marketplace``.
    Optional: ``old_asin`` (for tracking).

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
) -> ApiResponse:
    """Search Amazon to find the correct ASIN for a product on a marketplace.

    This is a **slow** operation (10-30 seconds) that launches a browser.
    Requires ``browser-use`` CLI to be installed.

    On success, writes the found ASIN to ``product_asins`` with status
    ``active``. Intent-level validation is intentionally omitted —
    interactive users verify the discovered product from its Keepa
    title in the next query (see v6 decision in
    docs/DEVELOPER.md "ASIN Status Semantics").

    Returns envelope with the found ASIN, or error if not found.
    """
    try:
        from amz_scout.browser import BrowserSession, check_browser_use_installed
        from amz_scout.marketplace import setup_marketplace

        if not check_browser_use_installed():
            return _envelope(
                False, error="browser-use CLI not installed. Install: uv tool install browser-use"
            )

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
                browser,
                product,
                site,
                mp_config,
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
                register_asin(
                    conn,
                    existing["id"],
                    site,
                    found_asin,
                    notes="discovered via browser search",
                )
            else:
                pid, _ = register_product(conn, "", brand, model, keywords)
                register_asin(
                    conn,
                    pid,
                    site,
                    found_asin,
                    notes="discovered via browser search",
                )

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
            "status": "active",
        },
    )
