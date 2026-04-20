# amz-scout 数据库 ER 图

> Schema 版本: 4 | SQLite (WAL 模式) | 10 张表

## ER 图

```mermaid
erDiagram
    %% ─── 产品注册表（身份层）───────────────────────────────

    products {
        INTEGER id PK "自增主键"
        TEXT category "NOT NULL 产品类别"
        TEXT brand "NOT NULL 品牌"
        TEXT model "NOT NULL 型号"
        TEXT search_keywords "搜索关键词"
        TEXT created_at "创建时间 ISO 8601"
        TEXT updated_at "更新时间 ISO 8601"
    }

    product_asins {
        INTEGER product_id PK,FK "-> products.id"
        TEXT marketplace PK "市场站点 如 UK DE US"
        TEXT asin "NOT NULL 如 B0XXXXXXXX"
        TEXT status "unverified|verified|wrong_product|not_listed|unavailable"
        TEXT notes "备注"
        TEXT last_checked "上次验证时间"
        TEXT created_at "创建时间"
        TEXT updated_at "更新时间"
    }

    product_tags {
        INTEGER product_id PK,FK "-> products.id"
        TEXT tag PK "标签 如 BE10000"
    }

    %% ─── Keepa 数据（API 层）────────────────────────────────

    keepa_products {
        TEXT asin PK "如 B0XXXXXXXX"
        TEXT site PK "如 UK DE US"
        TEXT title "产品标题"
        TEXT brand "品牌"
        TEXT manufacturer "制造商"
        TEXT model "型号"
        TEXT product_group "产品分组"
        TEXT binding "装订或类型"
        TEXT color "颜色"
        TEXT size "尺寸"
        INTEGER root_category "根分类 ID"
        TEXT category_tree "分类树 JSON"
        INTEGER sales_rank_ref "销售排名参考分类"
        TEXT ean_list "EAN 列表 JSON"
        TEXT upc_list "UPC 列表 JSON"
        INTEGER listed_since "上架时间 Keepa 时间戳"
        INTEGER tracking_since "追踪起始 Keepa 时间戳"
        INTEGER availability_amazon "Amazon 自营可用性"
        TEXT fetched_at "NOT NULL 抓取时间"
        TEXT fetch_mode "basic 或 detailed"
    }

    keepa_time_series {
        TEXT asin PK "如 B0XXXXXXXX"
        TEXT site PK "如 UK"
        INTEGER series_type PK "0-35 csv 100 月销 200+ 分类排名"
        INTEGER keepa_ts PK "Keepa 分钟时间戳"
        INTEGER value "价格分 评分x10 排名 数量"
        TEXT fetched_at "NOT NULL 抓取时间"
    }

    keepa_buybox_history {
        TEXT asin PK "如 B0XXXXXXXX"
        TEXT site PK "如 UK"
        INTEGER keepa_ts PK "Keepa 分钟时间戳"
        TEXT seller_id "NOT NULL 卖家 ID"
        TEXT fetched_at "NOT NULL 抓取时间"
    }

    keepa_coupon_history {
        TEXT asin PK "如 B0XXXXXXXX"
        TEXT site PK "如 UK"
        INTEGER keepa_ts PK "Keepa 分钟时间戳"
        INTEGER amount "NOT NULL 优惠金额"
        INTEGER coupon_type "优惠券类型"
        TEXT fetched_at "NOT NULL 抓取时间"
    }

    keepa_deals {
        TEXT asin PK "如 B0XXXXXXXX"
        TEXT site PK "如 UK"
        INTEGER start_time PK "开始时间 Keepa 分钟"
        INTEGER end_time "结束时间 可为空"
        TEXT deal_type "NOT NULL 促销类型"
        TEXT access_type "访问类型 默认 ALL"
        TEXT badge "标签徽章"
        INTEGER percent_claimed "已领取百分比"
        TEXT deal_status "ACTIVE 或 ENDED 或 UNKNOWN"
        TEXT fetched_at "NOT NULL 抓取时间"
    }

    %% ─── 浏览器抓取数据 ─────────────────────────────────────

    competitive_snapshots {
        INTEGER id PK "自增主键"
        TEXT scraped_at "NOT NULL 抓取时间"
        TEXT site "NOT NULL 站点 如 UK"
        TEXT category "NOT NULL 类别"
        TEXT brand "NOT NULL 品牌"
        TEXT model "NOT NULL 型号"
        TEXT asin "NOT NULL ASIN"
        TEXT title "产品标题"
        INTEGER price_cents "价格 分"
        TEXT currency "货币 如 GBP EUR USD"
        REAL rating "评分"
        INTEGER review_count "评论数"
        INTEGER bought_past_month "过去一个月购买量"
        INTEGER bsr "Best Sellers Rank"
        INTEGER available "1=有货 0=无货"
        TEXT url "产品页 URL"
        TEXT sold_by "卖家"
        TEXT fulfillment "配送方式"
        TEXT project "项目标识"
        TEXT created_at "创建时间"
    }

    %% ─── Schema 版本追踪 ────────────────────────────────────

    schema_migrations {
        INTEGER version PK "版本号"
        TEXT description "NOT NULL 描述"
        TEXT applied_at "应用时间"
    }

    %% ─── 关系 ───────────────────────────────────────────────

    %% 外键约束（数据库强制）
    products ||--o{ product_asins : "拥有各市场ASIN"
    products ||--o{ product_tags : "标记标签"

    %% 逻辑关联 通过 asin + site（无外键）
    keepa_products ||--o{ keepa_time_series : "价格排名序列"
    keepa_products ||--o{ keepa_buybox_history : "Buy Box 卖家"
    keepa_products ||--o{ keepa_coupon_history : "优惠券历史"
    keepa_products ||--o{ keepa_deals : "促销活动"

    %% 跨层逻辑关联
    product_asins }o..o| keepa_products : "asin+site 查找"
    product_asins }o..o{ competitive_snapshots : "asin 匹配"
```

## 表分组

| 层级 | 表 | 数据来源 |
|------|-----|---------|
| **产品注册表** | `products`, `product_asins`, `product_tags` | 手动添加 / YAML 导入 / Keepa 自动注册 |
| **Keepa API 数据** | `keepa_products`, `keepa_time_series`, `keepa_buybox_history`, `keepa_coupon_history`, `keepa_deals` | Keepa API（1 token/产品） |
| **浏览器抓取** | `competitive_snapshots` | browser-use CLI 爬取 Amazon 产品页 |
| **元数据** | `schema_migrations` | 自动管理 |

## 关键关系

### 强制外键（数据库层保证）
- `product_asins.product_id` -> `products.id`（ON DELETE CASCADE 级联删除）
- `product_tags.product_id` -> `products.id`（ON DELETE CASCADE 级联删除）

### 逻辑关联（无外键，通过 `asin` + `site` 连接）
- `keepa_products` <-> `keepa_time_series` / `keepa_buybox_history` / `keepa_coupon_history` / `keepa_deals`
- `product_asins.asin` <-> `keepa_products.asin`（跨层身份关联）
- `product_asins.asin` <-> `competitive_snapshots.asin`（跨层数据关联）

## 唯一约束与索引

| 表 | 唯一约束 | 主要索引 |
|----|---------|---------|
| `products` | `(brand, model)` | -- |
| `product_asins` | `(product_id, marketplace)` | `idx_pa_asin(asin)` |
| `product_tags` | `(product_id, tag)` | `idx_pt_tag(tag)` |
| `keepa_products` | `(asin, site)` | -- |
| `keepa_time_series` | `(asin, site, series_type, keepa_ts)` | `idx_kts_site_type`, `idx_kts_fetched` |
| `keepa_buybox_history` | `(asin, site, keepa_ts)` | `idx_kbb_seller` |
| `keepa_coupon_history` | `(asin, site, keepa_ts)` | -- |
| `keepa_deals` | `(asin, site, start_time)` | -- |
| `competitive_snapshots` | `(asin, site, scraped_at)` | `idx_cs_site_date`, `idx_cs_brand_model` |

## 时间序列类型参考（keepa_time_series）

| 范围 | 含义 | 示例 |
|------|------|------|
| 0-35 | Keepa `csv[]` 数组索引 | 0=Amazon 自营价, 1=第三方新品, 2=二手, 3=销售排名, 16=评分, 17=评论数 |
| 100 | 月销量 | `monthlySoldHistory` |
| 200+ | 分类销售排名 | 200+N，N = `salesRanks` 字典中的分类索引 |

> **值编码规则**: 价格以「分」为单位（除以 100 得实际价格），评分 x10（45 = 4.5 星），销售排名为原始整数，-1 表示不可用
