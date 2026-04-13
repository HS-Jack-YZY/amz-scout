"""Keepa data freshness strategy evaluation.

Contains both DB query helpers (query_freshness) and a pure-function core
(evaluate_freshness) that takes data in, returns decisions out with no
side effects — enabling trivial unit testing of the decision logic.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Literal

from amz_scout.db import query_keepa_fetched_at
from amz_scout.models import Product


class FreshnessStrategy(Enum):
    """Keepa data freshness strategy."""

    LAZY = "lazy"  # Use DB no matter how old; fetch only if missing
    OFFLINE = "offline"  # Use DB only; skip if missing
    MAX_AGE = "max_age"  # Use DB if <N days old; re-fetch if older or missing
    FRESH = "fresh"  # Always re-fetch from Keepa


@dataclass(frozen=True)
class ProductFreshness:
    """Freshness status for one product on one site."""

    asin: str
    site: str
    model: str
    brand: str
    fetched_at: str | None  # ISO date from DB, or None if never fetched
    age_days: int | None  # Days since last fetch, or None if never
    action: Literal["use_cache", "fetch", "skip"]
    reason: str  # Human-readable explanation


def query_freshness(
    conn: sqlite3.Connection,
    products: list[Product],
    sites: list[str],
) -> dict[tuple[str, str], tuple[str, str] | None]:
    """Query (fetched_at, fetch_mode) for each (asin, site) pair."""
    pairs = [(p.asin_for(s), s) for p in products for s in sites]
    return query_keepa_fetched_at(conn, pairs)


def evaluate_freshness(
    products: list[Product],
    sites: list[str],
    fetched_at_map: dict[tuple[str, str], tuple[str, str] | None],
    strategy: FreshnessStrategy,
    max_age_days: int = 7,
    today: str | None = None,
    requested_mode: str = "basic",
) -> list[ProductFreshness]:
    """Apply freshness strategy to determine action for each product/site pair.

    Pure function: no DB access, no side effects.

    *requested_mode* is ``"basic"`` or ``"detailed"``.  If the cached data
    was fetched in a lower mode than requested, it is treated as needing
    re-fetch regardless of age.
    """
    ref_date = date.fromisoformat(today) if today else date.today()
    results: list[ProductFreshness] = []

    for product in products:
        for site in sites:
            asin = product.asin_for(site)
            entry = fetched_at_map.get((asin, site))
            fetched_at = entry[0] if entry else None
            cached_mode = entry[1] if entry else None
            age_days = None
            if fetched_at:
                fetched_date = date.fromisoformat(fetched_at[:10])
                age_days = (ref_date - fetched_date).days

            action, reason = _decide(
                strategy,
                fetched_at,
                age_days,
                max_age_days,
                requested_mode,
                cached_mode,
            )
            results.append(
                ProductFreshness(
                    asin=asin,
                    site=site,
                    model=product.model,
                    brand=product.brand,
                    fetched_at=fetched_at,
                    age_days=age_days,
                    action=action,
                    reason=reason,
                )
            )

    return results


def _decide(
    strategy: FreshnessStrategy,
    fetched_at: str | None,
    age_days: int | None,
    max_age_days: int,
    requested_mode: str = "basic",
    cached_mode: str | None = None,
) -> tuple[Literal["use_cache", "fetch", "skip"], str]:
    """Return (action, reason) for a single product/site pair."""
    has_data = fetched_at is not None

    # Mode upgrade: basic cache cannot satisfy a detailed request
    if has_data and requested_mode == "detailed" and cached_mode == "basic":
        return "fetch", "cached data is basic, detailed requested"

    if strategy == FreshnessStrategy.LAZY:
        if has_data:
            return "use_cache", f"cached ({age_days}d ago)"
        return "fetch", "no cached data"

    if strategy == FreshnessStrategy.OFFLINE:
        if has_data:
            return "use_cache", f"cached ({age_days}d ago)"
        return "skip", "no cached data (offline mode)"

    if strategy == FreshnessStrategy.MAX_AGE:
        if has_data and age_days is not None and age_days < max_age_days:
            return "use_cache", f"fresh ({age_days}d < {max_age_days}d)"
        if has_data:
            return "fetch", f"stale ({age_days}d >= {max_age_days}d)"
        return "fetch", "no cached data"

    # FRESH
    if has_data:
        return "fetch", "force refresh"
    return "fetch", "no cached data"


def partition_by_action(
    freshness_results: list[ProductFreshness],
) -> tuple[list[ProductFreshness], list[ProductFreshness], list[ProductFreshness]]:
    """Split results into (use_cache, needs_fetch, skipped) lists."""
    cache = [r for r in freshness_results if r.action == "use_cache"]
    fetch = [r for r in freshness_results if r.action == "fetch"]
    skip = [r for r in freshness_results if r.action == "skip"]
    return cache, fetch, skip


def format_freshness_matrix(
    freshness_results: list[ProductFreshness],
    sites: list[str],
) -> list[dict]:
    """Format freshness results as rows for table display.

    Each row = one product model, columns = sites with age/status.
    """
    by_model: dict[str, dict[str, str]] = {}
    for r in freshness_results:
        if r.model not in by_model:
            by_model[r.model] = {"model": r.model, "brand": r.brand}
        cell = "never" if r.age_days is None else f"{r.age_days}d"
        by_model[r.model][r.site] = cell

    return list(by_model.values())


def resolve_strategy(
    lazy: bool = False,
    offline: bool = False,
    max_age: int | None = None,
    fresh: bool = False,
) -> tuple[FreshnessStrategy, int]:
    """Resolve CLI flags into (strategy, max_age_days).

    Raises ValueError if multiple strategy flags are specified.
    Default: MAX_AGE with 7 days.
    """
    flags = sum([lazy, offline, max_age is not None, fresh])
    if flags > 1:
        raise ValueError(
            "Only one strategy flag may be specified: --lazy, --offline, --max-age, or --fresh"
        )

    if lazy:
        return FreshnessStrategy.LAZY, 0
    if offline:
        return FreshnessStrategy.OFFLINE, 0
    if fresh:
        return FreshnessStrategy.FRESH, 0
    if max_age is not None:
        return FreshnessStrategy.MAX_AGE, max_age
    # Default
    return FreshnessStrategy.MAX_AGE, 7
