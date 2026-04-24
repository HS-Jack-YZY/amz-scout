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
- [ ] **Q3 — 小李's Excel export format preference**: Single sheet or multi-sheet? Formulas, pivot tables, conditional formatting needed? Risk: Low-Medium. Answer by: W1 D1 — 30 min interview with 小李.
- [ ] **Q4 — Other 4 colleagues' real use cases**: Are they actually aligned with 小李 (scenario 1), or does anyone actually need scenario 2 (daily monitoring)? If the latter, MVP won't meet the substitution metric. Risk: **High** — could invalidate metric 1. Recommended action: 10-minute interview with each of the other 4 **before** W3 Beta launch (total cost ~40 minutes; potential savings ~3 days of rework).
- [ ] **Q5 — Real Keepa token burn rate**: With 6 real concurrent users, what's the actual monthly token consumption? Risk: Low. Answer by: first 2 weeks post-launch monitoring.
- [ ] **Q6 — Chainlit thumbs up/down response rate**: If users don't click, the quality metric is unmeasurable. Mitigation: Jack prompts 小李 during W3 Alpha to click feedback explicitly so habit forms. Risk: Low for indicator integrity.
- [ ] **Q7 — Anthropic ZDR application**: Apply after MVP launch. Tracked in project memory (`project_web_deploy_zdr_todo.md`). Reminder trigger: when amz-scout web app goes to Beta or full internal rollout. Risk: Medium (sensitive competitive data exposure).

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
| **Could** | Wrap product registry tools (`list_products`, `add_product`, `remove_product_by_model`, `update_product_asin`, `register_market_asins`, `import_yaml`) | Jack chose "open all permissions" in MVP; register functions are safe and lightweight |
| **Could** | Wrap high-risk tools (`ensure_keepa_data`, `batch_discover`, `discover_asin`, `sync_registry`) with explicit confirmation dialogs | Honor Jack's "open all permissions" MVP decision; protect users from accidental cost |
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
| 3 | **Management tools** | Wrap 6 product registry functions (`list_products`, `add_product`, `remove_product_by_model`, `update_product_asin`, `register_market_asins`, `import_yaml`); honor `phase="needs_confirmation"` protocol in UI | pending | with 2 | 1 | - |
| 4 | **High-risk tools + long task UX** | Wrap `ensure_keepa_data`, `batch_discover`, `discover_asin`, `sync_registry` with `cl.Step` progress + explicit confirmation dialogs for token-consuming / long-running operations. **Sub-scope (2026-04-24)**: non-browser ASIN discovery via Anthropic `web_search_20260209` + `register_asin_from_url` delivered as a separate path — see [webapp-anthropic-web-search-asin.plan.md](../plans/completed/webapp-anthropic-web-search-asin.plan.md). The browser-based `discover_asin` wrap is still pending. | pending | - | 2, 3 | - |
| 5 | **Excel export layer** | 底层 xlsx 导出管道已随 slim-refactor Phase 3「查询直通模式」交付（`webapp/summaries.py::_rows_to_xlsx_bytes` + `cl.File` 附件通道；所有 row-emitting 工具自动附带单表 xlsx）。**剩余 scope**：(a) multi-sheet per query（product × market 分表）尚未实现 — 当前每个查询落成单表；(b) Q3 小李 Excel 格式访谈尚未进行，格式未针对性调优。 | partial | with 4 | 2 | inherited from [query-passthrough-mode.plan.md](../plans/completed/query-passthrough-mode.plan.md) |
| 6 | **Deployment** | Dockerfile (`python:3.12-slim-bookworm` + `pip install uv browser-use` + `browser-use install`), `docker-compose.yml`, AWS Lightsail provisioning, block storage mount, HTTP-only for rehearsal (HTTPS deferred until domain available), smoke test | complete | - | 1 | [phase6-deployment.plan.md](../plans/completed/phase6-deployment.plan.md) |
| 7 | **Alpha (Jack + 小李)** | Internal test with 小李 as first real user; iterate on tool docstrings + prompts based on observed failures; measure one real research task end-to-end; confirm Excel export format | pending | - | 4, 5, 6 | - |
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

**Phase 3: Management tools** (~W2 D1-D2, 3h)
- **Goal**: Honor "open all permissions" decision; product registry is manageable from UI
- **Scope**: Wrap `list_products`, `add_product`, `remove_product_by_model`, `update_product_asin`, `register_market_asins`, `import_yaml`; build `phase="needs_confirmation"` consumer in the UI layer
- **Success signal**: A user can add a new product registry entry via natural-language request
- **Runs in parallel with Phase 2**

**Phase 4: High-risk tools + long-task UX** (~W2 D3, 3h)
- **Goal**: All 25 `amz_scout.api` functions exposed; long-running ops don't confuse users
- **Scope**: `ensure_keepa_data`, `batch_discover`, `discover_asin`, `sync_registry` with `cl.Step` + confirmation dialogs
- **Success signal**: Triggering `ensure_keepa_data(fresh)` for 10 products shows step-by-step progress; `batch_discover` for 3 candidates shows per-candidate status
- **Hard constraint**: `headed=True` is hard-wired OFF in the web layer — not exposed as a user toggle
- **Sub-scope delivered (2026-04-24)**: Anthropic server-side `web_search_20260209` + client tool `register_asin_from_url` provide a non-browser ASIN-discovery path for webapp users without a Claude Code client. Lives alongside (not replacing) the still-pending browser-based `discover_asin` wrap. See [webapp-anthropic-web-search-asin.plan.md](../plans/completed/webapp-anthropic-web-search-asin.plan.md).

**Phase 5: Excel export** (~W2 D4, 3h) — **PARTIAL (底层管道已交付，2026-04-20)**
- **Goal**: 小李's workflow terminator — queries return downloadable artifacts
- **Scope (original)**: `webapp/export.py` with pandas + openpyxl; multi-sheet XLSX per-query; format per 小李's Q3 answer
- **Delivered via slim-refactor Phase 3**:
  - `webapp/summaries.py::_rows_to_xlsx_bytes` 用 openpyxl 生成 in-memory xlsx（无 pandas 依赖；走 `summarize_for_llm` decorator 自动附加到 `cl.File`）
  - 所有 row-emitting 工具（`query_latest` / `query_trends` / `query_compare` / `query_ranking` / `query_availability` / `query_sellers` / `query_deals`）每次查询自动产出单表 xlsx 附件
  - 每个工具有独立 `sheet_name`（如 `latest_snapshot` / `compare` / `deals`）
  - `MAX_XLSX_ROWS = 50_000` 保护，超限时 summary 标 `xlsx_truncated=True`
- **Remaining scope**:
  - Multi-sheet per query（原 Golden Path 描述的「one sheet per product × market」）尚未实现 — 跨市场对比当前也是单表
  - Q3 小李 Excel 格式访谈尚未进行 — 多表 / 单表 / pivot / 条件格式等偏好未确认
- **Success signal (original)**: Every query tool reply includes a `cl.File` attachment rendering as a download button ✅ 已达成
- **Runs in parallel with Phase 4**; depends on Phase 2 (query tools must exist first)

**Phase 6: Deployment** (~W2 D5 – W3 D1, 6h)
- **Goal**: Production URL accessible via HTTPS with whitelisted login
- **Scope**: `Dockerfile`, `docker-compose.yml`, Lightsail instance provisioning, EBS mount, domain + HTTPS cert, smoke test
- **Success signal**: Jack logs in from his browser at `https://amz-scout.<gl-inet-internal-host>` and completes one real query end-to-end
- **Answers Open Question Q2**

**Phase 7: Alpha (Jack + 小李)** (~W3 D2-D3, 6h)
- **Goal**: First real user validates the MVP hypothesis on a real task
- **Scope**: 小李 completes 1 real pricing research task; Jack observes, logs gaps, iterates on tool docstrings/prompts/LLM errors
- **Success signal**: 小李 reports "this worked, I'd use it again"; at least 1 real research task completed end-to-end; thumbs up/down actively used

**Phase 8: Beta (full rollout)** (~W3 D4-D5, 6h)
- **Goal**: MVP validation gate — 3 real tasks, ≥2 by 小李 independently, metric collection begins
- **Pre-requisite**: **Q4 interviews done** (40 min total, 10 min per colleague) to confirm use cases align with Scenario 1
- **Scope**: Roll out to 4 remaining colleagues; set up feedback channel; weekly silent-failure check starts
- **Success signal**: All hard metrics show positive directional movement; no critical bugs blocking daily use

### Parallelism Notes

- **Phases 2 and 3** run in parallel (both depend only on Phase 1 scaffolding and touch non-overlapping tool subsets).
- **Phases 4 and 5** run in parallel (Phase 4 adds long-running tools; Phase 5 adds export format; no file conflict).
- **Phase 6 (deployment)** can start early (after Phase 1) and be iteratively updated as new tools land.
- **Critical path**: `1 → (2 ∥ 3) → (4 ∥ 5) → 7 → 8`, with 6 running beside.
- **Total estimate**: ~45 hours / 3 weeks at 3 hours/day.

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
*Status: IN PROGRESS — 3/8 phases complete + 1 partial (Phase 1 scaffolding #3, Phase 2 query tools #4, Phase 6 deployment #6 all merged 2026-04-13; Phase 5 Excel export — xlsx 底层管道已随 slim-refactor Phase 3 间接交付 2026-04-20，multi-sheet + Q3 访谈仍待做). Phase 3/4/7/8 still pending; awaiting pre-launch baseline + Q3/Q4 interviews before W3 Beta rollout.*
*Drift reconciled: 2026-04-21 — Phase 1 status corrected from `in-progress` to `complete` (PR #3 merged 2026-04-13; plan moved to `plans/completed/`); Phase 5 status corrected from `pending` to `partial` (xlsx pipeline delivered via slim-refactor Phase 3 query-passthrough-mode, 2026-04-20).*
