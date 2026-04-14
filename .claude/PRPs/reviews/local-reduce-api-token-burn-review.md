# Local Review: Reduce Claude Token Burn

**Reviewed**: 2026-04-14
**Branch**: `feat/reduce-api-token-burn` (uncommitted)
**Scope**: 5 modified + 5 new files (4 code/test, 1 report)
**Decision**: **APPROVE with comments**

## Summary

Two coherent, well-scoped token-saving levers shipped together: (a) private
`_llm_trim` module that produces allow-listed copies of query result rows
before they hit the LLM, and (b) a *moving* `cache_control` breakpoint in
`webapp/llm.py` that correctly strips prior markers to stay within Anthropic's
4-block-per-request limit. End-to-end validation against the live Anthropic
API passed 5 consecutive tool-use turns (previously crashed at turn 3) with
measured cache reads of 2,462 to 9,149 tokens across turns. Full test suite
green (248 passed, 5 skipped as designed).

## Findings

### CRITICAL

None.

### HIGH

None.

### MEDIUM

**M1. `run_chat_turn` exceeds 50-line function guideline**
- File: `webapp/llm.py:27-101` — now 75 lines (was ~58 pre-PR)
- Rule: `~/.claude/rules/common/coding-style.md` — "Functions <50 lines"
- Cause: the new cache-control scrubbing loop (lines 83-93) added ~15 lines
  inside an already-long tool-use dispatch loop.
- Suggested fix: extract a small helper
  ```python
  def _strip_cache_control_from_prior_tool_results(history: list[dict]) -> None:
      for msg in history:
          if msg.get("role") == "user" and isinstance(msg.get("content"), list):
              for block in msg["content"]:
                  if isinstance(block, dict) and block.get("type") == "tool_result":
                      block.pop("cache_control", None)
  ```
  and replace the inline double-for with a one-line call.
- Severity rationale: pre-existing structure + logic is tested end-to-end
  and working. Not a blocker; worth cleaning up in a follow-up if the file
  grows further.

**M2. Pyright `reportArgumentType` warnings on Anthropic SDK call at `webapp/llm.py:42-44`**
- Pre-existing (not introduced by this PR). `list[dict]` vs. the SDK's
  stricter `Iterable[TextBlockParam]` / `Iterable[ToolUnionParam]` /
  `Iterable[MessageParam]` types.
- Runtime behavior is correct — the SDK accepts plain dicts and serialises
  them to the wire format.
- Suggested fix (low priority): type the `history` list as
  `list[MessageParam]` and `SYSTEM_BLOCKS` as `list[TextBlockParam]`,
  or add `# type: ignore[arg-type]` comments on the three offending lines
  with a brief explanation. Not worth blocking this PR.

**M3. `test_token_audit.py` — `monkeypatch.chdir(real_db.parent.parent)` in every test**
- File: `tests/test_token_audit.py:108, 127, 145, 164, 188, 233, 269`
- Repeated `chdir` setup in 7 tests. A module-scoped `autouse` fixture
  would centralize this.
- Not a blocker; fine to leave for the first iteration of the harness.

### LOW

**L1. `print()` calls in `tests/test_token_audit.py`**
- The harness uses `print()` to surface audit progress. Project-wide
  convention elsewhere is `logger = logging.getLogger(__name__)`.
- Justification: this is a pytest harness whose output is meant to be
  captured by pytest's stdout redirection, not a production code path.
  `print()` is fine here.

**L2. Unused `_kwargs` / `_name` / `_args` parameters in test stubs (Pyright ★ hints)**
- Files: `tests/test_webapp_smoke.py:119, 131, 264, 269, 324, 353, 358, 439`
- Already prefixed with underscore per Python convention. Pyright's "unused
  parameter" hint (star severity) is informational only — no fix needed.

**L3. `tests/test_webapp_smoke.py` — 455 lines total after the edit**
- Approaching the 800-line soft cap. Not over. If another TestCacheControl
  test class is added, consider splitting cache-control tests into their
  own `tests/test_llm_cache_control.py`.

## Security Review

- [x] No hardcoded credentials — scanned via regex; `.env` values come from
      `os.environ` / `python-dotenv` only.
- [x] No SQL injection — new `_llm_trim.py` does no DB access. Test
      harness uses parameterised `conn.execute(..., params)` throughout.
- [x] No path traversal — no file paths derived from LLM input.
- [x] No XSS vectors — trimmed envelopes are JSON-serialised with
      `json.dumps(..., ensure_ascii=False, default=str)`; receiver is
      Anthropic, not an HTML renderer.
- [x] No arbitrary code execution paths; no use of `eval`/`exec` or
      unsafe deserialisation libraries anywhere in the new code.
- [x] `.env` contents (including `ANTHROPIC_API_KEY`) were neither committed
      nor referenced in any changed file.

## Pattern Compliance

- [x] Immutable-transform convention: `_llm_trim.trim` follows `api._add_dates`
      exactly — list comprehension returning new dicts, zero mutation.
- [x] Envelope shape `{ok, data, error, meta}` untouched. `meta` never trimmed.
- [x] Test patterns: `_reset_webapp_modules` + `_set_fake_env` used
      consistently in new `TestCacheControlWiring` class.
- [x] Pytest markers: new `network` marker registered in
      `pyproject.toml:[tool.pytest.ini_options].markers` (fixes "unknown
      marker" warning that would otherwise appear).
- [x] Underscore-prefixed private module (`_llm_trim.py`) — signals
      CLI must not import from it; convention enforced by the module
      docstring.

## Validation Results

| Check | Result |
|---|---|
| `ruff check src/ tests/ webapp/` | **Pass** (0 issues) |
| `ruff format --check src/ tests/ webapp/` | **Pass** (40 files) |
| `pytest` fast-path (127 tests) | **Pass** (122 passed, 5 skipped as expected) |
| Full `pytest --ignore=tests/test_webapp_deployment_smoke.py` | **Pass** (248 passed, 5 skipped) |
| Live e2e 5-turn Anthropic API run | **Pass** (all 5 turns, max cache_control=1) |
| Pyright (IDE diagnostics) | Pre-existing SDK type-stub mismatches only; no new issues |

## Files Reviewed

| File | Action | Lines | Notes |
|---|---|---|---|
| `src/amz_scout/_llm_trim.py` | Added | 88 | Clean; pure functions; strong docstring rationale for each dropped field |
| `src/amz_scout/api.py` | Modified | +13/-0 | Single import block + 7 one-line insertions, no signature changes |
| `webapp/llm.py` | Modified | +17/-0 | Cache_control strip+move logic; M1 function-length nit |
| `tests/test_llm_trim.py` | Added | 220 | 15 tests covering allow-list, immutability, empty, missing keys, unicode, 730-row linear |
| `tests/test_token_audit.py` | Added | 310 | 8 network-gated tests + 1 synthetic fallback; produces `output/token_audit.json` |
| `tests/test_webapp_smoke.py` | Modified | +247/-0 | New `TestCacheControlWiring` class with 3 tests including the multi-turn regression guard |
| `pyproject.toml` | Modified | +1/-0 | `network` marker registration |
| `CLAUDE.md` | Modified | +7/-0 | Bullet #15 documenting trim contract for future sessions |
| `.claude/PRPs/reports/token-burn-reduction-report.md` | Added | 168 | Implementation report with real before/after numbers |
| `.claude/PRPs/plans/completed/reduce-api-token-burn.plan.md` | Moved | — | Archived from `plans/` |

## Regression Coverage

The critical regression was a multi-turn state accumulation bug. The original
`test_last_tool_result_block_gets_cache_control` only verified single-turn
wiring — it would NOT have caught the bug. The new
`test_cache_control_does_not_accumulate_across_turns` test explicitly drives
5 sequential `run_chat_turn` calls and asserts `len(marked_blocks) == 1`,
closing that gap definitively. Verified by stashing the fix — the new test
fails; restoring the fix — the new test passes.

## Recommended Follow-ups

1. **M1**: Extract `_strip_cache_control_from_prior_tool_results` helper in a
   small follow-up PR to bring `run_chat_turn` back under 60 lines.
2. **M2**: Add `# type: ignore[arg-type]` comments or proper typing on the
   Anthropic SDK call site to quiet pre-existing Pyright noise.
3. **Live webapp cache measurement**: the deployed Chainlit app now has
   `logger.info("usage: %s", ...)` on every turn. After the next deploy, tail
   those logs for a real 3+ turn session and append them to
   `.claude/PRPs/reports/token-burn-reduction-report.md` under "End-to-end
   turn example".
