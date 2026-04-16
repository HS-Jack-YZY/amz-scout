# Developer Reference

> Extracted from CLAUDE.md to reduce per-conversation token overhead.
> This file contains commands, architecture, schema, and conventions for human developers.

## Commands

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

## Architecture

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

## Key Design Decisions

- **browser-use is a subprocess**, not a Python library. `BrowserSession` calls the CLI via `subprocess.run()`. One session persists per marketplace.
- **ASIN resolution has a 4-level fallback**: DB registry → config products → ASIN pass-through → error. Found ASINs are auto-written to the SQLite product registry (not YAML).
- **Keepa raw JSON is always saved** so `reparse` can regenerate CSVs without spending tokens.
- **All data models are frozen dataclasses** (immutable). CSV merge creates new lists rather than mutating.
- **Config uses Pydantic for validation**, data models use stdlib `dataclasses` — intentional split.

## Database Schema (db.py)

9 tables: **Data**: `competitive_snapshots` (browser), `keepa_time_series` (price arrays), `keepa_buybox_history`, `keepa_coupon_history`, `keepa_deals`, `keepa_products` (metadata + fetch_mode). **Product registry**: `products`, `product_asins` (per-marketplace ASIN + status), `product_tags`. Series types 0-35 follow Keepa's csv[] indices; 100 = monthly_sold, 200+ = category rankings.

## Config Structure

- `config/marketplaces.yaml` — 13 marketplace definitions (domain, Keepa codes, currency, region, postcode). **Only required YAML file.**
- **Keepa support**: 11 of 13 marketplaces have Keepa API support. AU and NL are browser-only (`keepa_domain_code: null`).
- `config/<project>.yaml` — **Legacy import format**, not required for daily operations. Use `import_yaml()` to migrate to DB.

## External Dependencies

- **browser-use CLI** (`uv tool install browser-use`) — not a pip dependency, called via subprocess
- **Keepa API** — requires `KEEPA_API_KEY` in `.env`; Pro plan = 60 tokens, 1/min refill

## Output Layout

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

## Conventions

- Python 3.12+, ruff for linting/formatting (line-length 100)
- Frozen dataclasses for all data models — never mutate, always create new
- `utils.py` contains parsers (price, rating, BSR) and a `@retry` decorator
- Marketplace setup logic in `marketplace.py` has per-country address handlers (standard EU, Canada 2-part postcode, Australia postcode+city)

## Webapp Envelope Trimming

trim 只发生在 `webapp/tools.py` 的 `_step_*` 包装器上，通过 `@trim_for_llm(...)` 装饰器把 `amz_scout._llm_trim` 的白名单套到 envelope 的 `data` 字段。`amz_scout.api` 本身**永远返回完整 DB 行**，CLI 和 admin 工具看到全量字段。

白名单:
- `trim_competitive_rows`: 13 字段（site/category/brand/model/asin/price_cents/currency/rating/review_count/bought_past_month/bsr/available/scraped_at）
- `trim_timeseries_rows`: date + value
- `trim_seller_rows`: date + seller_id
- `trim_deals_rows`: 8 字段（asin/site/deal_type/badge/percent_claimed/deal_status/start_time/end_time）

若需添加字段，编辑 `src/amz_scout/_llm_trim.py` 对应 frozenset，然后跑 `pytest tests/test_token_audit.py` 确认 cost delta 可接受。**绝不**把 trim 调用搬回 `amz_scout/api.py`。**meta 从不过滤**。
