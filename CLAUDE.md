# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

amz-scout is a configuration-driven Amazon competitive data scraping tool. It automates collection of product page data (via browser-use CLI) and price history (via Keepa API) across 11 Amazon marketplaces, storing results in CSV + SQLite.

## Commands

```bash
# Install (editable mode)
pip install -e ".[dev]"

# Run
amz-scout scrape config/BE10000.yaml           # Full scrape (browser + Keepa)
amz-scout scrape config/BE10000.yaml --data-only  # Browser only, no Keepa tokens
amz-scout scrape config/BE10000.yaml --history-only  # Keepa only, no browser
amz-scout scrape config/BE10000.yaml -m UK -p "RT-BE58" --headed -v  # Debug single product
amz-scout discover config/BE10000.yaml          # Find cross-marketplace ASINs
amz-scout validate config/BE10000.yaml          # Validate config
amz-scout status config/BE10000.yaml            # Check data completeness
amz-scout reparse config/BE10000.yaml           # Regenerate CSV from raw JSON (free)

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
cli.py  ─────────────────────────  Typer CLI (5 commands: scrape/discover/validate/status/reparse)
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

- `config/marketplaces.yaml` — 11 marketplace definitions (domain, Keepa codes, currency, region, postcode)
- `config/<project>.yaml` — Product list, target marketplaces, scrape settings (retry_count, delays)
- Products can have `marketplace_overrides` for per-site ASINs and `search_keywords` for discovery fallback

## External Dependencies

- **browser-use CLI** (`uv tool install browser-use`) — not a pip dependency, called via subprocess
- **Keepa API** — requires `KEEPA_API_KEY` in `.env`; Pro plan = 60 tokens, 1/min refill

## Output Layout

```
output/<project>/data/{region}/
  ├── raw/{site}_{asin}.json       # Keepa raw responses
  ├── {site}_competitive_data.csv  # Current Amazon page data
  └── {site}_price_history.csv     # Keepa 90-day price trends
```

Regions: `eu` (UK/DE/FR/IT/ES/NL), `na` (US/CA/MX), `apac` (JP/AU)

## Conventions

- Python 3.12+, ruff for linting/formatting (line-length 100)
- Frozen dataclasses for all data models — never mutate, always create new
- `utils.py` contains parsers (price, rating, BSR) and a `@retry` decorator
- Marketplace setup logic in `marketplace.py` has per-country address handlers (standard EU, Canada 2-part postcode, Australia postcode+city)
- Keepa cents encoding: divide by 100 for price; -1 means unavailable
