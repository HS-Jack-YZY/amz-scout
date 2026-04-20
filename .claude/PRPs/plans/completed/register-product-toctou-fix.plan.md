# Plan: Fix `register_product` TOCTOU race

## Summary

`register_product` currently reads the row with a bare `SELECT`, then
inserts under `with conn:`. Between the SELECT and the INSERT a second
writer can claim the same `(brand_key, model_key)`, causing the second
caller to raise `sqlite3.IntegrityError` instead of the documented
`(existing_id, False)` return. Collapse the pattern into a single
`INSERT ... ON CONFLICT(brand_key, model_key) DO NOTHING RETURNING id`
statement with a `SELECT` fallback for the conflict path. This matches
the upsert precedent already used by `register_asin`.

## User Story

As the webapp process (or any multi-process caller), I want concurrent
`register_product` calls for the same normalized (brand, model) to
return `(existing_id, False)` for the losers, so that a Keepa-fetch
burst does not crash with `IntegrityError`.

## Problem → Solution

**Current**: SELECT → (context-switch) → another writer INSERTs same
key → our INSERT raises `sqlite3.IntegrityError` out of `register_product`,
breaking the documented `(int, bool)` contract.

**Desired**: One atomic SQL statement resolves insert-or-exist. Losers
fall back to one extra SELECT to fetch the canonical id. Never raises
on duplicate-key races.

## Metadata

- **Complexity**: Small
- **Source PRD**: N/A (follow-up from PR #15 code-review)
- **PRD Phase**: N/A
- **Estimated Files**: 2 (1 source, 1 test)

---

## UX Design

Internal change — no user-facing UX transformation. Webapp error-rate
under concurrent load goes from "rare 500 on duplicate registration"
to "0 on duplicate registration".

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `register_product()` under race | `sqlite3.IntegrityError` raised | Returns `(existing_id, False)` | Single-process CLI unaffected |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `src/amz_scout/db.py` | 1595-1628 | `register_product` — function being modified |
| **P0** | `src/amz_scout/db.py` | 1631-1648 | `register_asin` — precedent for `ON CONFLICT` usage in this file |
| P1 | `src/amz_scout/db.py` | 709-726 | `products` table DDL — confirms `UNIQUE(brand_key, model_key)` is the conflict target |
| P1 | `src/amz_scout/db.py` | 838-846 | `_normalize_key` — used to build the conflict keys |
| P1 | `tests/test_db.py` | 528-600 | `TestBrandModelKeyMigrationV7` — style reference for new-in-scope tests |
| P2 | `tests/test_db.py` | 540-555 | `test_v7_register_product_matches_whitespace_variants` — closest existing behavior test |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| SQLite `ON CONFLICT DO NOTHING RETURNING id` | https://sqlite.org/lang_returning.html | RETURNING is only emitted for rows that were actually INSERTed. DO NOTHING + conflict → zero rows returned. This is exactly the signal we need. |
| SQLite UPSERT | https://sqlite.org/lang_upsert.html | Requires ≥3.24 (ON CONFLICT). RETURNING requires ≥3.35. This repo runs Python ≥3.12 → stdlib sqlite3 ships SQLite ≥3.40, so both are safe. |
| Python sqlite3 isolation | https://docs.python.org/3/library/sqlite3.html | `with conn:` commits on success / rolls back on exception. Implicit transaction covers exactly one statement run in our case — good enough for this atomic upsert. |

### Research Notes

```
KEY_INSIGHT: `INSERT ... ON CONFLICT(...) DO NOTHING RETURNING id`
returns exactly one row on a fresh insert, zero rows on conflict.
APPLIES_TO: The `register_product` rewrite.
GOTCHA: Python's `sqlite3.Cursor.fetchone()` returns `None` on zero
rows, which is the canonical "conflict happened" signal. `lastrowid`
is unreliable on the conflict path (unchanged from prior statement).

KEY_INSIGHT: `register_asin` at db.py:1640-1648 already uses
`ON CONFLICT(product_id, marketplace) DO UPDATE`. Same mechanic, just
DO NOTHING instead of DO UPDATE.
APPLIES_TO: Pattern faithfulness — the new `register_product` should
read like a sibling of `register_asin`, not invent a new style.

KEY_INSIGHT: The `with conn:` block is required to hold the transaction
over the INSERT + conditional SELECT, so the fallback SELECT sees the
committed winning row. Without it the SELECT could race back to empty
if the winner's transaction hasn't committed yet.
APPLIES_TO: Task 1 structure.
GOTCHA: Do NOT call `conn.commit()` manually; `with conn:` owns commit.
```

---

## Patterns to Mirror

### UPSERT_WITH_ON_CONFLICT
```python
# SOURCE: src/amz_scout/db.py:1640-1648 (register_asin)
with conn:
    conn.execute(
        "INSERT INTO product_asins (product_id, marketplace, asin, status, notes) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(product_id, marketplace) DO UPDATE SET "
        "asin=excluded.asin, status=excluded.status, notes=excluded.notes, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
        (product_id, marketplace, asin, status, notes),
    )
```

### NORMALIZE_KEY_USAGE
```python
# SOURCE: src/amz_scout/db.py:1613-1614 (current register_product)
brand_key = _normalize_key(brand)
model_key = _normalize_key(model)
```

### RETURN_SHAPE
```python
# SOURCE: src/amz_scout/db.py:1610-1611 (docstring contract — MUST preserve)
# Returns ``(product_id, is_new)`` where *is_new* is True when the row
# was just inserted.
```

### TEST_STRUCTURE
```python
# SOURCE: tests/test_db.py:540-555 (TestBrandModelKeyMigrationV7)
def test_v7_register_product_matches_whitespace_variants(self, conn):
    from amz_scout.db import register_product

    pid1, new1 = register_product(conn, "Router", "TP-Link", "Archer BE400")
    pid2, new2 = register_product(conn, "Router", "  tp-link  ", "archer  be400")
    ...
    assert pid1 == pid2
    assert new1 is True
    assert new2 is False
```

### TEST_CONCURRENCY (no prior art — this plan introduces it)
```python
# Concurrency-test skeleton (new in this PR; use threading + file-backed
# DB because :memory: is connection-local in sqlite3):
import threading
import sqlite3

def _worker(db_path, brand, model, results, idx):
    c = sqlite3.connect(db_path, timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        results[idx] = register_product(c, "Router", brand, model)
    finally:
        c.close()
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | Rewrite `register_product` (lines 1595-1628) to a single UPSERT with RETURNING. |
| `tests/test_db.py` | UPDATE | Add `TestRegisterProductConcurrency` class: one test proves race-safety, one test documents the first-writer-wins display literal under race. |

## NOT Building

- A new helper or abstraction for UPSERT (the pattern is three lines — inline is correct).
- A retry loop on `IntegrityError` (the new statement cannot raise that).
- Changes to `find_product_exact`, `list_registered_products`, or `load_products_from_db` (already fixed in PR #15).
- Changes to `_find_product_by_ean` normalization drift (tracked separately under issue #12).
- A connection pool or lock manager (out of scope — sqlite3 already serializes writers at the file level).
- Docs/CLAUDE.md rule update (the behavioral contract is unchanged; only the crash mode is fixed).

---

## Step-by-Step Tasks

### Task 1: Rewrite `register_product` as single-statement UPSERT with fallback

- **ACTION**: Replace the bare SELECT → `with conn:` INSERT pair with a single `INSERT ... ON CONFLICT(brand_key, model_key) DO NOTHING RETURNING id` under `with conn:`. If `fetchone()` returns a row, return `(id, True)`. Otherwise, the conflict path was taken — run the same normalized SELECT and return `(id, False)`.
- **IMPLEMENT**: Inside `register_product` at `src/amz_scout/db.py:1595-1628`, replace lines 1615-1628 (keep lines 1613-1614 that compute `brand_key`/`model_key`, keep the docstring at 1602-1612) with:
  ```python
  with conn:
      row = conn.execute(
          "INSERT INTO products "
          "(category, brand, model, brand_key, model_key, search_keywords) "
          "VALUES (?, ?, ?, ?, ?, ?) "
          "ON CONFLICT(brand_key, model_key) DO NOTHING "
          "RETURNING id",
          (category, brand, model, brand_key, model_key, search_keywords),
      ).fetchone()
  if row is not None:
      return row["id"], True
  # Conflict path: another writer (or a prior call) owns the row.
  # Fetch the canonical id and return is_new=False.
  existing = conn.execute(
      "SELECT id FROM products WHERE brand_key = ? AND model_key = ?",
      (brand_key, model_key),
  ).fetchone()
  return existing["id"], False
  ```
- **MIRROR**: `UPSERT_WITH_ON_CONFLICT` pattern (from `register_asin` at `db.py:1640-1648`). The `ON CONFLICT(...)` column list is the declared `UNIQUE(brand_key, model_key)` constraint from `db.py:725`.
- **IMPORTS**: No new imports. `_normalize_key` is already in scope.
- **GOTCHA**:
  - The `conn.row_factory = sqlite3.Row` contract is assumed here (it's the project convention, set in `open_db` and in the test fixture). Index `row["id"]`, not `row[0]`, to stay consistent with the rest of `db.py`.
  - `RETURNING` only yields rows for *newly inserted* rows. On conflict it yields zero rows → `fetchone()` returns `None` → we enter the fallback SELECT.
  - Keep the fallback SELECT *outside* `with conn:`. The winner's transaction must already be committed by the time we query; running the SELECT inside our own txn is fine but not required, and pulling it out keeps the commit boundary minimal.
  - Do NOT keep `cur.lastrowid` — it's unreliable on the conflict path.
- **VALIDATE**: `pytest tests/test_db.py::TestBrandModelKeyMigrationV7::test_v7_register_product_matches_whitespace_variants -v` must still pass (preserves the (int, bool) contract and first-writer-wins). Then `pytest -q` (full suite) must stay at 296 passed.

### Task 2: Add concurrency test class

- **ACTION**: Add `TestRegisterProductConcurrency` to `tests/test_db.py` at the end of the file, after `TestFindProductByEanBrandGuardV7`. Two tests: (a) N concurrent threads registering the same normalized (brand, model) — exactly one gets `is_new=True`, the rest get `is_new=False` and the same `product_id`, none raise. (b) Concurrent writers with different display literals — the stored `brand` / `model` match whichever thread won (no corruption), and all threads see the same `product_id`.
- **IMPLEMENT**:
  ```python
  # ─── register_product concurrency ────────────────────────────────


  class TestRegisterProductConcurrency:
      """`register_product` must be race-safe: concurrent writers for
      the same normalized (brand, model) must all return the same
      product_id, with exactly one is_new=True and no IntegrityError
      escaping. Regression guard against the pre-UPSERT TOCTOU window.
      """

      def _run_workers(self, db_path, variants):
          import threading

          results: list[tuple[int, bool] | BaseException] = [None] * len(variants)

          def worker(i, brand, model):
              c = sqlite3.connect(str(db_path), timeout=5.0)
              c.row_factory = sqlite3.Row
              c.execute("PRAGMA foreign_keys = ON")
              try:
                  from amz_scout.db import register_product
                  results[i] = register_product(c, "Router", brand, model)
              except BaseException as exc:
                  results[i] = exc
              finally:
                  c.close()

          threads = [
              threading.Thread(target=worker, args=(i, b, m))
              for i, (b, m) in enumerate(variants)
          ]
          for t in threads:
              t.start()
          for t in threads:
              t.join()
          return results

      def test_concurrent_same_key_no_integrity_error(self, tmp_path):
          """N workers all register the same normalized (brand, model).
          Exactly one sees is_new=True; all share the same product_id;
          no IntegrityError leaks out.
          """
          import amz_scout.db as db_mod

          db_path = tmp_path / "concurrency.db"
          c0 = sqlite3.connect(str(db_path))
          c0.execute("PRAGMA foreign_keys = ON")
          init_schema(c0)
          c0.close()
          db_mod._schema_initialized.discard(str(db_path))

          variants = [("TP-Link", "Archer BE400")] * 8
          results = self._run_workers(db_path, variants)

          for r in results:
              assert not isinstance(r, BaseException), (
                  f"worker raised: {r!r}"
              )
          ids = {r[0] for r in results}
          new_flags = [r[1] for r in results]
          assert len(ids) == 1, f"expected all workers to agree on id, got {ids}"
          assert new_flags.count(True) == 1, (
              f"expected exactly one is_new=True, got {new_flags}"
          )
          assert new_flags.count(False) == 7

      def test_concurrent_variant_literals_display_is_first_writer(self, tmp_path):
          """Concurrent writers pass different casing/whitespace variants
          of the same normalized key. Regardless of which thread wins,
          the stored display literal is one of the inputs (no corruption)
          and all threads observe the same product_id.
          """
          import amz_scout.db as db_mod

          db_path = tmp_path / "concurrency_variants.db"
          c0 = sqlite3.connect(str(db_path))
          c0.execute("PRAGMA foreign_keys = ON")
          init_schema(c0)
          c0.close()
          db_mod._schema_initialized.discard(str(db_path))

          variants = [
              ("TP-Link", "Archer BE400"),
              ("tp-link", "archer be400"),
              ("  TP-LINK  ", "Archer  BE400"),
              ("Tp-Link", "archer\tbe400"),
          ]
          results = self._run_workers(db_path, variants)
          for r in results:
              assert not isinstance(r, BaseException), (
                  f"worker raised: {r!r}"
              )
          ids = {r[0] for r in results}
          assert len(ids) == 1, f"expected single canonical id, got {ids}"

          c = sqlite3.connect(str(db_path))
          c.row_factory = sqlite3.Row
          row = c.execute(
              "SELECT brand, model, brand_key, model_key FROM products"
          ).fetchone()
          c.close()
          # Display literal is ONE of the raced inputs, not a merged
          # string. Keys are canonical.
          assert (row["brand"], row["model"]) in variants
          assert row["brand_key"] == "tp-link"
          assert row["model_key"] == "archer be400"
  ```
- **MIRROR**: `TEST_STRUCTURE` + the new `TEST_CONCURRENCY` skeleton above. For the schema-init pattern, follow the `db_mod._schema_initialized.discard(...)` trick already used by `test_v7_migrates_v6_db_and_merges_duplicates` at `tests/test_db.py:674`.
- **IMPORTS**: All inside functions (matches existing test style — see `TestFindProductByEanBrandGuardV7` at `tests/test_db.py:878+`).
- **GOTCHA**:
  - Use `tmp_path` (file-backed), not `conn` (`:memory:`). SQLite `:memory:` DBs are **connection-local**; multiple connections would each get a fresh empty DB and the test would be a no-op.
  - Pass `timeout=5.0` on every threaded connection. SQLite's default is 0s → under contention workers would raise `OperationalError: database is locked` and look like a test flake.
  - Always set `PRAGMA foreign_keys = ON` per connection — it's connection-scoped.
  - Clear `db_mod._schema_initialized` for the path so `init_schema` inside workers (if called) will run; though in this test the bootstrap connection already primed the schema, workers do not call `init_schema` and therefore don't need this. Keep the `.discard` call as a defensive no-op for parity with the existing migration test.
- **VALIDATE**: `pytest tests/test_db.py::TestRegisterProductConcurrency -v` — both tests pass under repeated runs (try ~10 iterations locally: `for i in $(seq 10); do pytest tests/test_db.py::TestRegisterProductConcurrency -q || break; done`). Full suite must land at 298 passed.

### Task 3: Verify existing tests still lock in the contract

- **ACTION**: Run the v7 test classes without code changes — they cover the single-threaded case of the new upsert path.
- **IMPLEMENT**: No code changes. Just verify.
- **MIRROR**: N/A.
- **IMPORTS**: N/A.
- **GOTCHA**: If `test_v7_register_product_preserves_display` starts to fail, the RETURNING-plus-fallback rewrite accidentally overwrites the display literal on hit. Revisit Task 1.
- **VALIDATE**: `pytest tests/test_db.py::TestBrandModelKeyMigrationV7 tests/test_db.py::TestQuerySideNormalizationV7 -v` → 13 passed.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_v7_register_product_matches_whitespace_variants` (existing) | Three whitespace/case variants | Same `pid`; new flags `[True, False, False]` | No — happy path |
| `test_v7_register_product_preserves_display` (existing) | Second call with lowercase | Stored literal stays first writer's | No — display contract |
| `test_concurrent_same_key_no_integrity_error` (new) | 8 threads, identical (brand, model) | 1× `is_new=True`, 7× `is_new=False`, one shared id, zero exceptions | **Yes — TOCTOU race** |
| `test_concurrent_variant_literals_display_is_first_writer` (new) | 4 threads, literal-variant (brand, model) | Single canonical id; stored display ∈ input set; keys normalized | **Yes — race + display preservation** |

### Edge Cases Checklist

- [x] Concurrent access — covered by Task 2
- [x] First-writer-wins display literal under race — covered
- [x] Same-id convergence across threads — covered
- [ ] Empty / very long brand — intentionally OUT OF SCOPE; tracked under pr-test-analyzer's nice-to-have #5 (`test_register_product_empty_brand_model`). Not a regression risk for this fix.
- [ ] Cross-process race (multiple OS processes) — skipped; `threading` within one process exercises the same SQLite write-lock that cross-process workers would hit. Adding multiprocessing fanout has no additional signal and doubles flake risk.

---

## Validation Commands

### Static Analysis

No TypeScript / type-checker in this repo. `ruff` is the project linter.

```bash
ruff check src/amz_scout/db.py tests/test_db.py
```

EXPECT: zero errors.

### Unit Tests (targeted)

```bash
pytest tests/test_db.py::TestRegisterProductConcurrency -v
pytest tests/test_db.py::TestBrandModelKeyMigrationV7 tests/test_db.py::TestQuerySideNormalizationV7 -v
```

EXPECT: both groups pass.

### Full Test Suite

```bash
pytest -q
```

EXPECT: 298 passed, 8 skipped (was 296 passed pre-change, +2 from the new concurrency class).

### Race-Repetition (flake check)

```bash
for i in $(seq 1 10); do
  pytest tests/test_db.py::TestRegisterProductConcurrency -q || { echo "flake at iteration $i"; break; }
done
```

EXPECT: 10 consecutive green runs. If any iteration fails, the upsert is not truly atomic — re-examine Task 1.

### Manual Validation

- [ ] Read the rewritten `register_product` aloud. It should be a single UPSERT + conditional SELECT — no `lastrowid`, no bare SELECT-before-INSERT.
- [ ] `grep -n "SELECT id FROM products" src/amz_scout/db.py` — expect one hit inside `register_product` (the fallback) and no other callers that could sneak in a literal comparison.
- [ ] `grep -n "cur.lastrowid" src/amz_scout/db.py` — `register_product` must no longer appear.

---

## Acceptance Criteria

- [ ] `register_product` returns `(existing_id, False)` on conflict with zero exceptions.
- [ ] Concurrency tests pass 10/10 consecutive iterations.
- [ ] Existing display-literal preservation tests still pass.
- [ ] Full test suite at 298 passed.
- [ ] `ruff check` clean.

## Completion Checklist

- [ ] Matches `register_asin` UPSERT style.
- [ ] `_normalize_key` still invoked before binding keys (no duplicate normalization).
- [ ] `(int, bool)` return contract unchanged.
- [ ] Display literal preserved on hit (not overwritten).
- [ ] No new dependencies introduced.
- [ ] No changes outside `db.py` / `test_db.py`.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Host SQLite older than 3.35 (no RETURNING) | Low | High — build breaks at runtime | Project pins Python ≥3.12; stdlib sqlite3 bundles ≥3.40. Add a startup assert only if a deploy target without a bundled sqlite appears. |
| Thread-based test flakes on CI with low scheduling variance | Low | Medium | Use file-backed DB + `timeout=5.0` + 8 workers (enough to force contention). Run repetition loop locally once; if it ever flakes in CI, bump thread count. |
| `conn.row_factory = sqlite3.Row` assumption breaks if a caller passes a bare connection | Low | Low | All entry points (`open_db`, test `conn` fixture, webapp bootstrap) already set `row_factory`. Non-conforming callers are an independent bug. |
| Fallback SELECT races with another concurrent DELETE | Very low | Low | `register_product` never runs concurrently with delete paths in this project; SQLite's write-lock serializes them anyway. |

## Notes

- The fix is deliberately minimal. Any broader concurrency hardening (connection pool, WAL tuning, cross-process test coverage) belongs in a separate, larger effort — not this plan.
- If `INSERT ... RETURNING` returns zero rows but the subsequent SELECT also returns zero rows, that signals either schema corruption or an unexpected third party (e.g. a DELETE) ran between the two statements. The fallback will raise `TypeError: 'NoneType' object is not subscriptable`; this is acceptable — the contract of `register_product` assumes a stable schema, and surfacing a loud failure is better than silently returning a fake id. No defensive handling required.
- A follow-up to unify `_find_product_by_ean` normalization with `_normalize_key` (M1 from the local review / partial overlap with issue #12) is tracked separately and is NOT part of this plan.
