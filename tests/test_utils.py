"""Tests for utility functions."""

import pytest

from amz_scout.utils import (
    cents_to_price,
    parse_bsr_routers,
    parse_history_price,
    parse_monthly_sales,
    parse_price,
    parse_rating,
    parse_reviews,
)


class TestParsePrice:
    def test_gbp(self):
        assert parse_price("£104.50") == 104.50

    def test_eur_comma_decimal(self):
        assert parse_price("€94,99") == 94.99

    def test_eur_suffix(self):
        assert parse_price("83,99€") == 83.99

    def test_usd(self):
        assert parse_price("$129.99") == 129.99

    def test_cad(self):
        assert parse_price("C$198.99") == 198.99

    def test_european_with_thousands(self):
        assert parse_price("€1.115,00") == 1115.00

    def test_us_with_thousands(self):
        assert parse_price("$1,115.00") == 1115.00

    def test_unavailable(self):
        assert parse_price("Currently unavailable.") is None

    def test_na(self):
        assert parse_price("N/A") is None

    def test_dash(self):
        assert parse_price("-") is None

    def test_empty(self):
        assert parse_price("") is None

    def test_none(self):
        assert parse_price(None) is None


class TestParseRating:
    def test_standard(self):
        assert parse_rating("3.8 out of 5 stars") == 3.8

    def test_high_rating(self):
        assert parse_rating("4.7 out of 5 stars") == 4.7

    def test_na(self):
        assert parse_rating("N/A") is None


class TestParseReviews:
    def test_simple(self):
        assert parse_reviews("(20)") == 20

    def test_with_comma(self):
        assert parse_reviews("(1,115)") == 1115

    def test_na(self):
        assert parse_reviews("N/A") is None


class TestParseBsrRouters:
    def test_standard(self):
        assert parse_bsr_routers(
            "6,546 in Computers & Accessories (See Top 100)  51 in Routers"
        ) == 51

    def test_network_routers(self):
        assert parse_bsr_routers("#125 in Network Routers") == 125

    def test_with_hash(self):
        assert parse_bsr_routers("#14,209 in Electronics  #125 in Network Routers") == 125

    def test_truncated(self):
        assert parse_bsr_routers(
            "10,947 in Computer & Accessories (See Top 100)  85 i"
        ) == 85

    def test_na(self):
        assert parse_bsr_routers("N/A") is None


class TestParseMonthly:
    def test_hundred_plus(self):
        assert parse_monthly_sales("100+ bought in past month") == "100+"

    def test_fifty_plus(self):
        assert parse_monthly_sales("50+ bought in past month") == "50+"

    def test_na(self):
        assert parse_monthly_sales("N/A") == "-"


class TestParseHistoryPrice:
    def test_gbp(self):
        price, date = parse_history_price("£84.90 (Dec 11, 2025)")
        assert price == 84.90
        assert date == "Dec 11, 2025"

    def test_eur(self):
        price, date = parse_history_price("83,99€ (Mar 09, 2026)")
        assert price == 83.99
        assert date == "Mar 09, 2026"

    def test_dash(self):
        price, date = parse_history_price("-")
        assert price is None
        assert date == ""


class TestCentsToPrice:
    def test_standard(self):
        assert cents_to_price(15359) == 153.59

    def test_negative_one(self):
        assert cents_to_price(-1) is None

    def test_none(self):
        assert cents_to_price(None) is None

    def test_zero(self):
        assert cents_to_price(0) == 0.0
