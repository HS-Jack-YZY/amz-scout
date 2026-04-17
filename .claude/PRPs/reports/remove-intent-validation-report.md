# Implementation Report: Remove Intent Validation (v6 migration)

## Summary

Collapsed `product_asins.status` from 4 values (`unverified`/`verified`/`wrong_product`/`not_listed`) to 2 values (`active`/`not_listed`) under schema v6. Deleted `validate_asins()`, `validate_and_discover()`, and `get_unverified_asins()`. Query gate in `_resolve_asin` now only rejects `not_listed` — intent-level mismatches are pushed back to interactive users reading the Keepa title in query responses. `_try_mark_not_listed()`, `discover_asin()`, and the single-write `update_asin_status()` entry point remain unchanged.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Estimated Files | 8 | 8 touched + report |
| Test count | ~253–258 | **258 passed, 7 skipped** |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Bump `SCHEMA_VERSION` 5→6 | ✅ | db.py:105 |
| 2 | Add v6 migration block | ✅ | RENAME → CREATE → INSERT+CASE → DROP → index rebuild |
| 3 | Update `_SCHEMA_SQL` (fresh-DB path + schema_migrations seed) | ✅ | 2-value CHECK + DEFAULT `'active'` |
| 4 | Collapse query gates `NOT IN (...)` → `!= 'not_listed'` | ✅ | db.py two spots |
| 5 | Collapse `_check_asin_status_gate` | ✅ | single helper shared by both `_resolve_asin` paths |
| 6 | `register_asin` default → `"active"` + purge `"unverified"` callsites | ✅ | db.py (×3), api.py (×4), cli.py (×1) |
| 7 | Delete `get_unverified_asins()` | ✅ | db.py |
| 8 | Delete `validate_asins()` + `validate_and_discover()` | ✅ | api.py — docstring reference in `batch_discover` also cleaned |
| 9 | Update schema-version asserts 5→6; remove TestStatusMigrationV5 | ✅ | test_db.py |
| 10 | Add TestStatusMigrationV6 (5 tests) | ✅ | test_db.py |
| 11 | Remove `validate_asins` import + TestValidateAsins; simplify TestResolveAsinStatusGate | ✅ | test_api.py |
| 12 | Delete TestValidateAndDiscoverPhases + I8 docstring bullet | ✅ | test_core_flows.py |
| 13 | Rewrite DEVELOPER.md "ASIN Status Semantics" | ✅ | 2-value table + intent-vs-availability design note + 2-node mermaid |
| 14 | Prune CLAUDE.md decision tree / imports / Key Behaviors #10 | ✅ | also added an "Intent 验证" blockquote hinting at v6 policy |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Ruff | ✅ Pass | All checks passed (after removing one orphan import of `store_keepa_product` left by TestValidateAsins deletion) |
| Unit Tests (affected) | ✅ Pass | test_db.py + test_api.py + test_core_flows.py = 146 passed |
| Full suite | ✅ Pass | 258 passed, 7 skipped |
| Import smoke | ✅ Pass | `validate_asins` / `validate_and_discover` raise ImportError; `SCHEMA_VERSION == 6` |
| Live v5→v6 migration | ✅ Pass | `test_v6_migrates_legacy_statuses_to_active` forces v5 shape, reopens, asserts CASE-mapping correctness + idx rebuild + rejection of legacy values |

## Files Changed

| File | Action |
|---|---|
| `src/amz_scout/db.py` | UPDATE — SCHEMA_VERSION bump; v6 migration block; `_SCHEMA_SQL` CHECK + seed; 2 query gates; register_asin default; 2 caller cleanups; `get_unverified_asins` deleted |
| `src/amz_scout/api.py` | UPDATE — `_check_asin_status_gate` collapsed; `update_product_asin` default; `register_market_asins` SQL; `discover_asin` 2 callers + docstring + return dict; **deleted `validate_asins` (~135 lines) + `validate_and_discover` (~70 lines)**; `batch_discover` docstring prune |
| `src/amz_scout/cli.py` | UPDATE — `_save_discovered_asin` drops explicit `status="unverified"` |
| `tests/test_db.py` | UPDATE — schema assertions 5→6; TestStatusMigrationV5 replaced by TestStatusMigrationV6 (5 tests) |
| `tests/test_api.py` | UPDATE — `validate_asins` import removed; TestValidateAsins deleted; TestResolveAsinStatusGate simplified (`wrong_product` branch deleted, `verified`→`active`); `test_update_with_status` uses `status="not_listed"`; orphan `store_keepa_product` import removed |
| `tests/test_core_flows.py` | UPDATE — TestValidateAndDiscoverPhases deleted; I8 docstring bullet removed; hardcoded `"verified"`/`"unverified"` asserts updated to `"active"` |
| `docs/DEVELOPER.md` | UPDATE — ASIN Status Semantics rewritten (2-value table + intent-vs-availability + 2-node mermaid + Phase 3 deferrals) |
| `CLAUDE.md` | UPDATE — decision tree entries removed; imports block pruned; Key Behaviors #10 rewritten; Intent-validation policy blockquote added |

## Deviations from Plan

1. **Plan Task 6 undercounted api.py `"unverified"` occurrences.** Plan listed 2 callsites (default param + SQL literal). Actual cleanup also required `discover_asin` (2 × `status="unverified"` + 1 × return-dict `"status": "unverified"`), `api.py:1590` docstring, and `cli.py:418` (not in plan's Files-to-Change at all). All caught via grep.
2. **Pruned `batch_discover` docstring reference to `validate_and_discover`** — out of plan scope but needed to avoid a dangling reference to a deleted function.
3. **CLAUDE.md got an extra blockquote**: plan asked me to delete two decision-tree rows and update Key Behaviors #10; I added a one-line `> Intent 验证 ... 自 v6 起不再由系统预校验` note so future Claude sessions see the policy shift. Required one follow-up edit to remove a stray code-fence pair.

## Issues Encountered

1. **3 non-status tests regressed under the new CHECK** because they hardcoded `"verified"`/`"unverified"` literals (outside the intent-validation touchpoints the plan listed). Fixed by updating to `"active"`/`"not_listed"`.
2. **Orphan import** of `store_keepa_product` left in test_api.py after TestValidateAsins deletion — caught by ruff F401.
3. **Parallel-Edit ordering**: when batching Edits to the same file, each one is checked against the live file state; an earlier Edit can make a later Edit's old_string stale. Observed in test_core_flows.py class-deletion path — resolved by verifying with grep and re-issuing targeted edits.

## Tests Written / Modified

| Test File | Change |
|---|---|
| `tests/test_db.py` | +5 (TestStatusMigrationV6), −4 (TestStatusMigrationV5), +2 asserts 5→6 |
| `tests/test_api.py` | −5 (TestValidateAsins), −1 (wrong_product gate), rename `verified`→`active`, seed uses `active`/`not_listed` |
| `tests/test_core_flows.py` | −4 (TestValidateAndDiscoverPhases), literal status fixes in 2 tests |

## Acceptance Criteria

- [x] All 14 plan tasks completed
- [x] Ruff clean + 258 tests passing
- [x] 5 new TestStatusMigrationV6 tests green
- [x] 2 schema-version asserts updated 5→6
- [x] 3 test classes deleted (TestValidateAsins, TestValidateAndDiscoverPhases, TestStatusMigrationV5)
- [x] 2 API functions removed (validate_asins, validate_and_discover)
- [x] 1 DB helper removed (get_unverified_asins)
- [x] Remaining `'unverified'` / `'wrong_product'` literals confined to frozen v3/v5 migration blocks and the new v6 CASE-mapping comment
- [x] DEVELOPER.md rewritten to 2-value semantics with intent-vs-availability rationale
- [x] CLAUDE.md decision tree / imports / Key Behaviors #10 synced
- [ ] Real-world validation window open: observe interactive intent-correction rates over next 1–2 weeks (deferred to usage phase)

## Next Steps

- [ ] Code review via `/everything-claude-code:code-review`
- [ ] Commit via `/everything-claude-code:prp-commit`
- [ ] Push to remote

---

*Generated: 2026-04-17*
*Plan: `.claude/PRPs/plans/completed/remove-intent-validation.plan.md`*
*Source PRD: `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` Decision 2026-04-17*
