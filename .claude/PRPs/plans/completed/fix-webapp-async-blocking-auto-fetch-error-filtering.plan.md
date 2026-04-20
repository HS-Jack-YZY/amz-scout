# Plan: Fix webapp async blocking + auto_fetch_error filtering (closes #13)

## Summary
Fix two HIGH-severity webapp reliability bugs surfaced in PR #10 review:
(A) every `async def _step_*` wrapper in `webapp/tools.py` runs synchronous
SQLite/Keepa HTTP on the Chainlit event loop — one slow user freezes the
whole app; (B) when Keepa auto-fetch fails, `_auto_fetch` correctly flags
`auto_fetch_error` in envelope meta, but `webapp/summaries._build_summary`
drops it, so the LLM confidently summarizes stale data with no warning.

## User Story
As a Chainlit webapp user sharing the deployment with teammates,
I want my query to stay responsive when another user triggers a slow Keepa
fetch, and I want to see an explicit warning when auto-fetch silently fell
back to stale cache,
So that concurrent usage stays fluid and the LLM never gives me confident
but outdated product data.

## Problem → Solution
- **Before**: Sync `_api_*` calls block the event loop (5–30s per Keepa
  HTTP call stalls every concurrent session); `auto_fetch_error` is filtered
  out of the LLM-visible summary, so failures are invisible to the user.
- **After**: Every blocking `_api_*` call dispatches via `asyncio.to_thread`;
  the 3 API call sites that invoke `_auto_fetch` (`query_trends`,
  `query_sellers`, `query_deals`) convert its failure flag into a
  human-readable entry in the envelope's existing `warnings` channel,
  which already rides the production `_truncate_warnings` path to the
  LLM-facing summary.

## Metadata
- **Complexity**: Medium
- **Source PRD**: N/A — originated from GitHub issue #13
- **PRD Phase**: N/A
- **Estimated Files**: 4 code/test (2 src + 2 test)

---

## UX Design

### Before
```
User A: "趋势?" → query_trends → Keepa HTTP (12s block)
User B: (waits… entire app frozen)

Keepa returns 429 → _auto_fetch logs, sets
  meta.auto_fetch_error = True.
_build_summary drops the key. LLM sees ok=True,
  summarizes 3-day-old cache as "latest price".
```

### After
```
User A: "趋势?" → query_trends offloaded to thread pool
User B: query_latest proceeds in parallel (no freeze)

Keepa returns 429 → _auto_fetch sets the flag;
  caller appends "Keepa auto-fetch failed (HTTP 429);
  results may be stale." to envelope warnings.
Summary carries it through _truncate_warnings; LLM
  prefixes reply with the freshness caveat.
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Concurrent webapp users | Single queue (event loop stall) | Real parallelism | Bounded by Python's default thread pool (~min(32, cpu+4)) |
| LLM reply after Keepa failure | Confident summary, no warning | Reply opens with "results may be stale" | Uses existing `warnings` channel — no schema change |
| `result["meta"]` shape | `auto_fetch_error: True` present but ignored downstream | Still present (API contract unchanged) + a corresponding `warnings` entry | Belt-and-suspenders: both signals coexist |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/tools.py` | 286–407 | All 9 `_step_*` wrappers to be threaded |
| P0 | `src/amz_scout/api.py` | 353–381 | `_auto_fetch` exception return shape |
| P0 | `src/amz_scout/api.py` | 511–753 | 3 auto_fetch call sites (trends/sellers/deals) and their envelope assembly |
| P0 | `webapp/summaries.py` | 85–147 | `_truncate_warnings` + `_build_summary` — the existing warnings passthrough |
| P1 | `webapp/summaries.py` | 140–146 | `_build_summary` allowlist (`asin`/`model`/`brand`/`series_name`/`hint`/`phase` + `warnings`) |
| P1 | `tests/test_webapp_smoke.py` | 173–279 | Existing `TestToolDispatch` monkeypatch pattern for env, session, `cl.step`, `cl.File` |
| P2 | `tests/test_api.py` | 480–515 | Existing patterns for asserting `fetch_meta` keys land in envelope meta |
| P2 | `pyproject.toml` | 9 | `requires-python = ">=3.12"` — confirms `asyncio.to_thread` (3.9+) is available |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| `asyncio.to_thread` | Python 3.12 stdlib docs | Ships `fn(*args, **kw)` to the default `ThreadPoolExecutor` via `run_in_executor`; returns an awaitable. Arguments forwarded as-is. |
| Chainlit concurrency | Chainlit / Starlette / anyio | Chainlit runs on Starlette + anyio. Each session has its own task; without `to_thread`, ALL sessions still share one event loop. `asyncio.to_thread` keeps the import surface stdlib-only. |
| `_auto_fetch` design intent | `src/amz_scout/api.py:359` docstring | "Opportunistic LAZY fetch; failures are logged but never block the query." The design already wants failures to surface non-blockingly — `warnings` is the correct channel. |

---

## Patterns to Mirror

### ASYNC_THREAD_OFFLOAD (NEW — establish for this repo)
```python
# Pattern: keep the wrapper async, offload the sync call.
# Python 3.12+, stdlib-only.
import asyncio

async def _step_query_latest(marketplace: str, category: str | None = None) -> ApiResponse:
    logger.info("query_latest called: marketplace=%s category=%s", marketplace, category)
    return await asyncio.to_thread(
        _api_query_latest, marketplace=marketplace, category=category
    )
```
The minimum-diff transformation is "wrap the return value in
`await asyncio.to_thread(...)`" — keeps logging, decorators, and
signatures identical.

### ENVELOPE_META_WARNINGS
SOURCE: `src/amz_scout/api.py:601-603`, `706-708`
```python
meta_extra: dict = {}
if resolve_warnings:
    meta_extra["warnings"] = resolve_warnings
return _envelope(
    True,
    data=rows,
    ...,
    **fetch_meta,
    **meta_extra,
)
```
Existing convention: `warnings` is a `list[str]` carried inside `meta`.
`_envelope` splats `**meta` into the response dict's `meta` key.
`_build_summary` forwards `meta["warnings"]` through `_truncate_warnings`.

### AUTO_FETCH_RETURN_SHAPE (existing)
SOURCE: `src/amz_scout/api.py:373-381`
```python
# Success with work done:
return {"auto_fetched": True, "tokens_used": ..., "tokens_remaining": ...}
# Cache hit / nothing to do:
return {"auto_fetched": False}
# Exception path (the one we fix):
return {"auto_fetched": False, "auto_fetch_error": True, "auto_fetch_detail": str(e)}
```
We extend this contract indirectly: the caller (query_trends /
query_sellers / query_deals) reads `auto_fetch_error` and surfaces it
into `warnings`. `_auto_fetch` itself has no access to `resolve_warnings`,
so this has to live at the call site.

### TEST_MONKEYPATCH_WEBAPP
SOURCE: `tests/test_webapp_smoke.py:186-216`
```python
def _noop_step(**_kwargs):
    def _decorator(fn):
        return fn
    return _decorator

monkeypatch.setattr(cl, "step", _noop_step)
_reset_webapp_modules()
from webapp import tools as webapp_tools
from webapp.tools import dispatch_tool

def _fake_envelope(*_a, **_kw) -> dict:
    return {"ok": True, "data": [], "error": None, "meta": {"stub": True}}

monkeypatch.setattr(webapp_tools, "_api_query_latest", _fake_envelope)
```
Mirror this exact setup when adding Bug-A/B tests.

### TEST_ENVELOPE_ASSERTION
SOURCE: `tests/test_api.py:485-510`
```python
assert "auto_fetched" in r["meta"]
```
Same shape works for asserting `warnings` presence/content.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `webapp/tools.py` | UPDATE | Wrap the 9 `_api_*` calls in `asyncio.to_thread`; add `import asyncio` at the top (Bug A) |
| `src/amz_scout/api.py` | UPDATE | In `query_trends` / `query_sellers` / `query_deals`, convert `fetch_meta.get("auto_fetch_error")` into a `warnings` entry before envelope build (Bug B) |
| `tests/test_webapp_smoke.py` | UPDATE | Add `TestAsyncThreadOffload` verifying `asyncio.to_thread` dispatches the sync call (Bug A) |
| `tests/test_api.py` | UPDATE | Add `test_auto_fetch_error_surfaces_as_warning` cases for each of trends/sellers/deals (Bug B) |

## NOT Building

- **No new thread-pool tuning.** Default `asyncio.to_thread` uses Python's default executor; sizing is out of scope.
- **No retry on `auto_fetch_error`.** We only surface the signal. Retry belongs to `keepa_service`, not this PR.
- **No change to `_build_summary` allowlist.** We chose Option 2 (warnings channel) specifically to leave the summary schema alone.
- **No `cache_control` / token-audit work.** Issue #13 is scoped to the 2 reliability bugs; token-audit is the separate PR already open on this branch.
- **No `anyio.to_thread.run_sync` migration.** Stdlib `asyncio.to_thread` is equivalent for our usage and keeps the import surface minimal.
- **No change to `_api_*` function signatures.** The sync API surface stays untouched — threading is purely the wrapper layer's concern.

---

## Step-by-Step Tasks

### Task 1: Add `asyncio` import to `webapp/tools.py`
- **ACTION**: Add `import asyncio` at the top of `webapp/tools.py` (isort-sorted with existing stdlib imports).
- **IMPLEMENT**: Single-line addition before `import functools`.
- **MIRROR**: Existing import ordering at `webapp/tools.py:23-28`.
- **IMPORTS**: `asyncio` (stdlib).
- **GOTCHA**: Keep isort-compatible ordering: `asyncio` → `functools` → `logging`.
- **VALIDATE**: `ruff check webapp/tools.py`.

### Task 2: Thread-offload all 9 `_step_*` wrappers in `webapp/tools.py`
- **ACTION**: Replace `return _api_foo(...)` with `return await asyncio.to_thread(_api_foo, ...)` in every `_step_*` function.
- **IMPLEMENT**: Touch lines 297, 305, 311, 324, 337, 352, 365, 378, 407. Each becomes:
  ```python
  return await asyncio.to_thread(_api_query_latest, marketplace=marketplace, category=category)
  ```
  Preserve all kwargs exactly — `to_thread` forwards `*args, **kw` to the target.
- **MIRROR**: `ASYNC_THREAD_OFFLOAD` pattern above.
- **IMPORTS**: `asyncio` (Task 1).
- **GOTCHA**:
  - `asyncio.to_thread(fn, ...)`'s first positional must be the *callable*, not a call. Don't write `asyncio.to_thread(fn(...))` — that would execute sync on the event loop and hand the coroutine the return value.
  - `_step_keepa_budget` (no args) and `_step_check_freshness` (2 kwargs) still offload — even fast-ish functions share the event loop concern.
- **VALIDATE**: `pytest tests/test_webapp_smoke.py -k TestToolDispatch -q` (existing tests must still pass).

### Task 3: Convert `auto_fetch_error` to warnings entry in `query_trends`
- **ACTION**: In `src/amz_scout/api.py:query_trends` (around line 596, after `rows = _add_dates(rows)`), inspect `fetch_meta.get("auto_fetch_error")` and append a human-readable string to `resolve_warnings`.
- **IMPLEMENT**:
  ```python
  if fetch_meta.get("auto_fetch_error"):
      detail = fetch_meta.get("auto_fetch_detail") or "unknown error"
      resolve_warnings.append(
          f"Keepa auto-fetch failed ({detail}); results may be stale."
      )
  ```
  Place this immediately after the `_add_dates(rows)` line (~596), BEFORE the `meta_extra` assembly at ~601. `resolve_warnings` is already in scope from `_resolve_asin`.
- **MIRROR**: `ENVELOPE_META_WARNINGS` pattern above.
- **IMPORTS**: None.
- **GOTCHA**:
  - `resolve_warnings` is a `list[str]`; never re-initialize. Use `.append`.
  - The `if resolve_warnings: meta_extra["warnings"] = ...` check at 602-603 is re-evaluated AFTER this append, so the new warning automatically rides through.
  - Keep the message under ~150 chars — `_truncate_warnings` clips at 200 (`MAX_WARNING_CHARS`) but we want headroom.
  - `resolve_warnings` could be `[]` on the happy-path with no existing warnings; `.append` handles that.
- **VALIDATE**: `pytest tests/test_api.py -k query_trends -q`.

### Task 4: Same auto_fetch_error → warnings conversion in `query_sellers`
- **ACTION**: Mirror Task 3 in `query_sellers` (around line 701, after `rows = _add_dates(rows)`).
- **IMPLEMENT**: Identical 4-line block as Task 3.
- **MIRROR**: Task 3.
- **IMPORTS**: None.
- **GOTCHA**: Place the append BEFORE `meta_extra` at line 706.
- **VALIDATE**: `pytest tests/test_api.py -k query_sellers -q`.

### Task 5: Same auto_fetch_error → warnings conversion in `query_deals`
- **ACTION**: In `query_deals` (around line 748), introduce a local `warnings_list: list[str] = []` (the function has no `resolve_warnings` since it doesn't call `_resolve_asin`), append the auto-fetch-error string, then add a `warnings=...` key to the envelope call.
- **IMPLEMENT**:
  ```python
  warnings_list: list[str] = []
  if fetch_meta.get("auto_fetch_error"):
      detail = fetch_meta.get("auto_fetch_detail") or "unknown error"
      warnings_list.append(
          f"Keepa auto-fetch failed ({detail}); results may be stale."
      )
  envelope_extra: dict = {}
  if warnings_list:
      envelope_extra["warnings"] = warnings_list
  return _envelope(True, data=rows, count=len(rows), **fetch_meta, **envelope_extra)
  ```
  Place this after the `rows = query_deals_history(...)` line, before `return _envelope(...)`.
- **MIRROR**: `ENVELOPE_META_WARNINGS` pattern above.
- **IMPORTS**: None.
- **GOTCHA**:
  - Use the name `warnings_list`, NOT `warnings` — avoids any confusion with the stdlib `warnings` module, even though `query_deals` doesn't import it today.
  - Splat order: `**fetch_meta` then `**envelope_extra` — `_auto_fetch` never returns `warnings` so no collision, but document the assumption via the local variable name.
- **VALIDATE**: `pytest tests/test_api.py -k query_deals -q`.

### Task 6: Add `TestAsyncThreadOffload` suite in `tests/test_webapp_smoke.py`
- **ACTION**: New `@pytest.mark.unit` class at the end of the file. Verify every `_step_*` wrapper dispatches its `_api_*` call off the main thread.
- **IMPLEMENT**:
  ```python
  @pytest.mark.unit
  class TestAsyncThreadOffload:
      """Bug A (issue #13): async wrappers must offload blocking sync I/O
      so one user's slow Keepa fetch does not freeze the Chainlit event loop
      for every other concurrent session.
      """

      def test_every_step_wrapper_uses_asyncio_to_thread(
          self, monkeypatch: pytest.MonkeyPatch
      ) -> None:
          import threading

          _set_fake_env(monkeypatch)

          import chainlit as cl

          def _noop_step(**_kwargs):
              def _decorator(fn):
                  return fn
              return _decorator

          monkeypatch.setattr(cl, "step", _noop_step)

          _reset_webapp_modules()
          from webapp import tools as webapp_tools
          from webapp.tools import TOOL_SCHEMAS, dispatch_tool

          def _record_thread(**_kw) -> dict:
              return {
                  "ok": True,
                  "data": [],
                  "error": None,
                  "meta": {"_thread": threading.current_thread().name},
              }

          for attr in (
              "_api_check_freshness",
              "_api_keepa_budget",
              "_api_query_availability",
              "_api_query_compare",
              "_api_query_deals",
              "_api_query_latest",
              "_api_query_ranking",
              "_api_query_sellers",
              "_api_query_trends",
          ):
              monkeypatch.setattr(webapp_tools, attr, _record_thread)

          async def _run_all() -> list[tuple[str, dict]]:
              out: list[tuple[str, dict]] = []
              for tool in TOOL_SCHEMAS:
                  name = tool["name"]
                  args: dict = {}
                  for prop in tool["input_schema"].get("required", []):
                      args[prop] = "UK" if prop == "marketplace" else "Slate 7"
                  out.append((name, await dispatch_tool(name, args)))
              return out

          for name, result in asyncio.run(_run_all()):
              thread_name = result["meta"]["_thread"]
              assert not thread_name.startswith("MainThread"), (
                  f"{name}: sync _api_* ran on {thread_name!r} — Bug A regression. "
                  f"Wrapper must await asyncio.to_thread(_api_*, ...)."
              )
  ```
- **MIRROR**: `TEST_MONKEYPATCH_WEBAPP` pattern.
- **IMPORTS**: `threading` (added inside the test body to keep module-level imports minimal).
- **GOTCHA**:
  - Tool-level `meta` is passed through untouched by `summarize_for_llm` (only `data` is rewritten), so `result["meta"]["_thread"]` survives for the row-emitting tools.
  - `check_freshness` / `keepa_budget` don't go through `summarize_for_llm` — they also pass `meta` through unchanged.
  - Underscore-prefix (`_thread`) avoids collision with any real meta key.
- **VALIDATE**: `pytest tests/test_webapp_smoke.py::TestAsyncThreadOffload -q`.

### Task 7: Add auto_fetch_error warning tests in `tests/test_api.py`
- **ACTION**: Add 3 cases (or one parametrized test) that patch `amz_scout.keepa_service.get_keepa_data` to raise and verify the envelope `meta["warnings"]` contains the auto-fetch error string for `query_trends`, `query_sellers`, `query_deals`.
- **IMPLEMENT**: Next to the existing `"auto_fetched" in r["meta"]` block (lines 485–515), add:
  ```python
  def test_query_trends_auto_fetch_error_surfaces_as_warning(monkeypatch) -> None:
      import amz_scout.api as api_mod

      def _boom(*_a, **_kw):
          raise RuntimeError("HTTP 429 rate limited")

      monkeypatch.setattr("amz_scout.keepa_service.get_keepa_data", _boom)

      r = api_mod.query_trends(
          project=<reuse-existing-fixture>,
          product=<reuse-existing-fixture>,
          marketplace="UK",
      )
      warnings = r["meta"].get("warnings") or []
      assert any("auto-fetch failed" in w.lower() for w in warnings), (
          f"Bug B regression: auto_fetch_error did not reach envelope warnings. "
          f"meta={r['meta']!r}"
      )
      assert r["meta"].get("auto_fetch_error") is True, (
          "Backward-compat: legacy meta flag must still be present"
      )
  ```
  Add analogous tests for `query_sellers` and `query_deals` — reuse the exact fixtures the existing 485–515 block uses (project path / product name).
- **MIRROR**: `tests/test_api.py:480-515`.
- **IMPORTS**: None beyond existing.
- **GOTCHA**:
  - `_auto_fetch` does `from amz_scout.keepa_service import get_keepa_data` *inside* its try block. Patch string must be `"amz_scout.keepa_service.get_keepa_data"` (source module), NOT `"amz_scout.api.get_keepa_data"` — the import happens every call, so `monkeypatch.setattr` on the source module is effective.
  - Do NOT assert exact message text — substring "auto-fetch failed" survives copy tweaks.
  - Keep both assertions: guarantee the new warning AND preserve the legacy `auto_fetch_error` flag for backward compat.
- **VALIDATE**: `pytest tests/test_api.py -k auto_fetch_error -q`.

### Task 8: Full regression sweep
- **ACTION**: Run the full test suite and ruff.
- **IMPLEMENT**: `pytest -q && ruff check .`
- **MIRROR**: N/A.
- **IMPORTS**: N/A.
- **GOTCHA**:
  - `tests/test_token_audit.py` is skipped without `ANTHROPIC_API_KEY` (`pytestmark = pytest.mark.network`). Its contract-partner `tests/test_summaries_contract.py` runs unconditionally and must stay green — our changes do NOT touch `_build_summary` or `_envelope_summary`.
  - The existing `test_warnings_are_truncated_before_reaching_llm` already covers the webapp-side half of Bug B. Task 7 adds the API-side half.
- **VALIDATE**: All tests pass, no new ruff warnings.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_every_step_wrapper_uses_asyncio_to_thread` | Each of 9 tools invoked via `dispatch_tool` with a sync stub recording `threading.current_thread().name` | `thread_name` does NOT start with `"MainThread"` | no-arg tool (`keepa_budget`) included |
| `test_query_trends_auto_fetch_error_surfaces_as_warning` | `query_trends` with `get_keepa_data` raising `RuntimeError` | `meta["warnings"]` contains "auto-fetch failed"; `meta["auto_fetch_error"] is True` | network failure |
| `test_query_sellers_auto_fetch_error_surfaces_as_warning` | Ditto for `query_sellers` | Ditto | |
| `test_query_deals_auto_fetch_error_surfaces_as_warning` | Ditto for `query_deals` | Ditto | `query_deals` has no `resolve_warnings` — exercises local `warnings_list` branch from Task 5 |
| Existing `test_warnings_are_truncated_before_reaching_llm` | Unchanged | Still passes — we didn't modify `_truncate_warnings` or the summary allowlist | Regression guard |
| Existing `test_dispatcher_routes_all_known_tools` | Unchanged | Still passes — envelope shape untouched | Regression guard |
| Existing `test_envelope_summary_matches_production_build_summary` | Unchanged | Still passes — summary contract unchanged | Regression guard |

### Edge Cases Checklist
- [x] No-arg tool (`keepa_budget`) still offloads correctly
- [x] Tool with `None` kwargs (`query_deals` with no marketplace) still offloads
- [x] `_auto_fetch` cache-hit path (`auto_fetched: False`, no error) → no spurious warning appended
- [x] `_auto_fetch` success path (`auto_fetched: True`) → no spurious warning appended
- [x] Concurrent offloaded calls across 9 tools don't saturate the default thread pool (32+ workers)
- [x] `query_deals` local `warnings_list` doesn't shadow the stdlib `warnings` module

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/ src/amz_scout/ tests/
```
EXPECT: Zero new warnings; existing warnings unchanged.

### Unit Tests — targeted
```bash
pytest tests/test_webapp_smoke.py::TestAsyncThreadOffload -q
pytest tests/test_api.py -k auto_fetch_error -q
```
EXPECT: All new tests green.

### Unit Tests — regression sweep
```bash
pytest -q --ignore=tests/test_token_audit.py
```
EXPECT: Everything green (excluding the network-gated token audit).

### Full Test Suite (when ANTHROPIC_API_KEY available)
```bash
pytest -q
```
EXPECT: All tests pass.

### Manual Validation (optional smoke)
- [ ] Start Chainlit: `chainlit run webapp/app.py -w --port 8000`
- [ ] In one session: ask "Slate 7 UK 趋势" (triggers Keepa HTTP)
- [ ] In a second browser: immediately ask "keepa token 余额"
- [ ] Confirm session 2 replies in < 1s while session 1 is still fetching (Bug A fix)
- [ ] Simulate network failure (blackhole Keepa domain or let rate-limit trip) → confirm reply opens with "auto-fetch failed" wording (Bug B fix)

---

## Acceptance Criteria
- [ ] All 9 `_step_*` wrappers in `webapp/tools.py` await `asyncio.to_thread(_api_*, ...)`
- [ ] `query_trends`, `query_sellers`, `query_deals` append "auto-fetch failed" to envelope `meta["warnings"]` when `_auto_fetch` set `auto_fetch_error`
- [ ] `meta["auto_fetch_error"]` legacy flag remains present (backward compatibility)
- [ ] New `TestAsyncThreadOffload` suite green
- [ ] New `test_*_auto_fetch_error_surfaces_as_warning` suite (3 tests) green
- [ ] Full regression sweep green (excluding network-gated tests)
- [ ] Ruff clean

## Completion Checklist
- [ ] Code follows discovered patterns (`ASYNC_THREAD_OFFLOAD`, `ENVELOPE_META_WARNINGS`)
- [ ] Error handling matches codebase style (no new try/except; reuses `_auto_fetch`'s existing catch)
- [ ] Logging untouched (one-line `logger.info` per wrapper preserved)
- [ ] Tests follow `TEST_MONKEYPATCH_WEBAPP` pattern
- [ ] No hardcoded values beyond the localized warning string
- [ ] Documentation: none needed — CLAUDE.md already documents the LAZY contract; this PR restores it
- [ ] Scope: exactly Bug A + Bug B. Nothing else.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `asyncio.to_thread` + SQLite `check_same_thread` | Medium | Sync SQLite raises `ProgrammingError` if a connection is created on one thread and used on another | Each `_api_*` opens its own connection (`open_db` context manager) inside the call; the connection never crosses threads. Verified at `api.py:473`, `502`, `529`, `624`, `642`, `655`, `679`, `736`. |
| Default thread pool saturation | Low | High concurrency could queue > 32 blocking calls | Out of scope; current Chainlit deployments are small. Flag for future observability work if seen. |
| Existing tests relying on "sync-inline" behavior | Low | Tests could race or timing-assume | All 9 wrappers are covered by `TestToolDispatch` which uses `asyncio.run(...)` — already awaits the coroutine, so thread dispatch is transparent. |
| Double-reporting: `auto_fetch_error` both as meta flag AND warning | Low | Could be mistaken for two failures | Warning text names it explicitly ("Keepa auto-fetch failed") so the user/LLM sees one event described two ways, not two events. |
| `query_deals` has no registered products → `fetch_meta == {}` → no warning even when Keepa is down | Low | Silent failure — but `query_deals` never called `_auto_fetch` in that branch, so there is no failure to report | Current behavior preserved: no products = no auto-fetch attempt. |

## Notes
- Chose `asyncio.to_thread` over `anyio.to_thread.run_sync` purely for stdlib minimalism. Either works in Chainlit. If we later want finer-grained cancellation propagation, migrate to `anyio`.
- Chose warnings-channel (Option 2 in issue #13) over allowlist-extension (Option 1) because the summary schema is actively load-bearing in token-audit and design pressure should keep it tight.
- Scope boundary: this PR does NOT take on the token-audit bundle mentioned as "suggested PR scope" in the issue. That bundle is the work already underway on branch `fix/token-audit-envelope-drift` (commits `a6714d7` and the untracked `tests/test_summaries_contract.py` / `tests/test_token_audit.py` mods). Keeping the two PRs separate lets reviewers evaluate reliability (this PR) and cost (that PR) independently.
- PR body etiquette: per prior-session feedback (2026-04-20), `closes #13` is valid ONLY on the PR that genuinely closes the issue. If this PR implements both bugs, use `closes #13`. If for any reason only one bug lands, use `addresses #13` instead.
