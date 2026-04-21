# Implementation Report: Fix transient-failure permanent dead-listing (closes #11)

## Summary

Replaced the one-shot "empty title + no csv → permanent `not_listed`"
post-fetch check in `ensure_keepa_data` with a two-gate decision:

1. Responses with a populated `PriceHistory.fetch_error` (rate_limited,
   network, api_error, etc.) are classified as **transient** — status
   and strikes are left untouched; a clear warning surfaces in the
   envelope `meta["warnings"]`.
2. Eligible empty responses (`fetch_error == ""`) increment a new
   `product_asins.not_listed_strikes` INTEGER column. The row is only
   flipped to `not_listed` after
   `NOT_LISTED_STRIKE_THRESHOLD == 3` consecutive observations; any
   fetch that returns data resets the counter to 0.

Manual recovery (`update_asin_status(..., status='active')`) now has
explicit test coverage, closing the other gap noted in issue #11.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Files Changed | 4 | 5 |
| LOC delta | ~180 new | +485 / -35 (≈ 450 new) |
| Tests added | 7 | 8 (added `test_genuine_empty_increments_strike_under_threshold` beyond plan) |

`tests/test_db.py` was the 5th file — necessary scaffolding updates
for the schema-version bump that the plan didn't anticipate.

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Schema — bump v8 + add `not_listed_strikes` | Complete | Required an extra `_SCHEMA_SQL` seed insert for v8 (plan missed this) |
| 2 | DB helpers — increment/clear strikes | Complete | `clear` guards `WHERE not_listed_strikes != 0` to avoid `updated_at` churn |
| 3 | API — split `_try_mark_not_listed` | Complete | `_record_empty_observation` returns `(strikes, flipped)`; `_record_successful_observation` resets |
| 4 | API — rewrite `ensure_keepa_data` post-fetch | Complete | Three-branch flow: transient / empty / success |
| 5 | Tests — 3 new test classes | Complete | 8 new tests total (5 unit + 2 integration + 1 recovery) |
| 6 | Docs — DEVELOPER.md | Complete | v8 row + "Transient-Failure Guard" section + updated mermaid diagram |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `ruff check src/ tests/` — all checks passed |
| Circular-import guard | Pass | `import amz_scout.api, amz_scout.db` clean |
| Unit + integration tests | Pass | 312 passed, 8 skipped (pre-existing) |
| Schema validation | Pass | `SCHEMA_VERSION == 8`, column present |
| v7→v8 migration | Pass | Synthetic v7 DB migrates in place; legacy rows backfill to 0 |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | +84 / -3 |
| `src/amz_scout/api.py` | UPDATE | +133 / -27 |
| `tests/test_api.py` | UPDATE | +233 / 0 |
| `tests/test_db.py` | UPDATE | +28 / -11 (scaffolding) |
| `docs/DEVELOPER.md` | UPDATE | +31 / -1 |

## Deviations from Plan

| Deviation | Why | Evidence |
|---|---|---|
| **Monkey-patch target** changed from `api_mod.get_keepa_data` to `amz_scout.keepa_service.get_keepa_data` | `get_keepa_data` is a **lazy import inside `ensure_keepa_data`** (api.py:821), not a module-level binding. Patching the caller module has no effect because `api_mod.get_keepa_data` doesn't exist as an attribute. | `src/amz_scout/api.py:821` `from amz_scout.keepa_service import get_keepa_data` inside the function body |
| **`ProductFreshness` fixture fields** corrected | Plan said `action="needs_fetch"` + `mode=None`; real dataclass Literal is `"use_cache" / "fetch" / "skip"` and there is a required `reason: str` field instead of `mode`. | `src/amz_scout/freshness.py:27-38` |
| **Test ASIN `B0RECOV0001` shortened** to `B0RECOV001` | Plan used an 11-char string; `_resolve_asin` ASIN regex is `^[A-Z0-9]{10}$`, so 11-char strings fall through to "Product not found" instead of hitting the `not_listed` gate. Transient/empty ASINs (`B0TRANS0001`, `B0EMPTY0001`) also >=11 chars but are OK because they never go through `_resolve_asin` in those tests. | `src/amz_scout/api.py` `_ASIN_RE` |
| **`_SCHEMA_SQL` seed insert for v8** added | Plan only mentioned `_migrate()` block + baseline `CREATE TABLE`; missed that `_SCHEMA_SQL` also seeds every `schema_migrations` row. Without it, fresh DB would show `MAX(version)=7` even with column present — then re-run v8 migration on next open (idempotent but semantically confusing). | `src/amz_scout/db.py:597-598` |
| **`test_db.py` scaffolding edits** (5th file) | v6/v7 migration tests force-downgrade schema via `DELETE FROM schema_migrations WHERE version IN (...)`. With v8 added, these deletes left `MAX(version)=8`, causing `_migrate` to early-return and the target migration never to run. Changed to `WHERE version >= N`. Also explicit column list in v6 `INSERT SELECT` to avoid column-count mismatch with the new `not_listed_strikes` column. | `tests/test_db.py:435, 457-467, 624, 768` |
| **Extra integration test** `test_genuine_empty_increments_strike_under_threshold` | Complemented the transient test to exercise the symmetric "genuine empty → strike increments" branch end-to-end through `ensure_keepa_data`. | `tests/test_api.py` |

## Issues Encountered

1. **Lazy-import monkey-patch trap** — first attempt followed plan
   literally; test silently no-op'd because `api_mod.get_keepa_data`
   doesn't exist. Resolved by patching the source module instead.
2. **ASIN regex mismatch** — `B0RECOV0001` (11 chars) never reached the
   `not_listed` gate. Discovered via test failure; fixed by truncating.
3. **Transitive test breakage** — schema bump broke v6/v7 migration
   tests because they depend on `MAX(version)` relative to
   `SCHEMA_VERSION`. Resolved by changing literal version lists to
   range deletes (`WHERE version >= N`). Added a comment so future
   schema bumps don't repeat the same regression.

## Tests Written

| Test File | Class | Tests | Coverage |
|---|---|---|---|
| `tests/test_api.py` | `TestEmptyObservationStrikes` | 5 | unregistered no-op, first increment, threshold flip, success reset, already-not_listed path |
| `tests/test_api.py` | `TestEnsureKeepaDataTransientVsPermanent` | 2 | transient blip preserves status; genuine empty increments strike |
| `tests/test_api.py` | `TestAsinStatusRecovery` | 1 | `not_listed -> active` recovery restores `_resolve_asin` pass-through |

## Next Steps

- [ ] Code review via `/code-review`
- [ ] Create PR via `/prp-pr` (body: `Closes #11`)
- [ ] (Follow-up, not this PR) Use `keepa_products.availability_amazon == -1`
      to lower the strike threshold for ASINs Keepa explicitly reports
      as unavailable
- [ ] (Follow-up, not this PR) Background `not_listed -> active`
      auto-recovery job
