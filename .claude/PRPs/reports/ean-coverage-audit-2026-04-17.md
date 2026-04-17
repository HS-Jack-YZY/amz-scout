# EAN/UPC Coverage Audit — 2026-04-17

**Purpose**: Verify PRD `amz-scout-slim-refactor` Open Questions Q1 (EAN coverage) and Q2 (cross-brand conflicts) against production data before closing out Phase 2.

**Source**: Production SQLite DB `output/amz_scout.db` (142 keepa_products rows, 8 marketplaces).

**Verdict**:
- **Q1 — EAN coverage ≥95%**: ✅ **PASS** (97.2% EAN + 0% brand-bypass)
- **Q2 — Cross-brand EAN collision**: ✅ **PASS** (zero truly cross-brand conflicts)
- **Bonus finding**: minor brand case-sensitivity risk in CA marketplace; low-priority hardening suggested.

---

## Q1 — Coverage

### Overall

| Column | Non-empty | Coverage |
|---|---|---|
| `ean_list` | 138 / 142 | **97.2%** |
| `upc_list` | 19 / 142 | 13.4% |
| EAN **∪** UPC | 138 / 142 | **97.2%** |

**Target** (from PRD Success Metrics): EAN/UPC auto-bind accuracy ≥95%. ✅ cleared.

### Per-marketplace

| Site | Total | EAN | EAN % | Either | Either % |
|---|---|---|---|---|---|
| US | 35 | 32 | 91.4% | 32 | 91.4% |
| MX | 28 | 28 | 100.0% | 28 | 100.0% |
| CA | 26 | 25 | 96.2% | 25 | 96.2% |
| UK | 18 | 18 | 100.0% | 18 | 100.0% |
| DE | 17 | 17 | 100.0% | 17 | 100.0% |
| BR | 12 | 12 | 100.0% | 12 | 100.0% |
| JP | 5 | 5 | 100.0% | 5 | 100.0% |
| FR | 1 | 1 | 100.0% | 1 | 100.0% |

**Notes**:
- The 3 US rows and 1 CA row without EAN are likely Amazon private-label / third-party sellers where Keepa couldn't resolve an upstream barcode.
- Coverage is high enough that the **brand+title fallback path** in `_auto_register_from_keepa` only fires for ~3% of rows — acceptable.

### Brand presence (relevant to brand guard)

| Condition | Count | % |
|---|---|---|
| `brand` non-empty | 140 / 142 | 98.6% |
| `brand` missing **and** EAN present (would bypass brand guard) | 0 / 142 | **0.0%** |

The only 2 rows without brand also lack EAN, so they never reach the `_find_product_by_ean` path at all. The "no brand → skip brand guard" branch in `db.py:660-663` is latent dead code against current data — safe to keep as a defensive fallback.

---

## Q2 — Cross-brand EAN Collision

### Q2a — Truly different brands sharing EAN

**Result: 0 conflicts.** No EAN in the production DB is shared by ASINs from genuinely different brands.

The only "different brand" surface was one EAN shared between `ZYXEL` (DE) and `Zyxel` (UK) — same manufacturer, Keepa just returned a different case per marketplace. Not an OEM/white-label problem.

> PRD Q2 hypothesis "OEM/贴牌冲突" does not manifest in current data.

### Q2b — Same EAN mapping to multiple product_ids

**Result: 3 EAN → ≥2 product_ids in production DB.**

| EAN | Products | Notes |
|---|---|---|
| `4779051840540` | 15:Teltonika:RUTM50, 26:Teltonika:RUTM50 | Same brand+model registered twice — duplicate products_id. Latent data-quality issue (dedup opportunity), not an EAN correctness issue. |
| `6971131383550` | 4:GL.iNet:GL-X3000, 7:GL.iNet:GL-XE3000 | **Different physical models** sharing one EAN — Keepa-side data. |
| `6971131384717` | 3:GL.iNet:GL-X2000-AU, 53:GL.iNet:GL-X2000 | **Region variant** (AU) vs main model sharing EAN. |

**Current protection**: `_find_product_by_ean` at `db.py:666-674` returns `None` and logs `EAN ambiguity for {asin}: codes match N products, skipping auto-bind` when `len(rows) > 1`. So **future new ASINs under these three EANs will NOT be auto-bound** — they'll fall through to the brand+title path or manual registration.

**Historical data**: the ambiguous pairs above were registered **before Phase 2** (via `register_product` or `add_product`), so they predate the EAN auto-bind logic and are legitimate independent `product_id`s from a business perspective. No cleanup required.

### Q2c — Brand case-sensitivity risk

Production data shows **20 EANs** where `GL.iNet` coexists with `GL.iNET` (always CA marketplace returning uppercase `T`). Sample:

| EAN | Sites → brand variant |
|---|---|
| `6971131385202` (B0GF1J99S4) | US/DE/UK/MX = `GL.iNet`; CA = `GL.iNET` |
| `6971131383611` (B0CJF7KQ3Q) | DE/US/UK/MX = `GL.iNet`; CA = `GL.iNET` |
| `6971131384755` (B0F2MR53D6) | UK/JP/US/DE/MX = `GL.iNet`; CA = `GL.iNET` |
| *(17 more, same CA-uppercase pattern)* | — |

**Current code path** (`db.py:661-663`):

```python
if brand:
    sql += "    AND kp.brand = ?\n"
    params.append(brand)
```

Exact `=` comparison. If a new ASIN drops in CA with `brand='GL.iNET'` and the existing match in keepa_products uses `brand='GL.iNet'`, the guard **silently rejects the valid match**, falling through to the brand+title path which might then create a duplicate product_id.

**Current realized risk: 0** (all 20 affected ASINs are already bound from earlier runs). **Forward-looking risk**: whenever a new GL.iNet product first shows up in CA.

---

## Recommendations

### Must-do: none.
Both open questions resolved favorably; Phase 2 is safe to declare complete.

### Should-do: 1 small hardening (separate small PR).

**Change brand guard to case-insensitive match** — `src/amz_scout/db.py:662`:

```diff
-        sql += "    AND kp.brand = ?\n"
-        params.append(brand)
+        sql += "    AND LOWER(kp.brand) = LOWER(?)\n"
+        params.append(brand)
```

- Prevents the `GL.iNet` / `GL.iNET` silent miss pattern for future CA-first discoveries.
- No false-positives (case-insensitive brand equality is the semantically correct behavior for brand identity).
- Trivial 2-line change, zero runtime cost.

### Could-do: dedup the Teltonika RUTM50 duplicate.

`product_id=15` and `product_id=26` both represent `Teltonika RUTM50`. One is redundant. Not blocking; cleanup candidate for a future "registry hygiene" pass.

### Out of scope: EAN → multi-product_id cases (GL-X3000/XE3000 and GL-X2000/X2000-AU).

These are legitimate independent products that happen to share a manufacturer EAN. The ambiguity guard is the correct behavior — no code change needed, and merging these product_ids would be a **business decision**, not a data-quality fix.

---

## Updated PRD Status

Proposed transition for `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md`:

```diff
- [ ] **Q1 — EAN 覆盖率**: ...
- [ ] **Q2 — EAN 跨品牌冲突**: ...
+ [x] **Q1 — EAN 覆盖率** (verified 2026-04-17): 97.2% coverage (138/142 rows),
+      all 8 marketplaces ≥91%, zero brand-bypass cases. Target ≥95% met.
+      See .claude/PRPs/reports/ean-coverage-audit-2026-04-17.md
+ [x] **Q2 — EAN 跨品牌冲突** (verified 2026-04-17): 0 truly cross-brand
+      conflicts. 3 EANs map to >1 product_id (same-brand ambiguity),
+      already handled by _find_product_by_ean ambiguity guard.
+      Minor brand case-sensitivity hardening recommended as follow-up.
```

And status line:

```diff
-*Status: DRAFT — awaiting Q1/Q2 验证（EAN 覆盖率 + 跨品牌冲突检查）*
+*Status: DRAFT — Q1/Q2 verified 2026-04-17. Phase 2 complete; Phase 3 unblocked.*
```
