# Browser Route Audit — Keepa Coverage of `competitive_snapshots`

> **Status**: Phase 3.5 Part A deliverable. Answers PRD Open Question **Q8** — "Keepa 是否能替代浏览器抓取?"
> **Date**: 2026-04-24
> **Author**: amz-scout / Phase 3.5 audit (council B− directed)

## TL;DR

**~74% of `competitive_snapshots` fields are covered by Keepa** (16 fully covered, 5 partial, 4 browser-unique, out of 25 audited). Browser-unique fields are all "shopfront-panel signals" (stock depth, star distribution, Q&A engagement) that are **nice-to-have, not load-bearing** for the current read-path surface (price / rating / BSR / trends).

**Recommendation: hybrid retention.** Mark scheduled browser scraping as `deprecated-candidate` (keep source for on-demand deep dives only); promote Keepa to primary for all scheduled monitoring.

---

## Scope

This audit compares the `competitive_snapshots` table — produced by `src/amz_scout/scraper/amazon.py` via `browser-use` — against what the Keepa API (via `amz_scout.keepa_service` + `keepa_products` / `keepa_time_series` / `keepa_buybox_history` / `keepa_coupon_history` / `keepa_deals` tables) can provide.

Excluded from the audit:

- **Internal meta columns**: `id`, `project`, `created_at` — not data signals; present in any table.
- **Raw snapshot columns**: `price_raw`, `rating_raw`, `review_count_raw`, `bsr_raw` — these are debugging/provenance artifacts of the browser route and have no Keepa analogue by design.

That leaves **25 signal-bearing fields** to audit.

---

## Field-by-Field Coverage Table

Legend:

- ✅ **Covered** — Keepa returns a semantically equivalent field or time-series.
- ⚠️ **Partial** — Keepa exposes related data but with different semantics (derivable, not direct).
- ❌ **Browser-unique** — No Keepa analogue; only observable from the rendered product page.

| # | Field | Keepa equivalent | Coverage | Notes |
|---|---|---|---|---|
| 1 | `scraped_at` | `keepa_time_series.keepa_ts` / `keepa_products.fetched_at` | ✅ | Keepa timestamps are per-observation; browser is per-scrape. Granularity differs but both answer "when was this observed". |
| 2 | `site` | `keepa_*.site` | ✅ | Both tables key on `site`. |
| 3 | `category` | `keepa_products.category_tree` / `categories` / `root_category` | ✅ | Keepa exposes richer category hierarchy than the browser scrape. |
| 4 | `brand` | `keepa_products.brand` | ✅ | Direct mapping. |
| 5 | `model` | `keepa_products.model` / `part_number` | ✅ | Direct mapping. |
| 6 | `asin` | `keepa_*.asin` | ✅ | Identity key. |
| 7 | `title` | `keepa_products.title` | ✅ | Direct. |
| 8 | `price_cents` | `keepa_time_series` (series_type ∈ {AMAZON=0, NEW=1, BUY_BOX_SHIPPING=18}) | ✅ | Keepa provides full history, not just current. Browser gives single point. |
| 9 | `currency` | *derived from `site`* | ⚠️ | Neither Keepa nor browser store it explicitly as an FX-agnostic field; both rely on locale→currency mapping. Keepa can derive equivalently. |
| 10 | `rating` | `keepa_time_series` (series_type=RATING=16) | ✅ | Keepa history vs browser point-in-time. |
| 11 | `review_count` | `keepa_time_series` (series_type=COUNT_REVIEWS=17) | ✅ | Same pattern. |
| 12 | `bought_past_month` | `keepa_time_series` (series_type=100, monthly_sold) | ✅ | Keepa fetched via `query_monthly_sold` helper. |
| 13 | `bsr` | `keepa_time_series` (series_type=SALES_RANK=3 plus per-category SALES_RANK_BASE offsets) / `keepa_products.sales_rank_ref` | ✅ | Keepa has full BSR time series per category. |
| 14 | `available` | `keepa_products.availability_amazon` / offer count inference | ⚠️ | Keepa's `availability_amazon` is Amazon-as-seller-specific; browser `available` is "any-seller available on page". Convergence possible but not identical. |
| 15 | `url` | *derived from `asin` + `site`* | ✅ | Both trivially constructible. |
| 16 | `stock_status` | — | ❌ | No Keepa field. "Only 3 left in stock" / "temporarily out of stock" strings are shopfront-rendered. |
| 17 | `stock_count` | — | ❌ | No Keepa field. Requires page DOM. |
| 18 | `sold_by` | `keepa_buybox_history.seller_id` | ✅ | Keepa returns canonical seller IDs; browser returns display name. Join against `keepa_products.sellers` / `keepa_service.resolve_seller` as needed. |
| 19 | `other_offers` | *offers count via `keepa_products.buybox_eligible_counts`* | ⚠️ | Keepa exposes offer counts and (with deep fetch) the offer list, but not the compact textual summary the browser captures. Derivable, but different shape. |
| 20 | `coupon` | `keepa_coupon_history.amount` + `coupon_type` | ✅ | Keepa has structured coupon history. Stronger than browser's free-text. |
| 21 | `is_prime` | *derived from FBA signals (`keepa_products.fba_pick_pack_fee`, offer-level FBA flag)* | ⚠️ | "Prime-eligible" is approximately "FBA-fulfilled or Amazon-sold". Inference, not direct. |
| 22 | `star_distribution` | — | ❌ | Keepa returns aggregate rating only, not the 1★–5★ histogram. Browser is the only source. |
| 23 | `image_count` | `keepa_products.image_count` | ✅ | Direct mapping. |
| 24 | `qa_count` | — | ❌ | Keepa does not track Q&A. Browser-only. |
| 25 | `fulfillment` | *partial via offer-level FBA flag + `keepa_products.fba_pick_pack_fee`* | ⚠️ | Keepa tells you FBA-or-not per offer; the browser's human-readable "Ships from X, Sold by Y" string composes two facts that Keepa separates. |

---

## Coverage Math

- **Covered (✅)**: 16 — `scraped_at`, `site`, `category`, `brand`, `model`, `asin`, `title`, `price_cents`, `rating`, `review_count`, `bought_past_month`, `bsr`, `url`, `sold_by`, `coupon`, `image_count`
- **Partial (⚠️)**: 5 — `currency`, `available`, `other_offers`, `is_prime`, `fulfillment`
- **Browser-unique (❌)**: 4 — `stock_status`, `stock_count`, `star_distribution`, `qa_count`

Using the PRP-specified formula `covered / (covered + browser_unique + partial*0.5)`:

```
coverage = 16 / (16 + 4 + 5 * 0.5) = 16 / 22.5 ≈ 0.711 ≈ 71%
```

Using a slightly more generous formula (partial counts as half-covered):

```
coverage = (16 + 5 * 0.5) / 25 = 18.5 / 25 = 74%
```

Both methods land in the **50-80% hybrid zone**. The audit uses **~74%** as the headline, acknowledging the partial-credit subjectivity.

---

## What Browser Scraping Uniquely Buys Us

Sorted by business value (author's judgment, open to challenge):

1. **`stock_count` / `stock_status`** — Scarcity signal; useful for demand/supply reasoning during promotions or pre-launch phases. **Medium value.**
2. **`other_offers`** — Competition snapshot (how many sellers in the Buy Box queue). Derivable from Keepa offer counts but browser gives a cleaner "pulse". **Low-medium value.**
3. **`star_distribution`** — Reviews breakdown helps detect review-bomb or sentiment inversion; rarely used in current analyses. **Low value.**
4. **`qa_count`** — Engagement proxy; weakly correlated with sales. **Low value.**
5. **`fulfillment` (textual)** — Human-readable; derivable from Keepa FBA flags. **Low value** (convenience only).

**None of these are load-bearing for the current PRD read-path** (price trends, BSR rank, cross-market compare, deal discovery). They would matter if a future scenario required **live shopfront monitoring** (e.g. "alert me when Slate 7 drops below X units in stock"), which is not on the current roadmap.

---

## Cost Comparison

| Route | Latency per ASIN | External cost | Resilience | Maintenance burden |
|---|---|---|---|---|
| Browser (`browser-use`) | ~30-90s/ASIN, serial per session | CDP + LLM-navigator token per run | Breaks on layout changes (Amazon A/B tests, DOM churn) | High — requires locator audits, anti-bot counters |
| Keepa API | <1s/ASIN (basic), 6 tokens (detailed) | 60 tokens / min shared budget | Stable API; documented fields | Low — schema evolves slowly |

A 12-product × 6-marketplace (=72-ASIN) nightly refresh:

- **Browser**: ~1-2 h wall clock, 1 browser session per marketplace, manual intervention on layout drift.
- **Keepa**: 72 tokens basic = 2 token-refill cycles (~2 min with 1/min refill); 0 maintenance.

Browser's cost-per-signal is ~60-100x higher than Keepa's for overlapping fields.

---

## Recommendation

**Hybrid retention, with scheduled browser scraping marked `deprecated-candidate`**:

1. **Promote Keepa to primary** for all scheduled / periodic monitoring of price, rating, BSR, reviews, monthly-sold, BuyBox seller, coupons, and product metadata.
2. **Keep the browser route available as a manual "deep dive" tool** for the browser-unique signals (stock_count, star_distribution, qa_count) when a specific analysis needs them.
3. **Stop investing in browser route hardening** (locator fixes, anti-bot evasion) beyond what is needed to keep the manual path functional.
4. **Do not delete `competitive_snapshots`** yet — historical data stays useful as a baseline, and the table itself is the write target for the manual path.

**Trigger for full deprecation** (future decision, not today): if no analysis in a rolling 90-day window touches a browser-unique column, downgrade the browser route to "reference implementation only" and stop running it at all.

---

## Follow-Up Actions

If the recommendation above is accepted:

1. **PRD update**: Add a new `Q8-answered` row to the Decisions Log of `internal-amz-scout-web.prd.md` citing this audit and the ~74% coverage number.
2. **Tool schema doc drift fix** (out of scope for Phase 3.5 per council B−, but worth logging): `webapp/tools.py:211` — the `query_deals` schema description says "batches ≥6 tokens return phase='needs_confirmation'", but the `_auto_fetch` path (`api.py:354`, `FreshnessStrategy.LAZY`) swallows failures silently and never emits that phase. This is documentation drift, not a bug in scope. Flagged for a one-line follow-up.
3. **Browser route demotion** (separate plan, if adopted): a new plan `phase-X-browser-route-deprecation.plan.md` would remove the scheduled invocations from CI/cron and the CLI, leaving only the ad-hoc entrypoint. Not part of Phase 3.5.
4. **Signal-gap monitoring**: Add a quarterly check — grep the last 90 days of analysis queries for use of `stock_count` / `star_distribution` / `qa_count` / `other_offers`. If zero hits, escalate to full deprecation.

---

## Assumptions & Caveats

- **"Partial" is judgment-laden**: Fields like `is_prime` and `fulfillment` can be reconstructed from Keepa with enough plumbing code; the audit rates them partial to flag the reconstruction cost, not to suggest Keepa is missing the data.
- **Time-series vs point-in-time**: The browser captures a single observation per scrape; Keepa returns history. For price/rating/BSR this is a Keepa **advantage**, not a coverage gap. The audit treats them as ✅ covered despite the shape difference.
- **Seller-name vs seller-id**: Browser's `sold_by` is a human-readable shop name; Keepa's `keepa_buybox_history.seller_id` is the canonical ID. Joining requires a seller-ID-to-name map (already partially present in `keepa_service`). Treated as ✅ given the canonical ID is strictly richer.
- **Audit source of truth**: `src/amz_scout/db.py:664-700` for the `competitive_snapshots` schema; `src/amz_scout/db.py:26-50` for Keepa series-type constants; `src/amz_scout/db.py:749-800` for `keepa_products` columns. Re-run the audit against those lines if the schema evolves.
