"""Contract test: harness ``_envelope_summary`` vs production ``_build_summary``.

Locks issue #14 (``test_token_audit._envelope_summary`` drift). Any future
edit that reintroduces a hand-rolled summary in the audit harness, or that
changes ``_build_summary`` without running the audit, will flip this test red.

Kept separate from ``tests/test_token_audit.py`` because that module is
``pytestmark = pytest.mark.network`` (skips without ANTHROPIC_API_KEY). This
contract must run on every CI build regardless of network keys, so it lives
behind its own ``pytest.mark.unit`` marker.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_envelope_summary_matches_production_build_summary() -> None:
    """A canonical input must produce identical summary dicts from both
    the audit harness wrapper and production ``_build_summary``.

    Canonical fixture covers:
    - non-empty rows → exercises ``count`` + ``preview`` + ``date_range``
    - a ``date_field`` entry present in rows
    - meta carrying every passthrough key: asin / model / brand /
      series_name / hint / phase / warnings
    - warnings payload that crosses ``MAX_WARNINGS`` and ``MAX_WARNING_CHARS``
      → verifies the harness pipes warnings through production's
      ``_truncate_warnings`` (not raw pass-through, which was the bug).
    """
    from tests.test_token_audit import _envelope_summary
    from webapp.summaries import MAX_WARNING_CHARS, MAX_WARNINGS, _build_summary

    rows = [
        {"date": "2026-04-01", "asin": "B0X", "value": 100},
        {"date": "2026-04-03", "asin": "B0X", "value": 110},
        {"date": "2026-04-05", "asin": "B0X", "value": 105},
    ]
    long_warning = "W" * (MAX_WARNING_CHARS + 50)
    meta = {
        "asin": "B0X",
        "model": "Slate 7",
        "brand": "GL.iNet",
        "series_name": "NEW",
        "hint": "fresh data",
        "phase": "complete",
        "warnings": [long_warning] * (MAX_WARNINGS + 2),
    }

    def _preview(xs: list[dict]) -> list[dict]:
        return [{"date": r["date"], "value": r["value"]} for r in xs]

    harness_envelope = _envelope_summary(
        rows,
        preview_trimmer=_preview,
        date_field="date",
        file_name="audit.xlsx",
        meta=meta,
    )
    prod_summary = _build_summary(
        rows,
        file_name="audit.xlsx",
        meta=meta,
        preview_trimmer=_preview,
        date_field="date",
        truncated=False,
    )

    assert harness_envelope["data"] == prod_summary, (
        "Harness diverged from _build_summary — issue #14 regression. "
        f"harness={harness_envelope['data']!r} prod={prod_summary!r}"
    )
    # Meta carries unchanged — the envelope must not accidentally pipe
    # meta through _truncate_warnings; only summary.warnings is capped.
    assert harness_envelope["meta"] == meta
    assert harness_envelope["ok"] is True
    assert harness_envelope["error"] is None


def test_envelope_summary_empty_rows_matches_production() -> None:
    """Edge case: no rows → no preview / no date_range, count==0."""
    from tests.test_token_audit import _envelope_summary
    from webapp.summaries import _build_summary

    def _no_preview(_: list[dict]) -> list[dict]:
        return []

    harness = _envelope_summary(
        [],
        preview_trimmer=_no_preview,
        date_field="date",
        file_name="empty.xlsx",
        meta={},
    )
    prod = _build_summary(
        [],
        file_name="empty.xlsx",
        meta={},
        preview_trimmer=_no_preview,
        date_field="date",
        truncated=False,
    )
    assert harness["data"] == prod
