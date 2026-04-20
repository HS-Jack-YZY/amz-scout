# 已知问题 — 暂延后至上线之后

> **状态**：已接受的技术债，2026-04-17 记录。是否阻塞上线？**否**（当前"单产品、单 canonical ASIN"流程下可接受）。必须修复的时机：扩展到有多市场 listing 的竞品产品之前。

## 严重程度图例

- 🔴 **数据丢失风险** — 静默破坏既有状态
- 🟠 **正确性风险** — 返回误导结果，但原始数据可恢复
- 🟡 **体验风险** — 行为出乎意料，用户可自行绕开

---

## Issue #1 — Auto-register 静默覆盖现有 `(product_id, marketplace)` 注册 🔴

### 症状

当 `query_trends(product=<ASIN>, marketplace=<X>)` 自动 fetch 一个新 ASIN 且 Keepa 返回匹配的 title 时，系统对 UNIQUE `(product_id, marketplace)` 键执行 `INSERT OR REPLACE INTO product_asins`。如果该槽位已经被另一个 ASIN 占用（可能是人工校验过的），旧注册**被直接删除，无告警、无审计轨迹**。

### 复现（2026-04-17 已验证）

`product_id=2`（TP-Link Archer BE400）的初始注册：

| market | asin | status |
|--------|------|--------|
| CA | B0DSC928WF | active |
| UK | **B0DSJRDLGP** | active |
| US | B0DVBP5L6Y | active |

调用：`query_trends(product="B0DSC928WF", marketplace="UK")`

结果：
- Keepa 在 amazon.co.uk 为 `B0DSC928WF` 返回了有效 title（该产品在 UK 作为重复 listing 确实存在）
- Auto-register 触发 `INSERT OR REPLACE`
- **UK 槽位静默翻转**：`B0DSJRDLGP` → `B0DSC928WF`
- `B0DSJRDLGP` 完全从 `product_asins` 消失（没有 `inactive` 状态，没有历史行）

### 影响

- `B0DSJRDLGP` 原本有 **3880 行** `keepa_time_series` 数据（Keepa ts 7377688 → 8042862）。覆盖后它在 `keepa_time_series` 里仍然完整，只是**与 registry 脱钩** — 因此数据可恢复，但任何由 registry 驱动的 `check_freshness()` / `query_trends` / `query_compare` 现在都会指向错误 ASIN，显示 `count=1, value=-1` 而非真实价格历史
- DE 槽位：原本为空，没有发生覆盖 — 但 auto-register 仍然用低置信度匹配（4 行，全部 value=-1）占据了该槽位

### 根因

1. UNIQUE `(product_id, marketplace)` 上的 `INSERT OR REPLACE` 把任何冲突都当作"新值覆盖旧值"处理
2. Auto-register 把"Keepa 返回了 title"当作占据槽位的充分证据，忽略了 Amazon 普遍存在**同一产品、同一市场下多个并存 ASIN** 的现实（重复 listing、卖家 variation、地区合并）

### 修复方向（上线之后）

**L2（行为层，1–2 小时）**：改 auto-register 为拒绝覆盖：
- 若 `(product_id, marketplace)` 已被另一 ASIN 占用 → 写 warning，加入 `pending_markets`，**不触碰**既有行
- 需要显式调用 `update_product_asin()` 或 `register_market_asins(force=True)` 才能覆盖

**L3（结构层，半天）**：把 UNIQUE 约束改成 `(product_id, marketplace, asin)` + 增加 `is_primary BOOLEAN`。承认现实：一个产品在一个市场可以有多个 ASIN，但查询时只有一个 canonical primary。

### 恢复 SQL（上线后、首次查询 p2 UK/DE 之前执行）

```sql
-- 把 UK 槽位恢复为历史数据丰富的 ASIN
UPDATE product_asins SET asin='B0DSJRDLGP' WHERE product_id=2 AND marketplace='UK';

-- DE 槽位的处理（B0DSC928WF 目前占据，只有 4 行 value=-1）
-- 方案 A：删除，回到 pending 状态
DELETE FROM product_asins WHERE product_id=2 AND marketplace='DE' AND asin='B0DSC928WF';
-- 方案 B：保留，但先用 discover_asin("TP-Link", "Archer BE400", "DE") 验证
```

---

## Issue #2 — 跨市场查询时 ASIN pass-through 忽略产品身份 🟠

### 症状

当用户调用 `query_trends(product=<ASIN>, marketplace=<X>)`，同时满足：
- `<ASIN>` 在 DB 中已注册，但注册的是市场 `<Y>`（≠ `<X>`）
- 该 ASIN 对应的产品 `P`，在市场 `<X>` **另有**一个已注册 ASIN

…resolver 返回 `source="asin"` + warning，然后把**错误的 ASIN** 透传给 Keepa 到市场 `<X>` 查询，而不是从 registry 里取出 `P` 在 `<X>` 的真实 ASIN。

### 复现（2026-04-17 已验证）

Registry 状态：`p2|UK|B0DSJRDLGP`（UK 的 canonical ASIN，有 3880 行历史）。

调用：`resolve_product(query_str="B0DSC928WF", marketplace="UK")`

结果：
```
source: asin
model:  B0DSC928WF   ← 应该解析成 "Archer BE400"
asin:   B0DSC928WF   ← 应该返回 "B0DSJRDLGP"
warning: "B0DSC928WF is registered for [CA] ... you are querying UK. Same product may use a different ASIN on this marketplace."
```

Warning 准确地指出了风险，但 resolver **没有根据自己已有的信息采取行动** — 它已经知道 B0DSC928WF 属于哪个产品（`p2`），也已经有 `p2` 在 UK 的注册，却仍然 fallback 到 pass-through。

### 影响

- 用户用 CA/JP/DE 的 ASIN 去查其他市场时，会以错误 ASIN 发起 pass-through fetch
- 与 Issue #1 级联：pass-through fetch 若恰好返回匹配 title，**会覆盖正确的注册**
- 对于同一市场常见多 ASIN 并存的品类（配件、旅行路由器、TP-Link、Anker 等），查询可靠性会随着目录规模线性下降

### 修复方向（上线之后）

**L2**：在 `_resolve_asin()` 中，当 ASIN 匹配到 DB 行但查询的 marketplace 不同：
1. 从匹配行取出 `product_id`
2. 查询 `product_asins WHERE product_id=? AND marketplace=<target>`
3. 找到 → 返回该 ASIN，`source="db-cross-market"` + info warning（"已通过产品身份把 B0DSC928WF 翻译为 B0DSJRDLGP"）
4. 没找到 → 升级为当前 pass-through 行为，但写入 `pending_markets` 条目

---

## Issue #3 — Schema 假设 `(product, marketplace) → ASIN` 是 1:1，但 Amazon 现实是 1:N 🟡

### 观察（2026-04-17 已验证）

在 **amazon.fr** 上，`B0DSC928WF` 和 `B0DSJRDLGP` 都对应 Archer BE400 产品族。Keepa 为两者都返回有效产品数据。但 — 详见下面的"Addendum A" — 它们的 EAN 不同，所以更可能是 Amazon 在同一产品页下展示的两个不同 SKU/包装 variant，而非真正的重复 listing。

抛开 FR 这个具体案例，更大的现实模式依然成立：

- 产品在不同市场分阶段上架（先上的 ASIN 后来被跨市场铺货）
- 卖家自建的重复 listing 从未被合并
- 轻微 SKU 差异的品牌 variant 被当作独立 ASIN
- 区域 listing 被 Amazon 事后合并或拆分

### 当前 schema 假设

```sql
CREATE TABLE product_asins (
  product_id    INTEGER,
  marketplace   TEXT,
  asin          TEXT,
  ...
  UNIQUE(product_id, marketplace)   -- ← 假设 1:1
);
```

### 影响

- 强迫做"挑一个 ASIN"的决定，而 Amazon 本身并不强制
- 使 Issue #1 成为可能（REPLACE 语义的前提就是这个约束）
- 无法表达重复 / 次级 listing → 无法支持"在 amazon.fr 上这个产品的哪条 listing 拿到了 Buy Box"之类查询

### 修复方向（上线之后）

**L3 schema 演进**：
```sql
UNIQUE(product_id, marketplace, asin)
ADD COLUMN is_primary BOOLEAN DEFAULT 0

-- 部分唯一索引：每个 (product, market) 最多一个 primary
CREATE UNIQUE INDEX idx_one_primary_per_market
  ON product_asins(product_id, marketplace)
  WHERE is_primary = 1;
```

查询默认用 `is_primary=1`。展示层仍可列出同市场的兄弟 ASIN，保证透明。

---

## 当前 Registry 状态（截至 2026-04-17 16:30）

已经发生、需要上线后有意识处理的覆盖：

| product_id | market | 当前 ASIN | 原 ASIN | 需要的操作 |
|---|---|---|---|---|
| 2 | UK | B0DSC928WF | **B0DSJRDLGP** | 恢复 B0DSJRDLGP（3880 行历史） |
| 2 | DE | B0DSC928WF | （原本为空） | 通过 `discover_asin` 验证；B0DSC928WF 可能不是 DE 的 primary |

`keepa_time_series` 中的孤立数据（registry 指针已断，但数据完整）：
- `B0DSJRDLGP` @UK：3880 行
- `B0DSJRDLGP` @FR：9 行

**`keepa_time_series` 和 `keepa_products` 没有任何数据被删除** — 所有恢复都是纯 registry 层 SQL。

---

## Addendum A — Archer BE400 产品族的 EAN / UPC 指纹（参考数据）

2026-04-17 从 `keepa_products.ean_list` / `upc_list` 抓取。**单独记下来，防止 Keepa 重整或 Amazon 合并 listing 后丢失。**

| ASIN | Site | EAN（JSON 数组） | UPC（JSON 数组） | Keepa title 开头 |
|------|------|------------------|------------------|------------------|
| **B0DSC928WF** | CA | `["0810142822503"]` | `["810142822503"]` | TP-Link Dual-Band BE6500 WiFi 7 Router (Archer BE400) |
| **B0DSC928WF** | DE | `["0810142822503"]` | `["810142822503"]` | TP-Link BE6500 7 Dual Band WiFi Router (Archer BE400) |
| **B0DSC928WF** | FR | `["0810142822503"]` | `["810142822503"]` | TP-Link Routeur WiFi Double Bande BE6500 7 (Archer BE400) |
| **B0DSC928WF** | UK | `["0810142822503"]` | `["810142822503"]` | TP-Link BE6500 Dual-Band Wi-Fi 7 Router (Archer BE400) |
| B0DSC928WF | US | *(空)* | *(空)* | *(空 — amazon.com 未上架)* |
| **B0DSJRDLGP** | FR | `["1210002606721"]` | *(空)* | TP-Link Archer BE400, BE6500 routeur WiFi 7 Double Bande |
| **B0DSJRDLGP** | UK | `["1210002606721"]` | *(空)* | TP-Link Archer BE400, BE6500 Dual-Band WiFi 7 Router |
| **B0DVBP5L6Y** | US | `["0810142822220"]` | `["810142822220"]` | TP-Link BE6500 Dual-Band WiFi 7 Router (BE400) |

### 如何解读这些编码 — EAN 和 UPC 必须**同时**看（地区约定不同）

**先摆出编码事实**（GS1 标准）：
- **GTIN-13 (EAN) = "0" + GTIN-12 (UPC)**。UPC `810142822503` 和 EAN `0810142822503` 是**同一个条码**，只是显示宽度不同
- **美洲（US、CA、MX）**：GS1 US 分配 UPC-A（12 位）。厂家通常只注册 UPC。Keepa 常常双写该值 — 原始 UPC 写入 `upc_list`，`"0"+UPC` 写入 `ean_list`
- **欧洲 / 亚太 / 其他地区**：当地 GS1 办事处分配 EAN-13。`upc_list` 通常为空，因为该产品从未在美国 GS1 前缀段注册过

**不要单字段判断。** 匹配时必须归一化到统一形式。标准算法：

```python
def canonical_gtin(code: str) -> str:
    """左补零到 GTIN-14 再比较；无效返回 ''。"""
    code = (code or "").strip().lstrip("0")  # 去除前导零
    if not code.isdigit() or len(code) > 14:
        return ""
    return code.zfill(14)

def same_product(a_ean: list[str], a_upc: list[str],
                 b_ean: list[str], b_upc: list[str]) -> bool:
    a = {canonical_gtin(c) for c in (a_ean + a_upc) if c}
    b = {canonical_gtin(c) for c in (b_ean + b_upc) if c}
    a.discard(""); b.discard("")
    return bool(a & b)
```

### 归一化为 GTIN-14 后，其实是三个不同的产品

| Canonical GTIN-14 | 原始 EAN / UPC | ASINs | 市场 | 解读 |
|-------------------|----------------|-------|------|------|
| `00810142822503` | EAN `0810142822503` / UPC `810142822503` | **B0DSC928WF** | CA, DE, FR, UK | TP-Link 国际版 SKU — 同一个物理包装发往 CA 和 EU。EAN 和 UPC 是**同一个 GTIN** 的两种宽度，印证了 GS1 US 注册 |
| `00810142822220` | EAN `0810142822220` / UPC `810142822220` | **B0DVBP5L6Y** | US | TP-Link 的 US 专用 SKU（大概率是美式插头/FCC 标签 variant）。GTIN 与国际版不同 — **不是重复，而是独立产品** |
| `00001210002606721` ⚠️ | EAN `1210002606721` / UPC (无) | **B0DSJRDLGP** | FR, UK | **伪 GTIN** — 前缀 `121` 落在 GS1 保留段（120–139 未分配），不是合法国家码。无对应 UPC。几乎可以肯定是 Amazon 为没有注册 GTIN 的 listing 生成的占位符（卖家自建的重复 listing） |

### 回到那个问题："B0DSC928WF 和 B0DSJRDLGP 在 FR 是否同一产品？"

- 从 **消费者** 角度看 amazon.fr：Amazon 可能把它们合并到同一产品页、评论互通 → "同一产品"
- 从 **GS1 身份** 角度看：GTIN 集合交集为空（一个真实的国际 GTIN vs 一个伪 GTIN）。它们是**不同的 listing**，可能是不同的物理单元 — 带有合成码 `1210...` 的 `B0DSJRDLGP` 几乎可以断定是**卖家自有的重复 listing**，而不是 TP-Link 注册的 canonical SKU
- **结论**：身份匹配必须 (1) 同时把 EAN 和 UPC 归一化到 GTIN-14，(2) 排除非 GS1 前缀的伪 GTIN（`120–139`、`040–049` 等是保留段），之后 (3) 才做集合交集判断。Title 子串只能作为最后的 heuristic

### 这张表的用法

1. **执行恢复 SQL 之前**：确认被"恢复"的 ASIN 与产品族共享 canonical GTIN-14，而不仅仅是 EAN 字符串相等
2. **修 Issue #2 时**：当 registry 不完整时，GTIN 可以作为跨市场 ASIN 查找的替代手段。**两列都要建索引，两列都要查**（美洲站 UPC 可能是存储键，欧洲站则相反）：
   ```sql
   -- 通过 GTIN 做跨市场解析，EAN 和 UPC 对称处理
   SELECT asin, site FROM keepa_products
   WHERE ean_list LIKE '%810142822503%' OR upc_list LIKE '%810142822503%'
   ```
3. **修 Issue #3 时**：`is_primary` 应默认给 canonical GTIN-14 与产品注册到品牌方的 GS1 码匹配的 ASIN。伪 GTIN（`120–139`、`040–049` 等）**绝不能**成为 primary

---

## 上线 Checklist（延后但需追踪）

- [ ] **接入第二组目录之前**（竞品 / 多品牌）：至少把 Issue #2 最小化修完
- [ ] **接入已知存在重复 listing 的市场之前**（欧洲尤其是 FR/DE/IT/ES）：修 Issue #1 + #3
- [ ] **上线前数据清洗**：至少对 `p2|UK` 跑一次恢复 SQL
- [ ] **上线后可观测性**：加一个每日任务，标记 `product_asins` 里 `keepa_time_series` 行数 < 10 的 ASIN（可疑 / 刚被覆盖）
- [ ] **实现 `is_primary` 之前**：按 Addendum A 采用 GTIN-14 归一化验证（同时归一化 EAN 和 UPC，拒绝伪 GTIN 前缀）
