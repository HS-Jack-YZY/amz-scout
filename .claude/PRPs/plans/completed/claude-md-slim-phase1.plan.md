# Plan: CLAUDE.md 瘦身 (Phase 1)

## Summary

将 CLAUDE.md 从 ~5,287 tokens（373 行 / 21,151 字符）压缩至 ≤2,500 tokens（目标 ≤10,000 字符），通过三个策略：(1) 移除整个 Developer Reference 到 `docs/DEVELOPER.md`，(2) 移除"强制 ASIN 补全"指令和 webapp trim 细节，(3) 压缩示例从 11 个到 3 个核心示例、合并重复指令。

## User Story

As a Claude Code CLI user (Jack),
I want CLAUDE.md 在每轮对话中消耗 ≤2,500 tokens,
So that 我在使用 Claude Code 与 amz-scout 交互时节省 ~53% 的固定 token 开销。

## Problem → Solution

CLAUDE.md 每轮注入 ~5,287 tokens 固定开销（含 33 行强制 ASIN 补全指令 + 132 行 Developer Reference + 11 个冗余示例）→ 压缩至 ≤2,500 tokens，保留 Claude Code LLM 回答查询所需的核心指令。

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md`
- **PRD Phase**: Phase 1 — CLAUDE.md 瘦身
- **Estimated Files**: 3 (CLAUDE.md, docs/DEVELOPER.md, tests/test_claude_md_size.py)

---

## UX Design

N/A — internal change, no user-facing UI modification.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Claude Code CLI context | ~5,287 tokens/轮 | ≤2,500 tokens/轮 | ~53% reduction |
| Developer onboarding | All info in CLAUDE.md | Architecture/commands in docs/DEVELOPER.md | Developer reads docs/ |
| Webapp behavior | Unchanged | Unchanged | webapp uses config.py SYSTEM_PROMPT, not CLAUDE.md |
| CLI behavior | Unchanged | Unchanged | CLI delegates to api.py, unaffected |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 (critical) | `CLAUDE.md` | all (373 lines) | The file being compressed — must understand every section |
| P1 (important) | `webapp/config.py` | 21-29 | Verify SYSTEM_PROMPT is independent of CLAUDE.md |
| P1 (important) | `webapp/tools.py` | 85-317 | Tool schemas — verify tool docstrings are in tools.py not CLAUDE.md |
| P1 (important) | `webapp/llm.py` | 1-109 | LLM loop — verify CLAUDE.md is NOT loaded by webapp |
| P2 (reference) | `README.md` | 1-50 | Existing developer docs — avoid duplication with DEVELOPER.md |
| P2 (reference) | `tests/test_webapp_smoke.py` | all | Regression tests — verify they pass after CLAUDE.md change |
| P2 (reference) | `tests/test_api.py` | 1-40 | API tests — verify no CLAUDE.md dependency |
| P2 (reference) | `tests/test_token_audit.py` | all | Token audit harness — may need to add CLAUDE.md measurement |

## External Documentation

No external research needed — this is a documentation compression task using established internal patterns.

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: docs/database-er-diagram.md:1
# Documentation files use kebab-case filenames in docs/ directory
# Exception: DEVELOPER.md uses UPPER_CASE as conventional for project docs
```

### TEST_STRUCTURE
```python
# SOURCE: tests/test_webapp_smoke.py:28-39
# Tests use @pytest.mark.unit decorator
# Class-based test organization (TestWebappImports, TestToolDispatch, etc.)
# monkeypatch fixture for environment isolation
# _reset_webapp_modules() + _set_fake_env() helpers for module reload
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `CLAUDE.md` | UPDATE | Compress from 373→~100 lines; remove Developer Reference, ASIN 补全, webapp trim details; compress examples |
| `docs/DEVELOPER.md` | CREATE | Receive Developer Reference section (Architecture, Commands, Config, Schema, etc.) |
| `tests/test_claude_md_size.py` | CREATE | Regression guard: assert CLAUDE.md ≤10,000 chars |

## NOT Building

- **不修改 webapp 代码** — Phase 1 只改 CLAUDE.md 和文档
- **不修改 api.py / db.py** — 代码逻辑不变
- **不修改 test_token_audit.py** — 保持现有 token 审计不变
- **不修改 webapp/config.py SYSTEM_PROMPT** — webapp 独立于 CLAUDE.md
- **不压缩 tool schemas** — 那是 Phase 3 的事

---

## Step-by-Step Tasks

### Task 1: 创建 docs/DEVELOPER.md（迁移 Developer Reference）

- **ACTION**: 从 CLAUDE.md 的 `## Developer Reference` section（Lines 242-373）提取内容，创建 `docs/DEVELOPER.md`
- **IMPLEMENT**: 新文件包含以下 sections（保持原有标题和内容不变）：
  - Commands（`pip install`, daily workflow, query, legacy, admin, test, lint, deployment）
  - Architecture（ASCII diagram）
  - Key Design Decisions（5 bullet points）
  - Database Schema (db.py)（9 tables summary）
  - Config Structure（marketplaces.yaml + project.yaml）
  - External Dependencies（browser-use + Keepa）
  - Output Layout（directory tree + regions）
  - Conventions（Python 3.12+, ruff, frozen dataclasses, etc.）
- **MIRROR**: 与现有 `docs/database-er-diagram.md` 保持风格一致
- **IMPORTS**: N/A
- **GOTCHA**: 不要丢失任何 developer 信息——只是搬家，不是删除。在 DEVELOPER.md 开头添加说明来源。
- **VALIDATE**: 原 CLAUDE.md Developer Reference 所有标题和内容在 DEVELOPER.md 中均存在

### Task 2: 压缩 CLAUDE.md — 移除 Developer Reference

- **ACTION**: 删除 CLAUDE.md 中 `## Developer Reference` 及其所有子 section（Lines 242-373）
- **IMPLEMENT**: 替换为一行引用：`> Developer reference (commands, architecture, schema): see [docs/DEVELOPER.md](docs/DEVELOPER.md)`
- **MIRROR**: 保持 CLAUDE.md 其他部分的 markdown 风格
- **IMPORTS**: N/A
- **GOTCHA**: 确保 `---` 分隔线正确保留，不破坏 section 结构
- **VALIDATE**: `wc -l CLAUDE.md` 应减少 ~130 行

### Task 3: 移除"新产品后台 ASIN 补全"指令 (Key Behavior #12)

- **ACTION**: 删除 Key Behaviors 中 #12 整段（从 `12. **新产品后台 ASIN 补全` 到其结尾 `**禁止跳过此步骤**` 行）
- **IMPLEMENT**: 完全移除，不替换（Phase 2 将用 EAN/UPC 自动绑定替代此行为）
- **MIRROR**: 保持编号连续（后续 #13→#12, #14→#13, #15→#14）
- **IMPORTS**: N/A
- **GOTCHA**: 同时检查 CLAUDE.md 中其他引用了 "ASIN 补全" 或 "pending_markets" 的地方。由于 API 函数本身不删除（仅删除 CLAUDE.md 中的**强制行为指令**），import list 中的引用保留。
- **VALIDATE**: `grep -c "ASIN 补全\|pending_markets\|补全步骤" CLAUDE.md` 应返回 0

### Task 4: 移除 Webapp Trim 细节 (Key Behavior #15)

- **ACTION**: 删除 Key Behaviors #15（"Webapp 信封已精简"）整段
- **IMPLEMENT**: 这是 developer 实现细节，不是 Claude Code LLM 行为指令。LLM 不需要知道 trim 白名单的具体字段。完全移除。
- **MIRROR**: 编号已在 Task 3 中重排
- **IMPORTS**: N/A
- **GOTCHA**: 此信息可以从 `src/amz_scout/_llm_trim.py` 源码中获取。确认 DEVELOPER.md 中有对应说明。
- **VALIDATE**: `grep -c "trim_competitive_rows\|trim_timeseries_rows\|trim_for_llm" CLAUDE.md` 应返回 0

### Task 5: 合并重复 Keepa API 禁止指令

- **ACTION**: Key Behaviors #13（"禁止直接调用 Keepa API"）中有 4 条子规则。压缩为简洁的 2 行。
- **IMPLEMENT**: 将 #13 的 4 条子规则压缩为：
  ```
  **禁止直接调用 Keepa API**: 所有 Keepa 操作必须通过 `amz_scout.api` 函数。用户给产品名时按优先级找 ASIN：(1) 问用户 → (2) WebSearch 搜 Amazon URL 提取 → (3) `discover_asin()`。Keepa 60 token 上限，1/min 恢复，违规将阻塞所有用户 1 小时。
  ```
- **MIRROR**: 与其他 Key Behaviors 条目保持相同的简洁风格
- **IMPORTS**: N/A
- **GOTCHA**: 不要丢失关键警告信息（60 token 上限、1/min 恢复、1 小时阻塞）
- **VALIDATE**: Keepa 禁止指令从 ~8 行压缩至 2 行

### Task 6: 压缩 Examples 从 11 个到 3 个

- **ACTION**: 将 `### Examples (Chinese + English)` 中的 11 个示例压缩至 3 个核心示例
- **IMPLEMENT**: 保留以下 3 个（覆盖最常用的查询模式）：
  1. **`query_trends`** — 最常用查询，带 price encoding 说明（"Slate 7 UK 价格趋势"）
  2. **`add_product`** — 产品注册（带多市场 asins），保留简化版
  3. **`check_freshness`** — 数据新鲜度检查（覆盖"管理"类查询）

  移除的 8 个示例所覆盖的模式已在 Decision Tree 中明确映射，Claude Code 不需要冗余示例来推断正确的 API 调用。
- **MIRROR**: 保持现有示例格式（中文标题 + python code + comment）
- **IMPORTS**: N/A
- **GOTCHA**: 保留 `query_trends` 示例中的 `value 是 Keepa 编码: 除以 100 得到实际价格` 注释——这是理解 price encoding 的关键提示。
- **VALIDATE**: 示例部分行数从 ~83 行降至 ~25 行

### Task 7: 压缩 Calling the API section

- **ACTION**: 压缩 import 列表和 envelope 格式说明
- **IMPLEMENT**:
  - Import 列表从分组 block 改为单行列举
  - Envelope 格式保留但移除多余空行
  - 删除 `Always check result["ok"]` 这一行（已在 envelope 定义中体现）
- **MIRROR**: Python import 风格
- **IMPORTS**: N/A
- **GOTCHA**: 确保所有公开 API 函数名仍然列出——Claude Code 需要知道有哪些函数可用
- **VALIDATE**: Calling the API section 从 ~28 行降至 ~12 行

### Task 8: 精简 Key Behaviors 条目

- **ACTION**: 压缩剩余的 Key Behaviors 条目文字
- **IMPLEMENT**: 针对每条规则的压缩策略：
  - #1 Auto-fetch: 保留，压缩为 1 行
  - #2 Browser data: 保留，压缩为 1 行
  - #3 Marketplace aliases: 保留，压缩为 1 行（去掉示例列表）
  - #4 Product resolution: 保留核心（4 级 fallback），删除 ASIN 透传的 4 条子规则详细描述（保留行为结论）
  - #5 Token awareness: 保留，压缩为 1 行
  - #6 phase 响应协议: 保留 3 条 phase 值，删除冗余说明
  - #7 Price encoding: 保留具体规则（÷100, ×10, -1）
  - #8 Product registry: 压缩为 1 行核心概念
  - #9 project 参数: 压缩为 1 行
  - #10 ASIN 验证: 压缩为 1 行
  - #11 绝不猜测 ASIN: 保留，压缩为 1 行
  - #12 (原#13) Keepa API: 已在 Task 5 中处理
  - #13 (原#14) product_tags: 保留 1 行
- **MIRROR**: 统一为 `**标题**: 一句话描述` 格式
- **IMPORTS**: N/A
- **GOTCHA**: 不要丢失 Price encoding 的具体规则——这是 Claude Code 正确解读数据的关键
- **VALIDATE**: Key Behaviors section 从 ~78 行降至 ~25 行

### Task 9: 创建 CLAUDE.md 大小回归测试

- **ACTION**: 创建 `tests/test_claude_md_size.py` 作为 CLAUDE.md 大小的门卫
- **IMPLEMENT**:
  ```python
  """Regression guard: CLAUDE.md must stay under the token budget."""
  from pathlib import Path
  import pytest

  CLAUDE_MD = Path(__file__).parent.parent / "CLAUDE.md"
  MAX_CHARS = 10_000  # approx 2,500 tokens (mix of Chinese + English)

  @pytest.mark.unit
  def test_claude_md_char_budget():
      text = CLAUDE_MD.read_text()
      assert len(text) <= MAX_CHARS, (
          f"CLAUDE.md is {len(text)} chars (budget: {MAX_CHARS}). "
          f"Move developer docs to docs/DEVELOPER.md."
      )

  @pytest.mark.unit
  def test_claude_md_no_forced_asin_discovery():
      """PRD decision: forced ASIN discovery via WebSearch is removed."""
      text = CLAUDE_MD.read_text()
      assert "禁止跳过此步骤" not in text
      assert "后台 ASIN 补全" not in text
  ```
- **MIRROR**: 与 `tests/test_webapp_smoke.py` 保持相同的 `@pytest.mark.unit` 风格
- **IMPORTS**: `pathlib.Path`, `pytest`
- **GOTCHA**: 字符限制 10,000 对应约 2,500 tokens（中英混合文本的平均换算比例 ~4 chars/token）
- **VALIDATE**: `pytest tests/test_claude_md_size.py` 通过

### Task 10: 最终验证

- **ACTION**: 运行全量测试套件确保零回归
- **IMPLEMENT**:
  1. `wc -c CLAUDE.md` 确认 ≤10,000 字符
  2. `pytest` 全量通过
  3. `ruff check src/ tests/` 零错误
  4. 手动审查压缩后的 CLAUDE.md 确保所有核心行为指令保留
- **MIRROR**: N/A
- **IMPORTS**: N/A
- **GOTCHA**: 确保 `docs/DEVELOPER.md` 中的所有信息与原 CLAUDE.md Developer Reference 一致
- **VALIDATE**: 所有测试绿色 + `wc -c CLAUDE.md` ≤10,000

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_claude_md_char_budget` | Read CLAUDE.md | len(text) ≤ 10,000 | No |
| `test_claude_md_no_forced_asin_discovery` | Read CLAUDE.md | No "后台 ASIN 补全" text | No |
| Existing `test_webapp_smoke.py` suite | All webapp tests | All pass | Regression |
| Existing `test_api.py` suite | All API tests | All pass | Regression |
| Existing `test_core_flows.py` suite | All core flow tests | All pass | Regression |

### Edge Cases Checklist

- [x] CLAUDE.md 过度压缩导致 Claude Code 无法理解查询意图 → 通过保留 Decision Tree 和 3 个核心示例缓解
- [x] Developer Reference 外迁后信息丢失 → diff 验证
- [x] 编号重排后遗漏条目 → 逐条核对
- [x] webapp 不受影响 → webapp 使用 config.py SYSTEM_PROMPT，不读 CLAUDE.md
- [x] CLI 不受影响 → CLI 走 api.py，不依赖 CLAUDE.md 内容

---

## Validation Commands

### Static Analysis

```bash
ruff check src/ tests/
ruff format --check src/ tests/
```
EXPECT: Zero errors

### Unit Tests

```bash
pytest tests/test_claude_md_size.py -v
```
EXPECT: 2 tests pass

### Full Test Suite

```bash
pytest
```
EXPECT: All tests pass, no regressions

### Character Count

```bash
wc -c CLAUDE.md
```
EXPECT: ≤10,000 characters

### Content Integrity

```bash
# Verify Developer Reference exists in new location
grep -c "Architecture" docs/DEVELOPER.md
grep -c "Database Schema" docs/DEVELOPER.md
grep -c "Commands" docs/DEVELOPER.md
```
EXPECT: Each returns ≥1

### Removed Content Verification

```bash
grep -c "后台 ASIN 补全" CLAUDE.md
grep -c "禁止跳过此步骤" CLAUDE.md
grep -c "trim_competitive_rows" CLAUDE.md
grep -c "trim_timeseries_rows" CLAUDE.md
```
EXPECT: All return 0

---

## Acceptance Criteria

- [ ] CLAUDE.md ≤10,000 characters (~2,500 tokens)
- [ ] Developer Reference 完整迁移至 docs/DEVELOPER.md
- [ ] "强制 ASIN 补全" 指令已移除
- [ ] Webapp trim 细节已移除
- [ ] Keepa API 禁止指令合并为 1 段
- [ ] 示例从 11 个压缩至 3 个
- [ ] 所有核心行为指令保留（Decision Tree, API envelope, price encoding, phase protocol）
- [ ] 全量测试通过（零回归）
- [ ] 新增 CLAUDE.md 大小回归测试

## Completion Checklist

- [ ] Decision Tree 完整保留
- [ ] API envelope 格式说明保留
- [ ] 3 个核心示例覆盖查询/注册/管理三类场景
- [ ] Key Behaviors 全部精简但核心信息不丢失
- [ ] Developer Reference 在 docs/DEVELOPER.md 中完整
- [ ] 编号连续无遗漏
- [ ] 自包含——实现时不需要搜索代码库或提问

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CLAUDE.md 压缩过度导致 Claude Code 行为异常 | Low | High | 保留 Decision Tree + 3 示例 + 核心 Key Behaviors；压缩后手动验证典型查询 |
| Developer Reference 外迁后新开发者找不到 | Low | Low | CLAUDE.md 中保留指向 docs/DEVELOPER.md 的链接 |
| 移除 ASIN 补全指令后 Claude Code 不再自动补全 | Expected | None | 这正是目标——Phase 2 的 EAN/UPC 绑定替代此行为 |
| 测试遗漏某个被删除指令的副作用 | Low | Medium | 全量 `pytest` + 手动验证 3 个典型查询场景 |

## Notes

### CLAUDE.md Section-by-Section 压缩计划

| Section | Current Lines | Action | Target Lines | Chars Saved |
|---|---|---|---|---|
| Header + Decision Tree | 41 | Keep (slight trim) | 35 | ~300 |
| Calling the API | 28 | Compress imports | 12 | ~600 |
| Examples | 83 | 11→3 examples | 25 | ~3,000 |
| Key Behaviors #1-#11 | 45 | Compress each to 1 line | 20 | ~1,200 |
| Key Behavior #12 (ASIN补全) | 33 | **REMOVE** | 0 | ~2,000 |
| Key Behavior #13 (Keepa) | 5 | Compress | 2 | ~300 |
| Key Behavior #14 (tags) | 1 | Keep | 1 | 0 |
| Key Behavior #15 (trim) | 18 | **REMOVE** | 0 | ~1,200 |
| Available Projects | 6 | Keep | 4 | ~100 |
| Developer Reference | 132 | **MOVE to docs/** | 2 (link) | ~7,500 |
| **Total** | **373** | | **~100** | **~16,200** |

### 关键保留内容

以下内容是 Claude Code LLM 正确翻译用户查询的**必要上下文**，不可移除：

1. **Decision Tree** — 自然语言→API 函数的映射
2. **API envelope 格式** — 理解返回值结构
3. **Price encoding 规则** — 正确解读 Keepa 数据（÷100, ×10, -1）
4. **Phase 响应协议** — 处理 needs_confirmation / pending_confirmation
5. **Auto-fetch 行为** — 知道哪些函数自动 fetch、哪些不会
6. **Marketplace aliases** — 知道 "uk" = "GB" = "amazon.co.uk"
7. **Product resolution** — 4 级 fallback 的存在
8. **绝不猜测 ASIN** — 安全护栏
