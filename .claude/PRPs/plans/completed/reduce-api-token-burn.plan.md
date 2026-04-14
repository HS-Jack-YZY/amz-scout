# Plan: Reduce Claude Token Burn in amz_scout.api Tools

## Summary
Cut per-turn Claude token cost in the webapp by (a) trimming unused fields from the `amz_scout.api` envelopes that the LLM consumes and (b) adding a moving `cache_control` breakpoint over conversation history so prior tool results hit the prompt cache. Every change is gated by a pytest-based measurement harness so decisions are driven by real `count_tokens` numbers, not intuition.

## User Story
As **Jack (operator of the amz-scout webapp)**, I want **each LLM turn to consume substantially fewer input/output tokens**, so that **I can answer the same Amazon-data questions at 30-60% of current cost and stay under the Claude budget for longer sessions**.

## Problem → Solution
**Current:** `webapp/llm.py` sends the full conversation history on every turn with only 2 cache breakpoints (system + last tool). Tools like `query_latest` return `SELECT cs.*` — 32 columns of `competitive_snapshots` including `*_raw` fields, `url`, `star_distribution`, `id`, `created_at`, `project`. `query_trends` returns `keepa_ts` + `fetched_at` on every time-series point (90+ rows typical). Token burn compounds across turns.

**Desired:** A measurement harness reports tokens-per-tool-call into `output/token_audit.json`. Each tool envelope is trimmed by an explicit allow-list of LLM-relevant fields. `llm.py` marks the last message of every turn with `cache_control: ephemeral` so turn N re-uses turn N-1's cache. Both tracks report measured before/after deltas in the PR description.

## Metadata
- **Complexity**: Medium
- **Source PRD**: N/A — standalone efficiency task
- **PRD Phase**: N/A (precedes Phase 3 work per session notes)
- **Estimated Files**: ~8 (1 new tests file, 1 new helper, edits to api.py, tools.py, llm.py, pyproject, CLAUDE.md, PR report)

---

## UX Design

### Before
N/A — internal change. The webapp answers the same questions; only cost moves.

### After
N/A. Observable side effect: `resp.usage` in webapp logs shows `cache_read_input_tokens` climbing and `input_tokens` dropping after turn 2.

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| webapp log line per turn | `Chat turn complete (iterations=N)` | Same line + `usage={input, cache_read, cache_creation, output}` | Emit so we can see caching in production |
| `output/token_audit.json` | does not exist | Per-tool row: `{tool, args, tokens_before, tokens_after, pct_saved}` | Written by the new harness, not by the webapp |

---

## Mandatory Reading

Files that MUST be read before implementing:

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/llm.py` | 1-85 | Current cache_control layout and tool-use loop; all caching changes happen here |
| P0 | `webapp/tools.py` | 42-277 | Where `cache_control` on the LAST tool lives; trimming touches the dispatcher return path |
| P0 | `src/amz_scout/api.py` | 286-302 | `_envelope()` helper — single choke point for every tool return |
| P0 | `src/amz_scout/api.py` | 444-703 | All 9 query functions; signatures + `_envelope(**fetch_meta, **meta_extra)` pattern |
| P0 | `src/amz_scout/db.py` | 287-370 | Table schemas. Proves `competitive_snapshots` has 32 cols and identifies which are LLM-junk (`*_raw`, `url`, `star_distribution`, `id`, `created_at`, `project`) |
| P0 | `src/amz_scout/db.py` | 911-1106 | All `query_*` functions that `api.py` calls. Identifies which use `SELECT cs.*` (the bloat source) vs. explicit columns |
| P1 | `webapp/app.py` | 27-55 | How `history` is persisted on the Chainlit session — important for understanding the moving-breakpoint lifetime |
| P1 | `tests/test_api.py` | 1-130 | Fixture pattern for building a synthetic SQLite DB to drive the measurement harness without touching production data |
| P1 | `tests/test_webapp_smoke.py` | 1-50 | `_set_fake_env` + `_reset_webapp_modules` — the harness must follow this to import `webapp.*` cleanly |
| P2 | `pyproject.toml` | (whole) | Confirms `anthropic>=0.40` is already an optional `web` dep; no new runtime deps needed |
| P2 | `CLAUDE.md` | "How to Answer" section | So the trimmed envelope keys still satisfy the decision-tree contract documented for operators |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Prompt caching breakpoints | Anthropic API docs, "Prompt caching" | Up to **4 `cache_control` breakpoints per request**. Each breakpoint caches everything above it. To cache history, attach `cache_control` to a block inside the **last message** of `messages=`. Cache TTL is 5 minutes (ephemeral). |
| `count_tokens` endpoint | `anthropic.Anthropic().messages.count_tokens(...)` | Returns `{input_tokens: int}`. Accepts the same `model / system / tools / messages` kwargs as `create`. Does **not** bill — safe to call from tests. Requires `ANTHROPIC_API_KEY` (hits `/v1/messages/count_tokens`). Mark the harness test `@pytest.mark.network` and skip in CI unless the env var is set. |
| Cache-read vs. cache-creation pricing | Anthropic API docs, "Usage and billing" | `usage.cache_read_input_tokens` is ~10% of normal rate; `cache_creation_input_tokens` is ~125%. **Moving breakpoint pays the creation premium once per 5 min**, then every subsequent turn in the window is 10%. Net: positive EV after the 2nd turn. |

```
KEY_INSIGHT: The Anthropic SDK counts tool definitions as part of the tools block —
caching them via `cache_control` on the last tool (already done in webapp/tools.py:275)
is correct and should NOT be moved. The gap is on the `messages=` axis, not the `tools=` axis.
APPLIES_TO: Task "Audit and fix cache_control breakpoints"
GOTCHA: Do not attach cache_control to assistant messages — it's allowed but cache hits
require the prefix match to be byte-identical. Attach it to the user message that carries
the tool_result blocks; that's the stable growing prefix.
```

```
KEY_INSIGHT: `count_tokens` is free and synchronous. Use it in a pytest-only harness — do
NOT wire it into the hot path of `run_chat_turn`. Production cost observation comes from
the `resp.usage` dict returned by `messages.create`, which is already populated.
APPLIES_TO: Task "Design measurement harness"
GOTCHA: `count_tokens` still requires a valid `ANTHROPIC_API_KEY` at HTTP layer. Skip the
test cleanly when the env var is missing.
```

---

## Patterns to Mirror

Code patterns discovered in the codebase. Follow these exactly.

### ENVELOPE_HELPER
```python
# SOURCE: src/amz_scout/api.py:286-301
def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    hint_if_empty: str | None = None,
    **meta: Any,
) -> ApiResponse:
    """Build the standard response envelope."""
    if hint_if_empty and not data:
        meta["hint"] = hint_if_empty
    return {
        "ok": ok,
        "data": data if data is not None else [],
        "error": error,
        "meta": meta,
    }
```
**How to mirror:** All trimming happens *upstream* of `_envelope`. Never mutate `_envelope`'s output shape — the webapp and the CLI both depend on `{ok, data, error, meta}`.

### ENVELOPE_CALL_SITE (query_latest)
```python
# SOURCE: src/amz_scout/api.py:444-459
def query_latest(
    project: str | None = None,
    marketplace: str | None = None,
    category: str | None = None,
) -> dict:
    """Latest competitive snapshot per product/site."""
    try:
        info = _resolve_context(project, marketplace=marketplace, category=category)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        with open_db(info.db_path) as conn:
            rows = _db_query_latest(conn, site=site, category=category)
    except Exception as e:
        logger.exception("query_latest failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))
```
**How to mirror:** Introduce a single `trim_competitive_rows(rows)` call between the DB read and `_envelope(...)`. Place trimming next to the call site, not inside the DB layer — the DB functions keep returning full rows for CLI/admin use.

### _add_dates IMMUTABLE TRANSFORM
```python
# SOURCE: src/amz_scout/api.py:360-367
def _add_dates(rows: list[dict]) -> list[dict]:
    """Return new list with human-readable date field from keepa_ts."""
    return [
        {**r, "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime("%Y-%m-%d %H:%M")}
        if "keepa_ts" in r
        else r
        for r in rows
    ]
```
**How to mirror:** Every new trim helper must be a pure list-comprehension that returns a new list. No mutation. Follow the `_add_dates` pattern exactly: `[{...r, ...changes} for r in rows]`. Keeps the immutability contract the project already enforces.

### CACHE_CONTROL ON SYSTEM BLOCK
```python
# SOURCE: webapp/llm.py:18-24
SYSTEM_BLOCKS: list[dict[str, Any]] = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    },
]
```
**How to mirror:** Same `{"type": "ephemeral"}` shape. Do NOT use `{"type": "ephemeral", "ttl": "1h"}` — the 1h TTL is beta-gated and not needed for a single session.

### CACHE_CONTROL ON LAST TOOL
```python
# SOURCE: webapp/tools.py:272-276
        "required": ["product"],
    },
    # Cache_control on the LAST tool only — caches all 9 tool definitions together.
    "cache_control": {"type": "ephemeral"},
},
```
**How to mirror:** Keep this as-is. Do not add a second `cache_control` inside `TOOL_SCHEMAS`. The comment on webapp/tools.py:44 ("scattered cache_control = cache hit rate of 0") documents a real prior finding.

### TOOL_USE LOOP STRUCTURE
```python
# SOURCE: webapp/llm.py:38-78
for i in range(max_iterations):
    resp = _client.messages.create(
        model=MODEL_ID,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_BLOCKS,
        tools=TOOL_SCHEMAS,
        messages=history,
    )
    history.append(
        {
            "role": "assistant",
            "content": [block.model_dump() for block in resp.content],
        }
    )
    if resp.stop_reason != "tool_use":
        ...
    tool_results: list[dict] = [...]
    history.append({"role": "user", "content": tool_results})
```
**How to mirror:** The moving cache_control goes on `tool_results[-1]` *after* the list is built and *before* `history.append`. Pattern:
```python
if tool_results:
    tool_results[-1]["cache_control"] = {"type": "ephemeral"}
history.append({"role": "user", "content": tool_results})
```
This caches every message up to (and including) the most recent tool round-trip. The cache gets re-anchored each turn — Anthropic recognises the shared prefix and still bills cache-read rates.

### TEST FIXTURE PATTERN
```python
# SOURCE: tests/test_webapp_smoke.py:14-26
def _reset_webapp_modules() -> None:
    for mod in list(sys.modules):
        if mod.startswith("webapp"):
            del sys.modules[mod]

def _set_fake_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "fake")
    monkeypatch.setenv("APP_PASSWORD", "fake")
    monkeypatch.setenv("KEEPA_API_KEY", "fake")
```
**How to mirror:** For the token-audit test, load real production `output/amz_scout.db` via an optional fixture (skip if missing). Do not import `webapp.llm` at module level — import it inside the test body after `_set_fake_env`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/amz_scout/api.py` | UPDATE | Add trim helpers and route each query return through them; keep `_envelope` untouched |
| `src/amz_scout/_llm_trim.py` | CREATE | New private module holding `LLM_SAFE_COMPETITIVE_FIELDS`, `trim_competitive_rows`, `trim_timeseries_rows`, `trim_seller_rows`, `trim_deals_rows`. Keeps `api.py` from growing past its current 1636 lines |
| `webapp/llm.py` | UPDATE | Attach `cache_control: ephemeral` to the last tool_result block of each turn; log `resp.usage` at INFO |
| `tests/test_llm_trim.py` | CREATE | Unit tests for each trim helper (allow-list enforcement, immutability, empty-input safety) |
| `tests/test_token_audit.py` | CREATE | Measurement harness: build synthetic tool_result messages, call `client.messages.count_tokens`, diff before/after, write `output/token_audit.json`. Marked `@pytest.mark.network` (skipped when `ANTHROPIC_API_KEY` missing) |
| `tests/test_webapp_smoke.py` | UPDATE | Add one test asserting `run_chat_turn` attaches `cache_control` to the last user message after a tool round-trip (monkeypatch `_client.messages.create` to return canned `tool_use` → canned text, assert on `history`) |
| `CLAUDE.md` | UPDATE | Add a short "Trimmed envelope notice" paragraph pointing to `_llm_trim.py` and documenting what the LLM sees vs. what the CLI sees |
| `.claude/PRPs/reports/token-burn-reduction-report.md` | CREATE | Measurement report attached to the PR: before/after numbers from `output/token_audit.json`, sample usage logs from a live turn |

## NOT Building

- **No new runtime dependencies.** `anthropic` is already an optional `web` extra; no pyproject changes.
- **No changes to the DB layer (`db.py`).** Trimming happens in the API layer so CLI/admin commands still get full rows for debugging.
- **No config-driven trimming.** Field allow-lists live in code as frozen sets — simpler, easier to diff-review, no runtime toggle needed.
- **No streaming migration.** Tempting, but out of scope; streaming affects latency/UX, not token cost.
- **No model downgrade.** Switching from `claude-sonnet-4-6` to Haiku 4.5 is a separate cost lever with quality tradeoffs — not part of this plan.
- **No prompt-compression / summarization of history.** Prompt caching delivers most of the win at zero complexity cost; revisit compression only if `output/token_audit.json` shows cache hit rate < 80% after Phase B.
- **No changes to CLI output.** The CLI reads the full envelopes; trimming is webapp-only via `tools.py`.

---

## Step-by-Step Tasks

### Task 1: Create `src/amz_scout/_llm_trim.py` with allow-list helpers
- **ACTION**: Create a new module exposing pure functions that produce trimmed copies of the row dicts returned by `db.py` queries.
- **IMPLEMENT**:
  - `LLM_SAFE_COMPETITIVE_FIELDS: frozenset[str]` = `{"site", "category", "brand", "model", "asin", "price_cents", "currency", "rating", "review_count", "bought_past_month", "bsr", "available", "scraped_at"}` (13 of 32 columns — drops `id`, `title`, `url`, `stock_status`, `stock_count`, `sold_by`, `other_offers`, `coupon`, `is_prime`, `star_distribution`, `image_count`, `qa_count`, `fulfillment`, `price_raw`, `rating_raw`, `review_count_raw`, `bsr_raw`, `project`, `created_at`).
  - `LLM_SAFE_TIMESERIES_FIELDS: frozenset[str]` = `{"date", "value"}` (drops `keepa_ts` after `_add_dates` has added `date`; drops `fetched_at`).
  - `LLM_SAFE_SELLER_FIELDS: frozenset[str]` = `{"date", "seller_id"}`.
  - `LLM_SAFE_DEAL_FIELDS: frozenset[str]` = `{"asin", "site", "deal_type", "badge", "percent_claimed", "deal_status", "start_time", "end_time"}`.
  - `def trim(rows: list[dict], allow: frozenset[str]) -> list[dict]: return [{k: v for k, v in r.items() if k in allow} for r in rows]`.
  - Thin aliases: `trim_competitive_rows = functools.partial(trim, allow=LLM_SAFE_COMPETITIVE_FIELDS)` etc.
- **MIRROR**: `_add_dates` (list-comprehension, new list, zero mutation).
- **IMPORTS**: `import functools` only.
- **GOTCHA**: `query_availability` already hand-selects 7 columns in SQL (see `db.py:1042-1044`). It still needs to pass through `trim_competitive_rows` so *any* future schema addition that accidentally leaks a column is filtered out. Defense in depth.
- **VALIDATE**: `pytest tests/test_llm_trim.py -v` — tests assert (a) trimmed dict has only allow-listed keys, (b) original row dict is unchanged, (c) empty list returns empty list, (d) rows missing allow-listed keys don't raise.

### Task 2: Wire trim helpers into `api.py` query returns
- **ACTION**: Route every LLM-facing query return through the right `trim_*` helper before passing to `_envelope`.
- **IMPLEMENT**:
  - At top of `api.py`, add `from amz_scout._llm_trim import (trim_competitive_rows, trim_timeseries_rows, trim_seller_rows, trim_deals_rows)`.
  - `query_latest` (line 459): `rows = trim_competitive_rows(rows)` before the `_envelope` return.
  - `query_compare` (line 581): same.
  - `query_ranking` (line 599): same.
  - `query_availability` (line 612): same.
  - `query_trends` (line 547, after `_add_dates`): `rows = trim_timeseries_rows(rows)`.
  - `query_sellers` (line 652, after `_add_dates`): `rows = trim_seller_rows(rows)`.
  - `query_deals` (around line 700, after `query_deals_history` returns): `rows = trim_deals_rows(rows)`.
  - `check_freshness`, `keepa_budget`: do nothing — they already return small dicts.
- **MIRROR**: `ENVELOPE_CALL_SITE` pattern above (trim between DB read and `_envelope`).
- **IMPORTS**: the 4 trim aliases from `_llm_trim`.
- **GOTCHA**: `_auto_fetch` populates `fetch_meta` with `{"auto_fetched": bool, "tokens_used": int, "tokens_remaining": int, ...}`. This is small and LLM-useful (lets the model tell the user how many Keepa tokens were spent). **Do not trim `meta`** — only `data` rows. Keep `fetch_meta` in the envelope untouched.
- **VALIDATE**: `pytest tests/test_api.py -v` — existing tests should still pass. If a test asserts on a column name we just trimmed (e.g., `url`), either relax the assertion (if it's LLM-envelope-scoped) or mark the test xfail with an explanation. Do not silently lower coverage.

### Task 3: Build the measurement harness (`tests/test_token_audit.py`)
- **ACTION**: pytest module that measures tokens-per-tool-result before and after trimming, writes `output/token_audit.json`, and fails if any tool got *larger*.
- **IMPLEMENT**:
  ```python
  import json, os, pytest
  from pathlib import Path
  from anthropic import Anthropic
  from amz_scout import api as amz_api
  from amz_scout._llm_trim import (
      trim_competitive_rows, trim_timeseries_rows, trim_seller_rows, trim_deals_rows,
  )

  pytestmark = pytest.mark.network  # skipped unless ANTHROPIC_API_KEY is a real key

  @pytest.fixture(scope="module")
  def client():
      if not os.environ.get("ANTHROPIC_API_KEY"):
          pytest.skip("Set ANTHROPIC_API_KEY to run token audit")
      return Anthropic()

  @pytest.fixture(scope="module")
  def real_db():
      db = Path("output/amz_scout.db")
      if not db.exists():
          pytest.skip(f"Production DB not found at {db}")
      return db

  def _count(client, payload: dict) -> int:
      msg = [{
          "role": "user",
          "content": [{
              "type": "tool_result",
              "tool_use_id": "toolu_fake",
              "content": json.dumps(payload, ensure_ascii=False, default=str),
          }],
      }]
      return client.messages.count_tokens(
          model="claude-sonnet-4-6", system="You are a test.", messages=msg
      ).input_tokens

  def test_query_latest_token_delta(client, real_db, monkeypatch):
      monkeypatch.chdir(real_db.parent.parent)
      after_env = amz_api.query_latest(marketplace="UK")           # trimmed
      # Reconstruct untrimmed by re-running the db layer directly:
      from amz_scout.db import open_db, query_latest as db_query_latest
      with open_db(real_db) as conn:
          raw = db_query_latest(conn, site="UK", category=None)
      before_env = {"ok": True, "data": raw, "error": None, "meta": {}}
      before = _count(client, before_env)
      after = _count(client, after_env)
      audit = {"tool": "query_latest", "before": before, "after": after,
               "pct_saved": round((before - after) / before * 100, 1)}
      out = Path("output/token_audit.json")
      existing = json.loads(out.read_text()) if out.exists() else []
      existing = [r for r in existing if r["tool"] != "query_latest"] + [audit]
      out.write_text(json.dumps(existing, indent=2))
      assert after <= before, "Trim must never increase tokens"
      assert audit["pct_saved"] > 0, "Expected measurable savings"
  ```
  Repeat the same test shape for each of the 7 trimmed queries (`query_trends`, `query_compare`, `query_ranking`, `query_availability`, `query_sellers`, `query_deals`). Aggregate into a single JSON with one row per tool.
- **MIRROR**: `tests/test_api.py` fixture style (synthetic DB build) — for `query_trends` use an inline synthetic DB so the test runs without a real prod DB. Leave real-DB tests guarded by `real_db` fixture.
- **IMPORTS**: `anthropic.Anthropic`, `amz_scout.api`, `amz_scout._llm_trim`, `pathlib.Path`, `json`, `os`, `pytest`.
- **GOTCHA**: `count_tokens` is a network call. Keep the fixture `scope="module"` so we only authenticate once. Add `pytest.mark.network` and register the marker in `pyproject.toml`'s `[tool.pytest.ini_options] markers = ["network: requires ANTHROPIC_API_KEY"]` if not already registered.
- **GOTCHA 2**: To produce an untrimmed envelope for the A/B compare without resurrecting old code, call the `db.py` query directly and wrap it manually in the envelope shape. Do not add a `trim=False` kwarg to the public API — the plan said "no config-driven trimming."
- **VALIDATE**: `ANTHROPIC_API_KEY=... pytest tests/test_token_audit.py -v` — should produce `output/token_audit.json`, all assertions pass, `pct_saved` > 0 for every tool.

### Task 4: Add moving `cache_control` on the last tool_result in `webapp/llm.py`
- **ACTION**: Mark the last `tool_result` block of each turn as ephemeral-cached so subsequent turns read it from cache.
- **IMPLEMENT**: In the tool_use branch of `run_chat_turn` (webapp/llm.py:62-78), after the `tool_results` list is built and before `history.append({"role": "user", "content": tool_results})`:
  ```python
  if tool_results:
      tool_results[-1]["cache_control"] = {"type": "ephemeral"}
  history.append({"role": "user", "content": tool_results})
  ```
  Also: add `logger.info("usage: %s", resp.usage.model_dump())` right after `resp = _client.messages.create(...)` so production logs surface cache hit rates.
- **MIRROR**: `TOOL_USE LOOP STRUCTURE` pattern above.
- **IMPORTS**: none new.
- **GOTCHA**: The `cache_control` field on a content block coexists with the block's `type`/`tool_use_id`/`content` keys — Anthropic's Python SDK accepts it as a `TypedDict` extra. Do **not** wrap the whole user message with cache_control (that's only legal on `system` and `tools`). The breakpoint must be on a **content block inside the message**, not on the message itself.
- **GOTCHA 2**: The cache prefix is a *byte-exact match*. Keep `json.dumps(result, ensure_ascii=False, default=str)` deterministic — do not introduce sets, random iteration, or `dict.items()` without a sort for any trimmed structure whose JSON might vary between identical runs. The current `ApiResponse` is a plain dict with deterministic Python 3.7+ ordering, so this holds as long as we preserve insertion order in `_llm_trim`.
- **VALIDATE**:
  1. `pytest tests/test_webapp_smoke.py -v` — new test below passes.
  2. Manual: run the webapp, make 3 turns that all hit `query_latest`, tail the log. Turn 1 should show `cache_creation_input_tokens > 0`; turn 2+ should show `cache_read_input_tokens > 0`.

### Task 5: Add a smoke test for the cache_control wiring
- **ACTION**: Unit-test that `run_chat_turn` attaches `cache_control` to the last tool_result after a tool_use round-trip.
- **IMPLEMENT**: New test class in `tests/test_webapp_smoke.py`:
  ```python
  class TestCacheControlWiring:
      def test_last_tool_result_is_cached(self, monkeypatch):
          _set_fake_env(monkeypatch)
          _reset_webapp_modules()
          from webapp import llm as webapp_llm

          class _Block:
              def __init__(self, **kw): self.__dict__.update(kw)
              def model_dump(self): return dict(self.__dict__)
          class _Usage:
              def model_dump(self): return {}
          class _Resp:
              def __init__(self, content, stop_reason):
                  self.content, self.stop_reason = content, stop_reason
                  self.usage = _Usage()

          tool_use_block = _Block(type="tool_use", id="toolu_X", name="keepa_budget", input={})
          text_block = _Block(type="text", text="done")
          responses = iter([
              _Resp([tool_use_block], "tool_use"),
              _Resp([text_block], "end_turn"),
          ])
          monkeypatch.setattr(webapp_llm._client.messages, "create",
                              lambda **kw: next(responses))

          async def fake_dispatch(name, args):
              return {"ok": True, "data": [], "error": None, "meta": {}}
          monkeypatch.setattr(webapp_llm, "dispatch_tool", fake_dispatch)

          import asyncio
          history = [{"role": "user", "content": "hi"}]
          _, updated = asyncio.run(webapp_llm.run_chat_turn(history))

          tr_msg = next(
              m for m in updated
              if m["role"] == "user" and isinstance(m["content"], list)
              and any(b.get("type") == "tool_result" for b in m["content"])
          )
          last_block = tr_msg["content"][-1]
          assert last_block.get("cache_control") == {"type": "ephemeral"}
  ```
- **MIRROR**: `_reset_webapp_modules` + `_set_fake_env` pattern from existing smoke tests.
- **IMPORTS**: `asyncio`, existing helpers.
- **GOTCHA**: `_client` is instantiated at module import time in `webapp/llm.py:14`. Monkeypatching must happen *after* `_reset_webapp_modules()` + the import inside the test body, not at module scope.
- **VALIDATE**: `pytest tests/test_webapp_smoke.py::TestCacheControlWiring -v`.

### Task 6: Update `CLAUDE.md` with the trim allow-list
- **ACTION**: Add a short paragraph under "How to Answer User Questions" noting that webapp envelopes are trimmed and pointing to `_llm_trim.py`.
- **IMPLEMENT**: Append a new numbered bullet under the key-behaviors section:
  > 15. **Webapp envelopes are trimmed.** `webapp/tools.py` routes every query through `amz_scout._llm_trim` so the LLM only sees fields an analyst would cite: core product identity (brand/model/asin), price/rating/BSR, availability, and timestamp. CLI callers (`amz-scout query`, `amz-scout scrape`) still see full envelopes. If you need a field that's currently hidden from the LLM, add it to the relevant frozen set in `_llm_trim.py` and re-run `pytest tests/test_token_audit.py` to confirm the cost delta is acceptable.
- **MIRROR**: existing bullet-style numbered guidance in CLAUDE.md.
- **VALIDATE**: `git diff CLAUDE.md` — bullet reads naturally, references the right file.

### Task 7: Produce the before/after measurement report
- **ACTION**: After Tasks 1-6 land, run the harness twice (with and without trimming) and write a short markdown report.
- **IMPLEMENT**: `.claude/PRPs/reports/token-burn-reduction-report.md` with sections:
  1. **Method** — 1 paragraph describing the harness.
  2. **Per-tool deltas table** — tool | before | after | pct_saved, driven directly from `output/token_audit.json`.
  3. **End-to-end turn example** — paste 3 turns of real `resp.usage.model_dump()` log output showing `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`.
  4. **Estimated monthly savings** — `(tokens_saved_per_turn * turns_per_day * 30) * price_per_mtok`.
- **MIRROR**: `.claude/PRPs/reports/phase6-deployment-report.md` structure.
- **VALIDATE**: Manual — report has real numbers, not placeholders. Link it from the PR description.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_trim_competitive_rows_drops_raw_fields` | row dict with all 32 cs columns | dict with only 13 allow-listed keys | — |
| `test_trim_competitive_rows_immutable` | row dict | original unchanged after trim | ✅ mutation guard |
| `test_trim_competitive_rows_empty` | `[]` | `[]` | ✅ empty-input safety |
| `test_trim_competitive_rows_missing_keys` | row dict missing `rating` | no KeyError, rating simply absent in output | ✅ schema drift |
| `test_trim_timeseries_rows_drops_keepa_ts` | row with `keepa_ts`, `value`, `fetched_at`, `date` | `{"date": ..., "value": ...}` only | — |
| `test_query_latest_wraps_trim` | synthetic DB with one product | envelope `data[0]` lacks `url`, `star_distribution`, `created_at` | — |
| `test_query_trends_wraps_trim` | synthetic DB with 5 time-series points | each row has only `date` + `value` | — |
| `test_run_chat_turn_caches_last_tool_result` | monkeypatched client returning tool_use → text | user message with tool_results has `cache_control` on last block | ✅ core fix |

### Edge Cases Checklist
- [ ] Empty `rows` list (no products in registry for that marketplace)
- [ ] Row where the allow-listed key is `None` (e.g., `bsr = NULL`) — should pass through as `None`, not be dropped
- [ ] Row where `keepa_ts` is missing (pass-through rows from non-timeseries code paths)
- [ ] Unicode in `brand`/`model` (Chinese characters, `ensure_ascii=False` must hold)
- [ ] Very large result set (e.g. `query_trends` with `days=730`, ~730 points) — trim must still be O(n) and produce a JSON blob small enough that `max_tokens=4096` output limit isn't near
- [ ] Tool loop with zero tool calls — `tool_results` list empty, `cache_control` branch must not crash
- [ ] Tool loop at `max_iterations` — final message is a warning string, not a tool_result; `cache_control` must only attach when `tool_results` is populated

---

## Validation Commands

### Static Analysis
```bash
ruff check src/ tests/ webapp/
ruff format --check src/ tests/ webapp/
```
EXPECT: Zero errors, zero formatting diffs.

### Unit Tests (fast path, no network)
```bash
pytest tests/test_llm_trim.py tests/test_api.py tests/test_webapp_smoke.py -v
```
EXPECT: All pass. Existing test_api.py tests continue to pass (or are explicitly relaxed with a justification comment).

### Full Test Suite
```bash
pytest
```
EXPECT: No regressions. `test_token_audit.py` is skipped without `ANTHROPIC_API_KEY` — that's expected.

### Token Audit (network, manual)
```bash
ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2) pytest tests/test_token_audit.py -v
cat output/token_audit.json
```
EXPECT: JSON file exists; every tool row has `pct_saved > 0` and `after <= before`. Target: ≥40% saved on `query_latest`/`query_ranking`/`query_compare` (the `SELECT cs.*` tools); ≥25% on `query_trends`/`query_sellers` (time-series tools).

### Manual End-to-End Validation
- [ ] Start webapp locally: `chainlit run webapp/app.py -w`
- [ ] Send 3 consecutive questions that all call `query_latest` on UK.
- [ ] Tail `webapp.log` (or stdout). Confirm usage line appears per turn:
  - Turn 1: `cache_creation_input_tokens > 0`, `cache_read_input_tokens == 0`
  - Turn 2: `cache_read_input_tokens > 0` (history + system + tools all cached)
  - Turn 3: `cache_read_input_tokens` grows (covers turn 2's tool_result too)
- [ ] Answers still contain price/brand/rating/BSR — no visible degradation.
- [ ] Paste the 3 `usage` lines into the report file.

---

## Acceptance Criteria
- [ ] `src/amz_scout/_llm_trim.py` exists with 4 allow-lists + trim functions
- [ ] All 7 LLM-facing `query_*` functions route through the right trim helper
- [ ] `webapp/llm.py` attaches `cache_control: ephemeral` to the last tool_result each turn
- [ ] `webapp/llm.py` logs `resp.usage` per turn at INFO
- [ ] `tests/test_llm_trim.py` passes (8 cases minimum)
- [ ] `tests/test_webapp_smoke.py::TestCacheControlWiring` passes
- [ ] `tests/test_token_audit.py` runs green with `ANTHROPIC_API_KEY` set and produces `output/token_audit.json`
- [ ] `output/token_audit.json` shows every tool with `pct_saved > 0` and `after <= before`
- [ ] `.claude/PRPs/reports/token-burn-reduction-report.md` contains real numbers
- [ ] `ruff check` + `ruff format --check` clean
- [ ] Full `pytest` suite green (audit test may skip)
- [ ] Manual 3-turn webapp run shows cache reads on turn 2+

## Completion Checklist
- [ ] Code follows `_add_dates` immutable-transform pattern (no mutation, new lists)
- [ ] Error handling unchanged — trimming never raises; missing keys are silently absent
- [ ] Logging uses the existing `logger = logging.getLogger(__name__)` pattern, level INFO for usage lines
- [ ] Tests follow `_reset_webapp_modules` + `_set_fake_env` pattern for any test that imports `webapp.*`
- [ ] No hardcoded ANTHROPIC_API_KEY in tests — always read from env, always skip cleanly when missing
- [ ] `CLAUDE.md` updated so future sessions know webapp envelopes are trimmed and why
- [ ] PR description references `output/token_audit.json` and `.claude/PRPs/reports/token-burn-reduction-report.md`
- [ ] No unnecessary scope additions — the "NOT Building" list is respected
- [ ] Self-contained — implementer does not need to search the codebase; every file touched is listed with line-level guidance

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Trimmed fields turn out to be LLM-relevant (e.g., `title` or `sold_by`) and quality drops | Medium | Medium | Start conservative: include `title` in the competitive allow-list for the first iteration; measure; drop it if the report shows it's the biggest bloat and quality holds. Easy revert: one-line set edit. |
| `cache_control` on content blocks interacts badly with an SDK version < 0.40 | Low | High | `pyproject.toml` already pins `anthropic>=0.40`; installed version observed at `0.94.0`. Add a `_check_sdk_version()` gate if needed. |
| Cache prefix mismatches between turns due to non-deterministic JSON (dict ordering across Python versions) | Low | Medium | Pin to Python 3.12+ (already required by pyproject). Use plain `json.dumps(..., ensure_ascii=False, default=str)` with insertion order. Do not introduce sets in trimmed outputs. |
| Token audit depends on real production DB and is non-repeatable in CI | Low | Low | Harness has two modes: real-DB mode (skipped without the file) and synthetic-DB mode (always runs locally, still produces a report with real `count_tokens` calls). The per-tool pct_saved is the same either way — only absolute token counts differ. |
| `count_tokens` API quota or rate limiting | Low | Low | Module-scoped client fixture; each harness run makes ≤ 20 `count_tokens` calls. Well under any reasonable limit. |
| Chainlit session state survives across deploys and old history lacks `cache_control` → first post-deploy turn is uncached | Low | Low | Accept it. Cache TTL is 5 minutes anyway; worst case is one extra uncached turn per deploy. |

## Notes
- The two tracks (envelope trimming vs. cache_control) are independently valuable and can be shipped separately if the plan is split across two PRs. Recommended order: **trimming first** (lower risk, immediate savings on output tokens + smaller input tokens on every new tool call), **then caching** (larger savings on input tokens for multi-turn sessions, but depends on Anthropic caching behavior being measurable in production).
- The `_llm_trim.py` module is deliberately named with a leading underscore. It is an internal helper, not part of the public `amz_scout.api` contract. CLI code must not import from it.
- Feedback memory `feedback_dockerfile_upstream_introspection.md` established a "5-second dry-run before writing deps-heavy code" rule. Equivalent here: **run `count_tokens` on one real tool result before finalizing any allow-list**, not after. That's why Task 3's harness is specified before the trim allow-lists are hardened.
- Phase 3 of the webapp PRD (live Keepa fetch confirmations, etc.) builds on top of this. Finishing this plan first is the correct ordering — it makes every Phase-3 tool call cheaper too.
