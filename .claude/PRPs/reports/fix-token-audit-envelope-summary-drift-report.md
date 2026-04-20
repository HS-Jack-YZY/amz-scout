# Implementation Report: Fix `_envelope_summary` Drift from Production `_build_summary`

## Summary

Replaced the hand-rolled `_envelope_summary` helper in `tests/test_token_audit.py`
with a thin wrapper over `webapp.summaries._build_summary`, and added a new
unit-marked contract test (`tests/test_summaries_contract.py`) that locks
harness ↔ production equivalence on every CI build. Closes #14.

Zero production changes: `git diff webapp/` is empty.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small |
| Confidence | (high — no external research needed) | Matched |
| Files Changed | 1 UPDATED + 1 CREATED | 1 UPDATED (`tests/test_token_audit.py`) + 1 CREATED (`tests/test_summaries_contract.py`) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Rewire `_envelope_summary` to call `_build_summary` | Complete | Delegates to production; preserves outer signature; pins `truncated=False` for success-path measurement |
| 2 | Update module docstring | Complete | Removed stale "mirrors field-for-field" implication; added pointer to contract test |
| 3 | Create contract test file | Complete | 2 tests, `@pytest.mark.unit`; covers canonical input (warnings truncation) + empty rows edge case |
| 4 | Validation suite | Complete | ruff clean, 2 new passed, `-m "not network"` full suite green (296/2/8) |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff) | Pass | `ruff check tests/test_token_audit.py tests/test_summaries_contract.py` → No issues found |
| Import Smoke | Pass | `from tests.test_token_audit import _envelope_summary; from webapp.summaries import _build_summary` succeeds |
| Unit Tests (new file) | Pass | 2 passed, 0 failed |
| Full Suite (`-m "not network"`) | Pass | 296 passed, 2 skipped (pre-existing), 8 deselected (network-only token audit tests), 0 failures |
| Network Tier Token Audit | Not run (no `ANTHROPIC_API_KEY` in this env) | Harness signature unchanged; 5 callers in `test_query_*_token_delta` untouched |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `tests/test_token_audit.py` | UPDATED | +26 / -18 (net +8): docstring refresh + `_envelope_summary` body swap |
| `tests/test_summaries_contract.py` | CREATED | +106 (new contract test, 2 test functions) |

## Deviations from Plan

- **Empty-rows test `preview_trimmer`**: Plan used `lambda _x: []`; Pyright flagged `_x` as unused parameter. Replaced with a named `_no_preview(_: list[dict]) -> list[dict]` helper — same semantics, no lint noise, still matches plan intent.

## Issues Encountered

None. One Pyright warning (unused lambda param) surfaced during write and was
resolved immediately with a named helper.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_summaries_contract.py` | 2 tests | canonical path (count + preview + date_range + all meta passthroughs + warnings truncation) and empty-rows edge case |

## Next Steps

- [ ] Code review via `/code-review`
- [ ] Commit with message referencing issue #14
- [ ] Create PR with `closes #14` (confirmed this PR fully resolves the issue)
- [ ] (Optional, if local env has `ANTHROPIC_API_KEY` + `output/amz_scout.db`): rerun
      `pytest tests/test_token_audit.py -q` to regenerate `output/token_audit.json`
      baseline. Any `pct_saved_vs_raw` movement on `query_trends` should be recorded
      in PR body; if the 60%/30% gates fail, that is the legitimate product of #14's
      "optimistic measurement" and must be followed up separately — **not** masked
      in this PR.
