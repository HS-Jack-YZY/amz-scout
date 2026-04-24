"""Chainlit entry point for the amz-scout internal webapp.

Run with: chainlit run webapp/app.py -w
"""

import logging

import chainlit as cl

# Import order matters: config loads .env before anything else touches env vars
from webapp import config

config.validate_env()  # Loud failure if required env is missing

# These imports must come AFTER config.validate_env() to ensure env is set
from webapp import auth  # noqa: F401, E402 — registers the @cl.password_auth_callback
from webapp.llm import run_chat_turn  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("Webapp starting: model=%s db=%s", config.MODEL_ID, config.DB_PATH)


@cl.on_chat_start
async def on_chat_start() -> None:
    """Initialize a fresh conversation history for this session.

    Also seeds the Phase 3 query-passthrough state: ``query_log`` records
    each tool call for the (future) project-analysis mode, and
    ``pending_files`` buffers ``cl.File`` attachments produced by
    ``summarize_for_llm`` until ``on_message`` ships them.
    """
    cl.user_session.set("history", [])
    cl.user_session.set("query_log", [])
    cl.user_session.set("pending_files", [])
    user = cl.user_session.get("user")
    if user:
        await cl.Message(
            content=(
                f"欢迎 {user.identifier}! 可以向我提问任何 Amazon 产品数据问题。"
                f'\n\n示例: "show me latest UK data" 或 "最新的英国数据"'
            )
        ).send()


@cl.on_message
async def on_message(msg: cl.Message) -> None:
    """Handle a user message: run through the LLM + tool loop, send the reply.

    Phase 3: any xlsx attachments accumulated in ``pending_files`` during the
    turn are drained onto this reply's ``cl.Message.elements``. The drain is
    wrapped in ``try/finally`` so the session buffer is cleared on every
    exit path (success / LLM failure / future send failures) — the next
    turn must never inherit files from a previous turn.
    """
    history: list[dict] = cl.user_session.get("history", []) or []
    history.append({"role": "user", "content": msg.content})

    success = False
    final_text: str
    try:
        final_text, updated_history = await run_chat_turn(history)
        cl.user_session.set("history", updated_history)
        success = True
        # DIAG: log what we're about to send so we can distinguish
        # "LLM returned empty" from "cl.Message.send() dropped the content"
        logger.info(
            "on_message → cl.Message.send (len=%d, preview=%r)",
            len(final_text or ""),
            (final_text or "")[:300],
        )
    except Exception:
        # Keep stack + exception detail in server logs only — never echo the
        # raw exception to authenticated users since it can leak file paths,
        # configuration values, or internal error strings. Operators read
        # the actual cause from `logger.exception` server-side.
        logger.exception("run_chat_turn failed")
        final_text = (
            "⚠️ Sorry, something went wrong on the server. "
            "Please try again — if it keeps happening, ping the operator."
        )
    finally:
        # Invariant: pending_files is empty at the end of every turn. The
        # failure branch discards any partially-attached files since the
        # LLM never finished describing them to the user.
        pending = cl.user_session.get("pending_files", []) or []
        cl.user_session.set("pending_files", [])

    # Only attach files on a successful turn — a mid-turn exception means
    # the LLM reply referencing those files is gone, so shipping them
    # alone would confuse the user.
    if success and pending:
        await cl.Message(content=final_text, elements=pending).send()
    else:
        await cl.Message(content=final_text).send()
