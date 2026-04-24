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
    "Always show which tool you called and with what parameters so the user can verify.\n\n"
    "## ASIN Discovery Flow\n"
    "When a query tool returns 'not_listed' or the user asks about a product whose "
    "ASIN is not yet in the registry for the target marketplace:\n"
    "1. First confirm the product is genuinely missing by calling the relevant query "
    "tool (e.g. query_compare or query_latest).\n"
    "2. Use web_search with a query like 'site:amazon.<tld> <brand> <model>' — pick "
    "<tld> from the marketplace (UK=co.uk, DE=de, JP=co.jp, US=com, ...). "
    "Limit to 1-2 searches per product unless the first results are clearly wrong.\n"
    "3. From web_search results, pick the Amazon product page URL that best matches "
    "the user's requested product (check title, brand, model in the snippet).\n"
    "4. Call register_asin_from_url(brand, model, marketplace, amazon_url) to "
    "record the ASIN. The tool rejects the URL when its host does not match "
    "the target marketplace's amazon_domain; pass the marketplace the user "
    "asked about — the tool does NOT cross-check the marketplace arg against "
    "the user's original intent, so that choice is your responsibility.\n"
    "5. After successful registration, re-run the original query tool.\n"
    "6. Always show the user the registered ASIN + Amazon title from the next query "
    "so they can verify it's the right product; if wrong, advise them to contact "
    "the operator to remove the mapping.\n\n"
    "Never call register_asin_from_url with a URL the user did not see in a "
    "web_search result or in their own message — do not fabricate Amazon URLs."
)

# ─── Auth ────────────────────────────────────────────────────────
# Always anchor the domain with a leading "@" so endswith() can't be tricked
# by a lookalike like "attacker@evilgl-inet.com" when the operator forgets the
# "@" in their .env. We also lowercase here so callers don't have to.
_raw_allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN", "@gl-inet.com").strip().lower()
if not _raw_allowed_domain or _raw_allowed_domain == "@":
    # Empty / whitespace-only env var → fall back to default instead of
    # silently producing "@" which would lock out all users.
    _raw_allowed_domain = "@gl-inet.com"
elif not _raw_allowed_domain.startswith("@"):
    _raw_allowed_domain = "@" + _raw_allowed_domain
ALLOWED_EMAIL_DOMAIN = _raw_allowed_domain
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
