# Implementation Report: Webapp Anthropic Web Search ASIN Discovery

**Plan**: `.claude/PRPs/plans/completed/webapp-anthropic-web-search-asin.plan.md`
**Branch**: `feat/webapp-anthropic-web-search-asin`
**Implemented**: 2026-04-24

## Summary

Added an Anthropic server-side `web_search_20260209` tool declaration plus a
client-side bridge tool `register_asin_from_url` so webapp users (no Claude
Code, no `browser-use`) can find Amazon ASINs by chat and have the LLM
register them into the SQLite product registry — entirely without touching
Keepa search or launching a browser. `webapp/llm.py` was hardened with a
`pause_turn` resume branch (server-tool 10-iter cap) and a 20-block lookback
warning so cache-miss drift on long turns is observable.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Estimated Files | 6 (plan listed 7 incl. PRD) | 7 modified, 0 net-new files |
| Tests added | ~15 listed | 17 added, all green |

The plan's `register_asin_from_url` was inlined into existing `api.py`
rather than as a new module — same scope, fewer files.

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `register_asin_from_url` in `amz_scout.api` | Complete | Mirrors `discover_asin` dual-branch write; notes tagged `discovered via web_search` |
| 2 | `web_search_20260209` server-side schema | Complete | Inserted before `query_trends`; static `_AMAZON_DOMAINS`; `max_uses=5` |
| 2.5 | `pause_turn` resume in `webapp/llm.py` | Complete | Inserted before existing `!= tool_use` branch; no fake "Continue" injection |
| 2.6 | 20-block lookback warning | Complete | Threshold = 15 blocks (5-block buffer) |
| 3 | `register_asin_from_url` client tool | Complete | `cache_control` migrated from `query_trends` to new last element |
| 4 | `SYSTEM_PROMPT` ASIN Discovery Flow | Complete | 6-step flow + injection-defense line ("do not fabricate Amazon URLs") |
| 5 | Webapp tests | Complete | Renamed phase2 names test; iterators skip server-side tools; budget bumped 6000→8000 |
| 6 | API unit tests | Complete | 11 cases (plan listed 7); see Deviations |
| 7 | PRD drift fix | Complete | Sub-scope note in Phase 4 row + details, dated 2026-04-24 |
| 8 | Final validation | Complete | ruff clean; 349 passed / 8 skipped (network) / 0 failed |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff) | Pass | `src/ webapp/ tests/` all clean |
| Unit Tests (new) | Pass | 17 new tests — 11 in `test_api.py` + 6 in `test_webapp_smoke.py` |
| Unit Tests (full) | Pass | 349 passed, 8 skipped (network-marked, no local API keys), 0 failed |
| Build | N/A | Library project; import smoke verified |
| Integration | Skipped | Manual smoke (Task 8) requires real `ANTHROPIC_API_KEY` + console "web search" toggle |
| Edge Cases | Pass | invalid URL, host mismatch, alias normalization, no-scheme, intl TLDs (JP/BR/MX), unknown market, tracking-query tolerance |

## Files Changed

| File | Action | Notes |
|---|---|---|
| `src/amz_scout/api.py` | UPDATED | Added `_ASIN_URL_RE` const + `register_asin_from_url()` function (~106 lines) |
| `webapp/tools.py` | UPDATED | New `_AMAZON_DOMAINS` const, web_search server-side schema, register_asin_from_url client schema + step wrapper + dispatcher branch; cache_control migration |
| `webapp/llm.py` | UPDATED | `pause_turn` continue branch + total_blocks > 15 warning |
| `webapp/config.py` | UPDATED | SYSTEM_PROMPT extended with ASIN Discovery Flow |
| `tests/test_webapp_smoke.py` | UPDATED | Existing tests adapted (server-side tool skip, expected-set extension, schema budget bump) + 6 new tests |
| `tests/test_api.py` | UPDATED | Import addition + new `TestRegisterAsinFromUrl` class with 11 tests |
| `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | UPDATED | Phase 4 row + details: sub-scope note dated 2026-04-24 |

## Deviations from Plan

1. **Test count for register_asin_from_url**: plan listed 7 cases; ended up
   with 11 (added `test_invalid_url_no_dp_segment`, `test_unknown_marketplace_code`,
   `test_tracking_query_tolerated`, plus split `test_international_tld` into
   per-TLD methods for readable failure output). All pass.
2. **`tools_with_required` row**: plan suggested adding a row per required
   field for `register_asin_from_url`. The dispatcher returns on the *first*
   missing field (`brand`), so 4 rows would have failed the assertion. Kept
   1 representative row with a comment explaining the validation pattern.
3. **Test ASIN length bug** (caught & fixed mid-loop): initial new tests used
   11-char placeholder ASINs (`B0TESTTEST1`); the 10-char regex correctly
   rejected those. Fixed all 10 occurrences to 10-char ASINs and rerun
   succeeded.
4. **Pre-existing pyright noise**: lines 60-62 in `webapp/llm.py` and several
   places in `tests/` carry pre-existing pyright type-narrowing warnings
   (Anthropic SDK TypedDict vs plain dict). Project does not enforce pyright
   in CI; ruff is the gate, and ruff is clean.

## Issues Encountered

- **Initial 10/11 test failures** in `TestRegisterAsinFromUrl` from off-by-one
  in placeholder ASINs (11 chars instead of 10). Resolved by `replace_all`
  fixups; all 11 then passed.
- **No other blockers** — every task validated cleanly after that fix.

## Tests Written

| Test File | Class | Count | Coverage |
|---|---|---|---|
| `tests/test_api.py` | `TestRegisterAsinFromUrl` | 11 | Happy paths (new+existing product), invalid URL, host mismatch, marketplace alias, no-scheme URL, intl TLDs (JP/BR/MX), tracking-query tolerance, unknown market |
| `tests/test_webapp_smoke.py` | `TestWebSearchTool` | 2 | `web_search_20260209` declared with required `allowed_domains`; no `code_execution_*` co-declaration regression |
| `tests/test_webapp_smoke.py` | `TestRegisterAsinFromUrlDispatch` | 1 | Compact dict response bypasses `summarize_for_llm` (no spurious xlsx attach) |
| `tests/test_webapp_smoke.py` | `TestPauseTurnHandling` | 1 | `pause_turn → end_turn` two-call resume; no fake "Continue" user message injection |
| `tests/test_webapp_smoke.py` | `TestBlockCountMonitor` | 1 | `caplog` captures the warning when history > 15 blocks |
| `tests/test_webapp_smoke.py` | `TestSystemPromptContent` | 1 | SYSTEM_PROMPT contains ASIN Discovery Flow + injection-defense lines |

## Next Steps

- [ ] **Manual smoke (Task 8)** — pending operator action: enable Anthropic
  console "Web search" privacy toggle, then `chainlit run webapp/app.py -w`
  and try `find Slate 7 ASIN in Spain` (or similar untracked-market prompt).
- [ ] **Cache impact monitoring** — first request after this PR will
  cache-miss (tool definitions changed, including a new server-side type).
  Watch `cache_read_input_tokens` for 1-2 days post-merge.
- [ ] **Code review via `/code-review`** before PR.
- [ ] **PR via `/prp-pr`** after review.
