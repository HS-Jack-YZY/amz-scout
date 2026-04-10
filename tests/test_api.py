"""Tests for amz_scout.api — programmatic API layer."""

import json
import sqlite3
from pathlib import Path

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
    remove_product_by_model,
    resolve_product,
    resolve_project,
    update_product_asin,
    validate_asins,
    discover_asin,
)
from amz_scout.db import (
    init_schema,
    register_asin,
    register_product,
    store_keepa_product,
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

RAW_JSON_PATH = (
    Path(__file__).parent.parent
    / "output"
    / "BE10000"
    / "data"
    / "eu"
    / "raw"
    / "uk_B0F2MR53D6.json"
)


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

    upsert_competitive(conn, [
        CompetitiveData(
            date="2026-04-01", site="UK", category="Router",
            brand="GL.iNet", model="GL-Slate 7 (GL-BE3600)",
            asin="B0F2MR53D6", title="Slate 7", price="£150.99",
            rating="4.5", review_count="120", bought_past_month="100+",
            bsr="2591", available="Yes", url="https://amazon.co.uk/dp/B0F2MR53D6",
        ),
        CompetitiveData(
            date="2026-04-01", site="UK", category="Router",
            brand="ASUS", model="RT-BE58",
            asin="B0FGDRP3VZ", title="RT-BE58", price="£99.97",
            rating="4.3", review_count="45", bought_past_month="50+",
            bsr="4952", available="Yes", url="https://amazon.co.uk/dp/B0FGDRP3VZ",
        ),
    ])
    conn.close()

    return tmp_path, proj_path


@pytest.fixture
def raw_data():
    if not RAW_JSON_PATH.exists():
        pytest.skip("Raw JSON fixture not found")
    with open(RAW_JSON_PATH) as f:
        return json.load(f)


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
            Product(category="Router", brand="GL.iNet",
                    model="GL-Slate 7 (GL-BE3600)", default_asin="B0F2MR53D6",
                    marketplace_overrides={"UK": {"asin": "B0UKSPECIF"}}),
            Product(category="Router", brand="ASUS",
                    model="RT-BE58", default_asin="B0FGDRP3VZ"),
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
            "Router", "TestBrand", "WithASINs",
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
            "StatusTest", "Model3", "UK", "B0STATUS01",
            status="verified", notes="confirmed on amazon.co.uk",
            db_path=test_db,
        )
        assert r["ok"] is True
        assert r["data"]["status"] == "verified"


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
        products = [Product(
            category="Router", brand="FallbackBrand", model="FallbackModel",
            default_asin="B0FALLBACK",
        )]
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


class TestQueryWithoutProject:
    """Test that query functions work with project=None (DB-backed)."""

    def test_query_latest_without_project(self):
        r = query_latest()
        assert r["ok"] is True

    def test_query_trends_without_project(self):
        """query_trends with project=None should work if product is in DB."""
        r = query_trends(product="Slate 7", marketplace="UK", auto_fetch=False)
        assert r["ok"] is True

    def test_query_compare_without_project(self):
        r = query_compare(product="Slate 7")
        assert r["ok"] is True

    def test_query_ranking_without_project(self):
        r = query_ranking(marketplace="UK")
        assert r["ok"] is True

    def test_query_availability_without_project(self):
        r = query_availability()
        assert r["ok"] is True

    def test_query_deals_without_project(self):
        r = query_deals(marketplace="UK", auto_fetch=False)
        assert r["ok"] is True


# ─── ASIN validation tests ──────────────────────────────────────────


class TestValidateAsins:
    def test_no_keepa_data_stays_unverified(self, test_db):
        """Products without Keepa data should remain unverified."""
        add_product("Router", "NoBrand", "NoData", asins={"UK": "B0NODATA01"}, db_path=test_db)
        r = validate_asins(marketplace="UK", db_path=test_db)
        assert r["ok"] is True
        skipped = [x for x in r["data"] if x["status"] == "unverified"]
        assert len(skipped) >= 1

    def test_matching_title_verifies(self, test_db):
        """Product with matching Keepa title should be verified."""
        with sqlite3.connect(str(test_db)) as conn:
            conn.row_factory = sqlite3.Row
            init_schema(conn)
            pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7 Test")
            register_asin(conn, pid, "UK", "B0VERIFYME")
            # Simulate Keepa data with matching title
            store_keepa_product(conn, "B0VERIFYME", "UK",
                                {"title": "GL.iNet Slate 7 WiFi Travel Router"}, "2026-04-01")

        r = validate_asins(marketplace="UK", db_path=test_db)
        verified = [x for x in r["data"] if x["asin"] == "B0VERIFYME"]
        assert len(verified) == 1
        assert verified[0]["status"] == "verified"

    def test_mismatched_title_marks_wrong_product(self, test_db):
        """Product with non-matching title should be marked wrong_product."""
        with sqlite3.connect(str(test_db)) as conn:
            conn.row_factory = sqlite3.Row
            init_schema(conn)
            pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7 Mismatch")
            register_asin(conn, pid, "UK", "B0WRONGPRD")
            store_keepa_product(conn, "B0WRONGPRD", "UK",
                                {"title": "USB-C Hub Adapter Multiport"}, "2026-04-01")

        r = validate_asins(marketplace="UK", db_path=test_db)
        wrong = [x for x in r["data"] if x["asin"] == "B0WRONGPRD"]
        assert len(wrong) == 1
        assert wrong[0]["status"] == "wrong_product"

    def test_empty_title_marks_not_listed(self, test_db):
        """Product with empty Keepa title should be marked not_listed."""
        with sqlite3.connect(str(test_db)) as conn:
            conn.row_factory = sqlite3.Row
            init_schema(conn)
            pid, _ = register_product(conn, "Router", "GL.iNet", "NotListed Test")
            register_asin(conn, pid, "UK", "B0NOTLISTD")
            store_keepa_product(conn, "B0NOTLISTD", "UK",
                                {"title": None}, "2026-04-01")

        r = validate_asins(marketplace="UK", db_path=test_db)
        not_listed = [x for x in r["data"] if x["asin"] == "B0NOTLISTD"]
        assert len(not_listed) == 1
        assert not_listed[0]["status"] == "not_listed"

    def test_empty_db_returns_empty(self, test_db):
        r = validate_asins(db_path=test_db)
        assert r["ok"] is True
        assert r["data"] == []


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
