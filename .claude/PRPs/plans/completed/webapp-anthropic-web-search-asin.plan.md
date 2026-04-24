# Plan: Webapp Anthropic Web Search ASIN Discovery

> **Revision 2026-04-24**: 升级到 `web_search_20260209`（dynamic filtering 内置版），**不**加独立 `code_execution` 工具（官方警告会造成二环境冲突），llm.py 新增 `pause_turn` 处理 + 20-block lookback 监控。详见文末 "Revision Notes (2026-04-24)" 段。

## Summary
在 webapp 侧接入 Anthropic 官方 server-side **`web_search_20260209`**（dynamic filtering 内置版）+ 一个客户端桥接工具 `register_asin_from_url`，让**没有 Claude Code 客户端的 webapp 用户**也能通过对话找 ASIN 并入库——不依赖 `browser-use`，不用启动 Chromium，不碰 Keepa search。搜索由 Anthropic 服务端执行（约 \$0.01 / 次；dynamic filtering 内部的 code execution 与 web search 共用时**免费**，无需单独声明），LLM 自动把搜到的 Amazon URL 交回 `register_asin_from_url`，该工具用正则抽取 ASIN 并调现成的 `amz_scout.api.add_product` / `register_market_asins` 写回注册表。

## User Story
As a 小李（没有 Claude Code、只能用 webapp 的内部用户），
I want 用自然语言请求「帮我找 GL-Slate 7 在德国的 ASIN」就能直接拿到结果并入库，
So that 我不用去催 Jack、也不用自己跑 browser-use，就能把未注册的产品引到注册表里。

## Problem → Solution
**Current state**: webapp `TOOL_SCHEMAS` 只有 9 个只读 query 工具（`webapp/tools.py:106-283`）；ASIN 发现完全绕不过用户端 Claude Code 的 `WebSearch` + 人工 `register_market_asins()`，或者后端 `discover_asin`（browser-use，只存在于 CLI / Python API，webapp 里没暴露也没装 Chromium）。webapp 用户遇到未注册产品时只能撤退到「找 Jack 代劳」。

**Desired state**: webapp 的 `TOOL_SCHEMAS` 增加两块：
1. 一块 Anthropic server-side **`web_search_20260209`**（dynamic filtering 内置版）声明（允许域名锁定在 `amazon.*`，`max_uses=5` 防止失控）——搜索执行发生在 Anthropic 服务端，客户端只声明不 dispatch；**不**同时声明 `code_execution`（dynamic filtering 已内置，重复声明会混淆模型）。
2. 一个客户端工具 `register_asin_from_url`（接 `brand` / `model` / `marketplace` / `amazon_url`），用 `/dp/([A-Z0-9]{10})` 正则抽 ASIN、校验 URL 的 TLD 与 marketplace 的 `amazon_domain` 吻合，再委派到现成的 `amz_scout.api` 注册链路。
3. `webapp/llm.py` 新增 `pause_turn` 续跑分支（server tool loop 超 10 iterations 时）+ 20-block lookback 监控日志。

系统提示词补一段 "ASIN Discovery Flow"：优先从 `list_products` 找现成注册 → 未命中时用 `web_search` 搜 `site:amazon.{tld} {brand} {model}` → 从搜索结果里选 URL 喂给 `register_asin_from_url`。

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/internal-amz-scout-web.prd.md`（不是严格对齐某一 phase；与 Phase 4「高风险/长任务工具」互补——Phase 4 要接 `discover_asin`（browser-use），这个计划是**另一条不走浏览器的 ASIN 发现路径**，可并行或替代部署）
- **PRD Phase**: 不直接映射；建议以独立 PRP 完成后，在 PRD Phase 4 状态里加一条 sub-scope 注记
- **Estimated Files**: 6 个（2 新建 / 4 修改）

---

## UX Design

### Before
```
用户: "帮我查 TP-Link AX1500 在德国的价格"
LLM: (调 query_latest → 返回空 / 报错「not_listed」)
LLM: 「TP-Link AX1500 在 DE 还没注册 ASIN。请联系 Jack 手动添加。」
(用户卡住)
```

### After
```
用户: "帮我查 TP-Link AX1500 在德国的价格"
LLM: (调 list_products → 未命中)
LLM: (调 web_search "site:amazon.de TP-Link AX1500 travel router")
    → Anthropic 服务端执行搜索，返回 3 条 amazon.de URL
LLM: (调 register_asin_from_url, amazon_url="amazon.de/.../dp/B0XXXXXXXX/...")
    → 服务端正则抽出 B0XXXXXXXX，写入 product_asins
LLM: (重调 query_latest)
    → 返回 xlsx + summary
LLM: 「找到 TP-Link AX1500 在 DE 的 ASIN B0XXXXXXXX 并已入库；最新价格 €89.99。」
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| 未注册产品查询 | 返回 error 或 not_listed，用户无法自助 | LLM 可自助搜 + 注册 + 重试 | 需系统提示词引导 |
| Keepa 额度 | `discover_asin` 会绕过 Keepa，但 webapp 没这条路径 | `web_search` 不消耗 Keepa token | 成本在 Anthropic 账单 |
| 搜索来源 | N/A（没有路径） | Anthropic 官方 web_search，仅 amazon.* 白名单 | 降低 prompt-injection 曝露 |
| 每轮成本 | ~\$0.01–0.05 / 对话轮 | +\$0.01–0.05（每次搜索 \$0.01，典型 1–2 次/请求） | 可接受 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `webapp/tools.py` | 1-283 | `TOOL_SCHEMAS` 结构、`cache_control` 只放最后一个、`dispatch_tool` 路由模式 |
| P0 | `webapp/tools.py` | 425-494 | `dispatch_tool` 必须添加 `register_asin_from_url` 分支 |
| P0 | `webapp/llm.py` | 45-109 | 客户端 tool-use 循环；`block.type != "tool_use"` 过滤要能与 server-side tool 共存 |
| P0 | `src/amz_scout/api.py` | 102-113 | `_build_marketplace_aliases` —— 新工具要复用同样的市场解析策略 |
| P0 | `src/amz_scout/api.py` | 1195-1302 | `add_product` / `update_product_asin` / `register_market_asins` 三种写入路径 |
| P0 | `src/amz_scout/api.py` | 1615-1644 | `discover_asin` 尾段的「查现成 product → 不存在则 register_product + register_asin」双分支逻辑，**必须照搬进 `register_asin_from_url`** 以保证两种发现路径入库结果一致 |
| P1 | `webapp/config.py` | 20-29 | `SYSTEM_PROMPT` 要扩展 ASIN Discovery Flow 段落 |
| P1 | `config/marketplaces.yaml` | all | `amazon_domain` 字段——生成 `allowed_domains` 白名单 + 校验 URL TLD |
| P1 | `tests/test_webapp_smoke.py` | 139-171, 500-520 | 测试 `TOOL_SCHEMAS` 形状、`cache_control` 位置、tool schema 大小预算 |
| P1 | `tests/test_webapp_smoke.py` | 124-279 | `TestToolDispatch` 添加 `register_asin_from_url` 路由测试 |
| P2 | `src/amz_scout/scraper/search.py` | 15-60 | 参考浏览器路径如何从 Amazon DOM 抽 ASIN（正则对齐 `[A-Z0-9]{10}`） |
| P2 | `webapp/summaries.py` | 1-60 | `register_asin_from_url` 返回结构是小 dict 不走 `summarize_for_llm`，但要理解 envelope 形状约定 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Anthropic server-side web_search tool | `https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool` | 推荐 type `"web_search_20260209"`（dynamic filtering 内置）；旧版 `"web_search_20250305"` 仍可用。name `"web_search"` 固定；支持 `max_uses`、`allowed_domains`、`blocked_domains`、`user_location`。服务端执行，客户端**不写 dispatcher**。费用：每 1000 次搜索约 \$10。返回 blocks 类型为 `server_tool_use` + `web_search_tool_result`（**不是** `tool_use`） |
| Dynamic filtering 的架构 | `shared/tool-use-concepts.md:176-189`（claude-api skill） | **`web_search_20260209` 内置 dynamic filtering**——Anthropic 服务端在内部自己起临时 code execution 做过滤，客户端看不到 `bash_code_execution_tool_result` 块。**不需要**声明 `code_execution` 工具；**不需要** beta header。官方明确警告：同时声明 `code_execution` 会创建第二个执行环境、会混淆模型。组织管理员须在 Anthropic Console privacy 设置启用 web search |
| Server tool 的 pause_turn 停止原因 | `shared/tool-use-concepts.md:66-79`（claude-api skill） | Server-side tool loop 默认上限 10 iterations；超了会 `stop_reason: "pause_turn"`。续跑方式：重发同样 messages（assistant `response.content` 已包含 trailing `server_tool_use` block），Anthropic 自动识别并继续。**不要**注入假的 "Continue" user message |
| Messages API tool_use vs server_tool_use | `https://docs.claude.com/en/api/messages#response-content` | 客户端循环过滤 `block.type == "tool_use"` 时，server_tool_use 天然被忽略；但要在 `model_dump()` 回写 history 时把 server 生成的 block 也留住，否则下一轮缺上下文 |
| Prompt caching 与 tool 声明顺序 | `shared/prompt-caching.md`（claude-api skill） | `cache_control` 只能放**最后一个**工具；前面的工具会被一同缓存。**tool 定义变化（含 type 升级 20250305→20260209）会让 tools+system+messages 三级缓存全部失效**（invalidation-hierarchy 表）——升级后第一次请求必然 cache miss，预期。**20-block lookback**：每个 breakpoint 向前最多 20 blocks 找匹配；server tool 一次调用会产生 ≥2 blocks（`server_tool_use`+`web_search_tool_result`），单轮可能接近上限 |
| Model 是否支持 web_search_20260209 dynamic filtering | `https://docs.claude.com/en/docs/agents-and-tools/tool-use/web-search-tool` | 支持：Claude Mythos Preview / Opus 4.7 / Opus 4.6 / Sonnet 4.6。当前 `webapp/config.py:21` MODEL_ID = `claude-sonnet-4-6` ✅ |

---

## Patterns to Mirror

### CLIENT_TOOL_SCHEMA
// SOURCE: webapp/tools.py:205-283
```python
{
    "name": "query_trends",
    "description": (
        "Price/BSR/sales time series for ONE product × marketplace over a window. "
        "For '价格趋势'/'历史价格'/'past N days'. ..."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product": {"type": "string", "description": _PRODUCT_DESC},
            "marketplace": {
                "type": "string",
                "description": f"{_MARKETPLACE_DESC} Defaults to 'UK'.",
            },
            ...
        },
        "required": ["product"],
    },
    # Cache_control on the LAST tool only — caches all 9 tool definitions together.
    "cache_control": {"type": "ephemeral"},
},
```

### SERVER_TOOL_SCHEMA (new pattern for this plan)
// SOURCE: Anthropic docs（新 pattern；代码库里还不存在）
```python
{
    "type": "web_search_20260209",  # dynamic filtering 内置；不要加 code_execution
    "name": "web_search",
    "max_uses": 5,
    "allowed_domains": [
        "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
        "amazon.it", "amazon.es", "amazon.nl", "amazon.ca",
        "amazon.com.mx", "amazon.in", "amazon.com.br",
        "amazon.co.jp", "amazon.com.au",
    ],
}
```
（server-side tool 没有 `description` / `input_schema`；Anthropic 内部有定义。`max_uses` / `allowed_domains` / `blocked_domains` / `user_location` 在 `20250305` 与 `20260209` 两个版本上**完全相同**，升级版本号不需要改参数）

### DISPATCH_BRANCH
// SOURCE: webapp/tools.py:436-485
```python
if name == "query_latest":
    marketplace = args.get("marketplace")
    if not marketplace:
        return _missing_required("query_latest", "marketplace")
    return await _step_query_latest(
        marketplace=marketplace,
        category=args.get("category"),
    )
```
新工具照抄此形：必填字段用 `_missing_required`，可选字段用 `args.get(...)` 传入。

### STEP_WRAPPER
// SOURCE: webapp/tools.py:303-316
```python
@cl.step(type="tool", name="check_freshness")
async def _step_check_freshness(
    marketplace: str | None = None, product: str | None = None
) -> ApiResponse:
    logger.info("check_freshness called: marketplace=%s product=%s", marketplace, product)
    return await asyncio.to_thread(
        _api_check_freshness, marketplace=marketplace, product=product
    )
```
`register_asin_from_url` 是纯 Python / 正则 + sqlite 写入，属于「快速但阻塞 I/O」类——对齐 `check_freshness` 的 `asyncio.to_thread` 写法。

### ENVELOPE_SHAPE
// SOURCE: src/amz_scout/api.py:1236-1244（`add_product` 返回）
```python
return _envelope(
    True,
    data={"id": pid, "brand": brand, "model": model},
    asins_registered=len(asins) if asins else 0,
    new_product=is_new,
    pending_markets=pending if is_new else [],
    pending_domains=domains if is_new else {},
    warnings=warnings,
)
```
新工具返回 `{"ok": True, "data": {"asin": ..., "marketplace": ..., "brand": ..., "model": ..., "registered": bool, "new_product": bool}, "error": None, "meta": {...}}`。

### ASIN_DUAL_BRANCH (mandatory to mirror)
// SOURCE: src/amz_scout/api.py:1626-1643（`discover_asin` 写入段）
```python
existing = find_product_exact(conn, brand, model)
if existing:
    register_asin(
        conn, existing["id"], site, found_asin,
        notes="discovered via browser search",
    )
else:
    pid, _ = register_product(conn, "", brand, model, keywords)
    register_asin(
        conn, pid, site, found_asin,
        notes="discovered via browser search",
    )
```
`register_asin_from_url` 必须**逐行**镜像这段：先 `find_product_exact` → 命中则 `register_asin` 追加 marketplace；未命中则 `register_product` + `register_asin`。**notes 字段改为 `"discovered via web_search"` 以便将来区分两条来源。**

### MARKETPLACE_ALIAS
// SOURCE: src/amz_scout/api.py:102-113 + 1580-1586
```python
aliases = _build_marketplace_aliases(marketplaces)
site = aliases.get(marketplace.lower()) or marketplace
mp_config = marketplaces.get(site)
if not mp_config:
    return _envelope(False, error=f"Unknown marketplace: {marketplace}")
```
新工具必须复用同一套别名映射，**不要**另搞一套大小写判断——否则「DE」/「de」/「amazon.de」/「EUR」会走不同分支。

### TEST_STRUCTURE
// SOURCE: tests/test_webapp_smoke.py:154-171
```python
def test_all_phase2_tool_names_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_fake_env(monkeypatch)
    _reset_webapp_modules()
    from webapp.tools import TOOL_SCHEMAS

    names = {tool["name"] for tool in TOOL_SCHEMAS}
    expected = {
        "query_latest", "check_freshness", "keepa_budget",
        "query_availability", "query_compare", "query_deals",
        "query_ranking", "query_sellers", "query_trends",
    }
    assert names == expected, f"Missing or extra tools: {names ^ expected}"
```
测试 server-side tool 时要按 `tool.get("type")` 而不是 `tool["name"]` 识别（name 是 `"web_search"` 但没有 `input_schema`）。

### DISPATCHER_STUB_TEST
// SOURCE: tests/test_webapp_smoke.py:173-239
模式：用 `monkeypatch` 把 `webapp.tools._api_*` 替换成返回合法 envelope 的 fake——复制这个结构做 `register_asin_from_url` 的 dispatcher 测试，把 `amz_scout.api.add_product` / `register_market_asins` 用类似手段打桩。

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `webapp/tools.py` | UPDATE | 新增 **`web_search_20260209`** schema + `register_asin_from_url` schema；`cache_control` 从 `query_trends` 迁到**新的最后一个** tool；新增 `_step_register_asin_from_url` async wrapper；`dispatch_tool` 添加路由分支 |
| `webapp/llm.py` | UPDATE | **新**：`run_chat_turn` 循环加 `pause_turn` 续跑分支（server tool 超 10 iterations 时）；加 block 数监控日志防 20-block lookback cache miss |
| `webapp/config.py` | UPDATE | `SYSTEM_PROMPT` 扩展 ASIN Discovery Flow 段落 |
| `src/amz_scout/api.py` | UPDATE | 新增 `register_asin_from_url(brand, model, marketplace, amazon_url)` 公开 API 函数；内部复用 `find_product_exact` / `register_product` / `register_asin` |
| `tests/test_webapp_smoke.py` | UPDATE | 新增 `register_asin_from_url` 的 dispatcher / schema / cache_control 迁移测试；`test_all_phase2_tool_names_present` → 重命名或扩展为 `test_all_expected_tools_present`（容纳 `web_search` + `register_asin_from_url`） |
| `tests/test_api.py` | UPDATE | 新增 `register_asin_from_url` 的 unit test（URL 解析、双分支入库、marketplace 校验） |
| `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | UPDATE | 在 Phase 4 scope 里加一条 sub-note 链到本 plan，说明 "non-browser ASIN discovery via Anthropic web_search" 已作为替代路径交付 |

## NOT Building

- **不实现 discover_asin 的浏览器替代**：`discover_asin` / `batch_discover` 在 Python API 里保留原样，只是 webapp 不强依赖它们。
- **不加第三方搜索 provider**（Serper、Brave、Tavily、Exa）：方案 A 明确选 Anthropic 官方 web_search，一条账单一个 vendor。
- **不做 intent 校验**：CLAUDE.md 规则 10 已经明确 intent 错配（拿到的产品不是用户要的）靠用户看 Keepa title 自查；本工具只保证 ASIN 来自正确的 marketplace TLD，不判断是不是用户想要的产品。
- **不做 search_keywords 入参**：`register_asin_from_url` 不接受自定义 keywords——URL 已经是 web_search 决策的终点，没必要再加搜索参数。
- **不缓存 web_search 结果**：Anthropic 已在服务端做短期缓存；我们不重复造轮子。
- **不动 query 工具 schema**：现有 9 个 query tool 的 schema 和 docstring 不变动；本计划是**新增**不是重构。
- **不加速率限制**：`max_uses=5` 已是单轮上限；跨会话账单控制留给 Anthropic console 的 monthly budget cap（运维侧设置）。
- **不做 UI 确认对话框**：web_search 属于低成本工具（\$0.01/次），不走 `phase="needs_confirmation"` 协议——那是为 ≥6 Keepa token 预留的。

---

## Step-by-Step Tasks

### Task 1: 新增 `amz_scout.api.register_asin_from_url`
- **ACTION**: 在 `src/amz_scout/api.py` 中紧挨 `discover_asin` 之后新增公开函数 `register_asin_from_url(brand: str, model: str, marketplace: str, amazon_url: str, db_path: Path | str | None = None) -> ApiResponse`
- **IMPLEMENT**:
  1. 正则 `re.search(r"/dp/([A-Z0-9]{10})(?:[/?]|$)", amazon_url)` 抽 ASIN（对比 URL 实例：`https://www.amazon.de/GL-iNet/dp/B0CT94XNX3/ref=...`、`https://amzn.to/xxx` 不支持，必须是完整 dp 路径）
  2. 若未匹配，返回 `_envelope(False, error="No ASIN found in URL (expected /dp/<10-char>)")`
  3. 加载 `marketplaces.yaml`，用 `_build_marketplace_aliases` 把 `marketplace` 解析成 canonical code（复用 `discover_asin` 的写法）
  4. 校验 URL host 含 `mp_config.amazon_domain`（防止把 `.com` URL 写到 UK 市场）。匹配用 `urllib.parse.urlparse(amazon_url).netloc.lower().endswith(mp_config.amazon_domain)`；失败返回 `_envelope(False, error=f"URL host does not match marketplace {site} ({mp_config.amazon_domain})")`
  5. 镜像 `discover_asin` 尾段（`api.py:1621-1643`）的双分支写入：`find_product_exact` 命中 → `register_asin(..., notes="discovered via web_search")`；未命中 → `register_product` + `register_asin(..., notes="discovered via web_search")`
  6. 返回 envelope：`data={"asin": asin, "marketplace": site, "brand": brand, "model": model, "product_id": pid, "registered": True, "new_product": not bool(existing)}`
- **MIRROR**: `ASIN_DUAL_BRANCH`（`api.py:1626-1643`）、`MARKETPLACE_ALIAS`（`api.py:102-113`）
- **IMPORTS**: `import re`, `from urllib.parse import urlparse`, 以及就地 `from amz_scout.db import find_product_exact, register_asin, register_product`（保持局部 import 风格，与 `discover_asin` 一致）
- **GOTCHA**:
  - URL 里可能没有 scheme（`www.amazon.de/.../dp/B0...`）→ `urlparse` 会把它当 path；在 parse 前用 `if not amazon_url.startswith(("http://", "https://")): amazon_url = "https://" + amazon_url` 兜底
  - Amazon 国际域名如 `amazon.co.jp` / `amazon.com.br` 两段 TLD 要用 `endswith` 而不是 `split(".")[-1]`
  - 不能直接对 ASIN 大写校验——正则里已限定 `[A-Z0-9]`，不需要 `.upper()`
  - 必须用 `open_db` context manager，与其它 api 函数一致
- **VALIDATE**:
  ```python
  r = register_asin_from_url("TP-Link", "AX1500", "DE", "https://www.amazon.de/dp/B0TESTTEST1")
  assert r["ok"] and r["data"]["asin"] == "B0TESTTEST1" and r["data"]["marketplace"] == "DE"
  # 再调一次入已存在产品的另一个市场
  r2 = register_asin_from_url("TP-Link", "AX1500", "UK", "https://www.amazon.co.uk/dp/B0UK12345X")
  assert r2["ok"] and r2["data"]["new_product"] is False
  # URL/市场不匹配
  r3 = register_asin_from_url("X", "Y", "DE", "https://www.amazon.co.uk/dp/B0XXXXXXXX")
  assert not r3["ok"] and "host" in r3["error"].lower()
  ```

### Task 2: `webapp/tools.py` 新增 server-side web_search schema
- **ACTION**: 在 `TOOL_SCHEMAS` 的 `query_trends` **之前**插入 `web_search_20260209` schema。`cache_control` 留在 Task 3 的最后一个工具（`register_asin_from_url`）上，保持「仅最后一个」不变量。
- **IMPLEMENT**:
  ```python
  _AMAZON_DOMAINS = [
      "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
      "amazon.it", "amazon.es", "amazon.nl", "amazon.ca",
      "amazon.com.mx", "amazon.in", "amazon.com.br",
      "amazon.co.jp", "amazon.com.au",
  ]
  # 插在 query_trends 字典前
  {
      "type": "web_search_20260209",  # dynamic filtering 内置
      "name": "web_search",
      "max_uses": 5,
      "allowed_domains": _AMAZON_DOMAINS,
  },
  ```
  `_AMAZON_DOMAINS` 常量放模块顶部；**不要**在运行时从 `config/marketplaces.yaml` 读取——schema 是启动时序列化给 Anthropic 的静态内容，不需要动态市场解析的灵活度，而且每次请求重读 yaml 会破坏 prompt caching 的哈希稳定性。
- **MIRROR**: `SERVER_TOOL_SCHEMA`（新 pattern，见 Patterns 段）
- **IMPORTS**: 无新 import
- **GOTCHA**:
  - server-side tool 必须有 `"type"` 字段且值固定为 `"web_search_20260209"`；`"name"` 必须是 `"web_search"`，不要改
  - 没有 `description` / `input_schema` 字段——写了会被 Anthropic 拒绝
  - `allowed_domains` 不写 `https://` 前缀、不写路径；只写裸 domain
  - **与 `blocked_domains` 互斥**——只能选一种；我们用 `allowed_domains` 更紧
  - **不要**声明独立 `code_execution` 工具——dynamic filtering 已内置。官方警告（tool-use-concepts.md:189）：同时声明会创建第二个执行环境、会混淆模型
  - **无需 beta header**——dynamic filtering 不需要 `anthropic-beta` header（tool-use-concepts.md:176-177）
  - **Anthropic Console 开关**：组织管理员必须在 [console.anthropic.com/settings/privacy](https://console.anthropic.com/settings/privacy) 启用 web search——否则 server tool 返回 error_code；上线前让 Jack 确认此开关
- **VALIDATE**: `test_web_search_tool_declared`：断言 `TOOL_SCHEMAS` 中存在一个 `tool.get("type") == "web_search_20260209"` 的元素，且它的 `allowed_domains` 至少包含 `amazon.com` / `amazon.de` / `amazon.co.uk` / `amazon.co.jp`；断言不存在 `tool.get("type") == "code_execution_20260120"`（明确验证"不加 code execution"的决策）

### Task 2.5: `webapp/llm.py` 新增 `pause_turn` 续跑分支
- **ACTION**: 在 `run_chat_turn` 的 `if resp.stop_reason != "tool_use": ... return` 分支**之前**，插入 `pause_turn` 处理。
- **IMPLEMENT**:
  ```python
  # webapp/llm.py, 在 run_chat_turn 的 for 循环里，
  # resp.content append 到 history 之后（约 line 74 之后）、
  # 原 "if resp.stop_reason != 'tool_use'" 判断之前插入：

  if resp.stop_reason == "pause_turn":
      # Server-side tool (web_search) hit its 10-iteration server loop cap.
      # Anthropic will resume automatically when we re-send the same
      # messages — the trailing server_tool_use block in the assistant
      # content we just appended is the continuation signal. Do NOT add
      # a fake "Continue" user message.
      logger.info("pause_turn received; re-sending to resume server loop (iter=%d)", i + 1)
      continue

  if resp.stop_reason != "tool_use":
      # Final response — extract text and return
      ...
  ```
- **MIRROR**: `shared/tool-use-concepts.md:66-79`（claude-api skill）；`python/claude-api/tool-use.md:156-161`
- **IMPORTS**: 无
- **GOTCHA**:
  - 不要在 pause_turn 分支里把 `user_query` 重新 append 成新 user message——history 已经完整，trailing `server_tool_use` 就是 resume signal
  - `max_iterations = 10` 是客户端本地的循环上限；server-side 10-iteration cap 是 Anthropic 侧独立计数——一次客户端 iter 里发生多次 server tool 调用只算一次客户端 iter
  - 如果 web_search 非常长链（罕见），可能出现 `pause_turn` → resend → 又 `pause_turn` 的连续；`max_iterations=10` 给了 10 次 `continue` 机会，够用
- **VALIDATE**: 新增 `test_pause_turn_resume` 单测：mock `_client.messages.create` 第一次返回 `stop_reason="pause_turn"`、第二次返回 `stop_reason="end_turn"` + text；断言 `run_chat_turn` 返回了 text、history 里不存在注入的 fake "Continue" user message、`messages.create` 被调用了 2 次

### Task 2.6: `webapp/llm.py` 加 20-block lookback 监控
- **ACTION**: 在 `_strip_cache_control_from_prior_tool_results` 之后、挂新 `cache_control` 之前，加一条 block 数监控日志。
- **IMPLEMENT**:
  ```python
  # 在 webapp/llm.py 的 _strip_cache_control_from_prior_tool_results(history) 之后:

  # 20-block lookback guard: each cache_control breakpoint walks back at
  # most 20 content blocks. Server tools (web_search) produce multiple
  # blocks per call (server_tool_use + web_search_tool_result + text),
  # so a single turn with 1 search + 2 register_asin_from_url rounds +
  # re-query can approach the limit. Warn so ops can catch drift.
  total_blocks = sum(
      len(m["content"]) if isinstance(m.get("content"), list) else 1
      for m in history
  )
  if total_blocks > 15:
      logger.warning(
          "history total_blocks=%d approaching 20-block cache lookback limit "
          "(web_search + register_asin_from_url chain may cause cache miss next turn)",
          total_blocks,
      )
  ```
- **MIRROR**: `shared/prompt-caching.md` §20-block lookback window
- **IMPORTS**: 无
- **GOTCHA**:
  - 阈值 15（非 20）：`>15` 给 5 个 block 缓冲——下一轮若再加 5+ 个 block 就会溢出；此时日志已出，可以追。
  - 日志级别 warning（非 error）：cache miss 不影响功能，只影响成本
  - 未来若经常踩这条线，考虑周期性插入 intermediate breakpoint（shared/prompt-caching.md 的建议），但**不在本计划范围**——留作 TODO
- **VALIDATE**: 手工检查：在 webapp 手动跑一轮带 web_search 的对话，`chainlit` stdout 里应能看到 `history total_blocks=N` 行，N 是合理值（1-10 之间的小数字，第一次对话应 < 15）

### Task 3: `webapp/tools.py` 新增 `register_asin_from_url` 客户端工具
- **ACTION**:
  1. 在 `TOOL_SCHEMAS` 末尾追加 `register_asin_from_url` schema（此后它是数组最后一个）
  2. 把 `cache_control` 从 `query_trends` 移到 `register_asin_from_url`（保持「仅最后一个」不变量）
  3. 新增 `_step_register_asin_from_url` async step wrapper
  4. `dispatch_tool` 添加分支
- **IMPLEMENT**:
  ```python
  # 顶部 imports
  from amz_scout.api import register_asin_from_url as _api_register_asin_from_url

  # TOOL_SCHEMAS 末尾（把 query_trends 的 cache_control 删掉）
  {
      "name": "register_asin_from_url",
      "description": (
          "Register a product's ASIN into the registry by parsing an Amazon "
          "product URL (of form '.../dp/<ASIN>/...'). Use after web_search "
          "returns an Amazon search result URL. Creates the product if the "
          "(brand, model) pair is new; otherwise appends the marketplace "
          "mapping. Validates that the URL host matches the target marketplace "
          "(e.g. amazon.de for DE) — rejects mismatches to prevent wrong-market "
          "writes. Does NOT consume Keepa tokens."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "brand": {"type": "string", "description": "Product brand (e.g. 'TP-Link')."},
              "model": {"type": "string", "description": "Product model (e.g. 'AX1500')."},
              "marketplace": {"type": "string", "description": _MARKETPLACE_DESC},
              "amazon_url": {
                  "type": "string",
                  "description": (
                      "Full Amazon product page URL containing '/dp/<10-char-ASIN>'. "
                      "Obtained from web_search results."
                  ),
              },
          },
          "required": ["brand", "model", "marketplace", "amazon_url"],
      },
      "cache_control": {"type": "ephemeral"},
  },

  # 上一个 query_trends 条目删掉 cache_control 行

  @cl.step(type="tool", name="register_asin_from_url")
  async def _step_register_asin_from_url(
      brand: str, model: str, marketplace: str, amazon_url: str
  ) -> ApiResponse:
      logger.info(
          "register_asin_from_url called: brand=%s model=%s marketplace=%s url=%s",
          brand, model, marketplace, amazon_url,
      )
      return await asyncio.to_thread(
          _api_register_asin_from_url,
          brand=brand, model=model, marketplace=marketplace, amazon_url=amazon_url,
      )

  # dispatch_tool 里（在最后的 "Unknown tool" 之前）
  if name == "register_asin_from_url":
      for field in ("brand", "model", "marketplace", "amazon_url"):
          if not args.get(field):
              return _missing_required("register_asin_from_url", field)
      return await _step_register_asin_from_url(
          brand=args["brand"],
          model=args["model"],
          marketplace=args["marketplace"],
          amazon_url=args["amazon_url"],
      )
  ```
- **MIRROR**: `CLIENT_TOOL_SCHEMA`（`tools.py:205-283`）、`STEP_WRAPPER`（`tools.py:303-316`）、`DISPATCH_BRANCH`（`tools.py:436-485`）
- **IMPORTS**: 在文件顶部 api import 块里加 `from amz_scout.api import register_asin_from_url as _api_register_asin_from_url`
- **GOTCHA**:
  - **顺序敏感**：`cache_control` 必须**恰好**在 `TOOL_SCHEMAS` 的最后一个元素上——现有 test `test_tool_schemas_have_cache_control_on_last`（`test_webapp_smoke.py:139-152`）会断言这一点并在前 N-1 个元素里检查不能有 `cache_control`。新加 schema 后：`web_search` 在 `query_trends` 前 → `query_trends` 删掉 `cache_control` → `register_asin_from_url` 在末尾加 `cache_control`
  - **不要**走 `summarize_for_llm` decorator：返回值不是 row list，是小 dict——套 summarize 会把 `data` 字段错误地当空行处理；直接透传
  - 4 个字段全是 required，`_missing_required` 循环必须覆盖每一个
  - ASCII safety：`description` 里用英文即可，不必混中文（让 schema size 预算测试更宽松）
- **VALIDATE**:
  ```python
  # 启动 webapp 本地跑，断言 /chat 中 "帮我把 https://www.amazon.de/dp/B0TESTTEST1 加为 TP-Link AX1500 的 DE ASIN" 能成功注册
  # 并且 tests/test_webapp_smoke.py::TestToolDispatch 全绿
  ```

### Task 4: 更新 `webapp/config.py::SYSTEM_PROMPT`
- **ACTION**: 扩展系统提示词，教 LLM「搜 → 提 URL → 注册」的流程。
- **IMPLEMENT**:
  ```python
  SYSTEM_PROMPT = (
      "You are an Amazon product data analyst assistant for GL.iNet. "
      "When the user asks about Amazon product prices, BSR, sales, deals, or sellers, "
      "call the available tools to fetch real data from the amz-scout database. "
      "Present results clearly in Chinese or English matching the user's language. "
      "Always show which tool you called and with what parameters so the user can verify.\n\n"
      "## ASIN Discovery Flow\n"
      "When a query tool returns 'not_listed' or the user asks about a product whose "
      "ASIN is not yet in the registry for the target marketplace:\n"
      "1. First confirm the product is genuinely missing by calling the relevant query "
      "tool (e.g. query_compare or query_latest).\n"
      "2. Use web_search with a query like 'site:amazon.<tld> <brand> <model>' — pick "
      "<tld> from the marketplace (UK=co.uk, DE=de, JP=co.jp, US=com, ...). "
      "Limit to 1-2 searches per product unless the first results are clearly wrong.\n"
      "3. From web_search results, pick the Amazon product page URL that best matches "
      "the user's requested product (check title, brand, model in the snippet).\n"
      "4. Call register_asin_from_url(brand, model, marketplace, amazon_url) to "
      "record the ASIN. The tool rejects the URL if its host does not match the "
      "target marketplace — do NOT retry with a different marketplace to bypass this.\n"
      "5. After successful registration, re-run the original query tool.\n"
      "6. Always show the user the registered ASIN + Amazon title from the next query "
      "so they can verify it's the right product; if wrong, advise them to contact "
      "the operator to remove the mapping.\n\n"
      "Never call register_asin_from_url with a URL the user did not see in a "
      "web_search result or in their own message — do not fabricate Amazon URLs."
  )
  ```
- **MIRROR**: N/A（提示词是内容变更）
- **IMPORTS**: 无
- **GOTCHA**:
  - **prompt caching**: `SYSTEM_PROMPT` 被包装进 `SYSTEM_BLOCKS` 并带 `cache_control`（`llm.py:18-24`）——**每次修改提示词都会重建缓存**，第一次会话会 cache miss。提交时机：和其它变更一起发布，避免两次连续 cache miss
  - **防 prompt injection**：最后一句「不要捏造 Amazon URL」是针对 web_search 返回的结果中可能混入诱导性文本的防御——必须保留
  - 提示词扩到 ~1.3 KB，仍远小于 200k input budget
- **VALIDATE**: `test_config_imports`（`test_webapp_smoke.py:32-39`）保持通过；新增 `test_system_prompt_contains_asin_flow`：断言 `config.SYSTEM_PROMPT` 含 "register_asin_from_url" 和 "web_search"

### Task 5: 更新/新增 webapp 测试
- **ACTION**: 修改 `tests/test_webapp_smoke.py`：
  1. `test_all_phase2_tool_names_present` 改名为 `test_all_expected_tools_present`（或新增一个，保留旧名做兼容别名），**扩展** expected 集合加入 `"register_asin_from_url"`；对 server-side web_search **用 type 判定**
  2. `test_tool_schemas_have_cache_control_on_last` 验证仍然只有最后一个有 `cache_control`，且最后一个是 `register_asin_from_url` 而不是 `query_trends`
  3. `test_dispatcher_routes_all_known_tools` 的 fake envelope 集合里加 `_api_register_asin_from_url`，并让 required 字段映射包含 `brand`/`model`/`amazon_url`（`"brand" -> "TP-Link"`, `"model" -> "AX1500"`, `"amazon_url" -> "https://www.amazon.de/dp/B0TESTTEST1"`）
  4. `test_dispatcher_returns_error_envelope_when_required_field_missing`：扩展 `tools_with_required` 列表加 `("register_asin_from_url", "amazon_url")` 等
  5. `test_tool_schema_size_stays_within_regression_budget`：把上限从 6000 提到 8000（新加 web_search + register_asin_from_url 大致 +1000-1500 字符）。注释更新 Phase 3 baseline
  6. **新增** `test_web_search_tool_declared`：见 Task 2 VALIDATE
  7. **新增** `test_register_asin_from_url_skips_summarize_decorator`：dispatch 成功后断言 `cl.user_session['pending_files']` 没有附件（小 dict 响应不走 summary）
- **IMPLEMENT**: 照现有测试 `_set_fake_env` + `_reset_webapp_modules` 模板写；`_api_register_asin_from_url` 打桩返回 `{"ok": True, "data": {"asin": "B0TESTTEST1", ...}, "error": None, "meta": {}}`
- **MIRROR**: `TEST_STRUCTURE`（`test_webapp_smoke.py:154-171`）、`DISPATCHER_STUB_TEST`（`test_webapp_smoke.py:173-239`）
- **IMPORTS**: 同现有测试文件
- **GOTCHA**:
  - `TOOL_SCHEMAS` 遍历里，server-side tool 没有 `input_schema`——`test_dispatcher_routes_all_known_tools` 的那个 `for prop in tool["input_schema"].get("required", []):` 会 KeyError。**修正**：先 `if "input_schema" not in tool: continue`（server-side tool 不过客户端 dispatcher）
  - `test_all_phase2_tool_names_present` 如果旧名保留了，不要让它 failed——直接重命名、在 PR description 里讲清楚
- **VALIDATE**: `pytest tests/test_webapp_smoke.py -v` 全绿；`pytest tests/ -m unit -v` 全绿

### Task 6: 新增 `amz_scout.api.register_asin_from_url` 的 API 测试
- **ACTION**: 在 `tests/test_api.py` 末尾新增一个 `TestRegisterAsinFromUrl` 类
- **IMPLEMENT**:
  - `test_happy_path_new_product`：空 DB → 调用 → 断言返回 `new_product=True`、DB 里 `products` 表有记录、`product_asins` 里有对应 marketplace + ASIN + status=active + notes 含 "web_search"
  - `test_happy_path_existing_product`：先 `add_product(... asins={"UK": "..."})` → 调用 `register_asin_from_url(... marketplace="DE", ...)` → 断言 `new_product=False`、相同 product_id 下有两个 marketplace row
  - `test_invalid_url_no_dp_segment`：URL 不含 `/dp/` → `ok=False` + error 含 "ASIN"
  - `test_url_host_mismatch`：target marketplace=`DE` 但 URL 是 `amazon.co.uk/dp/...` → `ok=False` + error 含 "host"
  - `test_marketplace_alias`：同一调用用 `marketplace="de"` / `marketplace="amazon.de"` / `marketplace="DE"` 都应成功（`_build_marketplace_aliases` 的契约）
  - `test_url_without_scheme`：`amazon.de/dp/B0TESTTEST1` 无 `https://` → 应自动补齐并成功
  - `test_international_tld`：`amazon.co.jp` / `amazon.com.br` / `amazon.com.mx` 的 URL 都能被正确识别为对应 marketplace
- **MIRROR**: 参考 `tests/test_api.py` 现有 `add_product` / `update_product_asin` 测试（如果存在——否则参考 `test_db.py` 的临时 DB fixture 模式）
- **IMPORTS**: `from amz_scout.api import register_asin_from_url`；复用 `tests/conftest.py` 的临时 DB fixture
- **GOTCHA**:
  - 测试里用 `db_path` 参数传临时 sqlite，不要污染 `output/amz_scout.db`
  - `find_product_exact` 是按归一化 key 匹配（CLAUDE.md Key Behavior 15），测试里的 brand/model 要保持一致大小写或确认归一化工作
- **VALIDATE**: `pytest tests/test_api.py::TestRegisterAsinFromUrl -v` 全绿

### Task 7: PRD 漂移修复
- **ACTION**: 在 `.claude/PRPs/prds/internal-amz-scout-web.prd.md` Phase 4 条目的 scope 描述后面追加一行 sub-note，说明「non-browser ASIN discovery via Anthropic web_search 已作为独立 PRP 交付」，并链到 `.claude/PRPs/plans/webapp-anthropic-web-search-asin.plan.md`
- **IMPLEMENT**: 在 PRD 表格行 254（Phase 4）的 PRP Plan 列或 Scope 列追加注释；保持既有 Phase 4 的 `discover_asin` (browser-use) 方案不变——这两条路径共存
- **MIRROR**: 既有 Phase 5 `PARTIAL (底层管道已交付, 2026-04-20)` 的漂移注记格式
- **IMPORTS**: 无
- **GOTCHA**:
  - **不要**把 Phase 4 状态改成 `in-progress` 或 `complete`——`discover_asin` browser-use 路径仍 pending；本计划是**另一条**路径而不是替代
  - 漂移注记必须写日期 `2026-04-23` 以便后续审计
- **VALIDATE**: `grep -n "web_search" .claude/PRPs/prds/internal-amz-scout-web.prd.md` 能看到链接；人工读一遍 Phase 4 段落确保语义清楚

### Task 8: 本地手动 Smoke
- **ACTION**: 跑 webapp 本地实例验证端到端
- **IMPLEMENT**:
  1. 确保 `.env` 有 `ANTHROPIC_API_KEY`（本就需要）——web_search 不需要额外 key
  2. `chainlit run webapp/app.py -w`
  3. 登录后在 chat 里输入「帮我把 GL-iNet Slate 7 在西班牙的 ASIN 找出来并入库」（一个真实未注册市场；`Slate 7` 在 UK/DE/JP 应已注册，ES 未必）
  4. 观察 chainlit steps：LLM 应先 `query_compare` 或 `check_freshness` 确认 ES 确实没数据，再调 `web_search`，再调 `register_asin_from_url`，再重跑 `query_compare`
  5. 检查 logs：`logger.info` 应输出 4 次调用；Anthropic `usage` dict 里会多出 `server_tool_use.web_search_requests` 字段，值为 1 或 2
  6. DB 验证：`sqlite3 output/amz_scout.db "SELECT * FROM product_asins WHERE marketplace='ES' ORDER BY id DESC LIMIT 5"` 应看到新写入的记录，notes 含 "web_search"
- **MIRROR**: N/A（手动 QA）
- **GOTCHA**:
  - 如果 LLM 不主动调 `web_search` 而是瞎编 ASIN，说明系统提示词权重不够——调高提示词中的「不要捏造」语气，或者在 `web_search` 工具 description（通过 Anthropic 官方定义，不能改）之外加 "Use web_search for ASIN lookup" 到 SYSTEM_PROMPT
  - 第一次调用 Anthropic 账单多 \$0.01；日志 `usage` 字段可见
  - **headed 浏览器**：如果不慎在 webapp 里拉起浏览器（说明 `discover_asin` 被错调用），说明 Task 4 提示词没指引到位；本计划不导入 `discover_asin` 到 webapp schema，所以理论上不会发生
- **VALIDATE**: 端到端一次成功 + logs 里 `server_tool_use` / `web_search_tool_result` 出现 + DB 新行

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_register_asin_from_url_happy_path_new_product` | 空 DB + 新 brand/model + 合法 DE URL | envelope `ok=True, data.asin=B0..., new_product=True` | — |
| `test_register_asin_from_url_existing_product` | 已有产品 UK → 写 DE | `new_product=False`, `asins count=2` | 基础 |
| `test_register_asin_from_url_invalid_url` | URL 无 `/dp/` | `ok=False, error contains "ASIN"` | 必测 |
| `test_register_asin_from_url_host_mismatch` | marketplace=DE, URL 是 amazon.co.uk | `ok=False, error contains "host"` | 必测 |
| `test_register_asin_from_url_marketplace_alias` | `"de"` / `"amazon.de"` / `"EUR"` | 全部 → canonical `"DE"` | 必测 |
| `test_register_asin_from_url_no_scheme` | `amazon.de/dp/...` 无 `https://` | 自动补齐，成功 | 必测 |
| `test_register_asin_from_url_international_tld` | `amazon.co.jp`, `amazon.com.br`, `amazon.com.mx` | 各自 canonical code | 必测 |
| `test_web_search_tool_declared` | `TOOL_SCHEMAS` | 存在 `type=web_search_20260209`, `allowed_domains` 覆盖 amazon 域 | — |
| `test_no_code_execution_tool_declared` | `TOOL_SCHEMAS` | 不存在 `type=code_execution_*`（决策回归守护） | 必测 |
| `test_pause_turn_resume` | Mock: 第 1 次 `stop_reason="pause_turn"`, 第 2 次 `"end_turn"` | `run_chat_turn` 不抛、返回最终 text、`messages.create` 被调 2 次、history 无注入的 "Continue" user message | 必测 |
| `test_history_block_count_warning_logged` | 构造 history 超过 15 blocks | logger.warning 触发一次，含 `total_blocks=` | 回归 |
| `test_register_asin_from_url_in_schemas` | `TOOL_SCHEMAS` | 存在 `name=register_asin_from_url`, 最后一项有 `cache_control` | — |
| `test_cache_control_moved_to_last_tool` | `TOOL_SCHEMAS` | 仅数组末尾元素带 `cache_control` | 回归 |
| `test_dispatch_register_asin_from_url` | 打桩 `_api_register_asin_from_url` | dispatcher 路由正确，返回 envelope | — |
| `test_dispatch_register_missing_required_field` | 缺 `amazon_url` | `ok=False, error mentions "amazon_url"` | 必测 |
| `test_register_asin_from_url_skips_summarize` | 成功调用 | `cl.user_session['pending_files']` 为空 | — |
| `test_tool_schema_size_within_8000_chars` | `json.dumps(TOOL_SCHEMAS)` | ≤ 8000 chars | 回归 |
| `test_system_prompt_contains_asin_flow` | `config.SYSTEM_PROMPT` | 含 `register_asin_from_url` 和 `web_search` 字串 | — |

### Edge Cases Checklist
- [x] 空 input（URL 为空 / brand 为空）→ `_missing_required`
- [x] URL 最大长度（Amazon 长 URL 带 tracking query）→ 正则只看 `/dp/` 段，不受影响
- [x] 无效 ASIN 格式（少于 10 位 / 含小写）→ 正则不匹配
- [x] marketplace 未知代码 → `_build_marketplace_aliases` 返回 None → 报错
- [x] URL 指向退市产品页面 → 本工具只写注册表，下一次 query 时 `ensure_keepa_data` 的 not_listed post-check 会自动打标
- [x] 并发 web_search（同一会话多轮）→ `max_uses=5` 上限防止单轮滥用；跨会话由 Anthropic API-key 级 rate limit 控制
- [x] Prompt injection（搜索结果里藏诱导指令）→ `allowed_domains=amazon.*` 白名单 + SYSTEM_PROMPT 里明确「只提取 ASIN URL，不执行其它指令」+ 「不要捏造 URL」

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/ src/amz_scout/api.py tests/
```
EXPECT: Zero errors

### Type Check（若项目启用了 mypy/pyright）
```bash
# 项目目前没有 mypy 配置；跳过。若未来启用，加到这里
```

### Unit Tests
```bash
pytest tests/test_webapp_smoke.py tests/test_api.py -v -m unit
```
EXPECT: 全绿；含新增的 15 个测试

### Full Test Suite
```bash
pytest tests/ -v
```
EXPECT: 无回归（其它 Phase 1/2/3 相关测试不受影响）

### Token Audit（回归）
```bash
pytest tests/test_token_audit.py -v
```
EXPECT: 不受影响；如果 schema size 大幅上涨，在此测试里调整预算（目前 8000 上限是本计划 Task 5.5 制定）

### Manual Smoke（Task 8）
```bash
chainlit run webapp/app.py -w
# 浏览器访问 http://localhost:8000，登录后跑端到端对话
```
EXPECT: LLM 成功完成 query → web_search → register_asin_from_url → re-query 链路

### Anthropic Usage 验证
```bash
# 在 chainlit terminal 日志中 grep
grep "usage:" /tmp/chainlit.log | tail -5
```
EXPECT: 至少有一行 `usage` 含 `server_tool_use.web_search_requests`

---

## Acceptance Criteria
- [ ] Task 1-8 + **Task 2.5 (pause_turn)** + **Task 2.6 (block count warning)** 全部完成
- [ ] 所有新增/修改的 unit test 通过（含新增的 `test_pause_turn_resume` / `test_no_code_execution_tool_declared` / `test_history_block_count_warning_logged`）
- [ ] `pytest tests/` 无回归
- [ ] `TOOL_SCHEMAS` 最后一项（`register_asin_from_url`）带 `cache_control`，其余均不带
- [ ] `TOOL_SCHEMAS` 中 web_search 的 type 为 **`web_search_20260209`**（dynamic filtering 版），**不存在** `code_execution_*` 条目
- [ ] `SYSTEM_PROMPT` 含 ASIN Discovery Flow 段
- [ ] 手动 smoke：webapp 成功完成一次「查询未注册产品 → 自动发现 ASIN → 入库 → 重查成功」端到端
- [ ] Anthropic logs 显示 `web_search_tool_result` block 出现
- [ ] DB 中新注册记录的 `notes` 含 "web_search"（用于区分来源）
- [ ] 上线前 Jack 确认 Anthropic Console privacy 设置的 web search 开关已开启
- [ ] PRD Phase 4 条目下增加 sub-note 指向本 plan

## Completion Checklist
- [ ] 代码符合发现的模式（`MARKETPLACE_ALIAS` / `ASIN_DUAL_BRANCH` / `STEP_WRAPPER` 等）
- [ ] 错误处理对齐 `_envelope(False, error=...)` 模式
- [ ] 日志用 `logger.info` / `logger.exception`（与 api.py/tools.py 风格一致）
- [ ] 测试遵循 `_reset_webapp_modules` + `monkeypatch` 模板
- [ ] 无硬编码（marketplace 列表从 yaml；ASIN 正则在工具内定义一次）
- [ ] `CLAUDE.md` 不需要改（该文档是给 AI 读的，web_search 作为 webapp-internal 工具不影响 Python API 用法）
- [ ] 无不必要范围扩展（`discover_asin` 原样保留，`batch_discover` 不动）
- [ ] 计划自足——实现者不需要再读这一段之外的 PRD 或源码

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM 在系统提示词未生效前捏造 ASIN | Low-Medium | Medium | Task 8 smoke 手动验证；`register_asin_from_url` 的 URL host 校验是第二道防线（随便捏一个 `amazon.de/dp/FAKEFAKEFK` 也要 LLM 编一个符合格式的 ASIN，而 web_search 真实结果不会给这种） |
| **Anthropic Console 未启用 web search 权限** | Medium | High | 上线前 Jack 登录 [console.anthropic.com/settings/privacy](https://console.anthropic.com/settings/privacy) 确认 "Web search" 开关开启；未开启会返回 error_code 导致每次 web_search 失败 |
| **`pause_turn` 未处理，半成品回答返给用户** | Medium（若跳过 Task 2.5）| High | Task 2.5 显式实现 `pause_turn` 续跑分支；`test_pause_turn_resume` 单测回归验证；`webapp/llm.py` 现有代码默认把 `stop_reason != "tool_use"` 当终止，会直接返回没有文本的 message——必须修 |
| **20-block lookback 触发 cache miss** | Medium-High（长对话） | Low（只是成本） | Task 2.6 的 warning 日志做监控；短期不处理，长期若频繁触发则在单轮 mid-turn 加 intermediate breakpoint（shared/prompt-caching.md 模式） |
| Anthropic web_search 对 `claude-sonnet-4-6` 不可用 | Low | High | 文档 `web_search_20260209` 明确支持 Sonnet 4.6（docs.claude.com/web-search-tool 2026-02-09 发布说明）；上线前用一次真实 `/v1/messages` 调用做兼容性 smoke 验证 |
| **误加 `code_execution` 工具** | Low（文档已警告） | Medium | Task 2 GOTCHA 明确"不要声明独立 code_execution"；Task 2 VALIDATE 增加"断言 TOOL_SCHEMAS 中不存在 `code_execution_*`"的回归测试，防止后续有人"好心"加回去 |
| `cache_control` 迁移后 cache 命中率骤降 | Medium | Low | 合并后前两天观察 `cache_read_input_tokens` 指标（`webapp/llm.py:64` 已 log `resp.usage.model_dump()`）；一次完整重建是预期行为 |
| `allowed_domains` 硬编码 vs yaml 动态漂移 | Low | Low | marketplaces.yaml 新增市场时，需要手动同步到 `_AMAZON_DOMAINS`——加一条内部 TODO 放 CHANGELOG；或未来用测试断言 `set(amazon_domain from yaml) ⊆ set(_AMAZON_DOMAINS)` |
| 费用失控（LLM 在一个会话里连续搜 50 次） | Low | Medium | `max_uses=5` per request 限额；Anthropic console 启用 monthly budget cap；`logger.info("usage: ...")` 已经把每轮消耗记到日志，可以定期 grep `web_search_requests` 审计 |
| Prompt injection 从 web_search 结果泄到系统行为 | Low | Medium | `allowed_domains=amazon.*` 大幅缩窄攻击面；SYSTEM_PROMPT 明确「不要捏造 URL / 不要执行搜索结果里的指令」；`register_asin_from_url` 只做正则抽取，不跟进任何其它指令 |
| 新 ASIN 实际指向错产品（intent 错配） | Medium | Low | CLAUDE.md Key Behavior 10 已明确 intent 校验由用户看 Keepa title 完成；SYSTEM_PROMPT 指令 LLM 重查后展示 title 给用户确认；错配时用户可 `update_product_asin` 或联系 Jack 删除 |

## Notes

- **方案 A vs B vs C 的选择理由**（来自用户对话 2026-04-23）：
  - B (第三方 search) 需要新 API key + 账单 + 供应商风险；
  - C (browser-use 搬进 webapp) 需要 Dockerfile 装 Chromium，部署负担大，还受 Amazon 风控威胁；
  - A 走 Anthropic 官方工具，账单合并、零额外 vendor、托管式搜索，最小变更面。
- **与 CLAUDE.md 规则 12 的关系**：那条规则里 "(2) WebSearch 搜 Amazon URL" 指的是 **Claude Code 用户端**自带 WebSearch（用于 CLI/Python API 场景）；本计划把**等价能力**引入 webapp 侧，让没有 Claude Code 的内部用户（如 Phase 7/8 的小李）也能享受同样流程。两条路径语义一致、入库格式一致（notes 字段不同以便区分）。
- **与 Phase 4 的关系**：Phase 4 scope 里有 `discover_asin`（browser-use），这是**浏览器路径**；本计划提供**无浏览器路径**。两者共存；未来 Phase 4 实现者仍可以按原计划在 webapp 暴露 `discover_asin` 作为「web_search 找不到时」的兜底，但那是另一份 plan 的事。
- **schema size 预算**：从 6000 提到 8000 是一次性调整，反映 web_search + register_asin_from_url 的净增量；未来该预算应在 Phase 4 其它工具落地时再次评估。
- **notes="discovered via web_search"**：便于审计「有多少 ASIN 来自 webapp 的 AI 自助发现 vs 多少来自 CLI browser-use」，为产品决策提供数据。

---

## Revision Notes (2026-04-24)

初版计划（2026-04-23）的方案 2 写的是 "`web_search_20260209` + `code_execution_20250522`"，**这是错的**。经过 `/claude-api:claude-api` skill 的权威文档审查（`shared/tool-use-concepts.md:176-189`、`python/claude-api/tool-use.md:156-161`），以下两条事实修正了计划：

### 修正 1：`web_search_20260209` 的 dynamic filtering 是内置的，**不**需要也**不应该**声明 `code_execution`

官方原文（shared/tool-use-concepts.md:176-189）：

> Dynamic filtering is built into these tool versions and activates automatically; **you do not need to separately declare the `code_execution` tool or pass any beta header.**
>
> **Only include the standalone `code_execution` tool when your application needs code execution for its own purposes** (data analysis, file processing, visualization) independent of web search. **Including it alongside `_20260209` web tools creates a second execution environment that can confuse the model.**

换言之：
- 同时声明会**混淆**模型，降低准确度；
- web_search 内部用的 code execution **免费**（和 web search 一起用时不计费）；
- 单独声明 code execution 若要用，当前正确版本号是 `code_execution_20260120`（不是最初写的 `code_execution_20250522`）——但我们用不上。

### 修正 2：`webapp/llm.py` 必须显式处理 `pause_turn`

server-side tool 的 server loop 默认上限 10 iterations，超过后 `stop_reason="pause_turn"`。当前 `webapp/llm.py:75-79` 只有 `!= "tool_use"` 一条出口，会把 `pause_turn` 当成 end_turn，**返回没有 text 的半成品**。已新增 Task 2.5 修复。

### 新增 Task 2.6：20-block lookback 监控

`shared/prompt-caching.md` §20-block lookback window：每个 `cache_control` breakpoint 向前最多 20 blocks 找匹配。server tool 每次调用产生多个 blocks（`server_tool_use` + `web_search_tool_result` [+ 过滤后的 text]），单轮若包含 1 次 web_search + 2 次 `register_asin_from_url` + 重查，就可能 ~10 blocks；第二轮就会接近 20 上限导致 cache miss。Task 2.6 加日志监控；后续若常触发，再考虑加 intermediate breakpoint。

### 价格影响

| 项目 | 20250305 | 20260209 |
|---|---|---|
| web search 计费 | \$10/1000 | \$10/1000（**不变**） |
| dynamic filtering 内 code execution | N/A | **免费**（与 web search 共用时不计费） |
| token 消耗 | 原样结果入 context | 过滤后入 context → 少量节省 |

对于 amazon.* 白名单已经很窄的 ASIN 场景，dynamic filtering 的实际 token 节省不大——但**不会**比旧版贵。

### 模型 & beta header

- `claude-sonnet-4-6`（webapp 当前 MODEL_ID）✅ 支持 `web_search_20260209` + dynamic filtering
- **无需** `anthropic-beta` header（官方明确说明）
- 上线前须在 Anthropic Console privacy 设置里启用 web search 权限（见 Risks 表对应行）

### 决策审计

之所以**不**选方案 1（保留 `20250305` 不升级），理由：方案 2 是用户明确选择；新版在我们场景下性价比等同或略好；未来新模型可能不再支持旧版本（Anthropic 通常保留旧版 1-2 年），升级一次省后续迁移。本计划不暴露方案 3（两阶段先旧后新）因为一次性升级更简单。
