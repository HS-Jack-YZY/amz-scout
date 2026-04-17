# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to Answer User Questions About Amazon Data

When the user asks about Amazon product data (prices, trends, rankings, availability), **use the Python API directly** — do not shell out to CLI commands.

### Decision Tree

```
User asks about product data
  │
  ├─ 查询数据（不需要 YAML，自动从 DB 读取产品）
  │   ├─ "价格趋势" / "历史价格"   → query_trends(product=, marketplace=, series=)
  │   ├─ "对比" / "跨市场"          → query_compare(product=)
  │   ├─ "排名" / "BSR"            → query_ranking(marketplace=)
  │   ├─ "上架" / "哪些国家有卖"    → query_availability()
  │   ├─ "卖家" / "Buy Box"        → query_sellers(product=, marketplace=)
  │   ├─ "促销" / "折扣"           → query_deals(marketplace=)
  │   └─ "最新数据" / "当前价格"    → query_latest(marketplace=)
  │
  ├─ 数据管理
  │   ├─ "刷新数据" / "更新"        → ensure_keepa_data(marketplace=, strategy="fresh")
  │   ├─ "数据新鲜度"              → check_freshness()
  │   ├─ "Keepa token 余额"       → keepa_budget()
  │   └─ "同步注册表"              → sync_registry()
  │
  ├─ 产品注册表管理
  │   ├─ "有哪些产品"              → list_products(category?, brand?, marketplace?)
  │   ├─ "添加产品"                → add_product(category, brand, model, asins={...})
  │   ├─ "删除产品"                → remove_product_by_model(brand, model)
  │   ├─ "更新 ASIN"              → update_product_asin(brand, model, marketplace, asin)
  │   └─ "导入 YAML 配置"         → import_yaml(project_config, tag?)
  │
  └─ ASIN 发现（慢操作，需要浏览器）
      ├─ "搜索正确的 ASIN"         → discover_asin(brand, model, marketplace)
      └─ "批量搜索 ASIN"           → batch_discover(candidates=[...])
```

> Intent 验证（判断 ASIN 是否对应用户想要的产品）自 v6 起不再由系统预校验。用户从查询返回的 Keepa `title` 自查；误配时改用 `discover_asin()` 或补全更精确的 brand/model。

### Calling the API

```python
from amz_scout.api import (
    query_latest, query_trends, query_compare, query_ranking,
    query_availability, query_sellers, query_deals,
    ensure_keepa_data, check_freshness, keepa_budget, sync_registry,
    list_products, add_product, remove_product_by_model, update_product_asin,
    register_market_asins, get_pending_markets, import_yaml, discover_asin,
    batch_discover, resolve_product,
)
```

Every function returns `{"ok": bool, "data": ..., "error": str|None, "meta": {...}}`.

### Examples

**"GL-Slate 7 在英国的价格趋势"**
```python
r = query_trends(product="Slate 7", marketplace="UK", series="new")
# r["data"] = [{"date": "2026-04-01 02:12", "value": 15099, ...}, ...]
# value 是 Keepa 编码: 除以 100 得到实际价格 (15099 → £150.99)
```

**"把 TP-Link AX1500 加到产品列表"**
```python
r = add_product("Travel Router", "TP-Link", "AX1500",
                asins={"JP": "B0JP1234AB", "UK": "B0UK5678CD"})
```

**"数据多久没更新了？"**
```python
r = check_freshness()
# r["data"] = [{"model": "GL-Slate 7 ...", "UK": "0d", "DE": "3d", "US": "never"}, ...]
```

### Key Behaviors

1. **Auto-fetch**: `query_trends`/`query_sellers`/`query_deals` 自动 LAZY fetch（从未获取才 fetch，缓存命中 0 token）。无需手动调 `ensure_keepa_data`。
2. **Browser data 不能 auto-fetch**: `query_latest`/`query_compare`/`query_ranking`/`query_availability` 读 `competitive_snapshots`。为空时 `meta["hint"]` 提示跑 `amz-scout scrape`。
3. **Marketplace aliases**: 接受 `"uk"` / `"GB"` / `"amazon.co.uk"` / `"GBP"` 等变体。
4. **Product resolution**: DB registry → config → ASIN pass-through（`^[A-Z0-9]{10}$`，自动 fetch+注册）→ error。
5. **Token 门控**: Keepa 60 tokens, 1/min。`ensure_keepa_data()` 预估 ≥6 token 时返回 `phase="needs_confirmation"`，传 `confirm=True` 执行。
6. **phase 协议**: `"needs_confirmation"` → 展示+确认+`confirm=True` 重调；`"pending_confirmation"` → 确认后调 `batch_discover()`；无 phase → 正常完成。
7. **Price encoding**: value÷100=价格（分→元）。Rating×10（45=4.5★）。rank 原始整数。-1=不可用。
8. **Product registry**: 身份管理系统（跨市场 ASIN 关联），注册≠监控。三种注册：`add_product()` / `import_yaml()` / Keepa 写入自动注册。
9. **project 参数**: `str | None = None`。None→SQLite，字符串→旧 YAML 路径。
10. **ASIN 下架检测**: `ensure_keepa_data()` post-fetch 自动把空 title + 无 csv 的 ASIN 标记为 `not_listed`；查询门会拒绝。无需显式 validate。**Intent 错配**（拿到的产品不是用户想要的）由用户看返回 Keepa title 自查自修，不在系统侧预校验。
11. **绝不猜测 ASIN**: 不在注册表→问用户或用 `discover_asin()`。直接给 ASIN 查询会自动 fetch+注册。
12. **禁止直接调用 Keepa API**: 所有 Keepa 操作必须通过 `amz_scout.api`。找 ASIN 优先级：(1) 问用户 → (2) WebSearch 搜 Amazon URL → (3) `discover_asin()`。违规导致 60 token 耗尽，阻塞 1 小时。
13. **product_tags 暂不使用**: 过滤用 `category` / `brand` / `marketplace`。

### Data Source

产品数据在 SQLite 注册表（`products` + `product_asins`）。用 `import_yaml("BE10000")` 导入或 `add_product()` 注册。YAML 配置非必需。

---

> Developer reference (commands, architecture, schema): see [docs/DEVELOPER.md](docs/DEVELOPER.md)
