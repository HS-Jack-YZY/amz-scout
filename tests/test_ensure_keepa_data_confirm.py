"""Unit tests for the ensure_keepa_data confirmation flow in webapp/tools.py.

Covers ``_step_ensure_keepa_data`` and its routing through ``dispatch_tool``:

  1. No-confirm-needed path — envelope without ``needs_confirmation`` phase
     is returned identity, no dialog shown.
  2. ``meta is None`` — treated as no-confirmation-needed (defensive guard).
  3. Confirm path — dialog ``proceed=True`` re-calls api with
     ``confirm=True`` and **all five user-facing kwargs threaded through**
     unchanged.
  4. Cancel path — dialog ``proceed=False`` → ``phase="cancelled_by_user"``.
  5. Timeout / tab-close — ``send()`` returns ``None`` →
     ``phase="dialog_timeout"`` (truthful, distinct from explicit cancel).
  6. Pydantic-shaped response — attribute access (``response.payload``)
     works just like dict access, so a future Chainlit minor bump within
     ``>=2.7,<3`` does not silently flip Confirm into Cancel.
  7. Unrecognized response shape — fail loud with
     ``phase="fetch_failed"``, never silently treated as cancel.
  8. First api call raises → ``phase="fetch_failed"`` envelope.
  9. First api call returns ``ok=False`` → envelope returned untouched
     (no dialog, no second call).
 10. Post-confirm api call raises → ``phase="fetch_failed"`` envelope.
 11. Schema invariant — ``confirm`` is **not** an accepted property on the
     ``ensure_keepa_data`` tool schema. Pins the security boundary so the
     LLM cannot bypass the dialog.
 12. ``dispatch_tool`` routing — full end-to-end through the dispatcher
     with all 5 non-default kwargs, catching forwarding typos like
     ``args.get("stragegy")``.

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

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
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


def _load_webapp_tools(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Patch cl.step + reset webapp imports + reimport ``webapp.tools``.

    Centralizes the per-test boilerplate so failures in one test do not
    leave stale ``webapp.*`` modules visible to the next test.
    """
    _install_noop_cl_step(monkeypatch)
    _reset_webapp_modules()
    from webapp import tools as webapp_tools  # noqa: WPS433 — re-import is the point

    return webapp_tools


def _gate_envelope(*, estimated_tokens: int = 8, products_to_fetch: int = 8) -> dict:
    return {
        "ok": True,
        "data": {"preview": [{"asin": "B0XYZ", "site": "UK"}]},
        "error": None,
        "meta": {
            "phase": "needs_confirmation",
            "estimated_tokens": estimated_tokens,
            "products_to_fetch": products_to_fetch,
        },
    }


def _ok_envelope(meta: dict | None = None) -> dict:
    return {
        "ok": True,
        "data": {"outcomes": ["..."]},
        "error": None,
        "meta": meta if meta is not None else {},
    }


def _ask_mock(send_return: object) -> MagicMock:
    """Build a MagicMock standing in for ``cl.AskActionMessage(...).send()``."""
    ask = MagicMock()
    ask.return_value.send = AsyncMock(return_value=send_return)
    return ask


# All non-default kwargs used to verify full param threading. Distinct from
# the function signature defaults so a regression that swaps in a default
# would visibly break.
_FULL_KWARGS: dict = {
    "marketplace": "DE",
    "product": "Slate 7 Pro",
    "strategy": "fresh",
    "max_age_days": 3,
    "detailed": True,
}


# ---------------------------------------------------------------------------
# Branch 1 — no confirmation needed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_confirmation_needed_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """When api returns ok=True without needs_confirmation phase, return as-is."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    envelope = _ok_envelope()
    monkeypatch.setattr(
        webapp_tools, "_api_ensure_keepa_data", MagicMock(return_value=envelope)
    )

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="lazy"))
    assert result is envelope


# ---------------------------------------------------------------------------
# Branch 2 — meta is None (defensive guard at tools.py: meta = first.get("meta") or {})
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_meta_none_treats_as_no_confirmation_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If api returns meta=None, the helper must not crash and must not show a dialog."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    envelope = {"ok": True, "data": {}, "error": None, "meta": None}
    api_mock = MagicMock(return_value=envelope)
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})
    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="lazy"))

    assert result is envelope
    assert api_mock.call_count == 1
    assert ask_mock.call_count == 0


# ---------------------------------------------------------------------------
# Branch 3 — confirm path threads ALL five user-facing kwargs through
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confirm_path_threads_all_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm flips ``confirm`` False→True and forwards every other kwarg unchanged."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    fetch = _ok_envelope()
    api_mock = MagicMock(side_effect=[_gate_envelope(), fetch])
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(**_FULL_KWARGS))

    assert result is fetch
    assert api_mock.call_count == 2

    first_kwargs = api_mock.call_args_list[0].kwargs
    second_kwargs = api_mock.call_args_list[1].kwargs

    # 5 user-facing kwargs must be identical across both calls.
    for key in _FULL_KWARGS:
        assert first_kwargs[key] == _FULL_KWARGS[key], (
            f"first call dropped/mutated {key}: got {first_kwargs[key]!r}"
        )
        assert second_kwargs[key] == _FULL_KWARGS[key], (
            f"post-confirm call dropped/mutated {key}: got {second_kwargs[key]!r}"
        )

    # Only `confirm` differs between the two calls.
    assert first_kwargs["confirm"] is False
    assert second_kwargs["confirm"] is True


@pytest.mark.unit
def test_confirm_dialog_constructor_pins_timeout_and_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch upstream Chainlit signature drift: timeout=120, two named actions."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    api_mock = MagicMock(side_effect=[_gate_envelope(), _ok_envelope()])
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert ask_mock.call_count == 1
    ctor_kwargs = ask_mock.call_args.kwargs
    assert ctor_kwargs["timeout"] == 120
    actions = ctor_kwargs["actions"]
    assert len(actions) == 2
    assert {a.name for a in actions} == {"confirm", "cancel"}


# ---------------------------------------------------------------------------
# Branch 4 — explicit cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cancel_path_returns_cancelled_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """When user clicks Cancel, return ok=True with cancelled_by_user phase."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    api_mock = MagicMock(return_value=_gate_envelope())
    ask_mock = _ask_mock(send_return={"payload": {"proceed": False}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is True
    assert result["meta"]["phase"] == "cancelled_by_user"
    assert api_mock.call_count == 1


# ---------------------------------------------------------------------------
# Branch 5 — timeout (send → None) becomes phase="dialog_timeout", not cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_timeout_returns_dialog_timeout_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A None response (timeout / tab close / ws drop) must NOT be reported as cancel."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    api_mock = MagicMock(return_value=_gate_envelope())
    ask_mock = _ask_mock(send_return=None)

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is True
    assert result["meta"]["phase"] == "dialog_timeout"
    assert result["meta"]["phase"] != "cancelled_by_user"
    assert api_mock.call_count == 1


# ---------------------------------------------------------------------------
# Branch 6 — Pydantic / attribute-access response shape (future Chainlit compat)
# ---------------------------------------------------------------------------


class _PydanticLikeResponse:
    """Stands in for a hypothetical future Chainlit response object."""

    def __init__(self, proceed: bool) -> None:
        self.payload = {"proceed": proceed}


@pytest.mark.unit
def test_pydantic_response_shape_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """If Chainlit returns an attribute-access object, Confirm must still propagate."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    fetch = _ok_envelope()
    api_mock = MagicMock(side_effect=[_gate_envelope(), fetch])
    ask_mock = _ask_mock(send_return=_PydanticLikeResponse(proceed=True))

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result is fetch
    assert api_mock.call_count == 2
    assert api_mock.call_args_list[1].kwargs["confirm"] is True


# ---------------------------------------------------------------------------
# Branch 7 — unrecognized response shape fails loud
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unrecognized_response_shape_returns_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-dict, no-payload object must NOT be silently treated as cancel."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    class _Opaque:
        pass

    api_mock = MagicMock(return_value=_gate_envelope())
    ask_mock = _ask_mock(send_return=_Opaque())

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is False
    assert result["meta"]["phase"] == "fetch_failed"
    assert "unexpected shape" in (result["error"] or "")
    assert api_mock.call_count == 1


# ---------------------------------------------------------------------------
# Branch 8 — first api call raises (defense-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_api_call_raises_returns_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the gate-call api raises, the helper must convert it to a clean envelope."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    api_mock = MagicMock(side_effect=RuntimeError("registry unreachable"))
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="lazy"))

    assert result["ok"] is False
    assert result["meta"]["phase"] == "fetch_failed"
    assert "registry unreachable" in (result["error"] or "")
    assert ask_mock.call_count == 0  # never reaches the dialog


# ---------------------------------------------------------------------------
# Branch 9 — first api call returns ok=False (no dialog, no second call)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_api_call_ok_false_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ok=False envelope must propagate without surfacing a dialog."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    bad = {
        "ok": False,
        "data": {},
        "error": "Unknown strategy: 'wat'",
        "meta": {"phase": "validation_error"},
    }
    api_mock = MagicMock(return_value=bad)
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="wat"))

    assert result is bad
    assert api_mock.call_count == 1
    assert ask_mock.call_count == 0


# ---------------------------------------------------------------------------
# Branch 10 — post-confirm api call raises (defense-in-depth)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_post_confirm_api_call_raises_returns_fetch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the confirmed-fetch api raises, return ``phase=fetch_failed``."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    api_mock = MagicMock(
        side_effect=[_gate_envelope(), RuntimeError("keepa transport error")]
    )
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools._step_ensure_keepa_data(strategy="fresh"))

    assert result["ok"] is False
    assert result["meta"]["phase"] == "fetch_failed"
    assert "keepa transport error" in (result["error"] or "")
    assert api_mock.call_count == 2


# ---------------------------------------------------------------------------
# Branch 11 — schema invariant: ``confirm`` is NOT an LLM-controllable property
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confirm_is_not_in_tool_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the security boundary: only the UI may set ``confirm``."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    schema = next(
        s for s in webapp_tools.TOOL_SCHEMAS if s["name"] == "ensure_keepa_data"
    )
    properties = schema["input_schema"]["properties"]
    required = schema["input_schema"].get("required", [])

    assert "confirm" not in properties, (
        "regression: 'confirm' is a controllable input — LLM can bypass dialog"
    )
    assert "confirm" not in required


# ---------------------------------------------------------------------------
# Branch 12 — ``dispatch_tool`` integration (catches forwarding typos)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dispatch_tool_routes_to_step_with_full_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: dispatch_tool must forward all 5 kwargs unchanged through the gate."""
    webapp_tools = _load_webapp_tools(monkeypatch)

    fetch = _ok_envelope()
    api_mock = MagicMock(side_effect=[_gate_envelope(), fetch])
    ask_mock = _ask_mock(send_return={"payload": {"proceed": True}})

    monkeypatch.setattr(webapp_tools, "_api_ensure_keepa_data", api_mock)
    monkeypatch.setattr(webapp_tools.cl, "AskActionMessage", ask_mock)

    result = asyncio.run(webapp_tools.dispatch_tool("ensure_keepa_data", _FULL_KWARGS))

    assert result is fetch
    assert api_mock.call_count == 2
    second_kwargs = api_mock.call_args_list[1].kwargs
    for key in _FULL_KWARGS:
        assert second_kwargs[key] == _FULL_KWARGS[key], (
            f"dispatch_tool dropped/mutated {key}; "
            f"got {second_kwargs[key]!r} expected {_FULL_KWARGS[key]!r}"
        )
    assert second_kwargs["confirm"] is True
