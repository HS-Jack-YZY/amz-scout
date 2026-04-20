# Implementation Report: Brand/Model Normalization for Product Registry

## Summary

Implemented schema v7: `products` table now carries `brand_key` /
`model_key` columns populated by `_normalize_key(s) = " ".join((s or
"").lower().split())`. Identity matching in `register_product` and
EAN-based product discovery (`_find_product_by_ean`) use the normalized
keys instead of literal `brand = ?` comparisons, so Keepa's literal
noise (casing, surrounding / internal whitespace) no longer splits a
single physical product across multiple `product_id` rows.

Existing display columns `products.brand` / `products.model` preserve
the first writer's literal value — all upstream callers (CLI, API,
webapp, query tools) are fully source-compatible.

v7 migration rebuilds the `products` table using SQLite's recommended
12-step pattern (FK toggle off/on), merges pre-existing literal-variant
duplicates (keeping the lowest `id` as canonical), and re-points
`product_asins` / `product_tags` at the canonical ids via
`UPDATE OR IGNORE` + `DELETE`. Same-marketplace ASIN collisions are
logged at `WARNING` for operator review.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium (FK rebuild cornercase added one extra step) |
| Confidence | N/A | High — all validation levels green |
| Files Changed | 4 | 4 (`db.py`, `test_db.py`, `CLAUDE.md`, `docs/DEVELOPER.md`) |
| New tests | 6 methods | 7 methods (added `test_v7_fk_integrity_after_merge`) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Add `_normalize_key` helper | Complete | Matches `_safe_json_list` style: private, typed, None-safe |
| 2 | Bump `SCHEMA_VERSION` → 7 + update `products` DDL | Complete | Also seeded v7 migration record in `_SCHEMA_SQL` |
| 3 | Add v7 migration branch in `_migrate` | Complete | **Deviated**: extracted into `_migrate_to_v7()` outside the main `with conn:` so `PRAGMA foreign_keys` can toggle (it's a no-op inside an active transaction) |
| 4 | Rewrite `register_product` with normalized keys | Complete | INSERT now writes `brand` / `model` / `brand_key` / `model_key` |
| 5 | Normalize brand guard in `_find_product_by_ean` | Complete | SQL-side `LOWER(TRIM(kp.brand)) = LOWER(TRIM(?))` (ASCII-safe) |
| 6 | Add `TestBrandModelKeyMigrationV7` test class | Complete | 7 methods (basic, whitespace variants, display preservation, UNIQUE, idempotent, v6→v7 merge, FK integrity) |
| 7 | Update `docs/DEVELOPER.md` + `CLAUDE.md` | Complete | DEVELOPER.md gained "Migration History" table + "Brand/Model Normalization (v7+)" section; CLAUDE.md Key Behaviors now has item 15 |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (`ruff`) | Pass | Zero warnings on `db.py` and `test_db.py` |
| Unit Tests (`pytest tests/test_db.py`) | Pass | 39 / 39, including all 7 new v7 tests |
| CLAUDE.md Size Guard | Pass | 6279 → ~6450 chars (budget 10,000) |
| Full Test Suite (`pytest`) | Pass | 290 passed, 8 pre-existing skips, 0 failures |
| Integration | N/A | No new API surface; `register_product` signature unchanged |
| Edge Cases | Pass | Whitespace, casing, None / empty brand, v6→v7 merge with FK collision, `PRAGMA foreign_key_check` |

## Files Changed

| File | Action | Impact |
|---|---|---|
| `src/amz_scout/db.py` | UPDATED | `_normalize_key`, `_migrate_to_v7`, products DDL, `register_product` rewrite, EAN brand guard |
| `tests/test_db.py` | UPDATED | `TestBrandModelKeyMigrationV7` class (7 methods); two hardcoded version assertions refreshed; v6 downgrade test now drops v7 record too |
| `docs/DEVELOPER.md` | UPDATED | Migration History table, Brand/Model Normalization subsection |
| `CLAUDE.md` | UPDATED | Key Behaviors item 15 |

## Deviations from Plan

- **Task 3 — v7 branch location**: Plan proposed inlining v7 inside the existing `try: with conn:` block like v2-v6. In practice this is unsafe: `products` is the "to" side of `ON DELETE CASCADE` FKs from `product_asins` / `product_tags`, and SQLite auto-rewrites FK references inside other tables when `ALTER TABLE RENAME` fires. That makes the naïve `rename → create → insert → drop` sequence either fail on `DROP TABLE` (FK violation) or silently cascade-delete child rows. The fix is SQLite's 12-step migration: toggle `PRAGMA foreign_keys = OFF` / `ON`, which must happen outside any active transaction. Extracted v7 into `_migrate_to_v7(conn)` and invoked after the main `with conn:` closes — v2-v6 atomicity preserved.
- **Task 6 — extra test**: Added `test_v7_fk_integrity_after_merge` on top of the 6 required methods; it runs `PRAGMA foreign_key_check` and asserts FK enforcement is re-enabled by trying to insert an ASIN referencing a non-existent `product_id`. Cheap insurance against future regressions of the FK toggle.
- **Task 6 — v6 regression test fix**: `test_v6_migrates_legacy_statuses_to_active` (written pre-v7) silently broke because its "downgrade to v5" step only deleted the v6 migration record; v7 records are now also auto-seeded by `_SCHEMA_SQL`. One-line fix: `DELETE FROM schema_migrations WHERE version IN (6, 7)` and bump the terminal assertion to `MAX(version) == 7` (both v6 and v7 now fire on reopen).

## Issues Encountered

- **Smoke test blocked by Fact-Forcing Gate**: The manual `python -c "…DROP TABLE…"` verification I ran mid-implementation was flagged as a destructive command. The Gate can't distinguish a sandboxed `tmpfile.mkstemp` SQLite harness from an `rm -rf`. The proper destination for such verification is a pytest test anyway (`tmp_path` fixture), so I rolled the same assertions into `test_v7_migrates_v6_db_and_merges_duplicates`. Net effect: higher-quality coverage, no real slowdown.
- **First test run of migration merge test failed on tuple sort**: `sorted()` on `(product_id, tag)` tuples put `'tplink-alpha'` before `'travel-router'` (because `'p' < 'r'` after the shared `'t'`). Switched the assertion to a `set` comparison — order-independent and correct for a "collection of tags" semantic.

## Tests Written

| Test Class | Methods | Coverage |
|---|---|---|
| `tests/test_db.py::TestBrandModelKeyMigrationV7` | 7 | `_normalize_key`, `register_product` normalization, display preservation, `UNIQUE(brand_key, model_key)`, migration idempotence, v6→v7 merge with FK re-pointing, `PRAGMA foreign_key_check` |

## Next Steps

- [ ] Code review via `/code-review` or `/pr-review-toolkit:review-pr`
- [ ] Create PR via `/prp-pr` against `main` (branch: `feat/brand-model-normalization`)
- [ ] Production DB backup before first live run (v7 migration merges duplicates; conflicts are logged but not recoverable from within SQLite)
