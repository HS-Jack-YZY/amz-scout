"""Unit tests for the ensure_keepa_data confirmation flow in webapp/tools.py.

Covers the 4 branches of ``_step_ensure_keepa_data``:

  1. No-confirm-needed path: envelope has no ``needs_confirmation`` phase,
     pass-through identity.
  2. Confirm path: dialog returns ``proceed=True`` → second api call with
     ``confirm=True``, returns the fetch envelope.
  3. Cancel path: dialog returns ``proceed=False`` → cancel envelope, never
     a second api call.
  4. Timeout / tab-close path: dialog ``.send()`` returns ``None`` → same as
     cancel.

Conventions (mirror ``tests/test_webapp_smoke.py``):
  - Wrap async code in ``asyncio.run(...)`` rather than
    ``@pytest.mark.asyncio``. The repo does not install ``pytest-asyncio``,
    so the marker would silently skip.
  - Patch ``cl.step`` to a no-op decorator BEFORE (re)importing
    ``webapp.tools`` — the real decorator requires an active Chainlit
    session context (``context.session.thread_id`` lookup), which does
    not exist in standalone pytest and raises
    ``ChainlitContextException``.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


def _reset_webapp_modules() -> None:
    """Clear any cached webapp.* imports so cl.step patching takes effect."""
    for mod in list(sys.modules):
        if mod.startswith("webapp"):
            del sys.modules[mod]


def _install_noop_cl_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``cl.step`` with a decorator factory that returns the function as-is."""
    import chainlit as cl

    def _noop_step(**_kwargs):  # type: ignore[no-untyped-def]
        def _decorator(fn):  # type: ignore[no-untyped-def]
            return fn

        return _decorator

    monkeypatch.setattr(cl, "step", _noop_step)


@pytest.mark.unit
def test_no_confirmation_needed_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """When api returns ok=True without needs_confirmation phase, return as-is."""
    _install_noop_cl_step(monkeypatch)
    _reset_webapp_modules()

    from webapp import tools as webapp_tools

    envelope = {"ok": True, "data": {"outcomes": []}, "error": None, "meta": {}}
    monkeypatch.setattr(
        webapp_tools, "_api_ensure_keepa_data", MagicMock(return_value=envelope)
    )

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="lazy"))
    assert result is envelope


@pytest.mark.unit
def test_confirm_path_triggers_second_call_with_confirm_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user clicks Confirm, re-call api with ``confirm=True``."""
    _install_noop_cl_step(monkeypatch)
    _reset_webapp_modules()

    from webapp import tools as webapp_tools

    gate_envelope = {
        "ok": True,
        "data": {"preview": [{"asin": "B0XYZ", "site": "UK", "model": "Slate7"}]},
        "error": None,
        "meta": {
            "phase": "needs_confirmation",
            "estimated_tokens": 8,
            "products_to_fetch": 8,
        },
    }
    fetch_envelope = {
        "ok": True,
        "data": {"outcomes": ["..."]},
        "error": None,
        "meta": {},
    }

    api_mock = MagicMock(side_effect=[gate_envelope, fetch_envelope])
    ask_mock = MagicMock()
    ask_mock.return_value.send = AsyncMock(return_value={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result is fetch_envelope
    assert api_mock.call_count == 2
    assert api_mock.call_args_list[0].kwargs["confirm"] is False
    assert api_mock.call_args_list[1].kwargs["confirm"] is True


@pytest.mark.unit
def test_cancel_path_returns_cancelled_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """When user clicks Cancel, return ok=True with cancelled_by_user phase."""
    _install_noop_cl_step(monkeypatch)
    _reset_webapp_modules()

    from webapp import tools as webapp_tools

    gate_envelope = {
        "ok": True,
        "data": {"preview": []},
        "error": None,
        "meta": {
            "phase": "needs_confirmation",
            "estimated_tokens": 10,
            "products_to_fetch": 10,
        },
    }
    api_mock = MagicMock(return_value=gate_envelope)
    ask_mock = MagicMock()
    ask_mock.return_value.send = AsyncMock(return_value={"payload": {"proceed": False}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is True
    assert result["meta"]["phase"] == "cancelled_by_user"
    assert api_mock.call_count == 1


@pytest.mark.unit
def test_timeout_returns_cancelled_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """When dialog times out (send returns None), treat as cancel."""
    _install_noop_cl_step(monkeypatch)
    _reset_webapp_modules()

    from webapp import tools as webapp_tools

    gate_envelope = {
        "ok": True,
        "data": {"preview": []},
        "error": None,
        "meta": {
            "phase": "needs_confirmation",
            "estimated_tokens": 10,
            "products_to_fetch": 10,
        },
    }
    api_mock = MagicMock(return_value=gate_envelope)
    ask_mock = MagicMock()
    ask_mock.return_value.send = AsyncMock(return_value=None)

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is True
    assert result["meta"]["phase"] == "cancelled_by_user"
    assert api_mock.call_count == 1
