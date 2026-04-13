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
  │   ├─ "验证 ASIN"              → validate_asins(marketplace=)
  │   ├─ "验证并发现"              → validate_and_discover(marketplace=)
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

### Calling the API

```python
from amz_scout.api import (
    # Queries
    query_latest, query_trends, query_compare, query_ranking,
    query_availability, query_sellers, query_deals,
    # Data management
    ensure_keepa_data, check_freshness, keepa_budget, validate_asins,
    sync_registry,
    # Product registry
    list_products, add_product, remove_product_by_model,
    update_product_asin, register_market_asins, get_pending_markets,
    import_yaml, discover_asin,
    # Validate + discover workflow
    validate_and_discover, batch_discover,
    # Resolution helpers
    resolve_product,
)
```

Every function returns a dict envelope:
```python
{"ok": True, "data": [...], "error": None, "meta": {...}}
{"ok": False, "data": [], "error": "...", "meta": {}}
```

Always check `result["ok"]` before using `result["data"]`.

### Examples (Chinese + English)

**"GL-Slate 7 在英国的价格趋势"**
```python
r = query_trends(product="Slate 7", marketplace="UK", series="new")
# r["data"] = [{"date": "2026-04-01 02:12", "value": 15099, ...}, ...]
# value 是 Keepa 编码: 除以 100 得到实际价格 (15099 → £150.99)
```

**"对比 RT-BE58 在所有市场的价格"**
```python
r = query_compare(product="RT-BE58")
# r["data"] = [{"site": "UK", "price_cents": 9997, ...}, {"site": "DE", ...}]
```

**"有哪些产品在监控？"**
```python
r = list_products()
# r["data"] = [{"brand": "GL.iNet", "model": "GL-Slate 7 ...", "marketplace": "UK", "asin": "...", ...}, ...]
```

**"Keepa 还有多少 token？"**
```python
r = keepa_budget()
# r["data"] = {"tokens_available": 55, "tokens_max": 60, "refill_rate": "1/min"}
```

**"确保英国的数据是最新的再查"**
```python
r = ensure_keepa_data(marketplace="UK", strategy="fresh")
# r["meta"] = {"fetched": 18, "cached": 0, "skipped": 0, "tokens_used": 18, "tokens_remaining": 42}
# Then query:
r = query_trends(product="Slate 7", marketplace="UK")
```

**"数据多久没更新了？"**
```python
r = check_freshness()
# r["data"] = [{"model": "GL-Slate 7 ...", "UK": "0d", "DE": "3d", "US": "never"}, ...]
```

**"把 TP-Link AX1500 加到产品列表，日本和英国都要监控"**
```python
r = add_product("Travel Router", "TP-Link", "AX1500",
                asins={"JP": "B0JP1234AB", "UK": "B0UK5678CD"}, tag="travel_routers")
# r["data"] = {"id": 18, "brand": "TP-Link", "model": "AX1500"}
```

**"修改 AX1500 在日本的 ASIN，之前那个是错的"**
```python
r = update_product_asin("TP-Link", "AX1500", "JP", "B0NEWJPASIN", status="verified")
```

**"列出所有 Travel Router 产品"**
```python
r = list_products(category="Travel Router")
# r["data"] = [{"brand": "TP-Link", "model": "AX1500", "marketplace": "JP", "asin": "...", ...}, ...]
```

**"把 BE10000 的配置导入到产品库"**
```python
r = import_yaml("BE10000")
# r["data"] = {"tag": "BE10000", "products_imported": 17, "asins_registered": 147}
```

**"验证 ASIN 并告诉我哪些需要重新搜索"**
```python
r = validate_and_discover(marketplace="UK")
# r["meta"]["phase"] = "pending_confirmation"
# r["meta"]["discover_pending"] = [
#     {"brand": "GL.iNet", "model": "GL-Slate 7 ...", "marketplace": "UK",
#      "old_asin": "B0XXXXXX", "reason": "no title in Keepa"},
# ]
# → 展示给用户确认后再执行 batch_discover()
```

**"确认了，帮我批量搜索这些 ASIN"**
```python
r = batch_discover(candidates=r["meta"]["discover_pending"], headed=True)
# r["data"] = [{"brand": "GL.iNet", "model": "...", "marketplace": "UK",
#               "old_asin": "B0XXXXXX", "new_asin": "B0YYYYYY", "ok": True}, ...]
# r["meta"] = {"discovered": 3, "failed": 1}
```

### Key Behaviors to Remember

1. **Auto-fetch**: `query_trends`, `query_sellers`, `query_deals` auto-fetch missing Keepa data by default (LAZY strategy: fetch only if never fetched before, zero tokens if cached). No need to call `ensure_keepa_data` manually for these.

2. **Browser data cannot auto-fetch**: `query_latest`, `query_compare`, `query_ranking`, `query_availability` read from `competitive_snapshots` (browser scrape data). If empty, `meta["hint"]` tells the user to run `amz-scout scrape`.

3. **Marketplace aliases**: All marketplace parameters accept case variants (`"uk"`), Keepa codes (`"GB"`), Amazon domains (`"amazon.co.uk"`), and currency codes (`"GBP"`).

4. **Product resolution (4-level + auto-registration)**: DB registry → config products → ASIN pass-through → error。第 3 级（ASIN 透传）行为：
   - **格式校验**：`^[A-Z0-9]{10}$`，不匹配直接报错。非 B 开头加软警告（可能是 ISBN）。
   - **跨市场感知**：如果 ASIN 在 DB 中注册于其他市场，`meta.warnings` 会提醒。
   - **自动注册**：ASIN 透传时构造临时 Product 自动 LAZY fetch（消耗 1 token）。如果 Keepa 返回有效的 brand + title，**自动注册到 products + product_asins**。`meta.auto_registered=True` + `meta.registered_as` 包含注册信息。下次查询将直接在 DB registry 命中（level 1）。
   - **跳过条件**：如果 Keepa 返回的 brand 或 title 为空（产品不存在或数据不完整），不注册。

5. **Token awareness + 批量门控**: Keepa Pro 60 tokens, 1/min 恢复。`ensure_keepa_data()` 预估 token >= 6 时自动返回 `phase="needs_confirmation"` + 成本预览，传 `confirm=True` 后才执行。单次查询（<6 token）直接执行不拦截。

6. **phase 响应协议**: API 可能返回 `meta.phase` 字段：
   - `"needs_confirmation"` → 展示 `meta.message` + `data.preview` 给用户确认，确认后加 `confirm=True` 重新调用
   - `"pending_confirmation"` → 展示 `meta.discover_pending` 给用户确认，确认后调 `batch_discover()`
   - 无 phase 字段 → 正常完成，直接展示数据

7. **Price encoding**: Keepa time series `value` 是分为单位（除以 100）。Rating ×10（45 = 4.5 星）。Sales rank 是原始整数。value=-1 表示不可用。

8. **Product registry (SQLite)**: 注册表是产品身份管理系统（跨市场 ASIN 关联），不是监控白名单。注册 ≠ 监控刷新。Products 和 per-marketplace ASINs 在 `products` + `product_asins` 表。产品通过三种方式注册：(a) `add_product()` 手动注册，(b) `import_yaml()` 批量导入，(c) **Keepa 数据写入时自动注册**（brand+title 非空即注册）。`sync_registry()` 可一次性注册所有已有 Keepa 数据但未注册的孤儿 ASIN。

9. **project 参数可选**: 所有 query 函数的 `project` 参数为 `str | None = None`。传 None 从 SQLite 加载产品。传字符串走旧 YAML 路径（向后兼容）。

10. **ASIN 验证 + 发现流程**: 用 `validate_and_discover(marketplace=)` 一站式。默认返回 `phase="pending_confirmation"` + `discover_pending` 列表。确认后用 `batch_discover(candidates=...)` 执行。也可 `auto_discover=True` 一步到位（10-30s/个）。

11. **绝不猜测 ASIN**: 不在注册表的产品不要编造 ASIN。应该：(a) 问用户提供 ASIN，或 (b) 用 `discover_asin()` 浏览器搜索。用户直接给 ASIN 查询时会自动 fetch + 自动注册（如果 Keepa 有数据），无需手动操作。

12. **新产品后台 ASIN 补全（重要，所有注册路径均适用）**:

    **触发条件（满足任意一条即执行补全）**：
    - `query_trends` 返回 `meta.new_product=True`
    - `add_product()` 返回 `meta.new_product=True`（现已内置，所有 add_product 调用均返回）
    - `import_yaml()` 导入后（导入完成后统一对新产品执行）
    - 批量操作（如循环调用 `add_product`）：**循环结束后**对所有新产品统一执行一次

    ```
    优先级顺序：
    1. 先完成用户请求的所有市场查询（用户要 US+DE 就先查 US+DE）
    2. 回答用户的问题（展示数据）
    3. 最后静默补全其余市场的 ASIN（用户不需要知道这个过程）

    补全步骤：
    1. 从 add_product 返回的 meta.pending_markets 直接读取待搜索市场
       （或调用 get_pending_markets(product_id) 重新获取）
    2. 对 pending 列表中的每个市场，用 WebSearch 搜索 ASIN：
       搜索格式: "{brand} {model} site:{domain}"
       domain 从 meta.pending_domains（或 get_pending_markets）获取
    3. 从搜索结果的 Amazon URL 中提取 ASIN（/dp/B0XXXXXXXX 格式）
    4. 调用 register_market_asins(product_id, {"UK": "B0XXX", "DE": "B0YYY", ...})
    5. 注册完成后简短告知用户："已补全 X 个市场的 ASIN"
    ```

    **注意事项**：
    - 用户的查询请求永远优先，后台补全最后做
    - 批量注册（多个产品）时，并行发起 WebSearch，最后统一 register_market_asins
    - 搜不到的市场跳过，不要猜测
    - 只写 product_asins，不调 Keepa API（0 token）
    - 如果 WebSearch 返回的不是 Amazon 产品页（无 /dp/ 路径），跳过该市场
    - **禁止跳过此步骤**：无论是直接 add_product 还是通过 query_trends 触发，补全都必须执行

13. **禁止直接调用 Keepa API（严格执行）**:
    - **绝不**用 `requests.get("https://api.keepa.com/...")` 或任何方式直接调用 Keepa API。所有 Keepa 操作必须通过 `amz_scout.api` 函数。
    - **绝不**调用 Keepa search endpoint (`/search`)。一次搜索消耗 10+ token，极易耗尽额度。
    - 当用户给产品名而非 ASIN 时，按此优先级找 ASIN：(1) 问用户 → (2) WebSearch 工具搜 Amazon 产品页 URL 提取 ASIN（0 token）→ (3) `discover_asin()` 浏览器搜索（0 token，慢）。
    - 违反此规则会导致 token 耗尽（60 token 上限，1/min 恢复），阻塞所有用户至少 1 小时。

14. **product_tags 表暂不使用**: 表已建好但不作为功能依赖。过滤统一用 `category` / `brand` / `marketplace`。

### Available Projects

产品数据在 SQLite 产品注册表中（`products` + `product_asins` 表）。用 `import_yaml("BE10000")` 从 YAML 导入，或用 `add_product()` 直接注册。

**所有 CLI 命令和 API 函数都不再需要 YAML 配置文件。** YAML 通过 `--config` 参数仍然支持（向后兼容），但不是必需的。

---

## Developer Reference

### Commands

```bash
# Install (editable mode)
pip install -e ".[dev]"

# ── Daily workflow (no config file needed — reads from DB) ──
amz-scout scrape -m UK                            # Scrape all DB products on UK
amz-scout scrape -p "RT-BE58" -m UK --headed -v   # Debug single product
amz-scout scrape -c "Travel Router"               # Scrape by category
amz-scout keepa -m UK                             # Smart Keepa fetch (default: 7-day cache)
amz-scout keepa --lazy                            # Use cache no matter how old
amz-scout keepa --fresh -m UK                     # Force re-fetch from API
amz-scout keepa --check -m UK                     # Show data freshness matrix
amz-scout keepa --budget                          # Show token balance
amz-scout discover -m UK                          # Find cross-marketplace ASINs (browser)
amz-scout status -m UK                            # CSV + DB + freshness overview

# ── Query (no config file needed) ──
amz-scout query latest -m UK
amz-scout query trends -p "RT-BE58" -m UK --series new
amz-scout query compare -p "RT-BE58"
amz-scout query ranking -m UK
amz-scout query sellers -p "RT-BE58" -m UK
amz-scout query deals -m UK

# ── Legacy YAML mode (still supported via --config) ──
amz-scout scrape --config config/BE10000.yaml -m UK
amz-scout keepa --config config/BE10000.yaml --check
amz-scout validate config/BE10000.yaml            # Validate config (YAML only)

# ── Admin (one-time operations) ──
amz-scout admin reparse config/BE10000.yaml       # Regenerate CSV from raw JSON (free)
amz-scout admin migrate config/BE10000.yaml       # Import legacy data into SQLite
amz-scout admin merge-dbs                         # Consolidate per-project databases

# Test
pytest                        # All tests
pytest tests/test_api.py      # API layer tests
pytest --cov=amz_scout        # With coverage

# Lint
ruff check src/ tests/        # Check
ruff check --fix src/ tests/  # Auto-fix
ruff format src/ tests/       # Format

# ── Deployment (Phase 6, production) ──
docker compose up -d --build          # Build + start webapp + Caddy edge
docker compose logs -f webapp         # Tail webapp logs
docker compose logs -f caddy          # Tail TLS / ACME logs
scripts/smoke_deploy.sh "$DOMAIN"     # End-to-end deploy smoke test
# Full runbook: deploy/README.md
```

### Architecture

```
api.py  ─────────────────────────  Programmatic API (strings in, dicts out)
  │                                  20+ public functions, no exceptions to caller
  │
cli.py  ─────────────────────────  Typer CLI (thin shell, delegates to api.py for queries)
  │
  ├─→ config.py                    YAML loading via Pydantic (ProjectConfig + MarketplaceConfig)
  │     reads: config/marketplaces.yaml + config/<project>.yaml
  │
  ├─→ scraper/keepa.py             KeepaClient: HTTP wrapper for Keepa API
  │     - 1 token/product (basic) or ~6 tokens/product (--detailed)
  │     - Auto-waits for token refill
  │     - Saves raw JSON to output/<project>/data/{region}/raw/
  │     - Parses → PriceHistory dataclass
  │
  ├─→ browser.py                   BrowserSession: subprocess wrapper around `browser-use` CLI
  │     └→ marketplace.py          Per-marketplace setup (cookies, delivery address, currency)
  │     └→ scraper/amazon.py       JS extraction from product pages → CompetitiveData dataclass
  │     └→ scraper/search.py       ASIN discovery via search fallback + auto-writeback to DB
  │
  ├─→ freshness.py                  Strategy evaluation (lazy/offline/max-age/fresh) — pure functions
  ├─→ keepa_service.py              Cache-first orchestration: check DB → read raw JSON or fetch API
  │
  ├─→ csv_io.py                    Read/write/merge CSVs (key: date+site+model)
  ├─→ db.py                        SQLite (WAL mode) with 6 tables, query functions for analysis
  └─→ models.py                    Frozen dataclasses: Product, CompetitiveData, PriceHistory
```

### Key Design Decisions

- **browser-use is a subprocess**, not a Python library. `BrowserSession` calls the CLI via `subprocess.run()`. One session persists per marketplace.
- **ASIN resolution has a 4-level fallback**: DB registry → config products → ASIN pass-through → error. Found ASINs are auto-written to the SQLite product registry (not YAML).
- **Keepa raw JSON is always saved** so `reparse` can regenerate CSVs without spending tokens.
- **All data models are frozen dataclasses** (immutable). CSV merge creates new lists rather than mutating.
- **Config uses Pydantic for validation**, data models use stdlib `dataclasses` — intentional split.

### Database Schema (db.py)

9 tables: **Data**: `competitive_snapshots` (browser), `keepa_time_series` (price arrays), `keepa_buybox_history`, `keepa_coupon_history`, `keepa_deals`, `keepa_products` (metadata + fetch_mode). **Product registry**: `products`, `product_asins` (per-marketplace ASIN + status), `product_tags`. Series types 0-35 follow Keepa's csv[] indices; 100 = monthly_sold, 200+ = category rankings.

### Config Structure

- `config/marketplaces.yaml` — 13 marketplace definitions (domain, Keepa codes, currency, region, postcode). **Only required YAML file.**
- **Keepa support**: 11 of 13 marketplaces have Keepa API support. AU and NL are browser-only (`keepa_domain_code: null`).
- `config/<project>.yaml` — **Legacy import format**, not required for daily operations. Use `import_yaml()` to migrate to DB.

### External Dependencies

- **browser-use CLI** (`uv tool install browser-use`) — not a pip dependency, called via subprocess
- **Keepa API** — requires `KEEPA_API_KEY` in `.env`; Pro plan = 60 tokens, 1/min refill

### Output Layout

```
output/
  ├── amz_scout.db                   # Shared SQLite database (product registry + all data)
  └── data/{region}/
      ├── raw/{site}_{asin}.json     # Keepa raw responses
      ├── {site}_competitive_data.csv  # Current Amazon page data
      └── {site}_price_history.csv     # Keepa 90-day price trends
```

- **Database is the single source of truth** for products, ASINs, and all Keepa/competitive data
- **Raw JSON and CSV** are per-region flat directories (no per-project nesting in DB-first mode)

Regions: `eu` (UK/DE/FR/IT/ES/NL), `na` (US/CA/MX), `apac` (JP/AU/IN), `sa` (BR)

### Conventions

- Python 3.12+, ruff for linting/formatting (line-length 100)
- Frozen dataclasses for all data models — never mutate, always create new
- `utils.py` contains parsers (price, rating, BSR) and a `@retry` decorator
- Marketplace setup logic in `marketplace.py` has per-country address handlers (standard EU, Canada 2-part postcode, Australia postcode+city)
