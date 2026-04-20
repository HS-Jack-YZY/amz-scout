# Plan: Fix `test_token_audit._envelope_summary` Drift from Production `_build_summary`

## Summary
把 `tests/test_token_audit.py` 的本地 `_envelope_summary` helper 换成对 `webapp.summaries._build_summary` 的直接复用，并补一条 contract test 锁住"harness = 生产"这条不变量。消除 `file_attach_failed` / `xlsx_truncated` / `xlsx_row_limit` / warnings 截断 4 个已知漂移点，让 60%/50% PRD gate 衡量的是 LLM 真实收到的 envelope。Closes #14。

## User Story
作为依赖 token_audit 60% PRD gate 的维护者，
我希望 harness 度量的 summary envelope 与生产 `summarize_for_llm` 生成的 envelope 结构一致，
以便未来 `_build_summary` 新增字段（如 warnings 截断、xlsx 截断标志）时 audit 能立刻反映真实 token 成本而不是"乐观偏差"。

## Problem → Solution
**当前**：`tests/test_token_audit.py:60-89` 的 `_envelope_summary` 自己拼 summary dict，只覆盖 happy path：`count / file_attached / date_range / preview / meta passthrough`；漏掉生产 `_build_summary` 的 3 条字段分支 + 1 条 warnings 截断。导致在 failure/truncation 路径上 harness 度量值与 LLM 实收 envelope 结构不一致，60%/50% gate 是乐观下限。
**改后**：harness 直接 `from webapp.summaries import _build_summary`，用 `file_name=<非 None>, truncated=False` 固定成 success path 的度量姿态；再加一条 contract test 对 canonical 输入断言 `harness_wrapper(...) == {"ok": True, "data": _build_summary(...), ...}`。任何一侧漂移立刻红线。

## Metadata
- **Complexity**: Small
- **Source PRD**: N/A（自由文本 + issue #14）
- **PRD Phase**: N/A
- **Estimated Files**: 1 改 + 1 新建（不动 `webapp/summaries.py`）
- **Closes**: #14

---

## UX Design

### Before
```
┌──────────────────────────────────────────────────────────────────┐
│ Production `summarize_for_llm`                                   │
│   → _build_summary(rows, file_name, meta, truncated=True)        │
│   → {"count": N, "file_attached": "...", "xlsx_truncated": True, │
│       "xlsx_row_limit": 50000, "warnings": [...capped...], ...}  │
│                                                                  │
│ Token audit harness `_envelope_summary` (drifted)                │
│   → {"count": N, "file_attached": "...", "warnings": [...raw]}   │
│     ❌ 没有 xlsx_truncated / xlsx_row_limit                      │
│     ❌ 没有 file_attach_failed                                   │
│     ❌ warnings 未截断（可能 10x 大小）                          │
│                                                                  │
│ 后果：PRD 60% gate 对 optimistic payload 说"通过"                │
└──────────────────────────────────────────────────────────────────┘
```

### After
```
┌──────────────────────────────────────────────────────────────────┐
│ Production `summarize_for_llm`                                   │
│   → _build_summary(rows, file_name, meta, truncated=False|True)  │
│                                                                  │
│ Token audit harness                                              │
│   → _envelope_summary(...) → {"ok": True, "data":                │
│       _build_summary(rows, file_name, meta, truncated=False),    │
│       "error": None, "meta": meta}                               │
│     ✅ 一份实现，一次更新                                        │
│                                                                  │
│ Contract test (new): 同输入 → 同输出                             │
│   assert harness_wrapper(canonical_input) == expected_envelope   │
│     ✅ 任何分叉立刻红线                                          │
└──────────────────────────────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `_envelope_summary(...)` | 本地手拼 dict | 调用 `_build_summary` | 签名保持不变，测试函数调用方无感知 |
| `webapp.summaries._build_summary` | 仅生产使用 | 生产 + 审计复用 | 零改动，只新增 1 个 import |
| 新增 contract test | N/A | `test_envelope_summary_matches_production`（`@pytest.mark.unit`） | 不在 network gate 下；CI 总是跑 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/summaries.py` | 107-147 | `_build_summary` 的当前签名和所有字段分支 |
| P0 | `webapp/summaries.py` | 85-104 | `_truncate_warnings` 的截断规则（MAX_WARNINGS=3, MAX_WARNING_CHARS=200） |
| P0 | `webapp/summaries.py` | 28-37 | `MAX_PREVIEW_ROWS` / `MAX_XLSX_ROWS` / `MAX_WARNINGS` 常量 |
| P0 | `tests/test_token_audit.py` | 60-89 | 要替换的 `_envelope_summary` 当前实现 |
| P0 | `tests/test_token_audit.py` | 188-355 | 5 个调用点：`test_query_latest` / `test_query_trends` 等 |
| P1 | `tests/test_webapp_smoke.py` | 522-620 | `file_attach_failed` / `xlsx_truncated` / warnings 截断的已有生产测试，复用它们的测试范式 |
| P1 | `webapp/__init__.py` | all | 确认 `import webapp` 无副作用（无需 env fixture） |
| P1 | `pyproject.toml` | `[tool.pytest.ini_options]` | markers 定义：`unit` / `network` —— contract test 用 `unit` |
| P2 | `.claude/PRPs/plans/completed/brand-model-normalization.plan.md` | all | 最近一个同 scope 的 plan 风格范例 |

## External Documentation

> No external research needed — 完全基于已有生产代码的内部复用。

---

## Patterns to Mirror

### IMPORT_PRIVATE_HELPER_IN_TEST
// SOURCE: tests/test_webapp_smoke.py:532-534
```python
from webapp import summaries as webapp_summaries
from webapp import tools as webapp_tools
from webapp.tools import dispatch_tool
```
测试直接 import `webapp.summaries` 是已有范式。underscore-prefixed helper (`_build_summary`) 虽为 convention-private，但测试语境下复用是安全的（Python 无访问控制，且保留 leading underscore 表示"内部契约、改动要谨慎"）。

### TEST_FUNCTION_NAMING
// SOURCE: tests/test_webapp_smoke.py:522, 557, 600
```python
def test_attach_failure_drops_file_attached_from_summary(
    self, monkeypatch: pytest.MonkeyPatch
) -> None:
```
`test_<行为>_<对象>` 格式，动词描述不变量。contract test 命名沿用该风格：`test_envelope_summary_matches_production_build_summary`。

### PYTEST_MARKER_USAGE
// SOURCE: tests/test_token_audit.py:22 和 tests/test_webapp_smoke.py:28
```python
# 整文件 network（要真 API key）：
pytestmark = pytest.mark.network

# 单条 unit（无依赖）：
@pytest.mark.unit
class TestXxx:
```
`test_token_audit.py` 模块级就是 `network`，contract test 需要独立 `@pytest.mark.unit` 标记。本 plan 把 contract 剥到单独文件以便 CI 每次都跑（`-m "not network"` 不会跳过）。

### DOCSTRING_WHY_NOT_WHAT
// SOURCE: webapp/summaries.py:44-51, tests/test_token_audit.py:68-74
```python
def _rows_to_xlsx_bytes(
    rows: list[dict], sheet_name: str = "data"
) -> tuple[bytes, bool]:
    """Materialize full DB rows to an in-memory xlsx.
    Returns ``(bytes, truncated)``. ``truncated=True`` means ...
    """
```
仓内 docstring 惯用："一句概括 + 一段 why（设计选择/gotcha）"。harness 新 docstring 要解释"为什么固定成 success path 度量"。

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tests/test_token_audit.py` | UPDATE | 替换 `_envelope_summary` 为生产 `_build_summary` 的薄包装；更新文件 docstring 删掉"mirrors field-for-field"的过期声明 |
| `tests/test_summaries_contract.py` | CREATE | Contract test，`@pytest.mark.unit`，无网络依赖；锁住 harness ↔ 生产不变量 |

## NOT Building

- **不改 `webapp/summaries.py`**：`_build_summary` 当前实现就是 single source of truth，harness 应该去迎合它，不是反过来。
- **不改 PRD gate 阈值（60% / 30%）**：本次只修 harness 与生产对齐，不重新校准 gate。若对齐后某个 gate 掉到阈值以下，那是**真问题**，按 issue 讲的"optimistic measurement"本应 fail，属于单独跟进。
- **不引入 `unittest.mock` 或 fixture 框架换血**：现有 `pytest.MonkeyPatch` 足矣，保持最小 diff。
- **不添 chainlit session mock**：`_build_summary` 是纯函数，不触 session；只有 `_attach_file_to_session` 和 `_log_query` 碰 session，本改动不调用它们。
- **不改 `_count_tokens_for_tool_result` 签名**：token counter 继续接 `{"ok", "data", "error", "meta"}` 信封，只是 `data` 字段来源换成 `_build_summary`。
- **不把 `_build_summary` 改成 public API**：保留 underscore prefix；测试里 import 私有 helper 在本仓已有先例，无需"为了测试改 API"。

---

## Step-by-Step Tasks

### Task 1: 把 harness `_envelope_summary` 改成 `_build_summary` 的薄包装

- **ACTION**: 编辑 `tests/test_token_audit.py:60-89`
- **IMPLEMENT**:
  ```python
  def _envelope_summary(
      rows: list[dict],
      *,
      preview_trimmer,
      date_field: str | None,
      file_name: str,
      meta: dict | None = None,
  ) -> dict:
      """Build a Phase 3 summary envelope by **reusing production `_build_summary`**.

      Closes #14: the earlier hand-mirrored helper drifted from production
      (missing ``file_attach_failed`` / ``xlsx_truncated`` / warnings
      truncation). Delegating to the real helper guarantees the audit measures
      the exact envelope shape the LLM sees in production.

      The token audit measures the **success path** (xlsx attached, no row
      truncation), so we pin ``file_name`` non-None and ``truncated=False``
      here. Failure-path token cost is covered by the unit tests in
      ``tests/test_webapp_smoke.py``.
      """
      from webapp.summaries import _build_summary

      summary = _build_summary(
          rows,
          file_name=file_name,
          meta=meta or {},
          preview_trimmer=preview_trimmer,
          date_field=date_field,
          truncated=False,
      )
      return {"ok": True, "data": summary, "error": None, "meta": meta or {}}
  ```
- **MIRROR**: `DOCSTRING_WHY_NOT_WHAT`（一句概括 + why），`IMPORT_PRIVATE_HELPER_IN_TEST`（延迟 import，匹配文件现有"函数体内 `from amz_scout ...`"风格，见 `tests/test_token_audit.py:199-201`）。
- **IMPORTS**:
  - 新增文件级：无（`_build_summary` 在函数体内延迟 import）。
- **GOTCHA**:
  - `_build_summary` 的字段合并顺序：`count` → `file_attached`/`file_attach_failed` → `xlsx_truncated`/`xlsx_row_limit` → `date_range` → `preview` → meta passthrough → warnings。token 数对 dict 顺序敏感（JSON 序列化），对齐后 audit JSON 的绝对值会变；**不必**与旧值比，只需后续 pct 检查通过。
  - `_build_summary` 对 `meta["warnings"]` 走 `_truncate_warnings`（旧 harness 是原样透传）；如果 audit 的 `meta` 本不含 warnings，影响为零。
  - 调用方（5 个 `test_query_*_token_delta`）的签名和返回值不变，**零处改动**。
- **VALIDATE**: 运行 `pytest tests/test_token_audit.py -m network --no-cov -q`（本地设 `ANTHROPIC_API_KEY` 和 `output/amz_scout.db`），确认 5 条 per-tool audit 全跑过、`test_query_trends_token_delta` 的两个阈值 assert 不回退。

### Task 2: 更新 `test_token_audit.py` 文件级 docstring

- **ACTION**: 编辑 `tests/test_token_audit.py:1-10` 的模块 docstring
- **IMPLEMENT**:
  - 删掉或改写 `_envelope_summary` 声称"mirrors field-for-field"的任何残留语义。
  - 把当前文件 docstring 补上一句：`Summaries are generated by reusing webapp.summaries._build_summary to prevent drift (issue #14).`
- **MIRROR**: `DOCSTRING_WHY_NOT_WHAT`。
- **IMPORTS**: 无
- **GOTCHA**: 仅改 docstring，代码零影响；ruff/mypy 对纯 docstring 变动无噪声。
- **VALIDATE**: `ruff check tests/test_token_audit.py` 通过；`grep -n "mirrors" tests/test_token_audit.py` 无残留过时声明。

### Task 3: 新增 contract test 锁定 harness ↔ 生产等价

- **ACTION**: 新建 `tests/test_summaries_contract.py`
- **IMPLEMENT**:
  ```python
  """Contract test: harness `_envelope_summary` vs production `_build_summary`.

  Locks issue #14 (`test_token_audit._envelope_summary` drift). Any future
  edit that reintroduces a hand-rolled summary in the audit harness, or that
  changes `_build_summary` without running the audit, will flip this test red.

  Kept separate from `test_token_audit.py` because that module is
  ``pytestmark = pytest.mark.network`` (skips without ANTHROPIC_API_KEY).
  This contract must run on every CI build regardless of network keys.
  """
  from __future__ import annotations

  import pytest

  pytestmark = pytest.mark.unit


  def test_envelope_summary_matches_production_build_summary() -> None:
      """A canonical input must produce identical summary dicts from both
      the audit harness wrapper and production `_build_summary`.

      Canonical fixture covers:
      - non-empty rows → exercises ``count`` + ``preview`` + ``date_range``
      - a ``date_field`` entry present in rows
      - meta carrying every passthrough key: asin / model / brand /
        series_name / hint / phase / warnings
      - warnings payload that crosses MAX_WARNINGS and MAX_WARNING_CHARS
        → verifies the harness pipes warnings through production's
        ``_truncate_warnings`` (not raw pass-through, which was the bug).
      """
      from tests.test_token_audit import _envelope_summary
      from webapp.summaries import MAX_WARNING_CHARS, MAX_WARNINGS, _build_summary

      rows = [
          {"date": "2026-04-01", "asin": "B0X", "value": 100},
          {"date": "2026-04-03", "asin": "B0X", "value": 110},
          {"date": "2026-04-05", "asin": "B0X", "value": 105},
      ]
      long_warning = "W" * (MAX_WARNING_CHARS + 50)
      meta = {
          "asin": "B0X",
          "model": "Slate 7",
          "brand": "GL.iNet",
          "series_name": "NEW",
          "hint": "fresh data",
          "phase": "complete",
          "warnings": [long_warning] * (MAX_WARNINGS + 2),
      }

      def _preview(xs: list[dict]) -> list[dict]:
          return [{"date": r["date"], "value": r["value"]} for r in xs]

      harness_envelope = _envelope_summary(
          rows,
          preview_trimmer=_preview,
          date_field="date",
          file_name="audit.xlsx",
          meta=meta,
      )
      prod_summary = _build_summary(
          rows,
          file_name="audit.xlsx",
          meta=meta,
          preview_trimmer=_preview,
          date_field="date",
          truncated=False,
      )

      assert harness_envelope["data"] == prod_summary, (
          "Harness diverged from _build_summary — issue #14 regression. "
          f"harness={harness_envelope['data']!r} prod={prod_summary!r}"
      )
      # Meta carries unchanged — the envelope must not accidentally pipe
      # meta through _truncate_warnings; only summary.warnings is capped.
      assert harness_envelope["meta"] == meta
      assert harness_envelope["ok"] is True
      assert harness_envelope["error"] is None


  def test_envelope_summary_empty_rows_matches_production() -> None:
      """Edge case: no rows → no preview / no date_range, count==0."""
      from tests.test_token_audit import _envelope_summary
      from webapp.summaries import _build_summary

      harness = _envelope_summary(
          [], preview_trimmer=lambda _x: [], date_field="date",
          file_name="empty.xlsx", meta={},
      )
      prod = _build_summary(
          [], file_name="empty.xlsx", meta={},
          preview_trimmer=lambda _x: [], date_field="date", truncated=False,
      )
      assert harness["data"] == prod
  ```
- **MIRROR**:
  - `TEST_FUNCTION_NAMING` — `test_<行为>_<对象>`
  - `PYTEST_MARKER_USAGE` — 文件级 `pytestmark = pytest.mark.unit`
  - `DOCSTRING_WHY_NOT_WHAT` — 每个测试开头一段"为什么这个 case 存在"
- **IMPORTS**:
  - 顶部：`from __future__ import annotations` + `import pytest`
  - 测试体内延迟 import：`from tests.test_token_audit import _envelope_summary` 和 `from webapp.summaries import _build_summary`
- **GOTCHA**:
  - `from tests.test_token_audit import _envelope_summary` 会**触发** `test_token_audit.py` 的整文件 import（包括 `pytestmark = pytest.mark.network`），但 import 本身不会 skip 当前文件；contract test 自己的 `pytestmark = pytest.mark.unit` 决定它的运行条件。
  - `tests/__init__.py` 已存在（ls 确认），说明 `tests/` 已是 package，`from tests.test_token_audit import ...` 可工作。
  - `_build_summary` 返回**新 dict**，断言 `==` 用 Python dict deep equality 即可。
- **VALIDATE**:
  - `pytest tests/test_summaries_contract.py -q` 两条均通过
  - `pytest -m "not network" tests/test_summaries_contract.py -q` 也通过（证明不依赖 network mark）

### Task 4: 本地 + CI 验证 + audit JSON 基线重建

- **ACTION**: 运行完整 test suite 确认无回归；如果本地有 `output/amz_scout.db` 和 `ANTHROPIC_API_KEY`，重跑 `test_token_audit.py` 让 `output/token_audit.json` 重新生成成新基线。
- **IMPLEMENT**: 无代码改动；只是验证 + 可选基线刷新。
- **MIRROR**: N/A
- **IMPORTS**: N/A
- **GOTCHA**:
  - `output/token_audit.json` 按仓内 `output/` 风格通常 gitignored；本地 audit JSON 的绝对数字会变但对 CI 无影响。
  - 若本地 `test_query_trends_token_delta` 的两个阈值（60% / 30%）在对齐后**反而 fail**，这是 **issue #14 的正当产物**。按 issue 描述本应是"weaker guarantee than it appears"，对齐后若 fail 说明真实 pct 达不到门槛 —— 需要另开 follow-up（调 preview / 重估 baseline），**本 plan 不应掩盖真阴性**。PR body 应记录任何 pct 变化。
  - `_assert_nonregressive` 分支（5 个非 trends tool）不走 `_envelope_summary`，不受影响。
- **VALIDATE**:
  - `pytest -m "not network" -q`：全绿，contract 新 test 被纳入
  - `pytest tests/test_token_audit.py -q`（无 key 环境）：干净 skip
  - `ruff check tests/test_token_audit.py tests/test_summaries_contract.py`：无 warning

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_envelope_summary_matches_production_build_summary` | 非空 rows，meta 含所有 passthrough key + 超限 warnings | `harness["data"] == _build_summary(...)`，warnings 已按 `_truncate_warnings` 截断 | No（canonical） |
| `test_envelope_summary_empty_rows_matches_production` | `rows=[]`, `meta={}` | `harness["data"] == _build_summary(...)`，count=0、无 preview/date_range | Yes（empty） |

### Edge Cases Checklist
- [x] 空 rows（覆盖）
- [x] meta 带所有 passthrough key（覆盖）
- [x] warnings 超过 MAX_WARNINGS 和 MAX_WARNING_CHARS（覆盖）
- [ ] `date_field` 指向不存在的列 → 生产代码的 `if date_field in rows[0]` 会短路；harness 跟随。**不需要**单独 case，生产测试已覆盖。
- [ ] `file_name=None`（failure path） → token audit 不度量 failure path（注释已说明）；failure path 的等价由 `test_webapp_smoke.py::test_attach_failure_drops_file_attached_from_summary` 覆盖。
- [ ] `truncated=True`（xlsx 截断） → 同上，audit 不度量；生产已有 `test_xlsx_row_limit_truncates_and_flags_summary`。
- [ ] 并发访问 → N/A（纯函数）
- [ ] 权限拒绝 → N/A

---

## Validation Commands

### Static Analysis
```bash
ruff check tests/test_token_audit.py tests/test_summaries_contract.py
```
EXPECT: Zero warnings

```bash
# 冒烟 import
python -c "from tests.test_token_audit import _envelope_summary; from webapp.summaries import _build_summary; print('imports ok')"
```
EXPECT: `imports ok`

### Unit Tests
```bash
# 新 contract test
pytest tests/test_summaries_contract.py -v
```
EXPECT: 2 passed

```bash
# 全 unit 层回归（排除 network）
pytest -m "not network" -q
```
EXPECT: All pre-existing tests pass + 2 new tests pass; no skips beyond expected

### Full Test Suite
```bash
# 如有 ANTHROPIC_API_KEY + output/amz_scout.db：
pytest -q
```
EXPECT: No regressions. `test_query_trends_token_delta` 仍通过两个阈值 assert.

```bash
# 无 key 环境：
pytest -q
```
EXPECT: `test_token_audit.py` 整体 skip，其余全绿。

### Browser Validation
N/A — 纯测试改动，不涉及 UI。

### Manual Validation
- [ ] `grep -n "mirrors.*field-for-field" tests/test_token_audit.py` 无结果
- [ ] `grep -n "_build_summary" tests/test_token_audit.py` 有 1 条（新 import）
- [ ] `tests/test_summaries_contract.py` 新建且 2 条 test 通过
- [ ] `git diff webapp/summaries.py` 空（严禁改生产）
- [ ] 如有 key：对比 `output/token_audit.json` 新旧 `pct_saved_vs_raw`；`query_trends` 仍 ≥ 60%
- [ ] PR body 用 `closes #14`（按 2026-04-20 GitHub auto-close keywords 反馈：仅在本 PR 的本次提交**真正**关闭 issue 时使用）

---

## Acceptance Criteria
- [ ] `tests/test_token_audit.py::_envelope_summary` 内部仅调用 `webapp.summaries._build_summary`，不再手拼字段
- [ ] 新增 `tests/test_summaries_contract.py`，2 条 `@pytest.mark.unit` 测试全绿
- [ ] `pytest -m "not network" -q` 对新文件 2 passed，整体无回归
- [ ] `ruff check` 对两个受改文件无警告
- [ ] `webapp/summaries.py` 零改动（`git diff webapp/` 为空）
- [ ] `tests/test_token_audit.py` 模块 docstring 更新，"mirrors field-for-field" 过时声明删除
- [ ] PR body 引用 issue #14（`closes #14`）

## Completion Checklist
- [ ] 代码遵循 discovered patterns（`TEST_FUNCTION_NAMING`, `DOCSTRING_WHY_NOT_WHAT`, `IMPORT_PRIVATE_HELPER_IN_TEST`）
- [ ] 错误处理与仓内风格一致（本 plan 无新错误路径）
- [ ] 日志风格与仓内一致（本 plan 无新日志）
- [ ] 测试命名、标记、延迟 import 模式全部对齐现有测试
- [ ] 无硬编码值（所有 `MAX_*` 常量从 `webapp.summaries` 读）
- [ ] 生产代码零改动 — 只测试层改动
- [ ] 自包含 — 实现期间无需回查 issue 或代码库

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `test_query_trends_token_delta` 的 60%/30% gate 在对齐后 fail | Low | High（阻塞 CI） | 这才是 issue 要揭露的真问题。若本地验证时触发，不在本 PR 掩盖；另起 follow-up issue 调 preview 或重估基线。PR body 应记录新 `pct_saved_vs_raw` 值。 |
| `from tests.test_token_audit import _envelope_summary` 触发 `pytestmark = pytest.mark.network` 副作用 | Low | Low | pytest marker 是装饰器/属性设置，纯 import 不会 skip 当前文件；contract test 自己的 `pytestmark = pytest.mark.unit` 决定它的运行条件。 |
| 未来有人把 `_build_summary` rename 或重构参数 | Medium | Medium | contract test 的 `TypeError` / `ImportError` 会立刻捕获；同时 issue #14 的修复理由已入 plan 档案，后续 reviewer 可找到上下文。 |
| `_build_summary` 某些分支（如 `truncated=True`）未被 contract test 覆盖 | High | Low | 本 plan 明确**只**锁 audit 用到的 success path；failure / truncated 路径已有 `test_webapp_smoke.py` 的生产测试覆盖。 |

## Notes

- **为什么不走 Option 2（双 helper + contract test）**：issue 说"Option 1 (preferred)"。Option 1 消除了漂移源头（只有一份实现），而 Option 2 保留了两份、仅靠一个 test 约束，在 reviewer 疲劳的长期维护下 Option 2 更脆弱。本 plan 采用 Option 1 + 一条"即便未来有人又分叉出来也会红"的兜底 contract test。
- **为什么 contract test 独立成文件**：`test_token_audit.py` 模块级 `pytestmark = pytest.mark.network`，任何同文件测试都被 network mark 感染。CI 默认跑 `-m "not network"`，会 skip contract。独立文件让 contract 永远在 CI 前线。
- **为什么不新增 fixture 重构整文件**：5 个 `test_query_*_token_delta` 已经工作，改动面越大回归风险越高。本 plan 严格限定 diff：`_envelope_summary` 函数体 + 文件 docstring + 1 个新文件。
- **和 #12 / #10 的关系**：Session summary 显示 #15 已完成（brand-model-normalization），本条是 PR #10 review 衍生的漂移 issue。与 #10 review 提到的"CRITICAL Keepa transient + HIGH webapp reliability"可以一起打捆成 reliability PR，但本 plan **不** scoped 进那些 —— 它们是独立 issue，避免混合 review。
- **验收后动作**：commit → push → `gh pr create` 写 `closes #14`（确认本 PR 合并即关闭该 issue）。
