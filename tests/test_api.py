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
    check_freshness,
    ensure_keepa_data,
    keepa_budget,
    query_availability,
    query_compare,
    query_deals,
    query_latest,
    query_ranking,
    query_sellers,
    query_trends,
    resolve_product,
    resolve_project,
)
from amz_scout.db import init_schema, upsert_competitive
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
        asin, model, source = _resolve_asin(self._products(), "Slate 7", "UK")
        assert asin == "B0UKSPECIF"  # UK-specific override
        assert model == "GL-Slate 7 (GL-BE3600)"
        assert source == "config"

    def test_model_substring_default_asin(self):
        asin, model, source = _resolve_asin(self._products(), "Slate 7", "DE")
        assert asin == "B0F2MR53D6"  # Default ASIN (no DE override)

    def test_model_substring_no_marketplace(self):
        asin, _, _ = _resolve_asin(self._products(), "RT-BE58")
        assert asin == "B0FGDRP3VZ"

    def test_asin_passthrough(self):
        asin, model, source = _resolve_asin(self._products(), "B0XYZABCDE")
        assert asin == "B0XYZABCDE"
        assert source == "asin"

    def test_not_found_raises(self):
        with pytest.raises(ValueError, match="Product not found"):
            _resolve_asin(self._products(), "NonExistent")

    def test_case_insensitive(self):
        asin, _, _ = _resolve_asin(self._products(), "slate 7", "UK")
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
