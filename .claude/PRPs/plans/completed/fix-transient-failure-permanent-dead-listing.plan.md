# Plan: Fix transient-failure permanent dead-listing (closes #11)

## Summary

A single transient Keepa response (rate-limit, network blip, partial JSON) currently
sets `product_asins.status = 'not_listed'` permanently, blacklisting a live ASIN
with no automated recovery. This plan replaces the one-shot "empty title + no csv"
check with a two-gate decision: (1) only genuine Keepa "no data" responses
(`fetch_error == ""`) are eligible, and (2) `not_listed` requires N consecutive
eligible observations tracked by a new `not_listed_strikes` counter column.
Successful fetches clear the counter.

## User Story

As an amz-scout operator running recurring `ensure_keepa_data` jobs,
I want a single Keepa blip not to permanently blacklist a live ASIN,
so that I can trust the `not_listed` gate to reflect real Amazon delistings
and not transient API failures.

## Problem → Solution

**Current**: `api.py:922` checks `not title and not has_csv` after every fetch.
Any transient Keepa blip (rate-limit, network, partial response) satisfies that
condition and permanently flips status to `not_listed`. The only recovery is a
manual `update_asin_status()` call, which has zero test coverage.

**Target**:
1. `fetch_error != ""` → log WARNING, do nothing to status / counter.
2. `fetch_error == ""` AND `title` empty AND `has_csv` false → increment
   `not_listed_strikes`. Flip to `not_listed` only when strikes ≥ threshold (3).
3. Any fetch that returns a title or csv → reset `not_listed_strikes` to 0.
4. `not_listed → active` recovery path gets explicit test coverage.

## Metadata

- **Complexity**: Medium
- **Source PRD**: N/A (direct fix for GitHub issue #11)
- **PRD Phase**: N/A
- **Estimated Files**: 4 modified, 0 created
  - `src/amz_scout/db.py` — add v8 migration + strike helpers
  - `src/amz_scout/api.py` — rewrite post-fetch validation + replace `_try_mark_not_listed`
  - `tests/test_api.py` — add post-validation strike tests + recovery coverage
  - `docs/DEVELOPER.md` — update ASIN Status Semantics section + migration table

---

## UX Design

### Before

```
┌────────────────────────────────────────────────┐
│ User runs ensure_keepa_data(...) at 09:00      │
│   Keepa returns 429 for B0F2MR53D6             │
│   api.py marks (B0F2MR53D6, UK) = not_listed   │
│                                                │
│ User runs query_trends(..."UK") at 09:05       │
│   ValueError: "ASIN observed delisted on       │
│   Amazon. Run discover_asin() or               │
│   update_asin_status() if misclassified."      │
│                                                │
│ Product is actually live.                      │
│ Recovery requires manual SQL knowledge.        │
└────────────────────────────────────────────────┘
```

### After

```
┌────────────────────────────────────────────────┐
│ User runs ensure_keepa_data(...) at 09:00      │
│   Keepa returns 429 for B0F2MR53D6             │
│   fetch_error="rate_limited" → WARNING only.   │
│   status stays 'active', strikes stays 0.      │
│                                                │
│ User runs query_trends(..."UK") at 09:05       │
│   Cache hit, returns last-known trend data.    │
│                                                │
│ (Hypothetical) 3 consecutive fetches return    │
│ truly empty bodies → flip to 'not_listed'.     │
│ A single successful fetch in between resets    │
│ strikes to 0.                                  │
└────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `ensure_keepa_data` warnings | Single transient blip → "ASIN has no data" + status flipped | Transient blip → "Transient Keepa failure (rate_limited) for …; status unchanged" + status preserved | Distinguishes causes in user-facing text |
| `query_*` failure after one blip | ValueError forever until manual recovery | No ValueError — status still `active` | The primary bug fix |
| Strike accumulation (new) | N/A | "Empty Keepa response for … (strike 2/3); will mark not_listed after 3 consecutive observations" | Makes gradual dead-listing observable |
| Recovery from `not_listed` | Manual SQL | Still manual via `update_asin_status(..., status='active')`, now with test coverage | Issue explicitly flags untested recovery path |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `src/amz_scout/api.py` | 398-420, 882-948 | Exact functions to modify: `_try_mark_not_listed` + `ensure_keepa_data` post-fetch validation. |
| P0 | `src/amz_scout/api.py` | 216-238 | `_check_asin_status_gate` — read side of `not_listed`. Must stay compatible. |
| P0 | `src/amz_scout/db.py` | 171-420 | Migration machinery (v2-v7). v8 follows `if current < N:` + `INSERT OR IGNORE INTO schema_migrations`. |
| P0 | `src/amz_scout/db.py` | 729-744 | Current `product_asins` CREATE TABLE — v8 is ADD COLUMN, no rebuild. |
| P0 | `src/amz_scout/db.py` | 1856-1871 | `update_asin_status` single write entry point. New strike helpers coexist. |
| P1 | `src/amz_scout/keepa_service.py` | 32-62 | `KeepaProductOutcome` — `price_history.fetch_error` is the signal. |
| P1 | `src/amz_scout/models.py` | 68-96 | `PriceHistory.fetch_error: str = ""`. Signal already exists. |
| P1 | `src/amz_scout/scraper/keepa.py` | 131-209 | Where `fetch_error` is populated (rate_limited, invalid_response, api_error, network, unexpected, max_retries_exhausted) + the single "empty" path at line 184. |
| P1 | `tests/test_api.py` | 916-928, 1030-1102 | Existing `TestEnsureKeepaDataPostValidation` + `TestResolveAsinStatusGate`. New tests go next to them. |
| P2 | `docs/DEVELOPER.md` | 101-183 | Migrations table + "ASIN Status Semantics" state diagram. Must reflect v8 + threshold. |
| P2 | `src/amz_scout/db.py` | 344-406 | v6 migration — reference for status-related migration style. |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Keepa `products=[]` response | `scraper/keepa.py:182-184` | When Keepa has no record for an ASIN, `resp.json()["products"]` is empty. Our scraper returns `_empty_history(product, site)` with **empty `fetch_error`** — the single signal distinguishing "Keepa knows nothing" from every other empty path. |
| Keepa HTTP 429 | `scraper/keepa.py:150-167` | Returns `_empty_history(..., fetch_error="rate_limited")` after retries. Always transient. |
| Keepa non-200 / error JSON | `scraper/keepa.py:177-180` | Returns `_empty_history(..., fetch_error=f"api_error: ...")`. Treat as transient. |

No new external libraries required.

---

## Patterns to Mirror

### NAMING_CONVENTION — private helper shape
```python
# SOURCE: src/amz_scout/api.py:398-420
def _try_mark_not_listed(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> None:
    """If this ASIN is in the product registry, mark it as not_listed."""
    try:
        from amz_scout.db import update_asin_status
        row = conn.execute(
            "SELECT product_id FROM product_asins WHERE asin = ? AND marketplace = ?",
            (asin, site),
        ).fetchone()
        if row:
            update_asin_status(
                conn, row["product_id"], site, "not_listed",
                notes="Keepa returned no title or price data",
            )
    except Exception:
        logger.exception("Failed to mark ASIN %s/%s as not_listed", asin, site)
```
Mirror: (1) private underscore name, (2) `try/except Exception` around mutation, (3) `logger.exception(...)` with identifying fields, (4) silent no-op when ASIN isn't in registry.

### SCHEMA_MIGRATION — ADD COLUMN
```python
# SOURCE: src/amz_scout/db.py:254-265 (v4 migration)
if current < 4:
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(keepa_products)")]
    if "fetch_mode" not in cols:
        conn.execute(
            "ALTER TABLE keepa_products "
            "ADD COLUMN fetch_mode TEXT NOT NULL DEFAULT 'basic'"
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) "
        "VALUES (4, 'add fetch_mode to keepa_products')"
    )
    logger.info("Migrated schema to version 4")
```
v8 follows this exact shape. Lives inside the main `with conn:` txn because it does not touch `PRAGMA foreign_keys`.

### STATUS_WRITE — single entry point
```python
# SOURCE: src/amz_scout/db.py:1856-1871
def update_asin_status(
    conn: sqlite3.Connection,
    product_id: int,
    marketplace: str,
    status: str,
    notes: str = "",
) -> None:
    with conn:
        conn.execute(
            "UPDATE product_asins SET status = ?, notes = ?, last_checked = "
            "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE product_id = ? AND marketplace = ?",
            (status, notes, product_id, marketplace),
        )
```
DEVELOPER.md:170-173 calls this "the single write entry point". New strike helpers may update `not_listed_strikes` alone, but any transition that flips `status` to `not_listed` MUST still route through `update_asin_status()`.

### WARNING_ACCUMULATION
```python
# SOURCE: src/amz_scout/api.py:904-948
warnings: list[str] = []
fetched_outcomes = [o for o in result.outcomes if o.source == "fetched"]
if fetched_outcomes:
    # build title_map...
    for o in fetched_outcomes:
        title = title_map.get((o.asin, o.site), "")
        has_csv = o.price_history and (
            o.price_history.buybox_current is not None
            or o.price_history.new_current is not None
        )
        if not title and not has_csv:
            warnings.append(...)
            _try_mark_not_listed(conn, o.asin, o.site)

meta: dict = {...}
if warnings:
    meta["warnings"] = warnings
return _envelope(True, data={"outcomes": outcomes}, **meta)
```
Preserve this accumulator shape + `meta["warnings"]` envelope surface.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_api.py:1030-1054
class TestResolveAsinStatusGate:
    def _setup_db(self, tmp_path):
        db_path = tmp_path / "status_gate.db"
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        init_schema(c)
        pid, _ = register_product(c, "Router", "TestBrand", "TestModel")
        register_asin(c, pid, "UK", "B0DEADXXX1", status="not_listed", notes="")
        register_asin(c, pid, "FR", "B0GOOD0001", status="active", notes="")
        c.close()
        return db_path

    def test_raises_on_not_listed_asin_pass_through(self, tmp_path):
        db_path = self._setup_db(tmp_path)
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="not_listed"):
            _resolve_asin([], "B0DEADXXX1", marketplace="UK", conn=c)
        c.close()
```
Mirror per-test DB setup + `register_product` + `register_asin` + `pytest.raises(..., match=...)` + explicit `c.close()`.

### MONKEYPATCH
```python
# SOURCE: tests/test_api.py:1079-1102
def test_query_envelope_failure_for_not_listed(self, tmp_path, monkeypatch):
    from pathlib import Path as _Path
    import amz_scout.api as api_mod
    def fake_ctx(*args, **kwargs):
        return _Path(str(db_path)), None
    monkeypatch.setattr(api_mod, "_resolve_context", fake_ctx)
    r = query_trends(product="B0DEADXXX1", marketplace="UK", auto_fetch=False)
```
For the integration test, monkey-patch `api_mod.get_keepa_data` (imported into api from keepa_service) with a fake `KeepaResult`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | Bump `SCHEMA_VERSION` 7→8. Add v8 migration (ADD COLUMN `not_listed_strikes INTEGER NOT NULL DEFAULT 0`). Add column to baseline `CREATE TABLE product_asins` too. Add `increment_not_listed_strikes()` + `clear_not_listed_strikes()` next to `update_asin_status`. Define `NOT_LISTED_STRIKE_THRESHOLD = 3`. |
| `src/amz_scout/api.py` | UPDATE | Rewrite the post-fetch loop to branch on `fetch_error`. Replace `_try_mark_not_listed` with `_record_empty_observation` (increments, flips above threshold) and `_record_successful_observation` (resets strikes). Only one caller exists (line 930). |
| `tests/test_api.py` | UPDATE | Extend `TestEnsureKeepaDataPostValidation` + add `TestEmptyObservationStrikes`, `TestEnsureKeepaDataTransientVsPermanent`, `TestAsinStatusRecovery`. |
| `docs/DEVELOPER.md` | UPDATE | Append v8 row. Update status state diagram to show strike accumulation + transient path. Document threshold constant. |

## NOT Building

- **NOT** changing `status` enum values. Stays `'active' | 'not_listed'`.
- **NOT** adding `'suspected_not_listed'` as a user-visible state. Strikes are an internal counter.
- **NOT** touching `_check_asin_status_gate` / `_resolve_asin` / `load_products_from_db` read paths.
- **NOT** adding a `not_listed → active` background recovery job. Manual recovery + tests only.
- **NOT** using `keepa_products.availability_amazon` in this PR — not populated when `products=[]`. Noted as follow-up.
- **NOT** bundling in async/webapp/auto_fetch_error issues — shipped in PR #18.
- **NOT** changing the CLI surface or adding user commands.

---

## Step-by-Step Tasks

### Task 1: Schema — add `not_listed_strikes` column + v8 migration

- **ACTION**: Edit `src/amz_scout/db.py`.
- **IMPLEMENT**:
  1. Line 105: `SCHEMA_VERSION = 7` → `SCHEMA_VERSION = 8`.
  2. Add below it:
     ```python
     NOT_LISTED_STRIKE_THRESHOLD = 3
     ```
  3. In baseline `_SCHEMA_SQL`'s `CREATE TABLE product_asins` (around line 729), add after `last_checked TEXT,`:
     ```sql
     not_listed_strikes  INTEGER NOT NULL DEFAULT 0,
     ```
     Update the adjacent comment block to explain: "Consecutive-empty counter (transient-failure guard — flip to not_listed only at NOT_LISTED_STRIKE_THRESHOLD)."
  4. Inside `_migrate()`, after the v6 block and before the `# v7 lives OUTSIDE` comment:
     ```python
     if current < 8:
         cols = [
             r["name"]
             for r in conn.execute("PRAGMA table_info(product_asins)")
         ]
         if "not_listed_strikes" not in cols:
             conn.execute(
                 "ALTER TABLE product_asins "
                 "ADD COLUMN not_listed_strikes INTEGER NOT NULL DEFAULT 0"
             )
         conn.execute(
             "INSERT OR IGNORE INTO schema_migrations (version, description) "
             "VALUES (8, 'add not_listed_strikes counter to product_asins')"
         )
         logger.info("Migrated schema to version 8")
     ```
- **MIRROR**: `SCHEMA_MIGRATION` (v4, db.py:254-265).
- **IMPORTS**: None new.
- **GOTCHA**:
  - Must bump `SCHEMA_VERSION` or the early-return at line 176-177 skips v8.
  - `_schema_initialized` cache at line 146 uses DB file path; tests use fresh `tmp_path` so cache is not an issue.
  - `ALTER TABLE ADD COLUMN ... NOT NULL DEFAULT 0` applies the default to existing rows — no backfill.
- **VALIDATE**: See "Migration Validation" in Validation Commands.

### Task 2: DB helpers — strike counter mutations

- **ACTION**: Edit `src/amz_scout/db.py`, add two functions next to `update_asin_status` (around line 1856).
- **IMPLEMENT**:
  ```python
  def increment_not_listed_strikes(
      conn: sqlite3.Connection,
      product_id: int,
      marketplace: str,
  ) -> int:
      """Increment the empty-observation strike counter. Returns new value.

      Does NOT flip ``status``. Callers that cross
      :data:`NOT_LISTED_STRIKE_THRESHOLD` should follow up with
      :func:`update_asin_status` to preserve ``last_checked`` bookkeeping.
      """
      with conn:
          conn.execute(
              "UPDATE product_asins SET "
              "not_listed_strikes = not_listed_strikes + 1, "
              "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
              "WHERE product_id = ? AND marketplace = ?",
              (product_id, marketplace),
          )
          row = conn.execute(
              "SELECT not_listed_strikes FROM product_asins "
              "WHERE product_id = ? AND marketplace = ?",
              (product_id, marketplace),
          ).fetchone()
      return int(row["not_listed_strikes"]) if row else 0


  def clear_not_listed_strikes(
      conn: sqlite3.Connection,
      product_id: int,
      marketplace: str,
  ) -> None:
      """Reset the empty-observation strike counter to 0.

      Called on any fetch that returns a non-empty title or csv.
      """
      with conn:
          conn.execute(
              "UPDATE product_asins SET "
              "not_listed_strikes = 0, "
              "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
              "WHERE product_id = ? AND marketplace = ? "
              "AND not_listed_strikes != 0",
              (product_id, marketplace),
          )
  ```
- **MIRROR**: `update_asin_status` shape (db.py:1856-1871). SELECT-after-UPDATE inside same `with conn:` block (no `RETURNING`, to match existing db.py style).
- **IMPORTS**: None new.
- **GOTCHA**:
  - `AND not_listed_strikes != 0` guard in `clear_*` avoids `updated_at` churn on the hot path.
- **VALIDATE**: Task 5 unit tests.

### Task 3: API — split `_try_mark_not_listed` into observation helpers

- **ACTION**: Edit `src/amz_scout/api.py`. Replace `_try_mark_not_listed` (lines 398-420).
- **IMPLEMENT**:
  ```python
  def _record_empty_observation(
      conn: sqlite3.Connection,
      asin: str,
      site: str,
  ) -> tuple[int, bool]:
      """Record a Keepa 'no data' observation for a registered ASIN.

      Returns ``(new_strike_count, flipped_to_not_listed)``. Unregistered
      ASINs are a no-op — we only gate registered ones.
      """
      try:
          from amz_scout.db import (
              NOT_LISTED_STRIKE_THRESHOLD,
              increment_not_listed_strikes,
              update_asin_status,
          )
          row = conn.execute(
              "SELECT product_id, status FROM product_asins "
              "WHERE asin = ? AND marketplace = ?",
              (asin, site),
          ).fetchone()
          if not row:
              return (0, False)
          strikes = increment_not_listed_strikes(conn, row["product_id"], site)
          if row["status"] == "active" and strikes >= NOT_LISTED_STRIKE_THRESHOLD:
              update_asin_status(
                  conn,
                  row["product_id"],
                  site,
                  "not_listed",
                  notes=(
                      f"Keepa returned empty data {strikes}x in a row "
                      "(transient-failure guard threshold reached)"
                  ),
              )
              return (strikes, True)
          return (strikes, False)
      except Exception:
          logger.exception(
              "Failed to record empty observation for ASIN %s/%s", asin, site,
          )
          return (0, False)


  def _record_successful_observation(
      conn: sqlite3.Connection,
      asin: str,
      site: str,
  ) -> None:
      """Reset the strike counter after a Keepa fetch returned data."""
      try:
          from amz_scout.db import clear_not_listed_strikes
          row = conn.execute(
              "SELECT product_id FROM product_asins "
              "WHERE asin = ? AND marketplace = ?",
              (asin, site),
          ).fetchone()
          if row:
              clear_not_listed_strikes(conn, row["product_id"], site)
      except Exception:
          logger.exception(
              "Failed to clear empty-observation strikes for %s/%s", asin, site,
          )
  ```
  Delete the old `_try_mark_not_listed` after Task 4 removes its only caller.
- **MIRROR**: `_try_mark_not_listed` shape (api.py:398-420) — lazy imports, `try/except`, `logger.exception`, no-op when unregistered.
- **IMPORTS**: None at module scope (lazy-import style).
- **GOTCHA**:
  - Threshold gate is `status == 'active' AND strikes >= THRESHOLD` — already-`not_listed` rows silently increment their counter without note churn.
  - `_record_successful_observation` is cheap even when strikes already 0 (guarded by `clear_not_listed_strikes`).
- **VALIDATE**: Task 5 unit tests.

### Task 4: API — rewrite `ensure_keepa_data` post-fetch validation

- **ACTION**: Edit `src/amz_scout/api.py` lines 904-930.
- **IMPLEMENT** (replacing the current loop body):
  ```python
  from amz_scout.db import NOT_LISTED_STRIKE_THRESHOLD as _STRIKE_THRESHOLD

  for o in fetched_outcomes:
      ph = o.price_history
      fetch_error = ph.fetch_error if ph else ""
      title = title_map.get((o.asin, o.site), "")
      has_csv = ph and (
          ph.buybox_current is not None or ph.new_current is not None
      )

      if fetch_error:
          # Transient Keepa failure — do NOT touch status or strikes.
          warnings.append(
              f"{o.model} / {o.site} ({o.asin}): "
              f"Transient Keepa failure ({fetch_error}); "
              "status unchanged, cached data (if any) still valid."
          )
          continue

      if not title and not has_csv:
          strikes, flipped = _record_empty_observation(conn, o.asin, o.site)
          brand = o.freshness.brand
          if flipped:
              warnings.append(
                  f"{o.model} / {o.site} ({o.asin}): "
                  f"Marked not_listed after {strikes} consecutive empty "
                  "responses. Run discover_asin("
                  f"'{brand}', '{o.model}', '{o.site}') for a valid ASIN, "
                  "or update_asin_status(status='active') if re-listed."
              )
          elif strikes > 0:
              warnings.append(
                  f"{o.model} / {o.site} ({o.asin}): "
                  f"Empty Keepa response (strike {strikes}/"
                  f"{_STRIKE_THRESHOLD}); status unchanged. "
                  "Will mark not_listed after consecutive threshold."
              )
          else:
              # Unregistered ASIN — preserve legacy actionable warning.
              warnings.append(
                  f"{o.model} / {o.site} ({o.asin}): "
                  "ASIN has no data — likely wrong or not listed. "
                  f"Call discover_asin('{brand}', '{o.model}', "
                  f"'{o.site}') to search for the correct ASIN."
              )
          continue

      # Fetch returned data — reset any in-flight strike streak.
      _record_successful_observation(conn, o.asin, o.site)
  ```
  Use a lazy local import for `_STRIKE_THRESHOLD`, mirroring the existing lazy-import style.
- **MIRROR**: `WARNING_ACCUMULATION` (api.py:904-948). Envelope shape unchanged.
- **IMPORTS**: `from amz_scout.db import NOT_LISTED_STRIKE_THRESHOLD as _STRIKE_THRESHOLD` (lazy, inside the function).
- **GOTCHA**:
  - Preserve the unregistered-ASIN warning text so operators pasting a bad ASIN still get actionable feedback.
  - `_record_successful_observation` only runs in the "fetch returned data" arm.
  - The `has_csv` truthiness expression currently short-circuits on `None` price_history — keep the exact shape.
  - Grep-verify: the only caller of `_try_mark_not_listed` is this block. Delete the function after replacing.
- **VALIDATE**: Task 5 integration test.

### Task 5: Tests

- **ACTION**: Edit `tests/test_api.py`. Add three classes.
- **IMPLEMENT**:

  ```python
  class TestEmptyObservationStrikes:
      """Strike counter behaviour for _record_empty_observation."""

      def _seed(self, tmp_path, asin="B0STRIKE01", status="active"):
          from amz_scout.db import init_schema, register_asin, register_product
          db_path = tmp_path / "strikes.db"
          c = sqlite3.connect(str(db_path))
          c.row_factory = sqlite3.Row
          init_schema(c)
          pid, _ = register_product(c, "Router", "BrandX", "ModelX")
          register_asin(c, pid, "UK", asin, status=status, notes="")
          return c, pid

      def test_unregistered_asin_is_noop(self, tmp_path):
          from amz_scout.api import _record_empty_observation
          c, _ = self._seed(tmp_path)
          strikes, flipped = _record_empty_observation(c, "B0UNREG0001", "UK")
          assert (strikes, flipped) == (0, False)
          c.close()

      def test_first_empty_increments_only(self, tmp_path):
          from amz_scout.api import _record_empty_observation
          c, pid = self._seed(tmp_path)
          strikes, flipped = _record_empty_observation(c, "B0STRIKE01", "UK")
          assert strikes == 1
          assert flipped is False
          row = c.execute(
              "SELECT status, not_listed_strikes FROM product_asins "
              "WHERE product_id = ? AND marketplace = 'UK'", (pid,),
          ).fetchone()
          assert row["status"] == "active"
          assert row["not_listed_strikes"] == 1
          c.close()

      def test_threshold_flips_to_not_listed(self, tmp_path):
          from amz_scout.api import _record_empty_observation
          from amz_scout.db import NOT_LISTED_STRIKE_THRESHOLD
          c, pid = self._seed(tmp_path)
          for _ in range(NOT_LISTED_STRIKE_THRESHOLD - 1):
              _record_empty_observation(c, "B0STRIKE01", "UK")
          strikes, flipped = _record_empty_observation(c, "B0STRIKE01", "UK")
          assert strikes == NOT_LISTED_STRIKE_THRESHOLD
          assert flipped is True
          row = c.execute(
              "SELECT status FROM product_asins "
              "WHERE product_id = ? AND marketplace = 'UK'", (pid,),
          ).fetchone()
          assert row["status"] == "not_listed"
          c.close()

      def test_successful_observation_resets_strikes(self, tmp_path):
          from amz_scout.api import (
              _record_empty_observation, _record_successful_observation,
          )
          c, pid = self._seed(tmp_path)
          _record_empty_observation(c, "B0STRIKE01", "UK")
          _record_empty_observation(c, "B0STRIKE01", "UK")
          _record_successful_observation(c, "B0STRIKE01", "UK")
          row = c.execute(
              "SELECT not_listed_strikes FROM product_asins "
              "WHERE product_id = ? AND marketplace = 'UK'", (pid,),
          ).fetchone()
          assert row["not_listed_strikes"] == 0
          c.close()

      def test_already_not_listed_does_not_rewrite_notes(self, tmp_path):
          from amz_scout.api import _record_empty_observation
          c, _ = self._seed(tmp_path, status="not_listed")
          strikes, flipped = _record_empty_observation(c, "B0STRIKE01", "UK")
          assert flipped is False
          c.close()


  class TestEnsureKeepaDataTransientVsPermanent:
      def test_transient_blip_preserves_status(self, config_dir, monkeypatch):
          """fetch_error != '' must not touch status or strikes."""
          import amz_scout.api as api_mod
          from amz_scout.db import register_asin, register_product
          from amz_scout.keepa_service import KeepaProductOutcome, KeepaResult
          from amz_scout.freshness import ProductFreshness
          from amz_scout.models import PriceHistory

          tmp_path, proj_path = config_dir
          db_path = tmp_path / "output" / "amz_scout.db"
          c = sqlite3.connect(str(db_path))
          c.row_factory = sqlite3.Row
          pid, _ = register_product(c, "Router", "BrandY", "ModelY")
          register_asin(c, pid, "UK", "B0TRANS0001", status="active", notes="")
          c.close()

          def fake_get_keepa_data(conn, products, sites, marketplaces, **_):
              pf = ProductFreshness(
                  asin="B0TRANS0001", site="UK", brand="BrandY",
                  model="ModelY", action="needs_fetch", age_days=None,
                  fetched_at=None, mode=None,
              )
              ph = PriceHistory(
                  date="2026-04-21", site="UK", category="Router",
                  brand="BrandY", model="ModelY", asin="B0TRANS0001",
                  fetch_error="rate_limited",
              )
              outcome = KeepaProductOutcome(
                  asin="B0TRANS0001", site="UK", model="ModelY",
                  source="fetched", price_history=ph, freshness=pf,
              )
              return KeepaResult(outcomes=[outcome], tokens_used=0,
                                 tokens_remaining=60)

          monkeypatch.setattr(api_mod, "get_keepa_data", fake_get_keepa_data)
          r = ensure_keepa_data(proj_path, marketplace="UK")
          assert r["ok"] is True
          c = sqlite3.connect(str(db_path)); c.row_factory = sqlite3.Row
          row = c.execute(
              "SELECT status, not_listed_strikes FROM product_asins "
              "WHERE asin = 'B0TRANS0001' AND marketplace = 'UK'"
          ).fetchone()
          assert row["status"] == "active"
          assert row["not_listed_strikes"] == 0
          assert any("Transient Keepa failure" in w
                     for w in r["meta"]["warnings"])
          c.close()


  class TestAsinStatusRecovery:
      """Manual not_listed -> active recovery via update_asin_status."""

      def test_update_asin_status_restores_active(self, tmp_path):
          from amz_scout.db import (
              init_schema, register_asin, register_product, update_asin_status,
          )
          from amz_scout.api import _resolve_asin
          db_path = tmp_path / "recovery.db"
          c = sqlite3.connect(str(db_path)); c.row_factory = sqlite3.Row
          init_schema(c)
          pid, _ = register_product(c, "Router", "B", "M")
          register_asin(c, pid, "UK", "B0RECOV0001",
                        status="not_listed", notes="")
          with pytest.raises(ValueError, match="not_listed"):
              _resolve_asin([], "B0RECOV0001", marketplace="UK", conn=c)
          update_asin_status(c, pid, "UK", "active",
                             notes="operator confirmed re-listed")
          asin, *_ = _resolve_asin([], "B0RECOV0001",
                                   marketplace="UK", conn=c)
          assert asin == "B0RECOV0001"
          c.close()
  ```
- **MIRROR**: `TestResolveAsinStatusGate` (test_api.py:1030-1102) + monkey-patch pattern.
- **IMPORTS**: Use existing test imports; add `NOT_LISTED_STRIKE_THRESHOLD` via lazy `from amz_scout.db import`.
- **GOTCHA**:
  - Verify `ProductFreshness` / `PriceHistory` field names via `grep -n "@dataclass" src/amz_scout/freshness.py src/amz_scout/models.py` before finalizing fixtures — dataclass kwargs must match exactly.
  - Monkey-patch `api_mod.get_keepa_data` (the symbol imported into api.py), not `keepa_service.get_keepa_data`.
- **VALIDATE**: `pytest tests/test_api.py -k "Strike or Recovery or Transient" -v` green.

### Task 6: Docs — DEVELOPER.md

- **ACTION**: Edit `docs/DEVELOPER.md`.
- **IMPLEMENT**:
  1. Append after line 112 (v7 row):
     ```
     | 8 | Transient-failure guard | Add `not_listed_strikes` counter to `product_asins`; require N consecutive genuine empty observations before flipping to `not_listed` |
     ```
  2. Replace the state diagram (lines 163-168):
     ```mermaid
     stateDiagram-v2
         [*] --> active: register / discover / auto-register
         active --> active: transient fetch_error → log only
         active --> active: genuine empty, strikes < N → increment counter
         active --> not_listed: N consecutive genuine empty responses
         active --> active: any fetch with data → strikes reset to 0
         not_listed --> active: manual recovery via update_asin_status
     ```
  3. Under "Query Gate" (around line 155), add a short paragraph describing `NOT_LISTED_STRIKE_THRESHOLD = 3`, the `fetch_error` signal from `PriceHistory`, and the distinction between transient and genuine empty.
- **MIRROR**: Existing migrations table + mermaid diagram style.
- **IMPORTS**: N/A.
- **GOTCHA**: Append only — do not renumber historical rows.
- **VALIDATE**: `grep -n "^| 8 |" docs/DEVELOPER.md` returns the new row.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected | Edge Case? |
|---|---|---|---|
| `test_unregistered_asin_is_noop` | `_record_empty_observation` on empty registry | `(0, False)` | ✓ |
| `test_first_empty_increments_only` | Registered ASIN, one empty | strikes=1, active | ✓ |
| `test_threshold_flips_to_not_listed` | Registered ASIN, N empty | strikes=N, not_listed, flipped=True | ✓ |
| `test_successful_observation_resets_strikes` | 2 empties + 1 success | strikes=0, active | ✓ |
| `test_already_not_listed_does_not_rewrite_notes` | already `not_listed`, one empty | flipped=False | ✓ |
| `test_transient_blip_preserves_status` | `fetch_error="rate_limited"` | status=active, strikes=0, "Transient" warning | ✓ (the bug itself) |
| `test_update_asin_status_restores_active` | not_listed → `update_asin_status("active")` | `_resolve_asin` no longer raises | ✓ (#11 recovery gap) |

### Edge Cases Checklist
- [x] Unregistered ASIN — early return, no crash.
- [x] Already `not_listed` ASIN — strikes increment, no status churn.
- [x] Concurrent fetch race — SQLite file lock serializes updates; accepted.
- [x] `price_history is None` on a fetched outcome — defensive guard.
- [x] `fetch_error == "max_retries_exhausted"` — classified as transient (documented).
- [x] Fresh DB — baseline schema has the column, no migration runs.
- [x] v7 DB — `ALTER TABLE ADD COLUMN` appends with default 0.
- [x] Already-v8 DB — idempotent (PRAGMA guard + INSERT OR IGNORE).

---

## Validation Commands

### Static Analysis
```bash
ruff check src/ tests/
```
EXPECT: zero lint errors (`line-length = 100`).

```bash
python -c "import amz_scout.api, amz_scout.db; print('import ok')"
```
EXPECT: `import ok` — catches circular-import regressions.

### Unit Tests
```bash
pytest tests/test_api.py -v
pytest tests/test_db.py -v
```
EXPECT: all green including 7 new tests.

### Full Suite
```bash
pytest --cov=src/amz_scout --cov-report=term-missing
```
EXPECT: no regressions; coverage on new helpers ≥ 90%.

### DB Schema Validation
```bash
python - <<'PY'
import sqlite3, tempfile, pathlib
from amz_scout.db import init_schema, SCHEMA_VERSION
p = pathlib.Path(tempfile.mkdtemp()) / "check.db"
c = sqlite3.connect(str(p)); c.row_factory = sqlite3.Row
init_schema(c)
cols = {r["name"] for r in c.execute("PRAGMA table_info(product_asins)")}
assert "not_listed_strikes" in cols, cols
ver = c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
assert ver == SCHEMA_VERSION == 8, (ver, SCHEMA_VERSION)
print("schema v8 ok")
PY
```
EXPECT: `schema v8 ok`.

### Migration Validation (v7 → v8)
```bash
python - <<'PY'
import sqlite3, pathlib, tempfile
p = pathlib.Path(tempfile.mkdtemp()) / "legacy.db"
c = sqlite3.connect(str(p))
c.executescript("""
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, description TEXT,
  applied_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')));
INSERT INTO schema_migrations (version, description) VALUES (7, 'legacy');
CREATE TABLE products (id INTEGER PRIMARY KEY, category TEXT, brand TEXT,
  model TEXT, brand_key TEXT, model_key TEXT, search_keywords TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT, UNIQUE(brand_key, model_key));
CREATE TABLE product_asins (product_id INTEGER, marketplace TEXT, asin TEXT,
  status TEXT DEFAULT 'active' CHECK(status IN ('active','not_listed')),
  notes TEXT DEFAULT '', last_checked TEXT,
  created_at TEXT, updated_at TEXT,
  PRIMARY KEY(product_id, marketplace));
""")
c.commit(); c.close()

from amz_scout.db import init_schema
c = sqlite3.connect(str(p)); c.row_factory = sqlite3.Row
init_schema(c)
cols = {r["name"] for r in c.execute("PRAGMA table_info(product_asins)")}
assert "not_listed_strikes" in cols
print("v7->v8 migration ok")
PY
```
EXPECT: `v7->v8 migration ok`.

### Manual Validation
- [ ] DB with one `(active, strikes=0)` row.
- [ ] Call `_record_empty_observation` three times — strikes 1, 2, 3 and status flip on third.
- [ ] `update_asin_status(..., "active")` — `_resolve_asin` no longer raises.
- [ ] Webapp warnings string reads naturally for operators.

---

## Acceptance Criteria
- [ ] Schema version 8; column `not_listed_strikes` exists.
- [ ] Transient fetch errors never touch `status` or `not_listed_strikes`.
- [ ] Genuine empty responses increment the counter; flip only at threshold.
- [ ] Successful fetches reset counter to 0.
- [ ] `not_listed → active` recovery has an explicit test.
- [ ] All validation commands pass.
- [ ] No regressions in `TestResolveAsinStatusGate`.
- [ ] DEVELOPER.md migrations table + diagram updated.

## Completion Checklist
- [ ] Lazy imports, `with conn:`, `logger.exception` patterns followed.
- [ ] `try/except Exception` around optional mutations.
- [ ] Module-level `logger = logging.getLogger(__name__)` already present.
- [ ] Tests use `TestResolveAsinStatusGate` seeding style.
- [ ] Threshold centralised as `NOT_LISTED_STRIKE_THRESHOLD` constant.
- [ ] Docs updated.
- [ ] No scope creep (no new status values, no background jobs, no CLI flags).
- [ ] Self-contained — no open questions.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `_schema_initialized` cache skips v8 on stale worker | Low | Medium | Cache key is file path; migration idempotent. |
| Threshold N=3 miscalibrated | Medium | Low | One-line constant change; documented. |
| Concurrent callers race on `(product_id, marketplace)` | Low | Low | SQLite file lock serializes; double-increment is the intended semantic. |
| Existing stale `not_listed` rows don't auto-recover | Certain | Medium | Out of scope; manual recovery documented; follow-up issue suggested. |

## Notes
- `availability_amazon == -1` is absent when `products=[]`, so it can't accelerate
  the transient-blip case. Follow-up: use it to lower N for ASINs Keepa explicitly
  reports as unavailable.
- PR body should read `Closes #11` — safe usage of the auto-close keyword
  because this PR actually closes the issue. (Per memory
  `feedback_github_close_keywords`.)
