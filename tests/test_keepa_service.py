"""Tests for amz_scout.keepa_service — cache-first orchestration."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from amz_scout.config import MarketplaceConfig
from amz_scout.db import init_schema, store_keepa_product
from amz_scout.freshness import FreshnessStrategy
from amz_scout.keepa_service import _read_from_cache, get_keepa_data
from amz_scout.models import PriceHistory, Product

RAW_JSON_PATH = (
    Path(__file__).parent.parent
    / "output"
    / "BE10000"
    / "data"
    / "eu"
    / "raw"
    / "uk_B0F2MR53D6.json"
)


def _product() -> Product:
    return Product(
        category="Router",
        brand="GL.iNet",
        model="GL-MT6000",
        default_asin="B0F2MR53D6",
    )


def _marketplace() -> dict[str, MarketplaceConfig]:
    return {
        "UK": MarketplaceConfig(
            amazon_domain="amazon.co.uk",
            keepa_domain="GB",
            keepa_domain_code=2,
            currency_code="GBP",
            currency_symbol="£",
            region="eu",
            delivery_postcode="SW1A 1AA",
        ),
    }


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA foreign_keys = ON")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def raw_data():
    if not RAW_JSON_PATH.exists():
        pytest.skip("Raw JSON fixture not found")
    with open(RAW_JSON_PATH) as f:
        return json.load(f)


class TestReadFromCache:
    def test_reads_from_raw_json(self, raw_data):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir)
            json_path = raw_dir / "uk_B0F2MR53D6.json"
            with open(json_path, "w") as f:
                json.dump(raw_data, f)

            product = _product()
            result = _read_from_cache(product, "UK", raw_dir)
            assert result is not None
            assert isinstance(result, PriceHistory)
            assert result.site == "UK"
            assert result.asin == "B0F2MR53D6"

    def test_returns_none_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _read_from_cache(_product(), "UK", Path(tmpdir))
            assert result is None

    def test_returns_none_when_no_dir(self):
        result = _read_from_cache(_product(), "UK", None)
        assert result is None


class TestGetKeepaDataOffline:
    """Test offline strategy — no API calls, purely DB + cache."""

    def test_offline_reads_cache_no_api(self, conn, raw_data):
        """OFFLINE with data in DB + raw JSON on disk returns cached data."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-25")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_base = Path(tmpdir)
            raw_dir = output_base / "data" / "eu" / "raw"
            raw_dir.mkdir(parents=True)
            with open(raw_dir / "uk_B0F2MR53D6.json", "w") as f:
                json.dump(raw_data, f)

            result = get_keepa_data(
                conn,
                [_product()],
                ["UK"],
                _marketplace(),
                strategy=FreshnessStrategy.OFFLINE,
                output_base=output_base,
            )

        assert result.cache_count == 1
        assert result.fetch_count == 0
        assert result.skip_count == 0
        assert result.tokens_used == 0
        assert result.outcomes[0].source == "cache"
        assert result.outcomes[0].price_history is not None

    def test_offline_skips_missing(self, conn):
        """OFFLINE with no data skips without API call."""
        result = get_keepa_data(
            conn,
            [_product()],
            ["UK"],
            _marketplace(),
            strategy=FreshnessStrategy.OFFLINE,
        )
        assert result.skip_count == 1
        assert result.fetch_count == 0


class TestGetKeepaDataLazy:
    def test_lazy_serves_cache(self, conn, raw_data):
        """LAZY with old data still serves from cache."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2025-01-01")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_base = Path(tmpdir)
            raw_dir = output_base / "data" / "eu" / "raw"
            raw_dir.mkdir(parents=True)
            with open(raw_dir / "uk_B0F2MR53D6.json", "w") as f:
                json.dump(raw_data, f)

            result = get_keepa_data(
                conn,
                [_product()],
                ["UK"],
                _marketplace(),
                strategy=FreshnessStrategy.LAZY,
                output_base=output_base,
            )

        assert result.cache_count == 1
        assert result.fetch_count == 0
