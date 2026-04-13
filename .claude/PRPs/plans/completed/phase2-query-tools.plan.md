# Plan: Phase 2 — Remaining Query Tools

## Summary

Expand `webapp/tools.py` from 1 wrapped query function to **all 9 read-only query functions** from `amz_scout.api`, so the LLM can answer every Scenario-1 research question (price trends, cross-marketplace compare, BSR ranking, availability matrix, seller history, deals, freshness, Keepa budget). This is purely additive: the `app.py`/`llm.py`/`auth.py`/`config.py` wiring from Phase 1 stays untouched. After this phase the chatbot can answer *"compare Slate 7 and BE5400 in UK/DE/US past 6 months"* by chaining tool calls.

## User Story

As 小李 (PM), I want to ask multi-step Amazon research questions in natural language, so that I can get cross-product, cross-marketplace price/BSR/sales/deals answers without sending an Excel list to Jack and waiting 1-2 days.

## Problem → Solution

**Current**: Phase 1 ships only `query_latest`. Asking *"show me price trends for Slate 7 in UK"* makes Sonnet 4.6 either (a) hallucinate a non-existent tool, (b) say "I can only show latest data", or (c) call `query_latest` and return wrong-shaped data. The webapp covers ~10% of the Scenario-1 surface area.

**Desired**: Sonnet 4.6 sees 9 typed tool definitions covering every Scenario-1 query need, with bilingual Chinese + English descriptions. Asking *"对比 Slate 7 和 BE5400 在 UK/DE/US 过去 6 个月的价格"* triggers a chain of `query_trends` calls (one per product × marketplace), each rendered as an expandable `cl.Step` showing resolved params and raw envelope. No reply ever falls back to *"I don't have that tool"*.

## Metadata

- **Complexity**: Small-to-Medium
- **Source PRD**: `.claude/PRPs/prds/internal-amz-scout-web.prd.md`
- **PRD Phase**: Phase 2 — Query tools
- **Estimated Files**: 1 modified (`webapp/tools.py`), 1 modified (`tests/test_webapp_smoke.py`)
- **Estimated Lines**: ~400 added (mostly tool schemas + docstrings + dispatch branches)
- **Estimated Time**: ~3 hours (matches PRD W1 D4-D5 budget)

---

## UX Design

### Before (Phase 1 ships)

```
User: "对比 Slate 7 和 BE5400 在 UK/DE/US 过去 6 个月的价格走势"
  ↓
LLM sees only [query_latest]
  ↓
🔧 query_latest(marketplace="UK")  ← wrong tool
  ↓
💬 "I can show the latest snapshot, but I don't have a tool
    for historical trends. Please ask Jack."  ← falls back to Jack
```

### After (Phase 2 target)

```
User: "对比 Slate 7 和 BE5400 在 UK/DE/US 过去 6 个月的价格走势"
  ↓
LLM sees all 9 query tools
  ↓
🔧 query_trends(product="Slate 7", marketplace="UK", days=180)
🔧 query_trends(product="Slate 7", marketplace="DE", days=180)
🔧 query_trends(product="Slate 7", marketplace="US", days=180)
🔧 query_trends(product="BE5400", marketplace="UK", days=180)
🔧 query_trends(product="BE5400", marketplace="DE", days=180)
🔧 query_trends(product="BE5400", marketplace="US", days=180)
  ↓ (each step expands to show params + envelope)
💬 "Here's a summary table:
    | Product | UK | DE | US |
    | Slate 7 | £150.99 → £142.50 (-5.6%) | ... | ...
    | BE5400  | ... | ... | ...
    Charts and a downloadable Excel will be added in Phase 5."
```

### Interaction Changes

| Touchpoint | Before (Phase 1) | After (Phase 2) | Notes |
|---|---|---|---|
| Tool surface | 1 tool (`query_latest`) | 9 tools | All read-only, no token-spending operations except auto-fetch on `query_trends`/`sellers`/`deals` |
| Multi-step queries | Impossible | Native | Chain handled by Phase 1's existing `run_chat_turn` loop (max 10 iterations) |
| Auto-fetch behavior | N/A | Inherited from `amz_scout.api` LAZY default | Zero token cost if cached; ≥6 token operations are gated by `phase="needs_confirmation"` from the API itself — the LLM sees the gate response and asks the user |
| Excel export | N/A | N/A — deferred to Phase 5 | Phase 2 returns text only |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/tools.py` | 1-80 | The exact pattern to mirror — single tool schema + step wrapper + dispatcher branch |
| P0 | `src/amz_scout/api.py` | 445-924 | All 9 query function signatures + return shapes |
| P0 | `tests/test_webapp_smoke.py` | 53-82 | Pattern for asserting `cache_control` placement + dispatch envelope shape |
| P1 | `webapp/llm.py` | 27-85 | Tool-use loop — confirms `dispatch_tool` is awaited and result is JSON-serialized for the model. No changes needed here. |
| P1 | `src/amz_scout/api.py` | 105-180 | `_resolve_context`, `_resolve_site`, `_envelope` — internals so you understand what error messages can leak from the API |
| P1 | `CLAUDE.md` | 1-200 | Decision tree the LLM is expected to follow — tool descriptions should match the Chinese phrases users will use |
| P2 | `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | 240-275 | Phase 2 PRD scope and success signal |
| P2 | `.claude/PRPs/plans/completed/phase1-webapp-scaffolding.plan.md` | all | Reference plan; Phase 2 is a strict subset of "add more of what Phase 1 did once" |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Anthropic prompt caching for tools | `https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching` | `cache_control` on the LAST tool block caches all preceding tools. Scattering it = 0% cache hit. |
| Anthropic tool input_schema | `https://docs.anthropic.com/en/docs/build-with-claude/tool-use` | Use JSON Schema; `required` is an array of property names; descriptions are the LLM's only signal — invest in them |

(No external research needed beyond confirming Phase 1's caching pattern is still correct in 2026 — it is.)

---

## Patterns to Mirror

### TOOL_SCHEMA_SHAPE
```python
# SOURCE: webapp/tools.py:19-48
{
    "name": "query_latest",
    "description": (
        "Get the latest Amazon competitive snapshot (current price, rating, BSR, "
        "availability) for products in a specific marketplace. Use this when the user "
        "asks about 'current' or 'latest' product data. Returns a list of product rows "
        "from the competitive_snapshots table in the database."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "marketplace": {
                "type": "string",
                "description": (
                    "Marketplace code (e.g., 'UK', 'DE', 'US', 'JP'). "
                    "Also accepts aliases like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                ),
            },
            "category": {
                "type": "string",
                "description": "Optional product category filter (e.g., 'Travel Router').",
            },
        },
        "required": ["marketplace"],
    },
    # Cache_control ONLY on the last tool in the list
}
```

### CACHE_CONTROL_PLACEMENT
```python
# SOURCE: webapp/tools.py:45-47 + tests/test_webapp_smoke.py:73-81
# Only the LAST tool in TOOL_SCHEMAS gets cache_control. The test enforces this.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "tool_1", ..., },                                  # no cache_control
    {"name": "tool_2", ..., },                                  # no cache_control
    ...
    {"name": "tool_N", ..., "cache_control": {"type": "ephemeral"}},  # ONLY here
]
```

### STEP_WRAPPER_PATTERN
```python
# SOURCE: webapp/tools.py:52-57
@cl.step(type="tool", name="query_latest")
async def _step_query_latest(marketplace: str, category: str | None = None) -> dict:
    """Chainlit step wrapper that shows tool inputs/outputs in the UI."""
    logger.info("query_latest called: marketplace=%s category=%s", marketplace, category)
    result = _api_query_latest(marketplace=marketplace, category=category)
    return result
```

Notes:
- `@cl.step(type="tool", name="<api_name>")` — UI shows this as an expandable node
- `async def` — Phase 1's `run_chat_turn` `await`s the dispatcher
- `logger.info(...)` BEFORE the call — surfaces in stdout for Jack debugging
- Return the envelope dict **unchanged** — the LLM consumes `ok`/`data`/`error`/`meta` directly

### DISPATCHER_BRANCH_PATTERN
```python
# SOURCE: webapp/tools.py:60-79
async def dispatch_tool(name: str, args: dict) -> dict:
    if name == "query_latest":
        return await _step_query_latest(
            marketplace=args.get("marketplace", ""),
            category=args.get("category"),
        )
    # Unknown tool — return an envelope-shaped error so the LLM can recover
    logger.error("Unknown tool: %s", name)
    return {
        "ok": False,
        "data": [],
        "error": f"Unknown tool: {name}",
        "meta": {},
    }
```

Pattern for new branches: explicit `args.get(key, default)` for required args and `args.get(key)` (None default) for optional. Never `args[key]` — the LLM occasionally drops fields and we want a graceful API error not a `KeyError` crash.

### TEST_PATTERN
```python
# SOURCE: tests/test_webapp_smoke.py:68-81
def test_tool_schemas_have_cache_control_on_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fake_env(monkeypatch)
    _reset_webapp_modules()
    from webapp.tools import TOOL_SCHEMAS

    assert len(TOOL_SCHEMAS) >= 1
    assert "cache_control" in TOOL_SCHEMAS[-1]
    for tool in TOOL_SCHEMAS[:-1]:
        assert "cache_control" not in tool
```

This test will continue to pass with N=9 — it asserts only "last has it, others don't", not "exactly one tool". Add 2 more tests in Phase 2: a per-tool name assertion and a dispatcher-routing assertion (see Testing Strategy).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `webapp/tools.py` | UPDATE | Add 8 tool schemas + 8 step wrappers + 8 dispatch branches; move `cache_control` from `query_latest` to the last new tool |
| `tests/test_webapp_smoke.py` | UPDATE | Add tests for: (a) all 9 tool names present, (b) dispatcher routes each name without raising, (c) cache_control still only on last |

## NOT Building

- **No Excel export** — Phase 5 owns `webapp/export.py`. Phase 2 returns envelope text only.
- **No `cl.File` attachments** — same reason.
- **No new SYSTEM_PROMPT changes** — Phase 1's prompt already says "call the available tools"; the LLM picks tools from the schema, not from prompt enumeration.
- **No management tools** (`list_products`, `add_product`, …) — Phase 3 owns these.
- **No high-risk tools** (`ensure_keepa_data`, `validate_and_discover`, `batch_discover`, `discover_asin`, `validate_asins`, `sync_registry`) — Phase 4 owns these (they need confirmation dialogs and `cl.Step` progress).
- **No background ASIN backfill orchestration** — CLAUDE.md rule 12 is about Claude Code agent behavior, NOT the webapp LLM. The webapp LLM will simply see `meta.new_product=True` in the envelope and decide what to do; it does not need to call WebSearch + `register_market_asins` on its own. (If Jack later wants this, it's a separate phase.)
- **No new env vars or config knobs.**
- **No changes to `app.py`, `llm.py`, `auth.py`, `config.py`** — Phase 1's wiring is sufficient.

---

## Step-by-Step Tasks

### Task 1: Add the 8 new API imports to `webapp/tools.py`

- **ACTION**: Replace the single import line with all 9 query imports.
- **IMPLEMENT**:
  ```python
  from amz_scout.api import (
      check_freshness as _api_check_freshness,
      keepa_budget as _api_keepa_budget,
      query_availability as _api_query_availability,
      query_compare as _api_query_compare,
      query_deals as _api_query_deals,
      query_latest as _api_query_latest,
      query_ranking as _api_query_ranking,
      query_sellers as _api_query_sellers,
      query_trends as _api_query_trends,
  )
  ```
- **MIRROR**: existing `from amz_scout.api import query_latest as _api_query_latest`
- **GOTCHA**: Keep the `_api_*` alias prefix so the local `_step_*` function names don't shadow the API names.
- **VALIDATE**: `python -c "from webapp import tools; print(len([f for f in dir(tools) if f.startswith('_api_')]))"` → 9

### Task 2: Add 8 new tool schemas to `TOOL_SCHEMAS`

- **ACTION**: Append 8 dicts in this exact order (alphabetical to keep diffs reviewable): `check_freshness`, `keepa_budget`, `query_availability`, `query_compare`, `query_deals`, `query_ranking`, `query_sellers`, `query_trends`. Place `query_trends` LAST so it carries `cache_control` (it's the most-called tool, fine to be the cache anchor). Remove `cache_control` from `query_latest`.

- **IMPLEMENT**: Each schema follows TOOL_SCHEMA_SHAPE. Below are the exact descriptions and parameter shapes — write them verbatim.

  **`check_freshness`**
  ```python
  {
      "name": "check_freshness",
      "description": (
          "Show how stale the cached Keepa data is for each product × marketplace, "
          "as a freshness matrix (e.g., '0d', '3d', 'never'). Use this when the user "
          "asks '数据多久没更新了' / 'how fresh is the data' / 'when was X last updated'. "
          "Read-only — does NOT fetch from Keepa, costs 0 tokens."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "marketplace": {
                  "type": "string",
                  "description": (
                      "Optional marketplace filter (e.g., 'UK', 'DE'). Omit to check all."
                  ),
              },
              "product": {
                  "type": "string",
                  "description": "Optional product filter (brand/model/ASIN). Omit to check all.",
              },
          },
          "required": [],
      },
  },
  ```

  **`keepa_budget`**
  ```python
  {
      "name": "keepa_budget",
      "description": (
          "Check the current Keepa API token balance and refill rate. Use when the user "
          "asks 'Keepa 还有多少 token' / 'how many Keepa tokens left' / 'token余额'. "
          "Costs 0 Keepa tokens to call."
      ),
      "input_schema": {
          "type": "object",
          "properties": {},
          "required": [],
      },
  },
  ```

  **`query_availability`**
  ```python
  {
      "name": "query_availability",
      "description": (
          "Show which products are listed on which marketplaces — an availability matrix "
          "across all sites. Use when the user asks '哪些国家有卖' / 'which countries sell X' / "
          "'availability'. Reads from the competitive_snapshots table; does NOT call Keepa."
      ),
      "input_schema": {
          "type": "object",
          "properties": {},
          "required": [],
      },
  },
  ```

  **`query_compare`**
  ```python
  {
      "name": "query_compare",
      "description": (
          "Compare ONE product across ALL marketplaces using the latest snapshot "
          "(price, rating, BSR per marketplace). Use when the user asks "
          "'对比 X 在所有市场' / 'compare X across markets' / 'cross-market'. "
          "Reads browser-scraped snapshots, not Keepa history."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "product": {
                  "type": "string",
                  "description": (
                      "Product identifier — brand/model name (e.g., 'Slate 7') or ASIN. "
                      "Required."
                  ),
              },
          },
          "required": ["product"],
      },
  },
  ```

  **`query_deals`**
  ```python
  {
      "name": "query_deals",
      "description": (
          "Show deal/promotion/discount history for products on a marketplace. Use when "
          "the user asks '促销' / '折扣' / 'deals' / 'discounts' / 'sale history'. "
          "Auto-fetches missing Keepa data using the LAZY strategy (zero tokens if "
          "cached). For ≥6-token batches the API returns phase='needs_confirmation' "
          "— surface that to the user and ask them to confirm."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "marketplace": {
                  "type": "string",
                  "description": (
                      "Marketplace code (e.g., 'UK', 'DE', 'US'). Omit to query all "
                      "marketplaces in the registry."
                  ),
              },
          },
          "required": [],
      },
  },
  ```

  **`query_ranking`**
  ```python
  {
      "name": "query_ranking",
      "description": (
          "Show products ranked by Amazon Best Sellers Rank (BSR) for a marketplace. "
          "Use when the user asks '排名' / 'BSR' / 'best sellers' / 'who's #1 in X'. "
          "Reads browser-scraped snapshots; does NOT call Keepa."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "marketplace": {
                  "type": "string",
                  "description": (
                      "Marketplace code (e.g., 'UK', 'DE'). Required. Accepts aliases "
                      "like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                  ),
              },
              "category": {
                  "type": "string",
                  "description": "Optional category filter (e.g., 'Travel Router').",
              },
          },
          "required": ["marketplace"],
      },
  },
  ```

  **`query_sellers`**
  ```python
  {
      "name": "query_sellers",
      "description": (
          "Show Buy Box seller history for ONE product on ONE marketplace — who has "
          "owned the Buy Box over time. Use when the user asks '卖家' / 'Buy Box' / "
          "'seller history' / 'who is selling X'. Auto-fetches missing Keepa data using "
          "the LAZY strategy (zero tokens if cached)."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "product": {
                  "type": "string",
                  "description": (
                      "Product identifier — brand/model name or ASIN. Required."
                  ),
              },
              "marketplace": {
                  "type": "string",
                  "description": (
                      "Marketplace code (e.g., 'UK', 'DE'). Defaults to 'UK' if omitted."
                  ),
              },
          },
          "required": ["product"],
      },
  },
  ```

  **`query_trends`** (LAST — carries `cache_control`)
  ```python
  {
      "name": "query_trends",
      "description": (
          "Show price/BSR/sales time series for ONE product on ONE marketplace over a "
          "given window. Use when the user asks '价格趋势' / '历史价格' / 'price trends' "
          "/ 'past N days/months'. Series options: 'new' (Amazon new price), 'used', "
          "'buybox', 'sales_rank', 'rating', 'review_count', 'monthly_sold'. "
          "Auto-fetches missing Keepa data using the LAZY strategy (zero tokens if "
          "cached). Prices in the response are encoded as cents — divide by 100 for the "
          "real price. To compare multiple products or marketplaces, call this tool "
          "multiple times (once per product × marketplace)."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "product": {
                  "type": "string",
                  "description": (
                      "Product identifier — brand/model name (e.g., 'Slate 7', "
                      "'GL-MT3000') or ASIN. Required."
                  ),
              },
              "marketplace": {
                  "type": "string",
                  "description": (
                      "Marketplace code (e.g., 'UK', 'DE', 'US', 'JP'). Defaults to 'UK'. "
                      "Accepts aliases like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                  ),
              },
              "series": {
                  "type": "string",
                  "description": (
                      "Series type. One of: 'new' (default), 'used', 'buybox', "
                      "'sales_rank', 'rating', 'review_count', 'monthly_sold'."
                  ),
                  "enum": [
                      "new",
                      "used",
                      "buybox",
                      "sales_rank",
                      "rating",
                      "review_count",
                      "monthly_sold",
                  ],
              },
              "days": {
                  "type": "integer",
                  "description": (
                      "Lookback window in days. Default 90. Use 7/30/90/180/365 for "
                      "common windows (e.g., 'past 6 months' → 180)."
                  ),
                  "minimum": 1,
                  "maximum": 730,
              },
          },
          "required": ["product"],
      },
      "cache_control": {"type": "ephemeral"},  # caches all preceding tools
  },
  ```

- **MIRROR**: TOOL_SCHEMA_SHAPE pattern + CACHE_CONTROL_PLACEMENT pattern.
- **GOTCHA**: Verify exactly ONE schema (the last) carries `cache_control`. Test enforces this.
- **VALIDATE**: `pytest tests/test_webapp_smoke.py::TestToolDispatch::test_tool_schemas_have_cache_control_on_last -v`

### Task 3: Add 8 step wrappers

- **ACTION**: Append 8 `@cl.step` async functions, one per new tool, after the existing `_step_query_latest`.
- **IMPLEMENT**: Each wrapper mirrors STEP_WRAPPER_PATTERN. Verbatim:

  ```python
  @cl.step(type="tool", name="check_freshness")
  async def _step_check_freshness(marketplace: str | None = None, product: str | None = None) -> dict:
      logger.info("check_freshness called: marketplace=%s product=%s", marketplace, product)
      return _api_check_freshness(marketplace=marketplace, product=product)


  @cl.step(type="tool", name="keepa_budget")
  async def _step_keepa_budget() -> dict:
      logger.info("keepa_budget called")
      return _api_keepa_budget()


  @cl.step(type="tool", name="query_availability")
  async def _step_query_availability() -> dict:
      logger.info("query_availability called")
      return _api_query_availability()


  @cl.step(type="tool", name="query_compare")
  async def _step_query_compare(product: str) -> dict:
      logger.info("query_compare called: product=%s", product)
      return _api_query_compare(product=product)


  @cl.step(type="tool", name="query_deals")
  async def _step_query_deals(marketplace: str | None = None) -> dict:
      logger.info("query_deals called: marketplace=%s", marketplace)
      return _api_query_deals(marketplace=marketplace)


  @cl.step(type="tool", name="query_ranking")
  async def _step_query_ranking(marketplace: str, category: str | None = None) -> dict:
      logger.info("query_ranking called: marketplace=%s category=%s", marketplace, category)
      return _api_query_ranking(marketplace=marketplace, category=category)


  @cl.step(type="tool", name="query_sellers")
  async def _step_query_sellers(product: str, marketplace: str = "UK") -> dict:
      logger.info("query_sellers called: product=%s marketplace=%s", product, marketplace)
      return _api_query_sellers(product=product, marketplace=marketplace)


  @cl.step(type="tool", name="query_trends")
  async def _step_query_trends(
      product: str,
      marketplace: str = "UK",
      series: str = "new",
      days: int = 90,
  ) -> dict:
      logger.info(
          "query_trends called: product=%s marketplace=%s series=%s days=%s",
          product, marketplace, series, days,
      )
      return _api_query_trends(
          product=product, marketplace=marketplace, series=series, days=days,
      )
  ```

- **MIRROR**: STEP_WRAPPER_PATTERN.
- **IMPORTS**: None new — `cl`, `logger`, and the `_api_*` aliases already exist.
- **GOTCHA**:
  - Do NOT pass `project=` to any of these — Phase 2 uses the DB-first registry path (CLAUDE.md rule 9). Letting `project` default to `None` is intentional.
  - Do NOT pass `auto_fetch=` — let the API default (`True`) take effect. The PRD's Should-have explicitly wants this.
  - For `query_sellers` / `query_trends`, default `marketplace="UK"` matches the API default but be explicit so the user-visible step shows the resolved value when omitted.
- **VALIDATE**: `python -c "import asyncio; from webapp.tools import _step_keepa_budget; asyncio.run(_step_keepa_budget())"` (skips Chainlit step UI but exercises the call path; will only succeed with real `KEEPA_API_KEY`).

### Task 4: Extend `dispatch_tool` with 8 new branches

- **ACTION**: Add 8 `elif` branches to `dispatch_tool`, in the same alphabetical order as the schemas.
- **IMPLEMENT**:
  ```python
  async def dispatch_tool(name: str, args: dict) -> dict:
      if name == "query_latest":
          return await _step_query_latest(
              marketplace=args.get("marketplace", ""),
              category=args.get("category"),
          )
      if name == "check_freshness":
          return await _step_check_freshness(
              marketplace=args.get("marketplace"),
              product=args.get("product"),
          )
      if name == "keepa_budget":
          return await _step_keepa_budget()
      if name == "query_availability":
          return await _step_query_availability()
      if name == "query_compare":
          return await _step_query_compare(product=args.get("product", ""))
      if name == "query_deals":
          return await _step_query_deals(marketplace=args.get("marketplace"))
      if name == "query_ranking":
          return await _step_query_ranking(
              marketplace=args.get("marketplace", ""),
              category=args.get("category"),
          )
      if name == "query_sellers":
          return await _step_query_sellers(
              product=args.get("product", ""),
              marketplace=args.get("marketplace", "UK"),
          )
      if name == "query_trends":
          return await _step_query_trends(
              product=args.get("product", ""),
              marketplace=args.get("marketplace", "UK"),
              series=args.get("series", "new"),
              days=args.get("days", 90),
          )

      logger.error("Unknown tool: %s", name)
      return {
          "ok": False,
          "data": [],
          "error": f"Unknown tool: {name}",
          "meta": {},
      }
  ```
- **MIRROR**: DISPATCHER_BRANCH_PATTERN.
- **GOTCHA**:
  - Use independent `if`/`return` (not `elif`) — same as Phase 1. Keeps the diff small and easier to grep.
  - Always use `args.get(key, default)` — never `args[key]`. The LLM occasionally drops fields.
  - For optional args (`category`, `product` filter on `check_freshness`, `marketplace` on `query_deals`/`check_freshness`), default to `None` (not `""`) so they hit the API's "all sites/all products" path, not the "filter by empty string" path.
- **VALIDATE**: All 9 names route without raising (added in Task 5).

### Task 5: Extend smoke tests

- **ACTION**: Add 2 new tests to `tests/test_webapp_smoke.py::TestToolDispatch`.
- **IMPLEMENT**:
  ```python
  def test_all_phase2_tool_names_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
      _set_fake_env(monkeypatch)
      _reset_webapp_modules()
      from webapp.tools import TOOL_SCHEMAS

      names = {tool["name"] for tool in TOOL_SCHEMAS}
      expected = {
          "query_latest",
          "check_freshness",
          "keepa_budget",
          "query_availability",
          "query_compare",
          "query_deals",
          "query_ranking",
          "query_sellers",
          "query_trends",
      }
      assert names == expected, f"Missing or extra tools: {names ^ expected}"

  def test_dispatcher_routes_all_known_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
      """Every declared tool must route through dispatch_tool without KeyError/AttributeError.

      The underlying API call may legitimately return ok=False (no DB, no Keepa key,
      missing product) — we only assert the envelope shape, never that ok=True.
      """
      _set_fake_env(monkeypatch)
      _reset_webapp_modules()
      from webapp.tools import TOOL_SCHEMAS, dispatch_tool

      for tool in TOOL_SCHEMAS:
          name = tool["name"]
          # Build a minimal args dict satisfying required fields with safe placeholders
          args: dict = {}
          for prop in tool["input_schema"].get("required", []):
              args[prop] = "UK" if prop == "marketplace" else "Slate 7"
          result = asyncio.run(dispatch_tool(name, args))
          assert isinstance(result, dict), f"{name}: not a dict"
          assert "ok" in result, f"{name}: missing 'ok' key"
          assert "data" in result, f"{name}: missing 'data' key"
          assert "error" in result, f"{name}: missing 'error' key"
          assert "meta" in result, f"{name}: missing 'meta' key"
  ```
- **MIRROR**: TEST_PATTERN.
- **IMPORTS**: None new — `asyncio`, `pytest`, `_set_fake_env`, `_reset_webapp_modules` already in the file.
- **GOTCHA**:
  - The dispatcher routing test exercises **real** `amz_scout.api` calls with a fake DB — many will return `ok=False` because the fake `KEEPA_API_KEY="fake"` makes Keepa initialization fail and there's no real DB. The test asserts ENVELOPE SHAPE only, never `ok=True`. This is intentional — we're testing the dispatcher, not the API.
  - If a tool raises an unhandled exception (e.g., `AttributeError` from a typo), pytest catches it and the test fails with a useful traceback. This is the value of the test.
  - If `_api_query_trends` triggers a Keepa fetch attempt and hangs on network, we have a problem. But auto_fetch only fires when there's a real DB row to compare against — with no DB, the resolution step fails first and returns `ok=False`. Confirmed by reading `query_trends` lines 476-510.
- **VALIDATE**: `pytest tests/test_webapp_smoke.py -v -k Phase2 or all_phase2 or routes_all_known`

### Task 6: Hand-test the running webapp

- **ACTION**: Boot the dev server and exercise 3 representative flows.
- **IMPLEMENT** (manual):
  1. `chainlit run webapp/app.py -w`
  2. Log in with whitelisted email + `APP_PASSWORD` from `.env`
  3. Type each of these and verify tool calls render in the UI:
     - `"Keepa 还有多少 token"` → expect `keepa_budget` step → real number
     - `"列出所有产品在哪些市场上有卖"` → expect `query_availability` step → matrix
     - `"GL-Slate 7 在英国过去 6 个月的价格趋势"` → expect `query_trends(product="GL-Slate 7", marketplace="UK", days=180)` → time series rows
- **GOTCHA**:
  - First `query_trends` call on a never-fetched ASIN may take 10-30s (Keepa fetch). The Phase 1 `cl.Step` already shows pending state so this is acceptable.
  - If the user asks "compare X in UK and DE" and the LLM only calls `query_trends` once, that's a SYSTEM_PROMPT or model-behavior issue, NOT a Phase 2 bug. Note it for Phase 7 (Alpha) iteration but don't reopen Phase 2.
- **VALIDATE**: 3/3 flows render envelope data in chat. Logs show `query_*` info lines.

---

## Testing Strategy

### Unit Tests (smoke, isolated)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_tool_schemas_have_cache_control_on_last` | (existing) | Last tool has `cache_control`, others don't | yes |
| `test_all_phase2_tool_names_present` | all 9 expected names | exact set match | yes |
| `test_dispatcher_routes_all_known_tools` | each tool with min args | envelope dict (ok may be True/False) | yes |
| `test_unknown_tool_returns_envelope` | (existing) | `ok=False`, `Unknown tool` in error | yes |

### Edge Cases Checklist

- [x] Empty args dict on every required-field tool → graceful `ok=False`, no `KeyError`
- [x] Unknown tool name → envelope-shaped error
- [x] All 9 names routable
- [x] Cache control invariant preserved
- [x] LLM dropping an optional field (e.g., `category`) → handled by `args.get(key, None)` defaults
- [x] LLM passing `marketplace="amazon.co.uk"` (alias) → API's `_resolve_site` handles it (no Phase 2 work)
- [x] `query_trends` with non-existent product → API returns `ok=False` with descriptive `error` (CLAUDE.md rule 11 — never invent ASINs); LLM should ask user

### NOT tested in Phase 2 (deferred or out-of-scope)

- LLM judgment quality (does Sonnet 4.6 pick the right tool for a Chinese question?) → Phase 7 Alpha
- Real Keepa fetch correctness → already tested in `tests/test_api.py`
- Excel export → Phase 5
- Chainlit feedback round-trip → Phase 7 (`@cl.action_callback`)

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/ tests/test_webapp_smoke.py
ruff format --check webapp/ tests/test_webapp_smoke.py
```
EXPECT: zero issues

### Phase 2 Smoke Tests
```bash
pytest tests/test_webapp_smoke.py -v
```
EXPECT: All tests pass (4 existing + 2 new = 6 total)

### Full Test Suite (no regressions)
```bash
pytest -q
```
EXPECT: same pass/skip count as on `main` before this branch (Phase 2 must not break Phase 1 tests, API tests, or scraper tests)

### Import Surface
```bash
python -c "from webapp.tools import TOOL_SCHEMAS, dispatch_tool; print(len(TOOL_SCHEMAS), [t['name'] for t in TOOL_SCHEMAS])"
```
EXPECT: `9 ['query_latest', 'check_freshness', 'keepa_budget', 'query_availability', 'query_compare', 'query_deals', 'query_ranking', 'query_sellers', 'query_trends']`

### Browser Validation (manual)
```bash
chainlit run webapp/app.py -w
```
EXPECT: Server boots, login works, each of the 3 representative queries from Task 6 returns envelope data in the chat with a tool step expanded.

### Manual Validation Checklist
- [ ] `chainlit run webapp/app.py -w` boots without errors
- [ ] Login with `@gl-inet.com` email + APP_PASSWORD succeeds
- [ ] `"Keepa 还有多少 token"` triggers `keepa_budget` step, returns real token count
- [ ] `"列出所有产品在哪些市场上有卖"` triggers `query_availability`, returns matrix
- [ ] `"GL-Slate 7 在英国过去 6 个月的价格趋势"` triggers `query_trends(product=..., marketplace="UK", days=180)`, returns time series rows
- [ ] Each tool step in the UI shows the resolved params and raw envelope JSON
- [ ] No `Unknown tool` errors logged

---

## Acceptance Criteria

- [ ] All 9 query tools declared in `TOOL_SCHEMAS`
- [ ] All 9 step wrappers exist
- [ ] Dispatcher routes all 9 names
- [ ] Only the LAST tool (`query_trends`) carries `cache_control`
- [ ] All smoke tests pass (existing + 2 new)
- [ ] `ruff check` zero issues
- [ ] Manual flow: 3/3 representative queries return envelope data in the running webapp
- [ ] Zero changes to `app.py`, `llm.py`, `auth.py`, `config.py`, `amz_scout/`
- [ ] Diff is purely additive in `webapp/tools.py` (and one cache_control move)

## Completion Checklist

- [ ] Tool schemas mirror Phase 1's exact shape (description + input_schema + cache_control placement)
- [ ] Bilingual descriptions cover the Chinese phrases from CLAUDE.md's decision tree (`价格趋势`, `对比`, `排名`, `上架`, `卖家`, `促销`, `数据多久没更新`, `Keepa 还有多少 token`)
- [ ] Logger calls match Phase 1 format (`logger.info("query_X called: param=%s", ...)`)
- [ ] No `print()` statements
- [ ] No `args[key]` access — all `args.get(key, default)`
- [ ] No `project=` argument passed (DB-first per CLAUDE.md rule 9)
- [ ] No `auto_fetch=False` override — let API default win
- [ ] No new files outside the two listed in "Files to Change"
- [ ] Self-contained — implementation does NOT need to consult `amz_scout/` source again

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM picks the wrong tool for an ambiguous Chinese phrase | Medium | Low (cosmetic — user can re-ask) | Bilingual descriptions cover the decision-tree phrases verbatim. Phase 7 Alpha will surface real misrouting cases. |
| Tool schema descriptions exceed Anthropic's per-tool token budget and inflate the cached prompt | Low | Low | Each description is ~80-120 tokens. 9 tools × 120 ≈ 1100 tokens — well under the 200K context. Caching makes the marginal cost ~0 after warmup. |
| `query_trends` `enum` constraint on `series` rejects a future series the API adds | Low | Low | The enum mirrors the documented `SERIES_MAP` keys. If the API adds one, Phase 2 needs a one-line schema update — minor. |
| Auto-fetch on `query_trends` triggers a `phase="needs_confirmation"` envelope the LLM doesn't know how to handle | Low | Medium | The envelope is JSON-serialized into the tool result, and Sonnet 4.6 is good at reading structured `meta` fields. SYSTEM_PROMPT's "show which tool you called" already nudges it toward surfacing the gate. Worst case: Phase 4 hardens this with explicit confirmation UI. |
| The dispatcher routing test triggers a real network call and hangs CI | Low | Medium | `_set_fake_env` sets `KEEPA_API_KEY="fake"`, so any code path that constructs `KeepaClient` raises immediately and the API returns `ok=False`. No network. (Verified by reading `keepa_budget` lines 907-918 and `query_trends` resolution path.) If it ever does hang, wrap the test in `@pytest.mark.timeout(5)`. |

## Notes

- **Why `query_trends` is last (cache anchor)**: It will be by far the most-called tool (every "show me trends" request hits it). Anchoring `cache_control` on it minimizes cache invalidation churn — any future Phase 3/4 additions go BEFORE it in the list and inherit the same cache key.
- **Why no SYSTEM_PROMPT change**: The Phase 1 prompt is intentionally short. Adding tool enumeration to it is redundant — Anthropic's tool-use API tells the model what's available. Long prompts also bloat the cache footprint.
- **Why no `webapp/export.py` peek-ahead**: Tempting to start it here since the LLM will quickly want Excel for `query_trends` results. Resist — Phase 5 owns that and depends on Q3 (interview with 小李) to know the format. Premature scaffolding would lock in the wrong shape.
- **Pattern this leaves for Phase 3**: Phase 3 (management tools) will copy this exact pattern but introduce the `phase="needs_confirmation"` UI consumer. The dispatcher branch shape stays the same; only the step wrapper grows a check for `result.get("meta", {}).get("phase")`. Keep that in mind so Phase 2's wrappers don't accidentally do that work prematurely.
- **CLAUDE.md rule 12 clarification**: That rule is about Claude Code (the agent typing into Jack's terminal) doing background ASIN backfill. The webapp LLM is a different runtime — it sees `meta.new_product=True` in the envelope but the webapp does NOT call WebSearch or `register_market_asins`. Surfacing the flag to the user is sufficient for MVP. If Jack later wants the webapp to auto-backfill, that's a Phase 4+ feature with its own tool-use round.
