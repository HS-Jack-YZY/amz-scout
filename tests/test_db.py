"""Tests for amz_scout.db module using :memory: SQLite and real raw JSON."""

import sqlite3

import pytest

from amz_scout.db import (
    SERIES_MONTHLY_SOLD,
    SERIES_NEW,
    SERIES_SALES_RANK,
    SERIES_SALES_RANK_BASE,
    init_schema,
    query_availability,
    query_bsr_ranking,
    query_cross_market,
    query_deals_history,
    query_latest,
    query_monthly_sales,
    query_price_trends,
    query_review_growth,
    query_stats,
    store_keepa_product,
    upsert_competitive,
)
from amz_scout.models import CompetitiveData


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


# raw_data fixture is provided by conftest.py (synthetic + real fallback)


# ─── Schema tests ────────────────────────────────────────────────────


class TestSchema:
    def test_init_schema_creates_tables(self, conn):
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
        assert row[0] == 5  # v1 + v2 + v3 + v4 + v5

    def test_schema_version(self, conn):
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == 5


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
        assert row["item_weight"] == raw_data.get("itemWeight")
        assert row["has_reviews"] == int(raw_data.get("hasReviews", False))
        assert row["fetched_at"] == "2026-03-31"

    def test_store_time_series(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        # csv[3] = SALES_RANK should have data points
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM keepa_time_series "
            "WHERE asin = 'B0F2MR53D6' AND site = 'UK' AND series_type = ?",
            (SERIES_SALES_RANK,),
        ).fetchone()
        assert row["cnt"] > 0

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
            "SELECT COUNT(*) AS cnt FROM keepa_coupon_history WHERE asin = 'B0F2MR53D6'"
        ).fetchone()
        assert row["cnt"] > 0

    def test_store_deals(self, conn, raw_data):
        store_keepa_product(conn, "B0F2MR53D6", "UK", raw_data, "2026-03-31")

        row = conn.execute("SELECT * FROM keepa_deals WHERE asin = 'B0F2MR53D6'").fetchone()
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
            date="2026-03-31",
            site="UK",
            category="Travel Router",
            brand="GL.iNet",
            model="GL-BE3600",
            asin="B0F2MR53D6",
            title="Slate 7 Router",
            price="£117.29",
            rating="4.6 out of 5 stars",
            review_count="(1,117)",
            bought_past_month="100+ bought in past month",
            bsr="#2,719 in Routers",
            available="Yes",
            url="https://amazon.co.uk/dp/B0F2MR53D6",
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
        upsert_competitive(
            conn,
            [
                self._make_row(
                    price="N/A",
                    rating="N/A",
                    review_count="N/A",
                    bsr="N/A",
                    available="Not listed",
                )
            ],
        )
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
        upsert_competitive(
            conn,
            [
                CompetitiveData(
                    date="2026-03-31",
                    site="UK",
                    category="Travel Router",
                    brand="GL.iNet",
                    model="GL-BE3600",
                    asin="B0F2MR53D6",
                    title="Slate 7",
                    price="£117.29",
                    rating="4.6 out of 5 stars",
                    review_count="(1,117)",
                    bought_past_month="100+",
                    bsr="#2719 in Routers",
                    available="Yes",
                    url="",
                )
            ],
        )

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


# ─── Schema v5 migration tests ───────────────────────────────────────


class TestStatusMigrationV5:
    """Verify schema v5 migration: drop zombie 'unavailable' status."""

    def test_v5_check_constraint_rejects_unavailable(self, conn):
        from amz_scout.db import register_product

        pid, _ = register_product(conn, "Router", "Test", "M1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO product_asins (product_id, marketplace, asin, status) "
                "VALUES (?, 'UK', 'B0AAAAAAAA', 'unavailable')",
                (pid,),
            )

    def test_v5_check_constraint_accepts_four_values(self, conn):
        from amz_scout.db import register_product

        pid, _ = register_product(conn, "Router", "Test", "M2")
        statuses = ["unverified", "verified", "wrong_product", "not_listed"]
        for i, status in enumerate(statuses):
            conn.execute(
                "INSERT INTO product_asins (product_id, marketplace, asin, status) "
                "VALUES (?, ?, ?, ?)",
                (pid, f"MK{i}", f"B0BBBB{i:04d}", status),
            )

    def test_v5_idempotent(self, conn):
        init_schema(conn)  # second call
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations WHERE version = 5"
        ).fetchone()
        assert row["c"] == 1

    def test_v5_preserves_existing_rows(self, tmp_path):
        """Exercise the v4 → v5 migration: existing rows and the asin index
        must survive the rename → create → insert-select → drop → index rebuild.

        Setup forcibly downgrades a fresh DB to v4 shape (old 5-value CHECK
        including 'unavailable', no v5 migration record) so that the second
        ``init_schema`` call triggers the real v5 migration code path.
        Monkey-patching ``SCHEMA_VERSION`` alone is insufficient because
        ``_SCHEMA_SQL`` unconditionally seeds the v5 migration record.
        """
        import amz_scout.db as db_mod
        from amz_scout.db import register_asin, register_product

        db_path = tmp_path / "v4tov5.db"

        # Step 1: fresh init, then forcibly downgrade to v4 shape.
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        c0.execute("DELETE FROM schema_migrations WHERE version = 5")
        c0.execute("ALTER TABLE product_asins RENAME TO _pa_tmp")
        c0.execute("""
            CREATE TABLE product_asins (
                product_id  INTEGER NOT NULL
                    REFERENCES products(id) ON DELETE CASCADE,
                marketplace TEXT NOT NULL,
                asin        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'unverified'
                    CHECK(status IN (
                        'unverified','verified','wrong_product',
                        'not_listed','unavailable'
                    )),
                notes       TEXT NOT NULL DEFAULT '',
                last_checked TEXT,
                created_at  TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at  TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                PRIMARY KEY (product_id, marketplace)
            )
        """)
        c0.execute("INSERT INTO product_asins SELECT * FROM _pa_tmp")
        c0.execute("DROP TABLE _pa_tmp")
        # Drop the index too so we can assert v5 recreates it.
        c0.execute("DROP INDEX IF EXISTS idx_pa_asin")
        c0.commit()

        # Step 2: write test data under the v4-shape table.
        pid, _ = register_product(c0, "R", "B", "M")
        register_asin(
            c0, pid, "UK", "B0SURVIVE1", status="verified", notes="kept"
        )
        c0.close()

        # Clear the schema-init cache so init_schema re-runs _migrate.
        db_mod._schema_initialized.discard(str(db_path))

        # Step 3: reopen — v5 migration should now run for real.
        c2 = sqlite3.connect(str(db_path))
        c2.row_factory = sqlite3.Row
        init_schema(c2)

        # Data survived the table rebuild.
        row = c2.execute(
            "SELECT asin, status, notes FROM product_asins WHERE marketplace = 'UK'"
        ).fetchone()
        assert row["asin"] == "B0SURVIVE1"
        assert row["status"] == "verified"
        assert row["notes"] == "kept"

        # Index recreated by the migration.
        idx = c2.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_pa_asin'"
        ).fetchone()
        assert idx is not None, "v5 migration must recreate idx_pa_asin"

        # Migration record inserted.
        version = c2.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()
        assert version["v"] == 5

        # Tightened CHECK is in force: 'unavailable' now rejected.
        with pytest.raises(sqlite3.IntegrityError):
            c2.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin, status) "
                "VALUES (?, 'XX', 'B0ZZZZZZZZ', 'unavailable')",
                (pid,),
            )
        c2.close()
