# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

amz-scout is a configuration-driven Amazon competitive data scraping tool. It automates collection of product page data (via browser-use CLI) and price history (via Keepa API) across 11 Amazon marketplaces, storing results in CSV + SQLite.

## Commands

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

# ── Query (analysis from DB) ──
amz-scout query latest config/BE10000.yaml -m UK
amz-scout query trends config/BE10000.yaml -p "RT-BE58" -m UK --series new
amz-scout query compare config/BE10000.yaml -p "RT-BE58"
amz-scout query ranking config/BE10000.yaml -m UK
amz-scout query availability config/BE10000.yaml
amz-scout query sellers config/BE10000.yaml -p "RT-BE58" -m UK
amz-scout query deals config/BE10000.yaml

# ── Admin (one-time operations) ──
amz-scout admin reparse config/BE10000.yaml       # Regenerate CSV from raw JSON (free)
amz-scout admin migrate config/BE10000.yaml       # Import legacy data into SQLite
amz-scout admin merge-dbs                         # Consolidate per-project databases

# Test
pytest                        # All tests
pytest tests/test_utils.py    # Single file
pytest -m unit                # Unit tests only
pytest -m integration         # Integration tests only
pytest --cov=amz_scout        # With coverage

# Lint
ruff check src/ tests/        # Check
ruff check --fix src/ tests/  # Auto-fix
ruff format src/ tests/       # Format
```

## Architecture

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

## Config Structure

- `config/marketplaces.yaml` — 13 marketplace definitions (domain, Keepa codes, currency, region, postcode)
- **Keepa support**: 11 of 13 marketplaces have Keepa API support. AU and NL are browser-only (`keepa_domain_code: null`). Valid domain codes are defined in `KEEPA_VALID_DOMAINS` in `config.py`.
- `config/<project>.yaml` — Product list, target marketplaces, scrape settings (retry_count, delays)
- Products can have `marketplace_overrides` for per-site ASINs and `search_keywords` for discovery fallback

## External Dependencies

- **browser-use CLI** (`uv tool install browser-use`) — not a pip dependency, called via subprocess
- **Keepa API** — requires `KEEPA_API_KEY` in `.env`; Pro plan = 60 tokens, 1/min refill

## Output Layout

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
- Use `amz-scout admin merge-dbs` to consolidate existing per-project databases

Regions: `eu` (UK/DE/FR/IT/ES/NL), `na` (US/CA/MX), `apac` (JP/AU/IN), `sa` (BR)

## Conventions

- Python 3.12+, ruff for linting/formatting (line-length 100)
- Frozen dataclasses for all data models — never mutate, always create new
- `utils.py` contains parsers (price, rating, BSR) and a `@retry` decorator
- Marketplace setup logic in `marketplace.py` has per-country address handlers (standard EU, Canada 2-part postcode, Australia postcode+city)
- Keepa cents encoding: divide by 100 for price; -1 means unavailable

## Programmatic API (api.py)

All functions take simple strings and return a dict envelope:
```python
{"ok": True, "data": [...], "error": None, "meta": {...}}
```

### Usage

```python
from amz_scout.api import (
    resolve_project, resolve_product, ensure_keepa_data,
    query_latest, query_trends, query_compare, query_ranking,
    query_availability, query_sellers, query_deals,
    check_freshness, keepa_budget,
)

# Accept project name ("BE10000") or path ("config/BE10000.yaml")
info = resolve_project("BE10000")
# info["data"]["products"], info["data"]["target_marketplaces"]

# Ensure data exists (LAZY = fetch only if missing, zero tokens if cached)
ensure_keepa_data("BE10000", marketplace="UK")

# Query
trends = query_trends("BE10000", product="Slate 7", marketplace="UK", series="new")
# trends["data"] = [{"keepa_ts": ..., "value": ..., "date": "2026-04-01 02:12"}, ...]

compare = query_compare("BE10000", product="RT-BE58")
budget = keepa_budget()
```

### Functions

| Function | Args | Returns |
|----------|------|---------|
| `resolve_project(project)` | project name or path | products, marketplaces, ASINs |
| `resolve_product(project, query, marketplace?)` | model substring or ASIN | asin, model, source |
| `query_latest(project, marketplace?, category?)` | | competitive snapshots |
| `query_trends(project, product, marketplace, series?, days?, auto_fetch?)` | series: amazon\|new\|used\|sales_rank\|rating\|reviews | time series with dates |
| `query_compare(project, product)` | | cross-market comparison |
| `query_ranking(project, marketplace, category?)` | | BSR-sorted products |
| `query_availability(project)` | | availability matrix |
| `query_sellers(project, product, marketplace, auto_fetch?)` | | Buy Box seller history |
| `query_deals(project, marketplace?, auto_fetch?)` | | deal/promotion records |
| `ensure_keepa_data(project, marketplace?, product?, strategy?)` | strategy: lazy\|offline\|max_age\|fresh | fetch/cache counts, tokens used |
| `check_freshness(project, marketplace?, product?)` | | freshness matrix (Nd per cell) |
| `keepa_budget()` | | tokens available/max/refill rate |

### Smart query (auto-fetch)

`query_trends`, `query_sellers`, and `query_deals` have `auto_fetch=True` by default.
When enabled, missing Keepa data is fetched automatically (LAZY strategy: fetch only if
completely absent, never re-fetch stale data). The `meta` dict reports what happened:

```python
r = query_trends("BE10000", "Slate 7", "UK")
r["meta"]["auto_fetched"]     # True if data was just fetched, False if cached
r["meta"]["tokens_used"]      # only present if auto_fetched=True
```

Pass `auto_fetch=False` to skip auto-fetch (pure DB read, like `--offline`).

Browser-data queries (`query_latest`, `query_compare`, `query_ranking`, `query_availability`)
cannot auto-fetch. When results are empty, `meta["hint"]` explains how to populate data.

### Marketplace aliases

All functions accepting `marketplace` resolve aliases automatically:
- Case variants: `"uk"`, `"UK"` → `"UK"`
- Keepa domain codes: `"GB"` → `"UK"`, `"JP"` → `"JP"`
- Amazon domains: `"amazon.co.uk"` → `"UK"`, `"amazon.de"` → `"DE"`
- Currency codes: `"GBP"` → `"UK"`, `"JPY"` → `"JP"`

### Product resolution

`resolve_product` and query functions that take `product` accept:
- Model substrings: `"Slate 7"`, `"RT-BE58"`, `"BE550"` (case-insensitive)
- Direct ASINs: `"B0F2MR53D6"` (10-char alphanumeric)

Resolution uses the project config's product list first, not the database.
