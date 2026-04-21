# Local Review: fix/transient-failure-permanent-dead-listing (issue #11)

**Reviewed**: 2026-04-21
**Branch**: fix/transient-failure-permanent-dead-listing
**Scope**: 5 files, +485/-35
**Decision**: **APPROVE** (with MEDIUM/LOW advisory notes)

## Summary

Replaces the "1 empty fetch → permanent `not_listed`" blacklisting rule
with a strike-counter guard: transient Keepa failures (non-empty
`PriceHistory.fetch_error`) leave status untouched; genuine empty
responses (`fetch_error == ""`) increment `product_asins.not_listed_strikes`,
and only after `NOT_LISTED_STRIKE_THRESHOLD` (=3) consecutive eligible
observations does the row flip to `not_listed`. Any fetch with data
resets the counter. Manual `not_listed → active` recovery path now has
explicit test coverage.

Implementation matches the plan faithfully; deviations (monkey-patch
target, ASIN regex width, `_SCHEMA_SQL` seed insert, test_db
scaffolding edits) are all legitimate and surfaced in the report.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM

1. **Misleading warning text for already-`not_listed` rows**
   - File: `src/amz_scout/api.py:1004-1011`
   - Severity: MEDIUM (UX / observability)
   - When `ensure_keepa_data` re-fetches an already-`not_listed`
     ASIN and the response is still genuine empty,
     `_record_empty_observation` increments strikes and returns
     `(strikes, flipped=False)`. Because `flipped` is False and
     `strikes > 0`, the code emits:
     > "Empty Keepa response (strike 4/3); status unchanged. Will mark
     > not_listed after consecutive threshold."
   - Reads as semantically wrong: the ratio "4/3" is nonsensical,
     and the "will mark not_listed" promise is vacuous because the
     row is already `not_listed`.
   - Suggested fix: carry back the `status` from
     `_record_empty_observation` (change return to
     `(strikes, flipped, was_active_on_entry)` or have caller
     re-read) so the warning branch can distinguish:
     - `status == 'active'` + `strikes < THRESHOLD` → current
       "strike N/3" progression text
     - `status == 'not_listed'` → "Still observed as not_listed
       (observations: N)" or skip entirely
   - Alternatively, accept as-is and add an explicit inline comment
     that the ratio is intentionally unbounded for observational
     rows. The plan's Edge Cases checklist covers the *behavior* but
     not the *warning copy*, so either choice is defensible.

### LOW

1. **`price_history=None` on `source="fetched"` is treated as genuine empty**
   - File: `src/amz_scout/api.py:969-975`
   - `keepa_service.py:173` uses `history_map.get(pf.asin)`, which
     returns `None` if the scraper didn't yield a record for that
     ASIN. The new code short-circuits
     `fetch_error = ph.fetch_error if ph else ""`, so a `None`
     price_history is classified as **genuine empty** and increments
     strikes.
   - In practice the scraper always populates via `_empty_history()`,
     so the `None` path is theoretical — but the plan's Edge Cases
     checklist flags this as a "defensive guard" without a clear
     policy. Preserving prior behavior here is OK; a later follow-up
     could reclassify `price_history is None` as transient (since
     it means the scraper never got a response for that ASIN).

2. **Unbounded `not_listed_strikes` growth on already-delisted rows**
   - File: `src/amz_scout/api.py:414-446`
   - Counter has no upper cap; rows re-fetched for months will grow
     to 3-digit values. Documented in the docstring as an
     "observational log". No correctness impact (SQLite INTEGER holds
     2^63), but can be mildly confusing during manual DB inspection.
   - If the follow-up to use `keepa_products.availability_amazon == -1`
     to lower N lands later, consider capping strikes at some value
     or resetting when flipping to not_listed.

### Not a finding, worth noting

- **Migration ordering** — v8 is inside the main `with conn:` txn,
  while v7 runs afterward outside the txn (PRAGMA foreign_keys
  requirement). This inverts version order temporally (v8 before v7
  by wall-clock), but `_migrate_to_v7` only rebuilds the `products`
  table and does NOT touch `product_asins`, so the `not_listed_strikes`
  column added in v8 is preserved. Verified with the synthetic v7→v8
  legacy migration script — passes.

- **SQL construction in `ensure_keepa_data`** — `conds` f-string only
  concatenates fixed `(asin = ? AND site = ?)` fragments and fully
  parameterizes user values. No injection risk. Pre-existing pattern.

- **Concurrency** — `_record_empty_observation` does a SELECT, then
  calls `increment_not_listed_strikes` (its own `with conn:`), then
  conditionally `update_asin_status` (its own `with conn:`). SQLite's
  file lock serializes writes; the worst-case race is a stale-status
  read between SELECT and UPDATE which results in a possible missed
  flip that self-corrects on the next call. Acceptable.

## Validation Results

| Check | Result | Notes |
|---|---|---|
| `ruff check src/ tests/` | PASS | all checks passed |
| `import amz_scout.api, amz_scout.db` | PASS | no circular-import regression |
| Fresh DB schema v8 validation | PASS | `not_listed_strikes` present, MAX(version)=8 |
| v7 → v8 migration on synthetic legacy DB | PASS | column added by ALTER TABLE |
| `pytest tests/test_api.py tests/test_db.py` | PASS | 154/154 |
| `pytest` full suite | PASS | 312 passed, 8 skipped, 0 failures |
| `pytest -k "Strike or Recovery or Transient or Genuine"` | PASS | 8/8 new tests |

## Files Reviewed

| File | Change | Notes |
|---|---|---|
| `src/amz_scout/db.py` | Modified | v8 migration, baseline column + seed, two strike helpers |
| `src/amz_scout/api.py` | Modified | Split `_try_mark_not_listed` → two observation helpers; three-branch post-fetch loop |
| `tests/test_api.py` | Modified | +233 lines, 8 new tests across 3 classes |
| `tests/test_db.py` | Modified | Version bump + scaffolding adapted (`WHERE version >= N`, explicit column list) |
| `docs/DEVELOPER.md` | Modified | v8 migrations row, Transient-Failure Guard section, updated state diagram |

Untracked (non-source):
- `.claude/PRPs/plans/completed/fix-transient-failure-permanent-dead-listing.plan.md`
- `.claude/PRPs/reports/fix-transient-failure-permanent-dead-listing-report.md`

## Decision Rationale

Zero CRITICAL / HIGH issues. All validation passes. The MEDIUM finding
is pure UX polish on warning text for a narrow edge case; can ship as
an incremental improvement or be deferred. LOW findings are pre-existing
behavior preserved by this PR and call for follow-up items, not
blockers.

## Suggested Next Steps

- (Optional) Tighten the warning copy for already-`not_listed` rows
  per the MEDIUM finding (one-call-site change in `api.py`).
- Proceed with `/prp-pr` — the plan's note about using `Closes #11` in
  the body is correct (per `feedback_github_close_keywords` memory the
  keyword is safe here because this PR does close the issue).
- File follow-up issues for the two LOW items if the team wants to
  track them.
