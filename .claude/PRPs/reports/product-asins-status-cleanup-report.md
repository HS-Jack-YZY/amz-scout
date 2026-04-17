# Implementation Report: product_asins.status Cleanup (v5 migration)

## Summary

实现了 `product_asins.status` 的 Path B 最小补丁：(1) SQLite schema v5 migration 删除僵尸状态 `unavailable`，CHECK 收紧从 5 值到 4 值；(2) `_resolve_asin` 在 ASIN pass-through 和 DB registry hit 两条路径都加 status gate，遇到 `wrong_product`/`not_listed` 时 raise 结构化 `ValueError`；(3) SQL 查询过滤门 `load_products_from_db` 扩展为排除两个 bad status；(4) 文档化 4 状态语义 + mermaid 状态图。修复了 query lifecycle matrix #10 的 silent failure 问题。

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small-Medium | Small-Medium |
| Estimated Files | 5 | 6（含 PRD backlog）|
| New tests | 9 (4 migration + 5 gate) | 9 |
| Updated tests | 2 (version asserts) | 2 |
| Total line delta | — | +350 / -8 |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | 升 SCHEMA_VERSION=5 | Complete | 单行修改 |
| 2 | _migrate 加 v5 块 | Complete | RENAME+CREATE+INSERT+DROP 标准模式 |
| 3 | 同步 _SCHEMA_SQL（fresh DB）| Complete | 含 inline status 注释 + schema_migrations v5 INSERT |
| 4 | SQL 查询过滤门扩展 | Complete | 两处 != → NOT IN |
| 5 | _resolve_asin status gate | Complete | 两条路径（DB hit + ASIN pass-through）均加 gate |
| 6 | TestStatusMigrationV5 测试 | Complete | 4 tests：reject_unavailable / accept_four / idempotent / preserves_rows |
| 7 | 更新 schema version 断言 | Complete | 两处 4→5 |
| 8 | TestResolveAsinStatusGate 测试 | Complete | 5 tests 全绿 |
| 9 | DEVELOPER.md ASIN Status Semantics | Complete | 4 值表 + query gate + mermaid 图 + single entry 说明 |
| 10 | PRD backlog open questions | Complete | 3 条 Phase 3 open questions |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff) | Pass | 修改文件 0 violations（初次有 1 个 I001 已修）|
| Unit Tests | Pass | 267 passed, 7 skipped, 0 failed |
| Build (Python syntax) | Pass | `ast.parse` 通过 |
| Integration | Pass | end-to-end `query_trends` envelope 测试通过 |
| Manual sanity | Pass | SCHEMA_VERSION=5 / 5 migration rows / CHECK 拒绝 `unavailable` |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/amz_scout/db.py` | UPDATED | +91 / -6 |
| `src/amz_scout/api.py` | UPDATED | +38 / -0 |
| `tests/test_db.py` | UPDATED | +79 / -2 |
| `tests/test_api.py` | UPDATED | +84 / -0 |
| `docs/DEVELOPER.md` | UPDATED | +47 / -0 |
| `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` | UPDATED | +11 / -0 |

Total: 6 files, +350 / -8 lines

## Deviations from Plan

1. **测试 ASIN 命名**：plan 示例用 `B0NOTLISTED` / `B0WRONGGPRD` / `B0HEALTHYY1` — 实际 `_ASIN_RE` 是 10 字符限制，这些是 11 字符。改为 `B0DEADXXX1` / `B0WRONG001` / `B0GOOD0001`。根因：plan 作者笔误，status gate 只在 `_ASIN_RE.match` 内触发。
2. **ruff import 排序**：Task 6 的 `test_v5_preserves_existing_rows` 按 plan 先 `from` 后 `import` 违反 isort 规则；改为 `import amz_scout.db as db_mod` 在前。
3. **DEVELOPER.md 图标字符**：plan 原文含 unicode check/cross 符号，正文改为英文 `match`/`mismatch` 避免渲染问题。

## Issues Encountered

- **Fact-forcing gate 拦截多次 Edit**：gate 要求在 edit 每个文件前出示 facts（importers/affected functions/data shape），导致部分并行 edit 被单边拒绝。解决：逐次重试，第二次即通过。
- **plan 测试 ASIN 长度错误**：TestResolveAsinStatusGate 首次跑 4/5 失败，因 11 字符 ASIN 被 `_ASIN_RE` 静默跳过。修正为 10 字符后全绿。

## Tests Written

| Test File | Tests Added | Coverage |
|---|---|---|
| `tests/test_db.py::TestStatusMigrationV5` | 4 | v5 CHECK enforcement + idempotency + v4→v5 upgrade 行保留 |
| `tests/test_api.py::TestResolveAsinStatusGate` | 5 | not_listed / wrong_product raise + verified pass + load_products 过滤 + end-to-end envelope 失败 |

## Next Steps

- [ ] Code review via `/code-review`
- [ ] Create PR via `/prp-commit` + `/prp-pr`
- [ ] Phase 3 PRD 启动时回看 `Phase 3 Open Questions`（monitoring 列 / stale 时间戳 / transition enforcement）

---

*Generated: 2026-04-17*
*Plan source: council verdict + query-lifecycle-matrix audit*
*Branch: feat/claude-md-slim-phase1*
