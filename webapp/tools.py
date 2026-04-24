"""Chainlit-wrapped tools that call amz_scout.api functions.

Phase 2 exposes all 9 read-only query tools so the LLM can answer the full
Scenario-1 research surface (latest snapshots, trends, compare, ranking,
availability, sellers, deals, freshness, Keepa budget).

Phase 3 query-passthrough boundary
-----------------------------------
Row-emitting tools (latest/availability/compare/deals/ranking/sellers/trends)
no longer return row data to the LLM. ``summarize_for_llm`` rewrites the
envelope's ``data`` list into ``{count, date_range, file_attached, preview}``
and attaches the full DB rows as an in-memory xlsx to
``cl.user_session['pending_files']``; ``webapp.app.on_message`` then ships
those files as ``cl.File`` elements on the final ``cl.Message``. The LLM only
sees summaries — the user downloads the complete data.

``trim_for_llm`` is retained for ``preview`` generation (3 sample rows kept
inside the summary) and as a regression safety valve; ``check_freshness`` /
``keepa_budget`` already return summary-shaped dicts so they skip the
decorator.
"""

import asyncio
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
from amz_scout.api import ApiResponse
from amz_scout.api import check_freshness as _api_check_freshness
from amz_scout.api import keepa_budget as _api_keepa_budget
from amz_scout.api import query_availability as _api_query_availability
from amz_scout.api import query_compare as _api_query_compare
from amz_scout.api import query_deals as _api_query_deals
from amz_scout.api import query_latest as _api_query_latest
from amz_scout.api import query_ranking as _api_query_ranking
from amz_scout.api import query_sellers as _api_query_sellers
from amz_scout.api import query_trends as _api_query_trends
from amz_scout.api import register_asin_from_url as _api_register_asin_from_url
from webapp.summaries import summarize_for_llm

logger = logging.getLogger(__name__)


def trim_for_llm(
    trimmer: Callable[[list[dict]], list[dict]],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Project the ``data`` list of an api envelope through ``trimmer``.

    Retained after Phase 3 for (a) generating the 3-row ``preview`` inside
    ``summarize_for_llm`` and (b) as a regression safety valve if
    ``summarize_for_llm`` is ever bypassed. Row-emitting wrappers no longer
    stack this on top — see module docstring.
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


def _missing_required(tool_name: str, field: str) -> ApiResponse:
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
#
# Phase 3 compression: docstrings trimmed ~250 tokens. LLM is told "returns
# summary + xlsx" so it stops trying to iterate row data — that plus Key
# Behavior #14 in CLAUDE.md are the levers that make summaries work.
_MARKETPLACE_DESC = (
    "Marketplace code ('UK'/'DE'/'US'/'JP'...). Accepts aliases "
    "('uk', 'amazon.co.uk', 'GB', 'GBP')."
)
_PRODUCT_DESC = "Product identifier — brand/model name (e.g. 'Slate 7') or ASIN. Required."

# Allow-list for Anthropic server-side web_search. Kept as a static constant
# (not derived at runtime from marketplaces.yaml) so the schema hash stays
# stable across requests — dynamic values would invalidate prompt caching.
_AMAZON_DOMAINS = [
    "amazon.com",
    "amazon.co.uk",
    "amazon.de",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.nl",
    "amazon.ca",
    "amazon.com.mx",
    "amazon.in",
    "amazon.com.br",
    "amazon.co.jp",
    "amazon.com.au",
]

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "query_latest",
        "description": (
            "Latest competitive snapshot (price/rating/BSR/availability) per product "
            "in a marketplace. For '当前/最新'. Reads competitive_snapshots; 0 Keepa "
            "tokens. Returns summary + xlsx download — do NOT iterate rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "description": _MARKETPLACE_DESC},
                "category": {
                    "type": "string",
                    "description": "Optional category filter (e.g. 'Travel Router').",
                },
            },
            "required": ["marketplace"],
        },
    },
    {
        "name": "check_freshness",
        "description": (
            "Cached Keepa data staleness matrix per product × marketplace "
            "('0d'/'3d'/'never'). For '数据多久没更新'. Read-only, 0 Keepa tokens."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": "Optional marketplace filter. Omit for all.",
                },
                "product": {
                    "type": "string",
                    "description": "Optional product filter (brand/model/ASIN). Omit for all.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "keepa_budget",
        "description": (
            "Current Keepa token balance and refill rate. For 'token余额'/'多少 token'. "
            "Costs 0 Keepa tokens."
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
            "Availability matrix: which products listed on which marketplaces. "
            "For '哪些国家有卖'/'availability'. Reads competitive_snapshots; 0 Keepa "
            "tokens. Returns summary + xlsx."
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
            "Cross-market compare ONE product: latest snapshot (price/rating/BSR) per "
            "marketplace. For '对比'/'cross-market'. 0 Keepa tokens. Returns summary + xlsx."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": _PRODUCT_DESC},
            },
            "required": ["product"],
        },
    },
    {
        "name": "query_deals",
        "description": (
            "Deal/promotion/discount history per marketplace. For '促销'/'折扣'/'deals'. "
            "Auto-fetches Keepa (LAZY, 0 tokens if cached); ≥6-token batches return "
            "phase='needs_confirmation' — surface it and ask user to confirm. "
            "Returns summary + xlsx."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": ("Marketplace code. Omit to query all registered marketplaces."),
                },
            },
            "required": [],
        },
    },
    {
        "name": "query_ranking",
        "description": (
            "Products ranked by Amazon BSR for a marketplace. For '排名'/'BSR'/'best sellers'. "
            "Reads competitive_snapshots; 0 Keepa tokens. Returns summary + xlsx."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {"type": "string", "description": _MARKETPLACE_DESC},
                "category": {
                    "type": "string",
                    "description": "Optional category filter.",
                },
            },
            "required": ["marketplace"],
        },
    },
    {
        "name": "query_sellers",
        "description": (
            "Buy Box seller history over time for ONE product × marketplace. For "
            "'卖家'/'Buy Box'. Auto-fetches Keepa (LAZY, 0 tokens if cached). "
            "Returns summary + xlsx."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": _PRODUCT_DESC},
                "marketplace": {
                    "type": "string",
                    "description": f"{_MARKETPLACE_DESC} Defaults to 'UK'.",
                },
            },
            "required": ["product"],
        },
    },
    # Anthropic server-side web_search (dynamic filtering built-in).
    # No description / input_schema — Anthropic defines and executes it
    # server-side; the client only declares the declaration. Do NOT
    # co-declare a standalone `code_execution` tool: dynamic filtering
    # already provisions one internally, and duplicating it creates a
    # second execution environment that confuses the model (per
    # Anthropic docs, 2026-02-09 release notes).
    {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 5,
        "allowed_domains": _AMAZON_DOMAINS,
    },
    {
        "name": "query_trends",
        "description": (
            "Price/BSR/sales time series for ONE product × marketplace over a window. "
            "For '价格趋势'/'历史价格'/'past N days'. Auto-fetches Keepa (LAZY, 0 tokens "
            "if cached). Prices encoded as cents (÷100 for real price). Call once per "
            "product × marketplace for multi-compare. Returns summary + xlsx."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {"type": "string", "description": _PRODUCT_DESC},
                "marketplace": {
                    "type": "string",
                    "description": f"{_MARKETPLACE_DESC} Defaults to 'UK'.",
                },
                "series": {
                    "type": "string",
                    "description": "Series type. Default 'new'.",
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
                    "description": "Lookback in days. Default 90. Common: 7/30/90/180/365.",
                    "minimum": 1,
                    "maximum": 730,
                },
            },
            "required": ["product"],
        },
    },
    {
        "name": "register_asin_from_url",
        "description": (
            "Register a product's ASIN into the registry by parsing an Amazon "
            "product URL. Accepts both '.../dp/<ASIN>/...' and the older "
            "'.../gp/product/<ASIN>/...' forms (both still surfaced by "
            "web_search). Use after web_search returns an Amazon product "
            "URL. Creates the product if the (brand, model) pair is new; "
            "otherwise appends the marketplace mapping. Validates that the "
            "URL host matches the target marketplace (e.g. amazon.de for "
            "DE) — rejects mismatches to prevent wrong-market writes. "
            "Does NOT consume Keepa tokens."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {
                    "type": "string",
                    "description": "Product brand (e.g. 'TP-Link').",
                },
                "model": {
                    "type": "string",
                    "description": "Product model (e.g. 'AX1500').",
                },
                "marketplace": {
                    "type": "string",
                    "description": _MARKETPLACE_DESC,
                },
                "amazon_url": {
                    "type": "string",
                    "description": (
                        "Full Amazon product page URL containing "
                        "'/dp/<10-char-ASIN>' OR '/gp/product/<10-char-ASIN>'. "
                        "Obtained from web_search results."
                    ),
                },
            },
            "required": ["brand", "model", "marketplace", "amazon_url"],
        },
        # Cache_control on the LAST tool only — caches all preceding tool
        # definitions together. If a new tool is appended after this one,
        # move the marker to the new tail.
        "cache_control": {"type": "ephemeral"},
    },
]


# ─── Tool dispatcher ─────────────────────────────────────────────
@cl.step(type="tool", name="query_latest")
@summarize_for_llm(
    tool_name="query_latest",
    file_name_parts=lambda kw: ["query_latest", kw.get("marketplace"), kw.get("category")],
    preview_trimmer=trim_competitive_rows,
    date_field="scraped_at",
    sheet_name="latest_snapshot",
)
async def _step_query_latest(marketplace: str, category: str | None = None) -> ApiResponse:
    """Chainlit step wrapper that shows tool inputs/outputs in the UI."""
    logger.info("query_latest called: marketplace=%s category=%s", marketplace, category)
    return await asyncio.to_thread(
        _api_query_latest, marketplace=marketplace, category=category
    )


@cl.step(type="tool", name="check_freshness")
async def _step_check_freshness(
    marketplace: str | None = None, product: str | None = None
) -> ApiResponse:
    logger.info("check_freshness called: marketplace=%s product=%s", marketplace, product)
    return await asyncio.to_thread(
        _api_check_freshness, marketplace=marketplace, product=product
    )


@cl.step(type="tool", name="keepa_budget")
async def _step_keepa_budget() -> ApiResponse:
    logger.info("keepa_budget called")
    return await asyncio.to_thread(_api_keepa_budget)


@cl.step(type="tool", name="query_availability")
@summarize_for_llm(
    tool_name="query_availability",
    file_name_parts=lambda _kw: ["query_availability"],
    preview_trimmer=trim_competitive_rows,
    date_field="scraped_at",
    sheet_name="availability",
)
async def _step_query_availability() -> ApiResponse:
    logger.info("query_availability called")
    return await asyncio.to_thread(_api_query_availability)


@cl.step(type="tool", name="query_compare")
@summarize_for_llm(
    tool_name="query_compare",
    file_name_parts=lambda kw: ["query_compare", kw.get("product")],
    preview_trimmer=trim_competitive_rows,
    date_field="scraped_at",
    sheet_name="compare",
)
async def _step_query_compare(product: str) -> ApiResponse:
    logger.info("query_compare called: product=%s", product)
    return await asyncio.to_thread(_api_query_compare, product=product)


@cl.step(type="tool", name="query_deals")
@summarize_for_llm(
    tool_name="query_deals",
    file_name_parts=lambda kw: ["query_deals", kw.get("marketplace")],
    preview_trimmer=trim_deals_rows,
    # start_time/end_time are Keepa-encoded minute integers; min/max on them
    # produces garbage date_range like "7584000 to 7590000". Disable for now.
    date_field=None,
    sheet_name="deals",
)
async def _step_query_deals(marketplace: str | None = None) -> ApiResponse:
    logger.info("query_deals called: marketplace=%s", marketplace)
    return await asyncio.to_thread(_api_query_deals, marketplace=marketplace)


@cl.step(type="tool", name="query_ranking")
@summarize_for_llm(
    tool_name="query_ranking",
    file_name_parts=lambda kw: ["query_ranking", kw.get("marketplace"), kw.get("category")],
    preview_trimmer=trim_competitive_rows,
    date_field="scraped_at",
    sheet_name="ranking",
)
async def _step_query_ranking(marketplace: str, category: str | None = None) -> ApiResponse:
    logger.info("query_ranking called: marketplace=%s category=%s", marketplace, category)
    return await asyncio.to_thread(
        _api_query_ranking, marketplace=marketplace, category=category
    )


@cl.step(type="tool", name="query_sellers")
@summarize_for_llm(
    tool_name="query_sellers",
    file_name_parts=lambda kw: ["query_sellers", kw.get("product"), kw.get("marketplace")],
    preview_trimmer=trim_seller_rows,
    date_field="date",
    sheet_name="sellers",
)
async def _step_query_sellers(product: str, marketplace: str = "UK") -> ApiResponse:
    logger.info("query_sellers called: product=%s marketplace=%s", product, marketplace)
    return await asyncio.to_thread(
        _api_query_sellers, product=product, marketplace=marketplace
    )


@cl.step(type="tool", name="query_trends")
@summarize_for_llm(
    tool_name="query_trends",
    file_name_parts=lambda kw: [
        "query_trends",
        kw.get("product"),
        kw.get("marketplace"),
        kw.get("series"),
    ],
    preview_trimmer=trim_timeseries_rows,
    date_field="date",
    sheet_name="trends",
)
async def _step_query_trends(
    product: str,
    marketplace: str = "UK",
    series: str = "new",
    days: int = 90,
) -> ApiResponse:
    logger.info(
        "query_trends called: product=%s marketplace=%s series=%s days=%s",
        product,
        marketplace,
        series,
        days,
    )
    return await asyncio.to_thread(
        _api_query_trends,
        product=product,
        marketplace=marketplace,
        series=series,
        days=days,
    )


@cl.step(type="tool", name="register_asin_from_url")
async def _step_register_asin_from_url(
    brand: str,
    model: str,
    marketplace: str,
    amazon_url: str,
) -> ApiResponse:
    logger.info(
        "register_asin_from_url called: brand=%s model=%s marketplace=%s url=%s",
        brand,
        model,
        marketplace,
        amazon_url,
    )
    return await asyncio.to_thread(
        _api_register_asin_from_url,
        brand=brand,
        model=model,
        marketplace=marketplace,
        amazon_url=amazon_url,
    )


async def dispatch_tool(name: str, args: dict) -> ApiResponse:
    """Route a tool call from the LLM to the right Python function.

    Returns the amz_scout.api envelope dict. For row-emitting tools
    (latest/availability/compare/deals/ranking/sellers/trends), Phase 3
    rewrites the ``data`` field into a summary dict via ``summarize_for_llm``
    and attaches the full DB rows to ``cl.user_session['pending_files']`` as
    xlsx. ``check_freshness`` / ``keepa_budget`` pass through unchanged —
    they're already summary-shaped. ``meta`` and ``error`` are always
    passed through untouched.
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

    # Unknown tool — return an envelope-shaped error so the LLM can recover
    logger.error("Unknown tool: %s", name)
    return {
        "ok": False,
        "data": [],
        "error": f"Unknown tool: {name}",
        "meta": {},
    }
