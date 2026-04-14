"""Anthropic SDK integration: client init, tool-use loop, prompt caching."""

import json
import logging
from typing import Any

from anthropic import Anthropic

from webapp.config import MAX_TOKENS, MODEL_ID, SYSTEM_PROMPT
from webapp.tools import TOOL_SCHEMAS, dispatch_tool

logger = logging.getLogger(__name__)

_client = Anthropic()  # Reads ANTHROPIC_API_KEY from env automatically

# System prompt with ephemeral prompt caching.
# Anthropic caches the system block when cache_control is attached to it.
SYSTEM_BLOCKS: list[dict[str, Any]] = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    },
]


def _strip_cache_control_from_prior_tool_results(history: list[dict]) -> None:
    """Remove ``cache_control`` from every ``tool_result`` block in history.

    The moving cache_control breakpoint must actually MOVE: when we mark a
    new turn's tool_result as ephemeral, the old marker has to come off, or
    the total cache_control block count grows past Anthropic's hard limit
    of 4 per request. Dropping the marker does not invalidate the already-
    built cache — Anthropic's prefix match is over content tokens, not over
    the ``cache_control`` metadata itself.
    """
    for msg in history:
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block.pop("cache_control", None)


async def run_chat_turn(history: list[dict]) -> tuple[str, list[dict]]:
    """Run one chat turn with tool use until the model is done.

    Args:
        history: full conversation history as list of {role, content} dicts.

    Returns:
        (final_text, updated_history) where final_text is the last assistant
        text block and updated_history includes the full tool-use round-trip.
    """
    max_iterations = 10  # safety limit to prevent runaway tool calls
    for i in range(max_iterations):
        resp = _client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_BLOCKS,
            tools=TOOL_SCHEMAS,
            messages=history,
        )
        logger.info("usage: %s", resp.usage.model_dump())

        # Append the assistant turn to history (preserve typed content blocks).
        # Convert the SDK's Pydantic objects to dicts for history consistency.
        history.append(
            {
                "role": "assistant",
                "content": [block.model_dump() for block in resp.content],
            }
        )

        if resp.stop_reason != "tool_use":
            # Final response — extract text and return
            final_text = "".join(block.text for block in resp.content if block.type == "text")
            logger.info("Chat turn complete (iterations=%d)", i + 1)
            return final_text, history

        # LLM requested tools — run them all and feed results back
        tool_results: list[dict] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            logger.info("LLM requested tool: %s", block.name)
            result = await dispatch_tool(block.name, dict(block.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

        # Moving cache_control breakpoint: retire any prior marker, then
        # tag THIS iteration's last tool_result as ephemeral. One marker
        # at a time keeps us under Anthropic's 4-block-per-request limit.
        _strip_cache_control_from_prior_tool_results(history)
        if tool_results:
            tool_results[-1]["cache_control"] = {"type": "ephemeral"}
        # IMPORTANT: all tool results in ONE user message (for parallel tool safety)
        history.append({"role": "user", "content": tool_results})

    logger.warning("Hit max_iterations=%d in run_chat_turn", max_iterations)
    return (
        "(Tool-use loop exceeded max iterations. Please rephrase your question.)",
        history,
    )
