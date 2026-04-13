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
