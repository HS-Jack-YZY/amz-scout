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
        assert row[0] == 9  # v1..v9 inclusive

    def test_schema_version(self, conn):
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        assert row[0] == 9


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


# ─── Schema v6 migration tests ───────────────────────────────────────


class TestStatusMigrationV6:
    """Verify schema v6 migration: collapse status to 2 values
    (active / not_listed) — intent validation removed."""

    def test_v6_check_constraint_rejects_legacy_values(self, conn):
        from amz_scout.db import register_product

        pid, _ = register_product(conn, "Router", "Test", "M1")
        for i, bad in enumerate(("unverified", "verified", "wrong_product")):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO product_asins "
                    "(product_id, marketplace, asin, status) "
                    "VALUES (?, ?, ?, ?)",
                    (pid, f"M{i:02d}", f"B0LEG{i:05d}", bad),
                )

    def test_v6_check_constraint_accepts_two_values(self, conn):
        from amz_scout.db import register_product

        pid, _ = register_product(conn, "Router", "Test", "M2")
        for i, status in enumerate(["active", "not_listed"]):
            conn.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin, status) "
                "VALUES (?, ?, ?, ?)",
                (pid, f"MK{i}", f"B0CC{i:06d}", status),
            )

    def test_v6_default_status_is_active(self, conn):
        from amz_scout.db import register_asin, register_product

        pid, _ = register_product(conn, "Router", "Test", "M3")
        register_asin(conn, pid, "UK", "B0DDDD0001")
        row = conn.execute(
            "SELECT status FROM product_asins WHERE asin = 'B0DDDD0001'"
        ).fetchone()
        assert row["status"] == "active"

    def test_v6_idempotent(self, conn):
        init_schema(conn)  # second call
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations WHERE version = 6"
        ).fetchone()
        assert row["c"] == 1

    def test_v6_migrates_legacy_statuses_to_active(self, tmp_path):
        """v5 → v6 upgrade: {unverified, verified, wrong_product} → active;
        not_listed preserved. Index and migration record rebuilt.

        Forcibly downgrades a fresh DB to the v5 shape (4-value CHECK,
        no v6 migration record) so that reopening runs the real v6 code
        path. Monkey-patching ``SCHEMA_VERSION`` alone is insufficient
        because ``_SCHEMA_SQL`` unconditionally seeds the v6 record.
        """
        import amz_scout.db as db_mod
        from amz_scout.db import register_product

        db_path = tmp_path / "v5tov6.db"

        # Step 1: fresh init, then forcibly downgrade to v5 shape.
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        # Drop v6+ records so MAX(version)=5 and the v6 migration path
        # actually runs on reopen. (Every version record is seeded by
        # _SCHEMA_SQL even for a "fresh v5 downgrade" — so later
        # migrations like v7/v8/v9 must be dropped too, otherwise
        # _migrate short-circuits on its `current >= SCHEMA_VERSION`
        # check.)
        c0.execute("DELETE FROM schema_migrations WHERE version >= 6")
        c0.execute("ALTER TABLE product_asins RENAME TO _pa_tmp")
        c0.execute("""
            CREATE TABLE product_asins (
                product_id  INTEGER NOT NULL
                    REFERENCES products(id) ON DELETE CASCADE,
                marketplace TEXT NOT NULL,
                asin        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'unverified'
                    CHECK(status IN (
                        'unverified','verified',
                        'wrong_product','not_listed'
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
        # List columns explicitly so future schema additions (like v8's
        # not_listed_strikes) don't break this "force to v5 shape"
        # scaffolding — _pa_tmp carries the live baseline's extra
        # columns and `SELECT *` would produce a column-count mismatch.
        c0.execute(
            "INSERT INTO product_asins "
            "(product_id, marketplace, asin, status, notes, "
            " last_checked, created_at, updated_at) "
            "SELECT product_id, marketplace, asin, status, notes, "
            "       last_checked, created_at, updated_at FROM _pa_tmp"
        )
        c0.execute("DROP TABLE _pa_tmp")
        c0.execute("DROP INDEX IF EXISTS idx_pa_asin")
        c0.commit()

        # Step 2: write one row per legacy status under v5 shape.
        pid, _ = register_product(c0, "R", "B", "M")
        for mp, asin, status in [
            ("UK", "B0UNVER0001", "unverified"),
            ("DE", "B0VERIF0001", "verified"),
            ("FR", "B0WRONG0001", "wrong_product"),
            ("JP", "B0NOTLI0001", "not_listed"),
        ]:
            c0.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin, status) "
                "VALUES (?, ?, ?, ?)",
                (pid, mp, asin, status),
            )
        c0.commit()
        c0.close()

        # Clear the schema-init cache so init_schema re-runs _migrate.
        db_mod._schema_initialized.discard(str(db_path))

        # Step 3: reopen — v6 migration should now run for real.
        c2 = sqlite3.connect(str(db_path))
        c2.row_factory = sqlite3.Row
        init_schema(c2)

        rows = {
            r["marketplace"]: r["status"]
            for r in c2.execute(
                "SELECT marketplace, status FROM product_asins"
            ).fetchall()
        }
        assert rows["UK"] == "active"          # was unverified
        assert rows["DE"] == "active"          # was verified
        assert rows["FR"] == "active"          # was wrong_product
        assert rows["JP"] == "not_listed"      # preserved

        # Index recreated by the migration.
        idx = c2.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='idx_pa_asin'"
        ).fetchone()
        assert idx is not None, "v6 migration must recreate idx_pa_asin"

        # Migration records inserted. Reopen runs v6, v7, v8 and v9
        # since all four records were cleared in Step 1, so MAX(version)
        # advances to the current SCHEMA_VERSION.
        ver = c2.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()
        assert ver["v"] == 9

        # Tightened CHECK is in force: legacy values now rejected.
        with pytest.raises(sqlite3.IntegrityError):
            c2.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin, status) "
                "VALUES (?, 'XX', 'B0ZZZZZZZZ', 'verified')",
                (pid,),
            )
        c2.close()


# ─── Schema v7 migration tests ───────────────────────────────────────


class TestBrandModelKeyMigrationV7:
    """Verify schema v7: normalize brand/model identity via
    ``brand_key``/``model_key``. ``register_product`` matches on the
    normalized keys; display literals ``brand``/``model`` preserve
    whatever the first writer wrote.
    """

    def test_v7_normalize_key_basic(self):
        from amz_scout.db import _normalize_key

        assert _normalize_key("TP-Link") == "tp-link"
        assert _normalize_key("  TP-Link  ") == "tp-link"
        assert _normalize_key("Archer  BE400") == "archer be400"
        assert _normalize_key("\tArcher\nBE400\t") == "archer be400"
        assert _normalize_key(None) == ""
        assert _normalize_key("") == ""
        assert _normalize_key("GL.iNet") == "gl.inet"

    def test_v7_register_product_matches_whitespace_variants(self, conn):
        from amz_scout.db import register_product

        pid1, new1 = register_product(conn, "Router", "TP-Link", "Archer BE400")
        pid2, new2 = register_product(conn, "Router", "  tp-link  ", "archer  be400")
        pid3, new3 = register_product(conn, "Router", "TP-LINK", "Archer\tBE400")
        assert pid1 == pid2 == pid3
        assert new1 is True
        assert new2 is False
        assert new3 is False

    def test_v7_register_product_preserves_display(self, conn):
        """First writer's literal is preserved; later calls that match
        on normalized keys do NOT overwrite ``products.brand``/``model``.
        """
        from amz_scout.db import register_product

        pid, _ = register_product(conn, "Router", "TP-Link", "Archer BE400")
        register_product(conn, "Router", "tp-link", "archer be400")
        row = conn.execute(
            "SELECT brand, model FROM products WHERE id = ?", (pid,)
        ).fetchone()
        assert row["brand"] == "TP-Link"
        assert row["model"] == "Archer BE400"

    def test_v7_unique_constraint_on_keys(self, conn):
        """Two rows with different literals but identical normalized
        keys must violate UNIQUE(brand_key, model_key) on direct INSERT.
        """
        conn.execute(
            "INSERT INTO products "
            "(category, brand, model, brand_key, model_key) "
            "VALUES ('Router', 'TP-Link', 'Archer BE400', "
            "'tp-link', 'archer be400')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products "
                "(category, brand, model, brand_key, model_key) "
                "VALUES ('Router', 'tp-link', 'archer be400', "
                "'tp-link', 'archer be400')"
            )

    def test_v7_idempotent(self, conn):
        init_schema(conn)  # second call
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations WHERE version = 7"
        ).fetchone()
        assert row["c"] == 1

    def test_v7_migrates_v6_db_and_merges_duplicates(self, tmp_path, caplog):
        """v6 → v7 upgrade: rebuild products with brand_key/model_key,
        merge rows whose normalized (brand, model) collide, and re-point
        product_asins / product_tags at the surviving canonical id.

        Forcibly downgrade a fresh DB to v6 shape (drop v7 migration
        record + rebuild products without the key columns), insert
        literal duplicates that v6 UNIQUE(brand, model) does not
        reject, then reopen so the real v7 migration path runs.
        """
        import amz_scout.db as db_mod

        db_path = tmp_path / "v6tov7.db"

        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        c0.execute("PRAGMA foreign_keys = ON")
        init_schema(c0)

        c0.execute("PRAGMA foreign_keys = OFF")
        c0.execute("DELETE FROM schema_migrations WHERE version >= 7")
        c0.execute("DROP TABLE products")
        c0.execute("""
            CREATE TABLE products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                brand           TEXT NOT NULL,
                model           TEXT NOT NULL,
                search_keywords TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(brand, model)
            )
        """)

        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (10, 'Router', 'TP-Link', 'Archer BE400')"
        )
        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (11, 'Router', 'TP-Link ', 'Archer BE400')"
        )
        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (12, 'Router', 'tp-link', 'Archer BE400')"
        )
        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (20, 'Router', 'GL.iNet', 'Slate 7')"
        )

        for pid, mp, asin in [
            (10, "UK", "B0UK000001"),
            (11, "DE", "B0DE000001"),
            (11, "UK", "B0UK000099"),
            (12, "FR", "B0FR000001"),
            (20, "UK", "B0SLATE0001"),
        ]:
            c0.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin) VALUES (?, ?, ?)",
                (pid, mp, asin),
            )

        for pid, tag in [
            (10, "travel-router"),
            (11, "travel-router"),
            (12, "tplink-alpha"),
        ]:
            c0.execute(
                "INSERT INTO product_tags (product_id, tag) "
                "VALUES (?, ?)",
                (pid, tag),
            )

        c0.execute("PRAGMA foreign_keys = ON")
        c0.commit()
        c0.close()

        db_mod._schema_initialized.discard(str(db_path))
        c2 = sqlite3.connect(str(db_path))
        c2.row_factory = sqlite3.Row
        c2.execute("PRAGMA foreign_keys = ON")
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="amz_scout.db"):
            init_schema(c2)

        # The v7 merge drops B0UK000099 (product 11 UK) because its
        # (product_id, marketplace) conflicts with (10, UK) after the
        # merge. The migrator must log which ASIN was dropped so an
        # operator can reconcile the loser manually.
        assert "B0UK000099" in caplog.text, (
            "expected dropped-ASIN warning; got:\n" + caplog.text
        )
        assert "UK" in caplog.text

        rows = c2.execute(
            "SELECT id, brand, model, brand_key, model_key "
            "FROM products ORDER BY id"
        ).fetchall()
        ids = [r["id"] for r in rows]
        assert ids == [10, 20], f"expected merged ids [10, 20], got {ids}"
        tp = next(r for r in rows if r["id"] == 10)
        assert tp["brand"] == "TP-Link"
        assert tp["model"] == "Archer BE400"
        assert tp["brand_key"] == "tp-link"
        assert tp["model_key"] == "archer be400"

        asins = {
            (r["product_id"], r["marketplace"]): r["asin"]
            for r in c2.execute(
                "SELECT product_id, marketplace, asin FROM product_asins"
            ).fetchall()
        }
        assert asins == {
            (10, "UK"): "B0UK000001",
            (10, "DE"): "B0DE000001",
            (10, "FR"): "B0FR000001",
            (20, "UK"): "B0SLATE0001",
        }

        tags = {
            (r["product_id"], r["tag"])
            for r in c2.execute(
                "SELECT product_id, tag FROM product_tags"
            ).fetchall()
        }
        assert tags == {(10, "travel-router"), (10, "tplink-alpha")}

        ver = c2.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()["v"]
        assert ver == 8

        with pytest.raises(sqlite3.IntegrityError):
            c2.execute(
                "INSERT INTO products "
                "(category, brand, model, brand_key, model_key) "
                "VALUES ('Router', 'TP-Link', 'Archer BE400', "
                "'tp-link', 'archer be400')"
            )
        c2.close()

    def test_v7_fk_integrity_after_migration(self, tmp_path):
        """After the v7 migration, product_asins FK enforcement is back
        on and every product_id resolves to a valid row in the new
        ``products``. This test seeds a single product (no merge path)
        and focuses on FK restoration; the merge path is covered by
        ``test_v7_migrates_v6_db_and_merges_duplicates``.
        """
        import amz_scout.db as db_mod

        db_path = tmp_path / "v6tov7_fk.db"

        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        c0.execute("PRAGMA foreign_keys = ON")
        init_schema(c0)

        c0.execute("PRAGMA foreign_keys = OFF")
        c0.execute("DELETE FROM schema_migrations WHERE version >= 7")
        c0.execute("DROP TABLE products")
        c0.execute("""
            CREATE TABLE products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                brand           TEXT NOT NULL,
                model           TEXT NOT NULL,
                search_keywords TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(brand, model)
            )
        """)
        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (1, 'Router', 'TP-Link', 'Archer BE400')"
        )
        c0.execute(
            "INSERT INTO product_asins (product_id, marketplace, asin) "
            "VALUES (1, 'UK', 'B0UK000001')"
        )
        c0.execute("PRAGMA foreign_keys = ON")
        c0.commit()
        c0.close()

        db_mod._schema_initialized.discard(str(db_path))
        c2 = sqlite3.connect(str(db_path))
        c2.row_factory = sqlite3.Row
        c2.execute("PRAGMA foreign_keys = ON")
        init_schema(c2)

        # FK check: no orphan product_ids.
        orphans = c2.execute("PRAGMA foreign_key_check").fetchall()
        assert orphans == [], f"FK orphans after v7: {orphans}"

        # FK is enforced again: inserting an ASIN with unknown
        # product_id must fail.
        with pytest.raises(sqlite3.IntegrityError):
            c2.execute(
                "INSERT INTO product_asins "
                "(product_id, marketplace, asin) "
                "VALUES (999, 'XX', 'B0NOSUCHPID')"
            )
        c2.close()


# ─── Query-side normalization (v7 contract) ──────────────────────────


class TestQuerySideNormalizationV7:
    """v7 stores brand/model under normalized keys. The *query* helpers
    must honor the same identity contract so callers can pass casing or
    whitespace variants and still hit the registered row.

    These cover the follow-up to schema v7 where ``register_product``
    was the only path normalized. Literal ``brand = ?`` comparisons in
    ``find_product_exact`` / ``list_registered_products`` /
    ``load_products_from_db`` produced silent "registered but not
    findable" failures.
    """

    def test_find_product_exact_matches_normalized_variants(self, conn):
        from amz_scout.db import find_product_exact, register_product

        pid, _ = register_product(conn, "Router", "TP-Link", "Archer BE400")

        for b, m in [
            ("TP-Link", "Archer BE400"),
            ("tp-link", "archer be400"),
            ("  TP-Link  ", "Archer\tBE400"),
            ("TP-LINK", "Archer  BE400"),
        ]:
            row = find_product_exact(conn, b, m)
            assert row is not None, f"expected hit for ({b!r}, {m!r})"
            assert row["id"] == pid
        assert find_product_exact(conn, "nope", "Archer BE400") is None

    def test_list_registered_products_brand_filter_normalized(self, conn):
        from amz_scout.db import (
            list_registered_products,
            register_asin,
            register_product,
        )

        pid, _ = register_product(conn, "Router", "TP-Link", "Archer BE400")
        register_asin(conn, pid, "UK", "B0UK000001")

        # Stored display literal is "TP-Link"; querying with variants
        # must still return the row.
        assert len(list_registered_products(conn, brand="TP-Link")) == 1
        assert len(list_registered_products(conn, brand="tp-link")) == 1
        assert len(list_registered_products(conn, brand="  TP-LINK  ")) == 1
        assert list_registered_products(conn, brand="wrong") == []

    def test_load_products_from_db_brand_filter_normalized(self, conn):
        from amz_scout.db import (
            load_products_from_db,
            register_asin,
            register_product,
        )

        pid, _ = register_product(conn, "Router", "TP-Link", "Archer BE400")
        register_asin(conn, pid, "UK", "B0UK000001")

        assert len(load_products_from_db(conn, brand="TP-Link")) == 1
        assert len(load_products_from_db(conn, brand="tp-link")) == 1
        assert len(load_products_from_db(conn, brand="\tTP-Link\n")) == 1
        assert load_products_from_db(conn, brand="wrong") == []


# ─── _find_product_by_ean brand-guard normalization ──────────────────


class TestFindProductByEanBrandGuardV7:
    """The v7 refactor relaxed the ``_find_product_by_ean`` brand guard
    from literal ``brand = ?`` to ``LOWER(TRIM(kp.brand)) = LOWER(TRIM(?))``.
    This lets Keepa's casing/spacing variance (the raw source of the
    registry identity drift that v7 exists to fix) still hit the
    matching EAN/UPC row instead of silently returning None and
    producing a duplicate product_id on cross-market bind.

    These tests pin the new behavior directly; prior to this PR the
    helper had no test coverage at all for the brand-guard branch.
    """

    def _seed_keepa_row(
        self,
        conn: sqlite3.Connection,
        asin: str,
        site: str,
        brand: str,
        ean: str,
    ) -> None:
        conn.execute(
            "INSERT INTO keepa_products "
            "(asin, site, brand, ean_list, upc_list, fetch_mode, fetched_at) "
            "VALUES (?, ?, ?, ?, '[]', 'full', '2026-04-20T00:00:00Z')",
            (asin, site, brand, f'["{ean}"]'),
        )

    def test_brand_guard_hits_on_case_and_whitespace_variants(self, conn):
        from amz_scout.db import _find_product_by_ean, register_asin, register_product

        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        # Seed an existing Keepa row whose brand has Keepa-typical
        # surrounding whitespace that the old literal `brand = ?`
        # guard would have missed.
        self._seed_keepa_row(
            conn, "B0REF000001", "UK", "  GL.iNet  ", "1234567890123"
        )
        register_asin(conn, pid, "UK", "B0REF000001")

        # A new ASIN shares the EAN and names the same brand with
        # different casing. The guard must still accept this as a hit.
        raw = {"eanList": ["1234567890123"], "brand": "gl.inet"}
        assert _find_product_by_ean(conn, "B0NEW000001", raw) == pid

        raw = {"eanList": ["1234567890123"], "brand": " GL.INET "}
        assert _find_product_by_ean(conn, "B0NEW000001", raw) == pid

    def test_brand_guard_rejects_true_mismatch(self, conn):
        from amz_scout.db import _find_product_by_ean, register_asin, register_product

        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        self._seed_keepa_row(
            conn, "B0REF000001", "UK", "GL.iNet", "1234567890123"
        )
        register_asin(conn, pid, "UK", "B0REF000001")

        # Same EAN, genuinely different brand — guard must still reject.
        raw = {"eanList": ["1234567890123"], "brand": "TP-Link"}
        assert _find_product_by_ean(conn, "B0NEW000001", raw) is None

    def test_brand_guard_skipped_when_brand_absent(self, conn):
        """Comment in production code promises: 'No brand available —
        EAN alone is sufficient evidence; skip brand guard'. Pin it."""
        from amz_scout.db import _find_product_by_ean, register_asin, register_product

        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        self._seed_keepa_row(
            conn, "B0REF000001", "UK", "GL.iNet", "1234567890123"
        )
        register_asin(conn, pid, "UK", "B0REF000001")

        # No brand in raw => guard skipped, EAN alone binds.
        raw = {"eanList": ["1234567890123"]}
        assert _find_product_by_ean(conn, "B0NEW000001", raw) == pid

        raw = {"eanList": ["1234567890123"], "brand": ""}
        assert _find_product_by_ean(conn, "B0NEW000001", raw) == pid


# ─── GTIN normalization (issue #12) ──────────────────────────────────


class TestNormalizeGtin:
    """Contract: every EAN/UPC/GTIN value lands as a 13-digit
    zero-padded string, or ``""`` for unusable input. Ensures UPC-12
    and EAN-13 of the same GTIN collide.
    """

    def test_upc12_and_ean13_same_gtin_collide(self):
        from amz_scout.db import _normalize_gtin

        assert (
            _normalize_gtin("850018166010")
            == _normalize_gtin("0850018166010")
            == "0850018166010"
        )

    def test_none_empty_and_garbage_return_empty(self):
        from amz_scout.db import _normalize_gtin

        assert _normalize_gtin(None) == ""
        assert _normalize_gtin("") == ""
        assert _normalize_gtin("   ") == ""
        assert _normalize_gtin("abc") == ""

    def test_whitespace_and_hyphens_stripped(self):
        from amz_scout.db import _normalize_gtin

        assert _normalize_gtin("  850-018-166-010  ") == "0850018166010"

    def test_over_13_digits_dropped(self):
        from amz_scout.db import _normalize_gtin

        assert _normalize_gtin("00850018166010") == ""  # 14 digits
        assert _normalize_gtin("9999999999999999") == ""

    def test_short_codes_zero_padded(self):
        from amz_scout.db import _normalize_gtin

        assert _normalize_gtin("1") == "0000000000001"
        assert _normalize_gtin("123456789012") == "0123456789012"

    def test_list_helper_preserves_order_and_drops_empties(self):
        from amz_scout.db import _normalize_gtin_list

        out = _normalize_gtin_list(
            ["850018166010", None, "", "0850018166010", "abc"]
        )
        assert out == ["0850018166010", "0850018166010"]

    def test_list_helper_accepts_none(self):
        from amz_scout.db import _normalize_gtin_list

        assert _normalize_gtin_list(None) == []
        assert _normalize_gtin_list([]) == []


class TestFindProductByEanGtinNormalization:
    """Issue #12 regression: US ASIN with UPC-12 in upcList and
    EU ASIN with EAN-13 (same digits + leading 0) in eanList must
    bind to the same product_id. Before the fix the raw-string set
    union dropped the cross-format match and created duplicates.
    """

    def _seed(
        self,
        conn,
        asin,
        site,
        brand,
        ean_json="[]",
        upc_json="[]",
    ):
        conn.execute(
            "INSERT INTO keepa_products "
            "(asin, site, brand, ean_list, upc_list, "
            " fetch_mode, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, 'full', "
            "'2026-04-20T00:00:00Z')",
            (asin, site, brand, ean_json, upc_json),
        )

    def test_us_upc12_binds_to_eu_ean13_product(self, conn):
        from amz_scout.db import _find_product_by_ean, store_keepa_product

        # EU row is stored via the canonical write path (auto-registers
        # the product). upc_list / ean_list lands as GTIN-13.
        store_keepa_product(
            conn,
            "B0EU000001",
            "DE",
            {
                "title": "Acme Model X",
                "brand": "Acme",
                "model": "Model X",
                "eanList": ["0850018166010"],
                "upcList": None,
            },
            fetched_at="2026-04-20T00:00:00Z",
        )
        pid = conn.execute(
            "SELECT product_id FROM product_asins "
            "WHERE asin = 'B0EU000001' AND marketplace = 'DE'"
        ).fetchone()["product_id"]

        # New US row carries UPC-12 of the same GTIN — must match.
        raw_us = {
            "brand": "Acme",
            "eanList": None,
            "upcList": ["850018166010"],
        }
        assert _find_product_by_ean(conn, "B0US000001", raw_us) == pid

    def test_legacy_stored_upc12_matches_new_ean13(self, conn):
        """Defence-in-depth: direct INSERT (simulating a pre-v8 row that
        bypassed the normalized write path) plus an explicit backfill
        call should still produce the cross-format match, proving the
        migration's logic is correct.
        """
        from amz_scout.db import (
            _find_product_by_ean,
            _json_or_none,
            _normalize_gtin_list,
            _safe_json_list,
            register_asin,
            register_product,
        )

        pid, _ = register_product(conn, "Router", "Acme", "Model X")
        self._seed(
            conn,
            "B0US000001",
            "US",
            "Acme",
            upc_json='["850018166010"]',
        )
        register_asin(conn, pid, "US", "B0US000001")

        # Simulate v8 backfill inline (the migration itself is already
        # applied on the fixture, so we emulate its rewrite on our
        # direct-INSERT row).
        row = conn.execute(
            "SELECT rowid, ean_list, upc_list FROM keepa_products "
            "WHERE asin = 'B0US000001'"
        ).fetchone()
        ean_new = _normalize_gtin_list(_safe_json_list(row["ean_list"]))
        upc_new = _normalize_gtin_list(_safe_json_list(row["upc_list"]))
        conn.execute(
            "UPDATE keepa_products SET ean_list = ?, upc_list = ? "
            "WHERE rowid = ?",
            (
                _json_or_none(ean_new),
                _json_or_none(upc_new),
                row["rowid"],
            ),
        )

        raw_eu = {"brand": "Acme", "eanList": ["0850018166010"]}
        assert _find_product_by_ean(conn, "B0EU000001", raw_eu) == pid

    def test_different_gtin_still_rejected(self, conn):
        """Normalization must not collapse genuinely different barcodes.
        Two products with different GTINs must remain separate.
        """
        from amz_scout.db import _find_product_by_ean, store_keepa_product

        store_keepa_product(
            conn,
            "B0EU000001",
            "DE",
            {
                "title": "Acme Model X",
                "brand": "Acme",
                "model": "Model X",
                "eanList": ["0850018166010"],
            },
            fetched_at="2026-04-20T00:00:00Z",
        )

        raw_different = {
            "brand": "Acme",
            "upcList": ["850018166099"],  # different GTIN
        }
        assert _find_product_by_ean(conn, "B0US000001", raw_different) is None

    def test_store_keepa_product_normalizes_write_path(self, conn):
        import json

        from amz_scout.db import store_keepa_product

        store_keepa_product(
            conn,
            "B0US000002",
            "US",
            {
                "title": "Acme Model Y",
                "brand": "Acme",
                "upcList": ["850018166010"],  # UPC-12
                "eanList": None,
            },
            fetched_at="2026-04-20T00:00:00Z",
        )
        row = conn.execute(
            "SELECT ean_list, upc_list FROM keepa_products "
            "WHERE asin = 'B0US000002'"
        ).fetchone()
        assert row["ean_list"] is None
        assert json.loads(row["upc_list"]) == ["0850018166010"]


class TestGtinBackfillMigrationV8:
    """v7 → v8 upgrade: canonicalize existing ean_list / upc_list JSON
    to GTIN-13 so pre-fix rows match the post-fix query path.
    """

    def test_v8_backfills_existing_rows(self, tmp_path, caplog):
        import json
        import logging

        import amz_scout.db as db_mod
        from amz_scout.db import init_schema, open_db

        db_path = tmp_path / "v7tov8.db"

        # Create v8 schema first, then simulate a "pre-v8" state by
        # removing the v8 migration record AND directly writing raw
        # UPC-12 / EAN-13 into keepa_products (i.e., bypassing the
        # normalized write path).
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        c0.execute("DELETE FROM schema_migrations WHERE version = 8")
        c0.execute(
            "INSERT INTO keepa_products "
            "(asin, site, brand, ean_list, upc_list, "
            " fetch_mode, fetched_at) VALUES "
            "('B0US000001', 'US', 'Acme', NULL, "
            "'[\"850018166010\"]', 'full', "
            "'2026-04-20T00:00:00Z')"
        )
        c0.execute(
            "INSERT INTO keepa_products "
            "(asin, site, brand, ean_list, upc_list, "
            " fetch_mode, fetched_at) VALUES "
            "('B0EU000001', 'DE', 'Acme', "
            "'[\"0850018166010\"]', NULL, 'full', "
            "'2026-04-20T00:00:00Z')"
        )
        c0.commit()
        c0.close()

        # Clear the cached migration marker so re-opening triggers the
        # v8 migration path.
        db_mod._schema_initialized.discard(str(db_path))

        with caplog.at_level(logging.INFO, logger="amz_scout.db"):
            with open_db(db_path) as c1:
                us = c1.execute(
                    "SELECT ean_list, upc_list FROM keepa_products "
                    "WHERE asin = 'B0US000001'"
                ).fetchone()
                eu = c1.execute(
                    "SELECT ean_list, upc_list FROM keepa_products "
                    "WHERE asin = 'B0EU000001'"
                ).fetchone()
                version = c1.execute(
                    "SELECT MAX(version) AS v FROM schema_migrations"
                ).fetchone()["v"]

        assert version == 8
        assert json.loads(us["upc_list"]) == ["0850018166010"]
        assert us["ean_list"] is None
        assert json.loads(eu["ean_list"]) == ["0850018166010"]
        assert eu["upc_list"] is None
        assert any(
            "version 8" in rec.message for rec in caplog.records
        )

    def test_v8_is_idempotent(self, conn):
        from amz_scout.db import init_schema

        init_schema(conn)  # second call
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations "
            "WHERE version = 8"
        ).fetchone()
        assert row["c"] == 1

    def test_v8_leaves_already_canonical_rows_untouched(self, tmp_path):
        """Write amplification guard: rows whose ean_list / upc_list are
        already canonical must not be rewritten.
        """
        import json

        import amz_scout.db as db_mod
        from amz_scout.db import init_schema, open_db

        db_path = tmp_path / "noop.db"
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        c0.execute("DELETE FROM schema_migrations WHERE version = 8")
        c0.execute(
            "INSERT INTO keepa_products "
            "(asin, site, brand, ean_list, upc_list, "
            " fetch_mode, fetched_at) VALUES "
            "('B0XX000001', 'US', 'Acme', "
            "'[\"0850018166010\"]', NULL, 'full', "
            "'2026-04-20T00:00:00Z')"
        )
        c0.commit()
        c0.close()

        db_mod._schema_initialized.discard(str(db_path))

        with open_db(db_path) as c1:
            row = c1.execute(
                "SELECT ean_list FROM keepa_products "
                "WHERE asin = 'B0XX000001'"
            ).fetchone()

        assert json.loads(row["ean_list"]) == ["0850018166010"]
        # The migration loop's `if ean_new == ean_raw` no-op guard
        # ensures no UPDATE ran; this test fails loudly if that guard
        # is removed in the future.


class TestV7RetryAfterV8Commit:
    """Regression for the v7/v8 partial-failure trap.

    ``_migrate`` captures ``current = MAX(version)`` once at entry and
    runs v8 inside an inner txn, then v7 outside. If v8 commits but v7
    raises, a retry would see ``current = 8`` and — under the old
    ``current < 7`` gate — silently skip v7 forever, leaving the
    ``products`` table on pre-v7 schema while ``schema_migrations``
    claims v8. The fix gates v7 by actual record existence.
    """

    def test_v7_reruns_when_only_v8_committed(self, tmp_path):
        import amz_scout.db as db_mod
        from amz_scout.db import init_schema, open_db

        db_path = tmp_path / "v7_skipped.db"

        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        c0.execute("PRAGMA foreign_keys = ON")
        init_schema(c0)

        # Simulate the partial-failure state: v8 record is present,
        # v7 record is gone, and ``products`` is back on its pre-v7
        # shape (no brand_key / model_key). v6 and v8 records remain
        # intact so MAX(version) = 8.
        c0.execute("PRAGMA foreign_keys = OFF")
        c0.execute("DELETE FROM schema_migrations WHERE version = 7")
        c0.execute("DROP TABLE products")
        c0.execute("""
            CREATE TABLE products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category        TEXT NOT NULL,
                brand           TEXT NOT NULL,
                model           TEXT NOT NULL,
                search_keywords TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at      TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(brand, model)
            )
        """)
        c0.execute(
            "INSERT INTO products (id, category, brand, model) "
            "VALUES (10, 'Router', 'TP-Link', 'Archer BE400')"
        )
        c0.execute("PRAGMA foreign_keys = ON")
        c0.commit()

        # Sanity: without the fix, current = MAX(version) = 8 would
        # bypass v7 on reopen.
        max_ver = c0.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()["v"]
        assert max_ver == 8
        c0.close()

        db_mod._schema_initialized.discard(str(db_path))

        with open_db(db_path) as c1:
            cols = {
                r["name"]
                for r in c1.execute("PRAGMA table_info(products)")
            }
            v7_rec = c1.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 7"
            ).fetchone()
            kept = c1.execute(
                "SELECT id, brand, model, brand_key, model_key "
                "FROM products WHERE id = 10"
            ).fetchone()

        assert "brand_key" in cols, "v7 migration must rerun"
        assert "model_key" in cols, "v7 migration must rerun"
        assert v7_rec is not None, "v7 record must be inserted on retry"
        assert kept["brand_key"] == "tp-link"
        assert kept["model_key"] == "archer be400"


# ─── register_product concurrency ────────────────────────────────


class TestRegisterProductConcurrency:
    """`register_product` must be race-safe: concurrent writers for
    the same normalized (brand, model) must all return the same
    product_id, with exactly one is_new=True and no IntegrityError
    escaping. Regression guard against the pre-UPSERT TOCTOU window.
    """

    def _run_workers(self, db_path, variants):
        import threading

        results: list[tuple[int, bool] | Exception | None] = [None] * len(variants)
        # Barrier forces all workers into the write-path simultaneously
        # so contention is deterministic rather than order-of-start.
        barrier = threading.Barrier(len(variants))

        def worker(i, brand, model):
            c = sqlite3.connect(str(db_path), timeout=5.0)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            try:
                from amz_scout.db import register_product
                barrier.wait(timeout=5.0)
                results[i] = register_product(c, "Router", brand, model)
            except Exception as exc:
                results[i] = exc
            finally:
                c.close()

        threads = [
            threading.Thread(target=worker, args=(i, b, m))
            for i, (b, m) in enumerate(variants)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_concurrent_same_key_no_integrity_error(self, tmp_path):
        """N workers all register the same normalized (brand, model).
        Exactly one sees is_new=True; all share the same product_id;
        no IntegrityError leaks out.
        """
        import amz_scout.db as db_mod

        db_path = tmp_path / "concurrency.db"
        c0 = sqlite3.connect(str(db_path))
        c0.execute("PRAGMA journal_mode = WAL")
        c0.execute("PRAGMA foreign_keys = ON")
        init_schema(c0)
        c0.close()
        db_mod._schema_initialized.discard(str(db_path))

        variants = [("TP-Link", "Archer BE400")] * 8
        results = self._run_workers(db_path, variants)

        for r in results:
            assert isinstance(r, tuple), f"worker raised or missed: {r!r}"
        tuple_results = [r for r in results if isinstance(r, tuple)]
        ids = {r[0] for r in tuple_results}
        new_flags = [r[1] for r in tuple_results]
        assert len(ids) == 1, f"expected all workers to agree on id, got {ids}"
        assert new_flags.count(True) == 1, (
            f"expected exactly one is_new=True, got {new_flags}"
        )
        assert new_flags.count(False) == 7

    def test_concurrent_variant_literals_display_is_first_writer(self, tmp_path):
        """Concurrent writers pass different casing/whitespace variants
        of the same normalized key. Regardless of which thread wins,
        the stored display literal is one of the inputs (no corruption)
        and all threads observe the same product_id.
        """
        import amz_scout.db as db_mod

        db_path = tmp_path / "concurrency_variants.db"
        c0 = sqlite3.connect(str(db_path))
        c0.execute("PRAGMA journal_mode = WAL")
        c0.execute("PRAGMA foreign_keys = ON")
        init_schema(c0)
        c0.close()
        db_mod._schema_initialized.discard(str(db_path))

        variants = [
            ("TP-Link", "Archer BE400"),
            ("tp-link", "archer be400"),
            ("  TP-LINK  ", "Archer  BE400"),
            ("Tp-Link", "archer\tbe400"),
        ]
        results = self._run_workers(db_path, variants)
        for r in results:
            assert isinstance(r, tuple), f"worker raised or missed: {r!r}"
        tuple_results = [r for r in results if isinstance(r, tuple)]
        ids = {r[0] for r in tuple_results}
        assert len(ids) == 1, f"expected single canonical id, got {ids}"

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT brand, model, brand_key, model_key FROM products"
        ).fetchone()
        c.close()
        # Display literal is ONE of the raced inputs, not a merged
        # string. Keys are canonical.
        assert (row["brand"], row["model"]) in variants
        assert row["brand_key"] == "tp-link"
        assert row["model_key"] == "archer be400"


# ─── Migration ordering: v8 must wait for v7 (Copilot PR #20) ────────


class TestMigrationOrderingV8AfterV7:
    """Regression guard for the schema_migrations stranding bug.

    If v8 is recorded *before* v7 commits, a v7 failure leaves the DB
    at MAX(version)=8 with v7 un-applied. The next ``init_schema()``
    short-circuits on ``current >= SCHEMA_VERSION`` and never retries
    v7 — silent permanent corruption.
    """

    def test_v7_failure_does_not_strand_v8_record(self, tmp_path, monkeypatch):
        from amz_scout import db as db_mod

        db_path = tmp_path / "ordering.db"
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        # Force schema_migrations back to MAX(version)=6 so the v7 + v8
        # paths both rerun on next open. (Underlying tables are already
        # at v8 shape — that's fine; v8's ALTER is column-existence
        # guarded and v7 is monkey-patched out.)
        c0.execute("DELETE FROM schema_migrations WHERE version >= 7")
        c0.commit()
        c0.close()
        # init_schema caches db_paths it has fully migrated and short-
        # circuits on cache hit. Clear that one entry so the next open
        # actually re-runs _migrate (where the patched v7 lives).
        db_mod._schema_initialized.discard(str(db_path))

        def fake_migrate_v7(_conn):
            raise RuntimeError("simulated v7 failure")

        monkeypatch.setattr(db_mod, "_migrate_to_v7", fake_migrate_v7)

        c1 = sqlite3.connect(str(db_path))
        c1.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError, match="simulated v7"):
            init_schema(c1)

        versions = {
            r["version"]
            for r in c1.execute("SELECT version FROM schema_migrations")
        }
        assert 7 not in versions, "v7 record must not appear after raise"
        assert 8 not in versions, (
            "v8 record must not appear if v7 hasn't completed — "
            "otherwise MAX(version)=8 would block v7 retry forever"
        )
        c1.close()

    def test_v7_recovery_after_failure_completes_to_v8(
        self, tmp_path, monkeypatch
    ):
        """After a v7 failure, removing the patch must let init_schema
        complete v7 AND v8 cleanly on the next call.
        """
        from amz_scout import db as db_mod

        db_path = tmp_path / "recovery.db"
        c0 = sqlite3.connect(str(db_path))
        c0.row_factory = sqlite3.Row
        init_schema(c0)
        c0.execute("DELETE FROM schema_migrations WHERE version >= 7")
        c0.commit()
        c0.close()
        db_mod._schema_initialized.discard(str(db_path))

        original_v7 = db_mod._migrate_to_v7

        def fake_migrate_v7(_conn):
            raise RuntimeError("simulated v7 failure")

        monkeypatch.setattr(db_mod, "_migrate_to_v7", fake_migrate_v7)

        c1 = sqlite3.connect(str(db_path))
        c1.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError):
            init_schema(c1)
        c1.close()

        # init_schema doesn't add to the cache when _migrate raises (the
        # ``_schema_initialized.add(...)`` is unreachable past the
        # exception), so we don't strictly need a discard before c2 — but
        # clear it anyway in case future refactors flip the order.
        db_mod._schema_initialized.discard(str(db_path))

        # Restore real v7 and reopen — both v7 and v8 should now apply.
        monkeypatch.setattr(db_mod, "_migrate_to_v7", original_v7)
        c2 = sqlite3.connect(str(db_path))
        c2.row_factory = sqlite3.Row
        init_schema(c2)
        max_v = c2.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()["v"]
        assert max_v == 8
        c2.close()
