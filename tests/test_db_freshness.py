"""Tests for db.query_keepa_fetched_at."""

import json
import sqlite3
from pathlib import Path

import pytest

from amz_scout.db import init_schema, query_keepa_fetched_at, store_keepa_product

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA foreign_keys = ON")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


# raw_data fixture is provided by conftest.py (synthetic + real fallback)


class TestQueryKeepaFetchedAt:
    def test_returns_fetched_at(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-25")
        result = query_keepa_fetched_at(conn, [("B0F2MR53D6", "UK")])
        assert result[("B0F2MR53D6", "UK")] == ("2026-03-25", "basic")

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
        assert result[("B0F2MR53D6", "UK")] == ("2026-03-25", "basic")
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
        assert result[("B0F2MR53D6", "UK")] == ("2026-03-30", "basic")

    def test_fetch_mode_stored(self, conn, raw_data):
        """fetch_mode is stored and returned correctly."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-25",
                            fetch_mode="detailed")
        result = query_keepa_fetched_at(conn, [("B0F2MR53D6", "UK")])
        assert result[("B0F2MR53D6", "UK")] == ("2026-03-25", "detailed")
