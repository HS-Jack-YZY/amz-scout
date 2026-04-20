# Local Code Review: register_product TOCTOU fix

**Reviewed**: 2026-04-20
**Branch**: `fix/register-product-toctou` → `main`
**Decision**: APPROVE (with optional L1 note)

## Summary

Two-file diff (`src/amz_scout/db.py` +12/−10, `tests/test_db.py` +106/−0).
Collapses a racy SELECT→INSERT in `register_product` into a single
atomic `INSERT ... ON CONFLICT DO NOTHING RETURNING id` with a fallback
SELECT; adds two threading-based concurrency tests. Mirrors the
pre-existing `register_asin` UPSERT style exactly. Zero CRITICAL / HIGH
findings; validation clean; 10/10 flake-repetition stable.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM

**M1. Fallback SELECT without None-guard — documented trade-off, not a bug**
- Location: `src/amz_scout/db.py:1626-1630`
- The fallback path calls `existing["id"]` without checking `existing is None`. Theoretically, a concurrent DELETE between the UPSERT conflict and the fallback SELECT would surface `TypeError: 'NoneType' object is not subscriptable`.
- Plan explicitly addresses this: "the contract of `register_product` assumes a stable schema, and surfacing a loud failure is better than silently returning a fake id. No defensive handling required." `register_product` never runs concurrently with a delete path in this codebase; SQLite's write-lock further serializes them.
- Action: None. Intentional design per plan.

### LOW

**L1. Test DB does not enable WAL — preemptive hardening**
- Location: `tests/test_db.py:982, 1017`
- The bootstrap connection `c0` sets `PRAGMA foreign_keys = ON` but not `PRAGMA journal_mode = WAL`. The `conftest.py :memory:` fixture does enable WAL. With the default rollback journal, 8 concurrent writers lean harder on `timeout=5.0` busy-wait to avoid "database is locked".
- Evidence of stability: 10/10 iterations green locally. But if CI hosts are slow or worker count rises, this is the first place to wobble.
- Suggested fix: `c0.execute("PRAGMA journal_mode = WAL")` in each bootstrap. One-line preemptive change.
- Action: Optional, non-blocking.

**L2. Test file now exceeds 800 lines (1054, up from 948)**
- Location: `tests/test_db.py`
- Pre-existing condition; this PR adds 106 lines. Rules suggest <800. Organization by feature-class remains clean.
- Action: Out of scope for this PR. Consider splitting to `tests/db/test_*.py` in a future refactor.

**L3. Worker catches `BaseException`**
- Location: `tests/test_db.py:968`
- `except BaseException as exc` would swallow `KeyboardInterrupt` / `SystemExit`. In a non-daemon test thread this is contained but slightly heavy.
- Suggested fix: narrow to `Exception`.
- Action: Optional style nit.

## Validation Results

| Check | Result |
|---|---|
| Ruff | Pass |
| Pytest — `tests/test_db.py` | Pass (47/47) |
| Pytest — full suite (prior run) | Pass (298 passed, 8 skipped) |
| Flake check (10× `TestRegisterProductConcurrency`) | Pass |
| Manual grep — `cur.lastrowid` in `db.py` | Cleared |
| Manual grep — `SELECT id FROM products` | 2 hits: one fallback in `register_product`, one in unrelated `find_product_exact` |

## Files Reviewed

- `src/amz_scout/db.py` — Modified (register_product rewrite)
- `tests/test_db.py` — Modified (new TestRegisterProductConcurrency class)

## Notes

- Atomic UPSERT is correct: RETURNING yields one row on insert, zero on conflict; `.fetchone()` inside `with conn:` is consumed before commit.
- Fallback SELECT is outside `with conn:` — winner's transaction is guaranteed committed before the read.
- Pattern compliance: mirrors `register_asin` at `db.py:1640-1648` exactly.
- Immutability: `(int, bool)` tuple return preserved; no in-place mutation added.
