"""Webapp boundary: convert api envelopes to LLM-safe summaries
and generate downloadable Excel attachments for the user.

The LLM gets ``{count, date_range, file_attached, preview}`` — not row data.
The user gets the full DB rows as an Excel file via ``cl.File`` attached to
the final ``cl.Message``.

This module is the Phase 3 replacement for the row-emitting half of
``trim_for_llm``. ``webapp.tools.trim_for_llm`` is retained for the preview
field (≤3 rows) and as a regression safety valve — it is no longer stacked
on top of ``summarize_for_llm`` because the Excel attachment must carry the
full DB schema (``title``, ``url``, etc.), not the LLM-safe subset.
"""

import functools
import io
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import chainlit as cl
from openpyxl import Workbook

logger = logging.getLogger(__name__)

MAX_PREVIEW_ROWS = 3
# xlsx worker memory guard. openpyxl buffers all cells in RAM before save;
# 50k rows × ~20 fields keeps a single attachment under ~25 MB, which fits
# comfortably below Chainlit's per-session memory envelope.
MAX_XLSX_ROWS = 50_000
# Warnings passthrough is capped so failure paths don't blow the Phase 3
# token budget — first 3 entries, 200 chars each.
MAX_WARNINGS = 3
MAX_WARNING_CHARS = 200
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _rows_to_xlsx_bytes(
    rows: list[dict], sheet_name: str = "data"
) -> tuple[bytes, bool]:
    """Materialize full DB rows to an in-memory xlsx.

    Returns ``(bytes, truncated)``. ``truncated=True`` means the input
    exceeded ``MAX_XLSX_ROWS`` and only the first ``MAX_XLSX_ROWS`` rows were
    written — the caller surfaces that in the summary so the LLM can tell
    the user the xlsx is incomplete. Pure function: never mutates ``rows``.
    Schema-drift safe: union of keys across all rows, alphabetical header
    order for deterministic output.
    """
    truncated = len(rows) > MAX_XLSX_ROWS
    effective_rows = rows[:MAX_XLSX_ROWS] if truncated else rows
    wb = Workbook()
    ws = wb.active
    # A fresh Workbook() always has exactly one active worksheet; this assert is
    # purely for the type-checker (openpyxl's stub declares active as Optional).
    assert ws is not None
    ws.title = (sheet_name or "data")[:31] or "data"
    if not effective_rows:
        ws.append(["(empty)"])
    else:
        headers = sorted({k for r in effective_rows for k in r.keys()})
        ws.append(headers)
        for r in effective_rows:
            ws.append([r.get(h, "") for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), truncated


def _safe_filename(parts: list[str | None], ext: str = "xlsx") -> str:
    """Build a filesystem-safe filename from query params.

    Includes ``YYYY-MM-DD_HHMMSS`` (UTC) so the same tool firing twice in one
    day produces distinct filenames — Chainlit renders ``pending_files`` keyed
    by ``.name`` for UI display, and duplicate names show as ambiguous stacked
    cards. Seconds precision is sufficient given 1-call/min Keepa gating.
    """
    slug = "_".join(re.sub(r"[^A-Za-z0-9._-]+", "-", p or "").strip("-") for p in parts if p)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"{slug or 'query'}_{stamp}.{ext}"[:140]


def _truncate_warnings(raw: Any) -> list[str] | None:
    """Cap meta['warnings'] so failure paths don't blow the Phase 3 token budget.

    Accepts list-of-str (the conventional shape) or a single str. Returns up
    to ``MAX_WARNINGS`` entries, each truncated to ``MAX_WARNING_CHARS`` with
    an explicit ``…`` suffix so the LLM can tell the text was clipped. Non
    list/str values (defensive) are coerced via ``str()``.
    """
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    capped: list[str] = []
    for entry in items[:MAX_WARNINGS]:
        text = entry if isinstance(entry, str) else str(entry)
        if len(text) > MAX_WARNING_CHARS:
            text = text[:MAX_WARNING_CHARS] + "…"
        capped.append(text)
    if len(items) > MAX_WARNINGS:
        capped.append(f"(+{len(items) - MAX_WARNINGS} more warnings truncated)")
    return capped


def _build_summary(
    rows: list[dict],
    *,
    file_name: str | None,
    meta: dict[str, Any],
    preview_trimmer: Callable[[list[dict]], list[dict]] | None,
    date_field: str | None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Build the LLM-facing summary dict. Returns a NEW dict (never mutates meta).

    ``file_name=None`` means the xlsx attach failed (H1 fix): the summary
    omits ``file_attached`` and adds a warning so the LLM does NOT promise
    the user a download that was never produced.
    """
    summary: dict[str, Any] = {"count": len(rows)}
    if file_name is not None:
        summary["file_attached"] = file_name
    else:
        summary["file_attach_failed"] = True
    if truncated:
        summary["xlsx_truncated"] = True
        summary["xlsx_row_limit"] = MAX_XLSX_ROWS
    if date_field and rows and date_field in rows[0]:
        dates = [r[date_field] for r in rows if r.get(date_field)]
        if dates:
            summary["date_range"] = f"{min(dates)} to {max(dates)}"
    if rows and preview_trimmer is not None:
        summary["preview"] = preview_trimmer(rows[:MAX_PREVIEW_ROWS])
    # Passthrough useful meta identifiers the LLM already cites in reply text.
    # "count" excluded: len(rows) is the source of truth.
    # "warnings" goes through _truncate_warnings so a misbehaving upstream
    # can't silently 10x the summary token cost.
    for k in ("asin", "model", "brand", "series_name", "hint", "phase"):
        if k in meta and k not in summary:
            summary[k] = meta[k]
    if "warnings" in meta:
        capped = _truncate_warnings(meta["warnings"])
        if capped:
            summary["warnings"] = capped
    return summary


def _attach_file_to_session(name: str, content: bytes) -> bool:
    """Append a ``cl.File`` to ``session['pending_files']``.

    Returns ``True`` on success, ``False`` if attachment failed. Splits the
    two distinct failure modes so success is never silently reported:

    - **No-session path** (pytest, CLI, background threads): ``cl.File``'s
      ``__post_init__`` reads ``context.session.thread_id`` and raises
      ``AttributeError`` when the session is ``None``. This is the expected
      non-web path → DEBUG log, caller falls back to a session-free summary.
    - **Any other exception**: a genuine error — WARNING log with stack,
      return ``False`` so the caller omits ``file_attached`` from the
      summary. Previously all exceptions were swallowed at DEBUG level,
      which let the LLM promise attachments that never reached the user.
    """
    try:
        f = cl.File(name=name, content=content, mime=XLSX_MIME, display="inline")
    except AttributeError:
        logger.debug("No chainlit session; skipping pending_files attach")
        return False
    except Exception:
        logger.warning("cl.File construction failed; user will not receive %s", name, exc_info=True)
        return False
    try:
        pending = cl.user_session.get("pending_files", []) or []
        pending.append(f)
        cl.user_session.set("pending_files", pending)
    except AttributeError:
        logger.debug("No chainlit session; skipping pending_files attach")
        return False
    except Exception:
        logger.warning("pending_files session update failed; user will not receive %s", name, exc_info=True)
        return False
    return True


def _log_query(tool: str, kwargs: dict, summary: dict) -> None:
    """Append a structured query record to ``session['query_log']``.

    Seeds the interface contract for the future project-analysis mode
    (Phase 4+): log stores ``tool``, sanitized ``args``, ``count``,
    ``date_range``, ``file_name``, and a UTC timestamp.
    """
    try:
        log = cl.user_session.get("query_log", []) or []
        log.append(
            {
                "tool": tool,
                "args": {k: v for k, v in kwargs.items() if v is not None},
                "count": summary.get("count", 0),
                "date_range": summary.get("date_range"),
                "file_name": summary.get("file_attached"),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        cl.user_session.set("query_log", log)
    except Exception:
        logger.debug("No chainlit session; skipping query_log append")


def summarize_for_llm(
    *,
    tool_name: str,
    file_name_parts: Callable[[dict], list[str | None]],
    preview_trimmer: Callable[[list[dict]], list[dict]] | None = None,
    date_field: str | None = "date",
    sheet_name: str = "data",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Rewrite an envelope's ``data`` list into a summary dict and attach xlsx.

    Wraps an async ``_step_*`` wrapper so successful envelopes become:

        {ok, error, meta, data: {count, date_range?, file_attached, preview?, ...}}

    Failure envelopes (``ok=False``) pass through untouched — no Excel file
    is generated and no session state is mutated, so the LLM sees the full
    ``error`` string and can retry / clarify.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            result = await fn(*args, **kwargs)
            if not isinstance(result, dict) or not result.get("ok"):
                return result
            raw_rows = result.get("data")
            # Defensive: upstream is typed to return list, but a dict leak
            # would silently become `count=0` under the old `or []` — log
            # loudly and fall through with an empty list.
            if not isinstance(raw_rows, list):
                logger.warning(
                    "%s: expected list data, got %s; falling back to empty rows",
                    tool_name,
                    type(raw_rows).__name__,
                )
                rows: list[dict] = []
            else:
                rows = raw_rows
            file_name = _safe_filename(file_name_parts(kwargs))
            xlsx_bytes, truncated = _rows_to_xlsx_bytes(rows, sheet_name=sheet_name)
            attached = _attach_file_to_session(file_name, xlsx_bytes)
            summary = _build_summary(
                rows,
                file_name=file_name if attached else None,
                meta=result.get("meta") or {},
                preview_trimmer=preview_trimmer,
                date_field=date_field,
                truncated=truncated,
            )
            _log_query(tool_name, kwargs, summary)
            return {**result, "data": summary}

        return wrapper

    return decorator
