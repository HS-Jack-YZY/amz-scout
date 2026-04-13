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
    """Initialize a fresh conversation history for this session."""
    cl.user_session.set("history", [])
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
    """Handle a user message: run through the LLM + tool loop, send the reply."""
    history: list[dict] = cl.user_session.get("history", [])
    history.append({"role": "user", "content": msg.content})

    try:
        final_text, updated_history = await run_chat_turn(history)
    except Exception as e:
        logger.exception("run_chat_turn failed")
        await cl.Message(content=f"⚠️ Sorry, something went wrong: {e}").send()
        return

    cl.user_session.set("history", updated_history)
    await cl.Message(content=final_text).send()
