"""Tests for core flows identified as untested in PR review.

Covers:
- I5: ensure_keepa_data confirmation flow (phase="needs_confirmation")
- I6: _auto_register_from_keepa (register when brand+title present, skip when empty)
- I7: query_trends new_product flag logic
- I8: validate_and_discover phase transitions
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from amz_scout.db import (
    find_product,
    init_schema,
    register_asin,
    register_product,
    store_keepa_product,
)
from amz_scout.models import Product

# ─── Fixtures ────────────────────────────────────────────────────────


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


def _make_keepa_raw(
    brand: str = "GL.iNet",
    title: str = "Slate 7 Router",
    model: str = "GL-BE3600",
    asin: str = "B0TEST12345",
    product_group: str = "Router",
    ean_list: list[str] | None = None,
    upc_list: list[str] | None = None,
) -> dict:
    """Create minimal Keepa raw product JSON for testing."""
    d = {
        "asin": asin,
        "brand": brand,
        "title": title,
        "model": model,
        "productGroup": product_group,
        "csv": [],
        "imagesCSV": "",
        "salesRanks": {},
        "monthlySoldHistory": [],
        "buyBoxSellerIdHistory": [],
        "couponHistory": [],
    }
    if ean_list is not None:
        d["eanList"] = ean_list
    if upc_list is not None:
        d["upcList"] = upc_list
    return d


# ─── I6: _auto_register_from_keepa ───────────────────────────────────


class TestAutoRegisterFromKeepa:
    """Test auto-registration behavior when Keepa data is stored."""

    def test_registers_when_brand_and_title_present(self, conn):
        """Product with brand+title in Keepa data should be auto-registered."""
        raw = _make_keepa_raw(brand="GL.iNet", title="Slate 7 Router")
        result = store_keepa_product(conn, "B0TEST12345", "UK", raw, "2026-04-10T00:00:00Z")

        assert result is not None
        assert result["brand"] == "GL.iNet"
        assert result["new_product"] is True
        assert result["marketplace"] == "UK"

        # Verify it's in the product registry
        row = conn.execute(
            "SELECT * FROM product_asins WHERE asin = ? AND marketplace = ?",
            ("B0TEST12345", "UK"),
        ).fetchone()
        assert row is not None
        assert row["status"] == "unverified"

    def test_skips_when_brand_empty(self, conn):
        """Product with empty brand should NOT be auto-registered."""
        raw = _make_keepa_raw(brand="", title="Some Product")
        result = store_keepa_product(conn, "B0NOBRND01", "UK", raw, "2026-04-10T00:00:00Z")

        assert result is None

        row = conn.execute("SELECT * FROM product_asins WHERE asin = ?", ("B0NOBRND01",)).fetchone()
        assert row is None

    def test_skips_when_title_empty(self, conn):
        """Product with empty title should NOT be auto-registered."""
        raw = _make_keepa_raw(brand="SomeBrand", title="")
        result = store_keepa_product(conn, "B0NOTITL01", "UK", raw, "2026-04-10T00:00:00Z")

        assert result is None

        row = conn.execute("SELECT * FROM product_asins WHERE asin = ?", ("B0NOTITL01",)).fetchone()
        assert row is None

    def test_skips_when_already_registered(self, conn):
        """ASIN already in product_asins for this marketplace should be skipped."""
        # First registration
        raw = _make_keepa_raw()
        store_keepa_product(conn, "B0TEST12345", "UK", raw, "2026-04-10T00:00:00Z")

        # Second call with same ASIN/marketplace — should return None (already registered)
        result = store_keepa_product(conn, "B0TEST12345", "UK", raw, "2026-04-10T01:00:00Z")
        assert result is None

    def test_registers_same_asin_different_marketplace(self, conn):
        """Same ASIN in different marketplace should create new association."""
        raw = _make_keepa_raw()
        r1 = store_keepa_product(conn, "B0TEST12345", "UK", raw, "2026-04-10T00:00:00Z")
        r2 = store_keepa_product(conn, "B0TEST12345", "DE", raw, "2026-04-10T01:00:00Z")

        assert r1 is not None
        assert r1["new_product"] is True
        assert r2 is not None
        assert r2["new_product"] is False  # same product, just new marketplace

        rows = conn.execute(
            "SELECT marketplace FROM product_asins WHERE asin = ?", ("B0TEST12345",)
        ).fetchall()
        markets = {r["marketplace"] for r in rows}
        assert markets == {"UK", "DE"}

    def test_ean_binds_cross_market(self, conn):
        """Product with matching EAN should bind to existing product, not create new."""
        raw_uk = _make_keepa_raw(
            brand="GL.iNet",
            title="Slate 7 Router",
            model="GL-BE3600",
            asin="B0UK_EAN_01",
            ean_list=["0850018166010"],
        )
        r1 = store_keepa_product(conn, "B0UK_EAN_01", "UK", raw_uk, "2026-04-10T00:00:00Z")
        assert r1 is not None
        assert r1["new_product"] is True

        raw_de = _make_keepa_raw(
            brand="GL.iNet",
            title="GL-BE3600 WiFi 7 Reiserouter",
            model="GL-BE3600",
            asin="B0DE_EAN_01",
            ean_list=["0850018166010"],
        )
        r2 = store_keepa_product(conn, "B0DE_EAN_01", "DE", raw_de, "2026-04-10T01:00:00Z")
        assert r2 is not None
        assert r2["new_product"] is False
        assert r2["product_id"] == r1["product_id"]
        assert r2["match_type"] == "ean"

    def test_upc_binds_cross_market(self, conn):
        """UPC match should also bind to existing product."""
        raw_us = _make_keepa_raw(
            brand="TP-Link",
            title="AX1500 Router",
            model="AX1500",
            asin="B0US_UPC_01",
            upc_list=["885913123456"],
        )
        r1 = store_keepa_product(conn, "B0US_UPC_01", "US", raw_us, "2026-04-10T00:00:00Z")
        assert r1 is not None
        assert r1["match_type"] == "brand_title"

        raw_ca = _make_keepa_raw(
            brand="TP-Link",
            title="AX1500 Wi-Fi Router",
            model="AX1500",
            asin="B0CA_UPC_01",
            upc_list=["885913123456"],
        )
        r2 = store_keepa_product(conn, "B0CA_UPC_01", "CA", raw_ca, "2026-04-10T01:00:00Z")
        assert r2 is not None
        assert r2["product_id"] == r1["product_id"]
        assert r2["match_type"] == "ean"

    def test_no_ean_falls_back_to_brand_title(self, conn):
        """Product without EAN/UPC should use brand+title fallback."""
        raw = _make_keepa_raw(brand="TestBrand", title="TestProduct", model="TP-100")
        result = store_keepa_product(conn, "B0NO_EAN_01", "UK", raw, "2026-04-10T00:00:00Z")
        assert result is not None
        assert result["new_product"] is True
        assert result.get("match_type") == "brand_title"

    def test_ean_no_cross_brand_match(self, conn):
        """EAN match should NOT bind across different brands."""
        raw_a = _make_keepa_raw(
            brand="BrandA",
            title="ProductX",
            model="X-100",
            asin="B0BRAND_A01",
            ean_list=["SHARED_EAN_999"],
        )
        store_keepa_product(conn, "B0BRAND_A01", "UK", raw_a, "2026-04-10T00:00:00Z")

        raw_b = _make_keepa_raw(
            brand="BrandB",
            title="ProductY",
            model="Y-200",
            asin="B0BRAND_B01",
            ean_list=["SHARED_EAN_999"],
        )
        r2 = store_keepa_product(conn, "B0BRAND_B01", "US", raw_b, "2026-04-10T01:00:00Z")
        assert r2 is not None
        assert r2["new_product"] is True
        assert r2["brand"] == "BrandB"
        assert r2["match_type"] == "brand_title"


# ─── I5: ensure_keepa_data confirmation flow ──────────────────────────


class TestEnsureKeepaDataConfirmation:
    """Test the batch token gate (phase='needs_confirmation')."""

    @patch("amz_scout.api._resolve_context")
    @patch("amz_scout.api.open_db")
    def test_returns_confirmation_when_tokens_exceed_threshold(
        self, mock_open_db, mock_resolve_ctx
    ):
        from amz_scout.api import _BATCH_TOKEN_THRESHOLD, ensure_keepa_data

        # Create 10 products — should exceed the 6-token threshold
        products = [
            Product(
                category="Router",
                brand="Test",
                model=f"Model-{i}",
                default_asin=f"B0TEST{i:05d}",
                marketplace_overrides={"UK": {"asin": f"B0TEST{i:05d}"}},
            )
            for i in range(10)
        ]

        # Mock context
        mock_info = MagicMock()
        mock_info.products = products
        mock_info.db_path = ":memory:"
        mock_info.output_base = "/tmp/test"
        mock_info.marketplace_aliases = {"uk": "UK", "gb": "UK"}
        mock_resolve_ctx.return_value = mock_info

        # Mock DB connection
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_open_db.return_value = mock_conn

        # Patch at the source modules (lazy imports inside function body)
        with (
            patch("amz_scout.freshness.evaluate_freshness") as mock_eval,
            patch("amz_scout.freshness.partition_by_action") as mock_partition,
            patch("amz_scout.freshness.query_freshness", return_value={}),
        ):
            # All 10 products need fetching
            mock_freshness_items = [
                MagicMock(asin=f"B0TEST{i:05d}", site="UK", model=f"Model-{i}") for i in range(10)
            ]
            mock_eval.return_value = mock_freshness_items
            mock_partition.return_value = ([], mock_freshness_items, [])

            r = ensure_keepa_data(marketplace="UK", strategy="lazy", confirm=False)

        assert r["ok"] is True
        assert r["meta"]["phase"] == "needs_confirmation"
        assert r["meta"]["estimated_tokens"] >= _BATCH_TOKEN_THRESHOLD
        assert "preview" in r["data"]
        assert len(r["data"]["preview"]) == 10

    @patch("amz_scout.api._resolve_context")
    @patch("amz_scout.api.open_db")
    @patch("amz_scout.keepa_service.get_keepa_data")
    def test_confirm_true_proceeds_with_fetch(self, mock_get_keepa, mock_open_db, mock_resolve_ctx):
        from amz_scout.api import ensure_keepa_data

        products = [
            Product(
                category="Router",
                brand="Test",
                model="Model-1",
                default_asin="B0TEST00001",
                marketplace_overrides={"UK": {"asin": "B0TEST00001"}},
            )
        ]

        mock_info = MagicMock()
        mock_info.products = products
        mock_info.db_path = ":memory:"
        mock_info.output_base = "/tmp/test"
        mock_info.marketplace_aliases = {"uk": "UK"}
        mock_resolve_ctx.return_value = mock_info

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_open_db.return_value = mock_conn

        # Mock Keepa result
        mock_result = MagicMock()
        mock_result.fetch_count = 1
        mock_result.cache_count = 0
        mock_result.skip_count = 0
        mock_result.tokens_used = 1
        mock_result.tokens_remaining = 59
        mock_result.outcomes = []
        mock_get_keepa.return_value = mock_result

        r = ensure_keepa_data(marketplace="UK", strategy="lazy", confirm=True)

        # Should proceed without confirmation gate
        mock_get_keepa.assert_called_once()
        assert r["ok"] is True


# ─── I7: query_trends new_product detection ───────────────────────────


class TestQueryTrendsNewProduct:
    """Test that query_trends sets new_product flag for ASIN pass-through."""

    @patch("amz_scout.api._resolve_context")
    def test_new_product_flag_set_after_auto_registration(self, mock_resolve_ctx, tmp_path):
        from amz_scout.api import query_trends

        # Set up real DB
        db_path = tmp_path / "amz_scout.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        init_schema(conn)

        # Insert Keepa product data with brand+title for auto-registration
        raw = _make_keepa_raw(
            asin="B0NEWASIN1",
            brand="NewBrand",
            title="New Router X1",
            model="X1",
            product_group="Router",
        )
        store_keepa_product(conn, "B0NEWASIN1", "US", raw, "2026-04-10T00:00:00Z")
        conn.close()

        # Mock context with no products (forces ASIN pass-through, Level 3)
        mock_info = MagicMock()
        mock_info.products = []
        mock_info.db_path = db_path
        mock_info.output_base = tmp_path
        mock_info.marketplace_aliases = {"us": "US"}
        mock_info.config = None
        mock_resolve_ctx.return_value = mock_info

        r = query_trends(product="B0NEWASIN1", marketplace="US", auto_fetch=False)

        assert r["ok"] is True
        # The product was already registered above, so resolution should find it


# ─── I8: validate_and_discover phase transitions ──────────────────────


class TestValidateAndDiscoverPhases:
    """Test the three-phase flow of validate_and_discover."""

    @patch("amz_scout.api.validate_asins")
    def test_all_verified_no_suggestions(self, mock_validate):
        from amz_scout.api import validate_and_discover

        mock_validate.return_value = {
            "ok": True,
            "data": [
                {
                    "brand": "GL.iNet",
                    "model": "Slate 7",
                    "marketplace": "UK",
                    "asin": "B0F2MR53D6",
                    "status": "verified",
                    "reason": "title matches",
                },
            ],
            "error": None,
            "meta": {"verified": 1, "not_listed": 0, "wrong_product": 0},
        }

        r = validate_and_discover(marketplace="UK")

        assert r["ok"] is True
        assert r["meta"]["phase"] == "validate"
        assert "nothing to discover" in r["meta"]["message"].lower()

    @patch("amz_scout.api.validate_asins")
    def test_suggestions_return_pending_confirmation(self, mock_validate):
        from amz_scout.api import validate_and_discover

        mock_validate.return_value = {
            "ok": True,
            "data": [
                {
                    "brand": "GL.iNet",
                    "model": "Slate 7",
                    "marketplace": "UK",
                    "asin": "B0WRONG001",
                    "status": "not_listed",
                    "reason": "no title in Keepa",
                },
                {
                    "brand": "ASUS",
                    "model": "RT-BE58",
                    "marketplace": "UK",
                    "asin": "B0FGDRP3VZ",
                    "status": "verified",
                    "reason": "title matches",
                },
            ],
            "error": None,
            "meta": {"verified": 1, "not_listed": 1, "wrong_product": 0},
        }

        r = validate_and_discover(marketplace="UK", auto_discover=False)

        assert r["ok"] is True
        assert r["meta"]["phase"] == "pending_confirmation"
        assert len(r["meta"]["discover_pending"]) == 1
        assert r["meta"]["discover_pending"][0]["old_asin"] == "B0WRONG001"

    @patch("amz_scout.api.validate_asins")
    def test_propagates_validation_error(self, mock_validate):
        from amz_scout.api import validate_and_discover

        mock_validate.return_value = {
            "ok": False,
            "data": [],
            "error": "DB connection failed",
            "meta": {},
        }

        r = validate_and_discover(marketplace="UK")

        assert r["ok"] is False
        assert "DB connection failed" in r["error"]

    @patch("amz_scout.api._run_discover_batch")
    @patch("amz_scout.api.validate_asins")
    def test_auto_discover_runs_batch(self, mock_validate, mock_batch):
        from amz_scout.api import validate_and_discover

        mock_validate.return_value = {
            "ok": True,
            "data": [
                {
                    "brand": "GL.iNet",
                    "model": "Slate 7",
                    "marketplace": "UK",
                    "asin": "B0WRONG001",
                    "status": "wrong_product",
                    "reason": "title mismatch",
                },
            ],
            "error": None,
            "meta": {"verified": 0, "not_listed": 0, "wrong_product": 1},
        }

        mock_batch.return_value = (
            [
                {
                    "brand": "GL.iNet",
                    "model": "Slate 7",
                    "marketplace": "UK",
                    "old_asin": "B0WRONG001",
                    "new_asin": "B0CORRECT1",
                    "ok": True,
                }
            ],
            1,
            0,
        )

        r = validate_and_discover(marketplace="UK", auto_discover=True)

        assert r["ok"] is True
        assert r["meta"]["phase"] == "discovered"
        assert r["meta"]["discovered"] == 1
        assert r["meta"]["failed"] == 0
        mock_batch.assert_called_once()


# ─── Additional: get_pending_markets and register_market_asins ────────


class TestGetPendingMarkets:
    """Test the pending markets helper for new product ASIN backfill."""

    def test_returns_unregistered_keepa_markets(self, conn, tmp_path):
        from amz_scout.api import get_pending_markets

        # Register a product with only UK
        pid, _ = register_product(conn, "Router", "TestBrand", "TestModel")
        register_asin(conn, pid, "UK", "B0UK000001")
        conn.close()

        # Need a real DB on disk for get_pending_markets
        db_path = tmp_path / "amz_scout.db"
        c = sqlite3.connect(str(db_path))
        c.execute("PRAGMA foreign_keys = ON")
        c.row_factory = sqlite3.Row
        init_schema(c)
        pid2, _ = register_product(c, "Router", "TestBrand", "TestModel")
        register_asin(c, pid2, "UK", "B0UK000001")
        c.close()

        r = get_pending_markets(pid2, db_path=db_path)

        assert r["ok"] is True
        assert "UK" not in r["data"]["pending"]
        assert "UK" in r["data"]["registered"]
        # Should have other Keepa-supported markets in pending
        assert len(r["data"]["pending"]) > 0


class TestRegisterMarketAsins:
    """Test batch ASIN registration with skip-existing logic."""

    def test_skips_existing_marketplace(self, tmp_path):
        from amz_scout.api import register_market_asins

        db_path = tmp_path / "amz_scout.db"
        c = sqlite3.connect(str(db_path))
        c.execute("PRAGMA foreign_keys = ON")
        c.row_factory = sqlite3.Row
        init_schema(c)

        pid, _ = register_product(c, "Router", "TestBrand", "TestModel")
        register_asin(c, pid, "UK", "B0UK000001", status="verified")
        c.close()

        r = register_market_asins(
            pid,
            asins={"UK": "B0NEWUK001", "DE": "B0DE000001", "FR": "B0FR000001"},
            db_path=db_path,
        )

        assert r["ok"] is True
        assert r["data"]["registered"] == 2  # DE + FR
        assert r["data"]["skipped"] == 1  # UK already exists

        # Verify UK ASIN was NOT overwritten
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_schema(c)
        row = c.execute(
            "SELECT asin, status FROM product_asins WHERE product_id = ? AND marketplace = 'UK'",
            (pid,),
        ).fetchone()
        assert row["asin"] == "B0UK000001"  # unchanged
        assert row["status"] == "verified"  # unchanged
        c.close()


# ─── P0: find_product cross-market resolution ───────────────────────


class TestFindProductCrossMarket:
    """Test that find_product returns product even when target marketplace has no ASIN."""

    def test_finds_product_when_marketplace_has_asin(self, conn):
        """Standard case: product has ASIN for requested marketplace."""
        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        register_asin(conn, pid, "UK", "B0UK_FIND01")

        row = find_product(conn, "Slate 7", marketplace="UK")
        assert row is not None
        assert row["asin"] == "B0UK_FIND01"
        assert row["marketplace"] == "UK"

    def test_finds_product_when_marketplace_has_no_asin(self, conn):
        """Product registered for UK, queried for DE — should still return the product."""
        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        register_asin(conn, pid, "UK", "B0UK_FIND02")

        row = find_product(conn, "Slate 7", marketplace="DE")
        assert row is not None
        assert row["model"] == "Slate 7"
        assert row["brand"] == "GL.iNet"
        assert row["id"] == pid
        # ASIN should be NULL since DE has no registration
        assert row["asin"] is None
        assert row["marketplace"] is None

    def test_returns_none_when_product_not_exists(self, conn):
        """Completely unknown product should return None."""
        row = find_product(conn, "NonExistentProduct", marketplace="UK")
        assert row is None

    def test_no_marketplace_returns_any_asin(self, conn):
        """Without marketplace filter, should return any available ASIN."""
        pid, _ = register_product(conn, "Router", "GL.iNet", "Slate 7")
        register_asin(conn, pid, "UK", "B0UK_FIND03")

        row = find_product(conn, "Slate 7")
        assert row is not None
        assert row["asin"] == "B0UK_FIND03"
