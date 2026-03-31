"""Tests for data models."""

from amz_scout.models import CompetitiveData, PriceHistory, Product


class TestProduct:
    def test_asin_default(self):
        p = Product("Travel Router", "ASUS", "RT-BE58", "B0FGDRP3VZ", "")
        assert p.asin_for("UK") == "B0FGDRP3VZ"
        assert p.asin_for("DE") == "B0FGDRP3VZ"

    def test_asin_override(self):
        p = Product(
            "Travel Router", "ASUS", "RT-BE58", "B0FGDRP3VZ", "",
            {"CA": {"asin": "B0FSPQSJGF"}},
        )
        assert p.asin_for("UK") == "B0FGDRP3VZ"
        assert p.asin_for("CA") == "B0FSPQSJGF"

    def test_note(self):
        p = Product(
            "Home Router", "ASUS", "RT-BE88U", "B0D47MGRS4", "",
            {"AU": {"note": "Not listed on AU"}},
        )
        assert p.note_for("AU") == "Not listed on AU"
        assert p.note_for("UK") is None

    def test_frozen(self):
        p = Product("Travel Router", "ASUS", "RT-BE58", "B0FGDRP3VZ", "")
        try:
            p.brand = "TP-Link"  # type: ignore
            assert False, "Should raise"
        except AttributeError:
            pass


class TestCompetitiveData:
    def test_creation(self):
        cd = CompetitiveData(
            date="2026-03-31", site="UK", category="Travel Router",
            brand="ASUS", model="RT-BE58", asin="B0FGDRP3VZ",
            title="ASUS RT-BE58", price="£104.50", rating="3.8 out of 5 stars",
            review_count="(20)", bought_past_month="N/A",
            bsr="51 in Routers", available="Yes",
            url="https://www.amazon.co.uk/dp/B0FGDRP3VZ",
        )
        assert cd.site == "UK"
        assert cd.price == "£104.50"


class TestPriceHistory:
    def test_defaults_none(self):
        ph = PriceHistory(
            date="2026-03-31", site="UK", category="Travel Router",
            brand="GL.iNet", model="Beryl 7", asin="B0GF1J99S4",
        )
        assert ph.buybox_current is None
        assert ph.amz_lowest is None
        assert ph.sales_rank is None

    def test_with_values(self):
        ph = PriceHistory(
            date="2026-03-31", site="UK", category="Travel Router",
            brand="GL.iNet", model="Beryl 7", asin="B0GF1J99S4",
            buybox_current=153.59, buybox_lowest=140.00,
            buybox_highest=191.99, buybox_avg90=175.50,
        )
        assert ph.buybox_current == 153.59
