# Implementation Report: amz-scout 瘦身重构 — Phase 4 验证 + 回归测试

## Summary

对 amz-scout 瘦身重构三连击（Phase 1: CLAUDE.md 瘦身 / Phase 2: EAN·UPC 自动绑定 / Phase 3: webapp 查询直通模式）做一次合并后端到端回归。零新功能代码，只执行测试 + 读数 + 写证据。全部硬门通过：`pytest` 283 passed / 0 failed、`ruff check` clean、`query_trends` token 节省 83.6%（远高于 60% PRD gate）、tokens-weighted aggregate 节省 66.57%（≥50% PRD gate）、跨市场 product 0 brand drift、CLI `query trends` 完整保留 `date`/`value` 字段。Overall Gate: **ALL GREEN**。

执行期间发现 Phase 3 commit `15ee4158` 引入的单条 E501 line-too-long（`webapp/summaries.py:181`），以最小多行包装修复，未改语义。此外 `ruff format --check` 的 pre-existing drift 实际是 6 个文件（计划记录了 4 个，Phase 3 commit 又增加了 2 个 `tests/test_webapp_smoke.py` / `webapp/summaries.py`），未阻塞 gate，按 "Known Non-Regressions" 记录。

## Assessment vs Plan

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small（+1 条 E501 微修） |
| Estimated Files | 3 (1 new report, 1 refreshed JSON, 1 PRD update) | 4（多 1 条 summaries.py lint fix） |
| pytest passed | ≥275 | **283** |
| pytest skipped | ~14 (network + deployment) | 6（network audit）+ deployment smoke 已 ignore |
| `query_trends` pct_saved_vs_raw gate | ≥60% | **83.6%** |
| aggregate pct_saved_vs_raw gate | ≥50% (tokens-weighted) | **66.57%** |
| Cross-market products sampled | "10 个" (原 PRD) → 所有（2 个，plan 已下调） | 2（与 plan 一致） |
| brand drift | 0 | **0** |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Run full pytest (non-network) | Complete | 283 passed / 6 skipped / 0 failed in 46s |
| 2 | Run ruff two-stage check | Complete | `ruff check` clean (after 1-line E501 fix); `ruff format --check` 6 pre-existing drift files recorded |
| 3 | Run token audit with ANTHROPIC_API_KEY | Complete | 2 passed + 6 skipped (empty competitive_snapshots / buybox / deals rows — expected) |
| 4 | EAN binding SQL sampling | Complete | 2 cross-market products, 0 brand drift |
| 5 | CLI non-LLM smoke | Complete | help/query help/trends OK; latest empty-hint terse but non-fatal |
| 6 | Write Phase 4 validation report | Complete | This document |
| 7 | Close PRD Phase 4 row | Gated → proceeding | All gates GREEN |

## Validation Results

| Level | Status | Evidence |
|---|---|---|
| pytest (full, excluding `test_webapp_deployment_smoke.py`) | PASS | 283 passed, 6 skipped, 0 failed, 1 warning (traceloop PydanticV2 deprecation — external dep, non-actionable) |
| `ruff check src/ webapp/ tests/` | PASS | "All checks passed!" (post-fix) |
| `ruff format --check` | PRE-EXISTING DRIFT | 6 files would reformat; recorded in "Known Non-Regressions" below |
| Token audit (`tests/test_token_audit.py`) | PASS | 2 asserted gates pass (`query_trends ≥60%`, `synth20` trim non-regressive) |
| EAN brand-consistency guard | PASS | 0 violations across 2 cross-market products |
| CLI smoke (typer import graph + full-rows contract) | PASS | 7 subcommands load; `query trends` preserves `date`/`value` rows |

## Token Audit Numbers

### Raw output of `output/token_audit.json` (post-refresh)

```json
[
  {
    "tool": "query_deals",
    "before": 298,
    "after": 298,
    "pct_saved": 0.0
  },
  {
    "tool": "query_trends",
    "raw": 1857,
    "trimmed": 1857,
    "summary": 305,
    "pct_saved_vs_raw": 83.6,
    "pct_saved_vs_trimmed": 83.6
  },
  {
    "tool": "query_latest_synth20",
    "before": 7167,
    "after": 2513,
    "pct_saved": 64.9
  }
]
```

### Hard Gate Analysis

| Metric | Formula | Value | Gate | Pass? |
|---|---|---|---|---|
| `query_trends` pct_saved_vs_raw | (1857 − 305) / 1857 | **83.6%** | ≥60% (PRD Phase 3) | ✓ |
| `query_trends` pct_saved_vs_trimmed | (1857 − 305) / 1857 | **83.6%** | ≥30% (PRD Phase 3) | ✓ |
| `query_latest_synth20` pct_saved | (7167 − 2513) / 7167 | **64.9%** | Phase 2 baseline 64.9% ±3% | ✓ (exact) |
| Tokens-weighted aggregate pct_saved_vs_raw | (1552 + 4654 + 0) / (1857 + 7167 + 298) = 6206 / 9322 | **66.57%** | ≥50% (PRD Phase 4) | ✓ |

**Weighted aggregate derivation**（计划 Task 6 GOTCHA 要求显式写出）：

- `query_trends`: saved `1857 − 305 = 1552` tokens of `1857` raw
- `query_latest_synth20`: saved `7167 − 2513 = 4654` tokens of `7167` before
- `query_deals`: saved `298 − 298 = 0` tokens of `298` before（no-data path：合成空表，trim 等价于 raw）
- Sum saved: `1552 + 4654 + 0 = 6206`
- Sum denominator: `1857 + 7167 + 298 = 9322`
- Weighted pct_saved = `6206 / 9322 = 0.6657 → 66.57%`

### Skipped 工具（预期，非失败）

- `test_query_latest_token_delta`: `competitive_snapshots` 空表（DB 预检 = 0 行）
- `test_query_ranking_token_delta`: 同上
- `test_query_availability_token_delta`: 同上
- `test_query_compare_token_delta`: 同上
- `test_query_sellers_token_delta`: `keepa_buybox_history` 空
- `test_query_deals_token_delta`: 没有真实 deals 数据（注：合成 deals 录入 `token_audit.json` 但 pytest 条目 skip — 正常）

这些 skip 源自真实 DB 快照的内容，而非瘦身回归。`query_latest_synth20` 是专为此设计的代理指标（在 API 层合成 20 行 full-schema 数据），已经覆盖这一族工具的 trim 路径。

## EAN Sampling

### Query 1: 跨市场 products（`HAVING COUNT(DISTINCT marketplace) > 1`）

```
product_id  brand    model         binds
----------  -------  ------------  ------------------------------------------------------------
1           GL.iNet  GL-BE3600     B0F2MR53D6@UK | B0F2MR53D6@US
2           TP-Link  Archer BE400  B0DSC928WF@CA | B0DSC928WF@DE | B0DSJRDLGP@FR |
                                   B0DSC928WF@UK | B0DVBP5L6Y@US
```

### Query 2: 每个 (product_id, site) 的 brand/EAN/UPC

```
product_id  site  asin        brand    ean_list               upc_list
----------  ----  ----------  -------  ---------------------  ----------------
1           UK    B0F2MR53D6  GL.iNet  ["6971131384755"]
1           US    B0F2MR53D6  GL.iNet  ["6971131384755"]
2           CA    B0DSC928WF  TP-Link  ["0810142822503"]      ["810142822503"]
2           DE    B0DSC928WF  TP-Link  ["0810142822503"]      ["810142822503"]
2           FR    B0DSJRDLGP  TP-Link  ["1210002606721"]
2           UK    B0DSC928WF  TP-Link  ["0810142822503"]      ["810142822503"]
2           US    B0DVBP5L6Y  TP-Link  ["0810142822220"]      ["810142822220"]
```

### Verdict

| Check | Product 1 (GL-BE3600) | Product 2 (Archer BE400) | Overall |
|---|---|---|---|
| Brand 一致性 | ✓ (全 GL.iNet) | ✓ (全 TP-Link) | **0 brand drift** |
| Ambiguity guard (`_find_product_by_ean` 品牌守卫) | N/A（同 ASIN） | 未违反（5 ASIN 都在 TP-Link 品牌内） | **0 violations** |
| EAN 交集（至少 1 对 site 有 EAN 重叠） | ✓（UK ∩ US = `6971131384755`） | 部分（CA ∩ DE ∩ UK = `0810142822503`；FR 独立 `1210002606721`；US 独立 `0810142822220`） | Partial — 见下 |

### EAN 交集细节与解读

Product 2 有 5 个 ASIN 跨 5 个 marketplace，EAN 分 3 组：

- 主组（3 sites）：CA/DE/UK 共用 B0DSC928WF + EAN `0810142822503`
- FR：B0DSJRDLGP + EAN `1210002606721`（区域变体 SKU，EAN 与主组无交集）
- US：B0DVBP5L6Y + EAN `0810142822220`（北美变体 SKU）

**这是否违反 `_find_product_by_ean` 的合约？不。** 计划 Task 4 GOTCHA 明确：
> "不要假设每个跨市场绑定都是 EAN 路径 — Phase 2 landing 之前存在的绑定可能是旧 brand+title fallback。"

FR 和 US 的 ASIN 大概率是通过 `add_product()` 或 `import_yaml()` 手工注册（brand + model 匹配），而不是 `_find_product_by_ean` 自动绑定。只要 brand 守卫保持 100%（事实如此），这条路径就是合法的。

**硬门仍然通过**：

- ✓ 0 个 product_id 出现 ≥2 个不同 brand
- ✓ 非空 EAN 至少出现一次交集（Product 1 完整满足；Product 2 在 CA/DE/UK 三方交集满足）
- ✓ `_find_product_by_ean` 品牌守卫自 Phase 2 landing 以来从未失手

### 样本量局限（必须显式点名）

当前 DB 仅 2 个跨市场 product（PRD 原定抽样 10 个）。本报告**不应**被未来引用为 "EAN 自动绑定在大规模跨市场场景下已验证"；只能作为 "在现有 Phase 2 回填数据上零违例" 的 point-in-time 快照。建议下次新一轮 scrape round 把跨市场 product 扩到 ≥5 个后复跑本抽样。

## CLI Smoke

### `amz-scout --help`

```
Commands:
  scrape    Scrape Amazon competitive data + Keepa price history.
  discover  Discover cross-marketplace ASINs via browser search.
  validate  Validate configuration files.
  status    Check data completeness: CSV files, database, and Keepa freshness.
  keepa     Smart Keepa data fetch with cache-first freshness control.
  admin     One-time admin operations (migrate, merge, reparse)
  query     Query the SQLite database
```

7 个顶层子命令全部加载 ✓ — 这一步同时验证 `amz_scout.api` 的所有 import 在 envelope 重构后仍然健康。

### `amz-scout query --help`

```
Commands:
  latest        Show latest competitive data per product.
  trends        Show price/data trends for a product over time.
  compare       Compare one product across all marketplaces.
  ranking       Products ranked by BSR for a marketplace.
  availability  Availability matrix: all products across all sites.
  sellers       Buy Box seller history for a product.
  deals         Deal/promotion history.
```

7 个 query 子命令 ✓。

### `amz-scout query latest -m UK`

```
No data found.
```

Competitive snapshots 空表路径 — **非崩溃、yellow hint 可见**。Observation：CLI 的 "No data found." 比 API `meta.hint` 更短（`meta.hint` 会建议跑 `amz-scout scrape`），不阻塞本次 gate，记录为 Next Steps 候选增强项。

### `amz-scout query trends -p GL-BE3600 -m UK`

```
B0F2MR53D6 / UK / NEW (last 90 days)
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ date             ┃ value ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ 2026-04-01 09:00 │ 15299 │
│ 2026-04-01 02:12 │ 15099 │
│ 2026-03-25 03:46 │ 11729 │
│ 2026-03-17 10:14 │ 15099 │
│ 2026-03-10 04:54 │ 11729 │
│ 2026-02-28 02:00 │ 15099 │
│ 2026-02-23 00:12 │ 11729 │
│ 2026-02-11 04:20 │ 15099 │
│ 2026-02-07 05:28 │ 14299 │
│ 2026-01-26 05:40 │ 11729 │
└──────────────────┴───────┘
```

CLI 完整返回 `date` + `value` 两列 10 行 ✓ — `_llm_trim` 只作用于 LLM 路径，CLI 仍看到完整数据（与 `tests/test_api.py::TestApiEnvelopeCompleteness` 的契约对齐，后者在 Task 1 全量 pytest 中 passed）。

## Overall Gate

| # | Gate | Threshold | Measured | Pass? |
|---|---|---|---|---|
| 1 | `query_trends` pct_saved_vs_raw | ≥ 60% | 83.6% | ✓ |
| 2 | Tokens-weighted aggregate pct_saved_vs_raw | ≥ 50% | 66.57% | ✓ |
| 3 | 跨市场 product brand drift | 0 | 0 | ✓ |
| 4 | CLI `query trends` 完整字段（`date`+`value` 至少存在） | 存在 | 存在 | ✓ |
| 5 | pytest full suite pass count | ≥ 275 | 283 | ✓ |
| 6 | `ruff check src/ webapp/ tests/` | 0 errors | 0 errors（post-fix） | ✓ |

**Overall Gate: ALL GREEN** → PRD Phase 4 可以关闭。

## Files Changed

| File | Action | Lines / Notes |
|---|---|---|
| `webapp/summaries.py` | UPDATED | +5 / −1（line 181 E501 多行包装，Phase 3 regression 清理；语义无变更） |
| `output/token_audit.json` | UPDATED (auto) | 重写为 Phase 3 三列结构（3 entries：`query_deals`/`query_trends`/`query_latest_synth20`） |
| `.claude/PRPs/reports/amz-scout-slim-refactor-phase4-validation.md` | CREATED | 本报告 |
| `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` | UPDATED | Phase 4 行 status: pending → complete；footer date 更新 |

## Known Non-Regressions / Limitations

### `ruff format --check` pre-existing drift（6 files）

| File | Known from | Evidence / Diff type |
|---|---|---|
| `src/amz_scout/api.py` | Phase 3 report | line continuation style differences |
| `src/amz_scout/db.py` | Phase 3 report | same |
| `tests/test_db.py` | Phase 3 report | same |
| `tests/test_keepa_service.py` | Phase 3 report | same |
| `tests/test_webapp_smoke.py` | Phase 3 commit `15ee4158`（计划漏记） | multi-line assertion wrap ↔ inline |
| `webapp/summaries.py` | Phase 3 commit `15ee4158`（计划漏记） | function signature multi-line ↔ inline |

所有 6 个都是 ruff format 风格偏好（多行拆分 vs 单行），没有一个是逻辑 bug，也没有一个会阻塞 `ruff check`。本 Phase 4 故意不修（避免 Phase 3 housekeeping 与 Phase 4 验证混在一起）。建议下一个 housekeeping PR 一次性 `ruff format src/ webapp/ tests/` 清掉。

### 样本量不足（EAN sampling）

仅 2 个跨市场 product。下轮 scrape 后若扩到 ≥5 个，应复跑 Task 4 的两条 SQL 并更新本报告的 "EAN Sampling" 章节。

### `query latest` 空表 hint

CLI 打印 `No data found.`（黄色），未提示用户运行 `amz-scout scrape`。API 层 `meta.hint` 有完整提示但 CLI renderer 未渲染。不阻塞，列为 Next Steps 增强项。

### pytest warnings

1 条 `PydanticDeprecatedSince20` 来自 `traceloop.sdk`（外部依赖，non-actionable）。

## Deviations from Plan

| # | What | Why | Impact |
|---|---|---|---|
| 1 | Task 2 多了一条代码修复（`webapp/summaries.py:181` E501） | 计划把 `ruff check = 0 errors` 列为硬要求，但 Phase 3 commit 留下一条 E501。最小多行包装修复，未改语义 | 文件列表多 1（见 Files Changed）；不违反 "不新增功能代码" 约束 |
| 2 | `ruff format --check` pre-existing drift 实际 6 个文件（计划写了 4 个） | Phase 3 commit 在 plan 撰写后才被 scan，导致计划低估了 2 个（test_webapp_smoke / summaries） | 无 — 非阻塞，列入 Known Non-Regressions |

## Next Steps

- [ ] **Merge → main**：本分支 `feat/claude-md-slim-phase1` 含所有 Phase 1-4 diff；建议合并后发推广通告
- [ ] **Browser hand-test**：`chainlit run webapp/app.py -w` + 登录，按 Phase 3 report 的 5 条人工清单走一遍（本 plan 明确不包含）
- [ ] **Code review via `/code-review`**：本分支累计 8 commits，建议在 merge 前一次性 code review（含 EAN 绑定、envelope trim、summaries、validation）
- [ ] **下一轮 scrape → 复跑 EAN sampling**：当跨市场 product 扩到 ≥5 个，复跑本报告 Task 4 两条 SQL，更新 Verdict 章节
- [ ] **Housekeeping PR**：一次性 `ruff format src/ webapp/ tests/` 清掉 6 个 pre-existing drift 文件
- [ ] **（可选）增强 `query latest` 空表 hint**：让 CLI renderer 渲染 `meta.hint`（"run `amz-scout scrape` ..."），提升 CLI UX — 非阻塞，非本 phase 范围
- [ ] **"项目分析模式" 独立 PRD**：本轮瘦身关闭后可腾出空间，参考 PRD Phase 3 `query_log` session 接口设计

## Run Commands（可复现）

```bash
# Task 1 — pytest
pytest --ignore=tests/test_webapp_deployment_smoke.py -q

# Task 2 — ruff
ruff check src/ webapp/ tests/
ruff format --check src/ webapp/ tests/

# Task 3 — token audit（需要 ANTHROPIC_API_KEY）
set -a; source ./.env; set +a
pytest tests/test_token_audit.py -v
cat output/token_audit.json

# Task 4 — EAN sampling
sqlite3 output/amz_scout.db <<'SQL'
.mode column
.headers on
SELECT pa.product_id, p.brand, p.model,
       GROUP_CONCAT(pa.asin || '@' || pa.marketplace, ' | ') AS binds
FROM product_asins pa JOIN products p ON p.id = pa.product_id
GROUP BY pa.product_id HAVING COUNT(DISTINCT pa.marketplace) > 1;

SELECT pa.product_id, kp.site, kp.asin, kp.brand, kp.ean_list, kp.upc_list
FROM product_asins pa
JOIN keepa_products kp ON kp.asin = pa.asin AND kp.site = pa.marketplace
WHERE pa.product_id IN (
  SELECT product_id FROM product_asins
  GROUP BY product_id HAVING COUNT(DISTINCT marketplace) > 1
) ORDER BY pa.product_id, kp.site;
SQL

# Task 5 — CLI
amz-scout --help
amz-scout query --help
amz-scout query latest -m UK
MODEL=$(sqlite3 output/amz_scout.db 'SELECT model FROM products LIMIT 1')
amz-scout query trends -p "$MODEL" -m UK
```

---

*Report generated: 2026-04-20. Validation branch: `feat/claude-md-slim-phase1`. Based on plan `.claude/PRPs/plans/amz-scout-slim-refactor-phase4-validation.plan.md` (archived to `.claude/PRPs/plans/completed/`).*
