# Plan: EAN/UPC 自动绑定

## Summary

修改 `_auto_register_from_keepa()` 函数，在现有 brand+title 匹配之前增加 EAN/UPC 匹配逻辑。当 Keepa 数据写入 DB 时，提取新 ASIN 的 EAN/UPC 代码，查找已注册产品中共享相同代码的 ASIN，将新 ASIN 绑定到同一 `product_id`。零 token 成本替代 WebSearch 跨市场补全。

## User Story

As a amz-scout 系统管理员，
I want Keepa 数据写入时自动通过 EAN/UPC 匹配已注册产品并绑定 ASIN，
So that 新市场的同一产品能零成本、零 WebSearch 地关联到正确的 product_id。

## Problem → Solution

**当前**：新 ASIN 写入 DB 时，`_auto_register_from_keepa()` 只用 brand+title 匹配。如果 brand 相同但 title/model 不完全一致（跨市场常见），会创建重复的 product 行。CLAUDE.md 中的"强制 ASIN 补全"指令要求 AI 用 WebSearch 搜索 11 个市场，消耗大量 token。

**之后**：先提取 EAN/UPC → 查找已注册的共享相同 EAN/UPC 的 ASIN → 找到则绑定到同一 product_id，跳过新产品创建。EAN 全球唯一，零成本，零 token。

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md`
- **PRD Phase**: Phase 2 — EAN/UPC 自动绑定
- **Estimated Files**: 4

---

## UX Design

N/A — 内部数据管道变更，无用户界面修改。

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `store_keepa_product()` 返回值 | `{"new_product": True}` 当 brand+model 首次注册 | 增加 `"match_type": "ean"` 说明绑定来源 | 新增字段，向后兼容 |
| 日志 | `New product UK/B0xxx → product 5` | 增加 `EAN-bound DE/B0yyy → product 5 (via EAN 085001...)` | 仅日志级别变更 |
| `sync_registry_from_keepa()` | 仅 brand+title 匹配 | 先 EAN → 再 brand+title fallback | 逻辑一致性 |

---

## Mandatory Reading

Files that MUST be read before implementing:

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 (critical) | `src/amz_scout/db.py` | 532-567 | `_auto_register_from_keepa()` — 要修改的核心函数 |
| P0 (critical) | `src/amz_scout/db.py` | 486-529 | `_try_register_product()` — 现有注册逻辑，EAN 匹配要在它之前执行 |
| P0 (critical) | `src/amz_scout/db.py` | 1176-1218 | `register_product()` + `register_asin()` — 底层注册 API |
| P1 (important) | `src/amz_scout/db.py` | 464-483 | `store_keepa_product()` — 调用入口，了解事务上下文 |
| P1 (important) | `src/amz_scout/db.py` | 570-661 | `_upsert_keepa_product()` — EAN 数据写入方式（`eanList` → `ean_list`） |
| P1 (important) | `src/amz_scout/db.py` | 1234-1273 | `sync_registry_from_keepa()` — 需要同步增加 EAN 匹配 |
| P2 (reference) | `tests/test_core_flows.py` | 38-131 | 现有 `_auto_register_from_keepa` 测试模式 |
| P2 (reference) | `tests/fixtures/keepa_raw.py` | 137-160 | fixture 中 `eanList`/`upcList` 格式参考 |
| P2 (reference) | `docs/database-er-diagram.md` | 39-59 | `keepa_products` 表结构（`ean_list TEXT`） |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| SQLite `json_each()` | SQLite 内置 (3.45.3) | 完全支持，可展开 JSON 数组为行集 |
| EAN-13 全球唯一性 | GS1 标准 | EAN 全球唯一标识物理产品，同一产品跨市场共享 |

---

## Patterns to Mirror

Code patterns discovered in the codebase. Follow these exactly.

### NAMING_CONVENTION

```python
# SOURCE: src/amz_scout/db.py:532-536
def _auto_register_from_keepa(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
) -> dict | None:
```

Private helper 以 `_` 前缀。参数类型注解。返回 `dict | None`。

### ERROR_HANDLING

```python
# SOURCE: src/amz_scout/db.py:561-567
    if result is None:
        logger.debug(
            "Skip auto-register %s/%s: missing brand or title",
            site,
            asin,
        )
    return result
```

失败路径用 `logger.debug`，成功路径用 `logger.info`。返回 `None` 表示跳过，不抛异常。

### LOGGING_PATTERN

```python
# SOURCE: src/amz_scout/db.py:512-519
    logger.info(
        "%s %s/%s → product %d (%s %s)",
        "New product" if is_new else "Associated",
        site,
        asin,
        product_id,
        brand,
        model,
    )
```

使用 `%s` 格式化（不用 f-string），`→` 符号连接操作和结果。

### REGISTRATION_PATTERN

```python
# SOURCE: src/amz_scout/db.py:509-510
    product_id, is_new = register_product(conn, category, brand, model)
    register_asin(conn, product_id, site, asin, status="unverified")
```

先注册/查找 product，再绑定 ASIN。`register_asin` 用 `ON CONFLICT ... DO UPDATE`。

### TEST_STRUCTURE

```python
# SOURCE: tests/test_core_flows.py:64-83
class TestAutoRegisterFromKeepa:
    """Test auto-registration behavior when Keepa data is stored."""

    def test_registers_when_brand_and_title_present(self, conn):
        """Product with brand+title in Keepa data should be auto-registered."""
        raw = _make_keepa_raw(brand="GL.iNet", title="Slate 7 Router")
        result = store_keepa_product(conn, "B0TEST12345", "UK", raw, "2026-04-10T00:00:00Z")

        assert result is not None
        assert result["brand"] == "GL.iNet"
        assert result["new_product"] is True
```

通过 `store_keepa_product` 间接测试（集成级别）。`_make_keepa_raw()` 构建 fixture。断言返回值字段。

### FIXTURE_PATTERN

```python
# SOURCE: tests/test_core_flows.py:38-58
def _make_keepa_raw(
    brand: str = "GL.iNet",
    title: str = "Slate 7 Router",
    model: str = "GL-BE3600",
    asin: str = "B0TEST12345",
    product_group: str = "Router",
) -> dict:
    """Create minimal Keepa raw product JSON for testing."""
    return {
        "asin": asin,
        "brand": brand,
        "title": title,
        "model": model,
        "productGroup": product_group,
        "csv": [],
        ...
    }
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | 新增 `_find_product_by_ean()` + 修改 `_auto_register_from_keepa()` + 修改 `sync_registry_from_keepa()` |
| `tests/test_core_flows.py` | UPDATE | 新增 EAN 绑定测试（`_make_keepa_raw` 扩展 eanList/upcList） |
| `tests/fixtures/keepa_raw.py` | NO CHANGE | fixture 中已有 `eanList`/`upcList`，无需修改 |
| `docs/database-er-diagram.md` | NO CHANGE | 不改 schema，不需更新 ER 图 |

## NOT Building

- 不改 DB schema（`keepa_products` 表结构不变）
- 不新建索引（EAN 匹配在注册时一次性执行，不需要高频查询优化）
- 不修改 `_upsert_keepa_product()`（EAN 数据写入逻辑已正确）
- 不修改 `store_keepa_product()` 的签名或事务结构
- 不触碰 CLAUDE.md（Phase 1 负责）
- 不实现 WebSearch fallback（PRD 的 Could 优先级，不在 MVP 范围内）

---

## Step-by-Step Tasks

### Task 1: 扩展 `_make_keepa_raw()` fixture 支持 eanList/upcList

- **ACTION**: 修改 `tests/test_core_flows.py` 的 `_make_keepa_raw()` 函数签名，增加 `ean_list` 和 `upc_list` 参数
- **IMPLEMENT**:
  ```python
  def _make_keepa_raw(
      brand: str = "GL.iNet",
      title: str = "Slate 7 Router",
      model: str = "GL-BE3600",
      asin: str = "B0TEST12345",
      product_group: str = "Router",
      ean_list: list[str] | None = None,
      upc_list: list[str] | None = None,
  ) -> dict:
      d = {
          "asin": asin,
          "brand": brand,
          "title": title,
          "model": model,
          "productGroup": product_group,
          "csv": [],
          "imagesCSV": "",
          "salesRanks": {},
          "monthlySoldHistory": [],
          "buyBoxSellerIdHistory": [],
          "couponHistory": [],
      }
      if ean_list is not None:
          d["eanList"] = ean_list
      if upc_list is not None:
          d["upcList"] = upc_list
      return d
  ```
- **MIRROR**: FIXTURE_PATTERN — 与现有签名风格一致，使用默认参数
- **IMPORTS**: 无新增
- **GOTCHA**: 注意 Keepa raw JSON 中键名是 `eanList`（camelCase），不是 `ean_list`（snake_case）。DB 列名是 `ean_list`，但 raw dict 中是 `eanList`
- **VALIDATE**: 现有测试不受影响（新参数默认 None）

### Task 2: 新增 `_find_product_by_ean()` 函数

- **ACTION**: 在 `src/amz_scout/db.py` 的 `_auto_register_from_keepa()` 之前添加新函数
- **IMPLEMENT**:
  ```python
  def _find_product_by_ean(
      conn: sqlite3.Connection,
      asin: str,
      raw: dict,
  ) -> int | None:
      """Find an existing product_id by matching EAN/UPC codes.

      Extracts EAN and UPC lists from Keepa raw data, then searches
      keepa_products for other ASINs that share the same codes AND are
      already registered in product_asins. Returns the first matching
      product_id, or None if no match.
      """
      ean_list = raw.get("eanList") or []
      upc_list = raw.get("upcList") or []
      codes = set(ean_list + upc_list)
      if not codes:
          return None

      brand = (raw.get("brand") or "").strip()

      placeholders = ",".join(["?"] * len(codes))
      # Match existing registered products that share EAN/UPC codes.
      # Brand filter prevents cross-brand OEM/white-label false matches.
      sql = f"""
          SELECT DISTINCT pa.product_id
          FROM keepa_products kp
          JOIN product_asins pa ON pa.asin = kp.asin AND pa.marketplace = kp.site
          WHERE kp.asin != ?
          AND (
              EXISTS (SELECT 1 FROM json_each(kp.ean_list) WHERE value IN ({placeholders}))
              OR EXISTS (SELECT 1 FROM json_each(kp.upc_list) WHERE value IN ({placeholders}))
          )
      """
      params: list = [asin] + list(codes) + list(codes)

      if brand:
          sql += "    AND kp.brand = ?\n"
          params.append(brand)

      row = conn.execute(sql, params).fetchone()
      return row["product_id"] if row else None
  ```
- **MIRROR**: NAMING_CONVENTION（`_` 前缀，类型注解）、ERROR_HANDLING（返回 `None` 表示未找到）
- **IMPORTS**: 无新增（`json_mod` 已在文件头导入，但此函数不需要，Keepa raw dict 中 eanList 已是 Python list）
- **GOTCHA**:
  1. `raw.get("eanList")` 返回 Python list（不是 JSON 字符串），因为这是 Keepa raw dict（尚未经过 `_json_or_none` 序列化）
  2. 但 `kp.ean_list` 在 DB 中已经是 JSON 字符串（经 `_upsert_keepa_product` 序列化），所以 SQL 侧用 `json_each()` 是正确的
  3. Brand 过滤是必要的（PRD Q2：防止 OEM/贴牌场景的跨品牌误匹配）
  4. `kp.asin != ?` 排除自身（新 ASIN 可能已被 `_upsert_keepa_product` 写入 keepa_products）
- **VALIDATE**: 通过 Task 4 的测试验证

### Task 3: 修改 `_auto_register_from_keepa()` — 添加 EAN 优先匹配

- **ACTION**: 在 `_auto_register_from_keepa()` 的 "已注册检查" 之后、`_try_register_product()` 之前，插入 EAN 匹配逻辑
- **IMPLEMENT**:
  ```python
  def _auto_register_from_keepa(
      conn: sqlite3.Connection,
      asin: str,
      site: str,
      raw: dict,
  ) -> dict | None:
      """Auto-register a product from Keepa metadata if not already registered.

      Registration priority:
      1. Skip if ASIN already in product_asins for this marketplace
      2. EAN/UPC match: bind to existing product if codes match
      3. Brand+title fallback: create new product entry

      Returns a dict with registration details, or *None* if skipped.
      """
      existing = conn.execute(
          "SELECT product_id FROM product_asins WHERE asin = ? AND marketplace = ?",
          (asin, site),
      ).fetchone()
      if existing:
          return None

      # Priority 1: EAN/UPC match — zero-cost cross-market binding
      ean_product_id = _find_product_by_ean(conn, asin, raw)
      if ean_product_id is not None:
          register_asin(conn, ean_product_id, site, asin, status="unverified")
          prod = conn.execute(
              "SELECT brand, model, category FROM products WHERE id = ?",
              (ean_product_id,),
          ).fetchone()
          logger.info(
              "EAN-bound %s/%s → product %d (%s %s)",
              site,
              asin,
              ean_product_id,
              prod["brand"],
              prod["model"],
          )
          return {
              "product_id": ean_product_id,
              "brand": prod["brand"],
              "model": prod["model"],
              "category": prod["category"],
              "asin": asin,
              "marketplace": site,
              "new_product": False,
              "match_type": "ean",
          }

      # Priority 2: brand+title fallback
      result = _try_register_product(
          conn,
          asin,
          site,
          brand=raw.get("brand") or "",
          title=raw.get("title") or "",
          model=raw.get("model") or "",
          category=raw.get("productGroup") or "",
      )
      if result is None:
          logger.debug(
              "Skip auto-register %s/%s: missing brand or title",
              site,
              asin,
          )
      else:
          result["match_type"] = "brand_title"
      return result
  ```
- **MIRROR**: LOGGING_PATTERN（`logger.info` + `%s` 格式 + `→` 符号）、REGISTRATION_PATTERN（`register_asin()` 调用）
- **IMPORTS**: 无新增
- **GOTCHA**:
  1. `_find_product_by_ean()` 在 `_upsert_keepa_product()` 之后调用（`store_keepa_product` 先 upsert 再 auto-register），所以新 ASIN 的 EAN 数据已经在 DB 中。`_find_product_by_ean` 的 `kp.asin != ?` 排除了自身
  2. `new_product: False` 因为 EAN 匹配意味着产品已存在，只是新市场绑定
  3. 新增 `match_type` 字段（`"ean"` 或 `"brand_title"`）用于日志和调试，向后兼容
  4. EAN 匹配后不需要创建 product 行，只创建 product_asins 行
- **VALIDATE**: 现有测试应继续通过（无 EAN 的产品走 brand+title fallback）

### Task 4: 新增 EAN 绑定测试

- **ACTION**: 在 `tests/test_core_flows.py` 的 `TestAutoRegisterFromKeepa` 类中添加 EAN 相关测试
- **IMPLEMENT**:
  ```python
  def test_ean_binds_cross_market(self, conn):
      """Product with matching EAN should bind to existing product, not create new."""
      raw_uk = _make_keepa_raw(
          brand="GL.iNet", title="Slate 7 Router", model="GL-BE3600",
          asin="B0UK_EAN_01", ean_list=["0850018166010"],
      )
      r1 = store_keepa_product(conn, "B0UK_EAN_01", "UK", raw_uk, "2026-04-10T00:00:00Z")
      assert r1 is not None
      assert r1["new_product"] is True

      raw_de = _make_keepa_raw(
          brand="GL.iNet", title="GL-BE3600 WiFi 7 Reiserouter",
          model="GL-BE3600", asin="B0DE_EAN_01",
          ean_list=["0850018166010"],
      )
      r2 = store_keepa_product(conn, "B0DE_EAN_01", "DE", raw_de, "2026-04-10T01:00:00Z")
      assert r2 is not None
      assert r2["new_product"] is False
      assert r2["product_id"] == r1["product_id"]
      assert r2["match_type"] == "ean"

  def test_upc_binds_cross_market(self, conn):
      """UPC match should also bind to existing product."""
      raw_us = _make_keepa_raw(
          brand="TP-Link", title="AX1500 Router", model="AX1500",
          asin="B0US_UPC_01", upc_list=["885913123456"],
      )
      r1 = store_keepa_product(conn, "B0US_UPC_01", "US", raw_us, "2026-04-10T00:00:00Z")
      assert r1 is not None

      raw_ca = _make_keepa_raw(
          brand="TP-Link", title="AX1500 Wi-Fi Router", model="AX1500",
          asin="B0CA_UPC_01", upc_list=["885913123456"],
      )
      r2 = store_keepa_product(conn, "B0CA_UPC_01", "CA", raw_ca, "2026-04-10T01:00:00Z")
      assert r2 is not None
      assert r2["product_id"] == r1["product_id"]
      assert r2["match_type"] == "ean"

  def test_no_ean_falls_back_to_brand_title(self, conn):
      """Product without EAN/UPC should use brand+title fallback."""
      raw = _make_keepa_raw(brand="TestBrand", title="TestProduct", model="TP-100")
      result = store_keepa_product(conn, "B0NO_EAN_01", "UK", raw, "2026-04-10T00:00:00Z")
      assert result is not None
      assert result["new_product"] is True
      assert result.get("match_type") == "brand_title"

  def test_ean_no_cross_brand_match(self, conn):
      """EAN match should NOT bind across different brands."""
      raw_a = _make_keepa_raw(
          brand="BrandA", title="ProductX", model="X-100",
          asin="B0BRAND_A01", ean_list=["SHARED_EAN_999"],
      )
      store_keepa_product(conn, "B0BRAND_A01", "UK", raw_a, "2026-04-10T00:00:00Z")

      raw_b = _make_keepa_raw(
          brand="BrandB", title="ProductY", model="Y-200",
          asin="B0BRAND_B01", ean_list=["SHARED_EAN_999"],
      )
      r2 = store_keepa_product(conn, "B0BRAND_B01", "US", raw_b, "2026-04-10T01:00:00Z")
      assert r2 is not None
      assert r2["new_product"] is True
      assert r2["brand"] == "BrandB"
  ```
- **MIRROR**: TEST_STRUCTURE（类方法、docstring、fixture 使用、assert 模式）
- **IMPORTS**: 无新增（`store_keepa_product` 已导入）
- **GOTCHA**: 每个测试用不同的 ASIN 避免冲突。测试名称描述行为而非实现
- **VALIDATE**: `pytest tests/test_core_flows.py -v`

### Task 5: 修改 `sync_registry_from_keepa()` — 添加 EAN 匹配

- **ACTION**: 在 `sync_registry_from_keepa()` 中，对每个 orphan ASIN 先尝试 EAN 匹配
- **IMPLEMENT**:
  ```python
  def sync_registry_from_keepa(conn: sqlite3.Connection) -> list[dict]:
      """Register orphan ASINs from keepa_products that are missing from product_asins.

      Matching priority: (1) EAN/UPC → (2) brand+title.
      Only registers products with non-empty brand and title.
      Returns a list of dicts describing each registration.
      """
      orphans = conn.execute(
          """SELECT kp.asin, kp.site, kp.brand, kp.model, kp.title,
                    kp.product_group, kp.ean_list, kp.upc_list
             FROM keepa_products kp
             WHERE NOT EXISTS (
                 SELECT 1 FROM product_asins pa
                 WHERE pa.asin = kp.asin AND pa.marketplace = kp.site
             )"""
      ).fetchall()

      results: list[dict] = []
      for row in orphans:
          # Build a raw-like dict for _find_product_by_ean compatibility
          raw_compat = {
              "eanList": json_mod.loads(row["ean_list"]) if row["ean_list"] else [],
              "upcList": json_mod.loads(row["upc_list"]) if row["upc_list"] else [],
              "brand": row["brand"],
          }
          ean_product_id = _find_product_by_ean(conn, row["asin"], raw_compat)

          if ean_product_id is not None:
              register_asin(
                  conn, ean_product_id, row["site"], row["asin"],
                  status="unverified",
              )
              prod = conn.execute(
                  "SELECT brand, model, category FROM products WHERE id = ?",
                  (ean_product_id,),
              ).fetchone()
              logger.info(
                  "EAN-bound %s/%s → product %d (%s %s)",
                  row["site"],
                  row["asin"],
                  ean_product_id,
                  prod["brand"],
                  prod["model"],
              )
              results.append({
                  "registered": True,
                  "product_id": ean_product_id,
                  "brand": prod["brand"],
                  "model": prod["model"],
                  "asin": row["asin"],
                  "marketplace": row["site"],
                  "new_product": False,
                  "match_type": "ean",
              })
              continue

          reg = _try_register_product(
              conn,
              asin=row["asin"],
              site=row["site"],
              brand=row["brand"] or "",
              title=row["title"] or "",
              model=row["model"] or "",
              category=row["product_group"] or "",
          )
          if reg:
              reg["match_type"] = "brand_title"
              results.append({"registered": True, **reg})
          else:
              results.append({
                  "asin": row["asin"],
                  "marketplace": row["site"],
                  "registered": False,
                  "reason": "missing brand or title",
              })

      return results
  ```
- **MIRROR**: LOGGING_PATTERN、REGISTRATION_PATTERN
- **IMPORTS**: `json_mod` 已在文件头导入
- **GOTCHA**:
  1. `sync_registry_from_keepa` 从 DB 读取 `ean_list`（已是 JSON 字符串），需要 `json_mod.loads()` 解析为 Python list
  2. `_find_product_by_ean` 接收的是 Keepa raw-like dict（`eanList` camelCase key），所以需要构建兼容 dict
  3. 查询中新增 `kp.ean_list, kp.upc_list` 列
- **VALIDATE**: 手动验证 + 现有 `sync_registry` 测试（如果有）

### Task 6: 运行全量测试 + 验证

- **ACTION**: 运行完整测试套件，确保零回归
- **IMPLEMENT**: N/A（验证步骤）
- **MIRROR**: N/A
- **IMPORTS**: N/A
- **GOTCHA**: 确保现有测试（无 EAN 的产品）继续用 brand+title fallback 路径
- **VALIDATE**:
  1. `pytest tests/test_core_flows.py -v` — 新测试全部通过
  2. `pytest` — 全量测试无回归

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_ean_binds_cross_market` | UK 产品有 EAN + DE 产品共享同 EAN | DE 绑定到 UK 的 product_id | No |
| `test_upc_binds_cross_market` | US 产品有 UPC + CA 产品共享同 UPC | CA 绑定到 US 的 product_id | No |
| `test_no_ean_falls_back_to_brand_title` | 产品无 EAN/UPC | 走 brand+title 创建新产品 | Yes |
| `test_ean_no_cross_brand_match` | 不同品牌共享同 EAN（OEM 场景） | 不跨品牌绑定，创建新产品 | Yes |
| 现有 `test_registers_when_brand_and_title_present` | 无 EAN 的产品 | 不受影响，继续通过 | Regression |
| 现有 `test_skips_when_brand_empty` | 空 brand | 不受影响 | Regression |
| 现有 `test_skips_when_already_registered` | 已注册 ASIN | 返回 None | Regression |
| 现有 `test_registers_same_asin_different_marketplace` | 同 ASIN 不同市场 | 关联到同 product | Regression |

### Edge Cases Checklist

- [x] Empty EAN/UPC (None) — `_find_product_by_ean` 返回 None → fallback
- [x] Empty EAN array (`[]`) — `codes = set()` → 返回 None → fallback
- [x] Cross-brand same EAN — brand 过滤阻止误匹配
- [x] Multiple matching products (理论上不会，同 brand+EAN 应唯一) — 取第一个 `fetchone()`
- [x] ASIN 已注册 — `_auto_register_from_keepa` 开头检查，EAN 匹配不执行
- [x] `_upsert_keepa_product` 先于 EAN 匹配执行 — `kp.asin != ?` 排除自身

---

## Validation Commands

### Static Analysis

```bash
python3 -m py_compile src/amz_scout/db.py
```
EXPECT: Zero errors

### Unit Tests

```bash
pytest tests/test_core_flows.py -v
```
EXPECT: All tests pass (existing + new EAN tests)

### Full Test Suite

```bash
pytest
```
EXPECT: No regressions

### Manual Validation

- [ ] 在有真实数据的 DB 上运行 `sync_registry_from_keepa()`，检查日志中的 EAN 绑定
- [ ] 抽样验证 5 个已知跨市场产品的 EAN 匹配结果

---

## Acceptance Criteria

- [ ] `_find_product_by_ean()` 函数实现并通过测试
- [ ] `_auto_register_from_keepa()` 修改：EAN 优先 → brand+title fallback
- [ ] `sync_registry_from_keepa()` 同步增加 EAN 匹配逻辑
- [ ] 4 个新测试通过：EAN 跨市场绑定、UPC 绑定、无 EAN fallback、跨品牌不匹配
- [ ] 现有 4 个 `TestAutoRegisterFromKeepa` 测试零回归
- [ ] 全量 `pytest` 通过
- [ ] 返回值新增 `match_type` 字段（`"ean"` 或 `"brand_title"`）

## Completion Checklist

- [ ] 代码遵循已发现的模式（`_` 前缀、`%s` logger、`→` 符号）
- [ ] 错误处理匹配代码库风格（返回 None、不抛异常）
- [ ] 日志遵循代码库约定（info 成功、debug 跳过）
- [ ] 测试遵循测试模式（类方法、fixture、通过 `store_keepa_product` 间接测试）
- [ ] 无硬编码值
- [ ] 不需要文档更新（内部逻辑变更）
- [ ] 无不必要的范围扩展
- [ ] 自包含 — 实现时不需要搜索代码库或提问

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| EAN 覆盖率不足（部分产品无 EAN） | Medium | Low | brand+title fallback 路径保留不变 |
| SQLite `json_each(NULL)` 行为不一致 | Low | Medium | Python 侧先检查 codes 非空再执行 SQL |
| `_find_product_by_ean` 查询性能 | Very Low | Low | 每次注册最多执行一次，产品数量 <1000 |
| EAN 匹配影响现有测试 | Very Low | High | 现有 fixture 无 eanList → 走 fallback |

## Notes

- **事务上下文**：`store_keepa_product()` 中 `_upsert_keepa_product` 在 `with conn:` 事务块内执行，而 `_auto_register_from_keepa` 在事务块之外调用。这意味着 EAN 数据在匹配时已经提交。`register_asin()` 自带 `with conn:` 事务。
- **match_type 字段**：新增字段是向后兼容的扩展。现有代码中没有地方检查返回值的特定 key 列表。
- **sync_registry_from_keepa 的 raw_compat**：由于 `_find_product_by_ean` 从 raw dict 读取 `eanList`（camelCase），而 sync 从 DB 读取 `ean_list`（snake_case），需要构建兼容 dict。另一个选项是让 `_find_product_by_ean` 同时接受两种格式，但引入额外复杂度。选择最小改动方案。
