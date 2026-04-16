# amz-scout 瘦身重构：Token 优化 + 跨市场绑定 + 查询直通

## Problem Statement

amz-scout 的 webapp（Chainlit + Claude Sonnet function calling）在每次对话中消耗过多 LLM token，主要来源是：(1) CLAUDE.md 每轮注入 ~5,287 tokens 的固定开销，(2) "强制 ASIN 补全"指令迫使 AI 对每个新产品执行 11 次 WebSearch（每次消耗大量 token 解析搜索结果），(3) AI 会"自觉"读取和分析 raw data，而用户大部分时候只需要查询数据、不需要 AI 解读。这三个问题导致 Anthropic API 月费偏高，阻碍了 webapp 的内部推广。

## Evidence

- **CLAUDE.md token 审计**：373 行 / ~5,287 tokens，每轮对话（含 tool schemas ~1,100 tokens）固定消耗 ~6,480 tokens。其中"新产品后台 ASIN 补全"规则占 33 行，"禁止直接调用 Keepa API"警告重复 3 次。
- **WebSearch 浪费**：用户用 ASIN 直接查询的成本约 \$0.03，但新产品触发强制 ASIN 补全后，11 个市场 × WebSearch 解析 = 显著 token 开销，且搜索结果经常不准确。
- **EAN/UPC 验证**：在实际 raw JSON 中确认同一物理产品（如 B09K7SRMX4 Peplink BR1 Pro）在 UK/US/CA 共享相同 EAN `710859517771`。该数据**已存储在 `keepa_products.ean_list` 中**，可零成本替代 WebSearch 做跨市场绑定。
- **PM 访谈洞察**（来自 web PRD Phase 3）：小李的典型场景是查数据 + 导出 Excel 分析，不需要 AI 逐条解读数据。"查询"占日常使用的 ~80%。
- **owner 观察**：有时用户只需要获取数据，AI 却主动读 raw data 并生成分析，消耗大量不必要的 output tokens。

## Proposed Solution

三管齐下：

1. **CLAUDE.md 瘦身 50%+**：移除重复指令、压缩示例、去掉已内化的设计决策，将固定 token 开销从 ~5,287 降至 ~2,500。

2. **EAN/UPC 自动绑定替代 WebSearch 补全**：Keepa 数据写入 DB 时，用 `ean_list` / `upc_list` 自动匹配已注册产品，将新 ASIN 绑定到同一 `product_id`。零 token 成本，零 WebSearch 调用。移除 CLAUDE.md 中"强制 ASIN 补全"指令。

3. **查询直通模式**：webapp 的"查询"模式下，AI 只做 NL → API call 的翻译，返回数据摘要（"已查询 87 条记录"）+ 可下载的 Excel/CSV 附件。AI 不读取 raw data，token 消耗仅为翻译成本。同时为未来的"项目"分析模式预留 session-level 查询记录接口。

## Key Hypothesis

**We believe** CLAUDE.md 瘦身 + EAN/UPC 自动绑定 + 查询直通模式
**will** 将 webapp 的 per-query LLM token 消耗降低 60%+，同时保持查询准确性和用户体验不变，
**for** 6 名 GL.iNet 内部用户（5 PM + Jack）。

**We'll know we're right when**：Anthropic API 月费在 webapp 正常使用后显著降低（对比瘦身前后同类查询的 token 消耗）。

## What We're NOT Building

| Out of Scope | Why |
|---|---|
| "项目"分析模式的完整设计 | 需要专门的 PRD，本次只预留接口和数据基础 |
| api.py / db.py / cli.py 代码拆分 | 收益有限且风险高，瘦身 ROI 集中在 CLAUDE.md 和行为指令上 |
| DB schema 核心结构变更（products / product_asins 表） | 现有结构已验证正确，不需要改 |
| Keepa 获取逻辑变更 | 现有策略（lazy/fresh/offline）已经合理 |
| webapp UI 重设计 | 不在本次范围内 |
| 新增 Keepa API 调用来获取跨市场数据 | EAN/UPC 已在现有数据中，零额外成本 |

## Success Metrics

| Metric | Target | How Measured |
|---|---|---|
| CLAUDE.md token 占用 | 从 ~5,287 降至 ≤2,500 tokens | 字符数 / 4（英文）或 / 2（中文） |
| 新产品注册的 WebSearch 调用 | 从 11 次/产品 降至 0 | CLAUDE.md 指令移除 + EAN/UPC 替代 |
| 查询模式 per-query output tokens | 降低 60%+ | 对比瘦身前后同类查询的 Anthropic usage |
| EAN/UPC 自动绑定准确率 | ≥95%（与手动注册一致） | 对比已注册产品的 EAN 匹配结果 |
| 查询功能完整性 | 100%（零功能回归） | 现有测试套件全部通过 |

## Open Questions

- [ ] **Q1 — EAN 覆盖率**：有多少产品没有 EAN/UPC（如 Amazon 自有品牌、白牌产品）？需要抽样验证 keepa_products 表中 ean_list 非空的比例。如果覆盖率不足，需要保留 WebSearch 作为 fallback。
- [ ] **Q2 — EAN 跨品牌冲突**：是否存在不同品牌的不同产品共享同一 EAN 的情况（如 OEM/贴牌）？需要验证匹配逻辑是否需要额外的 brand 校验。
- [ ] **Q3 — 查询直通的 Excel 格式**：全量数据导出时应该包含哪些字段？是否沿用 `_llm_trim` 的白名单，还是导出完整 DB 行？
- [ ] **Q4 — Session 查询记录的持久化**：当前方案是内存中的 per-session 记录。如果用户关闭浏览器再回来，查询记录是否需要保留？这影响未来"项目"模式的设计。

---

## Users & Context

**Primary User**

- **Who**: Jack（amz-scout owner + 唯一开发者）+ 5 名 GL.iNet PM/市场分析师
- **Current behavior**: 通过 webapp 用自然语言查询 Amazon 产品数据，AI 翻译为 API 调用并返回结果
- **Trigger**: 发现 Anthropic API 月费偏高，且 AI 在查询模式下做了不必要的数据解读
- **Success state**: 同样的查询操作，token 消耗降低 60%+，查询体验不变

**Job to Be Done**

> When 我在 webapp 中查询产品数据时，I want to 只让 AI 翻译我的自然语言为查询指令、返回数据摘要和下载链接，so that 我不需要为 AI 读取和分析 raw data 付出不必要的 token 成本，同时保留未来让 AI 深度分析的能力。

**Non-Users**

不影响 CLI 用户（CLI 不经过 LLM，不消耗 Anthropic token）。

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|---|---|---|
| **Must** | CLAUDE.md 压缩至 ≤2,500 tokens | 最大杠杆：每轮省 ~2,787 tokens |
| **Must** | 移除"强制 ASIN 补全"指令 | 消除新产品注册时的 WebSearch token 浪费 |
| **Must** | EAN/UPC 自动绑定：Keepa 数据写入时匹配已注册产品 | 零成本替代 WebSearch 做跨市场绑定 |
| **Must** | 查询直通模式：AI 返回摘要 + cl.File 下载，不读 raw data | 降低 60%+ output tokens |
| **Should** | Session-level 查询记录（内存）：记录本次会话中用户查了什么 | 为未来"项目"模式预留数据索引接口 |
| **Should** | webapp tool docstrings 压缩 | 省 ~250 tokens/轮 |
| **Could** | 按需 ASIN 发现：用户主动说"帮我找其他市场"时才触发 WebSearch | EAN 绑定 fallback |
| **Won't** | "项目"分析模式完整实现 | 留给专门 PRD |
| **Won't** | 代码文件拆分（api.py / db.py） | ROI 低 |
| **Won't** | DB schema 核心结构变更 | 不需要 |

### MVP Scope

**最小可验证交付物**：

1. CLAUDE.md 从 ~5,287 tokens 压缩至 ≤2,500 tokens，所有现有测试通过
2. `_auto_register_from_keepa()` 新增 EAN/UPC 匹配逻辑，自动绑定跨市场产品
3. webapp 查询工具返回摘要 + Excel 下载，AI 不读 raw data
4. CLAUDE.md 中移除"强制 ASIN 补全"指令

**验证方式**：对比瘦身前后同一查询（如"查 BE3600 UK 价格历史"）的 token 消耗。

### 查询直通模式 User Flow

1. 用户输入：*"查 BE3600 在英国的价格历史"*
2. AI 翻译为：`query_trends(product="BE3600", marketplace="UK", series="new")`
3. API 执行，返回 87 条记录
4. AI 返回给用户：

   > 已查询 GL-RT3000 (BE3600) 在 UK 的价格历史数据。
   > - 记录数：87 条
   > - 时间范围：2025-07-01 至 2026-04-15
   > - [下载完整数据 (Excel)]

5. 同时在 session 查询记录中保存：`{product: "BE3600", marketplace: "UK", series: "new", count: 87}`
6. AI **不读取**这 87 条记录的具体内容（零 output token 浪费）
7. 用户可下载 Excel 查看全量数据，或在未来的"项目"模式中让 AI 分析

### EAN/UPC 自动绑定 Flow

```
Keepa 数据写入 DB
  |
  +-- _auto_register_from_keepa() 触发
  |
  +-- 检查 ASIN 是否已注册 --> 是 --> 跳过
  |
  +-- 提取 ean_list / upc_list
  |
  +-- 查询 keepa_products 表：是否有其他 ASIN 共享相同 EAN？
  |   |
  |   +-- 找到匹配 --> 获取对应的 product_id
  |   |   +-- 将当前 ASIN 绑定到该 product_id（INSERT product_asins）
  |   |
  |   +-- 未找到 --> 走原有逻辑（用 brand+title 注册新产品）
  |
  +-- 日志记录绑定结果
```

---

## Technical Approach

**Feasibility**: **HIGH**。三个优化都是增量修改，不涉及核心架构变更。

### Architecture Notes

**CLAUDE.md 瘦身策略**：
- 合并重复指令（"禁止 Keepa API"出现 3 次 → 1 次）
- 移除"新产品后台 ASIN 补全"整个 section（33 行）
- 压缩示例代码（保留 2-3 个核心示例，移除冗余的中英双语重复）
- 移除已内化的设计决策描述（如 4 级解析的详细说明）
- 将 Developer Reference 移到 README.md 或单独文件（开发者信息不需要每轮对话加载）

**EAN/UPC 绑定实现**：
- 修改 `db.py::_auto_register_from_keepa()`，在 brand+title 匹配之前先做 EAN 匹配
- 匹配逻辑：查询 `keepa_products` 中共享相同 EAN 的其他 ASIN，找到对应 product_id
- 匹配到 → 直接 `INSERT INTO product_asins (product_id, marketplace, asin)`
- 需要处理 EAN 为 JSON 数组格式的匹配（`json_each()` 或 Python 侧解析）

**查询直通实现**：
- 修改 `webapp/tools.py` 的 `_step_*` 包装器
- 返回给 LLM 的内容从 `trimmed_data` 改为 `{count: N, date_range: "...", file_attached: True}`
- 同时生成 `cl.File`（Excel/CSV）附加到回复
- Session 查询记录：在 Chainlit `user_session` 中维护 `query_log` 列表

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **EAN 覆盖率不足** | Medium | Medium | 抽样验证（Open Q1）；保留 brand+title fallback 路径 |
| **EAN 跨品牌冲突（OEM/贴牌）** | Low | Medium | 额外校验 brand 字段（Open Q2） |
| **CLAUDE.md 压缩过度导致 AI 行为异常** | Low | High | 逐步压缩 + 回归测试；保留关键行为指令 |
| **查询直通后用户体验下降** | Low | Medium | 保持摘要信息量充足；Excel 包含全量数据 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|---|---|---|---|---|---|
| 1 | **CLAUDE.md 瘦身** | 压缩至 ≤2,500 tokens，移除重复指令、强制 ASIN 补全、冗余示例；Developer Reference 外迁 | in-progress | with 2 | - | [plan](.claude/PRPs/plans/claude-md-slim-phase1.plan.md) |
| 2 | **EAN/UPC 自动绑定** | 修改 `_auto_register_from_keepa()` 添加 EAN 匹配逻辑；验证覆盖率；添加测试 | in-progress | with 1 | - | [plan](.claude/PRPs/plans/ean-upc-auto-binding.plan.md) |
| 3 | **查询直通模式** | 修改 webapp tool wrappers 返回摘要 + cl.File；添加 session 查询记录；压缩 tool docstrings | pending | - | 1 | - |
| 4 | **验证 + 回归测试** | 对比瘦身前后 token 消耗；全量测试套件；手动验证关键查询场景 | pending | - | 1, 2, 3 | - |

### Phase Details

**Phase 1: CLAUDE.md 瘦身** (~3h)
- **Goal**: 固定 token 开销降低 50%+
- **Scope**:
  - 合并重复指令（Keepa API 警告 3→1、ASIN 补全 33 行→移除）
  - 压缩示例（保留 3 个核心示例，移除冗余的中英双语重复）
  - Developer Reference（Architecture、Commands、Config）移至 `docs/DEVELOPER.md`
  - 保留：Decision Tree、API Calling、Key Behaviors 的精简版本
- **Success signal**: `wc -c CLAUDE.md` 字符数 ≤10,000（当前 ~21,000）

**Phase 2: EAN/UPC 自动绑定** (~4h)
- **Goal**: 零 token 成本的跨市场产品绑定
- **Scope**:
  - 修改 `db.py::_auto_register_from_keepa()`：先 EAN 匹配 → 再 brand+title fallback
  - 新增 `db.py::_find_product_by_ean()` 查询函数
  - 验证 `keepa_products.ean_list` 覆盖率（抽样 50 个产品）
  - 添加 `tests/test_core_flows.py` 的 EAN 绑定测试
  - 更新 CLAUDE.md（Phase 1 中同步完成）
- **Success signal**: 对已有的跨市场产品（如 B09K7SRMX4），EAN 匹配正确绑定到同一 product_id
- **与 Phase 1 并行**

**Phase 3: 查询直通模式** (~4h)
- **Goal**: 查询模式 output tokens 降低 60%+
- **Scope**:
  - 修改 `webapp/tools.py`：`_step_*` 返回摘要 dict 而非 trimmed data
  - 摘要格式：`{ok, count, date_range, product, marketplace, file_attached}`
  - 生成 `cl.File`（Excel/CSV）附加到每个查询回复
  - 在 `cl.user_session` 中维护 `query_log` 列表（为"项目"模式预留）
  - 压缩 tool schema docstrings（-250 tokens）
- **Success signal**: 同一查询（"查 BE3600 UK 价格历史"）的 output tokens 降低 60%+
- **依赖 Phase 1**（CLAUDE.md 指令需要先更新）

**Phase 4: 验证 + 回归测试** (~2h)
- **Goal**: 确认零功能回归 + token 节省达标
- **Scope**:
  - `pytest` 全量通过
  - 手动对比 3 个典型查询的 token 消耗（before/after）
  - 验证 EAN 绑定不产生误匹配（抽样 10 个产品）
  - 验证 CLI 不受影响（CLI 不经过 LLM）
- **Success signal**: 所有测试通过 + per-query token 降低 >=50%

### Parallelism Notes

- **Phase 1 和 Phase 2 可并行**：CLAUDE.md 修改和 db.py EAN 逻辑互不影响
- **Phase 3 依赖 Phase 1**：webapp tool wrappers 的行为需要与 CLAUDE.md 指令一致
- **Phase 4 在所有其他 Phase 完成后**
- **Critical path**: `(1 || 2) -> 3 -> 4`
- **Total estimate**: ~13 hours / ~4 天 at 3h/天

---

## Decisions Log

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| 跨市场绑定方式 | EAN/UPC 自动匹配 | WebSearch（现有）、parentAsin、手动注册 | EAN 全球唯一、已在 DB 中、零 token 成本；parentAsin 按区域不同不可靠 |
| CLAUDE.md 压缩策略 | 移除冗余 + Developer Reference 外迁 | 全面重写、拆分多文件 | 增量修改风险最低；外迁信息仍可访问 |
| 查询直通 vs AI 分析 | 查询返回摘要，分析留给"项目"模式 | 所有查询都 AI 分析、用户手动选择模式 | 80% 场景是纯查询，省 60%+ tokens |
| 不拆分大文件 | 保持 api.py/db.py 不变 | 按功能拆分为多模块 | 代码内聚度高，拆分 ROI 低且风险高 |
| "项目"模式 | 本次只预留接口，不实现 | 同时实现查询 + 项目 | 需要专门 PRD，避免范围蔓延 |
| Session 查询记录存储 | 内存（per-session, per-user） | SQLite 临时表、Redis | 6 用户规模不需要持久化；内存最简单 |
| Excel 导出内容 | 全量 DB 字段（不经过 _llm_trim） | 仅 _llm_trim 白名单字段 | AI 不读此数据，无 token 成本；用户需要全量信息 |

---

## Research Summary

### Token 消耗审计

| 来源 | 当前 (tokens/轮) | 优化后 | 节省 |
|------|------------------|--------|------|
| CLAUDE.md | ~5,287 | ~2,500 | 53% |
| Tool schemas | ~1,100 | ~850 | 23% |
| System prompt | ~93 | ~93 | 0% |
| Query output (avg) | ~650 (trimmed) | ~50 (summary only) | 92% |
| **固定开销合计** | **~6,480** | **~3,443** | **47%** |

### EAN/UPC 跨市场验证

| 产品 | ASIN | UK EAN | US EAN | DE EAN | JP EAN | 匹配 |
|------|------|--------|--------|--------|--------|------|
| Peplink BR1 Pro | B09K7SRMX4 | 710859517771 | 710859517771 | - | - | Yes |
| TP-Link Switch | B08VH4Q3NR | - | - | 6935364052881 | 6935364052881 | Yes |

**parentAsin 验证（不可靠）**：同一产品 B08VH4Q3NR 在 DE 的 parentAsin 是 B0CLL6N3PZ，在 JP 是 B09WZD74XP — 不同区域不同值，不适合跨市场绑定。

### 产品身份模型评估

| 维度 | 评价 | 本次是否修改 |
|------|------|-------------|
| `products` 表结构 | OK: (brand, model) UNIQUE | 不改 |
| `product_asins` 表结构 | OK: (product_id, marketplace) PK | 不改 |
| 4 级解析 | OK: DB -> Config -> ASIN -> Error | 不改 |
| 自动注册（brand+title） | OK: 保守合理 | 增加 EAN 优先匹配 |
| WebSearch 补全 | Problem: 过于激进 | 移除强制，改为按需 |

### 与 Web PRD 的关系

本 PRD 是 [internal-amz-scout-web.prd.md](internal-amz-scout-web.prd.md) 的**补充优化**，不改变 web PRD 的架构决策。两个 PRD 的关系：

- **Web PRD** 定义了 webapp 的功能范围（what to build）
- **本 PRD** 优化了 webapp 的运营成本（how to run efficiently）
- 本 PRD 的 Phase 3（查询直通）修改 webapp 代码，但不改变功能——用户仍能查询所有数据，只是 AI 不再逐条解读
- 本 PRD 为未来的"项目分析模式"预留接口（session 查询记录），该模式将有独立 PRD

---

*Generated: 2026-04-16*
*Status: DRAFT — awaiting Q1/Q2 验证（EAN 覆盖率 + 跨品牌冲突检查）*
