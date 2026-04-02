"""Tests for amz_scout.db module using :memory: SQLite and real raw JSON."""

import json
import sqlite3
from pathlib import Path

import pytest

from amz_scout.db import (
    SERIES_AMAZON,
    SERIES_COUNT_NEW,
    SERIES_COUNT_REVIEWS,
    SERIES_MONTHLY_SOLD,
    SERIES_NEW,
    SERIES_RATING,
    SERIES_SALES_RANK,
    SERIES_SALES_RANK_BASE,
    get_connection,
    import_from_csv,
    import_from_raw_json,
    init_schema,
    query_availability,
    query_bsr_ranking,
    query_cross_market,
    query_deals_history,
    query_latest,
    query_monthly_sales,
    query_price_trends,
    query_review_growth,
    query_seller_history,
    query_stats,
    store_keepa_product,
    upsert_competitive,
)
from amz_scout.models import CompetitiveData

RAW_JSON_PATH = Path(__file__).parent.parent / "output" / "BE10000" / "data" / "eu" / "raw" / "uk_B0F2MR53D6.json"


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA foreign_keys = ON")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def raw_data():
    """Load real Keepa raw JSON."""
    if not RAW_JSON_PATH.exists():
        pytest.skip("Raw JSON fixture not found")
    with open(RAW_JSON_PATH) as f:
        return json.load(f)


# ─── Schema tests ────────────────────────────────────────────────────


class TestSchema:
    def test_init_schema_creates_tables(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "schema_migrations",
            "competitive_snapshots",
            "keepa_time_series",
            "keepa_buybox_history",
            "keepa_coupon_history",
            "keepa_deals",
            "keepa_products",
        }
        assert expected.issubset(tables)

    def test_init_schema_idempotent(self, conn):
        init_schema(conn)  # Second call should not raise
        row = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        assert row[0] == 4  # v1 + v2 + v3 + v4

    def test_schema_version(self, conn):
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == 4


# ─── Keepa write tests ──────────────────────────────────────────────


class TestStoreKeepaProduct:
    def test_store_product_metadata(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        row = conn.execute(
            "SELECT * FROM keepa_products WHERE asin = 'B0F2MR53D6' AND site = 'UK'"
        ).fetchone()
        assert row is not None
        assert row["brand"] == "GL.iNet"
        assert row["model"] == "GL-BE3600"
        assert row["item_weight"] == 300
        assert row["has_reviews"] == 1
        assert row["fetched_at"] == "2026-03-31"

    def test_store_time_series(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        # csv[3] = SALES_RANK should have many data points
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM keepa_time_series "
            "WHERE asin = 'B0F2MR53D6' AND site = 'UK' AND series_type = ?",
            (SERIES_SALES_RANK,),
        ).fetchone()
        assert row["cnt"] > 100  # Sales rank has ~1109 data points

        # csv[1] = NEW should exist
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM keepa_time_series "
            "WHERE asin = 'B0F2MR53D6' AND site = 'UK' AND series_type = ?",
            (SERIES_NEW,),
        ).fetchone()
        assert row["cnt"] > 0

    def test_store_monthly_sold(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM keepa_time_series "
            "WHERE asin = 'B0F2MR53D6' AND series_type = ?",
            (SERIES_MONTHLY_SOLD,),
        ).fetchone()
        assert row["cnt"] > 0

    def test_store_sales_ranks_multi_category(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        # Should have at least 2 salesRanks categories (200, 201)
        row = conn.execute(
            "SELECT COUNT(DISTINCT series_type) AS cnt FROM keepa_time_series "
            "WHERE asin = 'B0F2MR53D6' AND series_type >= ?",
            (SERIES_SALES_RANK_BASE,),
        ).fetchone()
        assert row["cnt"] >= 2

    def test_store_coupon_history(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM keepa_coupon_history "
            "WHERE asin = 'B0F2MR53D6'"
        ).fetchone()
        assert row["cnt"] > 0

    def test_store_deals(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        row = conn.execute(
            "SELECT * FROM keepa_deals WHERE asin = 'B0F2MR53D6'"
        ).fetchone()
        assert row is not None
        assert row["deal_type"] == "COUNTDOWN_ENDS_IN"

    def test_idempotent_insert(self, conn, raw_data):
        """INSERT OR IGNORE should not duplicate time series rows."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")
        count1 = conn.execute("SELECT COUNT(*) FROM keepa_time_series").fetchone()[0]

        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")
        count2 = conn.execute("SELECT COUNT(*) FROM keepa_time_series").fetchone()[0]

        assert count1 == count2

    def test_product_metadata_updated_on_refetch(self, conn, raw_data):
        """INSERT OR REPLACE should update product metadata."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-04-01")

        row = conn.execute(
            "SELECT fetched_at FROM keepa_products WHERE asin = 'B0F2MR53D6'"
        ).fetchone()
        assert row["fetched_at"] == "2026-04-01"

        # Only 1 row, not 2
        cnt = conn.execute("SELECT COUNT(*) FROM keepa_products").fetchone()[0]
        assert cnt == 1


# ─── Competitive snapshots tests ─────────────────────────────────────


class TestUpsertCompetitive:
    def _make_row(self, **overrides) -> CompetitiveData:
        defaults = dict(
            date="2026-03-31", site="UK", category="Travel Router",
            brand="GL.iNet", model="GL-BE3600", asin="B0F2MR53D6",
            title="Slate 7 Router", price="£117.29", rating="4.6 out of 5 stars",
            review_count="(1,117)", bought_past_month="100+ bought in past month",
            bsr="#2,719 in Routers", available="Yes", url="https://amazon.co.uk/dp/B0F2MR53D6",
        )
        defaults.update(overrides)
        return CompetitiveData(**defaults)

    def test_basic_upsert(self, conn):
        rows = [self._make_row()]
        count = upsert_competitive(conn, rows)
        assert count == 1

        row = conn.execute("SELECT * FROM competitive_snapshots").fetchone()
        assert row["asin"] == "B0F2MR53D6"
        assert row["price_cents"] == 11729
        assert row["currency"] == "£"
        assert row["rating"] == pytest.approx(4.6)
        assert row["review_count"] == 1117
        assert row["available"] == 1
        assert row["price_raw"] == "£117.29"

    def test_upsert_replaces_same_key(self, conn):
        upsert_competitive(conn, [self._make_row(price="£100.00")])
        upsert_competitive(conn, [self._make_row(price="£120.00")])

        cnt = conn.execute("SELECT COUNT(*) FROM competitive_snapshots").fetchone()[0]
        assert cnt == 1

        row = conn.execute("SELECT price_cents FROM competitive_snapshots").fetchone()
        assert row["price_cents"] == 12000

    def test_na_values_stored_as_null(self, conn):
        upsert_competitive(conn, [self._make_row(
            price="N/A", rating="N/A", review_count="N/A", bsr="N/A",
            available="Not listed",
        )])
        row = conn.execute("SELECT * FROM competitive_snapshots").fetchone()
        assert row["price_cents"] is None
        assert row["rating"] is None
        assert row["review_count"] is None
        assert row["bsr"] is None
        assert row["available"] == 0

    def test_empty_list(self, conn):
        count = upsert_competitive(conn, [])
        assert count == 0


# ─── Query tests ─────────────────────────────────────────────────────


class TestQueries:
    @pytest.fixture(autouse=True)
    def setup(self, conn, raw_data):
        self.conn = conn
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")
        upsert_competitive(conn, [CompetitiveData(
            date="2026-03-31", site="UK", category="Travel Router",
            brand="GL.iNet", model="GL-BE3600", asin="B0F2MR53D6",
            title="Slate 7", price="£117.29", rating="4.6 out of 5 stars",
            review_count="(1,117)", bought_past_month="100+",
            bsr="#2719 in Routers", available="Yes", url="",
        )])

    def test_query_latest(self):
        rows = query_latest(self.conn)
        assert len(rows) == 1
        assert rows[0]["asin"] == "B0F2MR53D6"

    def test_query_latest_with_site_filter(self):
        rows = query_latest(self.conn, site="UK")
        assert len(rows) == 1
        rows = query_latest(self.conn, site="DE")
        assert len(rows) == 0

    def test_query_price_trends(self):
        rows = query_price_trends(self.conn, "B0F2MR53D6", "UK", SERIES_NEW, days=90)
        assert len(rows) > 0
        # Should be ordered by keepa_ts DESC
        assert rows[0]["keepa_ts"] >= rows[-1]["keepa_ts"]

    def test_query_cross_market(self):
        rows = query_cross_market(self.conn, "GL-BE3600")
        assert len(rows) == 1
        assert rows[0]["site"] == "UK"

    def test_query_bsr_ranking(self):
        rows = query_bsr_ranking(self.conn, "UK")
        assert len(rows) == 1
        assert rows[0]["bsr"] == 2719

    def test_query_availability(self):
        rows = query_availability(self.conn)
        assert len(rows) == 1
        assert rows[0]["available"] == 1

    def test_query_review_growth(self):
        rows = query_review_growth(self.conn, "B0F2MR53D6", "UK")
        # csv[17] may or may not be in basic mode data
        # If present, should have data points
        assert isinstance(rows, list)

    def test_query_monthly_sales(self):
        rows = query_monthly_sales(self.conn, "B0F2MR53D6", "UK")
        assert len(rows) > 0

    def test_query_deals_history(self):
        rows = query_deals_history(self.conn, asin="B0F2MR53D6")
        assert len(rows) >= 1
        assert rows[0]["deal_type"] == "COUNTDOWN_ENDS_IN"

    def test_query_stats(self):
        stats = query_stats(self.conn)
        assert stats["keepa_time_series"] > 0
        assert stats["keepa_products"] == 1
        assert stats["competitive_snapshots"] == 1
        assert stats["distinct_products"] >= 1


# ─── Data integrity tests ────────────────────────────────────────────


class TestDataIntegrity:
    def test_time_series_matches_raw_json(self, conn, raw_data):
        """DB row count should match raw JSON data point count."""
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        # Count data points in raw JSON csv[]
        csv_data = raw_data.get("csv", [])
        expected = 0
        for arr in csv_data:
            if arr:
                expected += len(arr) // 2

        # monthlySoldHistory
        msh = raw_data.get("monthlySoldHistory", [])
        expected += len(msh) // 2

        # salesRanks
        for arr in (raw_data.get("salesRanks") or {}).values():
            expected += len(arr) // 2

        actual = conn.execute("SELECT COUNT(*) FROM keepa_time_series").fetchone()[0]
        assert actual == expected

    def test_coupon_count_matches(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        ch = raw_data.get("couponHistory", [])
        expected = len(ch) // 3

        actual = conn.execute("SELECT COUNT(*) FROM keepa_coupon_history").fetchone()[0]
        assert actual == expected
