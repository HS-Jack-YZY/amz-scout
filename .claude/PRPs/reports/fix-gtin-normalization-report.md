# Implementation Report: Fix GTIN UPC-12 / EAN-13 Normalization (issue #12)

## Summary

Fixed the cross-format GTIN miss in `_find_product_by_ean` /
`_upsert_keepa_product` by normalizing every EAN/UPC code to canonical
GTIN-13 (digits-only, left-zero-padded to 13) on both the read path and
the write path, and by backfilling existing `keepa_products.ean_list` /
`upc_list` JSON arrays via a v8 migration. US ASINs whose Keepa `upcList`
carries UPC-12 (`"850018166010"`) now correctly bind to EU ASINs whose
`eanList` carries the same GTIN as EAN-13 (`"0850018166010"`).

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small |
| Files Changed | 3 (db.py, test_db.py, optional DEVELOPER.md) | 3 (db.py, test_db.py, DEVELOPER.md — added during code-review follow-up) |

`docs/DEVELOPER.md` was initially left alone; the code-review follow-up
added a v8 row to the Migration History table and a new
"GTIN Canonicalization (v8+)" subsection (see follow-up section below).

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Add `_normalize_gtin` + `_normalize_gtin_list` helpers | Complete | |
| 2 | Apply normalization in `_find_product_by_ean` read path | Complete | |
| 3 | Apply normalization in write path | Complete | Plan named `store_keepa_product`; the `INSERT` lives in `_upsert_keepa_product` (called by `store_keepa_product`). Edit applied there; semantically identical. |
| 4 | Add v8 migration (SCHEMA_VERSION=8) | Complete | Also added v8 seed row to `_SCHEMA_SQL` so fresh DBs start at v8 without triggering the migration path. |
| 5 | `TestNormalizeGtin` unit tests (7 tests) | Complete | |
| 6 | `TestFindProductByEanGtinNormalization` (3 cross-format tests) | Complete | Deviation — see below. |
| 7 | Write-path round-trip test (1 test) | Complete | Added as the fourth test in `TestFindProductByEanGtinNormalization`. |
| 8 | `TestGtinBackfillMigrationV8` (3 tests) | Complete | |
| 9 | `ruff` + full `pytest` | Complete | 318 passed, 8 skipped, 1 pre-existing warning. |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `ruff check src/ tests/` → All checks passed |
| Unit Tests | Pass | 14 new tests + 47 existing `test_db.py` tests green |
| Build | Pass | `import amz_scout.db` succeeds; `SCHEMA_VERSION == 8` |
| Integration | N/A | Pure DB logic; no HTTP server involved |
| Edge Cases | Pass | None/empty/garbage/hyphens/over-13/short-codes all covered |

Full suite: **318 passed, 8 skipped** in ~37s.

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/amz_scout/db.py` | UPDATED | +92 / ~0 deletions |
| `tests/test_db.py` | UPDATED | +334 / 17 edits (version bumps in 2 migration tests) |

## Deviations from Plan

1. **Write-path edit target**: Plan named `store_keepa_product` for the
   write-path change, but `INSERT OR REPLACE INTO keepa_products` lives
   in `_upsert_keepa_product` (called by `store_keepa_product`). Edit
   applied there; same effect.

2. **Fresh-DB seed for v8**: Added
   `INSERT INTO schema_migrations VALUES (8, 'canonicalize ean_list/upc_list to GTIN-13')`
   to `_SCHEMA_SQL` (parallel to the 1-7 entries already there). Keeps
   fresh DBs from running the v8 no-op migration on first open.

3. **Test auto-register pattern**: Three cross-format tests in the plan
   called `register_product` **then** `store_keepa_product`. The latter
   auto-registers a second product when the raw lacks a `model` field
   (falls back to ASIN as model), causing EAN-ambiguity on lookup.
   Rewrote to drop the manual `register_product` pre-call and pass
   `"model": "Model X"` in the raw dict. Auto-register then binds
   cleanly. Closer to production behavior.

4. **Downgrade-to-vN tests**: `test_v6_migrates_legacy_statuses_to_active`
   and `test_v7_migrates_v6_db_and_merges_duplicates` needed their
   `DELETE FROM schema_migrations WHERE version = 7` clauses broadened
   to `version IN (7, 8)` because `_SCHEMA_SQL` now seeds v8 too. The
   same tests had hardcoded `assert ver == 7` that became `assert ver == 8`.
   Test-infra maintenance only; no change to what is validated.

## Issues Encountered

- **Pre-existing migration tests coupled to seed-version list**: fixed
  via the downgrade-clause update above. Latent fragility of the seed
  pattern — every new `SCHEMA_VERSION` bump will need the same test
  adjustment. Out-of-scope for this fix; could be worth a follow-up to
  refactor those tests to delete all `version > N` rows dynamically.

## Tests Written

| Test Class | Tests | Coverage |
|---|---|---|
| `TestNormalizeGtin` | 7 | Scalar + list helpers, None/empty/garbage/hyphens/zero-padding/GTIN-14 drop |
| `TestFindProductByEanGtinNormalization` | 4 | Cross-format bind (issue #12 headline), legacy row via manual backfill, genuinely different GTIN rejection, write-path round-trip |
| `TestGtinBackfillMigrationV8` | 3 | Forced-downgrade backfill, idempotent re-run, no-op guard on canonical rows |

## Code Review Follow-up (2026-04-21)

Addressed findings from `/code-review` on the local diff before PR
creation.

### Findings fixed

| Severity | ID | Finding | Fix |
|---|---|---|---|
| HIGH | H1 | v7/v8 migration partial-failure trap: v7 runs outside the inner txn, v8 inside. On a pre-v7 DB, v8 commits first; if v7 then raises, retry sees `current = MAX(version) = 8` and the old `current < 7` gate plus the top-level `if current >= SCHEMA_VERSION: return` short-circuit skip v7 forever. | `_migrate` now computes `v7_applied` from an actual `schema_migrations` record lookup at entry and wires it into both the early-return guard and the v7 gate. Both short-circuits now require v7 to really be applied. |
| MEDIUM | M1 | `_normalize_gtin_list` missing parameter type annotation, violating the project's Python rule "type annotations on all function signatures". | Added `codes: Iterable[str | None] | None`. Added `from collections.abc import Iterable` import. |
| MEDIUM | M2 | Silent read-path semantic change: callers of `get_keepa_product`-style reads now receive canonical GTIN-13 instead of raw Keepa strings; no code consumer breaks (`sync_registry_from_keepa` re-normalizes via `_find_product_by_ean`; ad-hoc `LIKE '%<12-digit>%'` SQL still matches the GTIN-13 form because UPC-12 is a substring of "0"+UPC-12), but the behavior needed to be documented. | `docs/DEVELOPER.md`: added v8 row to Migration History, a "Partial-failure note (v7 ↔ v8 ordering)" callout, and a "GTIN Canonicalization (v8+)" subsection parallel to the v7 one. |

LOW findings (`fetchall()` memory, debug-level log for GTIN-14,
scattered inline test imports, `caplog.records[].message` vs
`.getMessage()`) left as-is — judgment calls, not blocking.

### New regression test

`TestV7RetryAfterV8Commit::test_v7_reruns_when_only_v8_committed` seeds
a DB in the exact partial-failure shape (v8 record present, v7 record
deleted, `products` back on pre-v7 schema) and asserts that reopening
via `open_db` re-runs the v7 migration. This test fails under the
original `current < 7` gate (because `MAX(version) = 8` short-circuits
the whole `_migrate` function) and passes after the two-part fix, so it
guards against both the original `current < 7` reintroduction and any
future regression of the top-level early-return guard.

### Files changed in follow-up

| File | Action | Notes |
|---|---|---|
| `src/amz_scout/db.py` | UPDATED | H1 + M1 — 2 touchpoints (`_migrate` gate logic, `_normalize_gtin_list` signature + `Iterable` import) |
| `tests/test_db.py` | UPDATED | Added `TestV7RetryAfterV8Commit` (1 test) |
| `docs/DEVELOPER.md` | ADDED | M2 — v8 row in Migration History table, partial-failure note, "GTIN Canonicalization (v8+)" subsection |

### Validation (post-follow-up)

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `ruff check src/ tests/` → All checks passed |
| Unit Tests | Pass | **319 passed, 8 skipped** (was 318 + 1 new H1 regression) |
| New test isolation | Pass | `test_v7_reruns_when_only_v8_committed` green; fails without the `_migrate` early-return fix, confirming the regression is locked in |

## Next Steps

- [x] Code review via `/code-review` (completed 2026-04-21 with H1/M1/M2 fixes above)
- [ ] Create PR via `/prp-pr` (suggested title: `fix: normalize GTIN UPC-12 / EAN-13 on read+write (closes #12)`)
- [ ] On production DB, inspect the `Migrated schema to version 8 (rewrote N keepa_products rows)` log line after deploy to quantify the backfill rewrite count.
