# amz-scout

Amazon 竞品数据自动化采集工具。配置驱动，一键采集多站点竞品价格、评分、BSR、月销 + Keepa 价格走势。

## 安装

```bash
# 1. 克隆仓库
git clone git@github.com:HS-Jack-YZY/amz-scout.git
cd amz-scout

# 2. 配置 Keepa API Key
cp .env.example .env
# 编辑 .env，填入你的 Keepa API Key

# 3. 安装工具
pip install -e .

# 4. 安装 browser-use CLI（用于 Amazon 页面采集）
uv tool install browser-use
```

**前置条件：**
- Python 3.12+
- [browser-use](https://github.com/browser-use/browser-use) CLI
- [Keepa](https://keepa.com/) API Key（价格走势数据）

## 快速开始

```bash
# 1. 校验配置
amz-scout validate config/BE10000.yaml

# 2. 首次运行：扫描跨站 ASIN（可选，已有覆盖的可跳过）
amz-scout discover config/BE10000.yaml

# 3. 完整采集（Amazon 产品页 + Keepa 价格走势）
amz-scout scrape config/BE10000.yaml

# 4. 查看数据状态
amz-scout status config/BE10000.yaml
```

## 命令参考

### `amz-scout scrape`

采集 Amazon 竞品数据 + Keepa 价格走势。**核心命令，完成全部数据采集工作。**

**参数：**

| 参数 | 说明 |
|------|------|
| `PROJECT_CONFIG` | 项目配置文件路径（必填） |
| `-m, --marketplace` | 只采集指定站点（如 `-m UK`） |
| `-p, --product` | 只采集指定产品（模糊匹配，如 `-p "RT-BE58"`） |
| `-x, --exclude` | 排除指定产品（如 `-x "RS500"` 跳过 NETGEAR RS500） |
| `--data-only` | 仅采集 Amazon 产品页当前数据，跳过 Keepa 价格走势。不需要 Keepa API Key |
| `--history-only` | 仅获取 Keepa 价格走势。不需要浏览器，纯 API 调用 |
| `--headed` | 显示浏览器窗口（Debug 用，可观察页面操作过程） |
| `-v, --verbose` | 显示详细日志（含每个 browser-use 命令） |

**使用示例：**

```bash
# 全量采集（8 站点 × 17 产品，约 30 分钟 + Keepa 等待时间）
amz-scout scrape config/BE10000.yaml

# 只跑 UK 站
amz-scout scrape config/BE10000.yaml -m UK

# 只跑某个产品（跨所有站点）
amz-scout scrape config/BE10000.yaml -p "RT-BE58"

# 仅当前价格（最常用，不消耗 Keepa token）
amz-scout scrape config/BE10000.yaml --data-only

# 仅价格走势（后台跑，自动等待 token refill）
amz-scout scrape config/BE10000.yaml --history-only

# Debug：单站单产品 + 可视化浏览器 + 详细日志
amz-scout scrape config/BE10000.yaml -m UK -p "RT-BE58" --headed -v
```

### `amz-scout discover`

扫描各站点 ASIN，自动填充配置中的 `marketplace_overrides`。**首次添加新产品后运行一次即可，后续 scrape 也会自动发现并回写。**

**参数：**

| 参数 | 说明 |
|------|------|
| `PROJECT_CONFIG` | 项目配置文件路径（必填） |
| `-m, --marketplace` | 只扫描指定站点 |
| `--headed` | 显示浏览器窗口 |
| `-v, --verbose` | 详细日志 |

**使用示例：**

```bash
# 扫描所有站点（添加新产品后运行）
amz-scout discover config/BE10000.yaml

# 仅扫描 DE 站
amz-scout discover config/BE10000.yaml -m DE

# 可视化模式（观察搜索过程）
amz-scout discover config/BE10000.yaml --headed
```

### `amz-scout reparse`

从已保存的 raw JSON 重新生成价格走势 CSV。**修改了解析逻辑后使用，不消耗任何 Keepa token。**

```bash
amz-scout reparse config/BE10000.yaml
amz-scout reparse config/BE10000.yaml -m UK   # 仅重新解析 UK
```

### `amz-scout validate`

校验 YAML 配置文件，检查 ASIN 格式、站点定义是否完整、产品列表是否有效。**建议在每次修改配置后运行。**

```bash
amz-scout validate config/BE10000.yaml
# 输出示例：
# Config valid!
#   Project: BE10000
#   Markets: UK, DE, FR, IT, ES, NL, CA, AU
#   Products: 17
```

### `amz-scout status`

检查各站点数据完整性，显示已采集的行数和缺失的数据文件。**用于快速了解哪些站点已跑完、哪些还需要补跑。**

```bash
amz-scout status config/BE10000.yaml
# 输出示例：
#        Data Status
# ┌──────┬──────────────────┬──────────────────┐
# │ Site │ Competitive Data │ Price History     │
# ├──────┼──────────────────┼──────────────────┤
# │ UK   │ 17 rows          │ 17 rows          │
# │ DE   │ 17 rows          │ missing          │
# │ ...  │ ...              │ ...              │
# └──────┴──────────────────┴──────────────────┘
```

## 配置文件

### `config/marketplaces.yaml`

定义 Amazon 站点。目前支持 8 个站点：

| 站点 | 域名 | 货币 | 区域 |
|------|------|------|------|
| UK | amazon.co.uk | GBP (£) | eu |
| DE | amazon.de | EUR (€) | eu |
| FR | amazon.fr | EUR (€) | eu |
| IT | amazon.it | EUR (€) | eu |
| ES | amazon.es | EUR (€) | eu |
| NL | amazon.nl | EUR (€) | eu |
| US | amazon.com | USD (\$) | na |
| CA | amazon.ca | CAD (\$) | na |
| MX | amazon.com.mx | MXN (\$) | na |
| JP | amazon.co.jp | JPY (¥) | apac |
| AU | amazon.com.au | AUD (\$) | apac |

### `config/<project>.yaml`

定义产品列表和采集设置。每个产品可以指定：
- `default_asin` — 默认 ASIN（通常为 UK ASIN）
- `marketplace_overrides` — 各站点的 ASIN 覆盖（跨站 ASIN 不同时使用）
- `search_keywords` — 搜索关键词（ASIN 不存在时用于搜索补全）

## ASIN 解析流程

跨站点 ASIN 经常不同（例如 ASUS RT-BE88U 在 UK 是 B0D47MGRS4，在 DE 是 B07MP95PXF）。工具的三层 fallback 机制：

1. 查 YAML `marketplace_overrides` → 有则用该 ASIN
2. 用 `default_asin` 访问产品页 → 404 则触发搜索
3. 用 `search_keywords` 在目标站搜索 → 匹配型号关键词
4. 找到后**自动回写**到 YAML 配置（下次不用重新搜）
5. 找不到则标记为 "Not listed"

## 输出目录

按 `地区/国家` 组织：

```
output/<project>/data/
├── eu/                         ← 欧洲
│   ├── uk_competitive_data.csv
│   ├── uk_price_history.csv
│   ├── de_competitive_data.csv
│   ├── de_price_history.csv
│   ├── fr_competitive_data.csv
│   ├── fr_price_history.csv
│   ├── it_competitive_data.csv
│   ├── it_price_history.csv
│   ├── es_competitive_data.csv
│   ├── es_price_history.csv
│   ├── nl_competitive_data.csv
│   └── nl_price_history.csv
├── na/                         ← 北美
│   ├── ca_competitive_data.csv
│   └── ca_price_history.csv
└── apac/                       ← 亚太
    ├── au_competitive_data.csv
    └── au_price_history.csv
```

**数据字段说明：**

| 文件 | 主要字段 |
|------|---------|
| `*_competitive_data.csv` | date, site, category, brand, model, asin, title, price, rating, review_count, bought_past_month, bsr, available, url + 库存字段 + Listing 质量字段 |
| `*_price_history.csv` | date, site, ...基础字段, buybox/amz/new 价格统计, sales_rank + **月销量 + Buy Box + 卖家字段** |

**价格走势字段（Keepa Pro）：**

| 字段 | 示例 | 说明 |
|------|------|------|
| `buybox/amz/new_current` | 135.99 | 当前价格（Buy Box / Amazon 自营 / 第三方新品） |
| `buybox/amz/new_lowest` | 119.95 | 90 天最低价 |
| `buybox/amz/new_highest` | 169.99 | 90 天最高价 |
| `buybox/amz/new_avg90` | 159.93 | 90 天均价 |
| `sales_rank` | 804 | 当前 Sales Rank |
| `monthly_sold` | 2000 | **精确月销量**（比 Amazon 的 "100+ bought" 精确得多） |
| `buybox_is_amazon` | "False" | Buy Box 是否 Amazon 自营 |
| `buybox_is_fba` | "True" | Buy Box 是否 FBA 发货 |
| `buybox_seller_id` | "A364119SDJA4QG" | Buy Box 卖家 ID |
| `seller_count` | 7 | 总卖家数 |
| `fba_seller_count` | 4 | FBA 卖家数 |

**库存字段：**

| 字段 | 示例 | 说明 |
|------|------|------|
| `stock_status` | "In stock" / "Only 2 left in stock." | 库存状态原始文本 |
| `stock_count` | "2" / "" | "Only X left" 的具体数字，充足时为空 |
| `sold_by` | "GL.iNet Technologie" / "Amazon Resale" | Buy Box 卖家名称 |
| `other_offers` | "New & Used (26) from £81.78" | 其他卖家报价摘要 |

**Listing 质量字段：**

| 字段 | 示例 | 说明 |
|------|------|------|
| `coupon` | "Save 5% with coupon" / "" | 优惠券信息 |
| `is_prime` | "True" / "False" | 是否有 Prime 标志 |
| `star_distribution` | `{"5_star":"50%","4_star":"17%",...}` | 评分分布（JSON） |
| `image_count` | "14" | 产品图片数量 |
| `qa_count` | "24 answered questions" | Q&A 数量 |
| `fulfillment` | "FBA" / "FBM" / "" | 配送方式（Amazon 发货 / 卖家自发） |

## 添加新产品

编辑 `config/<project>.yaml`，在 `products:` 下添加：

```yaml
  - category: "Home Router"
    brand: "New Brand"
    model: "New Model X100"
    default_asin: "B0XXXXXXXXX"
    search_keywords: "New Brand X100 WiFi 7"
```

然后运行 `amz-scout discover` 补全各站点 ASIN。

## 添加新站点

1. 编辑 `config/marketplaces.yaml` 添加站点定义
2. 在项目 YAML 的 `target_marketplaces` 中添加站点代码
3. 运行 `amz-scout discover` 扫描 ASIN

## Roadmap

- [x] ~~Settings 配置项生效~~ — retry_count, page_load_wait 已生效
- [x] ~~reparse 命令~~ — 从 raw JSON 重新生成 CSV，零 token 成本
- [x] ~~数据校验~~ — 采集后自动检查价格/评分异常
- [x] ~~Amazon 产品页重试~~ — 失败时自动重试 N 次
- [ ] 关键词搜索排名 — 指定关键词，追踪各产品在搜索结果中的排名位置
- [ ] 广告位检测 — 检测竞品是否在投 Sponsored Ads
- [ ] 差评关键词提取 — 提取 1-2 星评论的高频关键词，发现竞品弱点
- [ ] 评论增长速度 — 追踪每月新增评论数，判断销售趋势
- [ ] 报告生成集成 — 将 `generate_report.py` 迁移为 `amz-scout report` 命令
- [ ] 定时任务支持 — cron / schedule 定期自动采集
- [ ] 数据版本管理 — 按日期归档历史数据，支持时间序列分析

## Keepa API 说明

- Pro plan：60 tokens，1 token/min refill
- 默认模式：**1 token/产品**（价格历史 + 月销量 + Buy Box 信息）
- `--detailed` 模式：~5 tokens/产品（额外含卖家列表 + 预计算统计）
- 全量默认采集（8 站 × 17 产品 = 136 tokens）约 2 小时 refill
- `--data-only` 模式不消耗 Keepa token
- 工具内置自动等待 token refill 逻辑
- Raw JSON 保存在 `data/{region}/raw/` 目录，以后可重新解析无需花 token
