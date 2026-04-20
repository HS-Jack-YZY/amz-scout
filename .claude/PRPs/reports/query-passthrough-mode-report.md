# Implementation Report: 查询直通模式（Query Pass-Through Mode）

## Summary

Phase 3 收尾 row-emitting 工具的 LLM 输出成本：把「返回 trimmed rows 给 LLM 解读」改成「返回结构化摘要给 LLM + 把完整数据作为 Excel 附件给用户」。LLM 只做 NL → API 翻译，不再逐条读记录；用户通过 `cl.File` 下载全量 DB 字段。`query_log` 数据结构写入 `cl.user_session`，为未来「项目分析模式」预留接口。

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Estimated Files | 5（+1 new） | 6 touched (1 new, 5 modified) |
| Estimated Lines | ~400 added / ~80 removed | ~420 added / ~180 removed (含旧 TrimBoundary 类删除) |
| Estimated Time | ~4 hours | ~同量级（单次 session） |
| TOOL_SCHEMAS compression | ≤5,500 chars | **4,458 chars** (Phase 2 was ~6,500) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Create `webapp/summaries.py` | Complete | `_rows_to_xlsx_bytes` / `_safe_filename` / `_build_summary` / `_attach_file_to_session` / `_log_query` / `summarize_for_llm` decorator |
| 2 | Refactor `webapp/tools.py` | Complete | 7 row-emitting wrappers use `summarize_for_llm`; 9 schema docstrings compressed; `ApiResponse` return types applied |
| 3 | Update `webapp/app.py` | Complete | `on_chat_start` seeds `query_log` + `pending_files`; `on_message` drains & clears on both success and exception paths |
| 4 | Append Key Behavior #14 to CLAUDE.md | Complete | CLAUDE.md 5,805 → 6,279 chars (budget 10,000) |
| 5 | Rework `tests/test_webapp_smoke.py` | Complete | Removed 3 row-shape `TestWebappTrimBoundary` tests; new `TestQueryPassthrough` (6 tests) |
| 6 | Extend `tests/test_token_audit.py` | Complete | `_envelope_summary` helper, `_record_phase_metrics`, Phase 3 summary metric on `query_trends` (60% PRD gate) + `query_latest` (non-regressive) |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff check) | Pass | `webapp/` + modified test files clean. Pre-existing format drift in `src/amz_scout/api.py`, `db.py`, `test_db.py`, `test_keepa_service.py` not touched (out of Phase 3 scope) |
| Unit Tests | Pass | 39 tests in webapp smoke + llm_trim + CLAUDE.md size; 6 new `TestQueryPassthrough` cases |
| Full Suite (excluding network) | Pass | 277 passed + 2 skipped (deployment smoke needs live env); zero regressions in api/db/keepa tests |
| TOOL_SCHEMAS size regression guard | Pass | 4,458 chars ≤ 6,000 budget |
| CLAUDE.md size guard | Pass | 6,279 chars ≤ 10,000 budget |
| Token audit (manual, needs ANTHROPIC_API_KEY) | Not run in this session | Requires `ANTHROPIC_API_KEY` + populated `output/amz_scout.db` |
| Manual browser validation | Deferred | Requires `chainlit run webapp/app.py -w` + login — hand-test before merge |

## Files Changed

| File | Action | Approximate Lines |
|---|---|---|
| `webapp/summaries.py` | CREATE | +177 |
| `webapp/tools.py` | UPDATE | +138 / -115 (schema compressed, decorators swapped, return types → `ApiResponse`) |
| `webapp/app.py` | UPDATE | +18 / -2 |
| `CLAUDE.md` | UPDATE | +1 new Key Behavior line |
| `tests/test_webapp_smoke.py` | UPDATE | +225 / -172 (TrimBoundary class replaced with TestQueryPassthrough) |
| `tests/test_token_audit.py` | UPDATE | +100 / -15 (new helpers + summary metric extension) |

## Deviations from Plan

| # | What | Why |
|---|---|---|
| 1 | Added `assert ws is not None` to `_rows_to_xlsx_bytes` | `openpyxl.Workbook.active` is typed `Worksheet | None`; Pyright flagged it. A fresh `Workbook()` always has an active sheet, so the assert is purely for the type-checker and documents the invariant |
| 2 | Changed `_step_*` return types to `ApiResponse` instead of plain `dict` | Pyright rejects `TypedDict → dict` assignability. Importing `ApiResponse` from `amz_scout.api` gives a precise return type without suppressing diagnostics |
| 3 | `_envelope_summary` helper re-implements `_build_summary` locally inside `tests/test_token_audit.py` rather than importing `webapp.summaries` | Test harness already has no `webapp.*` imports; keeping it pure also avoids the chainlit transitive import when this network-gated test runs outside a Chainlit context |
| 4 | Deleted the entire `TestWebappTrimBoundary` class rather than keeping one test renamed | The retained failure-passthrough assertion (`result is failure`) is equivalent to `test_failure_envelope_passes_through` in `TestQueryPassthrough`; keeping it in a separate class added no signal and split the Phase 3 contract across two classes |
| 5 | `_count_tokens_for_tool_result` signature widened to `Mapping[str, Any]` | Pyright rejects `ApiResponse → dict`; using `Mapping` stays permissive without hiding real bugs |

## Issues Encountered

| # | Problem | Resolution |
|---|---|---|
| 1 | Pyright diagnostics cascade on `TypedDict → dict` in `webapp/tools.py` | Imported `ApiResponse` and annotated all 9 `_step_*` wrappers + `dispatch_tool` with it |
| 2 | `Workbook.active` typed as `Optional[Worksheet]` | Added `assert ws is not None` with a one-line comment explaining the invariant |
| 3 | Pre-existing format drift in `api.py`, `db.py`, `test_db.py`, `test_keepa_service.py` surfaced on full-tree `ruff format --check` | Out of Phase 3 scope; documented here so it's not mistaken for a Phase 3 regression. A future housekeeping pass can batch-format them |
| 4 | `cl.File(...)` would crash inside pytest (no `context.session`) | Task 5's `_FakeFile` dataclass stub monkey-patches `cl.File` so `_attach_file_to_session` can observe the attach in tests — as flagged in Task 1 GOTCHA #2 |

## Tests Written

| Test File | Tests Added | Coverage |
|---|---|---|
| `tests/test_webapp_smoke.py::TestQueryPassthrough` | 6 | summary envelope shape; full rows in xlsx (guards against trim-leak); query_log accumulation; failure passthrough (no xlsx / no log); query_deals omits date_range; TOOL_SCHEMAS size regression |
| `tests/test_token_audit.py` | 2 (extended) | `test_query_trends_token_delta` + `test_query_latest_token_delta` now emit three-phase metrics (raw / trimmed / summary); primary 60% PRD gate on `query_trends`, non-regressive on `query_latest` |

## Acceptance Criteria Status

- [x] All 6 tasks completed
- [x] All unit validation commands pass (39/39)
- [x] `webapp/summaries.py` created with unit test coverage
- [x] `webapp/tools.py` 7 row-emitting wrappers use `summarize_for_llm`
- [x] `webapp/app.py` seeds + drains session state (both paths)
- [x] CLAUDE.md Key Behavior #14 appended
- [x] `TestQueryPassthrough` 6 tests green
- [x] `tests/test_token_audit.py` extended with Phase 3 summary metrics
- [ ] Token savings ≥60% (single tool_result) — **needs manual run with `ANTHROPIC_API_KEY`**
- [x] Zero regressions (277 full-suite tests pass)
- [ ] `cl.File` downloadable in browser — **needs manual browser validation**

## Next Steps

- [ ] Hand-test in browser: `chainlit run webapp/app.py -w` → verify
  1. "查 Slate 7 UK 过去 90 天价格走势" → `count` + `date_range` + `cl.File` card visible, xlsx opens
  2. xlsx contains `title`, `url`, `id` fields (not LLM-safe subset)
  3. Three consecutive queries → three separate xlsx attachments
  4. "数据多久没更新了" → `check_freshness` still returns matrix (unchanged shape)
  5. Failure trigger → LLM sees `error`, no xlsx shipped
- [ ] Run token audit locally with `ANTHROPIC_API_KEY`:
  `ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY pytest tests/test_token_audit.py -m network -v`
  Verify `pct_saved_vs_raw >= 60%` on `query_trends`; capture `output/token_audit.json` snapshot
- [ ] `/code-review` on Phase 3 diff before committing
- [ ] `/prp-pr` to create PR against `feat/claude-md-slim-phase1`
