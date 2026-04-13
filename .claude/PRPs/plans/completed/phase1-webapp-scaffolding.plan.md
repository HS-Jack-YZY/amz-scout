# Plan: Phase 1 — amz-scout Webapp Scaffolding

## Summary

Create a minimal **Chainlit 2.x** webapp under a new `webapp/` module that imports `amz_scout.api` as a library, handles email-domain-whitelisted password auth, binds **Claude Sonnet 4.6** via the Anthropic Python SDK with function calling, and wires `query_latest` end-to-end so a whitelisted user can log in, ask *"show me latest UK data"*, and see the envelope result rendered in the chat UI. **Zero modifications** to the existing `amz_scout/` package — the webapp is a new standalone `webapp/` module at the repo root.

## User Story

As Jack (implementer), I want a runnable Chainlit scaffold with working auth + one real tool end-to-end, so that subsequent phases (2–8) have a proven foundation to extend without reworking the core wiring.

## Problem → Solution

**Current**: `amz_scout.api` is callable only from a Python REPL or the `amz-scout` CLI. No multi-user web interface exists. Colleagues cannot self-serve; they send Excel lists to Jack.

**Desired**: Running `chainlit run webapp/app.py -w` serves a chat UI at `http://localhost:8000`. Logging in with an `@gl-inet.com` email + password reaches a chat screen. Asking *"show me latest UK data"* triggers Sonnet 4.6 → `query_latest(marketplace="UK")` → envelope result rendered as a chat message with an expandable tool-call step.

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/internal-amz-scout-web.prd.md`
- **PRD Phase**: Phase 1 — Scaffolding
- **Estimated Files**: 7 new + 2 modified
- **Estimated Lines**: ~300 total
- **Estimated Time**: ~9 hours (matches PRD W1 D1-D3 budget)

---

## UX Design

### Before (current state)

```
┌─────────────────────────────────────────────────┐
│  小李 opens Slack/email                         │
│  ↓                                              │
│  "Jack 能帮我查下这几个 ASIN 在 UK 的最新数据吗" │
│  ↓                                              │
│  (waits ~1 day for Jack to find time)          │
│  ↓                                              │
│  Jack runs: amz-scout query latest -m UK       │
│  ↓                                              │
│  Jack sends CSV back                           │
│  ↓                                              │
│  小李 opens CSV in Excel                        │
└─────────────────────────────────────────────────┘
```

### After (Phase 1 target)

```
┌─────────────────────────────────────────────────┐
│  小李 opens https://localhost:8000 (dev)        │
│  ↓                                              │
│  Logs in: xiaoli@gl-inet.com + password        │
│  ↓                                              │
│  Types: "show me latest UK data"               │
│  ↓                                              │
│  Chainlit UI shows:                            │
│    🔧 Step: query_latest(marketplace="UK")     │
│         ▸ params resolved                       │
│         ▸ raw envelope returned                 │
│    💬 Message: "Here's the latest UK data..."  │
│  ↓                                              │
│  (Phase 5 will add: 📎 uk_latest.xlsx download)│
└─────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Entry | Slack/email to Jack | Web URL login | Requires Chainlit dev server running locally in Phase 1; AWS-deployed in Phase 6 |
| Auth | Implicit (it's Jack) | Email + password | Domain-whitelisted (`@gl-inet.com`) |
| Query | Free-form text to Jack | Free-form text to LLM | Sonnet 4.6 translates NL to tool call |
| Result | CSV attached to email | Chat message + tool step UI | Excel export deferred to Phase 5 |

---

## Mandatory Reading

Before implementation, read these files and sections in order:

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `src/amz_scout/api.py` | 1-9 | Module docstring — confirms envelope contract: *"Every public function takes simple strings/ints and returns a dict envelope... No exceptions are raised to the caller."* |
| **P0** | `src/amz_scout/api.py` | 81-87 | `ApiResponse` TypedDict — the exact envelope shape tools will return |
| **P0** | `src/amz_scout/api.py` | 287-302 | `_envelope()` helper — shows how `meta` + `hint_if_empty` flows in; Chainlit tool wrappers return this same shape unchanged |
| **P0** | `src/amz_scout/api.py` | 445-460 | `query_latest` signature — `(project: str \| None, marketplace: str \| None, category: str \| None) -> dict`; the Phase-1 target function |
| **P1** | `src/amz_scout/scraper/keepa.py` | 26-49 | `_load_dotenv()` + `os.environ.get("KEEPA_API_KEY", "")` — the existing env loading pattern; webapp should *not* import this private helper but should honor the same `.env` convention |
| **P1** | `src/amz_scout/db.py` | 125-143 | `open_db` context manager + `resolve_db_path` — webapp must pass an **absolute** `db_path` (PRD decision 4) because Chainlit working directory may differ |
| **P1** | `pyproject.toml` | 10-23 | Current dependencies + optional `dev` extras — webapp adds a new `web` optional extras group (no changes to core `dependencies`) |
| **P1** | `pyproject.toml` | 31-44 | pytest markers + ruff config — webapp tests reuse `unit`/`integration` markers; ruff line-length is 100 |
| **P2** | `tests/test_api.py` | 1-80 | Imports pattern + fixture layout — webapp smoke test mirrors this structure |
| **P2** | `tests/test_api.py` | 780-806 | `TestQueryWithoutProject` class — test pattern is `r = query_xxx(); assert r["ok"] is True` |
| **P2** | `tests/conftest.py` | 1-24 | Fixture pattern + `Path(__file__).parent.parent / "output"` pattern for test data |
| **P2** | `.env.example` | 1-5 | Existing env file structure — webapp adds 3 new vars alongside `KEEPA_API_KEY` |

---

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Chainlit install & run | https://docs.chainlit.io/get-started/installation | `pip install chainlit`; run with `chainlit run app.py -w` (watch mode) |
| Chainlit password auth | https://docs.chainlit.io/authentication/password | `@cl.password_auth_callback` takes `(username, password)`, returns `cl.User(identifier=...)` or `None`; requires `CHAINLIT_AUTH_SECRET` env (generate with `chainlit create-secret`) |
| Chainlit Step | https://docs.chainlit.io/concepts/step | `@cl.step(type="tool")` decorator renders tool calls as expandable UI cards |
| Chainlit File element | https://docs.chainlit.io/api-reference/elements/file | `cl.File(name=..., path=..., display="inline")` via `elements=[...]` on `cl.Message` — for Phase 5 Excel export |
| Chainlit parallel tool issue | https://github.com/Chainlit/chainlit/issues/2662 | Historical bug converting Anthropic parallel tool calls — **workaround: write the tool loop manually**, don't rely on Chainlit auto-wiring |
| Anthropic Python SDK | https://github.com/anthropics/anthropic-sdk-python | `pip install anthropic`; `Anthropic()` auto-loads `ANTHROPIC_API_KEY` from env |
| Anthropic tool use | https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use | Tools defined as `{name, description, input_schema}`; loop on `stop_reason == "tool_use"` |
| Anthropic tool call handling | https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls | Tool result goes into a `user` message (not `tool` role like OpenAI) with `{"type": "tool_result", "tool_use_id": ..., "content": ...}` blocks |
| Anthropic prompt caching | https://platform.claude.com/docs/en/build-with-claude/prompt-caching | `cache_control: {"type": "ephemeral"}` on the **LAST** tool caches everything before it; minimum 1024 tokens for Sonnet |
| Sonnet 4.6 pricing | https://platform.claude.com/docs/en/build-with-claude/prompt-caching | Input $3/MTok, Output $15/MTok, 5m cache write $3.75, cache read $0.30/MTok |
| Sonnet 4.6 model ID | https://caylent.com/blog/claude-sonnet-4-6-in-production-capability-safety-and-cost-explained | Alias `claude-sonnet-4-6`; dated `claude-sonnet-4-6-20260217` (released 2026-02-17) |

---

## Patterns to Mirror

Code patterns discovered in the existing codebase — new code in `webapp/` must match these.

### ENVELOPE_PATTERN — every tool returns `{ok, data, error, meta}`
```python
# SOURCE: src/amz_scout/api.py:287-302
def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    hint_if_empty: str | None = None,
    **meta: Any,
) -> ApiResponse:
    """Build the standard response envelope."""
    if hint_if_empty and not data:
        meta["hint"] = hint_if_empty
    return {
        "ok": ok,
        "data": data if data is not None else [],
        "error": error,
        "meta": meta,
    }
```
**Webapp usage**: Chainlit tools call `amz_scout.api.query_latest(...)` and **return the dict envelope unchanged**. Do not wrap, flatten, or translate it — the LLM receives the envelope directly as tool output, so the meta/hint/error structure is preserved across the translation boundary.

### LOGGER_PATTERN — module-level `logging.getLogger(__name__)`
```python
# SOURCE: src/amz_scout/api.py:11, 52
import logging
...
logger = logging.getLogger(__name__)
```
**Webapp usage**: Every webapp module starts with `import logging` + `logger = logging.getLogger(__name__)`. **No `print()` statements** (per Python rule `rules/python/hooks.md`: "Warn about `print()` statements in edited files — use `logging` module instead").

### ERROR_HANDLING_PATTERN — try/except with logger.exception + envelope
```python
# SOURCE: src/amz_scout/api.py:451-460
def query_latest(
    project: str | None = None,
    marketplace: str | None = None,
    category: str | None = None,
) -> dict:
    """Latest competitive snapshot per product/site."""
    try:
        info = _resolve_context(project, marketplace=marketplace, category=category)
        site = _resolve_site(marketplace, info.marketplace_aliases)
        with open_db(info.db_path) as conn:
            rows = _db_query_latest(conn, site=site, category=category)
    except Exception as e:
        logger.exception("query_latest failed")
        return _envelope(False, error=str(e))

    return _envelope(True, data=rows, hint_if_empty=BROWSER_QUERY_HINT, count=len(rows))
```
**Webapp usage**: Webapp tool wrappers **do not add a second layer of try/except** around `amz_scout.api` calls — the API already catches everything and returns `{ok: False, error: ...}`. Webapp's own try/except should only wrap Chainlit/Anthropic-specific failures (e.g., network timeouts to Anthropic, Chainlit message send failures), not the `amz_scout.api` layer.

### ENV_LOADING_PATTERN — reads `.env` from repo root, uses `os.environ.get`
```python
# SOURCE: src/amz_scout/scraper/keepa.py:26-49
def _load_dotenv() -> None:
    """Load .env file from project root if it exists."""
    for d in [Path.cwd(), Path(__file__).parent.parent.parent.parent]:
        env_file = d / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break

class KeepaClient:
    def __init__(self, api_key: str | None = None) -> None:
        _load_dotenv()
        self._key = api_key or os.environ.get("KEEPA_API_KEY", "")
        if not self._key:
            raise ValueError(
                "Keepa API key required. Create a .env file with KEEPA_API_KEY=your_key "
                "or set the KEEPA_API_KEY environment variable."
            )
```
**Webapp usage**: The webapp **does not import `_load_dotenv`** (it's private to `scraper/keepa.py`). Instead, the webapp uses the standard `python-dotenv` package (added to `web` extras) and calls `load_dotenv()` once at module import in `webapp/app.py`. This ensures `KEEPA_API_KEY` (for `amz_scout.api` internal calls), `ANTHROPIC_API_KEY`, `CHAINLIT_AUTH_SECRET`, and `APP_PASSWORD` are all loaded before any other code runs. The hand-rolled `_load_dotenv()` in `scraper/keepa.py` continues to work as a fallback when `amz_scout.api` is used without the webapp.

### DB_PATH_PATTERN — resolve with absolute path
```python
# SOURCE: src/amz_scout/db.py:134-143
def resolve_db_path(output_dir: str | None = None) -> Path:
    """Resolve the shared database path.

    The DB lives at ``output/amz_scout.db`` (one level above per-project dirs).
    """
    if output_dir:
        return Path(output_dir).parent / "amz_scout.db"
    return Path("output") / "amz_scout.db"
```
**Webapp usage**: The webapp must compute an **absolute** db path at import time (from `Path(__file__).parent.parent / "output" / "amz_scout.db"`) and pass it explicitly to any `amz_scout.api` function that takes `db_path`. Relying on the default relative path (`Path("output") / "amz_scout.db"`) is fragile because Chainlit's working directory may differ from the repo root. Most query functions don't expose `db_path` directly — they use `_resolve_context()` which in turn uses `resolve_db_path()`. So the working-directory fix is: **run `chainlit run` from the repo root**, verified by a startup assertion in `webapp/app.py`.

### TEST_STRUCTURE_PATTERN — pytest classes + `db_in_cwd` fixture
```python
# SOURCE: tests/test_api.py:780-784
class TestQueryWithoutProject:
    """Test that query functions work with project=None (DB-backed)."""

    def test_query_latest_without_project(self, db_in_cwd):
        r = query_latest()
        assert r["ok"] is True
```
**Webapp usage**: Webapp smoke test `tests/test_webapp_smoke.py` mirrors this: class-grouped (`TestWebappScaffold`), reuses the `db_in_cwd` fixture from `tests/conftest.py`, and asserts the envelope shape. Mock the Anthropic API client — do not hit the real Claude endpoint in unit tests (use a stub that returns a fake `tool_use` block for `query_latest`).

### PYPROJECT_EXTRAS_PATTERN — optional deps under named extras
```toml
# SOURCE: pyproject.toml:18-23
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
]
```
**Webapp usage**: Add a new `web` extras group. Install with `pip install -e ".[web]"` for webapp development, keeping core `amz-scout` CLI install unchanged.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `pyproject.toml` | UPDATE | Add `[project.optional-dependencies] web = [...]` with chainlit, anthropic, python-dotenv, openpyxl |
| `.env.example` | UPDATE | Add `CHAINLIT_AUTH_SECRET`, `ANTHROPIC_API_KEY`, `APP_PASSWORD`, `ALLOWED_EMAIL_DOMAIN` |
| `webapp/__init__.py` | CREATE | Module marker with `__version__ = "0.1.0"` and a comment pointing to the PRD |
| `webapp/config.py` | CREATE | Constants + env loading: model ID, allowed email domain, system prompt, absolute DB path computation |
| `webapp/auth.py` | CREATE | `@cl.password_auth_callback` implementation — email domain whitelist + shared password check |
| `webapp/tools.py` | CREATE | Chainlit tool wrapper for `amz_scout.api.query_latest` + Anthropic tool schema definition |
| `webapp/llm.py` | CREATE | Anthropic client init, tool-use loop, prompt caching on tools + system |
| `webapp/app.py` | CREATE | Chainlit entry point: auth callback registration + `@cl.on_message` handler + session management |
| `tests/test_webapp_smoke.py` | CREATE | Smoke test: import check + 1 envelope round-trip through the tool wrapper with mocked LLM |
| `.chainlit/config.toml` | CREATE | Minimal Chainlit project config (generated by `chainlit init` or hand-written) |

## NOT Building (explicit Phase 1 scope boundary)

- **Other query tools** (`query_trends`, `query_compare`, etc.) — Phase 2
- **Management tools** (`add_product`, `list_products`, etc.) — Phase 3
- **Long-running / high-risk tools** (`ensure_keepa_data`, `batch_discover`) — Phase 4
- **Excel export via `cl.File`** — Phase 5
- **Docker + Lightsail deployment** — Phase 6
- **Multi-user per-account bcrypt passwords** — Phase 6 (Phase 1 uses a single shared `APP_PASSWORD` env var)
- **SQLAlchemy / Literal AI data layer** (persistent chat history across restarts) — Out of scope for MVP
- **OAuth / SSO** — Out of MVP scope (PRD Won't)
- **Streaming responses** — Not required for Phase 1 success signal; Chainlit supports it but we return full messages for simplicity
- **Custom frontend styling / branding** — Default Chainlit UI is sufficient for MVP

---

## Step-by-Step Tasks

### Task 1: Add `web` optional dependencies to `pyproject.toml`
- **ACTION**: Edit `pyproject.toml` to add a new optional extras group
- **IMPLEMENT**:
  ```toml
  [project.optional-dependencies]
  dev = [
      "pytest>=8.0",
      "pytest-cov>=5.0",
      "ruff>=0.4",
  ]
  web = [
      "chainlit>=2.7,<3",
      "anthropic>=0.40",
      "python-dotenv>=1.0",
      "openpyxl>=3.1",    # for Phase 5 Excel export — add now to avoid re-editing
  ]
  ```
- **MIRROR**: `PYPROJECT_EXTRAS_PATTERN`
- **IMPORTS**: n/a (TOML)
- **GOTCHA**: Do **not** move chainlit/anthropic into the core `dependencies` list — the `amz-scout` CLI must still install cleanly without them.
- **VALIDATE**:
  ```bash
  pip install -e ".[web]" 2>&1 | tail -20
  python -c "import chainlit, anthropic, dotenv, openpyxl; print('ok')"
  ```
  EXPECT: Installs without errors; import test prints `ok`.

### Task 2: Extend `.env.example` with webapp env vars
- **ACTION**: Append new env vars to `.env.example`
- **IMPLEMENT**: Read current `.env.example`, then append these lines:
  ```bash
  # ─── Webapp (Phase 1+) ───────────────────────────────────────────
  # Anthropic API key for Claude Sonnet 4.6
  ANTHROPIC_API_KEY=sk-ant-xxxxx
  # Chainlit JWT secret — generate with: chainlit create-secret
  CHAINLIT_AUTH_SECRET=replace-with-generated-secret
  # Shared MVP password (Phase 1 — per-user bcrypt in Phase 6)
  APP_PASSWORD=replace-with-strong-password
  # Email domain whitelist (default is correct for GL.iNet)
  ALLOWED_EMAIL_DOMAIN=@gl-inet.com
  ```
- **MIRROR**: Follow the existing `KEEPA_API_KEY=...` line format in `.env.example`
- **IMPORTS**: n/a
- **GOTCHA**: The real `.env` (not `.env.example`) must **never** be committed. Verify `.env` is in `.gitignore` before proceeding to Task 8.
- **VALIDATE**:
  ```bash
  cat .env.example | grep -E "(ANTHROPIC|CHAINLIT|APP_PASSWORD|ALLOWED_EMAIL)"
  ```
  EXPECT: All 4 new lines present.

### Task 3: Create `webapp/__init__.py` and `webapp/config.py`
- **ACTION**: Create the webapp module skeleton with shared config
- **IMPLEMENT**: `webapp/__init__.py`:
  ```python
  """amz-scout webapp: Chainlit-based natural-language UI over amz_scout.api.

  See .claude/PRPs/prds/internal-amz-scout-web.prd.md for the product requirements.
  See .claude/PRPs/plans/phase1-webapp-scaffolding.plan.md for the Phase 1 plan.
  """

  __version__ = "0.1.0"
  ```

  `webapp/config.py`:
  ```python
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
      missing = [
          f"  {k}: {reason}"
          for k, reason in required.items()
          if not os.environ.get(k)
      ]
      if missing:
          raise ValueError(
              "Missing required environment variables:\n" + "\n".join(missing)
          )
  ```
- **MIRROR**: `LOGGER_PATTERN`, `ENV_LOADING_PATTERN`, `DB_PATH_PATTERN`
- **IMPORTS**: `logging`, `os`, `pathlib.Path`, `dotenv.load_dotenv`
- **GOTCHA**: **`CHAINLIT_AUTH_SECRET` missing is silent failure** — Chainlit accepts the login and then the session dies. The `validate_env()` assertion **must** be called at app startup (Task 7) to catch this loudly.
- **VALIDATE**:
  ```bash
  python -c "from webapp import config; print(config.DB_PATH, config.ALLOWED_EMAIL_DOMAIN)"
  ```
  EXPECT: Prints absolute path ending in `output/amz_scout.db` + `@gl-inet.com`.

### Task 4: Create `webapp/auth.py` with email-whitelisted password callback
- **ACTION**: Implement `password_auth_callback` with domain check
- **IMPLEMENT**:
  ```python
  """Chainlit password auth callback with @gl-inet.com email whitelist."""

  import logging

  import chainlit as cl

  from webapp.config import ALLOWED_EMAIL_DOMAIN, APP_PASSWORD

  logger = logging.getLogger(__name__)


  @cl.password_auth_callback
  def auth_callback(username: str, password: str) -> cl.User | None:
      """Authenticate a user by email domain + shared password.

      Phase 1 MVP: all @gl-inet.com emails share a single APP_PASSWORD.
      Phase 6 will replace this with per-user bcrypt hashes.
      """
      email = username.strip().lower()

      if not email.endswith(ALLOWED_EMAIL_DOMAIN.lower()):
          logger.warning("Auth rejected: email %r not in allowed domain %s",
                         email, ALLOWED_EMAIL_DOMAIN)
          return None

      if not APP_PASSWORD:
          logger.error("APP_PASSWORD is empty — all auth will fail")
          return None

      if password != APP_PASSWORD:
          logger.warning("Auth rejected: wrong password for %s", email)
          return None

      logger.info("Auth OK for %s", email)
      return cl.User(
          identifier=email,
          metadata={"role": "user", "domain": ALLOWED_EMAIL_DOMAIN},
      )
  ```
- **MIRROR**: `LOGGER_PATTERN`
- **IMPORTS**: `logging`, `chainlit as cl`, `webapp.config`
- **GOTCHA**:
  - Use `str.lower()` + `.endswith()` — don't regex-match, it's overkill and less readable.
  - Constant-time password comparison is **not** required for Phase 1 (single shared password, internal tool). Phase 6 upgrade to bcrypt will add this.
  - `return None` on any failure path — do **not** raise, Chainlit silently hangs on exceptions in the callback.
- **VALIDATE**: Test via Chainlit's built-in login screen in Task 9. No direct unit test of this callback (Chainlit harness needed).

### Task 5: Create `webapp/tools.py` with `query_latest` wrapper
- **ACTION**: Define the Anthropic tool schema + Chainlit tool wrapper
- **IMPLEMENT**:
  ```python
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
  ```
- **MIRROR**: `ENVELOPE_PATTERN`, `LOGGER_PATTERN`, `ERROR_HANDLING_PATTERN` (note: no try/except around `_api_query_latest` because it's already wrapped internally)
- **IMPORTS**: `logging`, `typing.Any`, `chainlit as cl`, `amz_scout.api.query_latest as _api_query_latest`
- **GOTCHA**:
  - The `@cl.step` decorator must wrap an `async` function even though `query_latest` is sync — Chainlit steps need async for UI streaming.
  - `cache_control` goes on the **last** tool in `TOOL_SCHEMAS`. In Phase 2/3 when more tools are added, move `cache_control` to the new last tool and remove it from `query_latest`.
  - **Do not** add a try/except around `_api_query_latest()` — it already returns `{ok: False, ...}` on any error. A second try/except would hide the error structure.
- **VALIDATE**:
  ```bash
  python -c "
  from webapp.tools import TOOL_SCHEMAS, dispatch_tool
  import asyncio
  r = asyncio.run(dispatch_tool('query_latest', {'marketplace': 'UK'}))
  print('ok:', r['ok'], 'error:', r['error'])
  "
  ```
  EXPECT: Prints `ok: True error: None` (or `ok: True` with empty data if no UK data exists; not `ok: False`).

### Task 6: Create `webapp/llm.py` with Anthropic client + tool-use loop
- **ACTION**: Implement the manual tool-use loop per Anthropic docs
- **IMPLEMENT**:
  ```python
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

          # Append the assistant turn to history (preserve typed content blocks).
          # Convert the SDK's Pydantic objects to dicts for history consistency.
          history.append({
              "role": "assistant",
              "content": [block.model_dump() for block in resp.content],
          })

          if resp.stop_reason != "tool_use":
              # Final response — extract text and return
              final_text = "".join(
                  block.text for block in resp.content if block.type == "text"
              )
              logger.info("Chat turn complete (iterations=%d)", i + 1)
              return final_text, history

          # LLM requested tools — run them all and feed results back
          tool_results: list[dict] = []
          for block in resp.content:
              if block.type != "tool_use":
                  continue
              logger.info("LLM requested tool: %s", block.name)
              result = await dispatch_tool(block.name, dict(block.input))
              tool_results.append({
                  "type": "tool_result",
                  "tool_use_id": block.id,
                  "content": json.dumps(result, ensure_ascii=False, default=str),
              })

          # IMPORTANT: all tool results in ONE user message (for parallel tool safety)
          history.append({"role": "user", "content": tool_results})

      logger.warning("Hit max_iterations=%d in run_chat_turn", max_iterations)
      return "(Tool-use loop exceeded max iterations. Please rephrase your question.)", history
  ```
- **MIRROR**: `LOGGER_PATTERN`
- **IMPORTS**: `json`, `logging`, `typing.Any`, `anthropic.Anthropic`, `webapp.config.*`, `webapp.tools.*`
- **GOTCHA**:
  - **Parallel tool calls**: all `tool_result` blocks must be returned in a **single** user message (list of content blocks), not separate user messages. Violating this triggers Anthropic issue #2662 behavior.
  - **Tool result format**: Anthropic's `tool_result` content is a **string** (or list of content blocks). Serialize the envelope dict with `json.dumps(..., ensure_ascii=False, default=str)` — `ensure_ascii=False` preserves Chinese characters, `default=str` handles `Path`, `datetime`, and other non-JSON types that may appear in envelope `meta`.
  - **`stop_reason`**: `"end_turn"` = done, `"tool_use"` = needs tools, `"max_tokens"` = hit limit (bump `MAX_TOKENS`). Do **not** use `"tool_calls"` — that's OpenAI.
  - **max_iterations safety**: without it, a buggy LLM can loop tool calls forever. 10 is generous — Phase 1 queries should need 1–2 iterations.
  - **Pydantic → dict conversion**: `block.model_dump()` is required when appending to history because later iterations need dict-serializable content. Mixing Pydantic objects and dicts in history causes silent JSON serialization bugs.
- **VALIDATE**: Tested indirectly via Task 9 end-to-end smoke test. A unit test with a mocked `Anthropic` client is in Task 8.

### Task 7: Create `webapp/app.py` as the Chainlit entry point
- **ACTION**: Wire auth + message handler + session management
- **IMPLEMENT**:
  ```python
  """Chainlit entry point for the amz-scout internal webapp.

  Run with: chainlit run webapp/app.py -w
  """

  import logging

  import chainlit as cl

  # Import order matters: config loads .env before anything else touches env vars
  from webapp import config

  config.validate_env()  # Loud failure if required env is missing

  # These imports must come AFTER config.validate_env() to ensure env is set
  from webapp import auth  # noqa: F401 — registers the @cl.password_auth_callback
  from webapp.llm import run_chat_turn

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
              content=f"欢迎 {user.identifier}! 可以向我提问任何 Amazon 产品数据问题。"
                      f"\n\n示例: \"show me latest UK data\" 或 \"最新的英国数据\""
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
          await cl.Message(
              content=f"⚠️ Sorry, something went wrong: {e}"
          ).send()
          return

      cl.user_session.set("history", updated_history)
      await cl.Message(content=final_text).send()
  ```
- **MIRROR**: `LOGGER_PATTERN`, `ERROR_HANDLING_PATTERN` (but around Chainlit/LLM calls, NOT around `amz_scout.api`)
- **IMPORTS**: `logging`, `chainlit as cl`, `webapp.config`, `webapp.auth`, `webapp.llm.run_chat_turn`
- **GOTCHA**:
  - **Import order**: `config.validate_env()` must run before `webapp.auth` or `webapp.llm` are imported, otherwise missing env vars cause cryptic failures inside Chainlit/Anthropic init rather than a clear error message.
  - **`webapp.auth` import must be present** (even though it's `# noqa: F401` unused-looking) because importing it is what registers the `@cl.password_auth_callback`. Removing the import breaks auth silently.
  - **`logging.basicConfig` before any other log calls**: otherwise the first `logger.info(...)` silently gets Python's default WARNING-level handler and nothing shows up.
  - **Chat history grows unbounded**: Phase 1 accepts this — max_tokens will trigger before context overflow in a 10-turn session. Phase 2+ can add history truncation.
- **VALIDATE**: Task 9 end-to-end smoke test.

### Task 8: Create `tests/test_webapp_smoke.py`
- **ACTION**: Add a smoke test that validates imports + tool dispatch with a mocked LLM
- **IMPLEMENT**:
  ```python
  """Smoke tests for the webapp scaffold (Phase 1).

  These tests do NOT hit the real Anthropic API or the real Chainlit server.
  They verify import integrity, tool dispatch envelope shape, and auth
  callback behavior in isolation.
  """

  import asyncio
  import os
  import sys
  from unittest.mock import patch

  import pytest


  @pytest.mark.unit
  class TestWebappImports:
      """Verify the webapp module imports cleanly with minimal env."""

      def test_config_imports(self, monkeypatch: pytest.MonkeyPatch) -> None:
          monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
          monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "fake")
          monkeypatch.setenv("APP_PASSWORD", "fake")
          monkeypatch.setenv("KEEPA_API_KEY", "fake")
          # Force a fresh import
          for mod in list(sys.modules):
              if mod.startswith("webapp"):
                  del sys.modules[mod]
          from webapp import config
          config.validate_env()  # should not raise
          assert config.MODEL_ID == "claude-sonnet-4-6"
          assert config.ALLOWED_EMAIL_DOMAIN == "@gl-inet.com"

      def test_validate_env_raises_when_missing(
          self, monkeypatch: pytest.MonkeyPatch
      ) -> None:
          # Unset required vars
          for var in ("ANTHROPIC_API_KEY", "CHAINLIT_AUTH_SECRET", "APP_PASSWORD"):
              monkeypatch.delenv(var, raising=False)
          # Force re-import to reset the module-level env load
          for mod in list(sys.modules):
              if mod.startswith("webapp"):
                  del sys.modules[mod]
          from webapp import config
          with pytest.raises(ValueError, match="Missing required environment variables"):
              config.validate_env()


  @pytest.mark.unit
  class TestToolDispatch:
      """Verify tool dispatch returns the amz_scout.api envelope shape."""

      def test_unknown_tool_returns_envelope(self, monkeypatch: pytest.MonkeyPatch) -> None:
          monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
          monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "fake")
          monkeypatch.setenv("APP_PASSWORD", "fake")
          monkeypatch.setenv("KEEPA_API_KEY", "fake")
          for mod in list(sys.modules):
              if mod.startswith("webapp"):
                  del sys.modules[mod]
          from webapp.tools import dispatch_tool

          result = asyncio.run(dispatch_tool("nonexistent", {}))
          assert result["ok"] is False
          assert "Unknown tool" in result["error"]
          assert "data" in result
          assert "meta" in result

      def test_tool_schemas_have_cache_control_on_last(
          self, monkeypatch: pytest.MonkeyPatch
      ) -> None:
          monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
          monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "fake")
          monkeypatch.setenv("APP_PASSWORD", "fake")
          monkeypatch.setenv("KEEPA_API_KEY", "fake")
          for mod in list(sys.modules):
              if mod.startswith("webapp"):
                  del sys.modules[mod]
          from webapp.tools import TOOL_SCHEMAS

          assert len(TOOL_SCHEMAS) >= 1
          assert "cache_control" in TOOL_SCHEMAS[-1], (
              "The last tool must have cache_control for prompt caching to work"
          )
          # Phase 1 has only 1 tool — no earlier tools should have cache_control
          for tool in TOOL_SCHEMAS[:-1]:
              assert "cache_control" not in tool, (
                  "Only the LAST tool should have cache_control; it caches all preceding tools"
              )
  ```
- **MIRROR**: `TEST_STRUCTURE_PATTERN` (pytest classes, `db_in_cwd`-style fixtures where applicable, `assert r["ok"] is ...`)
- **IMPORTS**: `asyncio`, `os`, `sys`, `unittest.mock.patch`, `pytest`
- **GOTCHA**:
  - Webapp modules read env at **import time**, so every test that modifies env must **force a fresh import** by clearing `sys.modules[webapp.*]`. This is ugly but necessary — a cleaner alternative is a pytest fixture that does this setup, which Phase 2+ can add.
  - Do **not** test the `@cl.password_auth_callback` directly — Chainlit's decorator registration machinery makes unit testing messy. Test it via manual login in Task 9 instead.
- **VALIDATE**:
  ```bash
  pytest tests/test_webapp_smoke.py -v
  ```
  EXPECT: 4 tests pass (2 import tests + 2 tool dispatch tests).

### Task 9: End-to-end local smoke test
- **ACTION**: Manually run the webapp and verify the full flow
- **IMPLEMENT**: No code — this is a manual validation script:
  ```bash
  # Terminal 1: Set env + start Chainlit
  cd /Users/yuanzheyi/GL-iNet/Projects/BrowserScraper/amz-scout
  pip install -e ".[web]"
  chainlit create-secret  # copy output into .env as CHAINLIT_AUTH_SECRET
  # Edit .env to add ANTHROPIC_API_KEY, APP_PASSWORD, CHAINLIT_AUTH_SECRET
  chainlit run webapp/app.py -w
  ```
  Then in a browser:
  1. Open `http://localhost:8000`
  2. Verify login screen appears
  3. **Negative test**: try logging in with `random@example.com` / anything → expect rejection
  4. **Positive test**: log in with `jack@gl-inet.com` + the `APP_PASSWORD` from `.env`
  5. Verify welcome message appears after login
  6. Type: `show me latest UK data`
  7. Verify Chainlit UI shows an expandable `query_latest` step (blue chip/card)
  8. Expand the step — verify it shows `marketplace="UK"` in inputs and the envelope (`ok`, `data`, `error`, `meta`) in outputs
  9. Verify the final assistant message renders in Chinese or English describing the data
  10. Repeat with: `帮我看看德国最新的数据` (Chinese) → expect `query_latest(marketplace="DE")`
- **MIRROR**: n/a (manual validation)
- **IMPORTS**: n/a
- **GOTCHA**:
  - If the login screen hangs after submit, `CHAINLIT_AUTH_SECRET` is almost certainly missing or changed — the JWT cookie can't be signed.
  - If `query_latest` returns `ok: True` but empty `data`, this is **not a bug** — it means the DB has no UK competitive snapshots. Seed the DB first with: `amz-scout scrape -m UK` (requires existing browser scrape data) OR accept the empty result and verify the envelope shape only.
  - If you see `"Anthropic authentication error"`, `ANTHROPIC_API_KEY` is wrong or unset.
- **VALIDATE**: All 10 manual steps above must pass. Screenshot the passing state as Phase 1 completion evidence.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_config_imports` | env vars set | `config.MODEL_ID == "claude-sonnet-4-6"` | No |
| `test_validate_env_raises_when_missing` | env vars unset | `ValueError` with "Missing required" | Yes (happy-path failure) |
| `test_unknown_tool_returns_envelope` | `dispatch_tool("nonexistent", {})` | `{ok: False, error: "Unknown tool: nonexistent", ...}` | Yes |
| `test_tool_schemas_have_cache_control_on_last` | `TOOL_SCHEMAS` | Last tool has `cache_control`, no others do | Yes (invariant check for prompt caching) |

### Edge Cases Checklist

- [x] Empty input (`query_latest` with no data → empty `data` list, still `ok: True`)
- [x] Invalid email domain (auth rejects non-`@gl-inet.com`)
- [x] Wrong password (auth rejects)
- [x] Missing env vars (`validate_env()` raises `ValueError`)
- [x] Unknown tool name (`dispatch_tool` returns `{ok: False, ...}` instead of raising)
- [x] Non-ASCII user input (Chinese query → LLM handles; `json.dumps(ensure_ascii=False)` in tool results)
- [ ] Network failure to Anthropic (deferred: wrapped by try/except in `on_message` but not tested)
- [ ] Concurrent users (deferred to Phase 7 Alpha; Phase 1 is single-user local)
- [ ] max_iterations exceeded (safety limit exists at 10; tested manually if LLM misbehaves)

---

## Validation Commands

### Static Analysis

```bash
# Run ruff on new webapp + test files
ruff check src/ tests/ webapp/
ruff format --check webapp/ tests/test_webapp_smoke.py
```
**EXPECT**: Zero errors. If ruff complains about line length in the system prompt or docstrings, split the string or add a targeted `# noqa: E501`.

### Unit Tests

```bash
# Run just the new webapp smoke tests
pytest tests/test_webapp_smoke.py -v

# Run the full suite to confirm no regressions
pytest
```
**EXPECT**: 4 new webapp tests pass; existing test suite still passes (webapp is additive, should not affect anything).

### Install verification

```bash
# Verify the web extras install cleanly
pip install -e ".[web]"
python -c "import chainlit, anthropic, dotenv, openpyxl; print('all ok')"

# Verify the core install still works without web extras
pip install -e .   # no extras
python -c "from amz_scout.api import query_latest; print('core ok')"
```
**EXPECT**: Both install paths succeed independently.

### End-to-End Validation

```bash
# Start the dev server
chainlit run webapp/app.py -w
```
**EXPECT**: Task 9 manual flow passes all 10 steps.

### Manual Validation Checklist

- [ ] `pip install -e ".[web]"` succeeds
- [ ] `.env` has all 5 required vars (`KEEPA_API_KEY`, `ANTHROPIC_API_KEY`, `CHAINLIT_AUTH_SECRET`, `APP_PASSWORD`, `ALLOWED_EMAIL_DOMAIN`)
- [ ] `chainlit run webapp/app.py -w` starts without errors
- [ ] Login page appears at `http://localhost:8000`
- [ ] Non-`@gl-inet.com` email is rejected
- [ ] Wrong password is rejected
- [ ] Correct `@gl-inet.com` + password succeeds → welcome message appears
- [ ] Chat input accepts `show me latest UK data`
- [ ] `query_latest` step appears in UI with expandable parameters + result
- [ ] Final assistant message renders in natural language
- [ ] Chinese query `德国最新数据` also works
- [ ] No `print()` statements in any new file
- [ ] All modules use `logger = logging.getLogger(__name__)` pattern

---

## Acceptance Criteria

- [ ] All 9 tasks completed
- [ ] All validation commands pass
- [ ] 4 unit tests written and passing
- [ ] No type errors (no mypy in this project, but function signatures are fully annotated)
- [ ] No ruff errors on `webapp/` or `tests/test_webapp_smoke.py`
- [ ] Manual smoke test (Task 9) passes all 10 steps
- [ ] No modifications to files under `src/amz_scout/` — webapp is purely additive
- [ ] `.env.example` updated with the 4 new webapp env vars
- [ ] `pyproject.toml` has the new `web` extras group
- [ ] PRD Phase 1 row marked `in-progress` (done by the skill that generated this plan)

## Completion Checklist

- [ ] Code follows discovered patterns (envelope, logger, error handling, env loading)
- [ ] Error handling matches codebase style (try/except ONLY around non-`amz_scout.api` calls)
- [ ] Logging follows codebase conventions (`logging.getLogger(__name__)`, no `print()`)
- [ ] Tests follow test patterns (pytest classes + markers + envelope assertions)
- [ ] No hardcoded values (model ID, email domain, password all in config or env)
- [ ] Documentation updated (PRD Phase 1 row, plan file cross-linked)
- [ ] No unnecessary scope additions (no Phase 2-8 work snuck in)
- [ ] Self-contained — no questions remain for the implementer

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `CHAINLIT_AUTH_SECRET` missing → silent auth failure | Medium | Medium | `config.validate_env()` called at app startup with explicit error message |
| Chainlit 2.x API shift between docs and installed version | Low-Medium | Medium | Pin `chainlit>=2.7,<3`; if API drift occurs, check the installed version's `help(cl.password_auth_callback)` and adjust |
| Anthropic SDK tool-use loop has edge cases (parallel tools, max_tokens) | Medium | Low | max_iterations=10 safety limit; parallel tools returned in single user message; max_tokens=4096 is generous for Phase 1 queries |
| Sonnet 4.6 model ID changes | Low | Low | Pinned to alias `claude-sonnet-4-6`; dated ID `claude-sonnet-4-6-20260217` is a documented fallback |
| Empty DB → query_latest returns empty data → user confusion | Medium | Low | The envelope has `meta.hint = BROWSER_QUERY_HINT` when data is empty; LLM will surface this. Seed with `amz-scout scrape -m UK` before Task 9. |
| `ruff` fails on long system prompt lines | Low | Low | Split strings with parentheses; `line-length = 100` per pyproject.toml |
| Tests mutate global env and bleed between tests | Medium | Low | Every test uses `monkeypatch` and forces `sys.modules` reset; Phase 2+ can extract to a shared fixture |
| User expects Excel export in Phase 1 | Low | Low | Phase 1 scope explicitly excludes it in "NOT Building"; welcome message can note "Excel export coming in Phase 5" |

## Notes

### Why this plan does not include Excel export

The PRD marks Excel export as a Must-Have (driven by Primary User 小李's workflow) but scopes it to **Phase 5**, not Phase 1. Phase 1's scope is "end-to-end pipe working" — validating that auth + LLM + one tool can reach the user's screen with zero Excel ceremony. Including Excel export in Phase 1 would double the task count and delay the "end-to-end proven" milestone by 2-3 hours without materially de-risking anything. The `openpyxl` dep is added to `web` extras now (Task 1) so Phase 5 doesn't re-edit `pyproject.toml`.

### Why shared `APP_PASSWORD` instead of per-user bcrypt

Phase 1 optimizes for "can a user log in and get to the chat screen at all." A single shared password behind an `@gl-inet.com` email whitelist is sufficient for local dev and Jack's internal alpha. Per-user bcrypt hashes are a 1-2 hour upgrade scheduled for Phase 6 (Deployment), when production secrets handling gets hardened alongside Lightsail provisioning. Doing both at once in Phase 1 would blur the scaffold/deploy boundary.

### Why manual tool loop instead of LangChain / Chainlit auto-agent

Phase 3 market research confirmed: Chainlit has no first-class Anthropic integration (OpenAI examples dominate). The safest path is the official Anthropic tool-use loop pattern from `platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls`. LangChain adds a heavy abstraction layer for marginal benefit on a project with only ~20 tools total and a single LLM provider. The manual loop is also easier to debug — every `messages.create()` call is a plain Python function call, not buried inside a framework.

### Why `openpyxl` is in Phase 1 dependencies

Phase 5 (Excel export) is the only reason for `openpyxl`, but Phase 1 Task 1 adds it now so `pyproject.toml` isn't touched again for dependency bumps until Phase 6 (Docker). Unused dependency cost: ~2 MB disk, zero runtime overhead if unused. Trade-off accepted.

### Why `validate_env()` is in `config.py` not `app.py`

`validate_env()` must be callable from tests (see Task 8 `test_validate_env_raises_when_missing`) without importing the full app stack. Keeping it in `config.py` means tests can import it in isolation, and `app.py` just calls it once at startup.

### Open question flagged for Phase 7 Alpha

The `SYSTEM_PROMPT` in `webapp/config.py` is a first draft written by the plan author (not tested against real user queries). During Phase 7 Alpha with 小李, expect to iterate on:
- Tool-call verification phrasing ("Always show which tool you called and with what parameters")
- Chinese vs English output handling
- How the LLM describes empty data (`meta.hint`)

Track these iterations in the PRD's Open Questions Q1 (LLM translation accuracy).

---

*Generated: 2026-04-13*
*Plan authored for single-pass implementation — no further codebase searches required.*
