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
