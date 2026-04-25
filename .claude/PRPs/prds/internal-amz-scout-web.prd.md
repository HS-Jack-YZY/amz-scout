# Internal amz-scout Web UI

## Problem Statement

At GL.iNet, 5 PMs and market analysts currently rely on Jack (the owner of this PRD) to generate custom Amazon product research reports — historical prices, BSR, sales, deals, seller history — across multiple marketplaces. Requests are sent as Excel sheets listing competitor + own-product ASINs; Jack runs CLI commands from the existing `amz-scout` tool and returns nested CSV bundles, typically taking **1-2 days per request** and **~2 hours of Jack's focused time**. This creates a bottleneck that blocks time-sensitive pricing, product selection, and listing decisions, consumes Jack's strategic time on repetitive data plumbing, and leaves `amz-scout`'s value locked to Jack's terminal — inaccessible to the rest of the team despite being fully functional.

## Evidence

- **Scenario 1 (quantified, recent)**: Primary User 小李 (PM, router category + new product exploration) sent Jack an Excel listing ~5 competitors and 1 own-product. Jack produced a nested CSV bundle organized as `{country}/{data_type}/{product}.csv`. Total time: ~2 hours of Jack's focused work; 小李 received it later and then imported to Excel for his own analysis.
- **Scenario 2 (quantified, recurring)**: A second colleague asks for daily price/sales/deal tracking for a handful of ASINs across countries, reported every morning. **Explicitly deferred to v1.1** — requires scheduled tasks and push notifications, orthogonal to MVP.
- **Fallback behaviors confirmed**:
  - All 5 colleagues own Keepa / Helium10 / JungleScout accounts.
  - These tools have no natural-language AI layer — users must know query syntax and navigate multiple tabs.
  - Cross-product comparison in Keepa requires opening N tabs.
  - Alternative path is "find Jack" → blocks on Jack's availability, often >1 day.
- **Jack's own motivation (Why now)**: Pulled into increasingly frequent ad-hoc data requests, losing time from strategic work. This is the primary trigger — not a new product launch, not executive mandate, not budget pressure.
- **Baseline gap**: Exact count of monthly data requests is **not currently tracked**. PRD assumes "multiple per month" but the true baseline must be measured during 2-week pre-launch observation to validate the "drop to ≤2" metric.

## Proposed Solution

Wrap the existing, production-ready `amz_scout.api` programmatic interface (25 typed functions with uniform `{ok, data, error, meta}` envelope) with a **Chainlit-based natural-language chat UI** deployed to AWS, gated by `@gl-inet.com` email whitelist, powered by **Claude Sonnet 4.6 with function calling**. The LLM translates natural-language questions into typed API calls; all results render as conversation messages **and** downloadable Excel/CSV files.

The web layer is a thin adapter — **zero modifications** to `amz_scout.api`, SQLite schema, Keepa integration, or browser scraping logic. A new `webapp/` module imports `amz_scout.api` as a library and exposes its functions to the LLM as Chainlit tools.

**Why function calling over text-to-SQL (the architectural bet)**
Industry research (Berkeley BFCL, Promethium enterprise benchmarks) shows LLM free-form SQL caps at ~10-20% production accuracy on heterogeneous data, while constrained function calling over typed interfaces achieves significantly higher accuracy. The existing `amz_scout.api` with its uniform envelope is already the correct substrate for this approach — this PRD's job is to expose it through a chat surface, not to rebuild the data layer.

## Key Hypothesis

**We believe** a Chainlit-based natural-language chat UI wrapping `amz_scout.api`'s function-calling interface with Excel export support
**will** eliminate GL.iNet's PMs/analysts' bottleneck of "send Excel list to Jack → wait 1-2 days for manual CSV bundle → import to Excel for analysis"
**for** 6 GL.iNet internal employees (5 colleagues + Jack).

**We'll know we're right when all 3 hard metrics are met:**

1. **Substitution**: Jack's monthly ad-hoc data requests drop to ≤2 (baseline TBD via 2-week pre-launch observation)
2. **Speed**: Average time from "colleague has question" to "colleague has answer" drops from ~1 day to ≤5 minutes
3. **Quality**: User-reported answer error rate (Chainlit thumbs up/down) stays <10%

**AND the soft metric** (to defend against silent failure):

4. **No silent failure**: Every week Jack asks 1 colleague "is there anything you wanted to query but the tool couldn't help?" — 2 consecutive weeks of "nothing" = soft-verified.

## What We're NOT Building

| Out of Scope | Why |
|---|---|
| Scenario 2 (scheduled monitoring / daily push notifications) | Orthogonal to MVP; needs scheduler + notification channel. Deferred to v1.1. |
| Non `@gl-inet.com` users | Internal tool positioning. External / SaaS is a separate future PRD. |
| Company executives / KPI dashboards | Different job (summary view, not interactive research). |
| Sales / procurement teams | Their data needs are internal inventory/orders, not Amazon market data. |
| PDF / Word / email export formats | Excel/CSV covers 小李's workflow; other formats are gilding. |
| Cross-session chat history recovery beyond Chainlit defaults | Chainlit's built-in history is sufficient for MVP. |
| Data sources beyond Keepa + Amazon scraping (e.g., Helium10 API) | Current sources already cover the research job; more sources = more tech debt. |
| Keepa plan upgrade (€49 → €459/month, 60 → 250 tokens/min) | 6-user scale doesn't need it. Upgrading would mask a healthy "cache-first" design constraint. |
| Automated tests / CI/CD | MVP first, test scaffolding after. **Exception**: at least one end-to-end smoke test before W3 Beta launch. |
| Multi-language UI (English version) | All 6 users are Chinese-native or fluent. |
| Formal IT audit logging / compliance | Chainlit built-in chat history + SQLite backup is sufficient for post-hoc reconstruction. |
| Multi-tenant / per-user token quotas | 6-user scale doesn't need it. |
| Mobile-optimized UI | Colleagues use the tool from company laptops. |
| Company SSO (Google Workspace OAuth) | Email whitelist is sufficient for MVP. SSO is v1.1+ if use case expands. |
| Anthropic Zero Data Retention (ZDR) application | **Deferred, not canceled**. Tracked in project memory for post-MVP reminder. |
| Additional query result caching beyond existing `amz_scout.api` DB cache | Premature optimization — observe real usage first. |
| 浏览器版 `discover_asin` UI 包装 | **Council B− (2026-04-24)**: 已被 Anthropic `web_search_20260209` 替代（PR #21）；CLI 代码保留作 fallback，不在 Chainlit UI 中暴露入口 |
| 管理工具 UI 包装（`list_products` / `add_product` / `remove_product_by_model` / `update_product_asin` / `register_market_asins` / `import_yaml`） | **Council B− (2026-04-24)**: 小李等 PM 用户不需要在 chat 里管理注册表；Jack 作为工具作者用 `amz_scout.api` 直接调用成本可接受；deferred-to-v1.1 |
| `batch_discover` / `sync_registry` 的 UI 包装 | **Council B− (2026-04-24)**: MVP 6 用户规模无人需要在 UI 里触发这类批量操作；Jack 用 API 直接调；deferred-to-v1.1 |
| Multi-sheet xlsx per product × market（在 Alpha 反馈前） | **Council B− (2026-04-24) — defer-pending-alpha-feedback**: 改用"跟访 > 预访谈"，根据小李第 1-2 次真实研究任务中是否手动拆分单表 / 是否主动要求，决定是否回补（xlsx 管道可在 1-2h 内改造） |

**Note**: "Non-router product categories" is **not** in Out of Scope. Since the LLM is a general analysis engine with no router-specific prompting, the tool is inherently category-agnostic and naturally supports 小李's new-product exploration across any category.

## Success Metrics

| Metric | Target | How Measured | Baseline |
|---|---|---|---|
| Jack's ad-hoc data requests | ≤2/month | Jack self-tallies monthly | TBD — collect 2 weeks pre-launch |
| Time from question to answer | ≤5 minutes | Chainlit session timestamps (query start → final answer) | ~1 day (from fallback interviews) |
| Answer error rate | <10% | Chainlit thumbs up/down logged to SQLite | N/A (new metric) |
| MVP validation task completion | ≥3 real research tasks by end of W3; ≥2 by 小李 independently | Manual tracking | N/A |
| Silent-failure check | 2 consecutive weeks of "nothing missing" | Jack weekly 1:1 with a colleague | N/A |

## Open Questions

- [ ] **Q1 — LLM translation accuracy**: Can Sonnet 4.6 with the tool definitions in this PRD stably translate queries like "Slate 7 在德国上周的价格" into `query_trends(product="Slate 7", marketplace="DE", days=7)`? Risk: Medium. Answer by: W1 D3 end-to-end validation.
- [ ] **Q2 — browser-use on AWS headless Linux**: Any unexpected pitfalls when running browser-use CLI + Chromium inside Lightsail Docker (download slowness, RAM, font issues)? Risk: Medium. Answer by: W2 D5 deployment test.
- [ ] **Q3 — 小李's Excel export format preference**: ~~Single sheet or multi-sheet? Formulas, pivot tables, conditional formatting needed? Risk: Low-Medium. Answer by: W1 D1 — 30 min interview with 小李.~~ **Revised 2026-04-24 per council B−**: 方法从"预访谈"改为 Alpha 首周 30min 跟访（行为 > 陈述）。Answer by: Phase 7 Alpha 的第 1-2 次真实研究任务之后。Risk: Low-Medium.
- [ ] **Q4 — Other 4 colleagues' real use cases**: Are they actually aligned with 小李 (scenario 1), or does anyone actually need scenario 2 (daily monitoring)? If the latter, MVP won't meet the substitution metric. Risk: **High** — could invalidate metric 1. Recommended action: 10-minute interview with each of the other 4 **before** W3 Beta launch (total cost ~40 minutes; potential savings ~3 days of rework).
- [ ] **Q5 — Real Keepa token burn rate**: With 6 real concurrent users, what's the actual monthly token consumption? Risk: Low. Answer by: first 2 weeks post-launch monitoring.
- [ ] **Q6 — Chainlit thumbs up/down response rate**: If users don't click, the quality metric is unmeasurable. Mitigation: Jack prompts 小李 during W3 Alpha to click feedback explicitly so habit forms. Risk: Low for indicator integrity.
- [ ] **Q7 — Anthropic ZDR application**: Apply after MVP launch. Tracked in project memory (`project_web_deploy_zdr_todo.md`). Reminder trigger: when amz-scout web app goes to Beta or full internal rollout. Risk: Medium (sensitive competitive data exposure).
- [ ] **Q8 — 浏览器抓取路线是否应降级**（新增 2026-04-24 per council B−）: `competitive_snapshots` 表中的独有字段，Keepa 是否能覆盖 ≥80%？若是 → 在 Decisions Log 正式标记浏览器路线为 `deprecated-candidate`，停止所有新投资。Risk: Low（审计本身很便宜；错误的答案代价是继续维护一条冗余路线）。Answer by: Phase 3.5 Part A（30min）。

---

## Users & Context

**Primary User**

- **Who**: 小李 (pseudonym), Product Manager, GL.iNet router category + new product category/line exploration
- **Current behavior**: Sends Excel lists to Jack with competitor + own-product ASINs, waits 1-2 days for nested CSV bundles, imports to Excel for analysis
- **Trigger**: New product pricing meeting 1-3 days away, realizes required data is missing or stale from existing Excel sheets
- **Success state**: Self-serve natural-language query in a web page, same-day (ideally same-hour) answer as chart-rendered chat reply AND downloadable Excel for deeper analysis in his power-Excel workflow
- **AI usage maturity**: **Medium** — writes multi-turn prompts with ChatGPT/Claude, no JSON/SQL/API fluency, power Excel user. Will expect natural-language input AND structured output artifacts.

**Job to Be Done**

> When I'm doing research for a new product (or an existing product's pricing/selection/listing decision) and I need cross-country, cross-competitor, multi-dimensional Amazon historical data — price, BSR, sales, deals, seller history — I want to ask questions in natural language on a web page, have the tool automatically pull data from Keepa and Amazon, and get answers as charts + tables + downloadable Excel, so that I don't have to open 5 Keepa tabs, don't have to find Jack and wait 1-2 days, don't have to maintain stale Excel sheets, and can complete a research task and bring conclusions to a decision meeting within the same day (ideally same hour).

**Secondary Users**

The other 4 colleagues (PMs + market analysts). Their specific use cases are formally unverified — see **Open Question Q4**. MVP UX is optimized for 小李; the other 4 are side-effect beneficiaries until Q4 is answered.

**Non-Users (MVP)**

- Only `@gl-inet.com` email whitelisted accounts (6 people total: 5 colleagues + Jack)
- Not for external users, partners, or customers
- Not for company executives (no dashboard/KPI view)
- Not for sales/procurement teams (their data needs are internal, not Amazon-facing)

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|---|---|---|
| **Must** | Natural-language chat UI (Chainlit + Sonnet 4.6 with function calling) | Core interaction paradigm |
| **Must** | Wrap the 9 read-only query functions from `amz_scout.api` as Chainlit tools (`query_latest`, `query_trends`, `query_compare`, `query_ranking`, `query_availability`, `query_sellers`, `query_deals`, `check_freshness`, `keepa_budget`) | Covers every Scenario-1 query need |
| **Must** | Excel / CSV export as `cl.File` attached to every query reply | 小李's workflow terminates in Excel — missing this = falling back to Jack |
| **Must** | `@gl-inet.com` email whitelist auth (Chainlit password auth with manually-created accounts) | Access control, non-negotiable |
| **Must** | AWS deployment (Lightsail + Docker + HTTPS) | Without deployment it's not "internal tool for colleagues" |
| **Must** | Live Keepa token budget display in top bar | 6 users don't burn budget, but visible cost is a healthy design anchor |
| **Should** | `auto_fetch=True` on `query_trends/query_sellers/query_deals` so queries auto-refresh missing Keepa data | Makes the natural-language experience seamless |
| **Should** | `cl.Step` progress display for long-running tools | Prevents "did it freeze?" misinterpretation |
| **Should** | Chainlit built-in thumbs up/down feedback stored in SQLite | Required infrastructure to measure the <10% error rate |
| ~~Could~~ **Won't (MVP)** | Wrap product registry tools (`list_products`, `add_product`, `remove_product_by_model`, `update_product_asin`, `register_market_asins`, `import_yaml`) | **Council B− (2026-04-24)**: 对小李透明，Jack 用 `amz_scout.api` CLI 管理成本可接受；"open all permissions" 是对 API 完整性的承诺，不是对每个 API 都加 UI 的承诺。Deferred to v1.1 |
| **Must (subset)** | `ensure_keepa_data` 确认对话框（Phase 3.5） | Council B− (2026-04-24): 唯一的 token 烧光风险点，Alpha 前必须堵上；消费 `amz_scout.api` 已有的 `phase="needs_confirmation"` 协议 |
| ~~Could~~ **Won't (MVP)** | 浏览器版 `discover_asin` 的 UI 包装 | **Council B− (2026-04-24)**: 已被 Anthropic `web_search_20260209` 替代（PR #21）；CLI 代码保留作 fallback，不加 UI 入口；避免给待废路线加重 user-facing surface area |
| ~~Could~~ **Won't (MVP)** | `batch_discover` / `sync_registry` 的 UI 包装 | **Council B− (2026-04-24)**: 6 用户规模无人需要在 UI 里触发批量操作；Jack 用 API 直接调成本可接受。Deferred to v1.1 |
| **Won't (MVP)** | Multi-sheet xlsx per product × market | **Council B− (2026-04-24)**: defer-pending-alpha-feedback；改用"跟访 > 预访谈"，根据 Alpha 首周真实使用行为决定是否回补（xlsx 管道已就绪，1-2h 可改造） |
| **Won't** | Scheduled monitoring (scenario 2) | Deferred to v1.1 |
| **Won't** | Multi-tenant / per-user quotas | 6-user scale doesn't need it |
| **Won't** | Mobile UI optimization | Company laptops only |
| **Won't** | Company SSO (Google Workspace OAuth) | Email whitelist is sufficient for MVP |
| **Won't** | Anthropic ZDR application (deferred, reminder stored) | Don't block MVP on this |
| **Won't** | Query caching beyond existing `amz_scout.api` DB cache | Premature optimization |

### MVP Scope (Validation Target)

**小李 must be able to complete one real new-product pricing research task end-to-end**: enter 4 competitor ASINs + 1 own-product ASIN (or product names), query UK/DE/US across the past 6 months for price curve + BSR + sales-volume changes, receive the reply as a chart-rendered chat message AND a downloadable Excel file, **without Jack's intervention**, within ≤30 minutes wall-clock (waiting ≤10 min, self-analysis ≤20 min).

**MVP success gate**: At least 3 real (not demo) research tasks completed end-to-end during Week 3 Beta, with ≥2 completed by 小李 independently.

### User Flow (Golden Path)

1. 小李 opens `https://amz-scout.<gl-inet-internal-host>` in a browser
2. Logs in with `xiaoli@gl-inet.com` + password (account manually provisioned by Jack on first rollout)
3. Types: *"帮我对比 GL-Slate 7 AX 和 TP-Link BE5400 在 UK/DE/US 过去 6 个月的价格走势和 BSR 变化"*
4. Chainlit UI shows Sonnet 4.6's tool calls as expandable `cl.Step` nodes (`query_trends` → `query_compare`), each showing resolved parameters + raw envelope response
5. Final reply renders as a chat message with 2 charts (price + BSR) and a summary table
6. Reply also includes a `cl.File` attachment: `slate7_vs_be5400_uk_de_us_6months.xlsx` (multi-sheet: one sheet per product × market)
7. 小李 downloads the Excel, imports into his existing analysis workbook, brings conclusions to the pricing meeting
8. 小李 clicks 👍 on the reply → feedback logged to SQLite for quality metric
9. (Optional) 小李 asks a follow-up: *"再帮我看看这两个产品在德国有没有打过折"* → Sonnet chains to `query_deals(product=..., marketplace="DE")` using the same session context

---

## Technical Approach

**Feasibility**: **HIGH**. Core API layer (`amz_scout.api`) is production-ready with uniform `{ok, data, error, meta}` envelope; WAL-mode SQLite is concurrent-safe for 6 users; `browser-use` default is headless-friendly. Zero code changes needed to the existing `amz_scout/` package — the web layer is a thin `webapp/` adapter.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser (6 users on @gl-inet.com emails)           │
│  https://amz-scout.<gl-inet-internal-host>          │
└──────────────────┬──────────────────────────────────┘
                   │ HTTPS
┌──────────────────▼──────────────────────────────────┐
│  AWS Lightsail instance (2GB / 2vCPU, ~$10/month)   │
│  ┌──────────────────────────────────────────────┐  │
│  │  Chainlit app (single Python process)         │  │
│  │  ├─ Auth: Chainlit password_auth_callback     │  │
│  │  │        + @gl-inet.com email whitelist      │  │
│  │  ├─ LLM: Claude Sonnet 4.6 + prompt caching   │  │
│  │  ├─ Tools: ~20 functions wrapped from          │  │
│  │  │          amz_scout.api via @cl.step         │  │
│  │  ├─ Feedback: Chainlit thumbs up/down          │  │
│  │  │            → SQLite (feedback table)        │  │
│  │  └─ Export: pandas → cl.File (XLSX/CSV)        │  │
│  │                                                │  │
│  │  ┌────────────────────────────────────────┐  │  │
│  │  │  amz_scout.api (imported as library,   │  │  │
│  │  │                 ZERO modifications)     │  │  │
│  │  └────────────────┬───────────────────────┘  │  │
│  │                   │                            │  │
│  │  ┌────────────────▼───────────────────────┐  │  │
│  │  │  SQLite WAL (/data/amz_scout.db)       │  │  │
│  │  │  + Keepa raw JSON cache directory      │  │  │
│  │  └────────────────────────────────────────┘  │  │
│  │                                                │  │
│  │  browser-use CLI (headless Chromium)           │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  EBS volume: 20GB (persistent /data)                │
│  Security Group: 443 + 22 (SSH from Jack's IP)      │
│  Automated daily snapshots (Lightsail built-in)     │
└──────────────────┬───────────────────────────────────┘
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
   Keepa API                Anthropic API
   (60 tokens/min)          (Sonnet 4.6 + caching)
```

### Technology Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Frontend framework | **Chainlit 2.x** | Native LLM chat + tool-call step visualization + built-in feedback mechanism + SQLite-backed persistence. Streamlit's rerun-every-interaction model fights with streaming LLM chat. |
| Tool wrapping pattern | `@cl.step` decorator on thin wrappers that call `amz_scout.api` functions | Every LLM tool call surfaces as an expandable UI node showing resolved params and raw envelope |
| LLM model | **Claude Sonnet 4.6** (via Anthropic API) + prompt caching for system prompt + tool defs | Accuracy for multi-step tool chains; Haiku risks losing context across long chains → violates <10% error target |
| Authentication | Chainlit `password_auth_callback` + explicit email whitelist | MVP-minimum: Jack manually creates 6 accounts, no self-registration. Google Workspace OAuth deferred to v1.1. |
| Deployment | **AWS Lightsail 2GB/2vCPU** | Simplest managed option; bundles SSD + bandwidth + snapshots for ~$10/month. EC2/ECS are overkill for 6 users. |
| Container base image | `python:3.12-slim-bookworm` | Small image, Debian-based (compatible with browser-use CLI + Playwright Chromium) |
| `browser-use` installation | `uv tool install browser-use` in Dockerfile + Playwright headless Chromium | Default `headed=False` works on headless Linux (confirmed via Phase 3 code audit) |
| SQLite storage | EBS volume mounted at `/data`, daily snapshots | WAL mode already enabled in `db.py`; survives container restart |
| Keepa API key management | `.env` file with `chmod 600`, mounted into container | MVP-acceptable for 6-user internal tool. AWS Secrets Manager is v1.1+ if compliance tightens. |
| LLM prompt caching | Anthropic prompt caching on system prompt + tool schemas | System prompt and 20+ tool definitions are static text → ~90% input-token savings vs. uncached |
| Long-running task display | Chainlit `cl.Step` with `async` tool functions | 6-user concurrency is low enough that Celery/RQ is unnecessary; Chainlit native steps are sufficient |
| Excel export | pandas → XLSX via `openpyxl`, returned as `cl.File` attached to chat reply | Auto-renders as a download button in Chainlit UI |

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **LLM parameter translation errors** (wrong marketplace, wrong series type, missing/extra days param) | Medium | Medium | (1) Detailed Chinese + English docstrings on each Chainlit tool. (2) Every tool response displays resolved parameters + data source so user can self-verify. (3) Chainlit thumbs-down reveals bad patterns over time. (4) Use `amz_scout.api`'s marketplace aliases (accepts `uk`/`GB`/`amazon.co.uk`/`GBP` — already implemented) to absorb LLM variation. |
| **browser-use CLI fails on AWS headless Linux** | Low-Medium | Medium | Default `headed=False` uses built-in headless Chromium (confirmed via Phase 3 code audit at `browser.py:24,36`). Verify in W2 D5 deployment smoke test. Never expose `headed=True` to users. |
| **Long-running tools** (`batch_discover`) **block UI** | Medium | Low (MVP scope) | `cl.Step` shows progress; explicit confirmation dialog before starting; 5-minute timeout with partial results; `batch_discover` honors existing `phase="pending_confirmation"` protocol to gate execution. |
| **Keepa token burn from concurrent users** | Low | Low | 60 tokens + 1/min refill is comfortable for 6 users. Token budget visible in top bar. Existing `phase="needs_confirmation"` gates ≥6-token operations — front-end just consumes it. |
| **Sonnet 4.6 monthly cost exceeds expectations** | Low | Low | Prompt caching from day 1 (~90% input token savings). Expected $40-80/month. Monitor weekly via Anthropic usage dashboard. Hybrid Haiku/Sonnet routing is a v1.1 optimization if needed. |
| **Sensitive competitive data routed through Anthropic API** | Medium | Medium | MVP accepts Anthropic's default 30-day retention (no model training). Post-MVP: apply for Zero Data Retention (tracked in `memory/project_web_deploy_zdr_todo.md`, target reminder ~2026-05-04). |
| **Secondary users' real job is actually Scenario 2 (daily monitoring)** | Medium-High | High (could invalidate metric 1 "Jack requests ≤2") | **Open Question Q4** — recommend 40 minutes of 10-min interviews with the other 4 colleagues before W3 Beta launch. |
| **Excel export misses 小李's preferred format** | Medium | Medium | **Open Question Q3** — 30-min interview with 小李 in W1 D1 to confirm multi-sheet vs single-sheet, any pivot/formula needs. |

---

## Implementation Phases

<!--
  STATUS: pending | in-progress | complete
  PARALLEL: phases that can run concurrently (e.g., "with 3" or "-")
  DEPENDS: phases that must complete first (e.g., "1, 2" or "-")
  PRP: link to generated plan file once created
-->

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|---|---|---|---|---|---|
| 1 | **Scaffolding** | `webapp/` module, Chainlit hello-world, password auth callback with `@gl-inet.com` whitelist, 1 tool (`query_latest`) wired end-to-end, local dev run verified | complete | - | - | [phase1-webapp-scaffolding.plan.md](../plans/completed/phase1-webapp-scaffolding.plan.md) |
| 2 | **Query tools** | Wrap 9 read-only query functions as Chainlit tools with bilingual docstrings + `amz_scout.api` marketplace aliasing support | complete | with 3 | 1 | [phase2-query-tools.plan.md](../plans/completed/phase2-query-tools.plan.md) |
| 3 | **Management tools** | ~~Wrap 6 product registry functions~~ **Deferred to v1.1 per council B− (2026-04-24)** — 对小李透明；Jack 用 `amz_scout.api` / CLI 管理注册表成本可接受；MVP 不需要 UI 包装。 | deferred-to-v1.1 | - | - | - |
| 3.5 | **Browser route audit + token safety gate** | **(新增 per council B−, 2026-04-24, ~2.5h)** (a) 30 分钟审计 `competitive_snapshots` 表每个字段的 Keepa 可覆盖率，产出 `docs/browser-route-audit.md`（含字段清单 + 覆盖率 + 降级建议）；(b) 为 `ensure_keepa_data` 添加 Chainlit 确认对话框，消费 `amz_scout.api` 已有的 `phase="needs_confirmation"` 协议。回答 Q8，堵 Alpha 前唯一的 token 烧光风险点。 | in-progress | - | 2 | [phase3.5-browser-audit-and-token-safety-gate.plan.md](../plans/phase3.5-browser-audit-and-token-safety-gate.plan.md) |
| 4 | ~~**High-risk tools + long task UX**~~ **Split / mostly cancelled per council B− (2026-04-24)** | 拆分：(a) `ensure_keepa_data` 确认对话 → **移入 Phase 3.5**；(b) 浏览器版 `discover_asin` 包装 → **cancelled**（已被 Anthropic `web_search_20260209` 替代，PR #21；CLI 代码保留作 fallback，不加 UI 入口）；(c) `batch_discover` / `sync_registry` UI 包装 → **deferred-to-v1.1**（Jack 用 API 直接调，6 用户规模无人需 UI）。Sub-scope Anthropic web_search ASIN 发现已交付。 | split | - | - | [webapp-anthropic-web-search-asin.plan.md](../plans/completed/webapp-anthropic-web-search-asin.plan.md) |
| 5 | **Excel export layer** | 单表 xlsx pipeline 已随 slim-refactor Phase 3 交付（`webapp/summaries.py::_rows_to_xlsx_bytes` + `cl.File` 附件通道；所有 row-emitting 工具自动附带单表 xlsx）。**Multi-sheet per product × market + Q3 小李 Excel 格式访谈 → deferred-pending-alpha-feedback（council B−, 2026-04-24）**：改为 Phase 7 Alpha 首周 30min 跟访小李，用真实使用行为决定是否补 multi-sheet（xlsx 管道可在 1-2h 内改造）。 | partial | - | 2 | inherited from [query-passthrough-mode.plan.md](../plans/completed/query-passthrough-mode.plan.md) |
| 6 | **Deployment** | Dockerfile (`python:3.12-slim-bookworm` + `pip install uv browser-use` + `browser-use install`), `docker-compose.yml`, AWS Lightsail provisioning, block storage mount, HTTP-only for rehearsal (HTTPS deferred until domain available), smoke test | complete | - | 1 | [phase6-deployment.plan.md](../plans/completed/phase6-deployment.plan.md) |
| 7 | **Alpha (Jack + 小李)** | Internal test with 小李 as first real user; **Jack 30min 跟访观察 xlsx 使用行为 + 询问 Excel 格式偏好（回答 Q3，替代原预访谈）**; iterate on tool docstrings + prompts based on observed failures; measure one real research task end-to-end; 跟访结论决定是否回补 Phase 5 multi-sheet | pending | - | 3.5, 6 | - |
| 8 | **Beta (full 5 colleagues)** | Roll out to remaining 4 colleagues with **Q4 interview done first**; establish feedback channel (Slack/Feishu); daily monitoring of error rate + token burn | pending | - | 7 | - |

### Phase Details

**Phase 1: Scaffolding** (~W1 D1-D3, ~9h)
- **Goal**: End-to-end pipe working — user logs in, asks a question, LLM calls 1 tool, response renders
- **Scope**: `webapp/app.py`, `webapp/auth.py`, `webapp/tools.py` with just `query_latest`, `webapp/llm.py` Sonnet 4.6 binding
- **Success signal**: Local `chainlit run webapp/app.py` serves UI at `localhost:8000`; test user logs in with whitelisted email; typing "show me latest UK data" triggers `query_latest(marketplace="UK")` and renders envelope data
- **Also in this phase**: 30-min interview with 小李 (**Open Question Q3**) on preferred Excel format

**Phase 2: Query tools** (~W1 D4-D5, 3h)
- **Goal**: Every scenario-1 query need is expressible
- **Scope**: Wrap `query_trends`, `query_compare`, `query_ranking`, `query_availability`, `query_sellers`, `query_deals`, `query_latest`, `check_freshness`, `keepa_budget`
- **Success signal**: LLM can answer "compare Slate 7 and BE5400 in UK/DE/US past 6 months" by chaining multiple tool calls
- **Runs in parallel with Phase 3**

**Phase 3: Management tools** — **Deferred to v1.1 per council B− (2026-04-24)**
- 详见 Implementation Phases 表。理由：对小李透明；Jack 用 `amz_scout.api` / CLI 管理注册表成本可接受；属于"对 API 完整性而非用户价值的包装"。

**Phase 3.5: Browser route audit + token safety gate** (~2.5h, 新增 per council B−, 2026-04-24)
- **Goal**: (1) 回答"浏览器抓取路线是否应继续投资"这个遗留问题；(2) 在 Alpha 前堵上唯一的 token 烧光风险点。
- **Scope**:
  - **Part A (30min)** — 审计 `competitive_snapshots` 表中的每个字段，对每个字段回答"Keepa 是否有等价数据？"。产出 `docs/browser-route-audit.md`，含：字段清单、Keepa 覆盖率百分比、降级建议（若 ≥80% 可覆盖则建议在 Decisions Log 补一条决策标记浏览器路线 `deprecated-candidate`）
  - **Part B (~2h)** — 为 `ensure_keepa_data` 添加 Chainlit 确认对话框；消费 `amz_scout.api` 已有的 `phase="needs_confirmation"` 协议；提示用户即将花费的 token 数 + 预计耗时 + 确认/取消按钮
- **Success signal**:
  - Part A：审计文档合入 `main`；若结论为"路线可降级"，Decisions Log 追加一条 follow-up 决策
  - Part B：手动触发"需要刷新 10 个产品"的自然语言请求，UI 显示确认对话框，取消后不消耗 token
- **Depends on**: Phase 2
- **Answers Open Question**: Q8（new）

**Phase 4: High-risk tools + long-task UX** — **Split / mostly cancelled per council B− (2026-04-24)**
- **4a** `ensure_keepa_data` 确认对话 → 移入 Phase 3.5
- **4b** 浏览器版 `discover_asin` 包装 → **cancelled**（已被 Anthropic `web_search_20260209` 替代，PR #21；CLI 浏览器代码保留作 fallback，不加 UI 入口）
- **4c** `batch_discover` / `sync_registry` UI 包装 → deferred-to-v1.1（Jack 用 API 直接调成本可接受）
- **Hard constraint 保留**: `headed=True` 绝不暴露给用户
- **Sub-scope delivered (2026-04-24)**: Anthropic server-side `web_search_20260209` + client tool `register_asin_from_url` provide a non-browser ASIN-discovery path for webapp users without a Claude Code client. See [webapp-anthropic-web-search-asin.plan.md](../plans/completed/webapp-anthropic-web-search-asin.plan.md).

**Phase 5: Excel export** (~W2 D4, 3h) — **PARTIAL (底层管道已交付，2026-04-20)**
- **Goal**: 小李's workflow terminator — queries return downloadable artifacts
- **Scope (original)**: `webapp/export.py` with pandas + openpyxl; multi-sheet XLSX per-query; format per 小李's Q3 answer
- **Delivered via slim-refactor Phase 3**:
  - `webapp/summaries.py::_rows_to_xlsx_bytes` 用 openpyxl 生成 in-memory xlsx（无 pandas 依赖；走 `summarize_for_llm` decorator 自动附加到 `cl.File`）
  - 所有 row-emitting 工具（`query_latest` / `query_trends` / `query_compare` / `query_ranking` / `query_availability` / `query_sellers` / `query_deals`）每次查询自动产出单表 xlsx 附件
  - 每个工具有独立 `sheet_name`（如 `latest_snapshot` / `compare` / `deals`）
  - `MAX_XLSX_ROWS = 50_000` 保护，超限时 summary 标 `xlsx_truncated=True`
- **Remaining scope (deferred-pending-alpha-feedback per council B−, 2026-04-24)**:
  - Multi-sheet per query（原 Golden Path 描述的「one sheet per product × market」）→ **延后**；真实使用 1-2 次后再决定。若 Alpha 跟访中小李明确要求或观察到他手动拆分单表 → 立即补做（xlsx 管道已在 `_rows_to_xlsx_bytes`，估时 1-2h）
  - Q3 小李 Excel 格式访谈 → **改为 Alpha 首周 30min 跟访**，用真实使用行为替代预访谈推断
- **Success signal (original)**: Every query tool reply includes a `cl.File` attachment rendering as a download button ✅ 已达成
- **Depends on Phase 2** (query tools must exist first)

**Phase 6: Deployment** (~W2 D5 – W3 D1, 6h)
- **Goal**: Production URL accessible via HTTPS with whitelisted login
- **Scope**: `Dockerfile`, `docker-compose.yml`, Lightsail instance provisioning, EBS mount, domain + HTTPS cert, smoke test
- **Success signal**: Jack logs in from his browser at `https://amz-scout.<gl-inet-internal-host>` and completes one real query end-to-end
- **Answers Open Question Q2**

**Phase 7: Alpha (Jack + 小李)** (~W3 D2-D3, 6h)
- **Goal**: First real user validates the MVP hypothesis on a real task; **answers Q3 via 跟访 instead of pre-interview (council B−, 2026-04-24)**
- **Scope**: 小李 completes ≥1 real pricing research task; **Jack 跟访 30min 观察 xlsx 使用行为 + 询问 Excel 格式偏好（原 Q3）**; Jack logs gaps, iterates on tool docstrings/prompts/LLM errors
- **Success signal**: 小李 reports "this worked, I'd use it again"; at least 1 real research task completed end-to-end; **跟访结论明确是否需要回补 Phase 5 multi-sheet**; thumbs up/down actively used
- **Answers Open Question**: Q3（方法改为跟访）

**Phase 8: Beta (full rollout)** (~W3 D4-D5, 6h)
- **Goal**: MVP validation gate — 3 real tasks, ≥2 by 小李 independently, metric collection begins
- **Pre-requisite**: **Q4 interviews done** (40 min total, 10 min per colleague) to confirm use cases align with Scenario 1
- **Scope**: Roll out to 4 remaining colleagues; set up feedback channel; weekly silent-failure check starts
- **Success signal**: All hard metrics show positive directional movement; no critical bugs blocking daily use

### Parallelism Notes

**Post-council B− revision (2026-04-24)**:

- ~~Phases 2 and 3 run in parallel~~ → Phase 3 deferred-to-v1.1
- ~~Phases 4 and 5 run in parallel~~ → Phase 4 split/cancelled; Phase 5 剩余 deferred-pending-alpha-feedback
- **Phase 3.5** (new) depends only on Phase 2 and is the single remaining pre-Alpha work unit
- **Phase 6 (deployment)** already complete; runs beside
- **New critical path**: `1 → 2 → 3.5 → 7 → 8`，with 6 complete beside
- **Revised total estimate**: **~17.5h remaining**（Phase 3.5 ~2.5h + Phase 7 Alpha ~6h + Phase 8 Beta ~6h + Q4 interviews ~40min + buffer），相比原 PRD ~45h 剩余估算砍掉约 60%

---

## Decisions Log

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| Internal tool only, not SaaS | Internal MVP for 6 users | Direct SaaS; dual-target architecture | Lack of commercialization evidence; Jack's current problem is observable and solvable quickly; future SaaS PRD can use this MVP's usage data as seed |
| Single scenario (scenario 1 only) | Research queries only | Add scenario 2 (daily monitoring) to MVP | Scenario 2 needs scheduler + notifications (different infra); scenario 1 alone validates the hypothesis |
| Function calling over text-to-SQL | LLM calls typed `amz_scout.api` functions | LLM generates SQL over SQLite directly | Text-to-SQL caps at 10-20% production accuracy; function calling significantly higher (Berkeley BFCL); amz_scout.api is already the right substrate |
| Chainlit | Chainlit 2.x | Streamlit, Gradio, Open WebUI, LibreChat | Purpose-built for LLM chat + tool visualization + feedback; Streamlit's rerun model fights with streaming chat |
| Sonnet 4.6 | Claude Sonnet 4.6 + prompt caching | Haiku 4.5; GPT-4o-mini; local Ollama | Accuracy at multi-step tool chains justifies the ~3x cost vs Haiku; meets <10% error target |
| AWS Lightsail | 2GB/2vCPU Lightsail instance | EC2, ECS Fargate, Heroku | Simplest managed option for a 6-user internal tool; includes snapshots + bandwidth |
| Keepa plan unchanged | Keep €49 / 60 tokens-per-minute | Upgrade to €459 / 250/min | 6-user scale doesn't need it; upgrade would mask healthy cache-first design constraint |
| Email whitelist auth | Chainlit password + `@gl-inet.com` whitelist | Google Workspace OAuth, LDAP, no auth | Lowest-friction MVP; SSO is v1.1 if rollout expands |
| Open all permissions in MVP | Expose all 25 `amz_scout.api` functions | Read-only + safe subset for MVP, hide risky tools until v1.1 | Jack chose open permissions; honor it; guard with confirmation dialogs for high-cost operations |
| Anthropic ZDR deferred | Apply post-MVP | Apply before MVP | Don't block MVP on 1-week ZDR approval; reminder stored in project memory |
| Not in Out-of-Scope: non-router categories | Tool is category-agnostic | Exclude non-router categories | LLM is general, no router-specific prompting, so categorical exclusion is artificial |
| Baseline metric gap accepted | Mark "Jack's monthly requests" baseline as TBD | Block PRD on getting exact number | 2-week pre-launch observation is sufficient; don't let perfect data block the PRD |
| **议会 B− 裁决 (council, 2026-04-24)** | 砍 Phase 3 / 浏览器版 `discover_asin` / multi-sheet；只留 `ensure_keepa_data` 确认对话；Q3 改跟访；新增 Phase 3.5 浏览器路线审计 | 方案 A（按原 PRD 全做完 ~9h）/ 方案 C（全砍直接 Alpha 0h） | 剩余 9h 工作全是假设驱动；真实使用 1-2 次的信息价值 > 3h 做 multi-sheet 的工程价值；浏览器路线 ROI 存疑（Anthropic `web_search` 已胜出），不应加重 UI 投资；但 token 安全门必留（防 60 tokens 一次烧光）。B− 估时 ~2.5h，相比方案 A 省 72%；相比方案 C 保留最小必要安全网。 |

---

## Research Summary

### Market Context (from Phase 3 grounding)

- **Direct analogs**: Only 2 found — `cosjef/keepa_MCP` (community open-source MCP server) and AMZBuddy (commercial SaaS wrapping Keepa with ChatGPT prompts for Amazon sellers).
- **Large incumbents** (Helium10 Precision AI, JungleScout, Amazon Seller Assistant / Project Amelia) target marketplace sellers with dashboards and automation, **not** hardware-vendor market research teams. Target ICP differentiation is naturally clean.
- **Keepa ecosystem**: No official MCP server from Keepa; community `akaszynski/keepa` Python package is the dominant wrapper. Keepa plan pricing: €49/mo entry, €459 at 250 tokens/min, up to €4,499 at 4,000 — 10x cost jump to remove token pressure.
- **Text-to-SQL accuracy ceiling**: GPT-4o ~52.5% on BIRD-Bench; enterprise studies cite 10-20% accuracy for AI-generated SQL in production against heterogeneous systems.
- **Function calling is the industry-standard mitigation** (Berkeley BFCL benchmark) — validates this PRD's core architectural bet of wrapping the typed `amz_scout.api`.
- **Chat UI framework comparison**: Chainlit is the clearest fit — purpose-built for LLM chat, native streaming + step visualization + feedback + persistence. Streamlit's rerun-every-interaction model fights with streaming LLM chat.

### Technical Context (from Phase 3 codebase audit)

- **API layer is web-ready**: 17 of 25 `amz_scout.api` functions are purely read-only and safe to expose to 6 concurrent users. 3 are conditionally side-effecting (may auto-fetch Keepa data). 5 are high-risk and need explicit confirmation UX.
- **`browser-use` subprocess**: Default `headed=False` works on headless Linux via built-in headless Chromium. Deployment-safe **as long as** `headed=True` is never exposed to end users.
- **SQLite concurrency**: WAL mode enabled at `db.py:113`; supports 5 concurrent readers + 1 writer; all writes are transactional. **Low risk for 6-user scale.**
- **Keepa token budget**: 60 tokens, 1/min refill, globally shared. At 6-user scale with typical research query frequency, token budget is **not** a bottleneck. Existing `_BATCH_TOKEN_THRESHOLD = 6` gate at `api.py:778` handles high-cost operations with `phase="needs_confirmation"`.
- **External dependencies**: `browser-use` CLI (via `uv tool install`), `KEEPA_API_KEY` env var, `config/marketplaces.yaml`. All are documented and reproducible in Docker.
- **No TTY assumptions in `api.py`**: it uses `logging` module exclusively (no `print()` calls), making it safe for web backend use.

---

*Generated: 2026-04-13*
*Status: IN PROGRESS — Phase 1/2/6 complete, Phase 5 partial, Phase 3.5/7/8 pending. Phase 3 & Phase 4 (mostly) cancelled/deferred per council B− (2026-04-24).*
*Scope revisions:*
  *- 2026-04-21 — Drift reconciled: Phase 1 → complete, Phase 5 → partial.*
  *- 2026-04-24 — Council B− amendment: Phase 3 deferred-to-v1.1; Phase 4 split (4a `ensure_keepa_data` confirm → 并入 3.5; 4b 浏览器版 `discover_asin` cancelled; 4c `batch_discover` / `sync_registry` deferred); Phase 5 multi-sheet + Q3 interview deferred-pending-alpha-feedback; new Phase 3.5 inserted (browser route audit + token safety gate, ~2.5h). Revised critical path: `1 → 2 → 3.5 → 7 → 8`, revised remaining estimate ~17.5h (was ~45h).*
