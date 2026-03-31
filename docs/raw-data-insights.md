# Raw Data Insights — 可从 Keepa Raw JSON 中挖掘的分析方向

**数据来源**: `output/<project>/data/{region}/raw/{site}_{asin}.json`
**每个 JSON 包含**: 91 个字段，61 个有数据，覆盖价格、销售、卖家、产品、分类等维度

---

## 一、价格分析（csv[] + couponHistory + deals）

### 1.1 多维度价格时间序列
csv[] 数组包含 36 种价格类型的完整历史记录（[时间戳, 价格(分)] 交替排列）：

| csv 索引 | 含义 | 分析用途 |
|---------|------|---------|
| 0 | Amazon 自营价 | Amazon 直售定价策略，是否频繁调价 |
| 1 | 第三方新品最低价 | 市场竞争地板价 |
| 2 | 二手品最低价 | 是否存在大量二手/翻新流通 |
| 3 | Sales Rank | 销量排名趋势（可做"排名-价格"关联分析） |
| 4 | List Price (RRP) | 参考零售价，计算实际折扣率 |
| 8 | FBM 新品价 | 自发货卖家的定价 vs FBA 价差 |
| 11 | 新品卖家数量 | 卖家竞争程度时间序列 |
| 12 | 二手卖家数量 | 二手市场活跃度 |
| 33 | 评分 (rating × 10) | 评分随时间变化趋势 |
| 34 | 评论数 | 评论增长速度 |

**可做分析：**
- **价格战检测**：当卖家数(csv[11])增加 + 价格(csv[1])下降 → 新卖家入场引发价格战
- **促销效果**：价格骤降 + 排名骤升(csv[3]) → 计算促销的 BSR 提升弹性
- **季节性定价**：按月聚合价格，找出全年最低价时间窗口（黑五/Prime Day/圣诞等）
- **价格弹性曲线**：价格变化百分比 vs 排名变化百分比，量化价格敏感度
- **FBA vs FBM 价差**：csv[0]或csv[1] vs csv[8]，分析 FBA 溢价空间

### 1.2 优惠券历史（couponHistory）
格式：[时间戳, 优惠券类型, 金额, ...]
- 竞品多久发一次券？每次发多少？
- 优惠券和价格下降是否同步？（叠加促销 vs 单独用券）

### 1.3 促销/秒杀（deals）
- Lightning Deal / Best Deal 参与频率
- 秒杀价和正常价的折扣幅度

### 1.4 MAP 政策检测（newPriceIsMAP）
- 是否存在最低广告价格限制（MAP=Minimum Advertised Price）
- MAP 限制下各卖家的定价一致性

---

## 二、销售分析（monthlySoldHistory + salesRanks）

### 2.1 精确月销量时间序列
`monthlySoldHistory` 是交替的 [时间戳, 销量] 数组，比 Amazon 页面的 "100+ bought" 精确得多。

**可做分析：**
- **销售趋势**：逐月画图，看产品是增长期/成熟期/衰退期
- **竞品销量对比**：同品类产品的月销量排名
- **新品上市加速度**：上架后月销量增长曲线（从 0 到稳态需要多久）
- **促销拉动效果**：促销月 vs 非促销月的销量差

### 2.2 多品类 Sales Rank
`salesRanks` 是一个 dict，key 是品类 ID，value 是 [时间戳, 排名] 数组。
- 同一产品在 "Computers & Accessories" 大类 vs "Routers" 子类的排名变化
- 品类内竞品排名相互消长（当 A 排名升 → B 排名降）

### 2.3 排名参考历史（salesRankReferenceHistory）
- 产品是否换过品类？换品类前后的排名变化

---

## 三、卖家生态分析（buyBox + offers + fbaFees）

### 3.1 Buy Box 卖家历史
`buyBoxSellerIdHistory` 记录了谁在什么时间赢得 Buy Box。
- **Buy Box 争夺频率**：一天换几次卖家？
- **Amazon 自营占比**：Amazon 多少时间拥有 Buy Box？
- **FBA vs FBM**：FBA 卖家赢 Buy Box 的概率

### 3.2 卖家列表分析（需 --detailed）
每个 offer 包含：sellerId, isFBA, isPrime, isAmazon, isWarehouseDeal, shipsFromChina, condition, offerCSV(卖家独立价格历史)
- **中国直发卖家占比**（shipsFromChina）— 跨境竞争强度
- **Amazon Warehouse 翻新品**（isWarehouseDeal）— 影响新品销售
- **各卖家定价策略**（offerCSV）— 谁在跟价、谁在溢价
- **FBA 占比**：FBA 卖家 / 总卖家 — 市场成熟度指标

### 3.3 FBA 费用（fbaFees）
- 物流成本 + 配送成本
- 计算各竞品的 FBA 费率（FBA费/售价）— 利润率粗估

---

## 四、产品信息深度挖掘

### 4.1 Listing 质量评分
从 raw data 中提取：
- `features` 数量和字符数 → Bullet Points 丰富度
- `images` 数量 → 视觉营销投入
- `title` 长度和关键词 → SEO 优化程度
- `description` 是否为空 → A+ Content 可能性

**可做分析：**
- **Listing 质量指数** = f(features数, images数, title长度, description有无)
- 质量指数 vs 评分 vs 销量的相关性

### 4.2 产品规格对比
`itemWeight`, `itemHeight/Length/Width`, `packageWeight` 等物理参数：
- 竞品体积/重量对比 → 物流成本差异
- 包装效率 = 产品重量 / 包装重量

### 4.3 条形码（eanList / upcList）
- 同一产品不同 ASIN 的条形码是否相同 → 判断是否同一物理产品
- 条形码缺失可能意味着非品牌授权渠道

---

## 五、竞争格局分析

### 5.1 评分 vs 评论增长对比
csv[33]=评分, csv[34]=评论数，按时间对比：
- **评分恶化预警**：评分持续下降的产品可能有质量问题（竞争对手的软肋）
- **评论增长速度**：每月新增评论数 → 推算每月销量（评论率约 1-3%）
- **新品冷启动**：从 0 评论到 100 评论用了多久

### 5.2 品牌集中度
按 brand 聚合所有产品的月销量：
- GL.iNet 在 Travel Router 品类的市场份额
- 各品牌的 SKU 数量 vs 总销量（爆款策略 vs 广撒网策略）

### 5.3 价格带分析
按价格区间分桶（\$0-50, \$50-100, \$100-200, \$200+），统计每个价格带的：
- 产品数量
- 平均评分
- 平均月销量
- 平均卖家数

**可做分析：**
- 哪个价格带竞争最激烈？
- 哪个价格带"评分高但销量低"？（有机会做差异化）

---

## 六、时间维度分析

### 6.1 产品生命周期
`listedSince` = 上架时间，`trackingSince` = Keepa 开始追踪时间。
- 竞品上架时间线 → 谁先进入市场
- 上架多久开始有稳定销量

### 6.2 数据新鲜度
`lastUpdate`, `lastPriceChange`, `lastRatingUpdate`, `lastSoldUpdate`：
- 哪些竞品长时间没有价格变动？（可能缺货或停售）
- 评论最后更新时间 → 是否还在活跃销售

### 6.3 Prime Day / 秒杀时间
`primeDealEndTime`：
- 竞品是否参加了 Prime Day 秒杀？
- 秒杀结束后价格恢复速度

---

## 七、跨站对比分析

同一产品在不同站点（UK/DE/FR/US/CA/AU）的 raw JSON 可以对比：

### 7.1 价格差异
- 同产品跨站价格换算后的差异 → 套利空间
- 哪个市场定价最高/最低

### 7.2 竞争差异
- 同产品在不同市场的卖家数量差异
- 某品牌在 A 市场强但 B 市场弱 → 市场进入机会

### 7.3 销量差异
- 同产品在各市场的月销量 → 市场规模对比
- 上架顺序 → 哪个市场先测试

---

## 八、可视化建议

| 图表 | 数据源 | 价值 |
|------|--------|------|
| 价格走势折线图 | csv[0,1] | 竞品定价策略一目了然 |
| 销量柱状图（月度） | monthlySoldHistory | 销售趋势 |
| 卖家数量变化图 | csv[11,12] | 竞争加剧/减弱 |
| 评分趋势 | csv[33] | 产品质量变化 |
| 价格-排名散点图 | csv[0] vs csv[3] | 价格弹性可视化 |
| 品牌市场份额饼图 | monthlySold 聚合 | 竞争格局 |
| 跨站价格热力图 | 各站 csv[0] | 定价差异 |

---

## 九、自动化报告建议

以上分析可以封装为 `amz-scout analyze` 命令，从 raw JSON 自动生成：

1. **竞品周报** — 本周价格变动 + 排名变动 + 新卖家入场
2. **市场月报** — 月销量趋势 + 品牌份额 + 价格带分析
3. **预警系统** — 竞品大幅降价 / 新品上架 / 评分暴跌

---

*以上所有分析均可从 1 token/产品 的基础 raw JSON 中完成，无需额外 API 调用。*
