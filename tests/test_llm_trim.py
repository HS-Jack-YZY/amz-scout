"""Unit tests for ``amz_scout._llm_trim`` — allow-list enforcement, immutability,
empty input, and schema-drift safety.
"""

from amz_scout._llm_trim import (
    LLM_SAFE_COMPETITIVE_FIELDS,
    LLM_SAFE_DEAL_FIELDS,
    LLM_SAFE_SELLER_FIELDS,
    LLM_SAFE_TIMESERIES_FIELDS,
    trim,
    trim_competitive_rows,
    trim_deals_rows,
    trim_seller_rows,
    trim_timeseries_rows,
)


def _full_cs_row() -> dict:
    """One synthetic competitive_snapshots row carrying all 32 columns.

    Mirrors the schema in ``db.py`` so a schema change here means the trim
    tests fail loudly instead of silently drifting.
    """
    return {
        "id": 42,
        "scraped_at": "2026-04-01T10:00:00Z",
        "site": "UK",
        "category": "Travel Router",
        "brand": "ExampleBrand",
        "model": "XR-100",
        "asin": "B0TESTTEST",
        "title": "ExampleBrand XR-100 Travel Router Pro",
        "price_cents": 14999,
        "currency": "GBP",
        "rating": 4.5,
        "review_count": 123,
        "bought_past_month": 50,
        "bsr": 42,
        "available": 1,
        "url": "https://www.amazon.co.uk/dp/B0TESTTEST",
        "stock_status": "In stock",
        "stock_count": None,
        "sold_by": "ExampleBrand",
        "other_offers": "",
        "coupon": "",
        "is_prime": 1,
        "star_distribution": '{"5":60,"4":25}',
        "image_count": 7,
        "qa_count": 3,
        "fulfillment": "Amazon",
        "price_raw": "£149.99",
        "rating_raw": "4.5 out of 5 stars",
        "review_count_raw": "123 ratings",
        "bsr_raw": "#42 in Electronics",
        "project": "",
        "created_at": "2026-04-01T10:00:01Z",
    }


class TestCompetitiveTrim:
    def test_drops_non_allowlisted_fields(self):
        row = _full_cs_row()
        out = trim_competitive_rows([row])

        assert len(out) == 1
        assert set(out[0].keys()) == LLM_SAFE_COMPETITIVE_FIELDS

    def test_raw_fields_are_removed(self):
        row = _full_cs_row()
        out = trim_competitive_rows([row])[0]

        for key in (
            "id",
            "title",
            "url",
            "stock_status",
            "stock_count",
            "sold_by",
            "other_offers",
            "coupon",
            "is_prime",
            "star_distribution",
            "image_count",
            "qa_count",
            "fulfillment",
            "price_raw",
            "rating_raw",
            "review_count_raw",
            "bsr_raw",
            "project",
            "created_at",
        ):
            assert key not in out, f"{key} leaked into trimmed envelope"

    def test_keeps_decision_fields(self):
        row = _full_cs_row()
        out = trim_competitive_rows([row])[0]

        assert out["brand"] == "ExampleBrand"
        assert out["model"] == "XR-100"
        assert out["asin"] == "B0TESTTEST"
        assert out["price_cents"] == 14999
        assert out["currency"] == "GBP"
        assert out["rating"] == 4.5
        assert out["bsr"] == 42
        assert out["available"] == 1
        assert out["scraped_at"] == "2026-04-01T10:00:00Z"

    def test_immutable_does_not_mutate_input(self):
        row = _full_cs_row()
        before = dict(row)

        _ = trim_competitive_rows([row])

        assert row == before, "trim must not mutate input dict"

    def test_empty_list_returns_empty_list(self):
        assert trim_competitive_rows([]) == []

    def test_missing_keys_do_not_raise(self):
        # Schema drift scenario: DB query omits ``bsr`` entirely.
        row = {
            "site": "UK",
            "brand": "B",
            "model": "M",
            "asin": "B0X",
            # no bsr, no rating, no price_cents
        }
        out = trim_competitive_rows([row])[0]

        assert out == {"site": "UK", "brand": "B", "model": "M", "asin": "B0X"}

    def test_null_values_pass_through(self):
        row = {
            "site": "UK",
            "brand": "B",
            "model": "M",
            "asin": "B0X",
            "bsr": None,
            "rating": None,
            "price_cents": None,
        }
        out = trim_competitive_rows([row])[0]

        assert out["bsr"] is None
        assert out["rating"] is None
        assert out["price_cents"] is None

    def test_unicode_brand_preserved(self):
        row = {
            "site": "JP",
            "brand": "中文品牌",
            "model": "モデル-1",
            "asin": "B0JPXXXX",
        }
        out = trim_competitive_rows([row])[0]

        assert out["brand"] == "中文品牌"
        assert out["model"] == "モデル-1"


class TestTimeseriesTrim:
    def test_drops_keepa_ts_and_fetched_at(self):
        rows = [
            {
                "keepa_ts": 7584000,
                "value": 14999,
                "fetched_at": "2026-04-01T10:00:00Z",
                "date": "2026-04-01 10:00",
            }
        ]
        out = trim_timeseries_rows(rows)

        assert out == [{"date": "2026-04-01 10:00", "value": 14999}]

    def test_large_series_is_linear(self):
        # 730 points (2 years at one-per-day) — must stay O(n) and correct.
        rows = [
            {"keepa_ts": i * 1440, "value": i * 10, "date": f"2026-{i:04d}"} for i in range(730)
        ]

        out = trim_timeseries_rows(rows)

        assert len(out) == 730
        assert set(out[0].keys()) == LLM_SAFE_TIMESERIES_FIELDS
        assert out[-1]["value"] == 7290

    def test_empty_list(self):
        assert trim_timeseries_rows([]) == []


class TestSellerTrim:
    def test_drops_keepa_ts(self):
        rows = [
            {
                "keepa_ts": 7584000,
                "seller_id": "A1EXAMPLE",
                "fetched_at": "2026-04-01T10:00:00Z",
                "date": "2026-04-01 10:00",
            }
        ]
        out = trim_seller_rows(rows)

        assert out == [{"date": "2026-04-01 10:00", "seller_id": "A1EXAMPLE"}]
        assert set(out[0].keys()) == LLM_SAFE_SELLER_FIELDS


class TestDealsTrim:
    def test_drops_access_type_and_fetched_at(self):
        rows = [
            {
                "asin": "B0TEST",
                "site": "UK",
                "start_time": 7584000,
                "end_time": 7590000,
                "deal_type": "LIGHTNING",
                "access_type": "ALL",
                "badge": "Deal of the Day",
                "percent_claimed": 60,
                "deal_status": "ACTIVE",
                "fetched_at": "2026-04-01T10:00:00Z",
            }
        ]
        out = trim_deals_rows(rows)

        assert len(out) == 1
        assert "access_type" not in out[0]
        assert "fetched_at" not in out[0]
        assert set(out[0].keys()) == LLM_SAFE_DEAL_FIELDS
        assert out[0]["deal_type"] == "LIGHTNING"
        assert out[0]["percent_claimed"] == 60


class TestGenericTrim:
    def test_trim_accepts_custom_allowlist(self):
        rows = [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]

        out = trim(rows, allow=frozenset({"a", "b"}))

        assert out == [{"a": 1, "b": 2}, {"a": 4, "b": 5}]

    def test_trim_returns_new_dict_objects(self):
        original = {"a": 1, "b": 2}
        out = trim([original], allow=frozenset({"a"}))

        assert out[0] is not original  # new dict, not a reference
        assert original == {"a": 1, "b": 2}  # source unchanged
