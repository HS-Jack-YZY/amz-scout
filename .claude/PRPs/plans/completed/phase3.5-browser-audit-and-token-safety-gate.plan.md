# Plan: Phase 3.5 — Browser Route Audit + Token Safety Gate

## Summary

Phase 3.5 of the `internal-amz-scout-web` PRD. Two surgical deliverables: (A) a 30-minute `competitive_snapshots` field-by-field audit answering "Keepa 是否能替代浏览器抓取"（Q8）, output as `docs/browser-route-audit.md`; (B) a Chainlit confirmation dialog for `amz_scout.api.ensure_keepa_data` that consumes the existing `phase="needs_confirmation"` protocol, preventing accidental ≥6-token Keepa burns before Alpha.

## User Story

- **(A)** As Jack (工程负责人), I want a field-level audit of what browser scraping uniquely provides, so that I can make an evidence-based decision on whether to keep investing in the browser route or mark it `deprecated-candidate`.
- **(B)** As a Chainlit webapp user (小李/Jack), I want a confirmation dialog with token cost preview before a batch Keepa fetch, so that I don't accidentally burn the 60-token/min Keepa budget and block everyone for an hour.

## Problem → Solution

**Before**: Q8 (浏览器路线 ROI) is an open assumption; `ensure_keepa_data` with `estimated_tokens ≥ 6` returns `phase="needs_confirmation"` in the envelope but no UI layer consumes it, so Alpha users could trigger a silent 60-token burn.

**After**: Q8 has a written answer with coverage math; `ensure_keepa_data` returns render an explicit confirm/cancel dialog in Chainlit, and `confirm=True` is only sent after a human click.

## Metadata

- **Complexity**: **Medium** (~2.5h total: Part A ~30min pure docs + Part B ~2h wiring)
- **Source PRD**: `.claude/PRPs/prds/internal-amz-scout-web.prd.md`
- **PRD Phase**: 3.5 (Browser route audit + token safety gate; new per council B− 2026-04-24)
- **Estimated Files**: 4 touched (1 CREATE doc, 1 UPDATE `tools.py`, 1 CREATE test, 1 UPDATE PRD phase status)

---

## UX Design

### Before (Part B only — Part A is pure docs)

```
User: "刷新所有产品的 Keepa 数据"
  ↓
LLM → ensure_keepa_data(strategy="fresh")  [tool call, no confirm]
  ↓
API returns envelope with
  {ok: True, phase: "needs_confirmation",
   meta: {estimated_tokens: 12, products_to_fetch: 12}}
  ↓
LLM receives envelope → paraphrases "would you like to proceed?"
  ↓
User types "yes" in chat
  ↓
LLM re-calls ensure_keepa_data(confirm=True)
  ↓
Fetch executes; 12 tokens burned
```

**Problem**: LLM paraphrasing can miss the token cost, "yes" is ambiguous across languages, and the delay invites accidental double-fires.

### After

```
User: "刷新所有产品的 Keepa 数据"
  ↓
LLM → ensure_keepa_data(strategy="fresh")
  ↓
API returns envelope with phase="needs_confirmation"
  ↓
webapp/tools.py _step_ensure_keepa_data detects phase
  ↓
cl.AskActionMessage renders in UI:
  ┌──────────────────────────────────────────────────┐
  │ ⚠️ Keepa fetch confirmation                      │
  │                                                   │
  │ Products to fetch: 12                            │
  │ Estimated token cost: 12 / 60 available           │
  │ Strategy: fresh                                   │
  │                                                   │
  │ [✓ Confirm & fetch]  [✗ Cancel]                  │
  └──────────────────────────────────────────────────┘
  ↓
User clicks → _step_ensure_keepa_data:
  • Confirm → re-calls ensure_keepa_data(confirm=True), returns result
  • Cancel  → returns cancelled envelope, LLM surfaces clean cancel text
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Keepa batch fetch confirmation | LLM paraphrases envelope, user types "yes" | Native UI button pair | Deterministic; no language ambiguity |
| Token cost surfacing | LLM decides whether/how to mention | Always shown in dialog title row | Removes LLM-dependent accuracy risk |
| Cancellation | User ignores or types "no" → LLM guesses intent | Explicit `Cancel` action → envelope flows back | Unambiguous |

---

## Mandatory Reading

Files that MUST be read before implementing:

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 (critical) | `src/amz_scout/api.py` | 847-935 | `ensure_keepa_data` function signature + `_BATCH_TOKEN_THRESHOLD = 6` + exact shape of `needs_confirmation` envelope (meta keys: `estimated_tokens`, `products_to_fetch`; data: `{"preview": [{"asin", "site", "model"}, ...]}`) |
| P0 (critical) | `webapp/tools.py` | 126-358 | Existing `TOOL_SCHEMAS` list with Anthropic tool-schema shape; cache_control on **last** tool only (line 356) — new `ensure_keepa_data` schema must NOT disturb the tail cache marker |
| P0 (critical) | `webapp/tools.py` | 361-603 | Wrapper pattern for `_step_*` (decorator stack + `asyncio.to_thread` to keep blocking API calls off the event loop) + `dispatch_tool` dispatcher |
| P1 (important) | `src/amz_scout/db.py` | 664-700 | `competitive_snapshots` schema — authoritative field list for Part A audit |
| P1 (important) | `webapp/summaries.py` | 214-268 | `summarize_for_llm` decorator — `ensure_keepa_data` does NOT need this (doesn't return row data), but the decorator reference shows the correct "dict wrapping returns" idiom |
| P1 (important) | `webapp/app.py` | 49-92 | `on_message` handler — tool-use happens inside `run_chat_turn`, which calls `dispatch_tool` synchronously per-turn; our AskActionMessage is `await`-ed inside dispatch and naturally blocks that path |
| P1 (important) | `webapp/llm.py` | 176-197 | Tool-use loop: `dispatch_tool` returns dict, gets `json.dumps`'d into tool_result; our return value must be JSON-serializable |
| P2 (reference) | `tests/test_webapp_smoke.py` | all | Test fixture/mock patterns for the webapp layer |
| P2 (reference) | `tests/test_api.py` | search for `ensure_keepa_data` | Existing `ensure_keepa_data` API-level tests — confirm the envelope shape in the test, don't re-invent |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Chainlit `AskActionMessage` | https://docs.chainlit.io/api-reference/ask/ask-for-action (Chainlit 2.x) | Returns `cl.AskActionResponse \| None` on timeout; `actions=[cl.Action(name, value, description, label)]`; default `timeout=90s`; None when user closes tab |
| Chainlit `cl.Action` signature | Chainlit 2.x source | `cl.Action(name: str, value: str, label: str, payload: dict)` — `payload` (dict) is what the user picked; `label` is the button text. `description` deprecated in 2.x. |

---

## Patterns to Mirror

Code patterns discovered in the codebase. Follow these exactly.

### TOOL_SCHEMA_ANTHROPIC_SHAPE
```python
# SOURCE: webapp/tools.py:146-166
{
    "name": "check_freshness",
    "description": (
        "Cached Keepa data staleness matrix per product × marketplace "
        "('0d'/'3d'/'never'). For '数据多久没更新'. Read-only, 0 Keepa tokens."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "marketplace": {
                "type": "string",
                "description": "Optional marketplace filter. Omit for all.",
            },
            "product": {
                "type": "string",
                "description": "Optional product filter (brand/model/ASIN). Omit for all.",
            },
        },
        "required": [],
    },
},
```
**Rules**:
1. Bilingual description (Chinese trigger words + English canonical)
2. `required` array even when empty
3. `cache_control: {"type": "ephemeral"}` goes ONLY on the **last** schema (currently line 356 `register_asin_from_url`). Adding a new tool **before** the tail is free; adding **after** means moving the cache marker.

### STEP_WRAPPER_PATTERN
```python
# SOURCE: webapp/tools.py:388-391
@cl.step(type="tool", name="keepa_budget")
async def _step_keepa_budget() -> ApiResponse:
    logger.info("keepa_budget called")
    return await asyncio.to_thread(_api_keepa_budget)
```
**Rules**:
1. `@cl.step(type="tool", name="<exact-tool-name>")` decorator
2. `async def _step_<name>`
3. **Always** `asyncio.to_thread(blocking_api_call)` — `amz_scout.api` is synchronous; calling directly blocks the Chainlit WebSocket (see `webapp/llm.py` comment at L115-121)
4. `logger.info(...)` before the call with all args
5. Return the raw envelope dict (no transformation — transformations live in decorator stack for row tools; `ensure_keepa_data` needs a custom confirm branch instead)

### DISPATCH_DISPATCHER_PATTERN
```python
# SOURCE: webapp/tools.py:542-548
if name == "check_freshness":
    return await _step_check_freshness(
        marketplace=args.get("marketplace"),
        product=args.get("product"),
    )
if name == "keepa_budget":
    return await _step_keepa_budget()
```
**Rules**:
1. One `if name == "..."` branch per tool
2. Required-field guards via `_missing_required(tool, field)` → envelope-shaped error (see line 78-91)
3. Use `args.get(...)` with default for optional; direct `args[...]` only AFTER `_missing_required` check
4. Unknown tool → envelope-shaped error (lines 595-602), never raise

### LOGGING_CONVENTION
```python
# SOURCE: webapp/tools.py:382-384
logger.info("check_freshness called: marketplace=%s product=%s", marketplace, product)
```
**Rules**: `logger.info("<tool_name> called: <arg1>=%s <arg2>=%s", arg1, arg2)` — percent-style, never f-strings (CLAUDE.md user rule says `print()` banned, `logging` only; percent-style is the codebase default for lazy-format safety)

### ASK_ACTION_MESSAGE_PATTERN (NEW — not yet used in codebase)
```python
# NEW PATTERN — no existing usage in webapp/*.py (verified via grep)
# Adapted from Chainlit 2.x docs
actions = [
    cl.Action(name="confirm", value="confirm",
              label="✓ Confirm & fetch",
              payload={"proceed": True}),
    cl.Action(name="cancel", value="cancel",
              label="✗ Cancel",
              payload={"proceed": False}),
]
response = await cl.AskActionMessage(
    content=(
        f"⚠️ Keepa fetch confirmation\n\n"
        f"• Products to fetch: **{products_to_fetch}**\n"
        f"• Estimated token cost: **{estimated_tokens} / 60** available\n"
        f"• Strategy: `{strategy}`\n\n"
        f"This will consume Keepa API tokens (shared, 1/min refill)."
    ),
    actions=actions,
    timeout=120,
).send()
# response: cl.AskActionResponse | None
# None when user closed tab or timeout elapsed
proceed = bool(response and response.get("payload", {}).get("proceed"))
```
**Rules**:
1. `timeout` in seconds; 120s gives slow thinkers room without hanging the tool-use loop forever
2. `response is None` when user abandoned → treat as cancel
3. `response["payload"]["proceed"]` is our decision contract — don't inspect `name`/`value` (they're displayable labels, payload is the data bus)

### TEST_STRUCTURE
```python
# SOURCE: tests/test_webapp_smoke.py (pattern for async Chainlit-dependent tests)
# Run: pytest tests/test_webapp_smoke.py -v
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_<behavior>():
    with patch("webapp.tools.cl.AskActionMessage") as mock_ask:
        mock_ask.return_value.send = AsyncMock(return_value={"payload": {"proceed": True}})
        # ... exercise dispatch_tool ...
```
**Rules**:
1. `pytest.mark.asyncio` for every async test
2. `unittest.mock.patch` targets the full path in the module under test (e.g. `webapp.tools.cl.AskActionMessage`)
3. `AsyncMock` for async methods; regular `MagicMock` for sync

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `docs/browser-route-audit.md` | **CREATE** | Part A deliverable — field-by-field audit + Keepa coverage percentage + recommendation (answers Q8) |
| `webapp/tools.py` | **UPDATE** | Part B — add `ensure_keepa_data` tool schema, `_step_ensure_keepa_data` wrapper with confirm dialog, dispatcher branch |
| `tests/test_ensure_keepa_data_confirm.py` | **CREATE** | Unit tests for the confirmation flow: (1) no-confirmation-needed path, (2) user confirms, (3) user cancels, (4) timeout/None |
| `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | **UPDATE** | Phase 3.5 table row: status `pending` → `in-progress`, PRP Plan column → link to this file |

## NOT Building

- **Confirmation dialog for `query_deals` / `query_trends` / `query_sellers` auto-fetch path**: These call `_auto_fetch` (api.py:354) which uses `FreshnessStrategy.LAZY` and does **not** emit `phase="needs_confirmation"` (failures are swallowed, see api.py:380-382). The `query_deals` tool schema description (tools.py:211) that says "≥6-token batches return phase='needs_confirmation'" is **stale documentation** — this plan flags it as a follow-up but does NOT fix it (scope discipline per council B−)
- **Wrapping `batch_discover` / `sync_registry` / `discover_asin` (browser)**: Council B− explicitly cancelled / deferred these (PRD rows 3, 4, Decisions Log)
- **Soft budget warnings below 6-token threshold**: Out of scope — `_BATCH_TOKEN_THRESHOLD` stays at 6; tuning is deferred
- **Automated browser route deprecation in code**: Part A produces a **recommendation**, not an automated migration. If the audit says "deprecate", a follow-up plan does the removal
- **Streaming progress indicators during fetch**: `cl.Step` already wraps the call and shows "running"; no additional progress bar
- **Multi-language dialog text**: English + Chinese text in the `content` string is enough; no i18n framework

---

## Step-by-Step Tasks

### Task 1: Part A — Produce browser route audit document

- **ACTION**: Write `docs/browser-route-audit.md` answering Q8.
- **IMPLEMENT**:
  1. Read `src/amz_scout/db.py:664-700` for the authoritative `competitive_snapshots` schema (25 non-audit fields: `id`, `scraped_at`, `site`, `category`, `brand`, `model`, `asin`, `title`, `price_cents`, `currency`, `rating`, `review_count`, `bought_past_month`, `bsr`, `available`, `url`, `stock_status`, `stock_count`, `sold_by`, `other_offers`, `coupon`, `is_prime`, `star_distribution`, `image_count`, `qa_count`, `fulfillment`, plus `*_raw` text snapshots and `project`/`created_at` meta)
  2. For each field, decide one of:
     - ✅ **Covered by Keepa** — cite the Keepa field (e.g. `keepa_products.title`, `keepa_time_series` series type for price/BSR/rating/review_count/monthly_sold, `keepa_buybox_history` for sold_by)
     - ❌ **Browser-unique** — not available via Keepa API (likely: `stock_count`, `stock_status`, `coupon`, `other_offers`, `star_distribution`, `image_count`, `qa_count`, `fulfillment`, `is_prime`)
     - ⚠️ **Partial** — Keepa has a proxy but different semantics
  3. Tabulate with columns: `field | keepa_equivalent | coverage | notes`
  4. Compute coverage %: `covered / (covered + browser_unique + partial*0.5)`
  5. Write **Recommendation** section: if coverage ≥ 80% → propose marking browser route `deprecated-candidate` in Decisions Log; if 50-80% → propose hybrid retention; if < 50% → keep browser route
  6. Close with a **Follow-up** section listing next actions if the recommendation is accepted
- **MIRROR**: Document structure follows existing `docs/KNOWN_ISSUES.md` / `docs/DEVELOPER.md` — Markdown H1 title + H2 section headers, no YAML frontmatter
- **IMPORTS**: N/A (pure docs)
- **GOTCHA**: `keepa_products` and `keepa_time_series` schemas are elsewhere in `db.py`. Confirm Keepa coverage by grep'ing `keepa_` tables in db.py before claiming ✅
- **VALIDATE**: Manual read-through; coverage % is an integer; every `competitive_snapshots` column appears exactly once in the audit table

### Task 2: Part B.1 — Add `ensure_keepa_data` tool schema

- **ACTION**: Append a new tool schema to `TOOL_SCHEMAS` in `webapp/tools.py`, inserted **before** the current tail (`register_asin_from_url`, line 315-357), so the existing `cache_control` marker stays on the tail.
- **IMPLEMENT**:
  ```python
  # Insert as a new dict in the TOOL_SCHEMAS list, BEFORE register_asin_from_url
  {
      "name": "ensure_keepa_data",
      "description": (
          "Refresh/top-up Keepa cache for products × marketplaces. For "
          "'刷新'/'更新数据'/'refresh Keepa'. Consumes Keepa tokens (shared, 60 "
          "max, 1/min refill). Strategies: 'lazy' (fetch only if missing, "
          "default), 'fresh' (force refresh), 'max_age' (refresh if older "
          "than max_age_days), 'offline' (no fetch). Batches ≥6 tokens "
          "surface a confirmation dialog to the user — the UI handles it; "
          "the LLM does NOT need to prompt 'proceed?'."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "marketplace": {
                  "type": "string",
                  "description": f"{_MARKETPLACE_DESC} Omit for all.",
              },
              "product": {
                  "type": "string",
                  "description": "Optional product filter. Omit for all.",
              },
              "strategy": {
                  "type": "string",
                  "description": "Fetch strategy. Default 'lazy'.",
                  "enum": ["lazy", "offline", "max_age", "fresh"],
              },
              "max_age_days": {
                  "type": "integer",
                  "description": "For max_age strategy. Default 7.",
                  "minimum": 1,
              },
              "detailed": {
                  "type": "boolean",
                  "description": (
                      "Fetch deep history (6 tokens/ASIN) vs basic "
                      "(1 token/ASIN). Default false."
                  ),
              },
          },
          "required": [],
      },
  },
  ```
  Verify `register_asin_from_url` is still the LAST schema; if it is, DO NOT touch the `cache_control` marker.
- **MIRROR**: TOOL_SCHEMA_ANTHROPIC_SHAPE (see Patterns section)
- **IMPORTS**: no new imports in this task
- **GOTCHA**: Do NOT expose `confirm` as a tool parameter. The LLM never sets `confirm=True`; that's the UI's job after the dialog. Exposing it would let the LLM bypass the dialog.
- **VALIDATE**: `python -c "from webapp.tools import TOOL_SCHEMAS; s=[t for t in TOOL_SCHEMAS if t.get('name')=='ensure_keepa_data']; assert len(s)==1, 'schema missing'; assert 'confirm' not in s[0]['input_schema']['properties'], 'confirm leaked'; print('ok')"`

### Task 3: Part B.2 — Implement `_step_ensure_keepa_data` with confirmation dialog

- **ACTION**: Add a new `_step_ensure_keepa_data` async function in `webapp/tools.py`; add its dispatch branch in `dispatch_tool`.
- **IMPLEMENT**:
  ```python
  # Add to imports at top of webapp/tools.py:
  from amz_scout.api import ensure_keepa_data as _api_ensure_keepa_data

  # Add after the other _step_* functions, before dispatch_tool:

  @cl.step(type="tool", name="ensure_keepa_data")
  async def _step_ensure_keepa_data(
      marketplace: str | None = None,
      product: str | None = None,
      strategy: str = "lazy",
      max_age_days: int = 7,
      detailed: bool = False,
  ) -> ApiResponse:
      """Run ensure_keepa_data; if batch gate fires, surface a Chainlit confirm dialog.

      The api layer's contract (api.py:920-935) returns
      ``phase="needs_confirmation"`` without fetching when estimated tokens
      ≥ _BATCH_TOKEN_THRESHOLD (6). We consume that envelope: show an
      AskActionMessage, and re-call with confirm=True on OK, or return a
      cancel envelope on abort/timeout.
      """
      logger.info(
          "ensure_keepa_data called: marketplace=%s product=%s strategy=%s "
          "max_age_days=%s detailed=%s",
          marketplace, product, strategy, max_age_days, detailed,
      )
      # First call: confirm=False. API decides whether to fetch or gate.
      first = await asyncio.to_thread(
          _api_ensure_keepa_data,
          marketplace=marketplace,
          product=product,
          strategy=strategy,
          max_age_days=max_age_days,
          detailed=detailed,
          confirm=False,
      )

      # Gate protocol: envelope is ok=True + meta.phase="needs_confirmation".
      # Anything else (ok=False, or ok=True without the phase) passes through.
      meta = first.get("meta") or {}
      if not (first.get("ok") and meta.get("phase") == "needs_confirmation"):
          return first

      estimated_tokens = meta.get("estimated_tokens", "?")
      products_to_fetch = meta.get("products_to_fetch", "?")

      actions = [
          cl.Action(
              name="confirm",
              value="confirm",
              label="✓ Confirm & fetch",
              payload={"proceed": True},
          ),
          cl.Action(
              name="cancel",
              value="cancel",
              label="✗ Cancel",
              payload={"proceed": False},
          ),
      ]
      content = (
          f"⚠️ **Keepa fetch confirmation**\n\n"
          f"- Products to fetch: **{products_to_fetch}**\n"
          f"- Estimated token cost: **{estimated_tokens}** / 60 available\n"
          f"- Strategy: `{strategy}`\n\n"
          f"This will consume Keepa API tokens (shared budget, 1/min refill)."
      )
      response = await cl.AskActionMessage(
          content=content,
          actions=actions,
          timeout=120,
      ).send()

      proceed = False
      if response is not None:
          payload = response.get("payload") if isinstance(response, dict) else None
          if isinstance(payload, dict):
              proceed = bool(payload.get("proceed"))

      if not proceed:
          logger.info("ensure_keepa_data cancelled by user")
          return {
              "ok": True,
              "data": {"cancelled": True},
              "error": None,
              "meta": {
                  "phase": "cancelled_by_user",
                  "message": "User cancelled the Keepa batch fetch.",
              },
          }

      # Second call: confirm=True → fetch executes.
      logger.info("ensure_keepa_data confirmed; proceeding with fetch")
      return await asyncio.to_thread(
          _api_ensure_keepa_data,
          marketplace=marketplace,
          product=product,
          strategy=strategy,
          max_age_days=max_age_days,
          detailed=detailed,
          confirm=True,
      )


  # In dispatch_tool, add after the existing branches (before the unknown-tool fallback):
  if name == "ensure_keepa_data":
      return await _step_ensure_keepa_data(
          marketplace=args.get("marketplace"),
          product=args.get("product"),
          strategy=args.get("strategy", "lazy"),
          max_age_days=args.get("max_age_days", 7),
          detailed=args.get("detailed", False),
      )
  ```
- **MIRROR**: STEP_WRAPPER_PATTERN + DISPATCH_DISPATCHER_PATTERN + ASK_ACTION_MESSAGE_PATTERN + LOGGING_CONVENTION (all in Patterns section)
- **IMPORTS**: Add `from amz_scout.api import ensure_keepa_data as _api_ensure_keepa_data` near the other `_api_` imports (alphabetical order in current file: `ensure_keepa_data` sorts between `check_freshness` and `keepa_budget`)
- **GOTCHA 1**: `api.py:920` returns `needs_confirmation` as a top-level envelope key, but `_envelope(...)` at api.py:924 merges positional kwargs into `meta`. Verify by running `ensure_keepa_data(strategy="fresh")` once in a unit test — whichever location `phase` lands in, align the check. The test in Task 4 verifies either convention.
- **GOTCHA 2**: `cl.AskActionMessage.send()` returns `None` on timeout AND on tab-close. Both map to "cancel". Never raise.
- **GOTCHA 3**: The cancel envelope must use `ok=True` (the operation succeeded in the sense that "user said no"), with `meta.phase="cancelled_by_user"`. An `ok=False` here would trip `summarize_for_llm`'s error path and confuse the LLM into retrying.
- **GOTCHA 4**: Do NOT decorate with `@summarize_for_llm` — `ensure_keepa_data` doesn't return a row list; it returns `{"preview": [...]}` or `{outcomes: [...]}` shape. The summarizer would silently log "expected list data, got dict" and fall through to empty.
- **VALIDATE**: `chainlit run webapp/app.py`; ask "刷新所有产品的 Keepa 数据"; dialog appears; clicking Cancel returns cancelled envelope (LLM says "取消了"); clicking Confirm fetches.

### Task 4: Tests — confirm flow coverage

- **ACTION**: Create `tests/test_ensure_keepa_data_confirm.py` with 4 tests.
- **IMPLEMENT**:
  ```python
  """Unit tests for the ensure_keepa_data confirmation flow in webapp/tools.py.

  Covers the 4 branches of _step_ensure_keepa_data:
    1. No-confirm-needed path: envelope has no phase, pass-through
    2. Confirm path: dialog returns proceed=True → second api call with confirm=True
    3. Cancel path: dialog returns proceed=False → cancel envelope
    4. Timeout path: dialog returns None → cancel envelope (same as #3)
  """
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest


  @pytest.mark.asyncio
  async def test_no_confirmation_needed_passes_through():
      """When api returns ok=True without needs_confirmation phase, return as-is."""
      envelope = {"ok": True, "data": {"outcomes": []}, "error": None, "meta": {}}
      with patch("webapp.tools._api_ensure_keepa_data", return_value=envelope):
          from webapp.tools import _step_ensure_keepa_data
          result = await _step_ensure_keepa_data(strategy="lazy")
      assert result is envelope  # identity: no wrapping

  @pytest.mark.asyncio
  async def test_confirm_path_triggers_second_call_with_confirm_true():
      """When user clicks Confirm, re-call api with confirm=True."""
      gate_envelope = {
          "ok": True,
          "data": {"preview": [{"asin": "B0XYZ", "site": "UK", "model": "Slate7"}]},
          "error": None,
          "meta": {"phase": "needs_confirmation", "estimated_tokens": 8, "products_to_fetch": 8},
      }
      fetch_envelope = {"ok": True, "data": {"outcomes": ["..."]}, "error": None, "meta": {}}

      api_mock = MagicMock(side_effect=[gate_envelope, fetch_envelope])
      ask_mock = MagicMock()
      ask_mock.return_value.send = AsyncMock(return_value={"payload": {"proceed": True}})

      with patch("webapp.tools._api_ensure_keepa_data", api_mock), \
           patch("webapp.tools.cl.AskActionMessage", ask_mock):
          from webapp.tools import _step_ensure_keepa_data
          result = await _step_ensure_keepa_data(strategy="fresh")

      assert result is fetch_envelope
      # 2 calls: first with confirm=False, second with confirm=True
      assert api_mock.call_count == 2
      assert api_mock.call_args_list[0].kwargs["confirm"] is False
      assert api_mock.call_args_list[1].kwargs["confirm"] is True

  @pytest.mark.asyncio
  async def test_cancel_path_returns_cancelled_envelope():
      """When user clicks Cancel, return ok=True with cancelled_by_user phase."""
      gate_envelope = {
          "ok": True,
          "data": {"preview": []},
          "error": None,
          "meta": {"phase": "needs_confirmation", "estimated_tokens": 10, "products_to_fetch": 10},
      }
      api_mock = MagicMock(return_value=gate_envelope)
      ask_mock = MagicMock()
      ask_mock.return_value.send = AsyncMock(return_value={"payload": {"proceed": False}})

      with patch("webapp.tools._api_ensure_keepa_data", api_mock), \
           patch("webapp.tools.cl.AskActionMessage", ask_mock):
          from webapp.tools import _step_ensure_keepa_data
          result = await _step_ensure_keepa_data(strategy="fresh")

      assert result["ok"] is True
      assert result["meta"]["phase"] == "cancelled_by_user"
      assert api_mock.call_count == 1  # never called with confirm=True

  @pytest.mark.asyncio
  async def test_timeout_returns_cancelled_envelope():
      """When dialog times out (send returns None), treat as cancel."""
      gate_envelope = {
          "ok": True,
          "data": {"preview": []},
          "error": None,
          "meta": {"phase": "needs_confirmation", "estimated_tokens": 10, "products_to_fetch": 10},
      }
      api_mock = MagicMock(return_value=gate_envelope)
      ask_mock = MagicMock()
      ask_mock.return_value.send = AsyncMock(return_value=None)

      with patch("webapp.tools._api_ensure_keepa_data", api_mock), \
           patch("webapp.tools.cl.AskActionMessage", ask_mock):
          from webapp.tools import _step_ensure_keepa_data
          result = await _step_ensure_keepa_data(strategy="fresh")

      assert result["ok"] is True
      assert result["meta"]["phase"] == "cancelled_by_user"
      assert api_mock.call_count == 1
  ```
- **MIRROR**: TEST_STRUCTURE pattern (see Patterns)
- **IMPORTS**: `unittest.mock.AsyncMock`, `unittest.mock.MagicMock`, `unittest.mock.patch`, `pytest`
- **GOTCHA**: Patch targets are `webapp.tools._api_ensure_keepa_data` and `webapp.tools.cl.AskActionMessage` (module-level attributes where the code under test looks them up, NOT their origin modules)
- **VALIDATE**: `pytest tests/test_ensure_keepa_data_confirm.py -v` — all 4 pass

### Task 5: Update PRD Phase 3.5 status

- **ACTION**: Edit `.claude/PRPs/prds/internal-amz-scout-web.prd.md` Phase 3.5 row in the Implementation Phases table: status `pending` → `in-progress`, PRP Plan column `-` → link to this plan.
- **IMPLEMENT**: Change
  ```
  | 3.5 | **Browser route audit + token safety gate** | ...(新增 per council B−...) | pending | - | 2 | - |
  ```
  to
  ```
  | 3.5 | **Browser route audit + token safety gate** | ...(新增 per council B−...) | in-progress | - | 2 | [phase3.5-browser-audit-and-token-safety-gate.plan.md](../plans/phase3.5-browser-audit-and-token-safety-gate.plan.md) |
  ```
- **MIRROR**: PRD status-tracking convention (same way Phase 1/2/6 link to their plans in `plans/completed/`)
- **IMPORTS**: N/A
- **GOTCHA**: The Phase 3.5 row has Chinese + English text and an em-dash inside the description — use `Edit` with enough surrounding context (e.g. the whole row), not just the `| pending |` substring which would match multiple rows
- **VALIDATE**: `grep "phase3.5" .claude/PRPs/prds/internal-amz-scout-web.prd.md` returns exactly 1 line; re-render the table mentally — 9 rows total (1, 2, 3, 3.5, 4, 5, 6, 7, 8)

---

## Testing Strategy

### Unit Tests (Task 4)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_no_confirmation_needed_passes_through` | API returns ok=True without `phase` in meta | Returns envelope identity (no wrapping) | No — happy path |
| `test_confirm_path_triggers_second_call_with_confirm_true` | Gate envelope + user confirms | 2 API calls (confirm=False then confirm=True), returns fetch envelope | No — core flow |
| `test_cancel_path_returns_cancelled_envelope` | Gate envelope + user cancels | 1 API call, returns ok=True + phase=cancelled_by_user | **YES** (cancel path) |
| `test_timeout_returns_cancelled_envelope` | Gate envelope + dialog times out (None) | Same as cancel | **YES** (timeout path) |

### Edge Cases Checklist

- [x] No confirmation needed (early return, no dialog)
- [x] User confirms (re-call with confirm=True)
- [x] User cancels (no re-call; cancel envelope)
- [x] User abandons tab / dialog times out (None → cancel)
- [ ] Downstream `ok=False` on first call passes through (covered by identity check in test 1; worth an explicit 5th test if future fields are added)
- [ ] Concurrent same-session dialog (out of scope — Chainlit serializes per-session)

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/tools.py tests/test_ensure_keepa_data_confirm.py
```
EXPECT: Zero warnings (matches existing CI per `bf7600f chore: repo-wide ruff check --fix`)

### Unit Tests — New
```bash
pytest tests/test_ensure_keepa_data_confirm.py -v
```
EXPECT: 4 tests pass

### Unit Tests — Regression
```bash
pytest tests/ -x -q
```
EXPECT: No pre-existing test breaks (tool schema count may appear in a count assertion in `test_webapp_smoke.py` — check and bump if needed)

### Schema Sanity Check
```bash
python -c "
from webapp.tools import TOOL_SCHEMAS
names = [t.get('name') for t in TOOL_SCHEMAS]
assert 'ensure_keepa_data' in names, 'schema missing'
last = TOOL_SCHEMAS[-1]
assert last.get('name') == 'register_asin_from_url', 'cache marker moved'
assert last.get('cache_control') == {'type': 'ephemeral'}, 'cache marker lost'
print('ok: tools=' + str(len(names)))
"
```
EXPECT: `ok: tools=N` (N = previous count + 1)

### Browser Validation (Part B manual smoke)
```bash
chainlit run webapp/app.py -w
```
Then in browser:
1. Log in with whitelisted email
2. Ask: *"刷新所有产品的 Keepa 数据"*
3. EXPECT: `cl.Step` for `ensure_keepa_data` opens; **separately** an AskActionMessage dialog appears with 2 buttons and the token cost line
4. Click **Cancel** → LLM replies with a cancellation acknowledgement; Keepa budget tool shows same token balance as before
5. Re-ask, click **Confirm** → Keepa fetch runs, subsequent `keepa_budget` shows reduced balance

### Part A Validation
```bash
# Audit doc exists and has the required sections
test -f docs/browser-route-audit.md
grep -c "^## " docs/browser-route-audit.md  # >= 3 H2 sections
grep -q "Recommendation" docs/browser-route-audit.md
grep -q "Coverage" docs/browser-route-audit.md
```
EXPECT: file exists, ≥3 H2 sections, both "Recommendation" and "Coverage" appear

---

## Acceptance Criteria

- [ ] `docs/browser-route-audit.md` exists with field-by-field table, coverage %, recommendation, follow-up
- [ ] `webapp/tools.py` contains `ensure_keepa_data` tool schema (inserted before the tail `register_asin_from_url`)
- [ ] `webapp/tools.py` contains `_step_ensure_keepa_data` async function
- [ ] `webapp/tools.py` `dispatch_tool` has the `ensure_keepa_data` branch
- [ ] `tests/test_ensure_keepa_data_confirm.py` exists and 4 tests pass
- [ ] No regressions in `pytest tests/ -x -q`
- [ ] `ruff check` passes
- [ ] Schema sanity script prints `ok: tools=N`
- [ ] Manual smoke shows dialog + cancel + confirm paths work end-to-end
- [ ] PRD Phase 3.5 row updated to `in-progress` with plan link

## Completion Checklist

- [ ] Code follows discovered patterns (STEP_WRAPPER / DISPATCH / LOGGING / TOOL_SCHEMA)
- [ ] `cache_control` still on `register_asin_from_url` (tail) only
- [ ] Error handling matches codebase style (envelope-shaped, never raise)
- [ ] Logging follows `logger.info("<tool> called: %s=%s", ...)` percent-style
- [ ] Tests follow pytest.mark.asyncio + AsyncMock/MagicMock patterns
- [ ] No hardcoded values (6-token threshold stays in `api.py:_BATCH_TOKEN_THRESHOLD`)
- [ ] No scope creep (no `batch_discover` / `query_deals` auto-fetch fix)
- [ ] Self-contained — implementer does not need to grep the codebase

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Chainlit `cl.AskActionMessage` API changed between 2.x releases | Low | Medium | Verify `cl.Action` signature + `.send()` return type against the installed version BEFORE wiring (`uv pip show chainlit` → check docs for that exact version) |
| `meta.get("phase")` lookup misses because phase key is top-level instead of in meta | Low | High (dialog never fires, token burn risk stays) | Read `api.py:920-935` carefully before writing the check; existing `_build_summary` (summaries.py:140) also looks up `meta["phase"]`, confirming the convention |
| Schema count assertion in existing tests breaks | Medium | Low | Run `pytest tests/ -x -q` and bump the count constant if one exists; it's an easy 1-line fix |
| Concurrent tool calls in same turn hit overlapping dialogs | Low | Low | Chainlit serializes `cl.AskActionMessage.send()` per-session; this is Chainlit's responsibility, not ours |
| Part A coverage number becomes load-bearing for "kill browser route" decision | Medium | Low-Medium | Treat the % as advisory; the Decisions Log entry must review the field list, not just the headline % |
| Stale `query_deals` schema description (tools.py:211 claims "≥6 batches return needs_confirmation" but `_auto_fetch` path doesn't emit it) | **Already exists** | Low (misleads LLM/docs only) | Flagged as a follow-up in `docs/browser-route-audit.md`'s "Follow-up" section; explicitly out of scope here |

## Notes

- **Architectural principle honored**: zero changes to `amz_scout.api` — the entire Phase 3.5 Part B is webapp-layer consumption of an existing API protocol. This preserves the PRD's core bet "web layer is a thin adapter" (PRD Proposed Solution section).
- **Why insert before the tail, not append**: the Anthropic prompt cache is keyed on the `cache_control` marker being on the literal last tool schema. Appending at the end requires moving the marker and updating the `register_asin_from_url` entry's cache_control field. Inserting before the tail leaves the marker undisturbed → zero cache invalidation cost.
- **Part A vs Part B sequencing**: Part A is pure docs and can run anytime. Part B can also run first. They're independent; pick whichever fits your energy.
- **Follow-up not in this plan**: If Part A's recommendation is "deprecate browser route", a separate plan (`phase-X-browser-route-deprecation.plan.md`) handles the code removal. Don't fold it in here.
- **Follow-up for `query_deals` doc drift**: The tool schema description at `webapp/tools.py:211` is stale; fix is a one-line docstring edit, but it's explicitly NOT in scope for 3.5 (council B− said "hold scope").
