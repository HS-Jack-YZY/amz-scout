# Local Code Review — 2026-04-17

**Reviewer**: Claude (local mode, uncommitted diff)
**Branch**: `feat/claude-md-slim-phase1`
**Scope**: `product_asins.status` cleanup — schema v5 tightens CHECK constraint + `_resolve_asin` fails loud on `wrong_product` / `not_listed` ASINs.
**Decision**: **REQUEST CHANGES** (1 HIGH issue blocks merge confidence)

## Summary

Solid two-layer defense: DB-level `CHECK` tightening + API-level status gate + docs update with state diagram. The `_resolve_asin` gates cover both registered-product and raw-ASIN pass-through paths, with actionable error messages pointing at existing `discover_asin()` / `update_asin_status()` entry points. The full suite passes (287 passed, 7 skipped).

However, the headline migration-preservation test **silently no-ops**: the v5 `rename → create → insert-select → drop → index` SQL is never actually executed in tests. A typo in that block would escape to production. This must be fixed before merging.

## Findings

### CRITICAL
None.

### HIGH

**[HIGH] `test_v5_preserves_existing_rows` does not exercise the v5 migration code path** — `tests/test_db.py` `TestStatusMigrationV5`

The test temporarily sets `db_mod.SCHEMA_VERSION = 4`, creates a fresh DB, writes a row, then restores `SCHEMA_VERSION = 5` and calls `init_schema` again hoping to trigger v4→v5 migration.

But `_SCHEMA_SQL` (`src/amz_scout/db.py:348-360`) unconditionally INSERTs schema_migrations records **1 through 5**, regardless of `SCHEMA_VERSION`. So the fresh DB lands at version 5 immediately. The second `init_schema` sees `current = 5 ≥ SCHEMA_VERSION`, and `_migrate` returns early. The entire v5 migration block (`db.py:268-339`) — rename, create-new, insert-select, drop-old, recreate-index — is never hit.

**Verified empirically** by spying on `_migrate`:

```
After init with SCHEMA_VERSION=4, migrations recorded: [1, 2, 3, 4, 5]
v5 migration code path exercised? False
```

**Impact**: any bug in the v5 rebuild SQL (wrong column order in INSERT, missing `idx_pa_asin` recreation, NULL-default mismatch, PRAGMA foreign-key edge case during RENAME) passes tests and breaks production DB upgrades.

**Fix** — craft a true v4-shape DB before calling `init_schema`:

```python
def test_v5_preserves_existing_rows(self, tmp_path):
    import amz_scout.db as db_mod
    db_path = tmp_path / "v4tov5.db"

    # Step 1: build a DB, then forcibly downgrade it to "v4 shape"
    c0 = sqlite3.connect(str(db_path))
    c0.row_factory = sqlite3.Row
    init_schema(c0)
    c0.execute("DELETE FROM schema_migrations WHERE version = 5")
    c0.execute("ALTER TABLE product_asins RENAME TO _pa_tmp")
    c0.execute("""
        CREATE TABLE product_asins (
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            marketplace TEXT NOT NULL,
            asin TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unverified'
                CHECK(status IN ('unverified','verified','wrong_product','not_listed','unavailable')),
            notes TEXT NOT NULL DEFAULT '',
            last_checked TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            PRIMARY KEY (product_id, marketplace)
        )
    """)
    c0.execute("INSERT INTO product_asins SELECT * FROM _pa_tmp")
    c0.execute("DROP TABLE _pa_tmp")
    c0.execute("CREATE INDEX IF NOT EXISTS idx_pa_asin ON product_asins(asin)")
    c0.commit()

    pid, _ = register_product(c0, "R", "B", "M")
    register_asin(c0, pid, "UK", "B0SURVIVE1", status="verified", notes="kept")
    c0.close()

    db_mod._schema_initialized.discard(str(db_path))

    # Step 2: reopen — v5 migration should now run for real
    c2 = sqlite3.connect(str(db_path))
    c2.row_factory = sqlite3.Row
    init_schema(c2)
    row = c2.execute(
        "SELECT asin, status, notes FROM product_asins WHERE marketplace='UK'"
    ).fetchone()
    assert row["asin"] == "B0SURVIVE1"
    assert row["status"] == "verified"
    assert row["notes"] == "kept"
    # Bonus: verify the index survived the rebuild
    idx = c2.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_pa_asin'"
    ).fetchone()
    assert idx is not None
    c2.close()
```

### MEDIUM

**[MEDIUM] Migration v5 sanity-check error is not operator-actionable** — `src/amz_scout/db.py:277-282`

```python
raise RuntimeError(
    f"Migration v5: found {bad['c']} rows with "
    "status='unavailable'. Manual cleanup required "
    "before applying v5."
)
```

An operator hitting this mid-upgrade has no SQL to run. Suggest appending the remediation inline:

```python
raise RuntimeError(
    f"Migration v5: found {bad['c']} rows with status='unavailable'. "
    "These rows were never written by current code (zombie status). "
    "To proceed:\n"
    "  UPDATE product_asins SET status='not_listed' "
    "WHERE status='unavailable';\n"
    "Then re-run init_schema to complete v5."
)
```

**[MEDIUM] Status-gate query duplicated in `_resolve_asin`** — `src/amz_scout/api.py:239-254` and `288-303`

Two identical `SELECT status FROM product_asins WHERE asin=? AND marketplace=?` blocks with identical `raise ValueError(...)` branches. Extract:

```python
def _check_asin_status_gate(
    conn: sqlite3.Connection, asin: str, marketplace: str
) -> None:
    row = conn.execute(
        "SELECT status FROM product_asins WHERE asin = ? AND marketplace = ?",
        (asin, marketplace),
    ).fetchone()
    if row and row["status"] in ("wrong_product", "not_listed"):
        raise ValueError(
            f"ASIN {asin} for {marketplace} is marked '{row['status']}'. "
            "Run discover_asin() for a valid ASIN, or update_asin_status() "
            "if this was misclassified."
        )
```

This also centralises the error wording — right now the two messages differ slightly (`"Run discover_asin() to find a valid ASIN"` vs `"Run discover_asin() for a valid ASIN"`), which makes log-based alerting noisier.

### LOW

**[LOW] v3 migration path still declares 5-value CHECK** — `src/amz_scout/db.py:224-228`

The v3 migration still creates `product_asins` with `'unavailable'` in its CHECK. Functionally safe: v3 → v5 run atomically inside `with conn:`, so no persistent intermediate state with the loose constraint is visible. But a future reader may wonder why this wasn't cleaned up. Add one comment line:

```python
# NOTE: v3's 5-value CHECK is retained for historical fidelity.
# Any DB at v3 is atomically carried through to v5 (4-value CHECK)
# in the same _migrate() transaction.
```

**[LOW] Extra SELECT after `find_product` already joined product_asins** — `src/amz_scout/api.py:240-244`

`find_product` JOINs `product_asins` but doesn't SELECT `status`. The status gate then re-queries the same row by `(asin, marketplace)` PK. Adding `pa.status` to `find_product`'s two SELECT lists would let the gate check `row["status"]` directly — one round-trip instead of two. Micro-optimization; acceptable as-is.

**[LOW] Raw status strings in tests** — `tests/test_api.py:1049-1051`, `tests/test_db.py:388`

`status="verified"` / `"not_listed"` etc. are bare strings. Consider a `StatusLiteral = Literal["unverified", "verified", "wrong_product", "not_listed"]` type alias in `db.py`, exported and used in both `register_asin` and `update_asin_status` signatures. Would let `mypy` catch typos in future callers. Not required for merge.

### INFO

- Plan was moved to `.claude/PRPs/plans/completed/` before the HIGH test-quality issue was caught. Reviewer note: a plan is only "complete" when its tests actually exercise what the plan claims.
- `update_asin_status()` symbol cited in error messages verified to exist at `db.py:1605` ✓
- Full grep confirms no source path writes `status='unavailable'` to `product_asins` — the "zombie" label in v5's description is accurate.
- `models.py:46-47`, `cli.py`, `scraper/amazon.py` uses of the word `unavailable` are unrelated stock-display strings, not enum values.

## Validation Results

| Check | Result |
|---|---|
| Type check | Skipped (no `mypy` config touched) |
| Lint | Skipped (no `ruff` invocation required) |
| Tests | **287 passed, 7 skipped** (`pytest -q`) |
| Build | N/A (Python) |
| Migration path exercised | **FAIL** — see HIGH finding |

## Files Reviewed

| File | Change | Verdict |
|---|---|---|
| `src/amz_scout/db.py` | +91/-4 (schema v5 migration + filter tightening) | ✓ correct; minor doc-comment suggestion (LOW) |
| `src/amz_scout/api.py` | +38/-0 (two status gates in `_resolve_asin`) | ✓ correct; DRY-able (MEDIUM) |
| `tests/test_db.py` | +78/-2 (v5 migration tests) | ⚠ one test no-ops (HIGH) |
| `tests/test_api.py` | +84 (status gate tests) | ✓ solid coverage |
| `docs/DEVELOPER.md` | +47 (ASIN Status Semantics section + state diagram) | ✓ excellent; matches code |
| `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` | +11 (Phase 3 open questions) | ✓ deferred items listed |

## Next Steps

1. **Fix the HIGH**: rewrite `test_v5_preserves_existing_rows` to genuinely exercise the v4→v5 migration SQL (see patch above). Re-run and verify the spied `_migrate` hits `current < 5` branch.
2. **Fix the MEDIUMs**: add operator-actionable SQL to the v5 `RuntimeError` and extract the duplicated status-gate block into a helper.
3. LOW items are optional; can be done in a follow-up.
4. Re-run `pytest -q` after changes to confirm no regressions.

---

## Remediation — 2026-04-17

All HIGH and MEDIUM items addressed in the same session. Post-fix decision: **APPROVE**.

### Changes applied

| Finding | File | Location | Change |
|---|---|---|---|
| HIGH (v5 migration test no-op) | `tests/test_db.py` | `test_v5_preserves_existing_rows` | Rewrote to forcibly downgrade a fresh DB to v4 shape (old 5-value CHECK + no v5 migration record), then let `init_schema` trigger the real v4→v5 migration. Added assertions for `idx_pa_asin` recreation and `'unavailable'` rejection post-migration. |
| MEDIUM-1 (unactionable error) | `src/amz_scout/db.py:277-286` | `_migrate` v5 sanity check | `RuntimeError` now includes the exact `UPDATE ... SET status='not_listed' WHERE status='unavailable'` SQL, plus "re-run `init_schema()`" follow-up. |
| MEDIUM-2 (duplicated gate) | `src/amz_scout/api.py` | New helper `_check_asin_status_gate` (L216-234); `_resolve_asin` (L253, L284) | Extracted 2 × ~18-line blocks into one helper; unified error wording. Net −11 LoC in `api.py`. |

### Follow-on cleanup (bonus)

While refactoring, 10 pre-existing Pyright `reportReturnType` diagnostics (`-> dict` returning `ApiResponse`) were discovered and also fixed:

- `src/amz_scout/api.py`: 22 public envelope functions changed from `-> dict` to `-> ApiResponse`. `_auto_fetch` (private, returns raw dict) changed to `-> dict[str, Any]` instead.
- One real-bug-class issue surfaced by the tighter types: `dr["data"].get("asin")` at the old line 1471 assumed `data` was always a dict. Added `isinstance(dr_data, dict)` narrowing — now type-safe and robust against empty-list responses.

### Verification

| Check | Result |
|---|---|
| `pytest -q` full suite | **287 passed, 7 skipped** (identical to pre-fix baseline) |
| Target tests (`TestStatusMigrationV5` + `TestResolveAsinStatusGate`) | **9/9 pass** |
| v5 migration self-assertions | `idx_pa_asin` recreated ✓ ; `'unavailable'` now rejected by `IntegrityError` ✓ ; data survived rebuild ✓ |
| Pyright on `src/amz_scout/api.py` | 10 pre-existing errors → 0 |
| New regressions | 0 |

### Remaining LOW items (deferred — optional follow-up PR)

- `src/amz_scout/db.py:224-228` — v3 migration still declares 5-value CHECK (history-faithful, functionally safe inside atomic transaction). A one-line clarifying comment would be sufficient.
- `_resolve_asin` makes an extra round-trip SELECT — could be removed by having `find_product` also SELECT `status`. Micro-optimization.
- Status strings in tests are bare literals — a `Literal[...]` type alias would catch typos via mypy.

### Final diff footprint

```
src/amz_scout/api.py |  85 ++++++++++++++++++++++----------
src/amz_scout/db.py  |  94 ++++++++++++++++++++++++++++++++++--
tests/test_db.py     | 134 ++++++++++++++++++++++++++++++++++++++++++++++++++-
3 files changed, 281 insertions(+), 32 deletions(-)
```

