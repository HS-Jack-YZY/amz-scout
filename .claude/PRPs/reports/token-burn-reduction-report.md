# Implementation Report: Reduce Claude Token Burn

**Branch**: `feat/reduce-api-token-burn`
**Date**: 2026-04-14
**Plan**: `.claude/PRPs/plans/reduce-api-token-burn.plan.md`

## Summary

Two independent token-saving levers landed in one plan:

1. **Envelope trimming** — every `_step_*` wrapper in `webapp/tools.py` is
   decorated with `@trim_for_llm(...)` from the new `amz_scout._llm_trim`
   module, which produces allow-listed copies of row dicts before the
   envelope reaches the LLM. The LLM sees only the fields an analyst would
   cite; **`amz_scout.api` itself returns full DB rows**, so CLI / admin /
   future scripts keep seeing the complete schema.
2. **Moving cache_control breakpoint** — `webapp/llm.py` attaches
   `cache_control: {"type": "ephemeral"}` to the last `tool_result` block of
   every turn, so every subsequent turn's prompt prefix is cache-read at
   ~10% of normal input cost.

> **Post-review correction (2026-04-14)** — the initial implementation wired
> `_llm_trim` *inside* the seven `amz_scout.api.query_*` functions. Copilot's
> PR review caught that this also trimmed CLI output (e.g. `amz-scout query
> latest` rendered an empty `fulfillment` column) and contradicted the
> module's own docstring ("CLI code must not import from it"). The fix moved
> trimming to the webapp boundary via `@trim_for_llm` decorators on every
> row-emitting `_step_*` wrapper, and added two regression suites:
> `tests/test_api.py::TestApiEnvelopeCompleteness` (api layer must return
> full rows) and `tests/test_webapp_smoke.py::TestWebappTrimBoundary`
> (webapp dispatch must trim). Token savings are unchanged — the LLM still
> sees identical bytes — only the ownership of the trim step moved one
> layer outwards.

Measurement is gated by a new pytest harness that calls Anthropic's
`count_tokens` endpoint (free, non-billing) and writes
`output/token_audit.json`.

## Method

`tests/test_token_audit.py` measures each tool twice — once with the untrimmed
envelope reconstructed by calling the `db.py` layer directly, once with the
trimmed envelope returned by the public `amz_scout.api` function. Both payloads
are wrapped in a minimal valid Anthropic message sequence:

```
user: "Run audit query."
assistant: tool_use(id=toolu_audit, name=audit_tool, input={})
user: tool_result(tool_use_id=toolu_audit, content=<json-serialised envelope>)
```

`count_tokens` returns `input_tokens`. The delta between the two calls is
dominated by the `tool_result.content` size; the scaffold (system prompt,
user question, tool_use block) adds a constant offset that cancels out.

The harness is marked `@pytest.mark.network`; CI skips cleanly when
`ANTHROPIC_API_KEY` is absent or `output/amz_scout.db` is missing. Every
per-tool test also skips when the underlying DB query yields zero rows —
trim savings on an empty list are uninformative.

## Per-tool deltas

From `output/token_audit.json` after running against the live shared DB at
`output/amz_scout.db` (2026-04-14):

| Tool                    | Before (tokens) | After (tokens) | Saved   |
|-------------------------|-----------------|----------------|---------|
| `query_trends` (real)   | 7,286           | 3,584          | **50.8%** |
| `query_deals` (real)    | 293             | 254            | **13.3%** |
| `query_latest_synth20`  | 7,167           | 2,513          | **64.9%** |

Notes:

- The live DB currently has **0 rows** in `competitive_snapshots` and
  `keepa_buybox_history`, so `query_latest`, `query_ranking`,
  `query_availability`, `query_compare`, and `query_sellers` each skipped with
  a clear reason. Synthetic coverage for the `SELECT cs.*` shape comes from
  `test_query_latest_synthetic_token_delta`, which builds 20 canonical
  competitive_snapshots rows in memory and exercises the same trim.
- The 64.9% saving on the synthetic competitive row set is representative
  of what `query_latest`/`query_ranking`/`query_availability`/`query_compare`
  will deliver once those tables have real rows: the 13-field allow-list
  drops 19 of 32 columns, including the large `*_raw` strings, `title`,
  `url`, and `star_distribution`.
- `query_trends` saves more in *absolute* terms than any other tool because
  it returns many rows (90+ daily points) and each row's dropped
  `keepa_ts`/`fetched_at` fields are high-entropy integers/timestamps.
- `query_deals` shows a modest 13% saving because there are only 6 deal rows
  in the live DB; envelope scaffolding dominates at that scale.

## Run commands

Validation happened locally via:

```bash
# Fast path (no network)
pytest tests/test_llm_trim.py tests/test_api.py tests/test_webapp_smoke.py

# Full suite
pytest --ignore=tests/test_webapp_deployment_smoke.py

# Token audit (requires ANTHROPIC_API_KEY + output/amz_scout.db)
set -a; source .env; set +a
pytest tests/test_token_audit.py
cat output/token_audit.json
```

Results:

- Fast path: **118 passed**.
- Full suite: **247 passed, 5 skipped** (all 5 skips are in the token audit,
  gated on empty DB tables).
- Ruff: `ruff check src/ tests/ webapp/` — 0 issues. `ruff format --check`
  — 40 files already formatted.

## End-to-end turn example

> **Status**: the moving cache_control wiring is code-complete and verified
> by the unit test in `tests/test_webapp_smoke.py::TestCacheControlWiring::
> test_last_tool_result_block_gets_cache_control`. Live 3-turn webapp logs
> with real `resp.usage.model_dump()` output are pending: they require a
> Chainlit server session and at least three consecutive `query_latest`
> calls. Once captured, paste them here under:
>
> ```
> Turn 1: usage={"input_tokens": X, "cache_creation_input_tokens": Y, "cache_read_input_tokens": 0, "output_tokens": Z}
> Turn 2: usage={"input_tokens": X', "cache_creation_input_tokens": 0, "cache_read_input_tokens": Y, "output_tokens": Z'}
> Turn 3: usage={"input_tokens": X'', "cache_creation_input_tokens": Δ, "cache_read_input_tokens": Y+..., "output_tokens": Z''}
> ```
>
> `logger.info("usage: %s", resp.usage.model_dump())` is already in place at
> `webapp/llm.py:47`, so any real session emits the lines above to stdout.

## Estimated monthly savings

Conservative (only the two confidently measured tools):

- Weighted average savings on `query_trends` + `query_deals`: ≈ **50%**
  (trends dominates absolute token volume).
- With the synthetic competitive numbers factored in (representative of
  `query_latest`/`query_ranking`/`query_availability`/`query_compare` once
  `competitive_snapshots` has real data): ≈ **55–60%** average savings on
  `tool_result` payloads across the 7 audited tools.

Assume ~50 turns/day, ~2 tool calls/turn, averaging ~3.5k tokens saved per
tool call → ~350k tokens/day → ~10.5M tokens/month of *input* savings. At
Sonnet 4.6 pricing ($3/Mtok input) that's ≈ **$31/month** saved on input
alone, before caching kicks in.

Once the moving cache_control breakpoint starts hitting (turn 2+ in any
single session), cache reads charge ~$0.30/Mtok instead of $3/Mtok — a
further ~90% discount on the fraction of each turn's prompt that lives above
the last tool_result. This is strictly additive to the trim savings.

Real numbers will come from the 3-turn webapp log above once captured.

## Files Changed

| File | Action | Change |
|---|---|---|
| `src/amz_scout/_llm_trim.py` | CREATED | 4 frozen sets + `trim` helper + 4 partial aliases |
| `src/amz_scout/api.py` | UPDATED | Initial PR added 7 trim call-sites here; the post-review correction REMOVED them — api now returns full DB rows. No signature changes. |
| `webapp/tools.py` | UPDATED | Added `trim_for_llm(trimmer)` decorator + applied it to 7 row-emitting `_step_*` wrappers (latest/availability/compare/deals/ranking/sellers/trends). Trim now lives at the webapp boundary only. |
| `webapp/llm.py` | UPDATED | `logger.info("usage: ...")` per turn + moving `cache_control` on `tool_results[-1]` |
| `tests/test_llm_trim.py` | CREATED | 15 unit tests for allow-list/immutability/empty-input/unicode (location-agnostic, unchanged by post-review correction) |
| `tests/test_api.py` | UPDATED | +1 class `TestApiEnvelopeCompleteness` (4 tests) — contract test asserting api layer returns wide-row markers (`title`, `url`, `sold_by`, `fulfillment`). Guards against trim creeping back into api.py. |
| `tests/test_token_audit.py` | CREATED | 8 network-gated tests + 1 synthetic fallback; writes `output/token_audit.json` |
| `tests/test_webapp_smoke.py` | UPDATED | +2 tests in `TestCacheControlWiring` class; +4 tests in new `TestWebappTrimBoundary` class (latest/trends/deals trim + failure-envelope passthrough) |
| `pyproject.toml` | UPDATED | Register `network` marker |
| `CLAUDE.md` | UPDATED | Add bullet #15 documenting the trimmed-envelope contract (post-review revision: explicitly state trim lives at webapp boundary, link to both regression suites) |
| `.claude/PRPs/reports/token-burn-reduction-report.md` | CREATED | This report |
| `.claude/PRPs/reviews/local-reduce-api-token-burn-review.md` | CREATED | Local self-review artifact |

## Deviations from Plan

- **Task 3 harness fix**: the plan's `_count` helper built a user message with
  a standalone `tool_result` block. Anthropic rejects this with HTTP 400
  (`tool_result` must be paired with a preceding `tool_use`). The implemented
  helper prepends a minimal `user → assistant(tool_use)` scaffold so
  `tool_result` has its mandatory counterpart. The additive offset is constant
  across before/after calls, so percentage savings are unaffected.
- **Empty-table skips**: the plan's harness assumed every audited tool would
  have rows in the live DB. The current `output/amz_scout.db` has 0 rows in
  `competitive_snapshots` and `keepa_buybox_history`. Rather than fail those
  tests, each was guarded with a `pytest.skip(...)` call that triggers only
  when the DB layer returns an empty list. A synthetic 20-row fallback was
  added for the competitive shape so the report always has one concrete
  "SELECT cs.*" measurement.
- **Risk mitigation not taken**: the plan suggested including `title` in the
  competitive allow-list "for the first iteration". The implementation
  dropped `title` — it is the single biggest bloat field in
  `competitive_snapshots` and the `brand`/`model` columns already carry the
  decision-relevant identity the LLM needs. If production quality regresses,
  add `"title"` back to `LLM_SAFE_COMPETITIVE_FIELDS` in `_llm_trim.py` and
  re-run the audit.

## Issues Encountered

1. **Fact-Forcing Gate**: the session's PreToolUse hook requires a fact
   statement before every file create/edit. Complied with on every write;
   no workarounds.
2. **`python -m pytest` shim**: `python -m pytest tests/test_api.py` returned
   "Pytest: No tests collected" in this shell environment regardless of
   arguments. Direct `pytest` binary invocation worked. Some flag
   combinations (`-x -q`, `-rs` without quoting the test path) also hit the
   same shim — quoting the path string unblocks. Not fixed in this PR; worth
   raising with the harness owner.
3. **Real API call surface**: `count_tokens` is free per Anthropic docs but
   still counts against rate limits. Harness scope is `scope="module"` so
   one authenticated client services all 7 tests; total calls per run are
   ≤ 16.

## Acceptance Criteria Status

- [x] `src/amz_scout/_llm_trim.py` exists with 4 allow-lists + trim functions
- [x] All 7 LLM-facing `query_*` tools have their `_step_*` webapp wrapper decorated with `@trim_for_llm` (post-review correction — the initial PR wired this in `api.py`, which leaked into CLI; trim now lives at the webapp boundary only)
- [x] `amz_scout.api.query_*` returns full DB rows for CLI / admin / future scripts (verified by `tests/test_api.py::TestApiEnvelopeCompleteness`)
- [x] `webapp/llm.py` attaches `cache_control: ephemeral` to the last tool_result each turn
- [x] `webapp/llm.py` logs `resp.usage` per turn at INFO
- [x] `tests/test_llm_trim.py` passes (15 cases, 8+ required)
- [x] `tests/test_api.py::TestApiEnvelopeCompleteness` passes (4 tests guarding the api-layer full-schema contract)
- [x] `tests/test_webapp_smoke.py::TestWebappTrimBoundary` passes (4 tests guarding the webapp-boundary trim contract)
- [x] `tests/test_webapp_smoke.py::TestCacheControlWiring` passes (2 new tests)
- [x] `tests/test_token_audit.py` runs green with `ANTHROPIC_API_KEY` set and produces `output/token_audit.json`
- [x] `output/token_audit.json` shows every *executed* tool with `pct_saved > 0` and `after <= before`
  (5 tools skipped cleanly due to empty DB tables; 3 executed, all saving ≥ 13%)
- [x] `.claude/PRPs/reports/token-burn-reduction-report.md` contains real numbers
- [x] `ruff check` + `ruff format --check` clean
- [x] Full `pytest` suite green (5 token-audit tests skip as expected)
- [ ] Manual 3-turn webapp run shows cache reads on turn 2+ — pending live session

## Next Steps

1. **Live webapp session**: run `chainlit run webapp/app.py -w`, issue three
   consecutive `query_latest` calls on UK, paste the usage log lines under
   the "End-to-end turn example" section above.
2. **Backfill live measurements**: once `competitive_snapshots` has rows for
   UK, re-run `pytest tests/test_token_audit.py` to replace the synthetic
   `query_latest_synth20` measurement with real numbers for the 4 `SELECT
   cs.*` tools.
3. **Phase 3 work** (next in the webapp PRD): live Keepa fetch confirmation
   UX. Both trim and cache savings apply there automatically; no changes
   to Phase 3 tools required.
