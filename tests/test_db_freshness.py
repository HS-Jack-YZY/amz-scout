"""Tests for db.query_keepa_fetched_at."""

import json
import sqlite3
from pathlib import Path

import pytest

from amz_scout.db import init_schema, query_keepa_fetched_at, store_keepa_product

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


class TestQueryKeepaFetchedAt:
    def test_returns_fetched_at(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-25")
        result = query_keepa_fetched_at(conn, [("B0F2MR53D6", "UK")])
        assert result[("B0F2MR53D6", "UK")] == "2026-03-25"

    def test_returns_none_for_missing(self, conn):
        result = query_keepa_fetched_at(conn, [("BXXXXXXXXX", "UK")])
        assert result[("BXXXXXXXXX", "UK")] is None

    def test_multiple_pairs(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-25")
        result = query_keepa_fetched_at(
            conn,
            [
                ("B0F2MR53D6", "UK"),
                ("B0F2MR53D6", "DE"),
                ("BXXXXXXXXX", "UK"),
            ],
        )
        assert result[("B0F2MR53D6", "UK")] == "2026-03-25"
        assert result[("B0F2MR53D6", "DE")] is None
        assert result[("BXXXXXXXXX", "UK")] is None

    def test_empty_input(self, conn):
        result = query_keepa_fetched_at(conn, [])
        assert result == {}

    def test_updated_fetched_at(self, conn, raw_data):
        """INSERT OR REPLACE updates fetched_at."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-20")
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-30")
        result = query_keepa_fetched_at(conn, [("B0F2MR53D6", "UK")])
        assert result[("B0F2MR53D6", "UK")] == "2026-03-30"
