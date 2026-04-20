# Local Review: Fix `_envelope_summary` Drift (issue #14)

**Reviewed**: 2026-04-20
**Branch**: `fix/token-audit-envelope-drift`
**Mode**: Local uncommitted review
**Decision**: APPROVE

## Summary

Two-file **code** change (1 update + 1 new test file) plus two PRP
documentation artifacts. Replaces the hand-rolled `_envelope_summary`
helper with a thin wrapper over `webapp.summaries._build_summary`, and
adds a unit-marked contract test pinning harness ↔ production
equivalence. Zero production changes. All validation green.

## Files Reviewed

| File | Change | LOC |
|---|---|---|
| `tests/test_token_audit.py` | Modified | +26 / -18 (net +8) |
| `tests/test_summaries_contract.py` | Added | +106 |
| `.claude/PRPs/reports/fix-token-audit-envelope-summary-drift-report.md` | Added | Non-code artifact |
| `.claude/PRPs/plans/completed/fix-token-audit-envelope-summary-drift.plan.md` | Moved | Non-code artifact |

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM
None.

### LOW

1. **`tests/test_token_audit.py:69` — `preview_trimmer` has no type annotation**
   - Ideal: `Callable[[list[dict]], list[dict]] | None` (matches `_build_summary`).
   - Current: pre-existing style in the file; introducing types here would
     expand diff beyond plan scope ("NOT Building" forbids unrelated cleanups).
   - Recommendation: leave as-is; handle in a separate typing-pass PR.

## Behavior Change (Intentional)

The rewritten `_envelope_summary` now pipes `meta["warnings"]` through
production's `_truncate_warnings` (MAX_WARNINGS=3, MAX_WARNING_CHARS=200).
The prior hand-rolled helper transferred `meta["warnings"]` raw. This is the
drift that #14 targets — harness now measures the same cap production applies,
removing the "optimistic" bias on failure-path token counts.

**Forward compatibility**: if an `ANTHROPIC_API_KEY` + DB-equipped environment
runs `pytest tests/test_token_audit.py` and observes `test_query_trends_token_delta`
dipping below its 60% / 30% thresholds, that reveals a real product concern
(#14's stated hypothesis) — it must be triaged separately, not masked here.

## Validation Results

| Check | Result |
|---|---|
| Ruff (`tests/test_token_audit.py tests/test_summaries_contract.py`) | Pass |
| Import smoke (`from tests.test_token_audit import _envelope_summary; from webapp.summaries import _build_summary`) | Pass |
| Contract tests (`tests/test_summaries_contract.py`) | 2 passed |
| Full non-network suite (`pytest -m "not network"`) | 296 passed / 2 skipped / 8 deselected / 0 failures |
| Production diff (`git diff webapp/`) | Empty (plan's NOT Building honored) |

## Decision Rationale

- Zero CRITICAL / HIGH issues.
- All static + unit validation passes.
- Implementation faithfully follows the plan with one documented Pyright-driven
  deviation (lambda → named helper).
- Change surface is minimal and fully scoped to the test layer.

**APPROVE**. No required fixes. Proceed to commit + PR.
