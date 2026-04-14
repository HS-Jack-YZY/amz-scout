"""Smoke tests for the webapp scaffold (Phase 1).

These tests do NOT hit the real Anthropic API or the real Chainlit server.
They verify import integrity, tool dispatch envelope shape, and auth
callback behavior in isolation.
"""

import asyncio
import sys

import pytest


def _reset_webapp_modules() -> None:
    """Clear any cached webapp.* imports so env changes take effect."""
    for mod in list(sys.modules):
        if mod.startswith("webapp"):
            del sys.modules[mod]


def _set_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("CHAINLIT_AUTH_SECRET", "fake")
    monkeypatch.setenv("APP_PASSWORD", "fake")
    monkeypatch.setenv("KEEPA_API_KEY", "fake")


@pytest.mark.unit
class TestWebappImports:
    """Verify the webapp module imports cleanly with minimal env."""

    def test_config_imports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp import config

        config.validate_env()  # should not raise
        assert config.MODEL_ID == "claude-sonnet-4-6"
        assert config.ALLOWED_EMAIL_DOMAIN == "@gl-inet.com"

    def test_validate_env_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Set to empty string (not delete) so dotenv's default override=False
        # won't repopulate from an existing .env file when webapp.config re-imports.
        for var in ("ANTHROPIC_API_KEY", "CHAINLIT_AUTH_SECRET", "APP_PASSWORD", "KEEPA_API_KEY"):
            monkeypatch.setenv(var, "")
        _reset_webapp_modules()
        from webapp import config

        with pytest.raises(ValueError, match="Missing required environment variables"):
            config.validate_env()


@pytest.mark.unit
class TestToolDispatch:
    """Verify tool dispatch returns the amz_scout.api envelope shape."""

    def test_unknown_tool_returns_envelope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp.tools import dispatch_tool

        result = asyncio.run(dispatch_tool("nonexistent", {}))
        assert result["ok"] is False
        assert "Unknown tool" in result["error"]
        assert "data" in result
        assert "meta" in result

    def test_tool_schemas_have_cache_control_on_last(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp.tools import TOOL_SCHEMAS

        assert len(TOOL_SCHEMAS) >= 1
        assert "cache_control" in TOOL_SCHEMAS[-1], (
            "The last tool must have cache_control for prompt caching to work"
        )
        # Only the last tool should carry cache_control; earlier tools must not.
        for tool in TOOL_SCHEMAS[:-1]:
            assert "cache_control" not in tool, (
                "Only the LAST tool should have cache_control; it caches all preceding tools"
            )

    def test_all_phase2_tool_names_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp.tools import TOOL_SCHEMAS

        names = {tool["name"] for tool in TOOL_SCHEMAS}
        expected = {
            "query_latest",
            "check_freshness",
            "keepa_budget",
            "query_availability",
            "query_compare",
            "query_deals",
            "query_ranking",
            "query_sellers",
            "query_trends",
        }
        assert names == expected, f"Missing or extra tools: {names ^ expected}"

    def test_dispatcher_routes_all_known_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every declared tool must route through dispatch_tool without KeyError/AttributeError.

        This test exercises ONLY the dispatcher — it does not want to hit the real
        amz_scout.api (which would touch the SQLite registry and, for query_trends
        / query_sellers / query_deals, attempt a real Keepa network call). So we:

        1. Patch `cl.step` to a no-op decorator BEFORE importing webapp.tools, since
           the real decorator requires an active Chainlit session context.
        2. Patch every `_api_*` alias on the freshly-imported webapp.tools module to
           a fake that returns a well-formed envelope dict. This bypasses the real
           API entirely and asserts only dispatcher routing + envelope shape.
        """
        _set_fake_env(monkeypatch)

        import chainlit as cl

        def _noop_step(**_kwargs):  # type: ignore[no-untyped-def]
            def _decorator(fn):  # type: ignore[no-untyped-def]
                return fn

            return _decorator

        monkeypatch.setattr(cl, "step", _noop_step)

        _reset_webapp_modules()
        from webapp import tools as webapp_tools
        from webapp.tools import TOOL_SCHEMAS, dispatch_tool

        def _fake_envelope(*_args, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
            return {"ok": True, "data": [], "error": None, "meta": {"stub": True}}

        for attr in (
            "_api_check_freshness",
            "_api_keepa_budget",
            "_api_query_availability",
            "_api_query_compare",
            "_api_query_deals",
            "_api_query_latest",
            "_api_query_ranking",
            "_api_query_sellers",
            "_api_query_trends",
        ):
            monkeypatch.setattr(webapp_tools, attr, _fake_envelope)

        async def _run_all() -> list[tuple[str, dict]]:
            """Run every schema's dispatch in a single event loop.

            Collapsing to one `asyncio.run` (vs one per tool) keeps the test cheap
            and avoids masking event-loop-scoped bugs in future real wrappers.
            """
            out: list[tuple[str, dict]] = []
            for tool in TOOL_SCHEMAS:
                name = tool["name"]
                # Build minimal args dict satisfying required fields with safe placeholders
                args: dict = {}
                for prop in tool["input_schema"].get("required", []):
                    args[prop] = "UK" if prop == "marketplace" else "Slate 7"
                out.append((name, await dispatch_tool(name, args)))
            return out

        for name, result in asyncio.run(_run_all()):
            assert isinstance(result, dict), f"{name}: not a dict"
            assert "ok" in result, f"{name}: missing 'ok' key"
            assert "data" in result, f"{name}: missing 'data' key"
            assert "error" in result, f"{name}: missing 'error' key"
            assert "meta" in result, f"{name}: missing 'meta' key"

    def test_dispatcher_returns_error_envelope_when_required_field_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tools with required schema fields must return a clear validation envelope
        when the LLM drops the field, rather than passing an empty string to the API
        and getting a cryptic downstream resolution error.

        No Chainlit-context monkeypatching needed: the null-check returns BEFORE the
        `@cl.step`-decorated wrapper is ever called.
        """
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp.tools import dispatch_tool

        tools_with_required = [
            ("query_latest", "marketplace"),
            ("query_compare", "product"),
            ("query_ranking", "marketplace"),
            ("query_sellers", "product"),
            ("query_trends", "product"),
        ]

        async def _run_all() -> list[tuple[str, str, dict]]:
            out: list[tuple[str, str, dict]] = []
            for tool_name, missing_field in tools_with_required:
                out.append((tool_name, missing_field, await dispatch_tool(tool_name, {})))
            return out

        for tool_name, missing_field, result in asyncio.run(_run_all()):
            assert result["ok"] is False, f"{tool_name}: expected ok=False, got {result}"
            assert missing_field in result["error"], (
                f"{tool_name}: error should mention '{missing_field}', got: {result['error']}"
            )
            assert "required" in result["error"], (
                f"{tool_name}: error should say 'required', got: {result['error']}"
            )
            assert result["data"] == []
            assert result["meta"] == {}


@pytest.mark.unit
class TestWebappTrimBoundary:
    """Webapp boundary contract: ``dispatch_tool`` must apply ``trim_for_llm``
    to the envelope ``data`` for every row-emitting tool, while ``meta`` and
    failure envelopes pass through untouched.

    This is the regression guard for PR #7's correction: the trim helpers live
    in ``amz_scout._llm_trim`` and used to be wired inside ``amz_scout.api``,
    which silently bled into CLI output. Trimming must now happen at the
    webapp boundary only — these tests fail loudly if anyone moves it back.
    """

    def _patch_chainlit_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import chainlit as cl

        def _noop_step(**_kwargs):  # type: ignore[no-untyped-def]
            def _decorator(fn):  # type: ignore[no-untyped-def]
                return fn

            return _decorator

        monkeypatch.setattr(cl, "step", _noop_step)

    def test_query_latest_envelope_is_trimmed_at_webapp_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_fake_env(monkeypatch)
        self._patch_chainlit_step(monkeypatch)
        _reset_webapp_modules()
        from webapp import tools as webapp_tools
        from webapp.tools import dispatch_tool

        wide_row = {
            "id": 7,
            "site": "UK",
            "brand": "ExampleBrand",
            "model": "XR-100",
            "asin": "B0TESTTEST",
            "title": "Should not leak",
            "url": "https://example.test",
            "price_cents": 14999,
            "currency": "GBP",
            "rating": 4.5,
            "review_count": 99,
            "bsr": 1,
            "available": 1,
            "fulfillment": "Amazon",
            "sold_by": "ExampleBrand",
            "scraped_at": "2026-04-01T10:00:00Z",
        }

        def _fake(*_args, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
            return {
                "ok": True,
                "data": [wide_row],
                "error": None,
                "meta": {"count": 1, "auto_fetched": False},
            }

        monkeypatch.setattr(webapp_tools, "_api_query_latest", _fake)

        result = asyncio.run(dispatch_tool("query_latest", {"marketplace": "UK"}))

        assert result["ok"] is True
        assert len(result["data"]) == 1
        trimmed = result["data"][0]
        assert trimmed["brand"] == "ExampleBrand"
        assert trimmed["model"] == "XR-100"
        assert trimmed["asin"] == "B0TESTTEST"
        assert trimmed["price_cents"] == 14999
        for leaked in ("id", "title", "url", "fulfillment", "sold_by"):
            assert leaked not in trimmed, (
                f"{leaked!r} leaked into LLM envelope — trim is no longer "
                f"applied at the webapp boundary"
            )
        assert result["meta"] == {"count": 1, "auto_fetched": False}

    def test_query_trends_timeseries_is_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_fake_env(monkeypatch)
        self._patch_chainlit_step(monkeypatch)
        _reset_webapp_modules()
        from webapp import tools as webapp_tools
        from webapp.tools import dispatch_tool

        wide_rows = [
            {
                "keepa_ts": 7584000 + i,
                "value": 14999 + i,
                "fetched_at": "2026-04-01T10:00:00Z",
                "date": f"2026-04-{i + 1:02d} 10:00",
            }
            for i in range(3)
        ]

        def _fake(*_args, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
            return {
                "ok": True,
                "data": wide_rows,
                "error": None,
                "meta": {"asin": "B0TEST", "series_name": "amazon_new"},
            }

        monkeypatch.setattr(webapp_tools, "_api_query_trends", _fake)

        result = asyncio.run(
            dispatch_tool(
                "query_trends",
                {"product": "Slate 7", "marketplace": "UK"},
            )
        )

        assert result["ok"] is True
        assert len(result["data"]) == 3
        for row in result["data"]:
            assert set(row.keys()) == {"date", "value"}, (
                f"timeseries row must be trimmed to date+value, got {row.keys()}"
            )
        assert result["meta"]["asin"] == "B0TEST"

    def test_query_deals_envelope_is_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_fake_env(monkeypatch)
        self._patch_chainlit_step(monkeypatch)
        _reset_webapp_modules()
        from webapp import tools as webapp_tools
        from webapp.tools import dispatch_tool

        wide_deal = {
            "asin": "B0TEST",
            "site": "UK",
            "deal_type": "LIGHTNING",
            "badge": "Deal of the Day",
            "percent_claimed": 60,
            "deal_status": "ACTIVE",
            "start_time": 7584000,
            "end_time": 7590000,
            "access_type": "ALL",
            "fetched_at": "2026-04-01T10:00:00Z",
        }

        def _fake(*_args, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
            return {
                "ok": True,
                "data": [wide_deal],
                "error": None,
                "meta": {},
            }

        monkeypatch.setattr(webapp_tools, "_api_query_deals", _fake)

        result = asyncio.run(dispatch_tool("query_deals", {"marketplace": "UK"}))

        assert result["ok"] is True
        trimmed = result["data"][0]
        assert "access_type" not in trimmed
        assert "fetched_at" not in trimmed
        assert trimmed["deal_type"] == "LIGHTNING"
        assert trimmed["percent_claimed"] == 60

    def test_failure_envelope_passes_through_without_trim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed envelope (ok=False) must skip trimming entirely so the
        diagnostic ``error`` and any debug ``data`` reach the model intact."""
        _set_fake_env(monkeypatch)
        self._patch_chainlit_step(monkeypatch)
        _reset_webapp_modules()
        from webapp import tools as webapp_tools
        from webapp.tools import dispatch_tool

        failure = {
            "ok": False,
            "data": [],
            "error": "synthetic failure for test",
            "meta": {},
        }

        def _fake(*_args, **_kwargs) -> dict:  # type: ignore[no-untyped-def]
            return failure

        monkeypatch.setattr(webapp_tools, "_api_query_latest", _fake)

        result = asyncio.run(dispatch_tool("query_latest", {"marketplace": "UK"}))

        assert result is failure


@pytest.mark.unit
class TestCacheControlWiring:
    """Verify run_chat_turn attaches cache_control to the last tool_result.

    Drives ``webapp.llm.run_chat_turn`` through a single tool round-trip using
    a stubbed Anthropic client, then inspects the ``history`` list to confirm
    the moving cache_control breakpoint is set. This is the core token-burn
    mitigation: every prior turn's prompt prefix must be cached for the next
    turn to pay ~10% instead of 100% of normal input cost.
    """

    def test_last_tool_result_block_gets_cache_control(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp import llm as webapp_llm

        class _Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

            def model_dump(self) -> dict:
                return dict(self.__dict__)

        class _Usage:
            def model_dump(self) -> dict:
                return {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }

        class _Resp:
            def __init__(self, content: list, stop_reason: str):
                self.content = content
                self.stop_reason = stop_reason
                self.usage = _Usage()

        tool_use_block = _Block(
            type="tool_use",
            id="toolu_X",
            name="keepa_budget",
            input={},
        )
        text_block = _Block(type="text", text="done")
        responses = iter(
            [
                _Resp([tool_use_block], "tool_use"),
                _Resp([text_block], "end_turn"),
            ]
        )

        def _fake_create(**_kwargs):
            return next(responses)

        monkeypatch.setattr(webapp_llm._client.messages, "create", _fake_create)

        async def _fake_dispatch(_name: str, _args: dict) -> dict:
            return {"ok": True, "data": [], "error": None, "meta": {}}

        monkeypatch.setattr(webapp_llm, "dispatch_tool", _fake_dispatch)

        history: list[dict] = [{"role": "user", "content": "what's the keepa budget?"}]
        final_text, updated = asyncio.run(webapp_llm.run_chat_turn(history))

        assert final_text == "done"

        tool_result_msgs = [
            m
            for m in updated
            if m["role"] == "user"
            and isinstance(m["content"], list)
            and any(b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(tool_result_msgs) == 1, (
            "Expected exactly one user message carrying tool_result blocks"
        )

        last_block = tool_result_msgs[0]["content"][-1]
        assert last_block.get("cache_control") == {"type": "ephemeral"}, (
            "Last tool_result block must be marked ephemeral for moving breakpoint caching"
        )

    def test_cache_control_does_not_accumulate_across_turns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After N sequential user turns that each invoke a tool, the history
        must still contain exactly ONE tool_result block with cache_control.

        Regression guard for the production 400: "A maximum of 4 blocks with
        cache_control may be provided. Found 5." The moving cache_control
        marker must actually MOVE (strip old, add new), not accumulate.
        """
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp import llm as webapp_llm

        class _Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

            def model_dump(self) -> dict:
                return dict(self.__dict__)

        class _Usage:
            def model_dump(self) -> dict:
                return {}

        class _Resp:
            def __init__(self, content: list, stop_reason: str):
                self.content = content
                self.stop_reason = stop_reason
                self.usage = _Usage()

        def _tool_use(idx: int) -> _Block:
            return _Block(
                type="tool_use",
                id=f"toolu_{idx}",
                name="keepa_budget",
                input={},
            )

        text_block = _Block(type="text", text="done")

        # Five user turns in a row, each doing exactly one tool_use then
        # resolving to an end_turn. That's 10 create() calls total.
        responses = iter(
            [
                _Resp([_tool_use(1)], "tool_use"),
                _Resp([text_block], "end_turn"),
                _Resp([_tool_use(2)], "tool_use"),
                _Resp([text_block], "end_turn"),
                _Resp([_tool_use(3)], "tool_use"),
                _Resp([text_block], "end_turn"),
                _Resp([_tool_use(4)], "tool_use"),
                _Resp([text_block], "end_turn"),
                _Resp([_tool_use(5)], "tool_use"),
                _Resp([text_block], "end_turn"),
            ]
        )

        def _fake_create(**_kwargs):
            return next(responses)

        monkeypatch.setattr(webapp_llm._client.messages, "create", _fake_create)

        async def _fake_dispatch(_name: str, _args: dict) -> dict:
            return {"ok": True, "data": [], "error": None, "meta": {}}

        monkeypatch.setattr(webapp_llm, "dispatch_tool", _fake_dispatch)

        history: list[dict] = []

        async def _drive_five_turns() -> list[dict]:
            for turn_i in range(5):
                history.append({"role": "user", "content": f"turn {turn_i}"})
                _, hist_after = await webapp_llm.run_chat_turn(history)
                # run_chat_turn mutates the same list in place, so just
                # continue with the updated reference.
                assert hist_after is history
            return history

        asyncio.run(_drive_five_turns())

        # Count every block in history that carries cache_control.
        marked_blocks = [
            blk
            for msg in history
            if msg["role"] == "user" and isinstance(msg["content"], list)
            for blk in msg["content"]
            if isinstance(blk, dict) and blk.get("cache_control") is not None
        ]
        assert len(marked_blocks) == 1, (
            f"Expected exactly 1 tool_result with cache_control after 5 turns, "
            f"found {len(marked_blocks)}. This means the moving breakpoint is "
            f"accumulating instead of moving, and production will hit the "
            f"Anthropic 4-block limit."
        )

        # It must be on a tool_result block (not an assistant tool_use etc.)
        assert marked_blocks[0].get("type") == "tool_result"

        # And it must be on the LATEST tool_result — verify by scanning in
        # reverse order for the first tool_result block.
        last_tool_result = None
        for msg in reversed(history):
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for blk in msg["content"]:
                    if isinstance(blk, dict) and blk.get("type") == "tool_result":
                        last_tool_result = blk
                        break
            if last_tool_result is not None:
                break

        assert last_tool_result is marked_blocks[0], (
            "cache_control must be on the LATEST tool_result block"
        )

    def test_end_turn_without_tool_use_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Safety rail: the cache_control branch must not fire when the
        iteration produces no tool_results (e.g., an end_turn reached on the
        first pass). Otherwise the list-index access would IndexError."""
        _set_fake_env(monkeypatch)
        _reset_webapp_modules()
        from webapp import llm as webapp_llm

        class _Block:
            def __init__(self, **kw):
                self.__dict__.update(kw)

            def model_dump(self) -> dict:
                return dict(self.__dict__)

        class _Usage:
            def model_dump(self) -> dict:
                return {}

        class _Resp:
            def __init__(self, content: list, stop_reason: str):
                self.content = content
                self.stop_reason = stop_reason
                self.usage = _Usage()

        text_block = _Block(type="text", text="hello back")

        def _fake_create(**_kwargs):
            return _Resp([text_block], "end_turn")

        monkeypatch.setattr(webapp_llm._client.messages, "create", _fake_create)

        final_text, updated = asyncio.run(
            webapp_llm.run_chat_turn([{"role": "user", "content": "hi"}])
        )

        assert final_text == "hello back"
        assert not any(
            m["role"] == "user"
            and isinstance(m["content"], list)
            and any(b.get("type") == "tool_result" for b in m["content"])
            for m in updated
        )
