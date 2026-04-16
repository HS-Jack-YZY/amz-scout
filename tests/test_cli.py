"""Unit tests for thin helpers inside `amz_scout.cli`.

The CLI command bodies themselves are exercised end-to-end by the wider test
suite via ``amz_scout.api`` (which the CLI delegates to). This file only
covers the stand-alone helpers whose contracts are easy to pin down without
spinning up Typer or the DB.
"""

from types import SimpleNamespace

import pytest

from amz_scout.cli import _resolve_target_sites
from amz_scout.models import Product


def _product_with_overrides(*sites: str) -> Product:
    # `marketplace_overrides` is dict[str, dict[str, str]]: the outer key is the
    # marketplace code (which is what `_resolve_target_sites` iterates over) and
    # the inner dict carries the actual ASIN / note for that marketplace.
    return Product(
        category="Router",
        brand="Vendor",
        model=f"M-{'-'.join(sites)}",
        default_asin="B0DEFAULT0",
        marketplace_overrides={s: {"asin": f"B0{s}000000"} for s in sites},
    )


@pytest.mark.unit
class TestResolveTargetSites:
    """Regression guard for deterministic CLI target-site ordering.

    Before the fix, `_resolve_target_sites` returned ``list({set comprehension})``
    in the DB-only branch, so the iteration order changed across runs (Python
    set ordering depends on hash randomization). That bled into CLI output
    rendering, downstream CSV write order, and DB upsert order — anywhere
    "the same command" should produce a stable diff.
    """

    def test_marketplace_flag_short_circuits(self):
        """Explicit `-m UK` always wins, regardless of products/config."""
        info = SimpleNamespace(config=None)
        assert _resolve_target_sites(info, [], "UK") == ["UK"]

    def test_yaml_config_branch_passes_through(self):
        """When `info.config` is present, the YAML's target_marketplaces wins
        and ordering is whatever YAML preserved (no re-sort needed)."""
        info = SimpleNamespace(
            config=SimpleNamespace(target_marketplaces=["UK", "DE", "FR"]),
        )
        assert _resolve_target_sites(info, [], None) == ["UK", "DE", "FR"]

    def test_db_only_branch_is_sorted(self):
        """No CLI flag, no YAML — DB product overrides feed the list. Output
        must be lexicographically sorted, deduped, and stable across runs."""
        info = SimpleNamespace(config=None)
        products = [
            _product_with_overrides("US", "DE"),
            _product_with_overrides("UK", "DE"),
            _product_with_overrides("JP"),
        ]
        result = _resolve_target_sites(info, products, None)
        assert result == ["DE", "JP", "UK", "US"]

    def test_db_only_branch_is_stable_across_calls(self):
        """Same inputs → same outputs, every call. Without the explicit sort,
        Python's hash randomization could surface different orderings between
        CLI invocations even within the same interpreter session."""
        info = SimpleNamespace(config=None)
        products = [_product_with_overrides("US", "DE", "UK", "JP", "BR")]
        first = _resolve_target_sites(info, products, None)
        second = _resolve_target_sites(info, products, None)
        third = _resolve_target_sites(info, products, None)
        assert first == second == third == ["BR", "DE", "JP", "UK", "US"]
