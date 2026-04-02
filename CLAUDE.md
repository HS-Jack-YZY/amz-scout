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
  │   └─ "验证 ASIN"              → validate_asins(marketplace=)
  │
  ├─ 产品注册表管理
  │   ├─ "有哪些产品"              → list_products(category?, brand?, marketplace?)
  │   ├─ "添加产品"                → add_product(category, brand, model, asins={...})
  │   ├─ "删除产品"                → remove_product_by_model(brand, model)
  │   ├─ "更新 ASIN"              → update_product_asin(brand, model, marketplace, asin)
  │   └─ "导入 YAML 配置"         → import_yaml(project_config, tag?)
  │
  └─ ASIN 发现（慢操作，需要浏览器）
      └─ "搜索正确的 ASIN"         → discover_asin(brand, model, marketplace)
```

### Calling the API

```python
from amz_scout.api import (
    # Queries
    query_latest, query_trends, query_compare, query_ranking,
    query_availability, query_sellers, query_deals,
    # Data management
    ensure_keepa_data, check_freshness, keepa_budget, validate_asins,
    # Product registry
    list_products, add_product, remove_product_by_model,
    update_product_asin, import_yaml, discover_asin,
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

### Key Behaviors to Remember

1. **Auto-fetch**: `query_trends`, `query_sellers`, `query_deals` auto-fetch missing Keepa data by default (LAZY strategy: fetch only if never fetched before, zero tokens if cached). No need to call `ensure_keepa_data` manually for these.

2. **Browser data cannot auto-fetch**: `query_latest`, `query_compare`, `query_ranking`, `query_availability` read from `competitive_snapshots` (browser scrape data). If empty, `meta["hint"]` tells the user to run `amz-scout scrape`.

3. **Marketplace aliases**: All marketplace parameters accept case variants (`"uk"`), Keepa codes (`"GB"`), Amazon domains (`"amazon.co.uk"`), and currency codes (`"GBP"`).

4. **Product resolution (4-level)**: DB registry first → config products fallback → ASIN pass-through → error. Accepts model substrings (`"Slate 7"`) or ASINs (`"B0F2MR53D6"`). Case-insensitive.

5. **Token awareness**: Keepa Pro plan has 60 tokens, refills 1/min. Basic queries cost 1 token/product. Always check `keepa_budget()` before suggesting a `strategy="fresh"` refresh.

6. **Price encoding**: Keepa time series `value` is in cents (divide by 100). Rating is ×10 (45 = 4.5 stars). Sales rank is raw integer. value=-1 means unavailable.

7. **Product registry (SQLite)**: Products and their per-marketplace ASINs are stored in `products` + `product_asins` tables. Use `add_product()` to register new products, `import_yaml()` to bulk-import from existing YAML configs. ASIN `status` tracks verification: `unverified` → `verified` / `wrong_product` / `not_listed` / `unavailable`. Use `validate_asins()` after fetching Keepa data to auto-verify by title matching.

8. **project 参数现在是可选的**: 所有 query 函数的 `project` 参数已改为 `str | None = None`。传 None 时从 SQLite 产品注册表加载产品，不需要 YAML 配置文件。传字符串时走旧的 YAML 路径（向后兼容）。

9. **ASIN 发现流程 (discover_asin)**: 当 `ensure_keepa_data` 返回 `warnings` 说 ASIN 无数据时，应该建议用户运行 `discover_asin(brand, model, marketplace)` 来搜索正确 ASIN。这是一个慢操作（10-30s，需要浏览器），所以不要自动执行，而是让用户确认后再调。发现的 ASIN 写入 DB 为 `unverified` 状态，之后可用 `validate_asins()` 确认。

10. **绝不猜测 ASIN**: 当产品不在注册表中时，不要从记忆中编造 ASIN。应该：(a) 问用户提供 ASIN，或 (b) 建议用 `discover_asin(brand, model, marketplace)` 浏览器搜索。错误的 ASIN 比没有 ASIN 更危险 — 会浪费 Keepa token 并返回空数据。

11. **product_tags 表暂不使用**: `product_tags` 表已建好但当前不作为任何功能的依赖。产品过滤统一用 `category` / `brand` / `marketplace` 三个维度，不用 tag。等真正需要分组时再启用。

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
