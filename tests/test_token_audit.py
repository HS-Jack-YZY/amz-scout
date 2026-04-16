"""Measurement harness: before/after token-count audit for trimmed tool envelopes.

Writes ``output/token_audit.json`` with one row per trimmed query function,
reporting ``before`` / ``after`` / ``pct_saved`` against the real Anthropic
``count_tokens`` endpoint.

Skipped unless ``ANTHROPIC_API_KEY`` is set AND ``output/amz_scout.db`` exists.
The harness is marked ``@pytest.mark.network`` so CI (which does not export the
key) cleanly skips it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def anthropic_client() -> Any:
    """Real Anthropic client. Skips if no key — ``count_tokens`` still requires
    a valid HTTP authentication, even though the endpoint itself is free."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; skipping token audit")
    anthropic = pytest.importorskip("anthropic")
    return anthropic.Anthropic()


@pytest.fixture(scope="module")
def real_db() -> Path:
    """The shared production SQLite DB. Skip cleanly if not present."""
    db = Path("output/amz_scout.db")
    if not db.exists():
        pytest.skip(f"Production DB not found at {db}")
    return db


@pytest.fixture(scope="module")
def audit_path() -> Path:
    out = Path("output/token_audit.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _envelope_untrimmed(data: list[dict], meta: dict | None = None) -> dict:
    """Wrap raw DB rows in an envelope matching the public shape but with no trim.

    When *meta* is provided the before-envelope carries the same metadata as the
    after-envelope so the token delta isolates the ``data`` field difference only.
    """
    return {"ok": True, "data": data, "error": None, "meta": meta if meta is not None else {}}


def _count_tokens_for_tool_result(client: Any, payload: dict) -> int:
    """Ask Anthropic how many input tokens a given ``tool_result`` payload costs.

    Mirrors the webapp's wire shape: user question → assistant tool_use →
    user tool_result. The Anthropic API requires every ``tool_result`` to
    have a matching ``tool_use`` in the previous assistant turn, so we
    fabricate a minimal tool_use block carrying the same id.

    The *delta* between before-trim and after-trim counts is dominated by
    the tool_result content, which is exactly what we want to measure. The
    harness scaffolding adds a constant offset that cancels out.
    """
    tool_use_id = "toolu_audit"
    msg = [
        {
            "role": "user",
            "content": "Run audit query.",
        },
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "audit_tool",
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
        },
    ]
    result = client.messages.count_tokens(
        model="claude-sonnet-4-6",
        system="You are a token audit harness.",
        messages=msg,
    )
    return int(result.input_tokens)


def _record(audit_path: Path, entry: dict) -> None:
    """Merge one audit row into ``output/token_audit.json`` (upsert by tool name)."""
    existing: list[dict] = []
    if audit_path.exists():
        try:
            existing = json.loads(audit_path.read_text())
        except json.JSONDecodeError:
            existing = []
    existing = [row for row in existing if row.get("tool") != entry["tool"]]
    existing.append(entry)
    audit_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


def _assert_nonregressive(tool: str, before: int, after: int) -> dict:
    """Trim must never *increase* tokens. pct_saved may be 0 for tiny payloads."""
    assert after <= before, f"{tool}: trim increased tokens ({before} -> {after})"
    pct = 0.0 if before == 0 else round((before - after) / before * 100, 1)
    return {"tool": tool, "before": before, "after": after, "pct_saved": pct}


# ─── Per-tool audits ─────────────────────────────────────────────────


def test_query_latest_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(real_db.parent.parent)
    from amz_scout import api as amz_api
    from amz_scout.db import open_db
    from amz_scout.db import query_latest as db_query_latest

    with open_db(real_db) as conn:
        raw = db_query_latest(conn, site="UK", category=None)
    if not raw:
        pytest.skip("competitive_snapshots has no UK rows — trim measurement is meaningless")
    after_env = amz_api.query_latest(marketplace="UK")
    before_env = _envelope_untrimmed(raw, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_latest", before, after)
    _record(audit_path, entry)


def test_query_ranking_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(real_db.parent.parent)
    from amz_scout import api as amz_api
    from amz_scout.db import open_db, query_bsr_ranking

    with open_db(real_db) as conn:
        raw = query_bsr_ranking(conn, site="UK", category=None)
    if not raw:
        pytest.skip("no BSR ranking rows for UK — trim measurement is meaningless")
    after_env = amz_api.query_ranking(marketplace="UK")
    before_env = _envelope_untrimmed(raw, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_ranking", before, after)
    _record(audit_path, entry)


def test_query_availability_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(real_db.parent.parent)
    from amz_scout import api as amz_api
    from amz_scout.db import open_db
    from amz_scout.db import query_availability as db_query_availability

    with open_db(real_db) as conn:
        raw = db_query_availability(conn)
    if not raw:
        pytest.skip("no availability rows — trim measurement is meaningless")
    after_env = amz_api.query_availability()
    before_env = _envelope_untrimmed(raw, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_availability", before, after)
    _record(audit_path, entry)


def test_query_compare_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pick any model present in the live DB; skip if the table is empty."""
    monkeypatch.chdir(real_db.parent.parent)
    from amz_scout import api as amz_api
    from amz_scout.db import open_db, query_cross_market

    with open_db(real_db) as conn:
        row = conn.execute("SELECT model FROM competitive_snapshots LIMIT 1").fetchone()
    if not row:
        pytest.skip("competitive_snapshots is empty — cannot audit query_compare")
    model = row["model"]

    after_env = amz_api.query_compare(product=model)
    with open_db(real_db) as conn:
        raw = query_cross_market(conn, model)
    before_env = _envelope_untrimmed(raw, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_compare", before, after)
    _record(audit_path, entry)


def test_query_trends_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compare direct DB read vs. trimmed envelope using a known ASIN in DB."""
    monkeypatch.chdir(real_db.parent.parent)
    from datetime import timedelta

    from amz_scout import api as amz_api
    from amz_scout.api import KEEPA_EPOCH, SERIES_MAP
    from amz_scout.db import SERIES_NEW, open_db, query_price_trends

    with open_db(real_db) as conn:
        row = conn.execute(
            "SELECT asin, site FROM keepa_time_series "
            "WHERE series_type = ? ORDER BY keepa_ts DESC LIMIT 1",
            (SERIES_NEW,),
        ).fetchone()
    if not row:
        pytest.skip("keepa_time_series has no NEW series rows — cannot audit query_trends")
    asin, site = row["asin"], row["site"]

    after_env = amz_api.query_trends(
        product=asin, marketplace=site, series="new", days=90, auto_fetch=False
    )

    # Build an equivalent untrimmed envelope by hand: same _add_dates transform,
    # no field trimming.
    with open_db(real_db) as conn:
        raw = query_price_trends(conn, asin, site, SERIES_MAP["new"], days=90)
    dated = [
        {
            **r,
            "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime("%Y-%m-%d %H:%M"),
        }
        for r in raw
    ]
    before_env = _envelope_untrimmed(dated, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_trends", before, after)
    _record(audit_path, entry)


def test_query_sellers_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(real_db.parent.parent)
    from datetime import timedelta

    from amz_scout import api as amz_api
    from amz_scout.api import KEEPA_EPOCH
    from amz_scout.db import open_db, query_seller_history

    with open_db(real_db) as conn:
        row = conn.execute("SELECT asin, site FROM keepa_buybox_history LIMIT 1").fetchone()
    if not row:
        pytest.skip("keepa_buybox_history is empty — cannot audit query_sellers")
    asin, site = row["asin"], row["site"]

    after_env = amz_api.query_sellers(product=asin, marketplace=site, auto_fetch=False)

    with open_db(real_db) as conn:
        raw = query_seller_history(conn, asin, site)
    dated = [
        {
            **r,
            "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime("%Y-%m-%d %H:%M"),
        }
        for r in raw
    ]
    before_env = _envelope_untrimmed(dated, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_sellers", before, after)
    _record(audit_path, entry)


def test_query_deals_token_delta(
    anthropic_client: Any, real_db: Path, audit_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(real_db.parent.parent)
    from amz_scout import api as amz_api
    from amz_scout.db import open_db, query_deals_history

    with open_db(real_db) as conn:
        raw = query_deals_history(conn, site="UK")
    if not raw:
        pytest.skip("no deals rows for UK — trim measurement is meaningless")
    after_env = amz_api.query_deals(marketplace="UK", auto_fetch=False)
    before_env = _envelope_untrimmed(raw, meta=after_env.get("meta", {}))

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_deals", before, after)
    _record(audit_path, entry)


# ─── Synthetic fallback for competitive_snapshots (empty in live DB) ──


def _synthetic_cs_row(i: int) -> dict:
    """One competitive_snapshots row. Synthetic values — not from production."""
    return {
        "id": i,
        "scraped_at": "2026-04-01T10:00:00Z",
        "site": "UK",
        "category": "Travel Router",
        "brand": f"Brand{i % 3}",
        "model": f"Model-XR-{i}",
        "asin": f"B0SYN{i:05d}",
        "title": f"Brand{i % 3} Model-XR-{i} Travel Router Professional Edition with Extras",
        "price_cents": 14999 + i * 100,
        "currency": "GBP",
        "rating": 4.5,
        "review_count": 100 + i,
        "bought_past_month": 50,
        "bsr": 100 + i,
        "available": 1,
        "url": f"https://www.amazon.co.uk/dp/B0SYN{i:05d}",
        "stock_status": "In stock",
        "stock_count": None,
        "sold_by": "ExampleRetailer",
        "other_offers": "",
        "coupon": "",
        "is_prime": 1,
        "star_distribution": '{"5":60,"4":25,"3":10,"2":3,"1":2}',
        "image_count": 7,
        "qa_count": 3,
        "fulfillment": "Amazon",
        "price_raw": f"£{149.99 + i}",
        "rating_raw": "4.5 out of 5 stars",
        "review_count_raw": f"{100 + i} ratings",
        "bsr_raw": f"#{100 + i} in Electronics & Photo",
        "project": "",
        "created_at": "2026-04-01T10:00:01Z",
    }


def test_query_latest_synthetic_token_delta(anthropic_client: Any, audit_path: Path) -> None:
    """Synthetic 20-row competitive_snapshots payload — proves the trim wins
    for the ``SELECT cs.*`` query shape even when the live DB table is empty."""
    from amz_scout._llm_trim import trim_competitive_rows

    raw = [_synthetic_cs_row(i) for i in range(20)]
    before_env = _envelope_untrimmed(raw)
    after_env = {
        "ok": True,
        "data": trim_competitive_rows(raw),
        "error": None,
        "meta": {"count": 20},
    }

    before = _count_tokens_for_tool_result(anthropic_client, before_env)
    after = _count_tokens_for_tool_result(anthropic_client, after_env)
    entry = _assert_nonregressive("query_latest_synth20", before, after)
    _record(audit_path, entry)
