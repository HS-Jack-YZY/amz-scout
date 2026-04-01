# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to Answer User Questions About Amazon Data

When the user asks about Amazon product data (prices, trends, rankings, availability), **use the Python API directly** — do not shell out to CLI commands.

### Decision Tree

```
User asks about product data (prices, trends, competitors, etc.)
  │
  ├─ "价格趋势" / "price trend" / "历史价格"
  │   → query_trends(project, product, marketplace, series)
  │
  ├─ "对比" / "compare" / "跨市场"
  │   → query_compare(project, product)
  │
  ├─ "排名" / "ranking" / "BSR"
  │   → query_ranking(project, marketplace)
  │
  ├─ "上架" / "availability" / "哪些国家有卖"
  │   → query_availability(project)
  │
  ├─ "卖家" / "seller" / "Buy Box" / "谁在卖"
  │   → query_sellers(project, product, marketplace)
  │
  ├─ "促销" / "deal" / "折扣"
  │   → query_deals(project, marketplace)
  │
  ├─ "最新数据" / "latest" / "当前价格"
  │   → query_latest(project, marketplace)
  │
  ├─ "数据新鲜度" / "多久没更新" / "freshness"
  │   → check_freshness(project)
  │
  ├─ "Keepa token" / "余额" / "budget"
  │   → keepa_budget()
  │
  ├─ "这个项目有哪些产品" / "配置" / "project info"
  │   → resolve_project(project)
  │
  └─ "刷新数据" / "更新" / "重新获取"
      → ensure_keepa_data(project, strategy="fresh")
```

### Calling the API

```python
from amz_scout.api import (
    resolve_project, resolve_product, ensure_keepa_data,
    query_latest, query_trends, query_compare, query_ranking,
    query_availability, query_sellers, query_deals,
    check_freshness, keepa_budget,
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
r = query_trends("BE10000", "Slate 7", "UK", series="new")
# r["data"] = [{"date": "2026-04-01 02:12", "value": 15099, ...}, ...]
# value 是 Keepa 编码: 除以 100 得到实际价格 (15099 → £150.99)
```

**"对比 RT-BE58 在所有市场的价格"**
```python
r = query_compare("BE10000", "RT-BE58")
# r["data"] = [{"site": "UK", "price_cents": 9997, ...}, {"site": "DE", ...}]
```

**"帮我查一下 BE10000 项目有哪些产品"**
```python
r = resolve_project("BE10000")
# r["data"]["products"] = [{"brand": "GL.iNet", "model": "GL-Slate 7 ...", ...}, ...]
# r["data"]["target_marketplaces"] = ["UK", "DE", "FR", ...]
```

**"Keepa 还有多少 token？"**
```python
r = keepa_budget()
# r["data"] = {"tokens_available": 55, "tokens_max": 60, "refill_rate": "1/min"}
```

**"确保英国的数据是最新的再查"**
```python
r = ensure_keepa_data("BE10000", marketplace="UK", strategy="fresh")
# r["meta"] = {"fetched": 18, "cached": 0, "skipped": 0, "tokens_used": 18, "tokens_remaining": 42}
# Then query:
r = query_trends("BE10000", "Slate 7", "UK")
```

**"数据多久没更新了？"**
```python
r = check_freshness("BE10000")
# r["data"] = [{"model": "GL-Slate 7 ...", "UK": "0d", "DE": "3d", "US": "never"}, ...]
```

### Key Behaviors to Remember

1. **Auto-fetch**: `query_trends`, `query_sellers`, `query_deals` auto-fetch missing Keepa data by default (LAZY strategy: fetch only if never fetched before, zero tokens if cached). No need to call `ensure_keepa_data` manually for these.

2. **Browser data cannot auto-fetch**: `query_latest`, `query_compare`, `query_ranking`, `query_availability` read from `competitive_snapshots` (browser scrape data). If empty, `meta["hint"]` tells the user to run `amz-scout scrape`.

3. **Marketplace aliases**: All marketplace parameters accept case variants (`"uk"`), Keepa codes (`"GB"`), Amazon domains (`"amazon.co.uk"`), and currency codes (`"GBP"`).

4. **Product resolution**: All product parameters accept model substrings (`"Slate 7"`) or ASINs (`"B0F2MR53D6"`). Case-insensitive.

5. **Token awareness**: Keepa Pro plan has 60 tokens, refills 1/min. Basic queries cost 1 token/product. Always check `keepa_budget()` before suggesting a `strategy="fresh"` refresh.

6. **Price encoding**: Keepa time series `value` is in cents (divide by 100). Rating is ×10 (45 = 4.5 stars). Sales rank is raw integer. value=-1 means unavailable.

### Available Projects

Check `config/` directory for project YAML files. Current projects:
- `BE10000` — GL.iNet BE10000 竞品分析 (17 products, 8 markets: UK/DE/FR/IT/ES/NL/CA/AU)
- `test_keepa` — Keepa 功能测试 (5 products, 3 markets: UK/DE/US)
- `JP_Competitor` — 日本市场竞品

When user doesn't specify a project, use `BE10000` as the default.

---

## Developer Reference

### Commands

```bash
# Install (editable mode)
pip install -e ".[dev]"

# ── Daily workflow ──
amz-scout scrape config/BE10000.yaml              # Full scrape (browser + Keepa)
amz-scout scrape config/BE10000.yaml --data-only  # Browser only, no Keepa tokens
amz-scout scrape config/BE10000.yaml -m UK -p "RT-BE58" --headed -v  # Debug single product
amz-scout keepa config/BE10000.yaml               # Smart Keepa fetch (default: 7-day cache)
amz-scout keepa config/BE10000.yaml --lazy        # Use cache no matter how old
amz-scout keepa config/BE10000.yaml --offline     # DB + raw JSON only, zero API calls
amz-scout keepa config/BE10000.yaml --fresh       # Force re-fetch from API
amz-scout keepa config/BE10000.yaml --check       # Show data freshness matrix
amz-scout keepa --budget                          # Show token balance
amz-scout discover config/BE10000.yaml            # Find cross-marketplace ASINs
amz-scout validate config/BE10000.yaml            # Validate config
amz-scout status config/BE10000.yaml              # Unified: CSV + DB + freshness overview

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
  │                                  12 public functions, no exceptions to caller
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
  │     └→ scraper/search.py       ASIN discovery via search fallback + auto-writeback to YAML
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
- **ASIN resolution has a 3-level fallback**: marketplace_overrides → default_asin → search. Found ASINs are auto-written back to the project YAML.
- **Keepa raw JSON is always saved** so `reparse` can regenerate CSVs without spending tokens.
- **All data models are frozen dataclasses** (immutable). CSV merge creates new lists rather than mutating.
- **Config uses Pydantic for validation**, data models use stdlib `dataclasses` — intentional split.

### Database Schema (db.py)

6 tables: `competitive_snapshots` (browser data), `keepa_time_series` (price arrays), `keepa_buybox_history`, `keepa_coupon_history`, `keepa_deals`, `keepa_products` (metadata). Series types 0-35 follow Keepa's csv[] indices; 100 = monthly_sold, 200+ = category rankings.

### Config Structure

- `config/marketplaces.yaml` — 13 marketplace definitions (domain, Keepa codes, currency, region, postcode)
- **Keepa support**: 11 of 13 marketplaces have Keepa API support. AU and NL are browser-only (`keepa_domain_code: null`). Valid domain codes are defined in `KEEPA_VALID_DOMAINS` in `config.py`.
- `config/<project>.yaml` — Product list, target marketplaces, scrape settings (retry_count, delays)
- Products can have `marketplace_overrides` for per-site ASINs and `search_keywords` for discovery fallback

### External Dependencies

- **browser-use CLI** (`uv tool install browser-use`) — not a pip dependency, called via subprocess
- **Keepa API** — requires `KEEPA_API_KEY` in `.env`; Pro plan = 60 tokens, 1/min refill

### Output Layout

```
output/
  ├── amz_scout.db                   # Shared SQLite database (all projects)
  └── <project>/data/{region}/
      ├── raw/{site}_{asin}.json     # Keepa raw responses (per-project)
      ├── {site}_competitive_data.csv  # Current Amazon page data
      └── {site}_price_history.csv     # Keepa 90-day price trends
```

- **Database is shared** across projects at `output/amz_scout.db` — Keepa data is ASIN-centric, no need to isolate
- **CSV and raw JSON remain per-project** for project-specific reports

Regions: `eu` (UK/DE/FR/IT/ES/NL), `na` (US/CA/MX), `apac` (JP/AU/IN), `sa` (BR)

### Conventions

- Python 3.12+, ruff for linting/formatting (line-length 100)
- Frozen dataclasses for all data models — never mutate, always create new
- `utils.py` contains parsers (price, rating, BSR) and a `@retry` decorator
- Marketplace setup logic in `marketplace.py` has per-country address handlers (standard EU, Canada 2-part postcode, Australia postcode+city)
