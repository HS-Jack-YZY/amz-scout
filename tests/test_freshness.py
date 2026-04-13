"""Tests for amz_scout.freshness — pure function strategy evaluation."""

import pytest

from amz_scout.freshness import (
    FreshnessStrategy,
    ProductFreshness,
    evaluate_freshness,
    format_freshness_matrix,
    partition_by_action,
    resolve_strategy,
)
from amz_scout.models import Product


def _product(model: str = "RT-BE58", asin: str = "B0FGDRP3VZ") -> Product:
    return Product(
        category="Router",
        brand="ASUS",
        model=model,
        default_asin=asin,
    )


class TestEvaluateFreshness:
    def test_lazy_uses_cache_when_data_exists(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2025-01-01", "basic")}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.LAZY,
            today="2026-04-01",
        )
        assert len(results) == 1
        assert results[0].action == "use_cache"

    def test_lazy_fetches_when_no_data(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): None}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.LAZY,
            today="2026-04-01",
        )
        assert results[0].action == "fetch"

    def test_offline_uses_cache_when_data_exists(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2025-01-01", "basic")}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.OFFLINE,
            today="2026-04-01",
        )
        assert results[0].action == "use_cache"

    def test_offline_skips_when_no_data(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): None}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.OFFLINE,
            today="2026-04-01",
        )
        assert results[0].action == "skip"

    def test_max_age_uses_cache_when_fresh(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-03-30", "basic")}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.MAX_AGE,
            max_age_days=7,
            today="2026-04-01",
        )
        assert results[0].action == "use_cache"
        assert results[0].age_days == 2

    def test_max_age_refetches_when_stale(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-03-20", "basic")}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.MAX_AGE,
            max_age_days=7,
            today="2026-04-01",
        )
        assert results[0].action == "fetch"
        assert results[0].age_days == 12

    def test_max_age_fetches_when_no_data(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): None}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.MAX_AGE,
            max_age_days=7,
            today="2026-04-01",
        )
        assert results[0].action == "fetch"

    def test_fresh_always_fetches(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-04-01", "basic")}
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.FRESH,
            today="2026-04-01",
        )
        assert results[0].action == "fetch"
        assert results[0].reason == "force refresh"

    def test_multiple_products_and_sites(self):
        products = [_product("A", "B0AAAAAAA1"), _product("B", "B0BBBBBBB2")]
        sites = ["UK", "DE"]
        fetched = {
            ("B0AAAAAAA1", "UK"): ("2026-03-31", "basic"),
            ("B0AAAAAAA1", "DE"): None,
            ("B0BBBBBBB2", "UK"): ("2026-03-20", "basic"),
            ("B0BBBBBBB2", "DE"): ("2026-03-31", "basic"),
        }
        results = evaluate_freshness(
            products,
            sites,
            fetched,
            FreshnessStrategy.MAX_AGE,
            max_age_days=7,
            today="2026-04-01",
        )
        assert len(results) == 4
        actions = {(r.model, r.site): r.action for r in results}
        assert actions[("A", "UK")] == "use_cache"
        assert actions[("A", "DE")] == "fetch"
        assert actions[("B", "UK")] == "fetch"
        assert actions[("B", "DE")] == "use_cache"


class TestPartitionByAction:
    def test_partitions_correctly(self):
        items = [
            ProductFreshness("A1", "UK", "M1", "B1", "2026-03-31", 1, "use_cache", ""),
            ProductFreshness("A2", "UK", "M2", "B2", None, None, "fetch", ""),
            ProductFreshness("A3", "UK", "M3", "B3", None, None, "skip", ""),
        ]
        cache, fetch, skip = partition_by_action(items)
        assert len(cache) == 1
        assert len(fetch) == 1
        assert len(skip) == 1

    def test_empty_input(self):
        cache, fetch, skip = partition_by_action([])
        assert cache == fetch == skip == []


class TestFormatFreshnessMatrix:
    def test_pivot_by_site(self):
        items = [
            ProductFreshness("A1", "UK", "ModelX", "Brand", "2026-03-31", 1, "use_cache", ""),
            ProductFreshness("A1", "DE", "ModelX", "Brand", None, None, "fetch", ""),
        ]
        rows = format_freshness_matrix(items, ["UK", "DE"])
        assert len(rows) == 1
        assert rows[0]["model"] == "ModelX"
        assert rows[0]["UK"] == "1d"
        assert rows[0]["DE"] == "never"


class TestResolveStrategy:
    def test_default_is_max_age_7(self):
        strategy, days = resolve_strategy()
        assert strategy == FreshnessStrategy.MAX_AGE
        assert days == 7

    def test_lazy_flag(self):
        strategy, _ = resolve_strategy(lazy=True)
        assert strategy == FreshnessStrategy.LAZY

    def test_offline_flag(self):
        strategy, _ = resolve_strategy(offline=True)
        assert strategy == FreshnessStrategy.OFFLINE

    def test_fresh_flag(self):
        strategy, _ = resolve_strategy(fresh=True)
        assert strategy == FreshnessStrategy.FRESH

    def test_max_age_custom(self):
        strategy, days = resolve_strategy(max_age=3)
        assert strategy == FreshnessStrategy.MAX_AGE
        assert days == 3

    def test_mutual_exclusivity(self):
        with pytest.raises(ValueError, match="Only one strategy"):
            resolve_strategy(lazy=True, fresh=True)


class TestFetchModeUpgrade:
    """Test that basic cache triggers re-fetch when detailed is requested."""

    def test_basic_cache_with_detailed_request_fetches(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-04-01", "basic")}
        results = evaluate_freshness(
            products, sites, fetched, FreshnessStrategy.LAZY,
            today="2026-04-01", requested_mode="detailed",
        )
        assert results[0].action == "fetch"
        assert "basic" in results[0].reason and "detailed" in results[0].reason

    def test_detailed_cache_with_detailed_request_uses_cache(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-04-01", "detailed")}
        results = evaluate_freshness(
            products, sites, fetched, FreshnessStrategy.LAZY,
            today="2026-04-01", requested_mode="detailed",
        )
        assert results[0].action == "use_cache"

    def test_basic_request_uses_basic_cache(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-04-01", "basic")}
        results = evaluate_freshness(
            products, sites, fetched, FreshnessStrategy.LAZY,
            today="2026-04-01", requested_mode="basic",
        )
        assert results[0].action == "use_cache"

    def test_detailed_cache_with_basic_request_uses_cache(self):
        products = [_product()]
        sites = ["UK"]
        fetched = {("B0FGDRP3VZ", "UK"): ("2026-04-01", "detailed")}
        results = evaluate_freshness(
            products, sites, fetched, FreshnessStrategy.LAZY,
            today="2026-04-01", requested_mode="basic",
        )
        assert results[0].action == "use_cache"
