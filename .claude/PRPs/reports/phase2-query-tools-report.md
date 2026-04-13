# Implementation Report: Phase 2 — Remaining Query Tools

## Summary

Expanded `webapp/tools.py` from a single wrapped query function (`query_latest`) to all 9 read-only query functions from `amz_scout.api`: `query_latest`, `check_freshness`, `keepa_budget`, `query_availability`, `query_compare`, `query_deals`, `query_ranking`, `query_sellers`, `query_trends`. Each tool has (a) a typed Anthropic tool schema with bilingual CN/EN descriptions, (b) a Chainlit `@cl.step` wrapper for UI tracing, and (c) a dispatcher branch. `cache_control` moved to `query_trends` (the new last/most-called tool) so the whole 9-tool block caches as one prompt-cache breakpoint. Two new smoke tests added. Purely additive — no changes to `app.py`, `llm.py`, `auth.py`, `config.py`, or `amz_scout/`.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small-to-Medium | Small-to-Medium (as predicted) |
| Estimated Lines | ~400 | ~310 added in `tools.py`, ~55 added in tests |
| Files Changed | 2 modified | 2 modified |
| Time | ~3 hours | ~40 min (single session) |
| Unexpected issues | None predicted | 1 — `@cl.step` requires Chainlit context at call time, which the plan's proposed dispatcher test did not account for |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Add 8 API imports | Complete | Used 9 one-line aliased imports to satisfy ruff isort (I001). |
| 2 | Add 8 new tool schemas | Complete | Alphabetical order, `query_trends` last with `cache_control`. Removed `cache_control` from `query_latest`. |
| 3 | Add 8 step wrappers | Complete | Mirrors Phase 1 `_step_query_latest` pattern exactly. |
| 4 | Extend `dispatch_tool` | Complete | 9 independent `if`/`return` branches + unknown-tool envelope fallback. |
| 5 | Extend smoke tests | Complete | Deviated — see below. |
| 6 | Hand-test running webapp | Not run | Requires interactive browser session; deferred to manual follow-up. |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff check) | Pass | Zero issues on `webapp/` + `tests/test_webapp_smoke.py` |
| Formatting (ruff format --check) | Pass | All 7 files already formatted |
| Unit Tests (smoke) | Pass | 6/6 in `tests/test_webapp_smoke.py` (4 existing + 2 new), 1.60s |
| Full Test Suite | Pass | 226 passed, 1 warning, 35.25s — zero regressions |
| Import Surface | Pass | `TOOL_SCHEMAS` contains exactly the 9 expected names |
| Manual Browser Validation | Skipped | Requires running `chainlit run webapp/app.py -w` + interactive login; not run in this automated pass. Jack should hand-test before merging. |

## Files Changed

| File | Action | Approx Lines |
|---|---|---|
| `webapp/tools.py` | UPDATED | +~310 / -~5 (adds 8 schemas + 8 wrappers + 8 dispatch branches; removes `cache_control` from `query_latest`) |
| `tests/test_webapp_smoke.py` | UPDATED | +~55 (adds `test_all_phase2_tool_names_present` and `test_dispatcher_routes_all_known_tools`) |

## Deviations from Plan

### Deviation 1 — Import style

**WHAT**: The plan specified a single multi-line `from amz_scout.api import (...)` block with 9 aliased names. Ruff's isort rule (I001) didn't accept that shape (or its autofix split it into 9 redundant full-form imports). Final form is 9 single-line `from amz_scout.api import X as _api_X` statements.

**WHY**: Ruff config in `pyproject.toml` has `select = ["E", "F", "I", "W"]`. The isort rule was unhappy with the grouped form. Nine single-line imports are unambiguously sorted and ruff accepts them cleanly.

**Impact**: Zero functional impact — same names imported into the module namespace.

### Deviation 2 — Dispatcher routing test required decorator + API stubs

**WHAT**: The plan's proposed `test_dispatcher_routes_all_known_tools` called `dispatch_tool(name, min_args)` directly and expected graceful envelope responses. Two problems surfaced when I ran it:

1. **`@cl.step` requires a Chainlit session context**. Every decorated wrapper raises `ChainlitContextException` when called outside a real Chainlit run (`Step.__init__` reads `context.session.thread_id`). Phase 1's only dispatcher test (`test_unknown_tool_returns_envelope`) never reaches a wrapper because it short-circuits on the unknown-tool path, so this didn't surface until we started routing through real wrappers.
2. **Real API calls hit the real SQLite registry**. With a real `amz_scout.db` present, `_api_query_trends(product="Slate 7", marketplace="UK")` resolves a real product, tries to LAZY-fetch Keepa with `KEEPA_API_KEY=fake`, and either errors slowly or hangs on network. One test run hung past 60 seconds before I killed it.

**WHY**: Both are environmental facts the plan's "auto_fetch only fires when there's no DB" assumption did not hold for Jack's actual dev environment, which has a real DB.

**HOW (fix)**: The dispatcher test now:
- Monkeypatches `chainlit.step` to a no-op decorator **before** re-importing `webapp.tools`, so the wrappers become plain async functions.
- Monkeypatches every `webapp.tools._api_*` alias on the freshly-imported module to a fake `_fake_envelope` function that returns `{"ok": True, "data": [], "error": None, "meta": {"stub": True}}`.

This preserves the test's intent (verify dispatcher routing + envelope shape) while isolating it from Chainlit and the real API. The test now passes in ~0.5s.

**Impact**: The test is slightly more involved than the plan sketch (two layers of monkeypatching instead of none), but the signal is better — it exercises every wrapper's call path, not just the unknown-tool fallback.

### Deviation 3 — Task 6 (hand-test webapp) deferred

**WHAT**: The plan's Task 6 asks for a manual `chainlit run webapp/app.py -w` session with three representative queries. I did not run this.

**WHY**: Requires an interactive browser login + a valid `APP_PASSWORD` + Jack's user inspection. Not suitable for an automated `/prp-implement` pass.

**Next step**: Jack should hand-test before merging the PR. The manual validation checklist from the plan still applies unchanged.

## Issues Encountered

1. **Ruff isort split imports** — resolved by switching to 9 single-line aliased imports (Deviation 1).
2. **`@cl.step` needs a Chainlit context** — resolved by monkeypatching `cl.step` to a no-op before re-import (Deviation 2).
3. **Real DB + real Keepa fetch attempt hung the dispatcher test** — resolved by stubbing every `_api_*` alias with a fake envelope (Deviation 2).

## Tests Written

| Test File | Tests Added | Coverage |
|---|---|---|
| `tests/test_webapp_smoke.py` | `test_all_phase2_tool_names_present` | Asserts the exact 9-tool name set in `TOOL_SCHEMAS` |
| `tests/test_webapp_smoke.py` | `test_dispatcher_routes_all_known_tools` | Asserts every schema's name routes through `dispatch_tool` and returns envelope-shaped dict (isolated from Chainlit + real API via monkeypatching) |

## Acceptance Criteria Check

- [x] All 9 query tools declared in `TOOL_SCHEMAS`
- [x] All 9 step wrappers exist
- [x] Dispatcher routes all 9 names
- [x] Only the LAST tool (`query_trends`) carries `cache_control`
- [x] All smoke tests pass (6/6)
- [x] `ruff check` zero issues
- [ ] Manual flow: 3/3 representative queries return envelope data in the running webapp — **DEFERRED to Jack**
- [x] Zero changes to `app.py`, `llm.py`, `auth.py`, `config.py`, `amz_scout/`
- [x] Diff is purely additive in `webapp/tools.py` (plus the `cache_control` move)

## Next Steps

- [ ] Jack hand-tests the running webapp with the 3 representative queries from Task 6
- [ ] Code review via `/code-review` (optional — changes are mechanical and mirror Phase 1)
- [ ] Commit and open PR via `/prp-commit` + `/prp-pr`
- [ ] Update PRD Phase 2 status from `in-progress` to `complete` once hand-test passes
