"""Chainlit-wrapped tools that call amz_scout.api functions.

Phase 1 exposes only query_latest. Phase 2 will expand to the full query set.
"""

import logging
from typing import Any

import chainlit as cl

from amz_scout.api import query_latest as _api_query_latest

logger = logging.getLogger(__name__)


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
        # Cache this (and all preceding tools) — Phase 1 has only this tool.
        "cache_control": {"type": "ephemeral"},
    },
]


# ─── Tool dispatcher ─────────────────────────────────────────────
@cl.step(type="tool", name="query_latest")
async def _step_query_latest(marketplace: str, category: str | None = None) -> dict:
    """Chainlit step wrapper that shows tool inputs/outputs in the UI."""
    logger.info("query_latest called: marketplace=%s category=%s", marketplace, category)
    result = _api_query_latest(marketplace=marketplace, category=category)
    return result


async def dispatch_tool(name: str, args: dict) -> dict:
    """Route a tool call from the LLM to the right Python function.

    Returns the raw amz_scout.api envelope dict unchanged — the LLM will
    consume meta/error/hint fields directly.
    """
    if name == "query_latest":
        return await _step_query_latest(
            marketplace=args.get("marketplace", ""),
            category=args.get("category"),
        )

    # Unknown tool — return an envelope-shaped error so the LLM can recover
    logger.error("Unknown tool: %s", name)
    return {
        "ok": False,
        "data": [],
        "error": f"Unknown tool: {name}",
        "meta": {},
    }
