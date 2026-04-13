# Local Review: Phase 1 Webapp Scaffolding

**Reviewed**: 2026-04-13
**Mode**: Local (uncommitted changes)
**Scope**: webapp/ module + tests + pyproject.toml + .env.example + auto-generated .chainlit/
**Decision**: APPROVE WITH FIXES — 1 HIGH already fixed in-review; 2 MEDIUM + 2 LOW remaining

## Summary

Implementation is solid and matches the plan. Found and fixed one real test-isolation
bug during the review (`test_validate_env_raises_when_missing` was passing in isolation
but breaking when `.env` had real values — dotenv's default `override=False` combined
with the forced module re-import repopulated the monkeypatched-away vars). Remaining
issues are Phase-6 deployment hygiene items and gitignore tweaks — nothing blocking
for the local scaffold.

---

## Findings

### CRITICAL
None.

### HIGH

**H1. `test_validate_env_raises_when_missing` broken by populated `.env`** — FIXED IN REVIEW
- **File**: `tests/test_webapp_smoke.py:41-48`
- **Issue**: `monkeypatch.delenv(var)` removes the var from `os.environ`, then
  `_reset_webapp_modules()` re-imports `webapp.config` which runs `load_dotenv()`.
  Since dotenv's default is `override=False`, it repopulates the deleted vars from
  the real `.env` file on disk. Result: if `.env` has real values for
  `ANTHROPIC_API_KEY`/`CHAINLIT_AUTH_SECRET`/`APP_PASSWORD`, the test fails because
  `validate_env()` sees them set and does not raise.
- **Detection**: Test passed when run right after file creation (.env had only
  `KEEPA_API_KEY`), then failed on the second run after the reviewer populated `.env`
  with real webapp values. Real test-isolation bug, not a flake.
- **Fix applied**: Changed `monkeypatch.delenv(...)` to `monkeypatch.setenv(var, "")`
  for all 4 required vars (including `KEEPA_API_KEY`). Empty string means dotenv's
  `override=False` won't replace it, and `validate_env()`'s
  `if not os.environ.get(k)` treats empty string as missing.
- **Verified**: `pytest tests/test_webapp_smoke.py -v` — 4/4 pass.

### MEDIUM

**M1. `.chainlit/config.toml` sets `allow_origins = ["*"]`**
- **File**: `.chainlit/config.toml:23`
- **Issue**: The auto-generated Chainlit config allows all origins, meaning any
  website can iframe or POST to the Chainlit server. Fine for local dev, but a
  CSRF/clickjacking risk once deployed to Lightsail (Phase 6).
- **Severity**: MEDIUM — deferred risk, not live yet.
- **Recommended fix before Phase 6 deployment**: Change to an explicit origin list:
  ```toml
  allow_origins = ["https://amz-scout.internal.gl-inet.com"]
  ```
- **Action**: Flag in the Phase 6 deployment plan, do not block Phase 1.

**M2. `.gitignore` does not exclude Chainlit runtime dirs**
- **File**: `.gitignore`
- **Issue**: Chainlit creates `.files/` (user uploads) and potentially `.chainlit/.files/`
  at runtime. These are not in `.gitignore`, so future uploads (Phase 5 Excel export or
  user uploads) could be accidentally committed.
- **Severity**: MEDIUM — preventive hygiene.
- **Recommended fix**:
  ```
  # Chainlit runtime
  .files/
  .chainlit/.files/
  ```
- **Action**: Add before the first commit that includes `.chainlit/`.

### LOW

**L1. Email whitelist assumes `@` prefix in `ALLOWED_EMAIL_DOMAIN`**
- **File**: `webapp/auth.py:21` + `webapp/config.py:33`
- **Issue**: The `email.endswith(ALLOWED_EMAIL_DOMAIN)` check is only safe if
  `ALLOWED_EMAIL_DOMAIN` starts with `@`. The default `@gl-inet.com` is correct, but
  if someone edits `.env` and writes `ALLOWED_EMAIL_DOMAIN=gl-inet.com` (no `@`),
  then `attacker@evilgl-inet.com` passes the whitelist.
- **Severity**: LOW — config-level footgun, default is safe, mitigated by internal-only
  access in Phase 1.
- **Recommended fix**: Validate at startup in `config.py`:
  ```python
  if not ALLOWED_EMAIL_DOMAIN.startswith("@"):
      raise ValueError(
          f"ALLOWED_EMAIL_DOMAIN must start with '@' (got {ALLOWED_EMAIL_DOMAIN!r})"
      )
  ```
- **Action**: Phase 2 nice-to-have, not blocking.

**L2. `chainlit.md` welcome file is default Chainlit boilerplate**
- **File**: `chainlit.md`
- **Issue**: Auto-generated welcome markdown shows Chainlit's default "Welcome to
  Chainlit" content instead of amz-scout-specific copy. Users will see a Chainlit
  promo screen on the landing page before login.
- **Severity**: LOW — cosmetic, pre-login page.
- **Recommended fix**: Replace with a short GL.iNet / amz-scout welcome, or leave
  empty to hide the screen entirely.
- **Action**: Phase 2 when iterating on UX.

---

## Validation Results

| Check | Result |
|---|---|
| Ruff lint (webapp/ + test) | PASS |
| Ruff format (webapp/ + test) | PASS (7 files formatted) |
| Webapp smoke tests | PASS (4/4 after H1 fix) |
| Full test suite regression | PASS (224/224 earlier; 4 webapp tests re-verified after fix) |
| Type check | N/A (no mypy in project) |
| Build | PASS (`pip install -e ".[web]"`) |

---

## Files Reviewed

| File | Change | Notes |
|---|---|---|
| `pyproject.toml` | Modified | +8/-1. `web` optional extras + `webapp` in hatch packages. Clean. |
| `.env.example` | Modified | +10. Placeholder values only, no real secrets. Clean. |
| `webapp/__init__.py` | Added | 7 lines. Clean. |
| `webapp/config.py` | Added | 51 lines. Loads .env, defines model ID, DB path (absolute), validate_env(). Clean. |
| `webapp/auth.py` | Added | 41 lines. See L1. Otherwise clean. |
| `webapp/tools.py` | Added | 79 lines. Envelope pattern preserved, no second try/except. Clean. |
| `webapp/llm.py` | Added | 84 lines. Manual tool loop, prompt caching, max_iterations safety. Clean. |
| `webapp/app.py` | Added | 55 lines. Correct import order (validate_env before auth/llm imports). Clean. |
| `tests/test_webapp_smoke.py` | Added | 79 lines. Test isolation bug fixed in review (H1). |
| `.chainlit/config.toml` | Auto-generated | See M1 (allow_origins=*). |
| `.chainlit/translations/*.json` | Auto-generated | ~5900 lines across 24 language files. Standard Chainlit bundle, safe to commit. |
| `chainlit.md` | Auto-generated | See L2 (default Chainlit welcome). |
| `.gitignore` | Not modified | See M2 (missing .files/). |

---

## Decision

**APPROVE WITH FIXES** — for the local scaffold and browser smoke test (Task 9),
the code is production-ready for internal dev use. The one HIGH issue (test isolation)
has been fixed in-review and all tests pass again.

### Before committing

1. Add `.files/` and `.chainlit/.files/` to `.gitignore` (M2).
2. Decide whether to commit `.chainlit/` + `chainlit.md` or gitignore them — standard
   Chainlit practice is to commit the config so other devs get the same settings, but
   customize `chainlit.md` or leave it empty (L2).

### Before Phase 6 deployment

1. Tighten `allow_origins` in `.chainlit/config.toml` (M1).
2. Add `ALLOWED_EMAIL_DOMAIN` prefix validation in `config.py` (L1).
3. Replace shared `APP_PASSWORD` with per-user bcrypt (already in the plan for Phase 6).
