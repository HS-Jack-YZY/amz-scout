# Implementation Report: `register_product` TOCTOU race fix

## Summary

Collapsed the racy `SELECT → with conn: INSERT` pattern in
`register_product` (src/amz_scout/db.py) into a single atomic
`INSERT ... ON CONFLICT(brand_key, model_key) DO NOTHING RETURNING id`
statement, with a fallback `SELECT` only on the conflict path. This
closes the TOCTOU window where concurrent webapp requests could both
pass the existence check and race on the INSERT, surfacing
`sqlite3.IntegrityError` instead of the documented `(existing_id, False)`
return contract. Added `TestRegisterProductConcurrency` with two
threading-based tests pinning the race-safe behavior and the
first-writer-wins display-literal contract. Full suite: 298 passed, 8
skipped; concurrency class stable across 10 repetitions.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small |
| Confidence | N/A (plan did not state) | High |
| Files Changed | 2 | 2 |
| Tests Added | 2 | 2 |
| Full Suite | 298 passed | 298 passed, 8 skipped |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Rewrite `register_product` to single-statement UPSERT + fallback SELECT | Complete | Matches `register_asin` UPSERT style exactly |
| 2 | Add `TestRegisterProductConcurrency` class (2 tests) | Complete | Minor cleanup: narrowed result types via `isinstance(r, tuple)` to silence Pyright — see Deviations |
| 3 | Verify existing v7 contract tests still pass | Complete | `TestBrandModelKeyMigrationV7` + `TestQuerySideNormalizationV7` → 10 passed (plan estimated 13; the two classes actually contain 10 tests total) |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff) | Pass | `ruff check src/amz_scout/db.py tests/test_db.py` → clean |
| Unit Tests (concurrency) | Pass | `TestRegisterProductConcurrency` → 2/2 |
| Unit Tests (v7 contract) | Pass | `TestBrandModelKeyMigrationV7` + `TestQuerySideNormalizationV7` → 10/10 |
| Full Suite | Pass | `pytest -q` → 298 passed, 8 skipped |
| Flake Check | Pass | 10/10 consecutive runs of `TestRegisterProductConcurrency` |
| Build | N/A | Pure-Python project, no separate build step |
| Manual Validation | Pass | `cur.lastrowid` no longer in `db.py`; `SELECT id FROM products` appears only in the expected fallback + the pre-existing `find_product_exact` |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/amz_scout/db.py` | UPDATED | +12 / −10 |
| `tests/test_db.py` | UPDATED | +106 / 0 |

Total: 2 files, +118 / −10.

## Deviations from Plan

1. **Type-narrowing rewrite in the concurrency helper.** Plan's test
   skeleton initialized `results: list[tuple[int, bool] | BaseException]`
   with `[None] * len(variants)`, which triggered Pyright
   `reportAssignmentType` (list invariance: `list[None]` is not
   assignable) plus `reportIndexIssue` on `r[0]` / `r[1]` because
   `assert not isinstance(r, BaseException)` does not narrow across
   iteration. **What changed**: expanded the type to
   `list[tuple[int, bool] | BaseException | None]` and replaced the
   `not isinstance(..., BaseException)` assertion with
   `assert isinstance(r, tuple)` + a `[r for r in results if isinstance(r, tuple)]`
   comprehension that Pyright *does* narrow. **Why**: the plan's code was
   functionally correct but produced editor/LSP diagnostic noise;
   project has no Pyright config so this was not a hard gate, but the
   clean narrowing is both type-safe and more readable. No behavioral
   change — both tests still assert no exceptions escape and all
   workers converge on one id.

## Issues Encountered

None beyond the Pyright narrowing note above. The atomic UPSERT worked
first try; both concurrency tests passed on the first run and remained
stable across 10 repetitions.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_db.py` | `test_concurrent_same_key_no_integrity_error` | 8 threads, identical (brand, model); verifies exactly one `is_new=True`, 7× `is_new=False`, single shared `product_id`, zero exceptions |
| `tests/test_db.py` | `test_concurrent_variant_literals_display_is_first_writer` | 4 threads, different case/whitespace variants; verifies single canonical id, stored display literal ∈ input set, `brand_key` / `model_key` normalized |

## Acceptance Criteria

- [x] `register_product` returns `(existing_id, False)` on conflict with zero exceptions (verified by `test_concurrent_same_key_no_integrity_error`).
- [x] Concurrency tests pass 10/10 consecutive iterations.
- [x] Existing display-literal preservation tests still pass (`test_v7_register_product_preserves_display`).
- [x] Full test suite at 298 passed.
- [x] `ruff check` clean.

## Next Steps

- [ ] Code review via `/code-review` (optional — changes are small and
      mirror an existing pattern).
- [ ] Create PR via `/prp-pr` — follow-up to PR #15 code-review finding.
- [ ] Monitor webapp error rates post-deploy; pre-fix the race would
      surface as sporadic 500s during concurrent Keepa-fetch bursts.
