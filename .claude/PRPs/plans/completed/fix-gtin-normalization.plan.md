# Plan: Fix GTIN UPC-12 / EAN-13 Normalization (closes #12)

## Summary

Keepa emits the same physical barcode in two formats across marketplaces
— `upcList` carries UPC-12 (`"850018166010"`) and `eanList` carries EAN-13
(`"0850018166010"`, same digits + leading zero). `_find_product_by_ean`
compares the two lists with a raw-string `set()` union, so a US ASIN and
an EU ASIN for the same physical product miss each other and fall through
to the brand+title path, producing duplicate `product_id` rows. Fix by
normalizing every EAN/UPC code to canonical **GTIN-13** (digits-only,
left-zero-padded to 13) on both the read path (`_find_product_by_ean`)
and the write path (`store_keepa_product`), and backfill existing
`keepa_products.ean_list` / `upc_list` JSON arrays via a v8 migration
so the SQL `json_each` matching still sees comparable values.

## User Story

As an operator tracking a product that Amazon US sells under UPC-12
(`"850018166010"`) and Amazon DE sells under EAN-13 (`"0850018166010"`),
I want `sync_registry_from_keepa` and auto-registration to recognise
them as the same physical product, so that `query_compare` returns
both markets under one `product_id` instead of silently splitting the
record in two.

## Problem → Solution

**Current**: `ean_list + upc_list` union uses raw strings → UPC-12 vs
EAN-13 of the same GTIN never match → cross-format cross-market binding
falls through to brand+title → `product_id` explosion → `query_compare`
can only see one side.

**Desired**: Every EAN/UPC value is normalized to zero-padded 13-digit
GTIN on write and read. `json_each(kp.ean_list)` / `json_each(kp.upc_list)`
return the same canonical form the caller is searching for. Existing
rows are one-off backfilled by a v8 migration.

## Metadata

- **Complexity**: Small
- **Source PRD**: N/A (direct fix for issue #12; referenced historically
  from `completed/brand-model-normalization.plan.md` as "Task 8" and
  from `completed/register-product-toctou-fix.plan.md` as "M1 tracked
  separately")
- **PRD Phase**: N/A
- **Estimated Files**: 3 (`src/amz_scout/db.py`, `tests/test_db.py`, and
  optionally `docs/DEVELOPER.md` for the migration entry)

---

## UX Design

Internal change — no user-facing UX transformation.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| US ASIN + EU ASIN of same physical product (cross-format GTIN) arrive via `ensure_keepa_data` | 2 separate `product_id` rows, `query_compare` can only see one | 1 `product_id` row, `query_compare` returns both | Matches commit `91d1d52`'s original EAN/UPC-binding promise |
| `sync_registry_from_keepa` on existing v7 DB (pre-fix ASINs already stored with raw UPC/EAN strings) | Matches only intra-format (EAN↔EAN, UPC↔UPC) | Matches cross-format after one-shot v8 backfill | Existing duplicate `product_id` rows are **not** merged automatically — out of scope; see NOT Building |
| Direct string match on `keepa_products.upc_list` via ad-hoc SQL | Values were raw as-received | Values are canonical GTIN-13 | Operators running ad-hoc SQL should be aware; noted in DEVELOPER.md |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `src/amz_scout/db.py` | 850-902 | `_find_product_by_ean` — the function to fix |
| **P0** | `src/amz_scout/db.py` | 860-864 | Exact lines quoted in issue #12 — the raw-string `set()` union |
| **P0** | `src/amz_scout/db.py` | 996-1079 | `store_keepa_product` upsert — the write path that must normalize before `_json_or_none` |
| **P0** | `src/amz_scout/db.py` | 1671-1712 | `sync_registry_from_keepa` — re-reads stored `ean_list` / `upc_list` and feeds them back into `_find_product_by_ean`; the backfill must make this path self-consistent |
| **P0** | `src/amz_scout/db.py` | 105 & 171-419 | `SCHEMA_VERSION` constant + `_migrate` — v8 must bump version, register a `schema_migrations` row, and run inside the existing `with conn:` txn block (it only rewrites JSON content; no `PRAGMA foreign_keys` toggle needed) |
| **P0** | `src/amz_scout/db.py` | 827-847 | `_safe_json_list` + `_normalize_key` — style reference for a tiny module-private helper with ≤1-line docstring and module placement |
| P1 | `src/amz_scout/db.py` | 422-552 | `_migrate_to_v7` — structural precedent for a complex migration; v8 is **much** simpler (no table rebuild), but reuse the same "merge-and-log" discipline for malformed rows |
| P1 | `tests/test_db.py` | 28-37 | `conn` fixture — how tests get a schema-initialized in-memory DB |
| P1 | `tests/test_db.py` | 869-948 | `TestFindProductByEanBrandGuardV7` — closest existing behavior test; new test class mirrors its structure |
| P1 | `tests/test_db.py` | 884-897 | `_seed_keepa_row` helper — the canonical way to insert a Keepa row with an EAN list for testing |
| P1 | `tests/test_db.py` | 592-650 | `test_v7_migrates_v6_db_and_merges_duplicates` — structural precedent for a migration-backfill test using `tmp_path` + forced downgrade |
| P2 | `docs/DEVELOPER.md` | 180-200 (EAN binding section) | Short mention of `_find_product_by_ean`; optional one-line "values are canonical GTIN-13 since v8" footnote |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| GTIN canonical form | GS1 — https://www.gs1.org/standards/id-keys/gtin | GTIN-13 (13 digits, zero-padded from GTIN-12/UPC-12) is GS1's recommended canonical internal representation. GTIN-14 outer-case barcodes are not ASIN-carried; dropped via `len(digits) > 13` guard. |
| UPC-12 ↔ EAN-13 | GS1 migration notes | Prepending `"0"` converts UPC-12 to EAN-13; no check-digit change. Confirms issue #12 example (`"850018166010"` → `"0850018166010"`). |
| SQLite `json_each` | https://sqlite.org/json1.html#jeach | Matches by exact string equality against `value`. Normalizing both the query list (in `_find_product_by_ean`) and the stored JSON (via write path + v8 migration) is sufficient; no SQL changes needed. |

### Research Notes

```
KEY_INSIGHT: Normalizing only at read time is insufficient. The SQL
uses `json_each(kp.ean_list) WHERE value IN (?, ?, ...)` — the right-
hand side is the caller's *already-normalized* list, but the left-
hand side is the stored JSON. If old rows stored `"850018166010"`
(UPC-12), a query normalized to `"0850018166010"` still misses.
APPLIES_TO: The decision to backfill via v8 migration.
GOTCHA: Don't skip the migration — the caller-side normalization
alone leaves a silent gap for any ASIN ingested before the fix.

KEY_INSIGHT: `_json_or_none(raw.get("eanList"))` currently stores
the list verbatim. Normalizing at write time means transforming
the list before passing it to `_json_or_none`. Keep the helper
module-private (prefix `_`) so we don't widen the public surface.
APPLIES_TO: Write path at db.py:1060-1061.

KEY_INSIGHT: Some Keepa rows have `eanList: null` or `upcList: null`.
`raw.get("eanList") or []` already handles that. `_normalize_gtin`
must also tolerate `None`, empty string, and non-digit garbage by
returning `""`, which the caller drops via `codes.discard("")`.
APPLIES_TO: Helper contract + `_find_product_by_ean` call site.

KEY_INSIGHT: Keepa occasionally emits codes with trailing
whitespace or hyphens (rare, but observed). Stripping to digits-
only covers both. `len(digits) > 13` is preserved as a guard — a
14-digit GTIN-14 (outer-case barcode) is not what Amazon consumer-
product ASINs carry, and silently zero-padding something longer
than 13 would corrupt the identity. Drop them instead.
APPLIES_TO: Helper contract. Logged at DEBUG when observed to
aid ingestion diagnostics.

KEY_INSIGHT: v8 migration only rewrites the JSON content of two
TEXT columns. No DDL change, no index change, no FK toggle. It
runs inside the existing `with conn:` block (unlike v7 which had
to live outside). This is why v8 is Small complexity even though
it's a migration.
APPLIES_TO: Migration design — avoid over-engineering.
```

---

## Patterns to Mirror

Code patterns discovered in the codebase. Follow these exactly.

### MODULE_PRIVATE_HELPER
```python
# SOURCE: src/amz_scout/db.py:827-847
def _safe_json_list(s: str | None) -> list:
    """Parse a JSON string as a list, returning [] on None or malformed input."""
    if not s:
        return []
    try:
        return json_mod.loads(s)
    except (json_mod.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON list in DB: %r", s)
        return []


def _normalize_key(s: str | None) -> str:
    """Normalize a brand/model string for identity matching.

    Lowercases, strips surrounding whitespace, and folds any internal
    whitespace runs (spaces, tabs) into a single space. Used as the
    uniqueness basis for ``products.brand_key`` / ``products.model_key``
    ...
    """
    return " ".join((s or "").lower().split())
```
Mirror: single-responsibility, `None`-tolerant, module-private (underscore
prefix), one-sentence-to-one-paragraph docstring explaining *what* and
*why* the normalization exists. Place `_normalize_gtin` adjacent to
`_normalize_key` in the same "private helpers" band (around line 848).

### EAN_READ_PATH
```python
# SOURCE: src/amz_scout/db.py:862-864
ean_list = raw.get("eanList") or []
upc_list = raw.get("upcList") or []
codes = set(ean_list + upc_list)
```
Mirror for the fix:
```python
ean_list = raw.get("eanList") or []
upc_list = raw.get("upcList") or []
codes = {_normalize_gtin(c) for c in (ean_list + upc_list)}
codes.discard("")
```

### EAN_WRITE_PATH
```python
# SOURCE: src/amz_scout/db.py:1060-1061
_json_or_none(raw.get("eanList")),
_json_or_none(raw.get("upcList")),
```
Mirror for the fix (build normalized lists once above the INSERT and
feed them in):
```python
normalized_ean = _normalize_gtin_list(raw.get("eanList"))
normalized_upc = _normalize_gtin_list(raw.get("upcList"))
# ...
_json_or_none(normalized_ean),
_json_or_none(normalized_upc),
```
where `_normalize_gtin_list` returns a `list[str]` with duplicates
preserved (to keep round-trip debugging identical to today). The plain
`_normalize_gtin` scalar helper is what `_find_product_by_ean` uses.

### MIGRATION_WITH_TXN
```python
# SOURCE: src/amz_scout/db.py:181-192 (v2) and 254-266 (v4)
if current < 4:
    # v4: add fetch_mode to keepa_products
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
Mirror: v8 slots in as the last `if current < 8:` block **inside** the
existing `with conn:` of `_migrate` (after the v6 block at line 406,
before the v7 FK-toggle block at line 412). Version insert is
`INSERT OR IGNORE INTO schema_migrations`. Log line uses
`logger.info("Migrated schema to version 8")`. No DDL — just
`UPDATE keepa_products SET ean_list = ?, upc_list = ? WHERE rowid = ?`
per row that contains at least one non-canonical value.

### TEST_SEED_PATTERN
```python
# SOURCE: tests/test_db.py:884-897
def _seed_keepa_row(
    self,
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    brand: str,
    ean: str,
) -> None:
    conn.execute(
        "INSERT INTO keepa_products "
        "(asin, site, brand, ean_list, upc_list, fetch_mode, fetched_at) "
        "VALUES (?, ?, ?, ?, '[]', 'full', '2026-04-20T00:00:00Z')",
        (asin, site, brand, f'["{ean}"]'),
    )
```
Mirror: extend the signature to accept separate `ean_json` and `upc_json`
strings for cross-format tests (e.g. EAN-13 on one row, UPC-12 on the
other). Keep `fetch_mode='full'` and a fixed `fetched_at` timestamp for
determinism.

### MIGRATION_TEST_PATTERN
```python
# SOURCE: tests/test_db.py:592-650 (abridged)
def test_v7_migrates_v6_db_and_merges_duplicates(self, tmp_path, caplog):
    ...
    db_path = tmp_path / "v6tov7.db"
    c0 = sqlite3.connect(str(db_path))
    c0.row_factory = sqlite3.Row
    init_schema(c0)
    # forcibly downgrade to v6 shape
    c0.execute("DELETE FROM schema_migrations WHERE version = 7")
    # ... insert legacy-shaped rows ...
    c0.close()
    # Reopen to trigger real v7 migration:
    with open_db(db_path) as c1:
        ...
```
Mirror for v8: forcibly downgrade (delete `schema_migrations` row for
v8 — and for v8 only, because the table structure itself is unchanged),
insert rows with raw UPC-12 in `upc_list` and EAN-13 in `ean_list`,
close, reopen, assert the JSON is now canonical GTIN-13 and that a
cross-format `_find_product_by_ean` call matches.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | Add `_normalize_gtin` + `_normalize_gtin_list` helpers; use them in `_find_product_by_ean` read path and in `store_keepa_product` write path; add v8 migration block; bump `SCHEMA_VERSION` to 8 |
| `tests/test_db.py` | UPDATE | Add `TestNormalizeGtin` (unit), add `TestFindProductByEanGtinNormalization` (cross-format matching), add `TestGtinBackfillMigrationV8` (migration) |
| `docs/DEVELOPER.md` | UPDATE (optional) | One-line note that `keepa_products.ean_list` / `upc_list` store canonical GTIN-13 since v8. Skip if the file has no schema-version reference today. |

## NOT Building

- **Merging existing duplicate `product_id` rows** created by the pre-fix
  miss. After the v8 backfill, future bindings are correct, but already-
  split products stay split until a human decides. We deliberately do
  NOT re-run the v7-style merge logic here because: 1) it requires
  human judgment on ambiguity, and 2) the issue scope is "fix the leak,"
  not "clean the past".
- **GTIN check-digit validation**. Amazon/Keepa already emit valid
  GTINs; a failing check-digit is upstream data corruption and should
  not silently mutate identity. We log at DEBUG if `len(digits)` is
  neither 12 nor 13 and still normalize, or drop if length > 13.
- **Normalizing ASIN values** (they're alphanumeric, already canonical,
  and separately validated by the `^[A-Z0-9]{10}$` ASIN pass-through
  in `resolve_product`).
- **Adding a new index** on `ean_list` / `upc_list`. The existing
  `json_each` scan over `keepa_products` is already bounded (the table
  is small — ~1k rows projected at steady state for single-tenant use).
  Indexing JSON values is out of scope; revisit only if query latency
  is measurable.
- **Reprocessing historical Keepa JSON files** on disk under
  `output/*/raw/`. Those are append-only audit artefacts; they remain
  as-received. Only the DB is normalized.

---

## Step-by-Step Tasks

### Task 1: Add `_normalize_gtin` scalar helper and `_normalize_gtin_list` list helper
- **ACTION**: Add two module-private helpers in `src/amz_scout/db.py`
  directly after `_normalize_key` (around line 847).
- **IMPLEMENT**:
  ```python
  def _normalize_gtin(code: str | None) -> str:
      """Normalize a GTIN/UPC/EAN code to canonical GTIN-13.

      Strips non-digit characters, then left-zero-pads to 13 digits
      so that UPC-12 (``"850018166010"``) and EAN-13
      (``"0850018166010"``) of the same physical product collide to
      the same string. Codes longer than 13 digits are dropped
      (GTIN-14 outer-case barcodes are not ASIN-carried and silently
      padding them would corrupt identity). ``None`` / empty /
      all-garbage inputs return ``""`` so the caller can drop them.
      """
      digits = "".join(c for c in (code or "") if c.isdigit())
      if not digits or len(digits) > 13:
          if digits:
              logger.debug("Dropping out-of-range GTIN: %r", code)
          return ""
      return digits.zfill(13)


  def _normalize_gtin_list(codes) -> list[str]:
      """Normalize a list of GTIN/UPC/EAN codes, dropping empties.

      Preserves order and duplicates (by the normalized form) so that
      ``_json_or_none`` emits the same shape Keepa returned, minus the
      format drift. Accepts ``None`` or any iterable of ``str | None``.
      """
      if not codes:
          return []
      out: list[str] = []
      for c in codes:
          n = _normalize_gtin(c)
          if n:
              out.append(n)
      return out
  ```
- **MIRROR**: `MODULE_PRIVATE_HELPER` pattern above (placement, naming,
  `None` tolerance, docstring voice).
- **IMPORTS**: None new — `logging` already imported at top of file.
- **GOTCHA**: Do not raise on malformed input. Keepa occasionally emits
  surprise shapes (e.g. a stray space); the function must degrade
  gracefully because it sits on the ingestion hot path.
- **VALIDATE**: Unit tests in Task 5 pin the contract.

### Task 2: Apply normalization in `_find_product_by_ean` (read path)
- **ACTION**: Replace lines 862-864 of `src/amz_scout/db.py`.
- **IMPLEMENT**:
  ```python
  ean_list = raw.get("eanList") or []
  upc_list = raw.get("upcList") or []
  codes = {_normalize_gtin(c) for c in (ean_list + upc_list)}
  codes.discard("")
  if not codes:
      return None
  ```
- **MIRROR**: `EAN_READ_PATH` above.
- **IMPORTS**: None — `_normalize_gtin` is same-module.
- **GOTCHA**: The existing `if not codes: return None` stays — it now
  fires if every code was garbage (previously impossible because raw
  strings were kept as-is).
- **VALIDATE**: Integration test in Task 6 proves cross-format match.

### Task 3: Apply normalization in `store_keepa_product` (write path)
- **ACTION**: In `store_keepa_product`, above the `INSERT OR REPLACE
  INTO keepa_products` statement (around line 996), compute the
  normalized lists once and substitute them for the raw `raw.get(...)`
  in the two `_json_or_none` arguments currently at lines 1060-1061.
- **IMPLEMENT**:
  ```python
  # Normalize EAN/UPC to canonical GTIN-13 so cross-format matches
  # (UPC-12 on US vs EAN-13 on EU for the same physical product) hit.
  normalized_ean = _normalize_gtin_list(raw.get("eanList"))
  normalized_upc = _normalize_gtin_list(raw.get("upcList"))
  # ... existing INSERT prep ...
  # in the VALUES tuple, replace
  #     _json_or_none(raw.get("eanList")),
  #     _json_or_none(raw.get("upcList")),
  # with
  #     _json_or_none(normalized_ean),
  #     _json_or_none(normalized_upc),
  ```
- **MIRROR**: `EAN_WRITE_PATH` above.
- **IMPORTS**: None.
- **GOTCHA**: `_json_or_none` returns `None` for empty lists — that
  behaviour is preserved because `_normalize_gtin_list([])` returns
  `[]`. Do not change `_json_or_none`.
- **VALIDATE**: Task 7 asserts the DB round-trip.

### Task 4: Add v8 migration that backfills existing `ean_list` / `upc_list`
- **ACTION**: Bump `SCHEMA_VERSION = 8` (line 105). Add a new
  `if current < 8:` block inside the existing `with conn:` of
  `_migrate` (after the v6 block at line 406, before the v7 FK-toggle
  block at line 412).
- **IMPLEMENT**:
  ```python
  if current < 8:
      # v8: canonicalize existing ean_list / upc_list JSON to
      # GTIN-13 so cross-format (UPC-12 ↔ EAN-13) matches work. No
      # DDL change; rewrite JSON content row-by-row only when it
      # would actually change, to keep the migration cheap.
      rows = conn.execute(
          "SELECT rowid, ean_list, upc_list FROM keepa_products "
          "WHERE ean_list IS NOT NULL OR upc_list IS NOT NULL"
      ).fetchall()
      rewritten = 0
      for row in rows:
          ean_raw = _safe_json_list(row["ean_list"])
          upc_raw = _safe_json_list(row["upc_list"])
          ean_new = _normalize_gtin_list(ean_raw)
          upc_new = _normalize_gtin_list(upc_raw)
          if ean_new == ean_raw and upc_new == upc_raw:
              continue
          conn.execute(
              "UPDATE keepa_products SET ean_list = ?, upc_list = ? "
              "WHERE rowid = ?",
              (
                  _json_or_none(ean_new),
                  _json_or_none(upc_new),
                  row["rowid"],
              ),
          )
          rewritten += 1
      conn.execute(
          "INSERT OR IGNORE INTO schema_migrations (version, description) "
          "VALUES (8, 'canonicalize ean_list/upc_list to GTIN-13')"
      )
      logger.info(
          "Migrated schema to version 8 (rewrote %d keepa_products rows)",
          rewritten,
      )
  ```
- **MIRROR**: `MIGRATION_WITH_TXN` pattern above.
- **IMPORTS**: None.
- **GOTCHA**:
  - Skip rows whose normalization is a no-op (both lists already
    canonical) to minimize write amplification.
  - `_safe_json_list` already tolerates malformed JSON → empty list.
    For those rows, `ean_new == ean_raw == []`, no UPDATE runs.
  - v8 lives inside the existing `with conn:` block because it does
    **not** toggle `PRAGMA foreign_keys` (unlike v7). Do not move it
    out.
- **VALIDATE**: `test_v8_backfills_existing_rows` below.

### Task 5: Unit tests for `_normalize_gtin` / `_normalize_gtin_list`
- **ACTION**: Add a new test class `TestNormalizeGtin` in
  `tests/test_db.py` adjacent to `TestFindProductByEanBrandGuardV7`
  (around line 869).
- **IMPLEMENT**:
  ```python
  class TestNormalizeGtin:
      """Contract: every EAN/UPC/GTIN value lands as a 13-digit
      zero-padded string, or `""` for unusable input. Ensures UPC-12
      and EAN-13 of the same GTIN collide.
      """

      def test_upc12_and_ean13_same_gtin_collide(self):
          from amz_scout.db import _normalize_gtin
          assert (
              _normalize_gtin("850018166010")
              == _normalize_gtin("0850018166010")
              == "0850018166010"
          )

      def test_none_empty_and_garbage_return_empty(self):
          from amz_scout.db import _normalize_gtin
          assert _normalize_gtin(None) == ""
          assert _normalize_gtin("") == ""
          assert _normalize_gtin("   ") == ""
          assert _normalize_gtin("abc") == ""

      def test_whitespace_and_hyphens_stripped(self):
          from amz_scout.db import _normalize_gtin
          assert _normalize_gtin("  850-018-166-010  ") == "0850018166010"

      def test_over_13_digits_dropped(self):
          from amz_scout.db import _normalize_gtin
          assert _normalize_gtin("00850018166010") == ""  # 14 digits
          assert _normalize_gtin("9999999999999999") == ""

      def test_short_codes_zero_padded(self):
          from amz_scout.db import _normalize_gtin
          assert _normalize_gtin("1") == "0000000000001"
          assert _normalize_gtin("123456789012") == "0123456789012"

      def test_list_helper_preserves_order_and_drops_empties(self):
          from amz_scout.db import _normalize_gtin_list
          out = _normalize_gtin_list(
              ["850018166010", None, "", "0850018166010", "abc"]
          )
          assert out == ["0850018166010", "0850018166010"]

      def test_list_helper_accepts_none(self):
          from amz_scout.db import _normalize_gtin_list
          assert _normalize_gtin_list(None) == []
          assert _normalize_gtin_list([]) == []
  ```
- **MIRROR**: test structure from
  `TestBrandModelKeyMigrationV7::test_v7_normalize_key_basic`
  (lines 531-540).
- **IMPORTS**: Inside tests, per existing style.
- **GOTCHA**: Test the cross-format collision first — it's the headline
  contract the fix exists to satisfy.
- **VALIDATE**: `pytest tests/test_db.py::TestNormalizeGtin -v`.

### Task 6: Cross-format `_find_product_by_ean` integration test
- **ACTION**: Add a new test class `TestFindProductByEanGtinNormalization`
  in `tests/test_db.py` after `TestFindProductByEanBrandGuardV7`.
- **IMPLEMENT**:
  ```python
  class TestFindProductByEanGtinNormalization:
      """Issue #12 regression: US ASIN with UPC-12 in upcList and
      EU ASIN with EAN-13 (same digits + leading 0) in eanList must
      bind to the same product_id. Before the fix the raw-string set
      union dropped the cross-format match and created duplicates.
      """

      def _seed(
          self,
          conn,
          asin,
          site,
          brand,
          ean_json="[]",
          upc_json="[]",
      ):
          conn.execute(
              "INSERT INTO keepa_products "
              "(asin, site, brand, ean_list, upc_list, "
              " fetch_mode, fetched_at) "
              "VALUES (?, ?, ?, ?, ?, 'full', "
              "'2026-04-20T00:00:00Z')",
              (asin, site, brand, ean_json, upc_json),
          )

      def test_us_upc12_binds_to_eu_ean13_product(self, conn):
          from amz_scout.db import (
              _find_product_by_ean,
              register_asin,
              register_product,
              store_keepa_product,
          )

          pid, _ = register_product(conn, "Router", "Acme", "Model X")

          # EU row is stored via the canonical write path, so its
          # upc_list / ean_list are already GTIN-13.
          store_keepa_product(
              conn,
              "B0EU000001",
              "DE",
              {
                  "title": "Acme Model X",
                  "brand": "Acme",
                  "eanList": ["0850018166010"],
                  "upcList": None,
              },
              fetched_at="2026-04-20T00:00:00Z",
          )
          register_asin(conn, pid, "DE", "B0EU000001")

          # New US row carries UPC-12 of the same GTIN — must match.
          raw_us = {
              "brand": "Acme",
              "eanList": None,
              "upcList": ["850018166010"],
          }
          assert (
              _find_product_by_ean(conn, "B0US000001", raw_us) == pid
          )

      def test_legacy_stored_upc12_matches_new_ean13(self, conn):
          """Defence-in-depth: direct INSERT (simulating a pre-v8
          row that bypassed the normalized write path) plus an
          explicit backfill call should still produce the cross-
          format match, proving the migration's logic is correct.
          """
          from amz_scout.db import (
              _find_product_by_ean,
              _json_or_none,
              _normalize_gtin_list,
              _safe_json_list,
              register_asin,
              register_product,
          )

          pid, _ = register_product(conn, "Router", "Acme", "Model X")
          self._seed(
              conn,
              "B0US000001",
              "US",
              "Acme",
              upc_json='["850018166010"]',
          )
          register_asin(conn, pid, "US", "B0US000001")

          # Simulate v8 backfill inline (the migration itself is
          # already applied on the fixture, so we emulate its rewrite
          # on our direct-INSERT row).
          row = conn.execute(
              "SELECT rowid, ean_list, upc_list FROM keepa_products "
              "WHERE asin = 'B0US000001'"
          ).fetchone()
          ean_new = _normalize_gtin_list(_safe_json_list(row["ean_list"]))
          upc_new = _normalize_gtin_list(_safe_json_list(row["upc_list"]))
          conn.execute(
              "UPDATE keepa_products SET ean_list = ?, upc_list = ? "
              "WHERE rowid = ?",
              (
                  _json_or_none(ean_new),
                  _json_or_none(upc_new),
                  row["rowid"],
              ),
          )

          raw_eu = {"brand": "Acme", "eanList": ["0850018166010"]}
          assert (
              _find_product_by_ean(conn, "B0EU000001", raw_eu) == pid
          )

      def test_different_gtin_still_rejected(self, conn):
          """Normalization must not collapse genuinely different
          barcodes. Two products with different GTINs must remain
          separate.
          """
          from amz_scout.db import (
              _find_product_by_ean,
              register_asin,
              register_product,
              store_keepa_product,
          )

          pid, _ = register_product(conn, "Router", "Acme", "Model X")
          store_keepa_product(
              conn,
              "B0EU000001",
              "DE",
              {
                  "title": "Acme Model X",
                  "brand": "Acme",
                  "eanList": ["0850018166010"],
              },
              fetched_at="2026-04-20T00:00:00Z",
          )
          register_asin(conn, pid, "DE", "B0EU000001")

          raw_different = {
              "brand": "Acme",
              "upcList": ["850018166099"],  # different GTIN
          }
          assert (
              _find_product_by_ean(conn, "B0US000001", raw_different)
              is None
          )
  ```
- **MIRROR**: `TEST_SEED_PATTERN` and `TestFindProductByEanBrandGuardV7` structure.
- **IMPORTS**: As shown in the test bodies.
- **GOTCHA**: The second test is deliberately verbose to document
  the contract boundary (direct INSERT bypasses write-path
  normalization; migration is what makes legacy rows consistent).
  Do NOT call `store_keepa_product` there, because that would
  silently normalize via Task 3 and hide what's being tested.
- **VALIDATE**: `pytest tests/test_db.py::TestFindProductByEanGtinNormalization -v`.

### Task 7: Write-path round-trip test
- **ACTION**: Add `test_store_keepa_product_normalizes_write_path` to
  `TestFindProductByEanGtinNormalization`.
- **IMPLEMENT**:
  ```python
  def test_store_keepa_product_normalizes_write_path(self, conn):
      import json
      from amz_scout.db import store_keepa_product

      store_keepa_product(
          conn,
          "B0US000002",
          "US",
          {
              "title": "Acme Model Y",
              "brand": "Acme",
              "upcList": ["850018166010"],  # UPC-12
              "eanList": None,
          },
          fetched_at="2026-04-20T00:00:00Z",
      )
      row = conn.execute(
          "SELECT ean_list, upc_list FROM keepa_products "
          "WHERE asin = 'B0US000002'"
      ).fetchone()
      assert row["ean_list"] is None
      assert json.loads(row["upc_list"]) == ["0850018166010"]
  ```
- **MIRROR**: `TestFindProductByEanBrandGuardV7` seed/assert structure.
- **IMPORTS**: `store_keepa_product` already imported at top of `test_db.py`.
- **GOTCHA**: Empty `eanList` ends up as `None` in DB because
  `_json_or_none([])` returns `None` — assert that behaviour is preserved.
- **VALIDATE**: Same pytest node.

### Task 8: v8 migration test — backfill existing rows
- **ACTION**: Add a test class `TestGtinBackfillMigrationV8` with three
  tests.
- **IMPLEMENT**:
  ```python
  class TestGtinBackfillMigrationV8:
      """v7 → v8 upgrade: canonicalize existing ean_list / upc_list
      JSON to GTIN-13 so pre-fix rows match the post-fix query path.
      """

      def test_v8_backfills_existing_rows(self, tmp_path, caplog):
          import json
          import logging
          import sqlite3

          from amz_scout.db import init_schema, open_db

          db_path = tmp_path / "v7tov8.db"

          # Create v8 schema first, then simulate a "pre-v8" state
          # by removing the v8 migration record AND directly writing
          # raw UPC-12 / EAN-13 into keepa_products (i.e., bypassing
          # the normalized write path).
          c0 = sqlite3.connect(str(db_path))
          c0.row_factory = sqlite3.Row
          init_schema(c0)
          c0.execute(
              "DELETE FROM schema_migrations WHERE version = 8"
          )
          c0.execute(
              "INSERT INTO keepa_products "
              "(asin, site, brand, ean_list, upc_list, "
              " fetch_mode, fetched_at) VALUES "
              "('B0US000001', 'US', 'Acme', NULL, "
              "'[\"850018166010\"]', 'full', "
              "'2026-04-20T00:00:00Z')"
          )
          c0.execute(
              "INSERT INTO keepa_products "
              "(asin, site, brand, ean_list, upc_list, "
              " fetch_mode, fetched_at) VALUES "
              "('B0EU000001', 'DE', 'Acme', "
              "'[\"0850018166010\"]', NULL, 'full', "
              "'2026-04-20T00:00:00Z')"
          )
          c0.commit()
          c0.close()

          # Clear the cached migration marker so re-opening triggers
          # the v8 migration path.
          import amz_scout.db as db_mod
          db_mod._schema_initialized.discard(str(db_path))

          with caplog.at_level(logging.INFO, logger="amz_scout.db"):
              with open_db(db_path) as c1:
                  us = c1.execute(
                      "SELECT ean_list, upc_list FROM keepa_products "
                      "WHERE asin = 'B0US000001'"
                  ).fetchone()
                  eu = c1.execute(
                      "SELECT ean_list, upc_list FROM keepa_products "
                      "WHERE asin = 'B0EU000001'"
                  ).fetchone()
                  version = c1.execute(
                      "SELECT MAX(version) AS v FROM schema_migrations"
                  ).fetchone()["v"]

          assert version == 8
          assert json.loads(us["upc_list"]) == ["0850018166010"]
          assert us["ean_list"] is None
          assert json.loads(eu["ean_list"]) == ["0850018166010"]
          assert eu["upc_list"] is None
          assert any(
              "version 8" in rec.message for rec in caplog.records
          )

      def test_v8_is_idempotent(self, conn):
          from amz_scout.db import init_schema
          init_schema(conn)  # second call
          row = conn.execute(
              "SELECT COUNT(*) AS c FROM schema_migrations "
              "WHERE version = 8"
          ).fetchone()
          assert row["c"] == 1

      def test_v8_leaves_already_canonical_rows_untouched(self, tmp_path):
          """Write amplification guard: rows whose ean_list / upc_list
          are already canonical must not be rewritten.
          """
          import json
          import sqlite3

          from amz_scout.db import init_schema, open_db

          db_path = tmp_path / "noop.db"
          c0 = sqlite3.connect(str(db_path))
          c0.row_factory = sqlite3.Row
          init_schema(c0)
          c0.execute("DELETE FROM schema_migrations WHERE version = 8")
          c0.execute(
              "INSERT INTO keepa_products "
              "(asin, site, brand, ean_list, upc_list, "
              " fetch_mode, fetched_at) VALUES "
              "('B0XX000001', 'US', 'Acme', "
              "'[\"0850018166010\"]', NULL, 'full', "
              "'2026-04-20T00:00:00Z')"
          )
          c0.commit()
          c0.close()

          import amz_scout.db as db_mod
          db_mod._schema_initialized.discard(str(db_path))

          with open_db(db_path) as c1:
              row = c1.execute(
                  "SELECT ean_list FROM keepa_products "
                  "WHERE asin = 'B0XX000001'"
              ).fetchone()

          assert json.loads(row["ean_list"]) == ["0850018166010"]
          # The migration loop's `if ean_new == ean_raw` no-op guard
          # ensures no UPDATE ran; this test fails loudly if that
          # guard is removed in the future.
  ```
- **MIRROR**: `test_v7_migrates_v6_db_and_merges_duplicates` at
  lines 592-650 for the `tmp_path` + forced-downgrade + reopen flow.
- **IMPORTS**: Inside tests, per existing style.
- **GOTCHA**:
  - Use a file-backed DB (`tmp_path / "*.db"`) not `:memory:`, because
    the migration runs via `init_schema` on connection open and we
    need to simulate a "previously opened" state.
  - Must discard `_schema_initialized` cache before re-opening,
    otherwise `init_schema` short-circuits.
  - Do NOT insert via `store_keepa_product` in the backfill test —
    that would silently normalize via Task 3 and hide what's being
    tested.
- **VALIDATE**: `pytest tests/test_db.py::TestGtinBackfillMigrationV8 -v`.

### Task 9 (optional): Short note in `docs/DEVELOPER.md`
- **ACTION**: If the developer doc references schema versions, add
  one line under the schema section:
  > `keepa_products.ean_list` / `upc_list` store canonical GTIN-13
  > (digits-only, zero-padded to 13) since schema v8. See
  > `_normalize_gtin` for the contract.
- **MIRROR**: existing schema-description bullet style.
- **IMPORTS**: N/A.
- **GOTCHA**: Skip entirely if the doc has no schema-version section
  today — don't invent one for this change.
- **VALIDATE**: Visual review.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_upc12_and_ean13_same_gtin_collide` | `"850018166010"` vs `"0850018166010"` | both → `"0850018166010"` | Core contract |
| `test_none_empty_and_garbage_return_empty` | `None` / `""` / `"   "` / `"abc"` | `""` | Yes |
| `test_whitespace_and_hyphens_stripped` | `"  850-018-166-010  "` | `"0850018166010"` | Yes |
| `test_over_13_digits_dropped` | `"00850018166010"` (14d) | `""` | Yes |
| `test_short_codes_zero_padded` | `"1"` | `"0000000000001"` | Yes |
| `test_list_helper_preserves_order_and_drops_empties` | mixed list | ordered, empties dropped | Yes |
| `test_list_helper_accepts_none` | `None` / `[]` | `[]` | Yes |
| `test_us_upc12_binds_to_eu_ean13_product` | cross-format Keepa raws | same `product_id` | Core regression |
| `test_different_gtin_still_rejected` | genuinely distinct GTINs | `None` | Guard |
| `test_legacy_stored_upc12_matches_new_ean13` | pre-v8 row + post-fix query | match | Defence-in-depth |
| `test_store_keepa_product_normalizes_write_path` | `upcList=["850018166010"]` | stored as `["0850018166010"]` | Write round-trip |
| `test_v8_backfills_existing_rows` | v7 DB with raw UPC/EAN rows | canonical after reopen | Migration |
| `test_v8_is_idempotent` | already-v8 DB | exactly one v8 migration row | Guard |
| `test_v8_leaves_already_canonical_rows_untouched` | pre-existing canonical row | unchanged after migration | Write amplification |

### Edge Cases Checklist
- [x] Empty input (`None`, `""`)
- [x] All-whitespace input (`"   "`)
- [x] All-non-digit garbage (`"abc"`)
- [x] UPC-12 vs EAN-13 same GTIN (core)
- [x] Different GTINs (must stay distinct)
- [x] Mixed `eanList` + `upcList` in same raw
- [x] One side `None`, other side populated
- [x] Duplicates in list (preserved after normalization)
- [x] Pre-v8 rows (backfilled by migration)
- [x] Already-canonical rows (migration no-op)
- [x] Idempotent re-open
- [ ] Concurrent writers — covered by prior `register_product` TOCTOU fix; GTIN change is orthogonal
- [ ] Network failure — N/A; this is pure DB code

---

## Validation Commands

### Static Analysis
```bash
ruff check src/amz_scout/db.py tests/test_db.py
```
EXPECT: Zero lint errors.

### Type Check
```bash
# amz-scout does not currently enforce mypy/pyright in CI. Skip unless
# a type-check pre-commit exists.
```

### Unit Tests — Targeted
```bash
pytest tests/test_db.py::TestNormalizeGtin -v
pytest tests/test_db.py::TestFindProductByEanGtinNormalization -v
pytest tests/test_db.py::TestGtinBackfillMigrationV8 -v
```
EXPECT: All new tests pass.

### Unit Tests — Regression
```bash
pytest tests/test_db.py -v
```
EXPECT: All pre-existing tests (especially `TestFindProductByEanBrandGuardV7`,
`TestBrandModelKeyMigrationV7`, `TestRegisterProductConcurrency`) still
pass.

### Full Test Suite
```bash
pytest
```
EXPECT: No regressions across `test_api.py`, `test_core_flows.py`,
`test_db_freshness.py`, `test_keepa_service.py`, etc.

### Database Validation on Real DB (manual, optional)
```bash
# On a production-shaped DB, verify the migration ran and see how
# many rows were rewritten.
sqlite3 output/amz_scout.db "SELECT version, description FROM schema_migrations ORDER BY version;"
sqlite3 output/amz_scout.db "SELECT COUNT(*) FROM keepa_products WHERE ean_list IS NOT NULL OR upc_list IS NOT NULL;"
sqlite3 output/amz_scout.db "SELECT asin, site, ean_list, upc_list FROM keepa_products LIMIT 5;"
```
EXPECT: `schema_migrations` shows version 8 row; `ean_list` / `upc_list`
values are 13-digit zero-padded strings.

### Manual Validation
- [ ] Run the test suite and confirm green.
- [ ] On a dev copy of the shared DB, check the v8 migration log line:
      `Migrated schema to version 8 (rewrote N keepa_products rows)`.
- [ ] Smoke: register a US ASIN whose `upcList` overlaps (as GTIN) with
      an already-registered EU ASIN's `eanList`. Confirm same `product_id`
      via `list_products` or SQL.

---

## Acceptance Criteria
- [ ] All tasks completed
- [ ] All validation commands pass
- [ ] Tests written and passing, including cross-format match test
- [ ] No type errors
- [ ] No lint errors
- [ ] `SCHEMA_VERSION` = 8 and a matching `schema_migrations` row is
      inserted by the migration
- [ ] `_find_product_by_ean` returns the correct `product_id` for a
      US UPC-12 ASIN that shares a GTIN with an existing EU EAN-13 ASIN
- [ ] `store_keepa_product` persists all EAN/UPC values as canonical
      GTIN-13

## Completion Checklist
- [ ] Code follows discovered patterns (module-private helper placement,
      migration-in-txn, test class structure)
- [ ] Error handling matches codebase style (graceful `None`, DEBUG log
      for anomalies, no exceptions on malformed input)
- [ ] Logging follows codebase conventions (`logger.info` for migration,
      `logger.debug` for dropped GTIN-14+)
- [ ] Tests follow test patterns (`conn` fixture, class-scoped grouping,
      `tmp_path` for migration tests)
- [ ] No hardcoded values beyond the `13` padding target (which is the
      GS1 contract itself)
- [ ] Documentation updated only if existing doc references schema
      versions
- [ ] No unnecessary scope additions — merging pre-existing duplicates,
      GTIN-14 support, and check-digit validation are explicitly OUT
- [ ] Self-contained — no questions needed during implementation

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| v8 migration runs on shared production DB and rewrites every Keepa row | High (intentional) | Low (UPDATE on a <1k-row table; WAL journal) | Measured in the v8 test's "leaves already canonical untouched" case; rewrite count is logged so operators see the cost |
| A pre-existing row has `upc_list` with a value that is non-digit gibberish | Low | Low (dropped to `[]`) | `_normalize_gtin` handles all malformed inputs; covered by `test_none_empty_and_garbage_return_empty` |
| Someone downstream reads `keepa_products.upc_list` expecting raw Keepa strings | Medium | Medium | Documented change in `docs/DEVELOPER.md` (if that file has a schema section) and in the migration log line |
| Duplicate `product_id` rows that existed before the fix are not merged | High (known; out of scope) | Low (operators can manually reconcile; no data loss) | Stated explicitly in NOT Building |
| GTIN-14 outer-case barcodes land in Keepa output | Very Low | Low | Dropped via `len(digits) > 13` guard; DEBUG-logged |
| `_schema_initialized` cache causes v8 not to run in the same process that just ran v7 | Low | Medium | The cache is keyed by the DB path; v8 migrates before the first write, so a fresh `open_db` after an upgrade deployment picks it up. Migration test explicitly discards the cache to prove this works |

## Notes

- Issue #12 cross-references PR #10 Copilot comment
  [#r3108022429](https://github.com/HS-Jack-YZY/amz-scout/pull/10#discussion_r3108022429).
  The "M1 normalization drift" item from
  `.claude/PRPs/reviews/local-review-2026-04-20.md:41` is adjacent but
  distinct — that one was about `_find_product_by_ean`'s brand guard
  normalization (fixed in the brand-model-normalization plan). This
  fix handles the GTIN-value-normalization drift.
- The helper is kept intentionally small and pure so it can be reused
  later if a webapp-side GTIN query filter is added (e.g. a search-by-
  GTIN input).
- The migration's no-op guard (`if ean_new == ean_raw and upc_new == upc_raw`)
  is the single most important performance knob — without it the
  migration would rewrite every row even on a clean DB.
- Per CLAUDE.md rule #12 (禁止直接调用 Keepa API), this fix operates
  purely on data already in the DB; no Keepa token spend.
