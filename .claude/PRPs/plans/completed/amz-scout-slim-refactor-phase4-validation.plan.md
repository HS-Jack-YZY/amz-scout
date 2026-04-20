# Plan: Phase 4 — 验证 + 回归测试（amz-scout 瘦身重构）

## Summary

这是 amz-scout 瘦身重构 PRD 的最后一个 phase：对前三个 phase（CLAUDE.md 瘦身、EAN/UPC 自动绑定、查询直通模式）做端到端验证，确认零功能回归、token 节省达标（`query_trends` `pct_saved_vs_raw` ≥ 60%，整体 ≥ 50%）、EAN 绑定无误匹配、CLI 输出完整保留。产出物是验证报告 + 刷新后的 `output/token_audit.json`。不新增功能代码。

## User Story

As amz-scout owner (Jack),
I want 一份端到端验证报告证明瘦身三管齐下没有破坏任何功能、token 消耗达到 PRD 承诺的节省幅度、EAN 自动绑定没有把不同产品误合并、CLI 用户看到的行数据仍然完整,
so that 可以安心关闭这轮重构 PRD、把 webapp 推广给另外 5 名 PM、并为后续「项目分析模式」的独立 PRD 腾出空间.

## Problem → Solution

三个 phase 已独立完成并各自有单元测试，但还缺三件事：
1. **跨 phase 全量测试**：Phase 1/2/3 的 diff 已分别在自己的 PR 上跑过测试，但没有一次合并后的全量 `pytest` 验证。
2. **live token 度量**：`tests/test_token_audit.py` 的 Phase 3 三段式 metric（raw / trimmed / summary）从未在有 `ANTHROPIC_API_KEY` 的环境下跑过，`output/token_audit.json` 还是 Phase 2 的两列旧结构。
3. **EAN 绑定的实战抽检 + CLI 回归**：`_find_product_by_ean` 有单元测试但未对真实 DB 里的跨市场产品抽样；CLI 的 `query` 子命令在 envelope trim 搬出 `api.py` 后没有再走一遍，需要确认用户看到 `title` / `url` / `fulfillment` 这些 LLM-safe 白名单之外的字段。

→ Phase 4 只做**验证**，不改生产代码；产出 `reports/amz-scout-slim-refactor-phase4-validation.md`、刷新 `output/token_audit.json`，如果所有门通过就把 PRD Phase 4 状态改成 `complete`。

## Metadata

- **Complexity**: Small（验证型 plan，无新 feature 代码）
- **Source PRD**: `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md`
- **PRD Phase**: Phase 4 — 验证 + 回归测试
- **Estimated Files**: 3（1 new report、1 refreshed JSON、1 PRD status update）

---

## UX Design

### Before
```
┌──────────────────────────────────────────────────────┐
│ CLAUDE.md 6,279 chars OK — 测试已过                   │
│ EAN binding unit tests OK — 未做真实数据抽样          │
│ Query passthrough smoke tests OK — token audit 挂空档 │
│   output/token_audit.json:                           │
│     [{tool, before, after, pct_saved}]  <- Phase 2 型 │
│   缺：summary 列、≥60% 门、跨 phase 聚合              │
│ CLI query latest/trends ? — 改动没在 CLI 侧再过一次   │
└──────────────────────────────────────────────────────┘
```

### After
```
┌───────────────────────────────────────────────────────────────────────┐
│ .claude/PRPs/reports/amz-scout-slim-refactor-phase4-validation.md    │
│   - ≥275/291 pytest 通过记录（含网络跳过说明）                        │
│   - query_trends pct_saved_vs_raw: X% ≥ 60% OK                       │
│   - query_latest pct_saved_vs_raw: Y% （non-regressive）OK           │
│   - 聚合节省 Z% ≥ 50% OK                                             │
│   - EAN 抽样：K 个跨市场产品，品牌一致率 100%                         │
│   - CLI query latest 输出含 title/url/fulfillment OK                  │
│   - Overall gate: ALL GREEN -> Phase 4 complete                      │
│ output/token_audit.json:                                             │
│   [{tool, raw, trimmed, summary, pct_saved_vs_raw, ...}] <- Phase 3 型│
│ PRD Phase 4 row status: pending -> complete                          │
└───────────────────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| PR merge gate | 各 phase 单独通过 | 全量 `pytest` 一次通过 | 无 code 改动，仅确认合并后稳定 |
| `output/token_audit.json` | 2 列旧结构（Phase 2） | 3 列新结构（Phase 3） | 由 `test_query_*_token_delta` 重写 |
| PRD 表状态 | Phase 4 = pending | Phase 4 = complete | 只改 PRD markdown |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` | 185-245 | Phase 4 scope + success signal（"≥50%"）与 Phase 3 60% gate 的关系 |
| P0 | `tests/test_token_audit.py` | 1-478 | 所有测试已写好；Phase 4 只负责跑 + 读结果 |
| P0 | `.claude/PRPs/reports/query-passthrough-mode-report.md` | 72-104 | 列明了待手工验证的 3 条（token audit、browser hand-test、code-review），本 plan 专注 token audit |
| P1 | `src/amz_scout/db.py` | 680-820 | `_find_product_by_ean` 的品牌守卫 + ambiguity guard 逻辑 — 抽样 SQL 要复用同一假设 |
| P1 | `webapp/summaries.py` | 107-147 | `_build_summary` 字段集合 — 对账 `test_token_audit.py::_envelope_summary` 是否已同步 |
| P2 | `.claude/PRPs/reports/token-burn-reduction-report.md` | 61-90 | 历史 token 度量，作为 Phase 4 数据的 sanity baseline |
| P2 | `src/amz_scout/cli.py` | 870-1021 | CLI `query` 子命令入口，决定手工 smoke 的命令行 |
| P2 | `CLAUDE.md` | 1-103 | 瘦身后现状快照，验证行数与 `tests/test_claude_md_size.py` 断言一致 |

## External Documentation

无外部库研究需要 — 只使用项目已建立的 pytest / ruff / sqlite3 / typer / anthropic 工具链。Anthropic `count_tokens` 用法已在 `test_token_audit.py:132-137` 固化。

---

## Patterns to Mirror

### PYTEST_NETWORK_GATE
```python
# SOURCE: tests/test_token_audit.py:25-32
@pytest.fixture(scope="module")
def anthropic_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; skipping token audit")
    anthropic = pytest.importorskip("anthropic")
    return anthropic.Anthropic()
```
→ Phase 4 手工跑时通过 `set -a; source .env; set +a` 或 `export ANTHROPIC_API_KEY=...` 激活。不改 fixture。

### THREE_PHASE_METRIC
```python
# SOURCE: tests/test_token_audit.py:160-182
def _record_phase_metrics(tool: str, raw: int, trimmed: int, summary: int) -> dict:
    assert trimmed <= raw, f"{tool}: trim increased tokens ({raw} -> {trimmed})"
    assert summary <= raw, f"{tool}: summary increased tokens vs raw ({raw} -> {summary})"
    pct_raw = 0.0 if raw == 0 else round((raw - summary) / raw * 100, 1)
    pct_trimmed = 0.0 if trimmed == 0 else round((trimmed - summary) / trimmed * 100, 1)
    return {"tool": tool, "raw": raw, "trimmed": trimmed, "summary": summary,
            "pct_saved_vs_raw": pct_raw, "pct_saved_vs_trimmed": pct_trimmed}
```
→ 报告里 aggregate 节省用同一口径（`pct_saved_vs_raw` 作为 headline，`pct_saved_vs_trimmed` 作为 Phase 3 相对 Phase 2 的增益）。

### EAN_AMBIGUITY_GUARD
```sql
-- SOURCE: src/amz_scout/db.py:709-726
SELECT DISTINCT pa.product_id
FROM keepa_products kp
JOIN product_asins pa ON pa.asin = kp.asin AND pa.marketplace = kp.site
WHERE kp.asin != ?
  AND (EXISTS (SELECT 1 FROM json_each(kp.ean_list) WHERE value IN (...))
    OR EXISTS (SELECT 1 FROM json_each(kp.upc_list) WHERE value IN (...)))
  AND kp.brand = ?   -- brand guard：跨品牌不绑定
```
→ 抽样 SQL 复用同一 JOIN + brand 守卫，验证每个 ean→product_id 映射在品牌维度上自洽。

### CLI_HAS_FULL_ROWS_CONTRACT
```python
# SOURCE: tests/test_api.py::TestApiEnvelopeCompleteness (reference only — Phase 2 已交付)
# Phase 4 的 CLI smoke 不是新测试，只是用 shell 跑一遍 CLI 命令并目视确认
# 输出含 `title` / `url` / `fulfillment` 这些 `_LLM_SAFE_COMPETITIVE_FIELDS` 之外的列。
```
→ `amz-scout query latest -m UK` 输出若只剩 LLM 白名单列即是回归；本 phase 不自动断言，只在报告里截图/贴输出。

### REPORT_TEMPLATE
```markdown
# SOURCE: .claude/PRPs/reports/query-passthrough-mode-report.md 整体结构
# Phase 4 报告复用同一 6-section 骨架：Summary / Assessment / Tasks / Validation / Files Changed / Next Steps
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `.claude/PRPs/reports/amz-scout-slim-refactor-phase4-validation.md` | CREATE | Phase 4 的唯一硬产出物：pytest 结果 / token 度量 / EAN 抽样 / CLI smoke / 整体 gate |
| `output/token_audit.json` | UPDATE (auto) | 由 `test_query_*_token_delta` 的 `_record` helper 自动 upsert 写入三列结构 |
| `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` | UPDATE | Phase 4 行 status：pending → complete（仅在所有 gate 绿后） |

## NOT Building

- **不新增测试**：`test_token_audit.py` 已覆盖三段式度量；`test_core_flows.py::TestAutoRegisterFromKeepa` 已覆盖 EAN 单测；`test_api.py::TestApiEnvelopeCompleteness` 已覆盖 CLI 完整 envelope 契约。Phase 4 只执行与读数。
- **不重新实现 browser hand-test**：Phase 3 report 已列明 `chainlit run webapp/app.py -w` 的 5 条人工清单；Phase 4 报告里可引用但不复制，也不在本 plan 的自动化范围内。
- **不改 CLAUDE.md**：行数已经 102 / 6,279 chars，符合 `tests/test_claude_md_size.py` 预算，保持现状。
- **不修补 pre-existing format drift**（`api.py` / `db.py` / `test_db.py` / `test_keepa_service.py`）：Phase 3 report 已标注是现存问题，本次 housekeeping 不合并到 Phase 4。
- **不做 CLI 自动化 diff**：CLI 只通过 1-2 条命令手工 smoke 并把输出贴进报告。

---

## Step-by-Step Tasks

### Task 1: 运行全量 pytest（非网络）
- **ACTION**: 在仓库根执行 `pytest --ignore=tests/test_webapp_deployment_smoke.py -q`，把 passed / skipped / failed 数字记到报告 Validation Results 表。
- **IMPLEMENT**: 单条 bash 命令；记录 `pytest` 退出码 + tail 20 行摘要。
- **MIRROR**: `.claude/PRPs/reports/query-passthrough-mode-report.md` 的 "Full Suite (excluding network)" 行（277 passed + 2 skipped）。Phase 4 的目标数应 ≥275（291 collected - 约 14 network / deployment skips）。
- **IMPORTS**: 无。
- **GOTCHA**: `python -m pytest` 在某些环境下会被 RTK / 代理捕获（Phase 2 report 记录过）。直接用 `pytest` 二进制最稳。若出现 "Pytest: No tests collected"，改用 `uv run pytest` 或指定路径 `pytest tests/`。
- **VALIDATE**: 退出码 0；如果有 failed 不得进入 Task 2，先退回 issue triage。

### Task 2: 运行 ruff 两段式回归
- **ACTION**: `ruff check src/ webapp/ tests/` + `ruff format --check src/ webapp/ tests/`。
- **IMPLEMENT**: 两条并行 bash 命令。
- **MIRROR**: Phase 3 report 的 "Static Analysis (ruff check) Pass"。
- **IMPORTS**: 无。
- **GOTCHA**: Phase 3 report 已记录 `src/amz_scout/api.py`、`db.py`、`test_db.py`、`test_keepa_service.py` 有 pre-existing format drift。报告里按"已知 pre-existing"标记，不当作 Phase 4 回归。
- **VALIDATE**: `ruff check` 必须 0 errors；`ruff format --check` 的 4 个 pre-existing 文件若再出现，记录在 "Issues Encountered"、不阻塞 gate。

### Task 3: 在有 `ANTHROPIC_API_KEY` 的环境下跑 token audit
- **ACTION**: `set -a; source .env; set +a; pytest tests/test_token_audit.py -v`，等 7 个 `test_query_*_token_delta` 产出 `output/token_audit.json`。
- **IMPLEMENT**: 一条 shell 命令序列；完成后 `cat output/token_audit.json` 把 JSON 贴进报告。
- **MIRROR**: Phase 2 `token-burn-reduction-report.md` 的 "Run commands" block。
- **IMPORTS**: `anthropic` 包已在 `pyproject.toml:27` 作为 `web` extra。
- **GOTCHA**:
  1. `competitive_snapshots` 现为 0 行（已查验 DB），`query_latest`/`ranking`/`availability`/`compare` 会 `pytest.skip` — 这是**预期行为**，不是失败。保留 `query_latest_synthetic_token_delta` 作为这一族工具的代理指标。
  2. `keepa_buybox_history` 同样可能空 → `query_sellers` 也可能 skip。
  3. `query_trends` 的 `pct_saved_vs_raw >= 60` 是硬断言（`test_token_audit.py:352`）；失败必须回 Phase 3 排查 preview 是否过大。
  4. `count_tokens` 是 free endpoint 但占 rate limit；harness `scope="module"` 只建一次 client，单次运行约 16 次 API 调用，可接受。
  5. `output/amz_scout.db` 必须存在（已确认 2,924,544 bytes），否则 fixture 会 skip。
- **VALIDATE**:
  - `output/token_audit.json` 每一行执行过的工具都有 `raw`/`trimmed`/`summary` 三列；
  - `query_trends` 的 `pct_saved_vs_raw >= 60`（来自 pytest 断言）；
  - `query_latest_synth20` 的 `pct_saved` 保持在 Phase 2 历史基线 64.9% ±3%（确认 trim 没回归）；
  - 整体（`query_trends` + `query_latest_synth20` + 任何非 skip 项）的加权均值 ≥ 50%（PRD Phase 4 success signal）。

### Task 4: EAN 绑定实战抽样
- **ACTION**: 直接跑 SQL 从 `output/amz_scout.db` 抽取所有跨市场 product_id（即 `product_asins` 里跨 ≥2 marketplace 的产品），逐一核对 `keepa_products.ean_list` 重叠与 `brand` 一致性。
- **IMPLEMENT**:
  ```bash
  sqlite3 output/amz_scout.db <<'SQL'
  -- 1) 找跨市场 product
  .mode column
  .headers on
  SELECT pa.product_id, p.brand, p.model,
         GROUP_CONCAT(pa.asin || '@' || pa.marketplace, ' | ') AS binds
  FROM product_asins pa JOIN products p ON p.id = pa.product_id
  GROUP BY pa.product_id HAVING COUNT(DISTINCT pa.marketplace) > 1;

  -- 2) 对每个跨市场 product，确认它们的 ean_list 有交集 + brand 一致
  SELECT pa.product_id, kp.site, kp.asin, kp.brand,
         kp.ean_list, kp.upc_list
  FROM product_asins pa
  JOIN keepa_products kp ON kp.asin = pa.asin AND kp.site = pa.marketplace
  WHERE pa.product_id IN (
    SELECT product_id FROM product_asins
    GROUP BY product_id HAVING COUNT(DISTINCT marketplace) > 1
  )
  ORDER BY pa.product_id, kp.site;
  SQL
  ```
- **MIRROR**: `src/amz_scout/db.py:689-737` `_find_product_by_ean` 的品牌守卫 + 歧义守卫。抽样的"合格"定义 = 同一 product_id 下，每个 `(asin, site)` 对应的 Keepa `brand` 都相同**且** `ean_list` 或 `upc_list` 至少有一个非空交集。
- **IMPORTS**: 无（纯 sqlite3 CLI）。
- **GOTCHA**:
  1. DB 目前只有 **2 个跨市场 product**（已预扫 `output/amz_scout.db`），达不到 PRD 原定的"抽样 10 个"。本 plan 把 Phase 4 抽样目标从 "10 个" 改为 **"所有跨市场产品（当前 2 个）"**，并在报告里标注样本不足的局限，推迟到后续有更多数据时复查。
  2. 不要假设每个跨市场绑定都是 EAN 路径 — Phase 2 landing 之前存在的绑定可能是旧 brand+title fallback。Phase 4 的检查**只要求 brand 一致 + 若 EAN 非空则交集非空**，不强制 EAN 必然触发。
  3. `ean_list` 是 JSON array 形式；直接文本比对会错过格式差异，必须用 `json_each()` 解包。
- **VALIDATE**:
  - 每个跨市场 product 的 `brand` 在所有 site 下一致（0 brand 漂移）；
  - 若 `ean_list` 字段非空（至少一侧），两侧 EAN 交集 ≥ 1（证明 `_find_product_by_ean` 的前提成立）；
  - 没有 product_id 同时出现两个不同 brand（等价于 `_find_product_by_ean` 的歧义守卫从未失手）。
  - 在报告中把 SQL 输出表格化粘贴。

### Task 5: CLI 非 LLM 路径 smoke
- **ACTION**: 顺序跑 `amz-scout --help`、`amz-scout query --help`、`amz-scout query latest -m UK`、`amz-scout query trends --help`，把输出贴进报告。
- **IMPLEMENT**:
  ```bash
  amz-scout --help
  amz-scout query --help
  amz-scout query latest -m UK    # 若 competitive_snapshots 空，用 query trends 代替
  amz-scout query trends -p "$(sqlite3 output/amz_scout.db 'SELECT model FROM products LIMIT 1')" -m UK
  ```
- **MIRROR**: Phase 2 `token-burn-reduction-report.md` 里提到 "CLI 侧 `fulfillment` 栏空掉"是 `_llm_trim` 误放 `api.py` 的症状。本 smoke 反向验证修复之后 CLI 仍看到这些字段。
- **IMPORTS**: 无。
- **GOTCHA**:
  1. `competitive_snapshots` 为空 → `query latest` 返回空表但不该报错；如空，在报告里记录"空表 + hint message 正常"，再用 `query trends` 做"有数据路径"smoke。
  2. Typer 输出依赖终端宽度；粘贴时保留原始换行。
  3. 不要运行 `scrape` / `discover` / 任何会触发浏览器或外部 API 的命令 — Phase 4 只验证只读 query。
- **VALIDATE**:
  - `--help` 输出列出 `scrape` / `discover` / `validate` / `status` / `admin` / `query` 等子命令（与 `cli.py:44` typer 结构一致）；
  - `query latest` 若有数据，输出至少含 `title`、`url`、`fulfillment` 三列中的任意一列（回归 Phase 2 post-review 修复）；若 0 行，看到 hint 提示跑 `amz-scout scrape`；
  - `query trends` 输出 `date` + `value` 等完整行，不被 `_llm_trim` 截断。

### Task 6: 撰写 Phase 4 validation 报告
- **ACTION**: 按 `reports/` 既有骨架写一份 `amz-scout-slim-refactor-phase4-validation.md`。
- **IMPLEMENT**: 新建 markdown，章节包括：Summary / Assessment vs Plan / Validation Results 表 / Token Audit Numbers / EAN Sampling / CLI Smoke / Overall Gate / Known Non-Regressions / Next Steps。
- **MIRROR**: `.claude/PRPs/reports/query-passthrough-mode-report.md` 和 `token-burn-reduction-report.md` 的 6-section 结构；把本 phase 特有的 "Overall Gate" 表放最后明确 pass/fail。
- **IMPORTS**: 无。
- **GOTCHA**:
  1. Aggregate `pct_saved` 计算口径必须与 `_record_phase_metrics` 一致 — 对 `query_trends` 取 `pct_saved_vs_raw`；对只有两列的旧工具（synth20）取 `pct_saved`；加权方式在报告里显式写出（tokens-weighted，不是 arithmetic mean）。
  2. 样本量不足（2 个跨市场 product）要显式点名，避免未来用这份报告当 "EAN 绑定已验证"论据时踩坑。
  3. "Overall Gate: GREEN" 的判定条件要逐条列出，便于下次用脚本自动判定。
- **VALIDATE**: 报告自检 checklist：
  - [ ] Validation Results 表 ≥ 4 行（pytest / ruff / token audit / EAN sampling / CLI smoke）
  - [ ] Token audit 贴了 `output/token_audit.json` 完整 JSON
  - [ ] EAN sampling 贴了 SQL + 输出
  - [ ] CLI smoke 贴了至少 2 条命令输出
  - [ ] Overall Gate 表列出 4 个硬门（60% trends / 50% aggregate / 0 brand drift / CLI 完整字段）

### Task 7: 关闭 PRD Phase 4 行（仅在 Overall Gate = GREEN 时）
- **ACTION**: 编辑 `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md` 的 Implementation Phases 表，把 Phase 4 行 Status 从 `pending` 改为 `complete`，PRP Plan 列填上本 plan 的相对路径。
- **IMPLEMENT**: 单次 `Edit` 替换表格第 4 行；不动其他 phase 状态。
- **MIRROR**: 与 Phase 1/2 行的已完成格式一致（"in-progress" 风格）。
- **IMPORTS**: 无。
- **GOTCHA**:
  1. PRD footer 日期（`*Status: DRAFT — ...*`）也要同步更新："Phase 4 closed YYYY-MM-DD"。
  2. 若 Task 3 的 60% trends 断言未达，**不能**进入 Task 7；回退到 Next Steps 标注失败原因并保持 `pending`。
- **VALIDATE**: `grep -n "Phase 4" .claude/PRPs/prds/amz-scout-slim-refactor.prd.md` 结果里 Status 列为 `complete`；PRD footer 日期同步更新。

---

## Testing Strategy

Phase 4 本身是 "验证 phase"，不产出新测试。断言由下列既有测试提供：

### 既有测试 → Phase 4 断言映射

| 断言维度 | 提供方 | 位置 |
|---|---|---|
| CLAUDE.md 尺寸 | `tests/test_claude_md_size.py` | 通过 = 压缩未反弹 |
| Forced ASIN discovery 指令已移除 | `tests/test_claude_md_size.py` | 通过 = Phase 1 行为固化 |
| EAN 跨市场绑定 | `tests/test_core_flows.py::TestAutoRegisterFromKeepa::test_ean_binds_cross_market` 等 4 cases | 通过 = Phase 2 逻辑稳定 |
| 跨品牌 EAN 不误绑 | 同上 `test_ean_no_cross_brand_match` | 通过 = 守卫未失 |
| webapp 摘要形状 | `tests/test_webapp_smoke.py::TestQueryPassthrough` | 11 cases 通过 |
| api envelope 完整性（CLI 路径） | `tests/test_api.py::TestApiEnvelopeCompleteness` | 通过 = CLI 看到 `title`/`url`/`sold_by`/`fulfillment` |
| cache_control 不累积 | `tests/test_webapp_smoke.py::TestCacheControlWiring` | 3 cases 通过 |
| token audit 三段式 | `tests/test_token_audit.py` | network 环境下 `query_trends` 60% 断言通过 |

### Edge Cases Checklist
- [x] competitive_snapshots 空表 → token audit 对应工具 skip（非失败）
- [x] keepa_buybox_history 空表 → query_sellers skip（非失败）
- [x] ANTHROPIC_API_KEY 未设 → network 测试整体 skip（非失败）
- [x] 跨市场 product 仅 2 个 → 样本不足但不阻塞（在报告显式点名）
- [x] query latest CLI 返回 0 行 → hint message 需可见

---

## Validation Commands

### Static Analysis
```bash
ruff check src/ webapp/ tests/
ruff format --check src/ webapp/ tests/
```
EXPECT: `ruff check` 0 issues；`ruff format --check` 若出现 `api.py` / `db.py` / `test_db.py` / `test_keepa_service.py` 的已知 drift，记录为 pre-existing、不阻塞。

### Unit + Integration Tests
```bash
pytest --ignore=tests/test_webapp_deployment_smoke.py -q
```
EXPECT: ≥275 passed, 剩余项为 network-skipped（`test_token_audit.py`）或明确标记的 deployment skip。

### Token Audit（需要 ANTHROPIC_API_KEY + 真实 DB）
```bash
set -a; source .env; set +a
pytest tests/test_token_audit.py -v
cat output/token_audit.json
```
EXPECT:
- `query_trends` 测试断言 `pct_saved_vs_raw >= 60` 通过；
- `pct_saved_vs_trimmed >= 30` 通过；
- `output/token_audit.json` 写入至少 3 行非 skip 的度量（`query_trends`、`query_latest_synth20`、`query_deals` 或其它非空工具）。

### EAN Sampling（DB 只读）
```bash
sqlite3 output/amz_scout.db <<'SQL'
.mode column
.headers on
SELECT pa.product_id, p.brand, p.model,
       GROUP_CONCAT(pa.asin || '@' || pa.marketplace, ' | ') AS binds
FROM product_asins pa JOIN products p ON p.id = pa.product_id
GROUP BY pa.product_id HAVING COUNT(DISTINCT pa.marketplace) > 1;

SELECT pa.product_id, kp.site, kp.asin, kp.brand,
       kp.ean_list, kp.upc_list
FROM product_asins pa
JOIN keepa_products kp ON kp.asin = pa.asin AND kp.site = pa.marketplace
WHERE pa.product_id IN (
  SELECT product_id FROM product_asins
  GROUP BY product_id HAVING COUNT(DISTINCT marketplace) > 1
)
ORDER BY pa.product_id, kp.site;
SQL
```
EXPECT:
- 每个 product_id 下所有 site 的 `brand` 一致；
- 若 `ean_list` 非空，两侧 EAN 交集非空；
- 没有一个 product_id 出现 ≥ 2 不同 brand。

### CLI Smoke（非 LLM 路径）
```bash
amz-scout --help
amz-scout query --help
amz-scout query latest -m UK
amz-scout query trends -p "$(sqlite3 output/amz_scout.db 'SELECT model FROM products LIMIT 1')" -m UK
```
EXPECT:
- `--help` 列出 `scrape`/`discover`/`validate`/`status`/`admin`/`query` 等子命令；
- `query latest` 若有数据含 `title`/`url`/`fulfillment` 任意一列；若空则显示 hint；
- `query trends` 输出完整行（`date` + `value` 至少存在）。

### Manual Validation Checklist
- [ ] pytest 全量通过，failed = 0
- [ ] ruff check 0 issues
- [ ] `output/token_audit.json` 刷新为 Phase 3 三列结构
- [ ] `query_trends` `pct_saved_vs_raw` ≥ 60%
- [ ] Aggregate（tokens-weighted）`pct_saved_vs_raw` ≥ 50%
- [ ] 跨市场 product 样本（当前 2 个）0 brand drift / 0 EAN 守卫失效
- [ ] CLI `query latest` / `query trends` 输出含 LLM-safe 白名单以外的字段
- [ ] 报告 Overall Gate 表 = ALL GREEN
- [ ] PRD Phase 4 行改为 `complete`（仅在上面全部通过时）

---

## Acceptance Criteria
- [ ] `pytest` 全量（ignore deployment smoke）通过
- [ ] `ruff check` clean；`ruff format --check` 无新漂移
- [ ] `tests/test_token_audit.py` network gate 下通过，`output/token_audit.json` 更新为 Phase 3 结构
- [ ] `query_trends` `pct_saved_vs_raw` ≥ 60%
- [ ] Aggregate `pct_saved_vs_raw` ≥ 50%（PRD Phase 4 success signal）
- [ ] 跨市场产品 EAN 抽样 0 违例
- [ ] CLI `query latest` / `query trends` 输出完整字段
- [ ] 报告 `amz-scout-slim-refactor-phase4-validation.md` 发布
- [ ] PRD Phase 4 行状态改为 `complete`（仅在上面全部通过时）

## Completion Checklist
- [ ] 代码（无新代码）
- [ ] 所有 validation 命令执行并记录原始输出
- [ ] 报告引用的每一条 gate 都有证据链接或 inline 贴图
- [ ] 样本不足（2 个跨市场 product）已在报告"Known Non-Regressions / Limitations"章节显式点名
- [ ] 后续动作（browser hand-test、code-review、PR）已列入 Next Steps 章节
- [ ] 若 60% gate 未达，报告给出根因假设 + 不关闭 Phase 4

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `ANTHROPIC_API_KEY` 不在环境中 → token audit 整组 skip | Medium | High | 在 `.env` 预置 + 文档明示；若真不可得，退而用 Phase 2 旧数据 + 合成 20 行度量作为代理，并在报告里标注 "live 度量待补" |
| `query_trends` `pct_saved_vs_raw < 60%` | Low | High | 回到 `webapp/summaries.py` 看 preview 是否超过 3 行或 `MAX_WARNING_CHARS` 被踩；必要时开 Phase 3 hotfix |
| `output/amz_scout.db` 被 clean / 重建为 0 行 | Low | High | Phase 4 开跑前 `sqlite3 ... SELECT COUNT(*) FROM keepa_products` 自检；<5 行就告警、不跑 token audit |
| 跨市场 product 只有 2 个，样本量薄 | High | Low | 报告显式标注 "样本不足，建议下一次 scrape round 后复查"；不阻塞 gate |
| Pre-existing ruff format drift 在本 phase 被误判为回归 | Low | Low | 在报告 "Known Non-Regressions" 章节显式列出 4 个文件，引用 Phase 3 report 的同样条目 |
| `query_latest` CLI 0 行让人误以为 CLI 挂了 | Low | Low | 先跑 `query trends`（已知有数据）作为有数据路径，`query latest` 只是验证 hint message |

## Notes

- Phase 4 报告本身会被未来复用作为 "瘦身三件套是否稳定" 的 regression baseline；因此报告里 "Known Non-Regressions / Limitations" 比 "一切通过" 更值钱，必须把样本不足 + pre-existing drift 显式落纸。
- `tests/test_token_audit.py` 在测试层已经硬断言了 60% / 30% 两条线；Phase 4 本 plan 把这两条线**再**在报告层冗余一次，不是重复，是为了让未来有人只看报告就能判断 gate 是否还成立。
- 若未来想把 Phase 4 自动化（cron on main），只需把 Task 1-5 串成一个 bash，读取每步退出码 + JSON 判定值；本 plan 故意保持手工流程以便每步观察，不做过度自动化。
- `amz-scout --help` smoke 这一步的意义远大于命令本身 — 它验证了 `typer` 入口、`amz_scout.api` 的所有顶层 import 在 envelope 重构后都还能加载；这是最便宜的 "整个 import graph 健康" 信号。
