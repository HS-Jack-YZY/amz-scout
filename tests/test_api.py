"""Tests for amz_scout.api — programmatic API layer."""

import sqlite3

import pytest
import yaml

from amz_scout.api import (
    _add_dates,
    _envelope,
    _load_project,
    _resolve_asin,
    _resolve_context,
    _resolve_site,
    add_product,
    check_freshness,
    discover_asin,
    ensure_keepa_data,
    import_yaml,
    keepa_budget,
    list_products,
    query_availability,
    query_compare,
    query_deals,
    query_latest,
    query_ranking,
    query_sellers,
    query_trends,
    register_asin_from_url,
    remove_product_by_model,
    resolve_product,
    resolve_project,
    update_product_asin,
)
from amz_scout.db import (
    init_schema,
    register_asin,
    register_product,
    upsert_competitive,
)
from amz_scout.models import CompetitiveData, Product

# ─── Fixtures ────────────────────────────────────────────────────────

MARKETPLACE_YAML = {
    "marketplaces": {
        "UK": {
            "amazon_domain": "amazon.co.uk",
            "keepa_domain": "GB",
            "keepa_domain_code": 2,
            "currency_code": "GBP",
            "currency_symbol": "£",
            "region": "eu",
            "delivery_postcode": "SW1A 1AA",
        },
        "DE": {
            "amazon_domain": "amazon.de",
            "keepa_domain": "DE",
            "keepa_domain_code": 3,
            "currency_code": "EUR",
            "currency_symbol": "€",
            "region": "eu",
            "delivery_postcode": "10115",
        },
    }
}

PROJECT_YAML = {
    "project": {"name": "test_api", "description": "API test project"},
    "target_marketplaces": ["UK", "DE"],
    "products": [
        {
            "category": "Router",
            "brand": "GL.iNet",
            "model": "GL-Slate 7 (GL-BE3600)",
            "default_asin": "B0F2MR53D6",
            "marketplace_overrides": {"UK": {"asin": "B0UKSPECIF"}},
        },
        {
            "category": "Router",
            "brand": "ASUS",
            "model": "RT-BE58",
            "default_asin": "B0FGDRP3VZ",
        },
    ],
}

# raw_data fixture is provided by conftest.py (synthetic + real fallback)


@pytest.fixture
def config_dir(tmp_path):
    """Create a temp config directory with project + marketplace YAMLs."""
    mp_path = tmp_path / "marketplaces.yaml"
    proj_path = tmp_path / "test_api.yaml"

    # Write marketplace config
    with open(mp_path, "w") as f:
        yaml.dump(MARKETPLACE_YAML, f)

    # Write project config with output_dir pointing to a temp location
    proj_data = {**PROJECT_YAML}
    proj_data["project"] = {
        **PROJECT_YAML["project"],
        "output_dir": str(tmp_path / "output" / "test_api"),
    }
    with open(proj_path, "w") as f:
        yaml.dump(proj_data, f)

    # Create the output dir and a DB
    db_dir = tmp_path / "output"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "amz_scout.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.close()

    return tmp_path, str(proj_path)


@pytest.fixture
def test_db(config_dir):
    """Return the temp DB path for product registry tests."""
    tmp_path, _ = config_dir
    return tmp_path / "output" / "amz_scout.db"


@pytest.fixture
def seeded_config(config_dir):
    """Config dir with some competitive data seeded in DB."""
    tmp_path, proj_path = config_dir
    db_path = tmp_path / "output" / "amz_scout.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    upsert_competitive(
        conn,
        [
            CompetitiveData(
                date="2026-04-01",
                site="UK",
                category="Router",
                brand="GL.iNet",
                model="GL-Slate 7 (GL-BE3600)",
                asin="B0F2MR53D6",
                title="Slate 7",
                price="£150.99",
                rating="4.5",
                review_count="120",
                bought_past_month="100+",
                bsr="2591",
                available="Yes",
                url="https://amazon.co.uk/dp/B0F2MR53D6",
            ),
            CompetitiveData(
                date="2026-04-01",
                site="UK",
                category="Router",
                brand="ASUS",
                model="RT-BE58",
                asin="B0FGDRP3VZ",
                title="RT-BE58",
                price="£99.97",
                rating="4.3",
                review_count="45",
                bought_past_month="50+",
                bsr="4952",
                available="Yes",
                url="https://amazon.co.uk/dp/B0FGDRP3VZ",
            ),
        ],
    )
    conn.close()

    return tmp_path, proj_path


# ─── Internal helpers ────────────────────────────────────────────────


class TestEnvelope:
    def test_success_envelope(self):
        r = _envelope(True, data=[{"a": 1}], count=1)
        assert r["ok"] is True
        assert r["data"] == [{"a": 1}]
        assert r["error"] is None
        assert r["meta"]["count"] == 1

    def test_error_envelope(self):
        r = _envelope(False, error="something failed")
        assert r["ok"] is False
        assert r["data"] == []
        assert r["error"] == "something failed"

    def test_default_data_is_empty_list(self):
        r = _envelope(True)
        assert r["data"] == []


class TestLoadProject:
    def test_loads_from_path(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert info.config.project.name == "test_api"
        assert len(info.products) == 2
        assert "UK" in info.marketplaces

    def test_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            _load_project("/nonexistent/path.yaml")


class TestResolveAsin:
    def _products(self):
        return [
            Product(
                category="Router",
                brand="GL.iNet",
                model="GL-Slate 7 (GL-BE3600)",
                default_asin="B0F2MR53D6",
                marketplace_overrides={"UK": {"asin": "B0UKSPECIF"}},
            ),
            Product(category="Router", brand="ASUS", model="RT-BE58", default_asin="B0FGDRP3VZ"),
        ]

    def test_model_substring_match(self):
        asin, model, brand, source, _warns = _resolve_asin(self._products(), "Slate 7", "UK")
        assert asin == "B0UKSPECIF"  # UK-specific override
        assert model == "GL-Slate 7 (GL-BE3600)"
        assert brand == "GL.iNet"
        assert source == "config"

    def test_model_substring_default_asin(self):
        asin, model, brand, source, _warns = _resolve_asin(self._products(), "Slate 7", "DE")
        assert asin == "B0F2MR53D6"  # Default ASIN (no DE override)

    def test_model_substring_no_marketplace(self):
        asin, _, _, _, _ = _resolve_asin(self._products(), "RT-BE58")
        assert asin == "B0FGDRP3VZ"

    def test_asin_passthrough(self):
        asin, model, brand, source, _warns = _resolve_asin(self._products(), "B0XYZABCDE")
        assert asin == "B0XYZABCDE"
        assert brand == ""
        assert source == "asin"

    def test_not_found_raises(self):
        with pytest.raises(ValueError, match="Product not found"):
            _resolve_asin(self._products(), "NonExistent")

    def test_case_insensitive(self):
        asin, _, _, _, _ = _resolve_asin(self._products(), "slate 7", "UK")
        assert asin == "B0UKSPECIF"


class TestAddDates:
    def test_adds_date_field(self):
        rows = [{"keepa_ts": 7351680, "value": 100}]
        result = _add_dates(rows)
        assert "date" in result[0]
        assert result[0]["date"].startswith("2024-")

    def test_preserves_rows_without_keepa_ts(self):
        rows = [{"price": 100}]
        result = _add_dates(rows)
        assert result == rows

    def test_does_not_mutate_input(self):
        rows = [{"keepa_ts": 7351680, "value": 100}]
        _add_dates(rows)
        assert "date" not in rows[0]


# ─── Public functions ────────────────────────────────────────────────


class TestResolveProject:
    def test_resolves_by_path(self, config_dir):
        _, proj_path = config_dir
        r = resolve_project(proj_path)
        assert r["ok"] is True
        assert r["data"]["name"] == "test_api"
        assert len(r["data"]["products"]) == 2
        assert r["data"]["target_marketplaces"] == ["UK", "DE"]

    def test_db_exists_flag(self, config_dir):
        _, proj_path = config_dir
        r = resolve_project(proj_path)
        assert r["data"]["db_exists"] is True

    def test_not_found_returns_error(self):
        r = resolve_project("/nonexistent/project.yaml")
        assert r["ok"] is False
        assert "not found" in r["error"].lower()

    def test_products_have_per_site_asins(self, config_dir):
        _, proj_path = config_dir
        r = resolve_project(proj_path)
        slate = r["data"]["products"][0]
        assert slate["asin_UK"] == "B0UKSPECIF"  # Override
        assert slate["asin_DE"] == "B0F2MR53D6"  # Default


class TestResolveProduct:
    def test_model_substring(self, config_dir):
        _, proj_path = config_dir
        r = resolve_product(proj_path, "Slate 7", "UK")
        assert r["ok"] is True
        assert r["data"]["asin"] == "B0UKSPECIF"
        assert r["data"]["source"] == "config"

    def test_asin_passthrough(self, config_dir):
        _, proj_path = config_dir
        r = resolve_product(proj_path, "B0XYZABCDE")
        assert r["ok"] is True
        assert r["data"]["asin"] == "B0XYZABCDE"
        assert r["data"]["source"] == "asin"

    def test_not_found(self, config_dir):
        _, proj_path = config_dir
        r = resolve_product(proj_path, "NoSuchProduct")
        assert r["ok"] is False


class TestQueryLatest:
    def test_returns_data(self, seeded_config):
        _, proj_path = seeded_config
        r = query_latest(proj_path, marketplace="UK")
        assert r["ok"] is True
        assert len(r["data"]) == 2

    def test_empty_db(self, config_dir):
        _, proj_path = config_dir
        r = query_latest(proj_path, marketplace="UK")
        assert r["ok"] is True
        assert r["data"] == []


class TestQueryTrends:
    def test_resolves_product_from_config(self, seeded_config):
        """Test that product resolution uses config, not DB."""
        _, proj_path = seeded_config
        r = query_trends(proj_path, product="Slate 7", marketplace="UK")
        assert r["ok"] is True
        assert r["meta"]["model"] == "GL-Slate 7 (GL-BE3600)"
        # No Keepa time series in the test DB, so data is empty
        assert r["meta"]["count"] == 0

    def test_bad_product(self, config_dir):
        _, proj_path = config_dir
        r = query_trends(proj_path, product="NoSuchProduct", marketplace="UK")
        assert r["ok"] is False
        assert "not found" in r["error"].lower()


class TestQueryCompare:
    def test_returns_data(self, seeded_config):
        _, proj_path = seeded_config
        r = query_compare(proj_path, product="Slate 7")
        assert r["ok"] is True
        # Should find the UK competitive snapshot
        assert r["meta"]["count"] >= 1


class TestQueryRanking:
    def test_returns_ranked(self, seeded_config):
        _, proj_path = seeded_config
        r = query_ranking(proj_path, marketplace="UK")
        assert r["ok"] is True
        if r["data"]:
            # Should be sorted by BSR ascending
            bsrs = [row["bsr"] for row in r["data"] if row["bsr"] is not None]
            assert bsrs == sorted(bsrs)


class TestQueryAvailability:
    def test_returns_matrix(self, seeded_config):
        _, proj_path = seeded_config
        r = query_availability(proj_path)
        assert r["ok"] is True


class TestQuerySellers:
    def test_resolves_product(self, seeded_config):
        _, proj_path = seeded_config
        r = query_sellers(proj_path, product="Slate 7", marketplace="UK")
        assert r["ok"] is True
        assert r["meta"]["model"] == "GL-Slate 7 (GL-BE3600)"


class TestQueryDeals:
    def test_returns_deals(self, config_dir):
        _, proj_path = config_dir
        r = query_deals(proj_path, marketplace="UK")
        assert r["ok"] is True


class TestCheckFreshness:
    def test_returns_matrix(self, config_dir):
        _, proj_path = config_dir
        r = check_freshness(proj_path)
        assert r["ok"] is True
        assert r["meta"]["count"] == 2  # 2 products


class TestEnsureKeepaData:
    def test_offline_never_fetches(self, config_dir):
        _, proj_path = config_dir
        r = ensure_keepa_data(proj_path, strategy="offline")
        assert r["ok"] is True
        assert r["meta"]["fetched"] == 0
        assert r["meta"]["tokens_used"] == 0

    def test_invalid_strategy(self, config_dir):
        _, proj_path = config_dir
        r = ensure_keepa_data(proj_path, strategy="invalid")
        assert r["ok"] is False
        assert "Unknown strategy" in r["error"]

    def test_with_product_filter(self, config_dir):
        _, proj_path = config_dir
        r = ensure_keepa_data(proj_path, product="Slate 7", strategy="offline")
        assert r["ok"] is True
        # Should only have outcomes for the one matched product
        outcomes = r["data"]["outcomes"]
        models = {o["model"] for o in outcomes}
        assert models == {"GL-Slate 7 (GL-BE3600)"}


class TestKeepaBudget:
    def test_returns_budget(self):
        """This test hits the real Keepa API but only the token endpoint."""
        r = keepa_budget()
        if not r["ok"]:
            pytest.skip("Keepa API key not configured")
        assert "tokens_available" in r["data"]
        assert r["data"]["tokens_max"] == 60


# ─── Smart query tests ──────────────────────────────────────────────


class TestResolveSite:
    def test_case_insensitive(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert _resolve_site("uk", info.marketplace_aliases) == "UK"
        assert _resolve_site("UK", info.marketplace_aliases) == "UK"
        assert _resolve_site("de", info.marketplace_aliases) == "DE"

    def test_keepa_domain_alias(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert _resolve_site("GB", info.marketplace_aliases) == "UK"
        assert _resolve_site("gb", info.marketplace_aliases) == "UK"

    def test_amazon_domain_alias(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert _resolve_site("amazon.co.uk", info.marketplace_aliases) == "UK"
        assert _resolve_site("amazon.de", info.marketplace_aliases) == "DE"

    def test_unknown_passes_through(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert _resolve_site("XX", info.marketplace_aliases) == "XX"
        assert _resolve_site("unknown", info.marketplace_aliases) == "unknown"

    def test_none_returns_none(self, config_dir):
        _, proj_path = config_dir
        info = _load_project(proj_path)
        assert _resolve_site(None, info.marketplace_aliases) is None


class TestAutoFetch:
    def test_auto_fetch_false_skips_fetch(self, config_dir):
        """auto_fetch=False should never trigger Keepa API."""
        _, proj_path = config_dir
        r = query_trends(proj_path, product="Slate 7", marketplace="UK", auto_fetch=False)
        assert r["ok"] is True
        assert "auto_fetched" not in r["meta"]

    def test_auto_fetch_true_reports_status(self, config_dir):
        """auto_fetch=True should report fetch status in meta."""
        _, proj_path = config_dir
        r = query_trends(proj_path, product="Slate 7", marketplace="UK", auto_fetch=True)
        assert r["ok"] is True
        assert "auto_fetched" in r["meta"]

    def test_sellers_auto_fetch(self, config_dir):
        _, proj_path = config_dir
        r = query_sellers(proj_path, product="Slate 7", marketplace="UK", auto_fetch=True)
        assert r["ok"] is True
        assert "auto_fetched" in r["meta"]

    def test_deals_auto_fetch(self, config_dir):
        _, proj_path = config_dir
        r = query_deals(proj_path, marketplace="UK", auto_fetch=True)
        assert r["ok"] is True
        assert "auto_fetched" in r["meta"]

    def test_deals_auto_fetch_false(self, config_dir):
        _, proj_path = config_dir
        r = query_deals(proj_path, marketplace="UK", auto_fetch=False)
        assert r["ok"] is True
        assert "auto_fetched" not in r["meta"]


class TestAutoFetchErrorWarnings:
    """Bug B (issue #13): when `_auto_fetch` catches a Keepa failure and sets
    ``auto_fetch_error`` in meta, the calling query must surface that signal
    into ``meta["warnings"]`` so the LLM-facing summary (which allowlists
    ``warnings`` but drops unknown meta keys) sees the freshness caveat.

    These tests patch ``amz_scout.keepa_service.get_keepa_data`` — the
    function is imported inside ``_auto_fetch``'s try block every call, so
    the patch must target the source module.
    """

    def _boom(self, *_a, **_kw):
        raise RuntimeError("HTTP 429 rate limited")

    def test_query_trends_auto_fetch_error_surfaces_as_warning(
        self, config_dir, monkeypatch
    ):
        _, proj_path = config_dir
        monkeypatch.setattr("amz_scout.keepa_service.get_keepa_data", self._boom)

        r = query_trends(proj_path, product="Slate 7", marketplace="UK")
        warnings = r["meta"].get("warnings") or []
        assert any("auto-fetch failed" in w.lower() for w in warnings), (
            f"Bug B regression: auto_fetch_error did not reach envelope warnings. "
            f"meta={r['meta']!r}"
        )
        assert r["meta"].get("auto_fetch_error") is True, (
            "Backward-compat: legacy meta flag must still be present"
        )

    def test_query_sellers_auto_fetch_error_surfaces_as_warning(
        self, config_dir, monkeypatch
    ):
        _, proj_path = config_dir
        monkeypatch.setattr("amz_scout.keepa_service.get_keepa_data", self._boom)

        r = query_sellers(proj_path, product="Slate 7", marketplace="UK")
        warnings = r["meta"].get("warnings") or []
        assert any("auto-fetch failed" in w.lower() for w in warnings), (
            f"Bug B regression: auto_fetch_error did not reach envelope warnings. "
            f"meta={r['meta']!r}"
        )
        assert r["meta"].get("auto_fetch_error") is True

    def test_query_deals_auto_fetch_error_surfaces_as_warning(
        self, config_dir, monkeypatch
    ):
        _, proj_path = config_dir
        monkeypatch.setattr("amz_scout.keepa_service.get_keepa_data", self._boom)

        r = query_deals(proj_path, marketplace="UK")
        warnings = r["meta"].get("warnings") or []
        assert any("auto-fetch failed" in w.lower() for w in warnings), (
            f"Bug B regression: auto_fetch_error did not reach envelope warnings. "
            f"meta={r['meta']!r}"
        )
        assert r["meta"].get("auto_fetch_error") is True


class TestMarketplaceAliasInQueries:
    def test_trends_with_alias(self, config_dir):
        _, proj_path = config_dir
        r = query_trends(proj_path, product="Slate 7", marketplace="gb", auto_fetch=False)
        assert r["ok"] is True

    def test_ranking_with_alias(self, seeded_config):
        _, proj_path = seeded_config
        r = query_ranking(proj_path, marketplace="gb")
        assert r["ok"] is True

    def test_sellers_with_alias(self, config_dir):
        _, proj_path = config_dir
        r = query_sellers(proj_path, product="RT-BE58", marketplace="gb", auto_fetch=False)
        assert r["ok"] is True

    def test_ensure_keepa_data_with_alias(self, config_dir):
        _, proj_path = config_dir
        r = ensure_keepa_data(proj_path, marketplace="gb", strategy="offline")
        assert r["ok"] is True

    def test_check_freshness_with_alias(self, config_dir):
        _, proj_path = config_dir
        r = check_freshness(proj_path, marketplace="gb")
        assert r["ok"] is True


class TestBrowserQueryHint:
    def test_latest_empty_has_hint(self, config_dir):
        _, proj_path = config_dir
        r = query_latest(proj_path, marketplace="UK")
        assert r["ok"] is True
        assert r["data"] == []
        assert "hint" in r["meta"]
        assert "scrape" in r["meta"]["hint"].lower()

    def test_latest_with_data_no_hint(self, seeded_config):
        _, proj_path = seeded_config
        r = query_latest(proj_path, marketplace="UK")
        assert r["ok"] is True
        assert len(r["data"]) > 0
        assert "hint" not in r["meta"]

    def test_compare_empty_has_hint(self, config_dir):
        _, proj_path = config_dir
        r = query_compare(proj_path, product="NonExistent")
        assert "hint" in r["meta"]

    def test_ranking_empty_has_hint(self, config_dir):
        _, proj_path = config_dir
        r = query_ranking(proj_path, marketplace="UK")
        assert "hint" in r["meta"]

    def test_availability_empty_has_hint(self, config_dir):
        _, proj_path = config_dir
        r = query_availability(proj_path)
        assert "hint" in r["meta"]


# ─── Product registry tests ─────────────────────────────────────────


class TestAddProduct:
    def test_basic_add(self, test_db):
        r = add_product("Router", "TestBrand", "TestModel", db_path=test_db)
        assert r["ok"] is True
        assert r["data"]["brand"] == "TestBrand"
        assert r["data"]["id"] > 0

    def test_add_with_asins(self, test_db):
        r = add_product(
            "Router",
            "TestBrand",
            "WithASINs",
            asins={"UK": "B0TESTUK01", "DE": "B0TESTDE01"},
            db_path=test_db,
        )
        assert r["ok"] is True
        assert r["meta"]["asins_registered"] == 2

    def test_add_with_tag(self, test_db):
        r = add_product("Router", "TestBrand", "Tagged", tag="my_project", db_path=test_db)
        assert r["ok"] is True
        r2 = list_products(tag="my_project", db_path=test_db)
        assert r2["ok"] is True
        assert r2["meta"]["count"] >= 1

    def test_add_duplicate_returns_existing_id(self, test_db):
        r1 = add_product("Router", "TestBrand", "DupTest", db_path=test_db)
        r2 = add_product("Router", "TestBrand", "DupTest", db_path=test_db)
        assert r1["ok"] and r2["ok"]
        assert r1["data"]["id"] == r2["data"]["id"]


class TestListProducts:
    def test_empty_db(self, test_db):
        r = list_products(db_path=test_db)
        assert r["ok"] is True
        assert r["data"] == []

    def test_filter_by_category(self, test_db):
        add_product("Router", "BrandA", "ModelA", db_path=test_db)
        add_product("Switch", "BrandB", "ModelB", db_path=test_db)
        r = list_products(category="Router", db_path=test_db)
        models = {p["model"] for p in r["data"]}
        assert "ModelA" in models
        assert "ModelB" not in models

    def test_filter_by_brand(self, test_db):
        add_product("Router", "BrandX", "ModelX", db_path=test_db)
        add_product("Router", "BrandY", "ModelY", db_path=test_db)
        r = list_products(brand="BrandX", db_path=test_db)
        assert all(p["brand"] == "BrandX" for p in r["data"])

    def test_filter_by_marketplace(self, test_db):
        add_product("Router", "BrandM", "OnlyUK", asins={"UK": "B0UKASIN01"}, db_path=test_db)
        add_product("Router", "BrandM", "OnlyDE", asins={"DE": "B0DEASIN01"}, db_path=test_db)
        r = list_products(marketplace="UK", db_path=test_db)
        models = {p["model"] for p in r["data"]}
        assert "OnlyUK" in models
        assert "OnlyDE" not in models


class TestRemoveProduct:
    def test_remove_existing(self, test_db):
        add_product("Router", "ToRemove", "RemoveMe", db_path=test_db)
        r = remove_product_by_model("ToRemove", "RemoveMe", db_path=test_db)
        assert r["ok"] is True
        assert r["data"]["removed"] is True
        r2 = list_products(brand="ToRemove", db_path=test_db)
        assert r2["meta"]["count"] == 0

    def test_remove_nonexistent(self, test_db):
        r = remove_product_by_model("NoSuch", "Product", db_path=test_db)
        assert r["ok"] is False
        assert "not found" in r["error"].lower()

    def test_remove_cascades_asins(self, test_db):
        add_product("Router", "Cascade", "Test", asins={"UK": "B0CASCADE1"}, db_path=test_db)
        remove_product_by_model("Cascade", "Test", db_path=test_db)
        r = list_products(brand="Cascade", db_path=test_db)
        assert r["meta"]["count"] == 0


class TestUpdateProductAsin:
    def test_update_existing_product(self, test_db):
        add_product("Router", "UpdateMe", "Model1", asins={"UK": "B0OLDASIN1"}, db_path=test_db)
        r = update_product_asin("UpdateMe", "Model1", "UK", "B0NEWASIN1", db_path=test_db)
        assert r["ok"] is True
        assert r["data"]["asin"] == "B0NEWASIN1"

    def test_add_new_marketplace(self, test_db):
        add_product("Router", "AddMP", "Model2", asins={"UK": "B0UKASIN02"}, db_path=test_db)
        r = update_product_asin("AddMP", "Model2", "DE", "B0DEASIN02", db_path=test_db)
        assert r["ok"] is True
        r2 = list_products(brand="AddMP", marketplace="DE", db_path=test_db)
        assert r2["meta"]["count"] == 1

    def test_update_nonexistent_product(self, test_db):
        r = update_product_asin("NoSuch", "Product", "UK", "B0WHATEVER", db_path=test_db)
        assert r["ok"] is False

    def test_update_with_status(self, test_db):
        add_product("Router", "StatusTest", "Model3", db_path=test_db)
        r = update_product_asin(
            "StatusTest",
            "Model3",
            "UK",
            "B0STATUS01",
            status="not_listed",
            notes="observed delisted on amazon.co.uk",
            db_path=test_db,
        )
        assert r["ok"] is True
        assert r["data"]["status"] == "not_listed"


class TestImportYaml:
    def test_import_project(self, config_dir, test_db):
        _, proj_path = config_dir
        r = import_yaml(proj_path, db_path=test_db)
        assert r["ok"] is True
        assert r["data"]["products_imported"] == 2
        assert r["data"]["tag"] == "test_api"

    def test_import_with_custom_tag(self, config_dir, test_db):
        _, proj_path = config_dir
        r = import_yaml(proj_path, tag="custom_tag", db_path=test_db)
        assert r["ok"] is True
        assert r["data"]["tag"] == "custom_tag"
        r2 = list_products(tag="custom_tag", db_path=test_db)
        assert r2["meta"]["count"] > 0

    def test_import_idempotent(self, config_dir, test_db):
        _, proj_path = config_dir
        r1 = import_yaml(proj_path, db_path=test_db)
        r2 = import_yaml(proj_path, db_path=test_db)
        assert r1["ok"] and r2["ok"]
        assert r1["data"]["products_imported"] == r2["data"]["products_imported"]

    def test_import_creates_marketplace_asins(self, config_dir, test_db):
        _, proj_path = config_dir
        import_yaml(proj_path, db_path=test_db)
        # GL-Slate 7 has UK override (B0UKSPECIF) and default for DE
        r = list_products(brand="GL.iNet", marketplace="UK", db_path=test_db)
        uk_asins = [p["asin"] for p in r["data"] if "Slate" in p["model"]]
        assert "B0UKSPECIF" in uk_asins

        r = list_products(brand="GL.iNet", marketplace="DE", db_path=test_db)
        de_asins = [p["asin"] for p in r["data"] if "Slate" in p["model"]]
        assert "B0F2MR53D6" in de_asins  # Default ASIN for DE

    def test_import_nonexistent_yaml(self, test_db):
        r = import_yaml("/nonexistent/config.yaml", db_path=test_db)
        assert r["ok"] is False


# ─── Dual-resolution and DB-backed query tests ──────────────────────


class TestResolveAsinDualMode:
    """Test that _resolve_asin checks DB registry before falling back to config."""

    def test_db_takes_priority(self, test_db):
        """Product in DB registry is found via DB path."""
        with sqlite3.connect(str(test_db)) as conn:
            conn.row_factory = sqlite3.Row
            pid, _ = register_product(conn, "Router", "TestBrand", "TestRouter")
            register_asin(conn, pid, "UK", "B0DBFOUND1")

            asin, model, brand, source, _warns = _resolve_asin([], "TestRouter", "UK", conn=conn)
            assert asin == "B0DBFOUND1"
            assert brand == "TestBrand"
            assert source == "db"

    def test_config_fallback_when_not_in_db(self):
        """Falls back to config products when DB has no match."""
        products = [
            Product(
                category="Router",
                brand="FallbackBrand",
                model="FallbackModel",
                default_asin="B0FALLBACK",
            )
        ]
        asin, _, brand, source, _ = _resolve_asin(products, "FallbackModel")
        assert asin == "B0FALLBACK"
        assert brand == "FallbackBrand"
        assert source == "config"

    def test_asin_passthrough_still_works(self, test_db):
        """Direct ASIN input works even with empty DB and empty config."""
        with sqlite3.connect(str(test_db)) as conn:
            conn.row_factory = sqlite3.Row
            asin, _, brand, source, _ = _resolve_asin([], "B0DIRECTIN", conn=conn)
            assert asin == "B0DIRECTIN"
            assert brand == ""
            assert source == "asin"


class TestResolveContext:
    """Test _resolve_context dual-source resolution."""

    def test_with_project_loads_yaml(self, config_dir):
        _, proj_path = config_dir
        ctx = _resolve_context(proj_path)
        assert len(ctx.products) == 2
        assert ctx.config is not None

    def test_without_project_loads_from_db(self):
        """When project=None, loads from DB + marketplaces.yaml."""
        ctx = _resolve_context(None)
        assert ctx.config is None
        assert ctx.db_path.name == "amz_scout.db"
        assert "UK" in ctx.marketplaces


@pytest.fixture
def db_in_cwd(tmp_path, monkeypatch):
    """Redirect the default DB path (``output/amz_scout.db``, relative to cwd)
    to a fresh temp DB seeded with a Slate 7 product.

    ``_resolve_context(None)`` resolves the DB via cwd, so this isolates the
    project=None query tests from the real production DB.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    db_path = tmp_path / "output" / "amz_scout.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        pid, _ = register_product(conn, "Router", "GL.iNet", "GL-Slate 7 (GL-BE3600)")
        register_asin(conn, pid, "UK", "B0F2MR53D6")
    return db_path


class TestQueryWithoutProject:
    """Test that query functions work with project=None (DB-backed)."""

    def test_query_latest_without_project(self, db_in_cwd):
        r = query_latest()
        assert r["ok"] is True

    def test_query_trends_without_project(self, db_in_cwd):
        """query_trends with project=None should work if product is in DB."""
        r = query_trends(product="Slate 7", marketplace="UK", auto_fetch=False)
        assert r["ok"] is True

    def test_query_compare_without_project(self, db_in_cwd):
        r = query_compare(product="Slate 7")
        assert r["ok"] is True

    def test_query_ranking_without_project(self, db_in_cwd):
        r = query_ranking(marketplace="UK")
        assert r["ok"] is True

    def test_query_availability_without_project(self, db_in_cwd):
        r = query_availability()
        assert r["ok"] is True

    def test_query_deals_without_project(self, db_in_cwd):
        r = query_deals(marketplace="UK", auto_fetch=False)
        assert r["ok"] is True


class TestDiscoverAsin:
    def test_unknown_marketplace_returns_error(self):
        from amz_scout.api import discover_asin

        r = discover_asin("GL.iNet", "TestModel", "XX_INVALID")
        assert r["ok"] is False
        assert "unknown" in r["error"].lower() or "not found" in r["error"].lower()

    def test_no_browser_use_returns_error(self, monkeypatch):
        """If browser-use is not installed, return clean error."""
        monkeypatch.setattr(
            "amz_scout.browser.check_browser_use_installed",
            lambda: False,
        )
        r = discover_asin("GL.iNet", "TestModel", "UK")
        assert r["ok"] is False
        assert "browser-use" in r["error"].lower()


class TestEnsureKeepaDataPostValidation:
    """Test that ensure_keepa_data warns about empty/invalid fetched data."""

    def test_fetched_empty_data_produces_warning(self, config_dir):
        """When Keepa returns data with no title/price, warnings should appear."""
        # This test uses the real DB which may have products with empty Keepa data
        # from the JP_travel_router test. We check the warning mechanism works.
        _, proj_path = config_dir
        # Use offline strategy so no actual API call; no warnings expected
        r = ensure_keepa_data(proj_path, marketplace="UK", strategy="offline")
        assert r["ok"] is True
        # Offline mode should not produce warnings (no fetches happen)
        assert "warnings" not in r["meta"] or r["meta"].get("warnings") == []


# Wide-row marker fields from the competitive_snapshots schema. These live
# OUTSIDE the LLM-safe allow-list in ``amz_scout._llm_trim`` and exist to
# prove that ``amz_scout.api`` returns full DB rows. If any of them is missing
# from a query result, somebody has wired trimming back into the api layer
# and broken the CLI / admin contract.
_API_WIDE_ROW_MARKERS = ("title", "url", "sold_by", "fulfillment")


class TestApiEnvelopeCompleteness:
    """Contract: ``amz_scout.api.query_*`` must return the full DB schema.

    This is the regression guard that complements ``TestWebappTrimBoundary``
    in ``test_webapp_smoke.py``. Trimming is a webapp-boundary concern only;
    the api layer must keep CLI / admin / future scripts seeing the complete
    row schema. If a future change moves trim back inside ``api.py``, every
    test in this class fails loudly with the leaked field name.
    """

    def test_query_latest_returns_full_schema(self, seeded_config):
        _, proj_path = seeded_config
        r = query_latest(proj_path, marketplace="UK")

        assert r["ok"] is True
        assert len(r["data"]) >= 1
        row = r["data"][0]
        for marker in _API_WIDE_ROW_MARKERS:
            assert marker in row, (
                f"api.query_latest dropped {marker!r} — trim has leaked back "
                f"into amz_scout.api. Trim must live at the webapp boundary only."
            )

    def test_query_compare_returns_full_schema(self, seeded_config):
        _, proj_path = seeded_config
        r = query_compare(proj_path, product="Slate 7")

        assert r["ok"] is True
        assert len(r["data"]) >= 1
        row = r["data"][0]
        for marker in _API_WIDE_ROW_MARKERS:
            assert marker in row, (
                f"api.query_compare dropped {marker!r} — trim leaked into api layer"
            )

    def test_query_ranking_returns_full_schema(self, seeded_config):
        # seeded_config seeds rows with bare-digit BSR strings that don't
        # match parse_bsr_routers' regex, so cs.bsr ends up NULL and
        # query_bsr_ranking filters them out. Inject one extra row with a
        # parseable "#N in Routers" string so this test has a row to inspect.
        tmp_path, proj_path = seeded_config
        db_path = tmp_path / "output" / "amz_scout.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        upsert_competitive(
            conn,
            [
                CompetitiveData(
                    date="2026-04-01",
                    site="UK",
                    category="Router",
                    brand="ContractTest",
                    model="BSR-Probe",
                    asin="B0BSRPROBE",
                    title="BSR Probe Router",
                    price="£99.00",
                    rating="4.0",
                    review_count="10",
                    bought_past_month="5+",
                    bsr="#42 in Routers",
                    available="Yes",
                    url="https://example.test/dp/B0BSRPROBE",
                ),
            ],
        )
        conn.close()

        r = query_ranking(proj_path, marketplace="UK")

        assert r["ok"] is True
        assert len(r["data"]) >= 1
        row = r["data"][0]
        for marker in _API_WIDE_ROW_MARKERS:
            assert marker in row, (
                f"api.query_ranking dropped {marker!r} — trim leaked into api layer"
            )

    # NOTE: ``query_availability`` is intentionally absent from this contract
    # test. Its DB layer (``db.query_availability``) hand-projects only 7
    # columns (brand/model/asin/site/available/price_cents/currency), all of
    # which happen to be in ``LLM_SAFE_COMPETITIVE_FIELDS`` — so even if a
    # future change re-introduced ``trim_competitive_rows`` inside
    # ``api.query_availability``, the output rows would be identical and a
    # marker-based regression test could not detect the leak. The webapp
    # boundary test ``TestWebappTrimBoundary`` is sufficient guard for this
    # tool's LLM-facing path.


# ─── Resolve ASIN status gate (plan: product_asins.status cleanup) ──


class TestResolveAsinStatusGate:
    """Cover query lifecycle matrix #10 (not_listed silent failure).

    v6 removed intent validation — the wrong_product gate no longer
    exists; only the not_listed availability gate remains.
    """

    def _setup_db(self, tmp_path):
        db_path = tmp_path / "status_gate.db"
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_schema(c)
        pid, _ = register_product(c, "Router", "TestBrand", "TestModel")
        register_asin(c, pid, "UK", "B0DEADXXX1", status="not_listed", notes="")
        register_asin(c, pid, "FR", "B0GOOD0001", status="active", notes="")
        c.close()
        return db_path

    def test_raises_on_not_listed_asin_pass_through(self, tmp_path):
        db_path = self._setup_db(tmp_path)
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="not_listed"):
            _resolve_asin([], "B0DEADXXX1", marketplace="UK", conn=c)
        c.close()

    def test_passes_for_active_asin(self, tmp_path):
        db_path = self._setup_db(tmp_path)
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        asin, _model, _brand, _source, _warnings = _resolve_asin(
            [], "B0GOOD0001", marketplace="FR", conn=c
        )
        assert asin == "B0GOOD0001"
        c.close()

    def test_query_filter_excludes_not_listed_in_load_products(self, tmp_path):
        from amz_scout.db import load_products_from_db

        db_path = self._setup_db(tmp_path)
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        products_uk = load_products_from_db(c, marketplace="UK")
        assert products_uk == []
        products_fr = load_products_from_db(c, marketplace="FR")
        assert len(products_fr) == 1
        assert products_fr[0].marketplace_overrides["FR"]["asin"] == "B0GOOD0001"
        c.close()

    def test_query_envelope_failure_for_not_listed(self, tmp_path, monkeypatch):
        """End-to-end: query_trends on a not_listed ASIN returns ok=False."""
        from pathlib import Path as _Path

        import amz_scout.api as api_mod
        from amz_scout.api import _ProjectInfo

        db_path = self._setup_db(tmp_path)

        def fake_ctx(project=None, **_kw):
            return _ProjectInfo(
                config=None,
                marketplaces={},
                products=[],
                db_path=db_path,
                output_base=_Path("output"),
                marketplace_aliases={"uk": "UK"},
            )

        monkeypatch.setattr(api_mod, "_resolve_context", fake_ctx)
        r = query_trends(product="B0DEADXXX1", marketplace="UK", auto_fetch=False)
        assert r["ok"] is False
        assert "not_listed" in r["error"]
        assert r["data"] == []


# ─── Transient-failure guard (issue #11) ─────────────────────────────


class TestEmptyObservationStrikes:
    """Strike counter behaviour for _record_empty_observation.

    Covers the internal state machine: unregistered ASIN no-op,
    incremental counting, threshold flip, and reset-on-success.
    """

    def _seed(self, tmp_path, asin="B0STRIKE01", status="active"):
        db_path = tmp_path / "strikes.db"
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_schema(c)
        pid, _ = register_product(c, "Router", "BrandX", "ModelX")
        register_asin(c, pid, "UK", asin, status=status, notes="")
        return c, pid

    def test_unregistered_asin_is_noop(self, tmp_path):
        from amz_scout.api import _record_empty_observation

        c, _ = self._seed(tmp_path)
        strikes, flipped, was_active = _record_empty_observation(
            c, "B0UNREG0001", "UK"
        )
        assert (strikes, flipped, was_active) == (0, False, False)
        c.close()

    def test_first_empty_increments_only(self, tmp_path):
        from amz_scout.api import _record_empty_observation

        c, pid = self._seed(tmp_path)
        strikes, flipped, was_active = _record_empty_observation(
            c, "B0STRIKE01", "UK"
        )
        assert strikes == 1
        assert flipped is False
        assert was_active is True
        row = c.execute(
            "SELECT status, not_listed_strikes FROM product_asins "
            "WHERE product_id = ? AND marketplace = 'UK'",
            (pid,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["not_listed_strikes"] == 1
        c.close()

    def test_threshold_flips_to_not_listed(self, tmp_path):
        from amz_scout.api import _record_empty_observation
        from amz_scout.db import NOT_LISTED_STRIKE_THRESHOLD

        c, pid = self._seed(tmp_path)
        for _ in range(NOT_LISTED_STRIKE_THRESHOLD - 1):
            _record_empty_observation(c, "B0STRIKE01", "UK")
        strikes, flipped, was_active = _record_empty_observation(
            c, "B0STRIKE01", "UK"
        )
        assert strikes == NOT_LISTED_STRIKE_THRESHOLD
        assert flipped is True
        # The threshold-crossing call itself saw the row as ``active``
        # — ``was_active_on_entry`` captures pre-flip state.
        assert was_active is True
        row = c.execute(
            "SELECT status FROM product_asins "
            "WHERE product_id = ? AND marketplace = 'UK'",
            (pid,),
        ).fetchone()
        assert row["status"] == "not_listed"
        c.close()

    def test_successful_observation_resets_strikes(self, tmp_path):
        from amz_scout.api import (
            _record_empty_observation,
            _record_successful_observation,
        )

        c, pid = self._seed(tmp_path)
        _record_empty_observation(c, "B0STRIKE01", "UK")
        _record_empty_observation(c, "B0STRIKE01", "UK")
        _record_successful_observation(c, "B0STRIKE01", "UK")
        row = c.execute(
            "SELECT not_listed_strikes FROM product_asins "
            "WHERE product_id = ? AND marketplace = 'UK'",
            (pid,),
        ).fetchone()
        assert row["not_listed_strikes"] == 0
        c.close()

    def test_already_not_listed_does_not_flip(self, tmp_path):
        """Already-not_listed rows still count strikes but don't re-flip.

        Intended side-effect: ``flipped=False`` protects us from
        rewriting ``notes`` every fetch, while ``strikes`` keeps
        accumulating as an observational log. ``was_active_on_entry``
        is False so callers can pick log-style copy instead of
        "strike N/THRESHOLD" progression text.
        """
        from amz_scout.api import _record_empty_observation

        c, _ = self._seed(tmp_path, status="not_listed")
        strikes, flipped, was_active = _record_empty_observation(
            c, "B0STRIKE01", "UK"
        )
        assert flipped is False
        assert strikes == 1
        assert was_active is False
        c.close()


class TestEnsureKeepaDataTransientVsPermanent:
    """End-to-end: ensure_keepa_data distinguishes transient from genuine empty."""

    def _register(self, tmp_path, asin, status="active"):
        db_path = tmp_path / "output" / "amz_scout.db"
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        pid, _ = register_product(c, "Router", "BrandY", "ModelY")
        register_asin(c, pid, "UK", asin, status=status, notes="")
        c.close()
        return db_path, pid

    @staticmethod
    def _make_outcome(asin, site, fetch_error=""):
        from amz_scout.freshness import ProductFreshness
        from amz_scout.keepa_service import KeepaProductOutcome
        from amz_scout.models import PriceHistory

        pf = ProductFreshness(
            asin=asin,
            site=site,
            model="ModelY",
            brand="BrandY",
            fetched_at=None,
            age_days=None,
            action="fetch",
            reason="test-fixture",
        )
        ph = PriceHistory(
            date="2026-04-21",
            site=site,
            category="Router",
            brand="BrandY",
            model="ModelY",
            asin=asin,
            fetch_error=fetch_error,
        )
        return KeepaProductOutcome(
            asin=asin,
            site=site,
            model="ModelY",
            source="fetched",
            price_history=ph,
            freshness=pf,
        )

    def test_transient_blip_preserves_status(self, config_dir, monkeypatch):
        """fetch_error != '' must not touch status or strikes."""
        from amz_scout.keepa_service import KeepaResult

        tmp_path, proj_path = config_dir
        db_path, _pid = self._register(tmp_path, "B0TRANS0001", status="active")

        def fake_get_keepa_data(conn, products, sites, marketplaces, **_kw):
            return KeepaResult(
                outcomes=[self._make_outcome("B0TRANS0001", "UK", "rate_limited")],
                tokens_used=0,
                tokens_remaining=60,
            )

        monkeypatch.setattr(
            "amz_scout.keepa_service.get_keepa_data", fake_get_keepa_data
        )
        r = ensure_keepa_data(proj_path, marketplace="UK", confirm=True)
        assert r["ok"] is True

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT status, not_listed_strikes FROM product_asins "
            "WHERE asin = 'B0TRANS0001' AND marketplace = 'UK'"
        ).fetchone()
        assert row["status"] == "active"
        assert row["not_listed_strikes"] == 0
        warnings = r["meta"].get("warnings", [])
        assert any("Transient Keepa failure" in w for w in warnings), warnings
        c.close()

    def test_genuine_empty_increments_strike_under_threshold(
        self, config_dir, monkeypatch
    ):
        """fetch_error='' + empty body → strike counter bumps, status stays."""
        from amz_scout.keepa_service import KeepaResult

        tmp_path, proj_path = config_dir
        db_path, _pid = self._register(tmp_path, "B0EMPTY0001", status="active")

        def fake_get_keepa_data(conn, products, sites, marketplaces, **_kw):
            return KeepaResult(
                outcomes=[self._make_outcome("B0EMPTY0001", "UK", "")],
                tokens_used=0,
                tokens_remaining=60,
            )

        monkeypatch.setattr(
            "amz_scout.keepa_service.get_keepa_data", fake_get_keepa_data
        )
        r = ensure_keepa_data(proj_path, marketplace="UK", confirm=True)
        assert r["ok"] is True

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT status, not_listed_strikes FROM product_asins "
            "WHERE asin = 'B0EMPTY0001' AND marketplace = 'UK'"
        ).fetchone()
        assert row["status"] == "active"
        assert row["not_listed_strikes"] == 1
        warnings = r["meta"].get("warnings", [])
        assert any("strike 1/" in w for w in warnings), warnings
        c.close()

    def test_missing_price_history_treated_as_transient(
        self, config_dir, monkeypatch
    ):
        """Copilot review (PR #20): KeepaProductOutcome with
        ``source='fetched'`` + ``price_history=None`` must NOT
        increment strikes — it's an internal fetch miss (scraper
        dropped a record), not a "Keepa says ASIN is dead" signal.
        """
        from amz_scout.freshness import ProductFreshness
        from amz_scout.keepa_service import KeepaProductOutcome, KeepaResult

        tmp_path, proj_path = config_dir
        db_path, _pid = self._register(
            tmp_path, "B0MISSNG001", status="active"
        )

        pf = ProductFreshness(
            asin="B0MISSNG001",
            site="UK",
            model="ModelY",
            brand="BrandY",
            fetched_at=None,
            age_days=None,
            action="fetch",
            reason="test-fixture",
        )
        outcome = KeepaProductOutcome(
            asin="B0MISSNG001",
            site="UK",
            model="ModelY",
            source="fetched",
            price_history=None,  # ← scraper miss
            freshness=pf,
        )

        def fake_get_keepa_data(conn, products, sites, marketplaces, **_kw):
            return KeepaResult(
                outcomes=[outcome],
                tokens_used=0,
                tokens_remaining=60,
            )

        monkeypatch.setattr(
            "amz_scout.keepa_service.get_keepa_data", fake_get_keepa_data
        )
        r = ensure_keepa_data(proj_path, marketplace="UK", confirm=True)
        assert r["ok"] is True

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT status, not_listed_strikes FROM product_asins "
            "WHERE asin = 'B0MISSNG001' AND marketplace = 'UK'"
        ).fetchone()
        # Status preserved, strikes NOT incremented.
        assert row["status"] == "active"
        assert row["not_listed_strikes"] == 0
        warnings = r["meta"].get("warnings", [])
        assert any("Internal fetch miss" in w for w in warnings), warnings
        c.close()

    def test_already_not_listed_emits_observational_log_copy(
        self, config_dir, monkeypatch
    ):
        """Re-fetching an already-not_listed ASIN must NOT emit
        'strike N/THRESHOLD; will mark not_listed after consecutive
        threshold' — the threshold has already been crossed, so that
        copy is misleading. Instead, emit a log-style message.
        """
        from amz_scout.keepa_service import KeepaResult

        tmp_path, proj_path = config_dir
        db_path, _pid = self._register(
            tmp_path, "B0DELIST001", status="not_listed"
        )

        def fake_get_keepa_data(conn, products, sites, marketplaces, **_kw):
            return KeepaResult(
                outcomes=[self._make_outcome("B0DELIST001", "UK", "")],
                tokens_used=0,
                tokens_remaining=60,
            )

        monkeypatch.setattr(
            "amz_scout.keepa_service.get_keepa_data", fake_get_keepa_data
        )
        r = ensure_keepa_data(proj_path, marketplace="UK", confirm=True)
        assert r["ok"] is True

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT status, not_listed_strikes FROM product_asins "
            "WHERE asin = 'B0DELIST001' AND marketplace = 'UK'"
        ).fetchone()
        # Status unchanged; strikes still advance as observational log.
        assert row["status"] == "not_listed"
        assert row["not_listed_strikes"] == 1
        warnings = r["meta"].get("warnings", [])
        # Observational-log copy, NOT progression copy.
        assert any(
            "Still observed as not_listed" in w for w in warnings
        ), warnings
        assert not any("strike 1/" in w for w in warnings), warnings
        assert not any(
            "Will mark not_listed after consecutive threshold" in w
            for w in warnings
        ), warnings
        c.close()


class TestAsinStatusRecovery:
    """Manual not_listed -> active recovery via update_asin_status (issue #11)."""

    def test_update_asin_status_restores_active(self, tmp_path):
        from amz_scout.api import _resolve_asin
        from amz_scout.db import update_asin_status

        db_path = tmp_path / "recovery.db"
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_schema(c)
        pid, _ = register_product(c, "Router", "B", "M")
        register_asin(c, pid, "UK", "B0RECOV001", status="not_listed", notes="")

        with pytest.raises(ValueError, match="not_listed"):
            _resolve_asin([], "B0RECOV001", marketplace="UK", conn=c)

        update_asin_status(
            c, pid, "UK", "active", notes="operator confirmed re-listed"
        )
        asin, *_ = _resolve_asin([], "B0RECOV001", marketplace="UK", conn=c)
        assert asin == "B0RECOV001"
        c.close()


@pytest.mark.unit
class TestRegisterAsinFromUrl:
    """URL-based ASIN registration — the non-browser counterpart of discover_asin.

    Reads marketplace config from the real repo ``config/marketplaces.yaml``
    (same behavior as ``discover_asin``); writes to the test_db fixture.
    """

    def test_happy_path_new_product(self, test_db):
        r = register_asin_from_url(
            "TP-Link",
            "AX1500",
            "DE",
            "https://www.amazon.de/dp/B0TESTTEST",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["asin"] == "B0TESTTEST"
        assert r["data"]["marketplace"] == "DE"
        assert r["data"]["new_product"] is True
        # Verify DB state: registry + mapping + notes source tag.
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT pa.asin, pa.marketplace, pa.status, pa.notes, p.brand, p.model "
            "FROM product_asins pa JOIN products p ON p.id = pa.product_id "
            "WHERE pa.asin = ?",
            ("B0TESTTEST",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["marketplace"] == "DE"
        assert row["status"] == "active"
        assert "web_search" in row["notes"], (
            "notes must identify the discovery source for auditability"
        )
        assert row["brand"] == "TP-Link"
        assert row["model"] == "AX1500"

    def test_happy_path_existing_product(self, test_db):
        # Seed the product with a UK mapping first.
        r0 = add_product(
            "Router",
            "TP-Link",
            "AX1500",
            asins={"UK": "B0UKTEST01"},
            db_path=test_db,
        )
        assert r0["ok"] is True
        # Now register a DE mapping via URL — existing product, append only.
        r = register_asin_from_url(
            "TP-Link",
            "AX1500",
            "DE",
            "https://www.amazon.de/dp/B0DETEST01",
            db_path=test_db,
        )
        assert r["ok"] is True
        assert r["data"]["new_product"] is False, (
            "Existing (brand, model) must resolve to the same product_id, "
            "not create a duplicate product row"
        )
        # Both UK and DE mappings should coexist under one product.
        conn = sqlite3.connect(str(test_db))
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM product_asins pa "
            "JOIN products p ON p.id = pa.product_id "
            "WHERE p.brand_key = 'tp-link' AND p.model_key = 'ax1500'"
        ).fetchone()["n"]
        conn.close()
        assert count == 2

    def test_invalid_url_no_dp_segment(self, test_db):
        r = register_asin_from_url(
            "X",
            "Y",
            "DE",
            "https://www.amazon.de/gp/product/some-other-path",
            db_path=test_db,
        )
        assert r["ok"] is False
        assert r["error"] is not None
        assert "ASIN" in r["error"]

    def test_url_host_mismatch(self, test_db):
        """DE target with a .co.uk URL must be rejected to prevent
        writing a UK ASIN into the DE marketplace row."""
        r = register_asin_from_url(
            "X",
            "Y",
            "DE",
            "https://www.amazon.co.uk/dp/B0TESTTEST",
            db_path=test_db,
        )
        assert r["ok"] is False
        assert r["error"] is not None
        assert "host" in r["error"].lower()

    def test_rejects_phishing_lookalike_host(self, test_db):
        """``fakeamazon.co.uk`` must NOT pass as amazon.co.uk.

        A naive ``host.endswith("amazon.co.uk")`` returns True here, which
        would let an attacker-controlled lookalike inject arbitrary ASIN
        rows. Strict match requires ``host == domain`` or
        ``host.endswith("." + domain)``.
        """
        cases = [
            ("fakeamazon.co.uk", "UK"),
            ("www.fakeamazon.co.uk", "UK"),
            ("notamazon.com", "US"),
            ("www.notamazon.com", "US"),
        ]
        for bad_host, market in cases:
            r = register_asin_from_url(
                "X",
                "Y",
                market,
                f"https://{bad_host}/dp/B0PHISHER0",
                db_path=test_db,
            )
            assert r["ok"] is False, f"phishing host {bad_host!r} bypassed check"
            assert r["error"] is not None
            assert "host" in r["error"].lower(), (
                f"phishing host {bad_host!r}: error should mention host, "
                f"got {r['error']!r}"
            )

    def test_accepts_bare_domain_without_subdomain(self, test_db):
        """``amazon.de`` (no ``www.`` prefix) must still register — the
        strict check must not break on the bare domain form."""
        r = register_asin_from_url(
            "TP-Link",
            "AX1500-bare",
            "DE",
            "https://amazon.de/dp/B0BARETEST",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["asin"] == "B0BARETEST"

    def test_marketplace_alias(self, test_db):
        """Marketplace aliases (lowercase code, amazon domain, uppercase)
        all canonicalize to the same site."""
        for alias in ("de", "DE", "amazon.de"):
            r = register_asin_from_url(
                "TP-Link",
                f"AX1500-alias-{alias}",  # unique model per call to avoid UNIQUE conflict
                alias,
                "https://www.amazon.de/dp/B0ALIAS001",
                db_path=test_db,
            )
            assert r["ok"] is True, f"alias {alias!r} failed: {r['error']}"
            assert r["data"]["marketplace"] == "DE", (
                f"alias {alias!r} did not canonicalize to 'DE'"
            )

    def test_url_without_scheme(self, test_db):
        """Bare 'amazon.de/dp/...' (no https://) must still parse."""
        r = register_asin_from_url(
            "TP-Link",
            "AX1500-noscheme",
            "DE",
            "www.amazon.de/dp/B0NOSCHEM0",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["asin"] == "B0NOSCHEM0"
        assert r["data"]["marketplace"] == "DE"

    def test_international_tld_jp(self, test_db):
        r = register_asin_from_url(
            "Sony",
            "WH-1000",
            "JP",
            "https://www.amazon.co.jp/dp/B0JPTESTJP",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["marketplace"] == "JP"

    def test_international_tld_br(self, test_db):
        r = register_asin_from_url(
            "Samsung",
            "Galaxy-A55",
            "BR",
            "https://www.amazon.com.br/dp/B0BRTESTBR",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["marketplace"] == "BR"

    def test_international_tld_mx(self, test_db):
        r = register_asin_from_url(
            "Apple",
            "iPhone-15",
            "MX",
            "https://www.amazon.com.mx/dp/B0MXTESTMX",
            db_path=test_db,
        )
        assert r["ok"] is True, r["error"]
        assert r["data"]["marketplace"] == "MX"

    def test_tracking_query_tolerated(self, test_db):
        """Real Amazon URLs carry ref/tracking query strings; the regex must
        only look at /dp/<ASIN> boundaries and ignore the rest."""
        r = register_asin_from_url(
            "TP-Link",
            "AX1500-tracking",
            "DE",
            "https://www.amazon.de/GL-iNet/dp/B0TRACKING/ref=sr_1_3?keywords=foo",
            db_path=test_db,
        )
        assert r["ok"] is True
        assert r["data"]["asin"] == "B0TRACKING"

    def test_unknown_marketplace_code(self, test_db):
        r = register_asin_from_url(
            "X",
            "Y",
            "ZZ",  # not a known market
            "https://www.amazon.com/dp/B0ZZZZZZZZ",
            db_path=test_db,
        )
        assert r["ok"] is False
        assert r["error"] is not None
        assert "marketplace" in r["error"].lower() or "unknown" in r["error"].lower()
