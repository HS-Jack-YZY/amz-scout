# Local Code Review — 2026-04-20

**Branch**: `feat/brand-model-normalization`
**Scope**: uncommitted changes (5 tracked + 2 untracked) — schema v7 brand/model normalization
**Decision**: **REQUEST CHANGES** (1 HIGH blocker: v7 contract only half-implemented)

## Summary

v7 migration in `db.py` and its tests are well-constructed: table rebuild, duplicate merge, FK toggle, and display-literal preservation all work and are verified. But `register_product` is the only *write* path that switched to normalized keys — the *query-side* helpers (`find_product_exact`, `list_products_with_asins`, `load_products_from_db`) still compare `brand` / `model` literally. This directly contradicts CLAUDE.md rule #15 added in this same PR, and produces a silent "registered but not findable" failure mode visible from `api.py` (`remove_product_by_model`, `set_product_asin`, `discover_asin` write-back).

## Findings

### CRITICAL
None. No hardcoded secrets; all SQL is parameterized; migration atomicity and FK integrity are verified by tests.

### HIGH

**H1 — `find_product_exact` breaks the v7 identity contract**
- File: `src/amz_scout/db.py:1864-1874`
- Callers: `cli.py:411`, `api.py:1085` (`remove_product_by_model`), `api.py:1111` (`set_product_asin`), `api.py:1452` (discovery write-back)
- Bug: `register_product(conn, "Travel Router", "TP-Link", "Archer BE400")` now stores `brand_key='tp-link'`. A later call `find_product_exact(conn, "tp-link", "archer be400")` runs `WHERE brand = ? AND model = ?` against the **display literal** and returns `None`, producing `"Product not found: tp-link archer be400"` even though the row exists.
- Why this matters beyond aesthetics: the API contract now says callers no longer have to normalize ("调用方无需自己归一化"), so users and tools *will* pass casing/whitespace variants and hit "not found".
- Fix: compare on `brand_key` / `model_key` using `_normalize_key(brand)` / `_normalize_key(model)`; no change needed at call sites.

**H2 — `list_products_with_asins` / `load_products_from_db` brand filter still literal**
- File: `src/amz_scout/db.py:1749-1751`, `1792-1794`
- Same failure mode applied to `brand` filter: passing `brand="TP-Link"` finds the row; `brand="tp-link"` does not, because the display literal may be `"TP-Link"`. Exposed via `list_products(brand=...)` public API.
- Fix: switch the filter to `p.brand_key = ?` and normalize the input (`params.append(_normalize_key(brand))`).

### MEDIUM

**M1 — `_find_product_by_ean` uses a different normalization than `_normalize_key`**
- File: `src/amz_scout/db.py:885-890`
- Diff introduced `LOWER(TRIM(kp.brand)) = LOWER(TRIM(?))`. This handles outer whitespace + case, but **does not fold internal multi-space**, so `"TP  Link"` vs `"TP Link"` diverge. `_normalize_key` folds both. Two normalization regimes in the same codebase for the same concept will drift.
- Impact is bounded (internal multi-space is rare in vendor brand fields), but a mismatch means the EAN match path and the registry key path disagree on identity — which is exactly what v7 is supposed to end.
- Fix options: (a) `REPLACE(LOWER(TRIM(kp.brand)), '  ', ' ')` — ugly and only folds double-space; (b) select `kp.brand` and compare in Python with `_normalize_key`; (c) add a `keepa_products.brand_key` column. (b) is lowest-friction; (c) is the consistent long-term answer.

### LOW

**L1 — `find_product_exact` docstring no longer accurate**
- File: `src/amz_scout/db.py:1868-1869`
- Says "exact brand + model match". After H1's fix it will be a normalized match; the docstring should say so. If the helper is left literal, the name is misleading given that `register_product` no longer behaves that way.

**L2 — `db.py` is 1996 lines**
- Exceeds the 800-line soft cap from coding-style rules. Pre-existing; this PR added ~140 lines of migration + helper, which is reasonable in isolation. Flag for a future split (migrations module, registry module); not blocking for this PR.

**L3 — `test_v7_fk_integrity_after_merge` does not actually exercise a merge**
- File: `tests/test_db.py:767-787`
- The forced-v6-downgrade seeds one `products` row, so the merge path never runs in this test. The test verifies FK enforcement is restored, which is the point, but the name oversells. Either rename to `test_v7_fk_enforced_after_migration` or add a duplicate row so both concerns are covered.

## Validation Results

| Check | Result |
|---|---|
| `pytest tests/test_db.py -q` | Pass (39/39) |
| Build | N/A (Python package, no build step) |
| Lint | Not run |

## Files Reviewed

| File | Change | Verdict |
|---|---|---|
| `src/amz_scout/db.py` | Modified | v7 migration + `register_product` correct; query helpers not updated — see H1/H2 |
| `tests/test_db.py` | Modified | Strong migration coverage; minor naming nit (L3) |
| `CLAUDE.md` | Modified | Rule #15 added; contradicts H1/H2 until those are fixed |
| `docs/DEVELOPER.md` | Modified | Migration table + v7 explainer accurate and useful |
| `.claude/PRPs/plans/brand-model-normalization.plan.md` | Deleted | Plan retired; replacement exists in `plans/completed/` |
| `.claude/PRPs/plans/completed/brand-model-normalization.plan.md` | Added | Completed plan archive — OK |
| `.claude/PRPs/reports/brand-model-normalization-report.md` | Added | Report artifact — OK |

## Recommended Next Steps

1. Fix H1 + H2 in `db.py` (swap the three literal comparisons to `*_key` columns using `_normalize_key`). Small, contained change.
2. Add one test per fix: `find_product_exact` matches whitespace/case variants; `list_products_with_asins(brand=...)` matches normalized variants.
3. Address M1 with a follow-up (or in this PR if scope allows) so the EAN reconciliation uses the same normalization regime.
4. Optionally tighten L1 / L3 in the same commit.
