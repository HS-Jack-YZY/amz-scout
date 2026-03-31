# amz-scout

Amazon 竞品数据自动化采集工具。配置驱动，一键采集多站点竞品价格、评分、BSR、月销 + Keepa 价格走势。

## 安装

```bash
cd amz-scout
pip install -e .
```

**前置条件：**
- Python 3.12+
- [browser-use](https://github.com/browser-use/browser-use) CLI：`uv tool install browser-use`
- Keepa API Key：`export KEEPA_API_KEY="your-key-here"`

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
# 全量采集
amz-scout scrape config/BE10000.yaml

# 单站点
amz-scout scrape config/BE10000.yaml -m UK

# 单产品
amz-scout scrape config/BE10000.yaml -p "RT-BE58"

# 仅当前价格（跳过 Keepa）
amz-scout scrape config/BE10000.yaml --data-only

# 仅价格走势（不需要浏览器）
amz-scout scrape config/BE10000.yaml --history-only

# Debug 模式（可视化浏览器）
amz-scout scrape config/BE10000.yaml -m UK -p "RT-BE58" --headed
```

### `amz-scout discover`

扫描各站点 ASIN，自动填充配置中的 `marketplace_overrides`。

```bash
amz-scout discover config/BE10000.yaml
amz-scout discover config/BE10000.yaml -m DE
```

### `amz-scout validate`

校验 YAML 配置文件。

### `amz-scout status`

检查数据完整性。

## 配置文件

### `config/marketplaces.yaml`

定义 Amazon 站点的域名、Keepa 域名、货币、配送地址。

### `config/<project>.yaml`

定义产品列表和采集设置。每个产品可以指定：
- `default_asin` — 默认 ASIN（通常为 UK ASIN）
- `marketplace_overrides` — 各站点的 ASIN 覆盖
- `search_keywords` — 搜索关键词（ASIN 不存在时用于搜索补全）

## ASIN 解析流程

当产品在某站点的 ASIN 不存在时：

1. 查 YAML `marketplace_overrides` → 有则用该 ASIN
2. 用 `default_asin` 访问产品页 → 404 则触发搜索
3. 用 `search_keywords` 在目标站搜索 → 匹配型号关键词
4. 找到后自动回写到 YAML 配置
5. 找不到则标记为 "Not listed"

## 输出

```
output/<project>/data/
├── eu/
│   ├── eu_competitive_data.csv
│   └── eu_price_history.csv
├── de/
│   ├── de_competitive_data.csv
│   └── de_price_history.csv
├── ca/
│   └── ...
└── au/
    └── ...
```

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

编辑 `config/marketplaces.yaml` 添加站点定义，再在项目 YAML 的 `target_marketplaces` 中添加。
