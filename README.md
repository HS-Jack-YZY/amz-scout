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

采集 Amazon 竞品数据 + Keepa 价格走势。

```bash
# 全量采集（8 站点 × 17 产品）
amz-scout scrape config/BE10000.yaml

# 单站点
amz-scout scrape config/BE10000.yaml -m UK

# 单产品（跨所有站点）
amz-scout scrape config/BE10000.yaml -p "RT-BE58"

# 仅当前价格（跳过 Keepa，不需要 API Key）
amz-scout scrape config/BE10000.yaml --data-only

# 仅价格走势（仅 Keepa API，不需要浏览器）
amz-scout scrape config/BE10000.yaml --history-only

# Debug 模式（可视化浏览器 + 详细日志）
amz-scout scrape config/BE10000.yaml -m UK -p "RT-BE58" --headed -v
```

### `amz-scout discover`

扫描各站点 ASIN，自动填充配置中的 `marketplace_overrides`。首次添加新产品后运行一次即可。

```bash
amz-scout discover config/BE10000.yaml
amz-scout discover config/BE10000.yaml -m DE   # 仅扫描 DE
```

### `amz-scout validate`

校验 YAML 配置文件（ASIN 格式、站点定义等）。

### `amz-scout status`

检查各站点数据完整性。

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
| CA | amazon.ca | CAD (\$) | na |
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

| 文件 | 字段 |
|------|------|
| `*_competitive_data.csv` | date, site, category, brand, model, asin, title, price, rating, review_count, bought_past_month, bsr, available, url |
| `*_price_history.csv` | date, site, category, brand, model, asin, buybox_current/lowest/highest/avg90, amz_current/lowest/highest/avg90, new_current/lowest/highest/avg90, sales_rank |

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

## Keepa API 说明

- 免费 plan：60 tokens，1 token/min refill
- 每个产品查询消耗 1 token
- 全量采集（8 站 × 17 产品 = 136 tokens）需要等待约 2 小时 refill
- `--data-only` 模式不消耗 Keepa token
- 工具内置自动等待 token refill 逻辑
