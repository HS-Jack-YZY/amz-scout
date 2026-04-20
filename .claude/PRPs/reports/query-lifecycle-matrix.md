# 查询生命周期矩阵（Query Lifecycle Matrix）

> 目的：枚举用户发起一次查询时所有可能路径，对齐当前代码实现，定位 gap。
> 生成日期：2026-04-17
> 状态：DRAFT — 审计报告（非 PRD，非 plan）
> 触发：Jack 询问"用户查询生命线所有情况是否都覆盖到了"，要求可视化覆盖表

---

## 1. 两阶段拆分

用户的任何一次查询都可以拆成两个独立阶段：

```
用户输入 ─┬─> [Phase A: Resolution] ─> (ASIN, market) ─> [Phase B: Fetch] ─> 结果
          │
          输入类型:
          ├─ product_name: "Slate 7" / "GL-AX1800"
          ├─ raw_asin:     "B0ABCDEFGH"
          └─ (未来) multi: ["Slate 7", "B0XXX"]
```

Phase A 和 Phase B 有各自的分支。拆开审计后分支数从组合爆炸（~144）降到 ~20。

---

## 2. Phase A — ASIN 解析

入口：`_resolve_asin` @ `src/amz_scout/api.py:216–302`

### 2.1 输入是产品名

```
product_name
  │
  ├─ [A1] DB 注册表 LIKE 匹配
  │   └─ find_product() @ db.py:1572–1619
  │       匹配字段: products.model / search_keywords / keepa_products.title
  │       marketplace 过滤在 JOIN ON (commit 7b4f18b)
  │
  ├─ [A2] Config YAML 产品列表 substring (旧路径, project=... 时)
  │
  └─ [A3] 全部失败 → ValueError
      ❌ 不会自动 WebSearch — WebSearch 是 Claude 手动兜底，不在代码路径
```

| 分支 | 当前行为 | Gap |
|------|---------|-----|
| A1a 命中单条，指定市场有 ASIN | 返回 ASIN | OK |
| A1b 命中单条，指定市场无 ASIN | 警告 + 列出其他市场已知 ASIN + 建议 `discover_asin` | 无自动跨市场 ASIN 复用（即便 EAN 相同） |
| A1c 命中多条 | 取第一条 | 应警告歧义或要求消歧 |
| A2 命中 config | 返回 ASIN | OK（遗留路径） |
| A3 全部失败 | `ValueError` | 未触发 WebSearch 或 `discover_asin` 自动化 |

### 2.2 输入是 ASIN (`^[A-Z0-9]{10}$`)

```
asin
  │
  ├─ [B1] DB product_asins 匹配
  │   ├─ asin + market 命中 → 直接用
  │   ├─ asin 存在但属于其他 market → warning, 继续用此 asin
  │   └─ asin 完全不在 DB → 追加 warning "will be auto-registered"
  │
  └─ [B2] 继续下一步 (不会因 DB miss 而失败)
      实际注册发生在 Phase B auto-fetch 成功后
```

| 分支 | 当前行为 | Gap |
|------|---------|-----|
| B1a ASIN 在 DB + 市场对 | 用此 ASIN | OK |
| B1b ASIN 在 DB 但市场不对 | 警告但继续用该 ASIN (api.py:276–289) | **沉默失败风险**：通常 Keepa 查不到数据，返回空，无 error |
| B1c ASIN 不在 DB | 进入 pass-through, auto-fetch 阶段再注册 | OK |
| B1d ASIN 格式正确但 Amazon 不存在 | Keepa 返回空 → 标记 `not_listed` (api.py:354–376) | 无显式 error，只有 `meta["hint"]` |
| B1e ASIN 状态为 `wrong_product` | 仍会被用 | 未检查 validation 状态 |

---

## 3. Phase B — 数据获取

入口：各 `query_*` 函数 @ `src/amz_scout/api.py`

### 3.1 查询函数分类

| 函数 | 数据源 | auto_fetch | 单/多市场 |
|------|--------|-----------|----------|
| `query_trends` | `keepa_products` + `keepa_prices_history` | default True | 单 |
| `query_sellers` | `keepa_sellers_history` | default True | 单 |
| `query_deals` | `keepa_deals_snapshots` | default True | 单 |
| `query_latest` | `competitive_snapshots` | 仅读 | 单 |
| `query_compare` | `competitive_snapshots` | 仅读 | 单产品跨市场 |
| `query_ranking` | `competitive_snapshots` | 仅读 | 单 |
| `query_availability` | `competitive_snapshots` | 仅读 | 全部 |

### 3.2 Keepa auto-fetch 分支（query_trends / sellers / deals）

```
(asin, market)
  │
  ├─ [C1] DB cache fresh? (当前无新鲜度闸门)
  │   └─ 直接返回 cache (即便过期)
  │
  ├─ [C2] 触发 ensure_keepa_data()
  │   ├─ 预估 token ≥ 6 → 返回 phase="needs_confirmation"
  │   ├─ Keepa 不支持该市场 → 静默跳过
  │   ├─ token 不够 → error
  │   ├─ fetch 成功 → auto-register (EAN/UPC bind 或新建)
  │   └─ fetch 失败 → meta["auto_fetch_error"]=True + 尝试返回旧 cache
  │
  └─ 返回 data
```

| 分支 | 当前行为 | Gap |
|------|---------|-----|
| C1 DB 有 cache 但过期 | 直接返回 | **缺少新鲜度闸门**，可能返回数月旧数据 |
| C2a token 不够 | phase gate 生效 | OK |
| C2b 市场不支持 | 静默跳过，返回空 | 应返回显式 error "marketplace not supported by Keepa" |
| C2c fetch 后发现 brand 错配 | 仍 auto-register 为新产品 | 延后到 `validate_asins` 才发现 |

### 3.3 snapshot-only 分支（query_latest / compare / ranking / availability）

```
(asin, market)
  │
  └─ 直接 SELECT competitive_snapshots
      ├─ 命中 → 返回
      └─ 未命中 → 空 list + meta["hint"]="Run 'amz-scout scrape'"
```

| 分支 | 当前行为 | Gap |
|------|---------|-----|
| D1 无快照 | 空 + hint | OK |
| D2 有快照但过期 | 仍返回 | 无新鲜度过滤 |
| D3 Keepa 有数据但 snapshot 无 | 返回空（仅读 snapshot） | 设计如此，但用户易困惑 |

---

## 4. 多市场查询

**现状**：所有 `query_*` 签名都是 `marketplace: str`，**没有 batch API**。

用户如果想查多市场：
```python
for mp in ["UK", "DE", "JP"]:
    r = query_trends(product="Slate 7", marketplace=mp)
```

| 分支 | 当前行为 | Gap |
|------|---------|-----|
| E1 DB 全部有 | 按顺序返回每个市场 | 无并发 |
| E2 DB 部分有 | 部分返回 cache，部分 auto-fetch | 串行 Keepa fetch 慢 |
| E3 DB 全部无 | 每个市场都 auto-fetch | 可能连续触发 `needs_confirmation` phase |

---

## 5. 完整决策表（输入 → 输出）

下表用 `sku=产品名` 和 `asin=10 字符正则匹配`，枚举每种组合的最终行为。

| # | 输入 | DB 状态 | Keepa 状态 | 当前行为 | Gap |
|---|------|---------|-----------|---------|-----|
| 1 | sku | 命中+市场匹配 | fresh | 返回 cache | 无新鲜度闸门 |
| 2 | sku | 命中+市场匹配 | missing | auto-fetch → 返回 | OK |
| 3 | sku | 命中+市场无ASIN | any | warning + 列其他市场 ASIN + 建议 discover | 能否用 EAN/UPC 自动跨市场复用？ |
| 4 | sku | 命中多条 | any | 取第一条 | 应要求消歧 |
| 5 | sku | 不命中 | n/a | `ValueError` | 无 WebSearch fallback |
| 6 | asin | 在DB+市场对 | fresh | 返回 cache | 同 #1 |
| 7 | asin | 在DB+市场对 | missing | auto-fetch → 返回 | OK |
| 8 | asin | 在DB但市场不对 | any | warning + 用此 asin → 通常空 | **沉默失败** |
| 9 | asin | 不在DB | Keepa 有 | auto-fetch + EAN/UPC bind 或新建产品 | OK |
| 10 | asin | 不在DB | Keepa 无 | 标记 `not_listed` + 返回空 | 无显式 error |
| 11 | asin | 不在DB | Keepa 不支持市场 | 静默跳过 | 应显式 error |
| 12 | asin | status=wrong_product | any | 仍用 | 未检查 validation |
| 13 | asin | 正则挂 (9/11 字符) | n/a | 落到 sku 路径 → #5 `ValueError` | 用户可能以为在查 ASIN |
| 14 | 多市场 sku | 部分命中 | mixed | loop 返回，每市场独立走 #1–#5 | 无 batch API |
| 15 | 多市场 asin | 跨市场 asin 相同 (GL.iNet) | any | 每市场独立走 #6–#11 | 同一 ASIN 多市场重复 fetch |
| 16 | 多市场 asin | 各市场 asin 不同 | any | 需要用户手动传每市场 asin | 无"给一个市场的 asin 推出其他市场"的能力（除非 EAN/UPC 已绑定） |

---

## 6. 核心 Gap 清单（按风险排序）

### P0 — 沉默失败 / 数据错误

1. **#8 ASIN 市场错配**：warning 后继续用错 asin 查询，返回空无 error，用户可能以为该市场没这产品。
   - 建议：`strict` 模式下直接 raise；否则在 meta 里加 `asin_mismatch=True`。
2. **#12 wrong_product 状态未检查**：`validate_asins` 已标记 `wrong_product` 的 ASIN 仍会被查询使用。
   - 建议：Phase A 读取 `status` 字段，`wrong_product` 直接拒绝。
3. **#1/#6 无新鲜度闸门**：DB cache 任意过期都原样返回。
   - 建议：引入 `max_age` 参数（默认 24h / 7d），过期触发 refresh。

### P1 — 用户体验

4. **#5 sku 不命中无 fallback**：直接 `ValueError`，既不 WebSearch 也不 `discover_asin`。
   - 建议：返回结构化错误 `{code: "NOT_IN_REGISTRY", suggestions: [discover_asin, import_yaml, add_product]}`。
5. **#4 sku 命中多条**：静默取第一条。
   - 建议：返回 `MULTIPLE_MATCHES` 错误 + 候选列表。
6. **#11 Keepa 不支持市场**：静默跳过。
   - 建议：显式 `UNSUPPORTED_MARKETPLACE` 错误。

### P2 — 功能缺失

7. **#14/#15/#16 无 batch API**：多市场查询必须调用方 loop。
8. **#16 跨市场 ASIN 推导**：除 EAN/UPC 自动绑定外，无"给 UK ASIN 推 DE ASIN"的显式 API。

### P3 — 一致性 / 可观测

9. **#13 ASIN 正则挂**：11 字符的 ASIN（输入错一位）当 sku 处理，失败信息误导。
    - 建议：检查"是否形似 ASIN 但长度错"，给专门错误。
10. **auto-register brand 错配**：Keepa 返回 brand 与用户意图不同仍新建产品，延后到 validate 才发现。

---

## 7. 下一步建议

**不建议**：立刻走 `/prp-prd` 流程写一份单一 PRD。这不是单一功能，是一组互相独立的 API 行为修复。

**建议**：
1. 本文档定稿 → 作为 audit baseline。
2. 把上面 10 条 Gap 按 P0/P1 分组，每组生成一个 **小型 PRP**（`/prp-plan`），独立验收。
3. **优先 P0-1, P0-2, P0-3**：这三条是"静默错误数据"，不做就会导致 BE10000 类分析结果错误而不自知。
4. P2-7（batch API）可以等 MVP 上线后再迭代，当前 loop 虽慢但可用。

---

## 8. 对 Jack 原始"枚举表"的回复

Jack 原枚举的 2×3×2 = 12 组合方向正确，但遗漏了以下维度：

- ASIN 归属维度（registered_correct / wrong_market / not_registered / wrong_product）
- Keepa 支持维度（supports market / doesn't support）
- DB 新鲜度维度（fresh / stale）
- 验证状态维度（unverified / verified / wrong_product / not_listed）
- Phase gate 维度（needs_confirmation / pending_confirmation / no gate）

展开后就是上面 §5 的 16 行决策表。
