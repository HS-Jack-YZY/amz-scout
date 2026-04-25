# Implementation Report: Phase 3.5 — Browser Route Audit + Token Safety Gate

## Summary

Delivered two surgical units from the Phase 3.5 plan:

- **Part A**: `docs/browser-route-audit.md` — field-by-field audit of `competitive_snapshots` against Keepa coverage. Headline: **~74% coverage**, falling in the 50–80% hybrid zone. Recommendation: mark scheduled browser scraping as `deprecated-candidate`, promote Keepa to primary for periodic monitoring, keep browser as an ad-hoc deep-dive tool.
- **Part B**: Chainlit `cl.AskActionMessage` confirmation dialog for `ensure_keepa_data`. Consumes the existing `phase="needs_confirmation"` envelope protocol from `amz_scout.api` (no api layer changes) and prevents Alpha users from accidentally burning the shared 60-token Keepa budget.

Zero `amz_scout.api` changes — entire Phase 3.5 Part B is webapp-layer consumption of an existing API protocol, preserving the PRD's "webapp is a thin adapter" bet.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium (~2.5h: 30m docs + 2h wiring) | Close — ~2.5h session wall-clock including gateguard overhead |
| Confidence | High (prescriptive plan with MIRROR + GOTCHA) | Confirmed — only 1 deviation (Chainlit Action signature) caught by Pyright before shipping |
| Files Changed | 4 (1 create doc, 1 update tools.py, 1 create test, 1 update PRD) | 6 — added: 3-line bumps in existing `tests/test_webapp_smoke.py` (2 `_api_*` patch lists needed the new alias, 1 expected-set needed `ensure_keepa_data`) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Part A — Write browser route audit doc | [done] Complete | 9 H2 sections; ~74% coverage computed via two formulas bracketing the headline |
| 2 | Part B.1 — Add `ensure_keepa_data` tool schema | [done] Complete | Inserted before `register_asin_from_url` (tail) so `cache_control` marker stayed put; `confirm` intentionally not exposed |
| 3 | Part B.2 — Wire `_step_ensure_keepa_data` with confirm dialog | [done] Complete | Deviated: removed `value=` from `cl.Action` — not in Chainlit 2.x signature; Pyright caught it; decision bus stays `payload["proceed"]` |
| 4 | Tests — confirm flow coverage | [done] Complete | 4 tests pass (pass-through, confirm, cancel, timeout); used `asyncio.run()` not `@pytest.mark.asyncio` (pytest-asyncio not installed) |
| 5 | Update PRD Phase 3.5 status | [done] Complete | `pending` → `in-progress`, PRP Plan column linked to this plan |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (ruff) | [done] Pass | `webapp/tools.py` + both test files clean |
| Unit Tests (new) | [done] Pass | 4/4 in `tests/test_ensure_keepa_data_confirm.py` |
| Regression Tests | [done] Pass | 363 passed, 8 skipped, 0 failed (full `pytest tests/ -x -q`) |
| Build (import sanity) | [done] Pass | `from webapp.tools import TOOL_SCHEMAS` works; 12 tools total (was 11); tail `register_asin_from_url` still holds `cache_control` |
| Part A audit doc | [done] Pass | File exists, 9 H2 sections (plan required ≥3), Recommendation + Coverage both present |
| Browser smoke (manual) | [not run] | Auto mode — no live Chainlit UI session executed; plan lists as manual step, left for Jack to run before Alpha |

## Files Changed

| File | Action | Purpose |
|---|---|---|
| `docs/browser-route-audit.md` | CREATED | Part A deliverable — Q8 answered |
| `webapp/tools.py` | UPDATED | Schema + `_step_ensure_keepa_data` + dispatch branch + one import alias |
| `tests/test_ensure_keepa_data_confirm.py` | CREATED | 4 tests for confirm-flow branches |
| `tests/test_webapp_smoke.py` | UPDATED | Added `ensure_keepa_data` to 1 expected set + 2 `_api_*` patch lists |
| `.claude/PRPs/prds/internal-amz-scout-web.prd.md` | UPDATED | Phase 3.5 row `pending` → `in-progress` + link to plan |

> Per-file line counts intentionally omitted; use `git diff --stat` for the authoritative numbers (which drift as the branch is updated).

## Deviations from Plan

1. **`cl.Action(value=...)` removed** — Plan (following outdated Chainlit 2.x docs) called for `name`/`value`/`label`/`payload`. Actual installed `chainlit/action.py` source shows the dataclass signature is `(name, payload, label="", tooltip="", icon=None, forId=None, id=...)`. Pyright flagged the extra `value` kwarg. Removed; decision bus remains `response["payload"]["proceed"]` per plan ASK_ACTION_MESSAGE_PATTERN rule #3, so the fix is semantically identical.

2. **Test convention `asyncio.run()` vs `@pytest.mark.asyncio`** — Plan's TEST_STRUCTURE section prescribed `@pytest.mark.asyncio`, but the codebase (`tests/test_webapp_smoke.py`) uses `asyncio.run()` directly. `pytest-asyncio` is not installed, so that marker would require an async pytest plugin to actually run the tests. Followed the codebase convention instead (which is the stronger rule per STEP_WRAPPER_PATTERN "Mirror patterns exactly").

3. **Extra edits in `tests/test_webapp_smoke.py`** — Plan's Risks table predicted "schema count assertion in existing tests may break" as Medium likelihood. It did: `test_all_expected_tools_present` had a hardcoded `expected` set, and `test_dispatcher_routes_all_known_tools` + `test_every_step_wrapper_uses_asyncio_to_thread` each had an `_api_*` patch tuple. 3 single-line additions to unblock regression.

## Issues Encountered

1. **`@cl.step` decorator requires Chainlit context at runtime** — Attempting `asyncio.run(_step_ensure_keepa_data(...))` in tests raised `ChainlitContextException` because the real decorator dereferences `context.session.thread_id`. Solution: follow existing `test_webapp_smoke.py` pattern — `monkeypatch.setattr(cl, "step", _noop_step)` plus `_reset_webapp_modules()` to force a fresh re-import so the no-op decorator is what wraps `_step_ensure_keepa_data`. All 4 tests green after this.

2. **PyPI download timeouts blocked `uv sync --extra web`** — `chainlit-2.11.1.whl` and `httpcore-1.0.9.whl` both timed out fetching. Worked around by using the existing `/opt/miniconda3` Python env which already had Chainlit/pytest/anthropic installed. Test runs + schema sanity used `/opt/miniconda3/bin/pytest` and `/opt/miniconda3/bin/python3` respectively. Did NOT modify `pyproject.toml` or retry the sync — `uv.lock` was already present untracked, so the repo's declared deps are unchanged.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_ensure_keepa_data_confirm.py` | 4 | 4 branches of `_step_ensure_keepa_data`: (1) pass-through when no `needs_confirmation` phase, (2) user confirms → second api call with `confirm=True`, (3) user cancels → cancelled envelope, (4) dialog timeout/None → same as cancel |

## Next Steps

- [ ] Manual smoke via `chainlit run webapp/app.py -w` — ask "刷新所有产品的 Keepa 数据"; verify dialog renders, Cancel + Confirm both work. Plan lists this as Jack's pre-Alpha validation.
- [ ] Code review via `/code-review` before merge
- [ ] Create PR via `/prp-pr`
- [ ] After merge: if the audit's "deprecate browser route" recommendation is accepted, open a follow-up plan `phase-X-browser-route-deprecation.plan.md` to remove scheduled invocations
- [ ] Flag `webapp/tools.py:211` `query_deals` schema description drift (claims ≥6-token batches surface `needs_confirmation`, but `_auto_fetch` path swallows it silently) — 1-line docstring edit, explicitly out of Phase 3.5 scope per council B−
