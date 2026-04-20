"""Contract test: harness ``_envelope_summary`` vs production ``_build_summary``.

Locks issue #14 (``test_token_audit._envelope_summary`` drift). The current
harness is a thin delegate over ``_build_summary``, so ``_build_summary``
behavior changes propagate to both sides and this test stays green by
design — that is the intended guarantee. What this test *does* flip red on:

- Someone reintroduces a hand-rolled summary in the audit harness
  (the original bug this closes).
- Someone changes the wrapper's arguments in a way that diverges from
  production (e.g., defaulting ``truncated=True``, dropping
  ``preview_trimmer``, passing a different ``meta`` shape).
- Someone changes the envelope shell (``ok`` / ``error`` / ``meta``) so
  that the audit no longer mirrors the production envelope wrapper.
- Someone introduces caller-``meta`` mutation in either ``_envelope_summary``
  or ``_build_summary`` (the canonical test deep-copies meta before the
  call and compares afterward).

Kept separate from ``tests/test_token_audit.py`` because that module is
``pytestmark = pytest.mark.network`` (skips without ANTHROPIC_API_KEY). This
contract must run on every CI build regardless of network keys, so it lives
behind its own ``pytest.mark.unit`` marker.
"""

from __future__ import annotations

import copy

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

    # Deep-copy the caller-visible meta *before* invoking either side, so we
    # can later detect in-place mutation. Comparing `harness_envelope["meta"]`
    # to `meta` alone would be a tautology (same object), and would silently
    # pass even if a future edit to `_build_summary` mutated its input.
    original_meta = copy.deepcopy(meta)

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
    # Envelope passes meta through by reference — it must be the *same*
    # object the caller handed in, not a copy (the wrapper's documented
    # behavior; copies would silently double memory on large meta).
    assert harness_envelope["meta"] is meta
    # Neither _envelope_summary nor _build_summary may mutate the caller's
    # meta. `warnings` is the field most at risk because _truncate_warnings
    # would be tempting to apply in-place; this assertion locks that it is
    # NOT mutated on the input dict (only reflected in summary.warnings).
    assert meta == original_meta, (
        f"meta was mutated in-place. original={original_meta!r} after={meta!r}"
    )
    assert harness_envelope["ok"] is True
    assert harness_envelope["error"] is None


def test_envelope_summary_empty_rows_matches_production() -> None:
    """Edge case: no rows → no preview / no date_range, count==0.

    Also locks the meta-by-reference contract for the empty-dict case —
    historically the wrapper used ``meta or {}`` which silently replaced an
    empty caller dict with a new object, breaking the reference passthrough
    the canonical test asserts on truthy meta. Using a single shared
    ``meta_empty`` object across both calls lets us assert the same
    invariants uniformly.
    """
    from tests.test_token_audit import _envelope_summary
    from webapp.summaries import _build_summary

    def _no_preview(_: list[dict]) -> list[dict]:
        return []

    meta_empty: dict = {}
    original_meta = copy.deepcopy(meta_empty)

    harness = _envelope_summary(
        [],
        preview_trimmer=_no_preview,
        date_field="date",
        file_name="empty.xlsx",
        meta=meta_empty,
    )
    prod = _build_summary(
        [],
        file_name="empty.xlsx",
        meta=meta_empty,
        preview_trimmer=_no_preview,
        date_field="date",
        truncated=False,
    )
    assert harness["data"] == prod
    # Reference passthrough must hold for empty meta as well.
    assert harness["meta"] is meta_empty
    assert meta_empty == original_meta
