"""Shared webapp configuration loaded from environment."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env from repo root (one level above webapp/)
_REPO_ROOT = Path(__file__).parent.parent
_ENV_FILE = _REPO_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
    logger.info("Loaded .env from %s", _ENV_FILE)
else:
    logger.warning(".env not found at %s — relying on process environment", _ENV_FILE)

# ─── Model / LLM ─────────────────────────────────────────────────
MODEL_ID = "claude-sonnet-4-6"  # alias; dated ID: claude-sonnet-4-6-20260217
MAX_TOKENS = 4096
SYSTEM_PROMPT = (
    "You are an Amazon product data analyst assistant for GL.iNet. "
    "When the user asks about Amazon product prices, BSR, sales, deals, or sellers, "
    "call the available tools to fetch real data from the amz-scout database. "
    "Present results clearly in Chinese or English matching the user's language. "
    "Always show which tool you called and with what parameters so the user can verify."
)

# ─── Auth ────────────────────────────────────────────────────────
ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "@gl-inet.com")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# ─── Database ────────────────────────────────────────────────────
# Absolute path — never rely on CWD since Chainlit may run from elsewhere
DB_PATH = (_REPO_ROOT / "output" / "amz_scout.db").resolve()


# ─── Startup validation ──────────────────────────────────────────
def validate_env() -> None:
    """Raise ValueError if required env vars are missing."""
    required = {
        "ANTHROPIC_API_KEY": "Get from https://console.anthropic.com/",
        "CHAINLIT_AUTH_SECRET": "Generate with: chainlit create-secret",
        "APP_PASSWORD": "Set a strong shared password in .env",
        "KEEPA_API_KEY": "Already required by amz_scout.api",
    }
    missing = [f"  {k}: {reason}" for k, reason in required.items() if not os.environ.get(k)]
    if missing:
        raise ValueError("Missing required environment variables:\n" + "\n".join(missing))
