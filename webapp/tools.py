"""Chainlit-wrapped tools that call amz_scout.api functions.

Phase 2 exposes all 9 read-only query tools so the LLM can answer the full
Scenario-1 research surface (latest snapshots, trends, compare, ranking,
availability, sellers, deals, freshness, Keepa budget).

Trimming policy (see ``amz_scout._llm_trim``): ``amz_scout.api`` deliberately
returns full DB rows so that CLI/admin callers keep the complete schema. This
module is the LLM boundary, so each ``_step_*`` wrapper that emits
competitive-snapshot / time-series / seller / deal rows is decorated with
``trim_for_llm(...)``, which projects the envelope's ``data`` list to the
LLM-safe allow-list before the result reaches the model.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

import chainlit as cl

from amz_scout._llm_trim import (
    trim_competitive_rows,
    trim_deals_rows,
    trim_seller_rows,
    trim_timeseries_rows,
)
from amz_scout.api import check_freshness as _api_check_freshness
from amz_scout.api import keepa_budget as _api_keepa_budget
from amz_scout.api import query_availability as _api_query_availability
from amz_scout.api import query_compare as _api_query_compare
from amz_scout.api import query_deals as _api_query_deals
from amz_scout.api import query_latest as _api_query_latest
from amz_scout.api import query_ranking as _api_query_ranking
from amz_scout.api import query_sellers as _api_query_sellers
from amz_scout.api import query_trends as _api_query_trends

logger = logging.getLogger(__name__)


def trim_for_llm(
    trimmer: Callable[[list[dict]], list[dict]],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Project the ``data`` list of an api envelope through ``trimmer``.

    Wrap an async ``_step_*`` wrapper so successful envelopes have their
    ``data`` rows passed through the LLM-safe allow-list. Failed envelopes
    (``ok=False``) and ``meta`` are passed through untouched. Returns a NEW
    envelope dict so the api-layer return value is never mutated.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            result = await fn(*args, **kwargs)
            if not isinstance(result, dict) or not result.get("ok"):
                return result
            rows = result.get("data") or []
            return {**result, "data": trimmer(rows)}

        return wrapper

    return decorator


def _missing_required(tool_name: str, field: str) -> dict[str, Any]:
    """Envelope-shaped validation error for a required tool field the LLM dropped.

    The Anthropic tool schema marks some fields as `required`, but LLMs occasionally
    omit them. Returning this envelope (instead of falling through to the API with an
    empty string) gives the model a clear "you dropped a required field" signal rather
    than a cryptic downstream resolution error.
    """
    return {
        "ok": False,
        "data": [],
        "error": f"{field} is required for {tool_name}",
        "meta": {},
    }


# ─── Anthropic tool schemas ──────────────────────────────────────
# IMPORTANT: cache_control goes on the LAST tool only — it caches all
# preceding tools. Scattered cache_control = cache hit rate of 0.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "query_latest",
        "description": (
            "Get the latest Amazon competitive snapshot (current price, rating, BSR, "
            "availability) for products in a specific marketplace. Use this when the user "
            "asks about 'current' or 'latest' product data. Returns a list of product rows "
            "from the competitive_snapshots table in the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Marketplace code (e.g., 'UK', 'DE', 'US', 'JP'). "
                        "Also accepts aliases like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Optional product category filter (e.g., 'Travel Router').",
                },
            },
            "required": ["marketplace"],
        },
    },
    {
        "name": "check_freshness",
        "description": (
            "Show how stale the cached Keepa data is for each product × marketplace, "
            "as a freshness matrix (e.g., '0d', '3d', 'never'). Use this when the user "
            "asks '数据多久没更新了' / 'how fresh is the data' / 'when was X last updated'. "
            "Read-only — does NOT fetch from Keepa, costs 0 tokens."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Optional marketplace filter (e.g., 'UK', 'DE'). Omit to check all."
                    ),
                },
                "product": {
                    "type": "string",
                    "description": "Optional product filter (brand/model/ASIN). Omit to check all.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "keepa_budget",
        "description": (
            "Check the current Keepa API token balance and refill rate. Use when the user "
            "asks 'Keepa 还有多少 token' / 'how many Keepa tokens left' / 'token余额'. "
            "Costs 0 Keepa tokens to call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "query_availability",
        "description": (
            "Show which products are listed on which marketplaces — an availability matrix "
            "across all sites. Use when the user asks '哪些国家有卖' / 'which countries sell X' / "
            "'availability'. Reads from the competitive_snapshots table; does NOT call Keepa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "query_compare",
        "description": (
            "Compare ONE product across ALL marketplaces using the latest snapshot "
            "(price, rating, BSR per marketplace). Use when the user asks "
            "'对比 X 在所有市场' / 'compare X across markets' / 'cross-market'. "
            "Reads browser-scraped snapshots, not Keepa history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": (
                        "Product identifier — brand/model name (e.g., 'Slate 7') or ASIN. Required."
                    ),
                },
            },
            "required": ["product"],
        },
    },
    {
        "name": "query_deals",
        "description": (
            "Show deal/promotion/discount history for products on a marketplace. Use when "
            "the user asks '促销' / '折扣' / 'deals' / 'discounts' / 'sale history'. "
            "Auto-fetches missing Keepa data using the LAZY strategy (zero tokens if "
            "cached). For ≥6-token batches the API returns phase='needs_confirmation' "
            "— surface that to the user and ask them to confirm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Marketplace code (e.g., 'UK', 'DE', 'US'). Omit to query all "
                        "marketplaces in the registry."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_ranking",
        "description": (
            "Show products ranked by Amazon Best Sellers Rank (BSR) for a marketplace. "
            "Use when the user asks '排名' / 'BSR' / 'best sellers' / 'who's #1 in X'. "
            "Reads browser-scraped snapshots; does NOT call Keepa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Marketplace code (e.g., 'UK', 'DE'). Required. Accepts aliases "
                        "like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter (e.g., 'Travel Router').",
                },
            },
            "required": ["marketplace"],
        },
    },
    {
        "name": "query_sellers",
        "description": (
            "Show Buy Box seller history for ONE product on ONE marketplace — who has "
            "owned the Buy Box over time. Use when the user asks '卖家' / 'Buy Box' / "
            "'seller history' / 'who is selling X'. Auto-fetches missing Keepa data using "
            "the LAZY strategy (zero tokens if cached)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": ("Product identifier — brand/model name or ASIN. Required."),
                },
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Marketplace code (e.g., 'UK', 'DE'). Defaults to 'UK' if omitted."
                    ),
                },
            },
            "required": ["product"],
        },
    },
    {
        "name": "query_trends",
        "description": (
            "Show price/BSR/sales time series for ONE product on ONE marketplace over a "
            "given window. Use when the user asks '价格趋势' / '历史价格' / 'price trends' "
            "/ 'past N days/months'. Series options: 'new' (Amazon new price), 'used', "
            "'buybox', 'sales_rank', 'rating', 'review_count', 'monthly_sold'. "
            "Auto-fetches missing Keepa data using the LAZY strategy (zero tokens if "
            "cached). Prices in the response are encoded as cents — divide by 100 for the "
            "real price. To compare multiple products or marketplaces, call this tool "
            "multiple times (once per product × marketplace)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": (
                        "Product identifier — brand/model name (e.g., 'Slate 7', "
                        "'GL-MT3000') or ASIN. Required."
                    ),
                },
                "marketplace": {
                    "type": "string",
                    "description": (
                        "Marketplace code (e.g., 'UK', 'DE', 'US', 'JP'). Defaults to 'UK'. "
                        "Accepts aliases like 'uk', 'amazon.co.uk', 'GB', 'GBP'."
                    ),
                },
                "series": {
                    "type": "string",
                    "description": (
                        "Series type. One of: 'new' (default), 'used', 'buybox', "
                        "'sales_rank', 'rating', 'review_count', 'monthly_sold'."
                    ),
                    "enum": [
                        "new",
                        "used",
                        "buybox",
                        "sales_rank",
                        "rating",
                        "review_count",
                        "monthly_sold",
                    ],
                },
                "days": {
                    "type": "integer",
                    "description": (
                        "Lookback window in days. Default 90. Use 7/30/90/180/365 for "
                        "common windows (e.g., 'past 6 months' → 180)."
                    ),
                    "minimum": 1,
                    "maximum": 730,
                },
            },
            "required": ["product"],
        },
        # Cache_control on the LAST tool only — caches all 9 tool definitions together.
        "cache_control": {"type": "ephemeral"},
    },
]


# ─── Tool dispatcher ─────────────────────────────────────────────
@cl.step(type="tool", name="query_latest")
@trim_for_llm(trim_competitive_rows)
async def _step_query_latest(marketplace: str, category: str | None = None) -> dict:
    """Chainlit step wrapper that shows tool inputs/outputs in the UI."""
    logger.info("query_latest called: marketplace=%s category=%s", marketplace, category)
    return _api_query_latest(marketplace=marketplace, category=category)


@cl.step(type="tool", name="check_freshness")
async def _step_check_freshness(marketplace: str | None = None, product: str | None = None) -> dict:
    logger.info("check_freshness called: marketplace=%s product=%s", marketplace, product)
    return _api_check_freshness(marketplace=marketplace, product=product)


@cl.step(type="tool", name="keepa_budget")
async def _step_keepa_budget() -> dict:
    logger.info("keepa_budget called")
    return _api_keepa_budget()


@cl.step(type="tool", name="query_availability")
@trim_for_llm(trim_competitive_rows)
async def _step_query_availability() -> dict:
    logger.info("query_availability called")
    return _api_query_availability()


@cl.step(type="tool", name="query_compare")
@trim_for_llm(trim_competitive_rows)
async def _step_query_compare(product: str) -> dict:
    logger.info("query_compare called: product=%s", product)
    return _api_query_compare(product=product)


@cl.step(type="tool", name="query_deals")
@trim_for_llm(trim_deals_rows)
async def _step_query_deals(marketplace: str | None = None) -> dict:
    logger.info("query_deals called: marketplace=%s", marketplace)
    return _api_query_deals(marketplace=marketplace)


@cl.step(type="tool", name="query_ranking")
@trim_for_llm(trim_competitive_rows)
async def _step_query_ranking(marketplace: str, category: str | None = None) -> dict:
    logger.info("query_ranking called: marketplace=%s category=%s", marketplace, category)
    return _api_query_ranking(marketplace=marketplace, category=category)


@cl.step(type="tool", name="query_sellers")
@trim_for_llm(trim_seller_rows)
async def _step_query_sellers(product: str, marketplace: str = "UK") -> dict:
    logger.info("query_sellers called: product=%s marketplace=%s", product, marketplace)
    return _api_query_sellers(product=product, marketplace=marketplace)


@cl.step(type="tool", name="query_trends")
@trim_for_llm(trim_timeseries_rows)
async def _step_query_trends(
    product: str,
    marketplace: str = "UK",
    series: str = "new",
    days: int = 90,
) -> dict:
    logger.info(
        "query_trends called: product=%s marketplace=%s series=%s days=%s",
        product,
        marketplace,
        series,
        days,
    )
    return _api_query_trends(product=product, marketplace=marketplace, series=series, days=days)


async def dispatch_tool(name: str, args: dict) -> dict:
    """Route a tool call from the LLM to the right Python function.

    Returns the amz_scout.api envelope dict. For tools that emit row data
    (latest/availability/compare/deals/ranking/sellers/trends), the ``data``
    list has been projected through ``trim_for_llm`` to the LLM-safe
    allow-list defined in ``amz_scout._llm_trim``; ``meta`` and ``error``
    are always passed through untouched. The LLM consumes meta/error/hint
    fields directly.
    """
    if name == "query_latest":
        marketplace = args.get("marketplace")
        if not marketplace:
            return _missing_required("query_latest", "marketplace")
        return await _step_query_latest(
            marketplace=marketplace,
            category=args.get("category"),
        )
    if name == "check_freshness":
        return await _step_check_freshness(
            marketplace=args.get("marketplace"),
            product=args.get("product"),
        )
    if name == "keepa_budget":
        return await _step_keepa_budget()
    if name == "query_availability":
        return await _step_query_availability()
    if name == "query_compare":
        product = args.get("product")
        if not product:
            return _missing_required("query_compare", "product")
        return await _step_query_compare(product=product)
    if name == "query_deals":
        return await _step_query_deals(marketplace=args.get("marketplace"))
    if name == "query_ranking":
        marketplace = args.get("marketplace")
        if not marketplace:
            return _missing_required("query_ranking", "marketplace")
        return await _step_query_ranking(
            marketplace=marketplace,
            category=args.get("category"),
        )
    if name == "query_sellers":
        product = args.get("product")
        if not product:
            return _missing_required("query_sellers", "product")
        return await _step_query_sellers(
            product=product,
            marketplace=args.get("marketplace", "UK"),
        )
    if name == "query_trends":
        product = args.get("product")
        if not product:
            return _missing_required("query_trends", "product")
        return await _step_query_trends(
            product=product,
            marketplace=args.get("marketplace", "UK"),
            series=args.get("series", "new"),
            days=args.get("days", 90),
        )

    # Unknown tool — return an envelope-shaped error so the LLM can recover
    logger.error("Unknown tool: %s", name)
    return {
        "ok": False,
        "data": [],
        "error": f"Unknown tool: {name}",
        "meta": {},
    }
