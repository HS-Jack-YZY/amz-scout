# amz-scout · GL.iNet 内部产品数据助手

面向 GL.iNet 内部用户的 Amazon 产品数据自然语言查询工具。

## 你可以问什么

- **最新数据** — "show me latest UK data" · "美国最新的竞品数据"
- **价格趋势** — "Slate 7 在德国的 90 天价格曲线"
- **跨市场对比** — "RT-BE58 在 UK/DE/US 的现价对比"
- **BSR 排名** — "英国 Travel Router 类目的排名"

## 登录

请使用你的 `@gl-inet.com` 邮箱 + 管理员分享的 MVP 密码登录。

> Phase 1 MVP — 全体用户共享一个密码。Phase 6 部署时会升级为每人独立账号。

## 数据来源

- **Keepa** — 历史价格、BSR、库存、促销（API）
- **Amazon 页面抓取** — 当前价、评分、Buy Box（browser-use）
- **本地 SQLite** — 产品注册表 + 快照历史
