# Plan: Brand/Model Normalization for Product Registry

## Summary
对 `products.brand` 和 `products.model` 引入归一化键（`brand_key` / `model_key`），用 `LOWER(TRIM(...))` + 折叠多空格作为唯一性基础，避免 Keepa 返回的字面差异（大小写、首尾空白、多余空格）导致同一物理产品被拆成多条 product 行。保留原始字面用于显示，新 UNIQUE 约束建在归一化键上。

## User Story
作为依赖 product_id 做跨市场 ASIN 关联的系统，
我希望 `register_product` 在匹配 (brand, model) 时对字符串做归一化，
以便 Keepa 的字面噪声不会打破 "一个 product_id = 一个物理产品" 的不变量。

## Problem → Solution
**当前**：`register_product` 用 `WHERE brand = ? AND model = ?` 精确匹配，`'TP-Link '` vs `'TP-Link'`、`'tp-link'` vs `'TP-Link'` 都会 miss → 建出重复 products 行，跨市场 ASIN 绑定错位到不同 product_id，后续查询拿不全跨市场数据。
**改后**：加归一化键列，匹配和 UNIQUE 都在归一化键上。同一个 `'TP-Link'`（或其任意大小写/空白变体）+ `'Archer BE400'`（或其任意变体）始终命中同一 product_id。

## Metadata
- **Complexity**: Medium
- **Source PRD**: N/A（自由文本，源于 2026-04-17 Keepa 绑定脆弱点讨论）
- **PRD Phase**: N/A
- **Estimated Files**: 4（1 源码 + 1 测试 + 1 迁移文档更新 + 1 CLAUDE.md 补注）

---

## UX Design

### Before
```
┌──────────────────────────────────────────────────────────┐
│ Keepa 返回:                                              │
│   B0ABC123 @ UK: brand="TP-Link",  model="Archer BE400"  │
│   B0XYZ789 @ DE: brand="TP-Link ", model="Archer BE400"  │  ← 尾空格
│                                                          │
│ register_product 精确匹配:                               │
│   UK 首次 → INSERT → product_id=2                        │
│   DE 查  'TP-Link ' vs 'TP-Link'  → miss                 │
│   DE 走 INSERT → product_id=3  ❌ 分裂                   │
│                                                          │
│ 后果: query_compare 只能拿到单市场数据                   │
└──────────────────────────────────────────────────────────┘
```

### After
```
┌──────────────────────────────────────────────────────────┐
│ Keepa 返回（同上，字面差异保留）:                        │
│   B0ABC123 @ UK: brand="TP-Link",  model="Archer BE400"  │
│   B0XYZ789 @ DE: brand="TP-Link ", model="Archer BE400"  │
│                                                          │
│ register_product 归一化匹配:                             │
│   brand_key = "tp-link"   (两边一致)                     │
│   model_key = "archer be400"  (两边一致)                 │
│   UK 首次 → INSERT → product_id=2                        │
│   DE 查 → 命中 product_id=2 ✅                           │
│                                                          │
│ products 行仍保留原始 "TP-Link"（显示用）                │
│ UNIQUE 约束移到 (brand_key, model_key)                   │
└──────────────────────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `register_product(conn, cat, brand, model)` | 精确匹配 brand/model | 匹配归一化键，保留显示字面 | 调用方无感知，返回值签名不变 |
| `products.brand` / `.model` | 参与 UNIQUE | 仅显示字段 | 字面保留 Keepa 原值 |
| `products.brand_key` / `.model_key` | 不存在 | 新列，NOT NULL，参与 UNIQUE | 入库时自动填充 |
| `list_products()` / `query_*` 返回 | `brand="TP-Link"` | `brand="TP-Link"`（不变） | 对外 API 完全兼容 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `src/amz_scout/db.py` | 105-169 | `SCHEMA_VERSION`, `init_schema`, `_ensure_schema` 入口 |
| P0 | `src/amz_scout/db.py` | 171-410 | `_migrate` 框架，看 v2-v6 迁移写法 |
| P0 | `src/amz_scout/db.py` | 569-578 | `products` 表定义，含当前 `UNIQUE(brand, model)` |
| P0 | `src/amz_scout/db.py` | 1430-1452 | `register_product` —— 主改动点 |
| P0 | `src/amz_scout/db.py` | 632-675 | `_try_register_product` —— 已有 `.strip()`，要验证路径 |
| P1 | `src/amz_scout/db.py` | 689-737 | `_find_product_by_ean` —— brand 过滤也要归一化 |
| P1 | `src/amz_scout/db.py` | 776-821 | `_auto_register_from_keepa` —— 两条绑定路径的编排 |
| P1 | `src/amz_scout/db.py` | 1488-1540 | `sync_registry_from_keepa` —— orphan 回填 |
| P1 | `tests/test_db.py` | 365-500 | v6 迁移测试范式（v5→v6 强降测试） |
| P2 | `tests/test_db.py` | 170-270 | 现有 register_product 调用示例 |
| P2 | `tests/conftest.py` | all | DB fixture 约定 |
| P2 | `.claude/PRPs/plans/completed/ean-upc-auto-binding.plan.md` | all | 同区域最近一次 plan，迁移 + 绑定风格范式 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| SQLite ALTER TABLE | https://sqlite.org/lang_altertable.html | SQLite 不支持 `ALTER TABLE ADD COLUMN` 后立即加 UNIQUE；需 "create-new + copy + drop-old + rename" 模式 |
| SQLite `UPPER`/`LOWER` | https://sqlite.org/lang_corefunc.html | SQLite 默认 `LOWER` 只处理 ASCII；brand/model 目前都是 ASCII 安全 |
| str normalization | stdlib | `" ".join(s.lower().split())` 同时完成 trim、lower、多空格折叠，无需 regex |

> No external research needed beyond SQLite migration mechanics — 归一化本身是标准 Python。

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: src/amz_scout/db.py:632-650
def _try_register_product(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    brand: str,
    title: str,
    model: str,
    category: str,
) -> dict | None:
    """Register a product if brand and title are present."""
    brand = brand.strip()
    title = title.strip()
    model = model.strip() or asin
```
**遵循点**：私有辅助函数 `_snake_case`，类型注解，docstring 首行单句，显式 `return None` vs 结果 dict。

### ERROR_HANDLING
```python
# SOURCE: src/amz_scout/db.py:689-736
def _find_product_by_ean(conn, asin, raw) -> int | None:
    """...返回第一个匹配的 product_id，或 None。"""
    codes = set(ean_list + upc_list)
    if not codes:
        return None
    ...
    if len(rows) > 1:
        logger.warning("EAN ambiguity for %s: codes match %d products %s, skipping auto-bind", ...)
        return None
    return rows[0]["product_id"] if rows else None
```
**遵循点**：边界为空 → `return None`；歧义不抛错，`logger.warning` + `return None`；永远不 swallow 异常，但对 "未找到" 这种非错误状态用 sentinel 值。

### LOGGING_PATTERN
```python
# SOURCE: src/amz_scout/db.py:756-763
logger.info(
    "EAN-bound %s/%s → product %d (%s %s)",
    site,
    asin,
    product_id,
    prod["brand"],
    prod["model"],
)
```
**遵循点**：`logger = logging.getLogger(__name__)`（文件顶已有），用 `%s` 占位符而非 f-string（懒格式化），关键字段用 `→` / `/` 分隔以便 grep。

### SCHEMA_MIGRATION
```python
# SOURCE: src/amz_scout/db.py:195-265（v3 示例）
if current < 3:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "products" not in tables:
        conn.execute("""CREATE TABLE products (...)""")
        ...
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, description) "
        "VALUES (3, 'add product registry tables')"
    )
    logger.info("Migrated schema to version 3")
```
**遵循点**：`if current < N` 守卫；幂等性（先 PRAGMA 探测再 ALTER）；`INSERT OR IGNORE` 记录迁移；`logger.info("Migrated schema to version N")` 收尾。

### TEST_STRUCTURE
```python
# SOURCE: tests/test_db.py:365-411（v6 迁移测试范式）
class TestStatusMigrationV6:
    def test_v6_check_constraint_rejects_legacy_values(self, conn):
        from amz_scout.db import register_product
        pid, _ = register_product(conn, "Router", "Test", "M1")
        ...

    def test_v6_idempotent(self, conn):
        init_schema(conn)  # second call
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM schema_migrations WHERE version = 6"
        ).fetchone()
        assert row["c"] == 1
```
**遵循点**：`TestXxxMigrationVN` 类；`conn` fixture（来自 conftest）；幂等性测试必备；函数内 import 避免循环。

### V5→V6 FORCED DOWNGRADE PATTERN
```python
# SOURCE: tests/test_db.py:412-470
def test_v6_migrates_legacy_statuses_to_active(self, tmp_path):
    """v5 → v6 upgrade: ..."""
    c0 = sqlite3.connect(str(db_path))
    c0.row_factory = sqlite3.Row
    init_schema(c0)
    c0.execute("DELETE FROM schema_migrations WHERE version = 6")
    c0.execute("ALTER TABLE product_asins RENAME TO _pa_tmp")
    c0.execute("""CREATE TABLE product_asins ( ... v5 shape ... )""")
    c0.execute("INSERT INTO product_asins SELECT ... FROM _pa_tmp")
    c0.execute("DROP TABLE _pa_tmp")
    c0.close()
    # 重新开 → 触发 v6 迁移
    conn2 = sqlite3.connect(str(db_path))
    ...
```
**遵循点**：先 `init_schema` 到最新，再**强制降级**到 N-1 shape，重开触发目标迁移 —— 这是项目唯一受认可的迁移测试范式。

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/db.py` | UPDATE | `SCHEMA_VERSION=7`；新增 `_normalize_key()`；`_migrate` 增 v7 分支；`products` DDL 加 `brand_key`/`model_key` + UNIQUE；`register_product` 改用 key 匹配 |
| `tests/test_db.py` | UPDATE | 新增 `TestBrandModelKeyMigrationV7` 类，覆盖：幂等、v6→v7 强降、重复合并、匹配归一化、显示字面保留 |
| `docs/DEVELOPER.md` | UPDATE | 在 schema 版本表追加 v7 条目；注明 brand/model 归一化语义 |
| `CLAUDE.md` | UPDATE | Key Behaviors 列表补第 14 条：brand/model 匹配按归一化键，但显示保留原值 |

## NOT Building

- **不做 fuzzy 匹配**（Levenshtein、token sort 等）—— 只做机械归一化（lower + trim + 多空格折叠）
- **不归一化品牌别名**（`'TP Link'` vs `'TP-Link'` 仍视作不同品牌）—— 品牌别名表是单独的需求
- **不处理 Unicode case**（如土耳其语 I / ı）—— brand/model 实务上都是 ASCII，且 SQLite 的 `LOWER` 只处理 ASCII
- **不改 product_asins / product_tags schema** —— 它们引用 product_id，合并由迁移脚本处理
- **不改 API 签名** —— `register_product` 调用方不需要改任何一行
- **不归一化 keepa_products.brand** —— 那张表是原始 Keepa 快照，保持字面

---

## Step-by-Step Tasks

### Task 1: 新增 `_normalize_key` 辅助函数
- **ACTION**：在 `src/amz_scout/db.py` 的私有辅助区（约 `_safe_json_list` 附近，L678）新增 `_normalize_key(s: str) -> str`
- **IMPLEMENT**：
  ```python
  def _normalize_key(s: str | None) -> str:
      """Normalize a brand/model string for identity matching.

      Collapses whitespace, lowercases, strips surrounding space.
      Used as the uniqueness basis for products.brand_key / model_key.
      """
      return " ".join((s or "").lower().split())
  ```
- **MIRROR**：`_safe_json_list` (L678-686) —— 私有、类型注解、单句 docstring、接受 None 安全
- **IMPORTS**：无新增（纯 stdlib）
- **GOTCHA**：`None` 输入必须安全（Keepa 有时返回空 brand）；空串归一化后仍为空串，调用方需在空串上自行决策（目前 `_try_register_product` L652 已拦空 brand）
- **VALIDATE**：单测 `_normalize_key('  TP-Link  ')=='tp-link'`、`_normalize_key('Archer  BE400')=='archer be400'`、`_normalize_key(None)==''`、`_normalize_key('')==''`

### Task 2: 更新 `SCHEMA_VERSION` 并扩展 `_SCHEMA_SQL` 里的 products DDL
- **ACTION**：`SCHEMA_VERSION = 7`（L105）；在 `_SCHEMA_SQL` 的 products 表定义（L569-578）加 `brand_key` / `model_key` 列和新 UNIQUE
- **IMPLEMENT**：
  ```sql
  CREATE TABLE products (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      category        TEXT NOT NULL,
      brand           TEXT NOT NULL,
      model           TEXT NOT NULL,
      brand_key       TEXT NOT NULL,
      model_key       TEXT NOT NULL,
      search_keywords TEXT NOT NULL DEFAULT '',
      created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      UNIQUE(brand_key, model_key)
  );
  ```
  同时在 `_SCHEMA_SQL` 底部加 `INSERT INTO schema_migrations VALUES (7, 'normalize brand/model matching via brand_key/model_key');`
- **MIRROR**：现有 v6 record 写法（L426 附近）
- **IMPORTS**：无
- **GOTCHA**：去掉旧的 `UNIQUE(brand, model)` —— 字面唯一性不再有意义
- **VALIDATE**：`sqlite3` CLI 对新 DB `.schema products` 能看到新列和新 UNIQUE；`SELECT version FROM schema_migrations` 含 7

### Task 3: 在 `_migrate` 添加 v7 分支（v6 → v7）
- **ACTION**：在 `_migrate`（L171）现有 `if current < 6` 之后新增 `if current < 7` 分支
- **IMPLEMENT**：
  ```python
  if current < 7:
      # v7: add brand_key/model_key for normalized matching
      cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
      if "brand_key" not in cols:
          # SQLite cannot add a UNIQUE constraint via ALTER — rebuild table
          conn.execute("ALTER TABLE products RENAME TO _products_v6")
          conn.execute("""CREATE TABLE products ( ... new shape ... )""")
          # Populate via Python-side normalization (SQLite LOWER is ASCII-only;
          # _normalize_key also folds multi-space, which LOWER(TRIM(...)) cannot)
          legacy = conn.execute(
              "SELECT id, category, brand, model, search_keywords, created_at, updated_at "
              "FROM _products_v6"
          ).fetchall()
          # Merge duplicates: group by (brand_key, model_key), keep min(id) as canonical
          by_key: dict[tuple[str, str], dict] = {}
          id_remap: dict[int, int] = {}
          for row in legacy:
              bkey = _normalize_key(row["brand"])
              mkey = _normalize_key(row["model"])
              canonical = by_key.setdefault((bkey, mkey), dict(row))
              id_remap[row["id"]] = canonical["id"]
          # Insert canonical rows with keys
          for (bkey, mkey), row in by_key.items():
              conn.execute(
                  "INSERT INTO products (id, category, brand, model, brand_key, model_key, "
                  "search_keywords, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (row["id"], row["category"], row["brand"], row["model"],
                   bkey, mkey, row["search_keywords"],
                   row["created_at"], row["updated_at"]),
              )
          # Remap duplicate references in product_asins / product_tags
          for old_id, new_id in id_remap.items():
              if old_id == new_id:
                  continue
              conn.execute(
                  "UPDATE OR IGNORE product_asins SET product_id=? WHERE product_id=?",
                  (new_id, old_id),
              )
              conn.execute("DELETE FROM product_asins WHERE product_id=?", (old_id,))
              conn.execute(
                  "UPDATE OR IGNORE product_tags SET product_id=? WHERE product_id=?",
                  (new_id, old_id),
              )
              conn.execute("DELETE FROM product_tags WHERE product_id=?", (old_id,))
          conn.execute("DROP TABLE _products_v6")
      conn.execute(
          "INSERT OR IGNORE INTO schema_migrations (version, description) "
          "VALUES (7, 'normalize brand/model matching via brand_key/model_key')"
      )
      logger.info("Migrated schema to version 7")
  ```
- **MIRROR**：v6 迁移（L335 附近）用 rename + create + copy + drop 模式；migration 记录写法（L337）
- **IMPORTS**：无（`_normalize_key` 同文件）
- **GOTCHA**：
  1. SQLite 不能在 `ALTER TABLE ADD COLUMN` 后加 UNIQUE —— 必须 rename-create-copy-drop
  2. `product_asins.product_id` 有 `ON DELETE CASCADE`，但我们用 UPDATE 重定向，不靠 CASCADE
  3. `UPDATE OR IGNORE` 处理 (product_id, marketplace) 冲突（两条重复 product 都占用了同 marketplace），先 IGNORE 后 DELETE 残留
  4. 整块必须在 `with conn:` 事务里（L179-180 已是）
- **VALIDATE**：手工构造 v6 DB 含重复产品 `('TP-Link', 'X')` / `('TP-Link ', 'X')`，升级后只剩一行，product_asins 全部指向保留 id

### Task 4: 改写 `register_product` 用归一化键匹配
- **ACTION**：替换 L1430-1452 的 `register_product` 实现
- **IMPLEMENT**：
  ```python
  def register_product(
      conn: sqlite3.Connection,
      category: str,
      brand: str,
      model: str,
      search_keywords: str = "",
  ) -> tuple[int, bool]:
      """Insert a product, or return existing id if normalized (brand, model) matches.

      Returns ``(product_id, is_new)``.  Matching uses ``_normalize_key`` on
      brand/model so that surrounding whitespace, casing, and internal
      multi-space differences fold to the same identity.  The original
      ``brand``/``model`` strings are preserved for display.
      """
      brand_key = _normalize_key(brand)
      model_key = _normalize_key(model)
      row = conn.execute(
          "SELECT id FROM products WHERE brand_key = ? AND model_key = ?",
          (brand_key, model_key),
      ).fetchone()
      if row:
          return row["id"], False
      with conn:
          cur = conn.execute(
              "INSERT INTO products (category, brand, model, brand_key, model_key, search_keywords) "
              "VALUES (?, ?, ?, ?, ?, ?)",
              (category, brand, model, brand_key, model_key, search_keywords),
          )
      return cur.lastrowid, True  # type: ignore[return-value]
  ```
- **MIRROR**：原函数同位置 L1430-1452，保持签名、返回值 tuple、`with conn:` 事务、`type: ignore` 注释
- **IMPORTS**：无
- **GOTCHA**：
  1. 调用方传入的 `brand='TP-Link'` 还是首次入库值，保留为显示字面
  2. 如果两个调用方先后传 `'TP-Link'` / `'tp-link'`，第二次命中第一次的记录，第二次传入的 `'tp-link'` **不覆盖** 已存字面（符合 "first writer wins" 简化语义）
  3. 空 brand 或空 model：`_normalize_key('')==''` 合法值，但上游 `_try_register_product` L652 已拦空 → 不会走到这里
- **VALIDATE**：`register_product(conn, 'Router', 'TP-Link', 'Archer BE400')` 后再调 `register_product(conn, 'Router', ' tp-link ', 'archer  be400')` 返回同一 id；`products.brand == 'TP-Link'`（显示未被覆盖）

### Task 5: `_find_product_by_ean` 的 brand 过滤也归一化
- **ACTION**：修改 L723-725 的 brand guard
- **IMPLEMENT**：
  ```python
  # No brand available — EAN alone is sufficient evidence; skip brand guard
  if brand:
      # Normalize both sides so "TP-Link" and " tp-link " match
      sql += "    AND LOWER(TRIM(kp.brand)) = LOWER(TRIM(?))\n"
      params.append(brand)
  ```
  （这里用 SQL 侧 `LOWER(TRIM(...))` 而非 Python `_normalize_key`，因为 `keepa_products.brand` 不加 key 列；且 brand guard 是弱约束，ASCII LOWER+TRIM 已足够）
- **MIRROR**：现有 brand guard（L723-725）
- **IMPORTS**：无
- **GOTCHA**：这是 keepa_products 表，不是 products 表 —— 不要加 key 列，只改查询
- **VALIDATE**：手动构造 keepa_products 里 brand=`'TP-Link '` 和 `'TP-Link'` 各一条，同 EAN，传入 `brand='tp-link'` 能匹配到两条（当前只能匹配字面相同的）

### Task 6: 新增测试类 `TestBrandModelKeyMigrationV7`
- **ACTION**：在 `tests/test_db.py` 追加新测试类（`TestStatusMigrationV6` 之后）
- **IMPLEMENT**：至少 6 个测试方法：
  1. `test_v7_normalize_key_basic` — 直接测 `_normalize_key` 各种输入
  2. `test_v7_register_product_matches_whitespace_variants` — 新 DB 上验证 `'TP-Link'` 和 `' tp-link '` 命中同 id
  3. `test_v7_register_product_preserves_display` — 首次写入的字面不被后续调用覆盖
  4. `test_v7_unique_constraint_on_keys` — 尝试直接 INSERT 两行同归一化键但字面不同的 products，应 IntegrityError
  5. `test_v7_idempotent` — 对齐 v6 的幂等范式
  6. `test_v7_migrates_v6_db_and_merges_duplicates` — 强降到 v6，手动插入 `('TP-Link','X')` 和 `('TP-Link ','X')`（绕过当前 UNIQUE —— 在 v6 shape 下字面不同算两行），各绑不同 ASIN / tag，升级后 merge 成一行，ASIN/tag 聚合到保留 id
- **MIRROR**：`TestStatusMigrationV6` 全套（tests/test_db.py:365-500），特别是 `test_v6_migrates_legacy_statuses_to_active` 的强降范式
- **IMPORTS**：
  ```python
  from amz_scout.db import _normalize_key, register_product, register_asin, init_schema
  ```
- **GOTCHA**：
  1. 强降要 `DELETE FROM schema_migrations WHERE version = 7`，然后 `ALTER TABLE products RENAME TO _products_tmp` + 重建无 `brand_key`/`model_key` 的 v6 shape + 复制 + drop
  2. 测试用 `tmp_path` 拿到干净 DB 路径，不要用 conftest 的 `conn` fixture（那个已经是 v7）
  3. 合并测试：旧两行 id=10, id=11 → 升级后应只剩 id=10（min 保留），两者 asins/tags 都归到 10
- **VALIDATE**：`pytest tests/test_db.py::TestBrandModelKeyMigrationV7 -v` 全部通过

### Task 7: 文档更新
- **ACTION**：更新 `docs/DEVELOPER.md` 的 schema 版本表，添加 v7 条目；在 `CLAUDE.md` 的 Key Behaviors 追加第 14 条
- **IMPLEMENT**：
  - `docs/DEVELOPER.md`：`| 7 | normalize brand/model matching via brand_key/model_key | 2026-04-17 |`（对齐现有表格）
  - `CLAUDE.md`：
    ```
    14. **Brand/model 归一化匹配**：`register_product` 按 `_normalize_key(s)=" ".join(s.lower().split())` 做身份匹配；`products.brand`/`model` 保留首次入库字面作显示。调用方无需归一化，但查询侧不应再依赖字面精确比较。
    ```
- **MIRROR**：CLAUDE.md 现有 Key Behaviors 第 1-13 条的格式
- **IMPORTS**：N/A
- **GOTCHA**：CLAUDE.md 有 size 限制（tests/test_claude_md_size.py），追加一条时注意控制字数
- **VALIDATE**：`pytest tests/test_claude_md_size.py` 通过

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `_normalize_key('TP-Link')` | `'TP-Link'` | `'tp-link'` | No |
| `_normalize_key('  TP-Link  ')` | leading/trailing space | `'tp-link'` | Yes |
| `_normalize_key('Archer  BE400')` | double internal space | `'archer be400'` | Yes |
| `_normalize_key(None)` | None | `''` | Yes |
| `_normalize_key('')` | empty | `''` | Yes |
| `register_product` 首次 `'TP-Link', 'Archer BE400'` | — | `(id, True)` | No |
| `register_product` 再次 `' tp-link ', 'archer  be400'` | 同上归一化 | `(same_id, False)` | Yes |
| 首次入库后 `SELECT brand, model FROM products WHERE id=?` | 第二次传 `'tp-link'` | `brand='TP-Link'`（未覆盖） | Yes |
| 直接 `INSERT INTO products ... UNIQUE 冲突` | 同归一化键 | `sqlite3.IntegrityError` | Yes |
| v6→v7 升级含重复行 | `'TP-Link'+'X'` 和 `'TP-Link '+'X'` 各 1 | merge 后 1 行，asins/tags 聚合 | Yes |

### Edge Cases Checklist
- [x] 空 brand/model（`_normalize_key` 返回空串；上游已拦）
- [x] None 输入（`_normalize_key` 用 `(s or "")` 兜底）
- [x] 中文/Unicode（项目内 brand 全 ASCII，SQLite `LOWER` ASCII-only 够用 —— 若将来引入需要改 Python 层 casefold）
- [x] 强降 v6 DB 里手动 INSERT 两条字面不同但归一化相同的行（旧 UNIQUE 按字面，不会阻止）
- [x] 合并时 product_asins 同一 marketplace 冲突（`UPDATE OR IGNORE` + `DELETE` 残留）
- [x] 幂等（重复 `init_schema` 不应重跑 v7）

---

## Validation Commands

### Static Analysis
```bash
cd /Users/yuanzheyi/GL-iNet/Projects/BrowserScraper/amz-scout
ruff check src/amz_scout/db.py tests/test_db.py
```
EXPECT: 零 warning/error

### Unit Tests (focused)
```bash
pytest tests/test_db.py -v
```
EXPECT: 所有既有测试通过 + `TestBrandModelKeyMigrationV7` 全绿

### Full Test Suite
```bash
pytest
```
EXPECT: 无回归

### Database Validation
```bash
# 对本地 DB 做一次 init_schema 跑迁移，检查 v7 记录
python -c "
from amz_scout.db import get_connection, resolve_db_path, init_schema
conn = get_connection(resolve_db_path())
init_schema(conn)
print(conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])
print([dict(r) for r in conn.execute('PRAGMA table_info(products)').fetchall()])
"
```
EXPECT: `7`；列表里含 `brand_key` 和 `model_key`

### Manual Validation
- [ ] 跑迁移后 `products` 表行数 ≤ 迁移前（合并了重复）
- [ ] 所有 `product_asins` 的 `product_id` 都能在 `products.id` 找到（无 orphan）
- [ ] `register_product(conn, 'X', 'TP-Link', 'Archer BE400')` 和 `register_product(conn, 'X', ' tp-link ', 'Archer  BE400')` 返回同一 id
- [ ] 首次入库字面 `'TP-Link'` 在第二次调用后未被改成 `'tp-link'`
- [ ] 对现有生产 DB（如 BE10000）运行迁移后，跑一次 `query_compare` 对 Archer BE400，跨市场数据条数不减少

---

## Acceptance Criteria
- [ ] Task 1-7 全部完成
- [ ] 所有 validation 命令通过
- [ ] `TestBrandModelKeyMigrationV7` 6 个测试方法全绿
- [ ] 现有 `TestStatusMigrationV6` 及 register_product 相关测试无回归
- [ ] `tests/test_claude_md_size.py` 通过
- [ ] CLAUDE.md Key Behaviors 已追加第 14 条
- [ ] `docs/DEVELOPER.md` schema 版本表已更新

## Completion Checklist
- [ ] 代码遵循 `_snake_case` 私有函数 + 类型注解 + 单句 docstring 的现有范式
- [ ] 错误处理匹配仓库风格（`return None` 表示未找到，`logger.warning` 表示歧义，不 swallow）
- [ ] 日志用 `%s` 懒格式化，前缀 `"Migrated schema to version N"`
- [ ] 测试沿用 `TestXxxMigrationVN` 类 + `tmp_path` 强降范式
- [ ] 无硬编码（`SCHEMA_VERSION = 7` 是唯一来源）
- [ ] 文档同步更新
- [ ] 无额外 scope（不做 fuzzy 匹配、别名表、Unicode case）
- [ ] Self-contained —— 实现时无需再搜代码库

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 生产 DB 有跨 product_id 的 ASIN 合并冲突（两个重复 product 在同 marketplace 都绑了 ASIN，但 ASIN 不同） | 中 | 高（数据丢失） | 迁移用 `UPDATE OR IGNORE` + `DELETE`，日志记录被丢弃的 `(old_id, asin, marketplace)` 方便手动复查；建议迁移前备份 DB |
| `_normalize_key` 合并了原本用户认为不同的品牌（如 `'TP-Link'` vs `'TP Link'`） | 低 | 中 | 归一化只做大小写和空白 —— 连字符/特殊字符保留。文档明确声明 |
| 某些调用方依赖 `products.brand` 字面未被归一化（用于显示） | 低 | 低 | 显式保留原始字面，不改写；测试有 `test_v7_register_product_preserves_display` 覆盖 |
| SQLite 事务大小（合并重复时可能 UPDATE/DELETE 大量行） | 低 | 低 | 迁移整体在 `with conn:` 单事务，符合现有范式；本项目 DB 量小（<1k product），无性能风险 |
| v7 迁移跑到一半崩 → DB 半升级状态 | 低 | 高 | `with conn:` 自动回滚；`schema_migrations` 只在结束前 INSERT，失败时 version 仍为 6 |

## Notes

- 这个 plan 是 **v6 → v7 schema 迁移**，不是纯代码改动。注意区别于"加个 `LOWER()` 到查询"的低配方案
- 选用"保留字面 + 加 key 列"方案而非"归一化覆盖原始字段"是因为：(1) UI/报告用户习惯看 `TP-Link` 不是 `tp-link`；(2) 未来若需要 fuzzy display 或别名表，原始字面不可丢
- 未来扩展（不属于本 plan）：
  - 若观察到 `TP-Link` vs `TP Link`（连字符 vs 空格）的重复 → 加别名表 `brand_aliases(canonical_key, variant_key)`
  - 若引入非 ASCII 品牌 → 把 `_normalize_key` 里的 `.lower()` 换成 `.casefold()`，并用 Python 层归一化替代 SQL `LOWER(TRIM())`（Task 5）
- 迁移完成后，**旧的 `sync_registry_from_keepa`（db.py:1488）也会隐式受益**，因为它最终调 `_try_register_product` → `register_product` 走归一化路径，无需额外改
