# Implementation Report: Phase 1 — amz-scout Webapp Scaffolding

## Summary

Implemented a minimal Chainlit 2.x webapp under a new `webapp/` module that imports
`amz_scout.api` as a library, handles email-domain-whitelisted password auth, binds
Claude Sonnet 4.6 via the Anthropic Python SDK with function calling, and wires
`query_latest` end-to-end. Zero modifications to `src/amz_scout/` — webapp is purely
additive.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Estimated Files | 7 new + 2 modified | 7 new + 2 modified |
| Estimated Lines | ~300 | ~310 |
| Estimated Time | ~9 hours | ~45 min (scaffolding only; manual Task 9 deferred) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Add `web` extras to `pyproject.toml` | Complete | Also added `webapp` to hatch wheel packages |
| 2 | Extend `.env.example` with 4 webapp vars | Complete | |
| 3 | Create `webapp/__init__.py` + `webapp/config.py` | Complete | |
| 4 | Create `webapp/auth.py` password callback | Complete | |
| 5 | Create `webapp/tools.py` query_latest wrapper | Complete | See deviation below |
| 6 | Create `webapp/llm.py` tool-use loop | Complete | |
| 7 | Create `webapp/app.py` Chainlit entry point | Complete | |
| 8 | Create `tests/test_webapp_smoke.py` | Complete | 4 tests |
| 9 | End-to-end browser smoke test | Deferred | Manual step — requires real API keys + running Chainlit server |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff check) | Pass | `webapp/` + `tests/test_webapp_smoke.py` clean. Pre-existing 52 errors in `src/`/`tests/` are out of scope for this plan. |
| Static Analysis (ruff format) | Pass | Auto-formatted on first run |
| Unit Tests (webapp smoke) | Pass | 4/4 pass |
| Full Test Suite | Pass | 224/224 pass — zero regressions |
| Install Check | Pass | `pip install -e ".[web]"` succeeds; `from amz_scout.api import query_latest` still works |
| Webapp Package Import | Pass | `from webapp import config` resolves `DB_PATH` + `ALLOWED_EMAIL_DOMAIN` correctly |
| Integration (browser) | Deferred | Task 9 — manual, run `chainlit run webapp/app.py -w` + browser login |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `pyproject.toml` | UPDATE | +8 / -1 |
| `.env.example` | UPDATE | +10 / 0 |
| `webapp/__init__.py` | CREATE | +8 |
| `webapp/config.py` | CREATE | +51 |
| `webapp/auth.py` | CREATE | +44 |
| `webapp/tools.py` | CREATE | +78 |
| `webapp/llm.py` | CREATE | +82 |
| `webapp/app.py` | CREATE | +54 |
| `tests/test_webapp_smoke.py` | CREATE | +84 |

Total: 8 new files, 2 updated files.

## Deviations from Plan

### D1 — Added `webapp` to hatch wheel packages (pyproject.toml change)

**What**: Modified `[tool.hatch.build.targets.wheel] packages` from
`["src/amz_scout"]` to `["src/amz_scout", "webapp"]`.

**Why**: The plan did not specify how to make `webapp/` importable to pytest.
Without this, `from webapp import config` fails at test collection because the
repo root isn't on `sys.path`. Adding `webapp` to the hatch wheel ensures the
editable install puts it on the path. This is safe for users who install without
`[web]` extras — `webapp/__init__.py` has no runtime imports, and the chainlit/
anthropic imports only happen in leaf modules that are only loaded when
`chainlit run webapp/app.py` is executed.

### D2 — Task 5 standalone CLI validation command does not work

**What**: The plan's Task 5 VALIDATE command runs
`asyncio.run(dispatch_tool('query_latest', {'marketplace': 'UK'}))` from a
standalone `python -c` shell. This raises `ChainlitContextException: Chainlit
context not found`.

**Why**: `@cl.step(type="tool")` is a UI decorator that requires an active
Chainlit session context at call time. Outside a Chainlit runtime (e.g., in a
standalone Python script or a pytest unit test that hits the real path), the
step wrapper blows up before reaching `_api_query_latest`.

**Resolution**: The `test_unknown_tool_returns_envelope` unit test still
validates the envelope shape via the error path (unknown tool), and the
`cache_control` invariant test covers the tool schema. The real `query_latest`
path will run inside Chainlit's context during Task 9 browser smoke test.

**Recommendation for Phase 2**: Extract a pure `tools_core.py` layer that
`tools.py` thin-delegates to via `@cl.step`. This will make the tool dispatch
unit-testable without a Chainlit runtime and allow pre-browser CI validation of
the envelope round-trip.

## Issues Encountered

None beyond the documented deviations. The editable install produced the
expected Pyright "import cannot be resolved" warnings for chainlit/anthropic/
webapp during file creation — all resolved after `pip install -e ".[web]"`.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_webapp_smoke.py` | 4 | Config import & env validation (2), tool dispatch envelope shape (2) |

Tests intentionally avoid hitting the real Anthropic API or Chainlit server.
They verify:
1. `webapp.config` imports and `validate_env()` passes with fake env vars set
2. `validate_env()` raises `ValueError` when required vars are missing
3. `dispatch_tool("nonexistent", {})` returns `{ok: False, error: "Unknown tool: ...", data: [], meta: {}}`
4. `TOOL_SCHEMAS` has `cache_control` on the LAST tool only (prompt-caching invariant)

## Manual Validation Checklist (Task 9 — deferred to user)

Before declaring Phase 1 complete, Jack should run:

- [ ] `.env` contains `ANTHROPIC_API_KEY`, `CHAINLIT_AUTH_SECRET` (from `chainlit create-secret`), `APP_PASSWORD`, `KEEPA_API_KEY`
- [ ] `chainlit run webapp/app.py -w`
- [ ] Open `http://localhost:8000` — login page appears
- [ ] Reject path: `random@example.com` / anything → rejected
- [ ] Reject path: `jack@gl-inet.com` / wrong password → rejected
- [ ] Happy path: `jack@gl-inet.com` + `APP_PASSWORD` → welcome message appears
- [ ] Query: `show me latest UK data` → `query_latest` step visible in UI with expandable inputs/outputs
- [ ] Envelope shape in step output: `ok`, `data`, `error`, `meta`
- [ ] Chinese query: `帮我看看德国最新的数据` → `query_latest(marketplace="DE")`

## Next Steps

- [ ] Run `/ecc:code-review` to review changes before committing
- [ ] Manual browser smoke test (Task 9) with real API keys
- [ ] `/ecc:prp-commit` when Task 9 passes
- [ ] `/ecc:prp-pr` to open a PR for Phase 1
- [ ] Update PRD Phase 1 row from `in-progress` to `complete` after browser test passes
- [ ] Plan Phase 2 with `/ecc:prp-plan` once Phase 1 is merged
