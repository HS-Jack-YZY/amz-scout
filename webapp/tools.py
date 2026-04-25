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
from amz_scout.api import ensure_keepa_data as _api_ensure_keepa_data
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
            "Auto-fetches Keepa (LAZY, 0 tokens if cached). For an explicit refresh "
            "that respects the token-batch gate, route through the `ensure_keepa_data` "
            "tool. Returns summary + xlsx."
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
        "name": "ensure_keepa_data",
        "description": (
            "Refresh/top-up Keepa cache for products × marketplaces. For "
            "'刷新'/'更新数据'/'refresh Keepa'. Consumes Keepa tokens (shared, 60 "
            "max, 1/min refill). Strategies: 'lazy' (fetch only if missing, "
            "default), 'fresh' (force refresh), 'max_age' (refresh if older "
            "than max_age_days), 'offline' (no fetch). Batches ≥6 tokens "
            "surface a confirmation dialog to the user — the UI handles it; "
            "the LLM does NOT need to prompt 'proceed?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "marketplace": {
                    "type": "string",
                    "description": f"{_MARKETPLACE_DESC} Omit for all.",
                },
                "product": {
                    "type": "string",
                    "description": "Optional product filter. Omit for all.",
                },
                "strategy": {
                    "type": "string",
                    "description": "Fetch strategy. Default 'lazy'.",
                    "enum": ["lazy", "offline", "max_age", "fresh"],
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "For max_age strategy. Default 7.",
                    "minimum": 1,
                },
                "detailed": {
                    "type": "boolean",
                    "description": (
                        "Fetch deep history (6 tokens/ASIN) vs basic "
                        "(1 token/ASIN). Default false."
                    ),
                },
            },
            "required": [],
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


def _fetch_failed_envelope(error: str) -> ApiResponse:
    return {
        "ok": False,
        "data": {},
        "error": error,
        "meta": {"phase": "fetch_failed"},
    }


def _extract_proceed(response: object) -> bool | None:
    """Decode the ``proceed`` flag from an ``AskActionMessage.send()`` result.

    Chainlit ``>=2.7,<3`` returns ``AskActionResponse`` (a TypedDict, dict
    at runtime). Accept attribute access too, so a future minor-version
    shape change (Pydantic / dataclass / ``cl.Action``) does not silently
    flip a Confirm into a Cancel. Returns ``None`` when the shape is
    unrecognized so the caller can fail loud.
    """
    payload = (
        response.get("payload")
        if isinstance(response, dict)
        else getattr(response, "payload", None)
    )
    if isinstance(payload, dict):
        return bool(payload.get("proceed"))
    if payload is not None and hasattr(payload, "proceed"):
        return bool(getattr(payload, "proceed"))
    return None


@cl.step(type="tool", name="ensure_keepa_data")
async def _step_ensure_keepa_data(
    marketplace: str | None = None,
    product: str | None = None,
    strategy: str = "lazy",
    max_age_days: int = 7,
    detailed: bool = False,
) -> ApiResponse:
    """Run ensure_keepa_data; if batch gate fires, surface a Chainlit confirm dialog.

    The api layer's contract returns ``phase="needs_confirmation"`` without
    fetching when estimated tokens ≥ ``_BATCH_TOKEN_THRESHOLD`` (6). We
    consume that envelope and surface an ``AskActionMessage``. Branches:

      - confirm clicked → re-call api with ``confirm=True`` and return its result.
      - cancel clicked → return ``phase="cancelled_by_user"`` envelope.
      - ``send()`` returns ``None`` (timeout / tab close / ws drop) →
        return ``phase="dialog_timeout"`` envelope (truthful, distinct
        from explicit cancel).
      - dialog returns a shape we do not recognize → ``phase="fetch_failed"``
        envelope with ``logger.exception``.
      - any raw exception from the api layer → caught and returned as
        ``phase="fetch_failed"`` envelope (defense-in-depth; ``api.py``
        already wraps its own body, but a transport / import error
        outside that body would otherwise crash the chat turn).

    ``confirm`` is deliberately NOT exposed in the tool schema — only this
    UI path sets it.
    """
    fetch_kwargs: dict[str, Any] = {
        "marketplace": marketplace,
        "product": product,
        "strategy": strategy,
        "max_age_days": max_age_days,
        "detailed": detailed,
    }
    logger.info(
        "ensure_keepa_data called: marketplace=%s product=%s strategy=%s "
        "max_age_days=%s detailed=%s",
        marketplace,
        product,
        strategy,
        max_age_days,
        detailed,
    )

    try:
        first = await asyncio.to_thread(
            _api_ensure_keepa_data, **fetch_kwargs, confirm=False
        )
    except Exception as exc:  # defense-in-depth: api.py already wraps its body
        logger.exception("ensure_keepa_data: api gate call raised")
        return _fetch_failed_envelope(str(exc))

    meta = first.get("meta") or {}
    if not first.get("ok"):
        logger.warning("ensure_keepa_data api error: %s", first.get("error"))
        return first
    if meta.get("phase") != "needs_confirmation":
        return first

    estimated_tokens = meta.get("estimated_tokens", "?")
    products_to_fetch = meta.get("products_to_fetch", "?")

    actions = [
        cl.Action(
            name="confirm",
            label="✓ Confirm & fetch",
            payload={"proceed": True},
        ),
        cl.Action(
            name="cancel",
            label="✗ Cancel",
            payload={"proceed": False},
        ),
    ]
    content = (
        f"⚠️ **Keepa fetch confirmation**\n\n"
        f"- Products to fetch: **{products_to_fetch}**\n"
        f"- Estimated token cost: **{estimated_tokens}** / 60 cap "
        f"(shared bucket, 1/min refill — current balance not shown)\n"
        f"- Strategy: `{strategy}`\n\n"
        f"This will consume Keepa API tokens."
    )
    response = await cl.AskActionMessage(
        content=content,
        actions=actions,
        timeout=120,
    ).send()

    if response is None:
        logger.warning("ensure_keepa_data dialog timed out after 120s")
        return {
            "ok": True,
            "data": {"cancelled": True, "reason": "timeout"},
            "error": None,
            "meta": {
                "phase": "dialog_timeout",
                "message": (
                    "No response within 120s; fetch not started — "
                    "re-run if you still want it."
                ),
            },
        }

    try:
        proceed = _extract_proceed(response)
    except Exception:
        logger.exception(
            "ensure_keepa_data: error decoding dialog response of type %s",
            type(response).__name__,
        )
        return _fetch_failed_envelope(
            "Confirmation dialog returned an unexpected shape; fetch aborted.",
        )

    if proceed is None:
        logger.error(
            "ensure_keepa_data: unrecognized AskActionMessage response shape: %s",
            type(response).__name__,
        )
        return _fetch_failed_envelope(
            "Confirmation dialog returned an unexpected shape; fetch aborted.",
        )

    if not proceed:
        logger.info("ensure_keepa_data cancelled by user")
        return {
            "ok": True,
            "data": {"cancelled": True, "reason": "user_cancel"},
            "error": None,
            "meta": {
                "phase": "cancelled_by_user",
                "message": "User cancelled the Keepa batch fetch.",
            },
        }

    logger.info("ensure_keepa_data confirmed; proceeding with fetch")
    try:
        return await asyncio.to_thread(
            _api_ensure_keepa_data, **fetch_kwargs, confirm=True
        )
    except Exception as exc:
        logger.exception("ensure_keepa_data: post-confirm fetch raised")
        return _fetch_failed_envelope(str(exc))


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
    if name == "ensure_keepa_data":
        return await _step_ensure_keepa_data(
            marketplace=args.get("marketplace"),
            product=args.get("product"),
            strategy=args.get("strategy", "lazy"),
            max_age_days=args.get("max_age_days", 7),
            detailed=args.get("detailed", False),
        )

    # Unknown tool — return an envelope-shaped error so the LLM can recover
    logger.error("Unknown tool: %s", name)
    return {
        "ok": False,
        "data": [],
        "error": f"Unknown tool: {name}",
        "meta": {},
    }
