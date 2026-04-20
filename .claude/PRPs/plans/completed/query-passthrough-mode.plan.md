# Plan: 查询直通模式（Query Pass-Through Mode）

## Summary

把 webapp 的查询工具从「返回 trimmed rows 给 LLM 解读」改成「返回结构化摘要给 LLM + 把完整数据作为 Excel 附件给用户」。LLM 只做 NL → API 翻译，不再读取记录内容；用户通过下载链接拿全量数据。同时在 `cl.user_session` 中维护 `query_log` 记录本次会话所有查询，为未来「项目分析模式」预留接口。

## User Story

As 小李（GL.iNet PM，webapp 主要用户），
I want AI 翻译我的自然语言查询后，只告诉我「查到了多少条、时间范围、下载链接」，把原始数据写进可下载的 Excel，
So that 我拿到全量数据自己分析，不为 AI 逐条解读付出不必要的 output token 成本。

## Problem → Solution

**当前**：`webapp/tools.py` 的 `_step_*` 包装器用 `@trim_for_llm(...)` 把 `data` 列表投影到 LLM-safe 字段，仍然把所有行（通常 87-180 条）塞进 tool_result content，LLM 逐条消费。同一查询（"查 BE3600 UK 价格历史"）的 output tokens ~650。

**之后**：row-emitting 工具不再给 LLM 喂行数据，而是返回 `{count, date_range, file_attached, preview}` 摘要；完整数据（不经过 `_llm_trim`）导出为 Excel，通过 `cl.File` 附加到最终 `cl.Message`。同一查询的 output tokens 降至 ~50（-92%）。同时在 session 中记录 `query_log`，供未来项目分析模式使用。

## Metadata

- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/amz-scout-slim-refactor.prd.md`
- **PRD Phase**: Phase 3 — 查询直通模式
- **Estimated Files**: 5（1 新模块 `webapp/summaries.py` + `webapp/tools.py` + `webapp/app.py` + `tests/test_webapp_smoke.py` + `tests/test_token_audit.py`）
- **Estimated Lines**: ~400 added / ~80 removed
- **Estimated Time**: ~4 hours
- **Dependencies**: Phase 1（CLAUDE.md 已更新）— 已完成

---

## UX Design

### Before（当前 Phase 2 状态）

```
User: "查 Slate 7 在 UK 过去 90 天的价格走势"
  ↓
LLM sees tools [query_trends, ...]
  ↓
query_trends(product="Slate 7", marketplace="UK", series="new", days=90)
  ↓
tool_result (trimmed to date+value, 87 rows, ~3,500 tokens):
  [{"date": "2026-04-15 10:00", "value": 15099},
   {"date": "2026-04-14 10:00", "value": 14999}, ... × 87]
  ↓
LLM 逐条读取 87 条数据 + 生成分析文字（~650 output tokens）
→ "过去 90 天 Slate 7 UK 的价格走势如下：
   2026-04-15: £150.99 ... 整体呈现下降趋势 ..."
```

### After（Phase 3 目标）

```
User: "查 Slate 7 在 UK 过去 90 天的价格走势"
  ↓
LLM sees tools [query_trends, ...]  (docstrings compressed -250 tokens)
  ↓
query_trends(product="Slate 7", marketplace="UK", series="new", days=90)
  ↓
tool_result (summary only, ~80 tokens):
  {
    "ok": true,
    "data": {
      "count": 87,
      "date_range": "2025-07-18 to 2026-04-15",
      "file_attached": "query_trends_Slate-7_UK_new_2026-04-17.xlsx",
      "preview": [{"date": "2026-04-15 10:00", "value": 15099}, ...],
      "asin": "B0F2MR53D6",
      "model": "Slate 7"
    },
    "meta": { ... passthrough ... }
  }
  ↓
LLM 生成摘要文字（~50 output tokens）
→ "已查询 GL-MT6000 Slate 7 Pro (BE3600) 在 UK 的 amazon_new 价格历史。
    - 记录数：87 条
    - 时间范围：2025-07-18 至 2026-04-15
    [query_trends_Slate-7_UK_new_2026-04-17.xlsx]  ← cl.File 附件"
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| tool_result.data | 行数组（trimmed，87 行 × 2 字段 ≈ 3,500 tokens） | 摘要 dict（~80 tokens） | LLM 不再「读」具体行 |
| 用户获取全量数据 | 从 AI 输出文字里复制 | 点击 cl.File 下载 Excel | 全量 DB 字段，不经过 `_llm_trim` |
| meta 穿透 | 原样返回（fetch_meta、warnings、hint） | 原样返回 | 不变，LLM 仍看到 `phase="needs_confirmation"` 等控制信号 |
| 失败 envelope | `ok=False` 原样返回 | `ok=False` 原样返回 | 失败路径不生成文件，LLM 看到完整 error |
| session `query_log` | 不存在 | list of `{tool, args, count, ...}` | 为未来项目模式预留 |
| tool schema docstrings | 9 工具 ~1,100 tokens | ~850 tokens | -250 tokens/轮 |
| CLAUDE.md | 描述「返回行数据」 | 描述「返回摘要 + cl.File 下载」 | 1 条 Key Behavior 更新 |

---

## Mandatory Reading

Files that MUST be read before implementing:

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 (critical) | `webapp/tools.py` | 1-462 | 要修改的核心文件：9 个 `_step_*` 包装器、`trim_for_llm` 装饰器、`dispatch_tool` 分发 |
| P0 (critical) | `webapp/app.py` | 1-65 | `on_message` 处理流程，最终 `cl.Message` 发送位置（要把 pending files 附加到这里） |
| P0 (critical) | `src/amz_scout/api.py` | 409-416 | `_add_dates` 不可变转换模式 |
| P0 (critical) | `src/amz_scout/api.py` | 493-718 | 7 个 row-emitting query 函数的返回 envelope 形状（`data`、`meta` 字段） |
| P0 (critical) | `src/amz_scout/_llm_trim.py` | 1-87 | trim 模块：Phase 3 后仍保留 `trim_*` 用于 preview（前 3 行），**不删除** |
| P1 (important) | `webapp/llm.py` | 82-103 | tool_use loop 里 `tool_results.content` 是 `json.dumps` 的 envelope — 摘要必须 JSON-safe |
| P1 (important) | `tests/test_webapp_smoke.py` | 124-465 | 测试骨架：`_noop_step`、`_fake_envelope`、`monkeypatch.setattr(webapp_tools, "_api_*", ...)` |
| P1 (important) | `tests/test_token_audit.py` | 1-100 | 测量框架：`count_tokens` before/after 对比；Phase 3 新行加在这里 |
| P2 (reference) | `CLAUDE.md` | 79-93 | 需要更新的 Key Behaviors（1 条）— row-emitting 查询返回摘要而非数据 |
| P2 (reference) | `.claude/PRPs/plans/completed/phase2-query-tools.plan.md` | 1-100 | Phase 2 的 tool schema pattern — 压缩 docstring 时参考原始设计意图 |
| P2 (reference) | `pyproject.toml` | 25-30 | `openpyxl>=3.1` 已在 `web` optional deps，无需新增依赖 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Chainlit `cl.File` | `chainlit.element.File` (v2.11) | 构造参数：`name`, `content: bytes\|str`, `mime`, `display: Literal['inline','side','page']`。`content=bytes` + `mime` 是最干净的用法，无需写磁盘 |
| Chainlit `cl.Message(elements=[...])` | Chainlit docs | `elements` 接受 `File`/`Image`/etc.；附加到消息后在 UI 中显示为内联下载卡片 |
| openpyxl `Workbook.save(BytesIO())` | openpyxl 3.1 | `save()` 接受文件路径 **或** 类文件对象；用 `io.BytesIO` 生成内存 xlsx，无需临时文件 |
| Anthropic `count_tokens` | Anthropic API | Phase 3 后重跑 `test_token_audit.py` 验证 data-field delta（目标 ≥60%） |

```
KEY_INSIGHT: cl.File(content=..., mime=...) 内存模式不写磁盘，退出 session 自动 GC。
APPLIES_TO: Task "Excel 文件生成" — 不需要临时目录、不需要清理钩子。
GOTCHA: Chainlit 2.11 的 File.content 类型签名是 Union[bytes, str, None]。bytes 直传 xlsx 二进制；str 会被当成文本，所以 xlsx 必须用 bytes。
```

```
KEY_INSIGHT: LLM 看到的 tool_result.content 是 json.dumps(envelope, ensure_ascii=False, default=str) 的字符串。摘要字段必须 JSON-safe（datetime → isoformat str、frozenset → list、bytes → 排除）。
APPLIES_TO: Task "生成摘要 envelope"
GOTCHA: file_attached 字段只放文件名（str），不要放 cl.File 对象本身 — 那会触发 json.dumps 递归失败。
```

```
KEY_INSIGHT: 现有 trim_* 函数保留用于生成 "preview" 字段（摘要里带前 1-3 行样本），不删除。
APPLIES_TO: Task "设计 summary schema"
GOTCHA: preview 可选字段；空数据场景下省略。
```

---

## Patterns to Mirror

Code patterns discovered in the codebase. Follow these exactly.

### ENVELOPE_SHAPE
```python
# SOURCE: src/amz_scout/api.py:335-350
def _envelope(
    ok: bool,
    data: list | dict | None = None,
    error: str | None = None,
    hint_if_empty: str | None = None,
    **meta: Any,
) -> ApiResponse:
    if hint_if_empty and not data:
        meta["hint"] = hint_if_empty
    return {
        "ok": ok,
        "data": data if data is not None else [],
        "error": error,
        "meta": meta,
    }
```
**How to mirror**: 摘要版 envelope 的 `data` 字段变成 dict（而非 list），但 `ok`/`error`/`meta` 四字段形状不变。webapp 边界的新 helper 走「新建 dict，不改旧」的不可变模式。

### TRIM_DECORATOR
```python
# SOURCE: webapp/tools.py:41-63
def trim_for_llm(
    trimmer: Callable[[list[dict]], list[dict]],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            result = await fn(*args, **kwargs)
            if not isinstance(result, dict) or not result.get("ok"):
                return result
            rows = result.get("data") or []
            return {**result, "data": trimmer(rows)}
        return wrapper
    return decorator
```
**How to mirror**: 新的 `summarize_for_llm(file_name_parts, preview_trimmer, ...)` 装饰器照抄这个形状：失败直通、只改 `data` 字段、用 `{**result, "data": summary}` 做不可变 merge。**`trim_for_llm` 保留**，但 row-emitting wrappers 不再叠加它 — 在 summarize 装饰器内部显式调 trim 生成 preview（见 Task 2 的 GOTCHA）。

### STEP_WRAPPER
```python
# SOURCE: webapp/tools.py:376-391
@cl.step(type="tool", name="query_trends")
@trim_for_llm(trim_timeseries_rows)
async def _step_query_trends(product: str, marketplace: str = "UK", ...) -> dict:
    logger.info("query_trends called: ...")
    return _api_query_trends(product=product, marketplace=marketplace, ...)
```
**How to mirror**: 装饰器顺序 = `@cl.step` → `@summarize_for_llm(...)` → function。summarize 装饰器在 cl.step 里面，这样 cl.step UI 看到的 return 已经是摘要。

### DISPATCH_TABLE
```python
# SOURCE: webapp/tools.py:394-453
async def dispatch_tool(name: str, args: dict) -> dict:
    if name == "query_latest":
        marketplace = args.get("marketplace")
        if not marketplace:
            return _missing_required("query_latest", "marketplace")
        return await _step_query_latest(marketplace=marketplace, ...)
    # ... if-elif chain
```
**How to mirror**: 不改 dispatch_tool 的分派逻辑；只改 `_step_*` 内部行为。dispatch 仍然返回 envelope dict。

### IMMUTABLE_TRANSFORM
```python
# SOURCE: src/amz_scout/api.py:409-416
def _add_dates(rows: list[dict]) -> list[dict]:
    return [
        {**r, "date": (KEEPA_EPOCH + timedelta(minutes=r["keepa_ts"])).strftime(...)}
        if "keepa_ts" in r else r
        for r in rows
    ]
```
**How to mirror**: `_build_summary()` / `_rows_to_xlsx_bytes()` 纯函数，不改输入 rows。dict 用 `{**old, "new_key": val}`。

### SESSION_STATE
```python
# SOURCE: webapp/app.py:29-30
@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("history", [])
```
**How to mirror**: `on_chat_start` 再加 `cl.user_session.set("query_log", [])` 和 `cl.user_session.set("pending_files", [])`。

### TEST_MODULE_RESET
```python
# SOURCE: tests/test_webapp_smoke.py:14-26
def _reset_webapp_modules() -> None:
    for mod in list(sys.modules):
        if mod.startswith("webapp"):
            del sys.modules[mod]

def _set_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    # ...
```
**How to mirror**: 新测试继续调 `_set_fake_env` + `_reset_webapp_modules`；monkey-patch `cl.step` 为 no-op；用 `monkeypatch.setattr(webapp_tools, "_api_*", _fake)` 注入假 envelope。

### CL_STEP_NOOP_PATCH
```python
# SOURCE: tests/test_webapp_smoke.py:190-196
def _noop_step(**_kwargs):
    def _decorator(fn):
        return fn
    return _decorator
monkeypatch.setattr(cl, "step", _noop_step)
```
**How to mirror**: 对 `cl.user_session` 同样 monkey-patch 成 in-memory store（见 Task 5 的 `_FakeSession` 示例）。

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `webapp/summaries.py` | CREATE | 新模块：`_rows_to_xlsx_bytes()` / `_build_summary()` / `summarize_for_llm()` 装饰器 / `_attach_file_to_session()` / `_log_query()` |
| `webapp/tools.py` | MODIFY | 用 `summarize_for_llm` 替换 row-emitting wrappers 上的 `trim_for_llm`；保留 `trim_for_llm` 导入用于 preview；压缩 9 个 tool schema docstrings（-250 tokens） |
| `webapp/app.py` | MODIFY | `on_chat_start` 初始化 `query_log` + `pending_files`；`on_message` 在 `cl.Message(...)` 里 `elements=pending_files` 并清空 |
| `CLAUDE.md` | MODIFY | Key Behavior 追加：查询工具返回摘要 + Excel 下载，AI 不应逐条分析 |
| `tests/test_webapp_smoke.py` | MODIFY | (1) **删除** `TestWebappTrimBoundary` 里 3 个 row-shape 测试（首跑即崩）+ 保留 1 个失败直通测试；(2) 新增 `TestQueryPassthrough`：摘要 envelope、full-row Excel、query_log、失败直通、schema size 守护；(3) `_FakeSession` 新增 `_FakeFile` dataclass stub（绕开 chainlit context.session 依赖） |
| `tests/test_token_audit.py` | MODIFY | 改 `query_trends` / `query_latest` 的基线为 **untrimmed raw envelope**（贴合 PRD 60% 目标），而非 phase2 trim 的增量；新增 `phase3_summary_tokens` 列 |

## NOT Building

- **完整「项目分析模式」实现** — 本次只写 `query_log` 数据结构，不做 UI mode switch
- **持久化 `query_log`** — 仅内存 per-session；关闭 tab 即失效（与 PRD Open Q4 一致）
- **新 Keepa API 调用** — 摘要来自现有 rows，零额外 Keepa token
- **Excel 列重命名/本地化/条件格式** — MVP 直接写 DB 字段名；美化留到未来
- **删除 `_llm_trim.py`** — 保留：(a) preview 字段生成；(b) CLI/admin 不受影响；(c) 回归安全阀
- **修改 `check_freshness` / `keepa_budget`** — 这两个已是摘要形状
- **修改 `amz_scout.api`（CLI boundary）** — 变更只在 webapp 边界
- **cl.File 写磁盘** — 全部用 `BytesIO` 内存生成

---

## Step-by-Step Tasks

### Task 1: 新建 `webapp/summaries.py`

- **ACTION**: 新建模块，声明 Excel 生成、摘要构建、装饰器、session 日志
- **IMPLEMENT**:
  ```python
  """Webapp boundary: convert api envelopes to LLM-safe summaries
  and generate downloadable Excel attachments for the user.

  The LLM gets {count, date_range, file_attached, preview} — not row data.
  The user gets the full DB rows as an Excel file via cl.File.
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
  XLSX_MIME = (
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  )

  def _rows_to_xlsx_bytes(rows: list[dict], sheet_name: str = "data") -> bytes:
      """Materialize full DB rows to in-memory xlsx. Pure, schema-drift safe."""
      wb = Workbook()
      ws = wb.active
      ws.title = (sheet_name[:31] or "data")
      if not rows:
          ws.append(["(empty)"])
      else:
          headers = sorted({k for r in rows for k in r.keys()})
          ws.append(headers)
          for r in rows:
              ws.append([r.get(h, "") for h in headers])
      buf = io.BytesIO()
      wb.save(buf)
      return buf.getvalue()

  def _safe_filename(parts: list[str | None], ext: str = "xlsx") -> str:
      """Build a filesystem-safe xlsx name from query params.

      Includes `YYYY-MM-DD_HHMMSS` (UTC) to prevent filename collisions
      when the same tool fires twice in one day (same session or across
      sessions). Chainlit's pending_files list is keyed by .name for UI
      display, and duplicate names show as ambiguous stacked cards.
      """
      slug = "_".join(
          re.sub(r"[^A-Za-z0-9._-]+", "-", p or "").strip("-")
          for p in parts if p
      )
      stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
      return f"{slug or 'query'}_{stamp}.{ext}"[:140]

  def _build_summary(
      rows: list[dict],
      *,
      file_name: str,
      meta: dict[str, Any],
      preview_trimmer: Callable[[list[dict]], list[dict]] | None,
      date_field: str | None,
  ) -> dict[str, Any]:
      """Build the LLM-facing summary dict. Returns a NEW dict."""
      summary: dict[str, Any] = {
          "count": len(rows),
          "file_attached": file_name,
      }
      if date_field and rows and date_field in rows[0]:
          dates = [r[date_field] for r in rows if r.get(date_field)]
          if dates:
              summary["date_range"] = f"{min(dates)} to {max(dates)}"
      if rows and preview_trimmer is not None:
          summary["preview"] = preview_trimmer(rows[:MAX_PREVIEW_ROWS])
      for k in ("asin", "model", "brand", "series_name", "hint",
                "phase", "warnings", "count"):
          # Note: api 返回的 meta.count 用 rows len 覆盖；其余字段穿透
          if k in meta and k not in summary:
              summary[k] = meta[k]
      return summary

  def _attach_file_to_session(name: str, content: bytes) -> None:
      """Append cl.File to session['pending_files']. Silent if no ctx."""
      try:
          pending = cl.user_session.get("pending_files", []) or []
          pending.append(
              cl.File(name=name, content=content,
                      mime=XLSX_MIME, display="inline")
          )
          cl.user_session.set("pending_files", pending)
      except Exception:
          logger.debug("No chainlit session; skipping pending_files")

  def _log_query(tool: str, kwargs: dict, summary: dict) -> None:
      """Append a structured query record to session['query_log']."""
      try:
          log = cl.user_session.get("query_log", []) or []
          log.append({
              "tool": tool,
              "args": {k: v for k, v in kwargs.items() if v is not None},
              "count": summary.get("count", 0),
              "date_range": summary.get("date_range"),
              "file_name": summary.get("file_attached"),
              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          })
          cl.user_session.set("query_log", log)
      except Exception:
          logger.debug("No chainlit session; skipping query_log")

  def summarize_for_llm(
      *,
      tool_name: str,
      file_name_parts: Callable[[dict], list[str | None]],
      preview_trimmer: Callable[[list[dict]], list[dict]] | None = None,
      date_field: str | None = "date",
      sheet_name: str = "data",
  ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
      """Rewrite envelope.data (list of rows) → summary dict, attach xlsx."""
      def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
          @functools.wraps(fn)
          async def wrapper(*args: Any, **kwargs: Any) -> dict:
              result = await fn(*args, **kwargs)
              if not isinstance(result, dict) or not result.get("ok"):
                  return result
              rows = result.get("data") or []
              # kwargs 是调用传入；args 位置参数通常为空（dispatch_tool 用关键字）
              file_name = _safe_filename(file_name_parts(kwargs))
              xlsx_bytes = _rows_to_xlsx_bytes(rows, sheet_name=sheet_name)
              _attach_file_to_session(file_name, xlsx_bytes)
              summary = _build_summary(
                  rows,
                  file_name=file_name,
                  meta=result.get("meta") or {},
                  preview_trimmer=preview_trimmer,
                  date_field=date_field,
              )
              _log_query(tool_name, kwargs, summary)
              return {**result, "data": summary}
          return wrapper
      return decorator
  ```
- **MIRROR**: `ENVELOPE_SHAPE`、`TRIM_DECORATOR`、`IMMUTABLE_TRANSFORM`
- **IMPORTS**: `functools`, `io`, `logging`, `re`, `datetime.datetime`, `datetime.timezone`, `typing.Any`, `collections.abc.Callable`, `chainlit as cl`, `openpyxl.Workbook`
- **GOTCHA**:
  1. `cl.File(content=bytes, mime=...)` — v2.11 要求 xlsx 用 bytes，str 会被当文本
  2. **`cl.File(...)` 构造在 chainlit context 外会 crash**：`chainlit/element.py` 的 `Element.__post_init__` 有 `thread_id: str = Field(default_factory=lambda: context.session.thread_id)`，`context.session is None`（pytest / CLI / 后台线程）时抛 `AttributeError: 'NoneType' object has no attribute 'thread_id'`。因此 `cl.File(...)` 构造**必须**在 `_attach_file_to_session` 的 try/except 块内（本 plan 代码已满足）。测试里**必须 monkey-patch `cl.File`** 为一个有相同字段的 `@dataclass` stub（见 Task 5 的 `_FakeFile`），否则所有测试在构造 cl.File 时即抛错、断言不可达
  3. `cl.user_session.get/set` 在 `context.session is None` 时会走内部 early-return（`user_session.py:17-18,36-37`）soft-fail — 对 user_session 的 try/except 技术上冗余但保留作为显式防御
  4. `datetime.now(timezone.utc)` 替代 deprecated `utcnow()`（Python 3.12）
  5. summary 里**只**放 `file_attached: str`，绝不放 bytes / `cl.File` 对象（json.dumps 会炸）
  6. `_safe_filename` 用 `re.sub` 一次过过滤；不要链式 `str.replace`
  7. `_build_summary` 里 `count` 以 `len(rows)` 为准，**不**用 meta 里的 `count`（api.py 有时传 count 但含义不同）
  8. **内存累积边界**：xlsx bytes 存在 `cl.user_session["pending_files"]` 即 Chainlit 进程级 dict；session 不过期则不 GC。按 6 用户 × 20 查询/日 × ~30 KB/xlsx ≈ 3.6 MB/日，server 重启前累积在单进程内存。当前规模可接受；未来若规模扩大需改用磁盘临时文件 + TTL 清理
- **VALIDATE**:
  - `_rows_to_xlsx_bytes([])` 返回非空 bytes（`openpyxl.load_workbook(BytesIO(bytes))` 可读回）
  - `_rows_to_xlsx_bytes([{"a":1},{"b":2}])` header 为 `["a","b"]`（字母排序）
  - `_safe_filename(["query_trends", "Slate 7", "UK/GB", None])` 不含 `/`、空格、None
  - `_build_summary([])["count"] == 0` 且无 `date_range`

### Task 2: 改造 `webapp/tools.py`

- **ACTION**: 把 7 个 row-emitting wrappers 的 `@trim_for_llm` 替换为 `@summarize_for_llm`（内嵌 preview_trimmer）；压缩 9 个 tool schema description
- **IMPLEMENT**:
  - 保留 `from webapp.summaries import summarize_for_llm`；保留 `from amz_scout._llm_trim import trim_*`（作为 preview_trimmer 参数使用）
  - `query_latest` 示例：
    ```python
    @cl.step(type="tool", name="query_latest")
    @summarize_for_llm(
        tool_name="query_latest",
        file_name_parts=lambda kw: [
            "query_latest", kw.get("marketplace"), kw.get("category"),
        ],
        preview_trimmer=trim_competitive_rows,
        date_field="scraped_at",
        sheet_name="latest_snapshot",
    )
    async def _step_query_latest(marketplace: str, category: str | None = None) -> dict:
        logger.info(...)
        return _api_query_latest(marketplace=marketplace, category=category)
    ```
  - `query_trends` 用 `date_field="date"`, `preview_trimmer=trim_timeseries_rows`（`api._add_dates` 已把 keepa_ts 解码成 `YYYY-MM-DD HH:MM` 字符串）
  - `query_sellers` 用 `date_field="date"`, `preview_trimmer=trim_seller_rows`（同上）
  - `query_deals` 用 `date_field=None`, `preview_trimmer=trim_deals_rows` — **⚠️ 不能用 `start_time`**：`LLM_SAFE_DEAL_FIELDS` 里的 `start_time`/`end_time` 是 Keepa 编码的分钟整数（如 `7584000`），直接 `min()/max()` 会产出 `"7584000 to 7590000"` 这种 garbage summary。MVP 阶段暂不向 LLM 暴露 deals 的 date_range；未来如需要，应在 `_build_summary` 外先对 rows 做类似 `_add_dates` 的 decode（用 `KEEPA_EPOCH + timedelta(minutes=start_time)`）
  - `query_compare` / `query_ranking` / `query_availability` 用 `date_field="scraped_at"`, `preview_trimmer=trim_competitive_rows`（`scraped_at` 已是 ISO 字符串）
  - `check_freshness` / `keepa_budget` **不加** `summarize_for_llm`（已是摘要形态）
  - 压缩 9 个 tool description：移除"Use when the user asks..."的中英双语重复，合并重复的 "Auto-fetches missing Keepa data using LAZY strategy..." 到单行。目标：`len(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))` ≤ 5,500 chars（当前 ~6,500）
  - 模块 docstring 加一行：「Phase 3 后 row-emitting 工具返回摘要 dict；完整数据通过 cl.File (Excel) 附加到用户消息」
- **MIRROR**: `STEP_WRAPPER`（装饰器顺序）、`DISPATCH_TABLE`（dispatch_tool 不动）
- **IMPORTS**: 新增 `from webapp.summaries import summarize_for_llm`；`trim_*` 保留（用于 preview_trimmer 参数）
- **GOTCHA**:
  1. **不再叠加 `@trim_for_llm`** — summarize 装饰器内部用 preview_trimmer 生成前 3 行 preview。xlsx 走全量 rows（满足 PRD Decisions Log「全量 DB 字段」）
  2. 装饰器顺序必须 `@cl.step` → `@summarize_for_llm` → function
  3. 压缩 docstrings 时不能删「required/optional」「accepts aliases」等关键语义
  4. `TOOL_SCHEMAS[-1]` 仍需 `cache_control` — 不要动
  5. `check_freshness` 的 data 是 dict（matrix），不是 list rows — 不适用 summarize 装饰器
  6. `keepa_budget` data 也是 dict — 同上
- **VALIDATE**:
  - `test_all_phase2_tool_names_present`（现有）通过
  - `test_tool_schemas_have_cache_control_on_last`（现有）通过
  - 新增 `test_tool_schema_size_reduced`：`len(json.dumps(TOOL_SCHEMAS)) ≤ 5500`
  - 手动：chainlit 启动，step 展开看到摘要而非行数组

### Task 3: 改造 `webapp/app.py`

- **ACTION**: `on_chat_start` 初始化 session 状态；`on_message` 附加并清空 pending files
- **IMPLEMENT**:
  ```python
  @cl.on_chat_start
  async def on_chat_start() -> None:
      cl.user_session.set("history", [])
      cl.user_session.set("query_log", [])
      cl.user_session.set("pending_files", [])
      user = cl.user_session.get("user")
      if user:
          await cl.Message(content=f"欢迎 {user.identifier}! ...").send()

  @cl.on_message
  async def on_message(msg: cl.Message) -> None:
      history = cl.user_session.get("history", [])
      history.append({"role": "user", "content": msg.content})
      try:
          final_text, updated_history = await run_chat_turn(history)
      except Exception:
          logger.exception("run_chat_turn failed")
          cl.user_session.set("pending_files", [])  # drop on failure
          await cl.Message(content="⚠️ Sorry ...").send()
          return
      cl.user_session.set("history", updated_history)
      pending = cl.user_session.get("pending_files", []) or []
      cl.user_session.set("pending_files", [])
      await cl.Message(content=final_text, elements=pending).send()
  ```
- **MIRROR**: `SESSION_STATE`
- **IMPORTS**: 无新增
- **GOTCHA**:
  1. 异常路径必须清空 `pending_files`，否则下一轮错误附加上一轮失败前文件
  2. `elements=[]` 合法（非 row-emitting 查询不产文件）
  3. `cl.Message(elements=pending)` Chainlit 2.11 直接接受 `list[File|...]`
- **VALIDATE**:
  - 新增 `test_on_chat_start_initializes_session_keys`
  - 新增 `test_pending_files_drained_after_message`
  - 新增 `test_pending_files_drained_on_exception`

### Task 4: 更新 CLAUDE.md

- **ACTION**: Key Behaviors 追加一条，反映查询直通行为
- **IMPLEMENT**:
  - 在 Key Behaviors #1 后追加（或新增 #14）：
    > **14. 查询直通**：row-emitting 工具（latest/availability/compare/deals/ranking/sellers/trends）在 webapp 中不返回具体数据行，只返回 `{count, date_range, file_attached, preview}` 摘要；完整数据通过 Excel 附件下载给用户。AI **不应**尝试逐条分析返回的 preview，应引导用户下载 Excel 或基于 count/date_range 总结。`check_freshness` / `keepa_budget` 保持原样（本身就是摘要）。
- **MIRROR**: 沿用现有 CLAUDE.md 编号列表风格
- **IMPORTS**: N/A（文档）
- **GOTCHA**: 总字符数保持 ≤10,000（当前 ~4,200，追加 <500 无越界风险）
- **VALIDATE**:
  - `pytest tests/test_claude_md_size.py` 通过
  - `wc -c CLAUDE.md` ≤ 10,000

### Task 5: 扩展 `tests/test_webapp_smoke.py` + **重构既有 `TestWebappTrimBoundary`**

- **ACTION**:
  1. **删除或重构** 现有 `TestWebappTrimBoundary` 三个 row-shape 断言测试 — Phase 3 后 `result["data"]` 是 dict 而非 list，这些测试首跑 pytest 即崩（`TypeError` on `len(result["data"])` 或 `result["data"][0]`）
  2. 新增 `TestQueryPassthrough` 测试类
- **IMPLEMENT**:

  **Part A — 处理既有 `TestWebappTrimBoundary`**（`tests/test_webapp_smoke.py:282-465`）:

  | 现有测试 | Phase 3 处理 | 理由 |
  |---|---|---|
  | `test_query_latest_envelope_is_trimmed_at_webapp_boundary` (L304-356) | **DELETE** | 断言 `result["data"]` 是 list 且含 trimmed row；Phase 3 后是 summary dict。等价覆盖已被 `TestQueryPassthrough::test_full_rows_land_in_xlsx_not_trimmed` 接管（xlsx 含全量字段、LLM 看摘要） |
  | `test_query_trends_timeseries_is_trimmed` (L358-398) | **DELETE** | 同上，被 `test_query_trends_returns_summary_not_rows` 接管 |
  | `test_query_deals_envelope_is_trimmed` (L400-437) | **DELETE** | 同上；deals 的覆盖在 `TestQueryPassthrough` 加一个 `test_query_deals_summary_has_no_date_range`（对应 Task 2 的 `date_field=None` 决策） |
  | `test_failure_envelope_passes_through_without_trim` (L439-464) | **KEEP**，重命名为 `test_failure_envelope_passes_through` | 失败 envelope 直通语义 Phase 3 仍成立（summarize 装饰器也对 `ok=False` 直通）；断言 `result is failure` 保持正确 |

  删除的三个测试的 regression 保护已被 `TestQueryPassthrough` 用更贴近 Phase 3 契约的断言替代 — 不丢覆盖面。

  **Part B — 新增 `TestQueryPassthrough`**:

  ```python
  @pytest.mark.unit
  class TestQueryPassthrough:
      """Row-emitting tools return summaries (not rows) to LLM,
      and attach full DB rows as cl.File to session."""

      def _patch_session_and_step(self, monkeypatch):
          """Patch cl.user_session, cl.step, AND cl.File.

          cl.File(...) 构造时会读 context.session.thread_id，pytest 中
          context.session is None → AttributeError。必须 stub 成纯
          dataclass，否则测试在构造 cl.File 时即崩。
          """
          import chainlit as cl
          from dataclasses import dataclass, field
          store: dict = {}

          class _FakeSession:
              def get(self, k, default=None): return store.get(k, default)
              def set(self, k, v): store[k] = v

          @dataclass
          class _FakeFile:
              name: str = ""
              content: bytes | str | None = None
              mime: str | None = None
              display: str = "inline"

          def _noop_step(**_kw):
              def _dec(fn): return fn
              return _dec

          monkeypatch.setattr(cl, "user_session", _FakeSession())
          monkeypatch.setattr(cl, "step", _noop_step)
          monkeypatch.setattr(cl, "File", _FakeFile)
          return store

      def test_query_trends_returns_summary_not_rows(self, monkeypatch):
          _set_fake_env(monkeypatch)
          store = self._patch_session_and_step(monkeypatch)
          _reset_webapp_modules()
          from webapp import tools as webapp_tools
          from webapp.tools import dispatch_tool
          rows = [
              {"date": f"2026-04-{i:02d}", "value": 100+i,
               "keepa_ts": 7584000+i}
              for i in range(1, 88)
          ]
          def _fake(**_kw):
              return {"ok": True, "data": rows, "error": None,
                      "meta": {"asin": "B0X", "model": "Slate 7"}}
          monkeypatch.setattr(webapp_tools, "_api_query_trends", _fake)

          result = asyncio.run(dispatch_tool(
              "query_trends", {"product": "Slate 7", "marketplace": "UK"}
          ))
          assert result["ok"] is True
          assert isinstance(result["data"], dict)
          assert result["data"]["count"] == 87
          assert "date_range" in result["data"]
          assert result["data"]["file_attached"].endswith(".xlsx")
          assert result["meta"]["asin"] == "B0X"
          # preview present but ≤3 rows
          assert len(result["data"].get("preview", [])) <= 3

      def test_full_rows_land_in_xlsx_not_trimmed(self, monkeypatch):
          """Excel must carry full DB fields, not LLM-safe trimmed set."""
          from io import BytesIO
          from openpyxl import load_workbook
          _set_fake_env(monkeypatch)
          store = self._patch_session_and_step(monkeypatch)
          _reset_webapp_modules()
          from webapp import tools as webapp_tools
          from webapp.tools import dispatch_tool

          wide_row = {
              "id": 42, "site": "UK", "brand": "ExampleBrand",
              "model": "XR-100", "asin": "B0T",
              "title": "MUST APPEAR IN XLSX",
              "price_cents": 14999, "url": "https://example.test",
          }
          def _fake(**_kw):
              return {"ok": True, "data": [wide_row], "error": None, "meta": {}}
          monkeypatch.setattr(webapp_tools, "_api_query_latest", _fake)
          asyncio.run(dispatch_tool("query_latest", {"marketplace": "UK"}))

          pending = store.get("pending_files", [])
          assert len(pending) == 1
          xlsx_bytes = pending[0].content
          wb = load_workbook(BytesIO(xlsx_bytes))
          ws = wb.active
          headers = [c.value for c in ws[1]]
          for field in ("title", "url", "id"):
              assert field in headers, f"{field!r} missing from xlsx"

      def test_query_log_appended(self, monkeypatch):
          _set_fake_env(monkeypatch)
          store = self._patch_session_and_step(monkeypatch)
          _reset_webapp_modules()
          from webapp import tools as webapp_tools
          from webapp.tools import dispatch_tool
          def _fake(**_kw):
              return {"ok": True, "data": [{"x": 1}], "error": None, "meta": {}}
          monkeypatch.setattr(webapp_tools, "_api_query_latest", _fake)

          asyncio.run(dispatch_tool("query_latest", {"marketplace": "UK"}))
          asyncio.run(dispatch_tool("query_latest", {"marketplace": "DE"}))

          log = store.get("query_log", [])
          assert len(log) == 2
          assert log[0]["tool"] == "query_latest"
          assert log[0]["args"]["marketplace"] == "UK"
          assert log[1]["args"]["marketplace"] == "DE"
          assert all("ts" in e for e in log)

      def test_failure_envelope_passes_through_without_summary(self, monkeypatch):
          _set_fake_env(monkeypatch)
          store = self._patch_session_and_step(monkeypatch)
          _reset_webapp_modules()
          from webapp import tools as webapp_tools
          from webapp.tools import dispatch_tool
          failure = {"ok": False, "data": [],
                     "error": "synthetic", "meta": {}}
          monkeypatch.setattr(webapp_tools, "_api_query_latest",
                              lambda **_: failure)
          result = asyncio.run(dispatch_tool(
              "query_latest", {"marketplace": "UK"}))
          assert result == failure
          assert store.get("pending_files", []) == []
          assert store.get("query_log", []) == []

      def test_tool_schema_size_reduced(self, monkeypatch):
          _set_fake_env(monkeypatch)
          _reset_webapp_modules()
          from webapp.tools import TOOL_SCHEMAS
          import json
          size = len(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))
          # Regression guard set comfortably below the Phase 2 baseline (~6,500)
          # rather than at the exact compression target. Purpose is to catch
          # future bloat, not to enforce a tight compression outcome.
          assert size <= 6000, (
              f"TOOL_SCHEMAS size {size} chars exceeds 6,000 regression budget "
              f"(Phase 2 baseline was ~6,500, Phase 3 target ≤5,500)"
          )
  ```
- **MIRROR**: `TEST_MODULE_RESET`, `CL_STEP_NOOP_PATCH`
- **IMPORTS**: `io.BytesIO`, `openpyxl.load_workbook`, `dataclasses.dataclass`
- **GOTCHA**:
  1. `_FakeSession` / `_FakeFile` 都用显式类（dataclass），不要用 dict（dict.set 是 frozenset API，不兼容）
  2. `pending[0].content` 是 dataclass 属性（非 `.get()`）— `_FakeFile` 字段与 `cl.File` 对齐
  3. **阈值选择**：`test_tool_schema_size_reduced` 的 6,000 是 regression guard（Phase 2 baseline 6,500），**不是**压缩达成测试。压缩目标（5,500）的验证在 Task 6 的 token audit 做，这里只保证不退化
  4. 不要忘了 delete 现有 3 个 row-shape trim 测试 — 否则首跑 pytest 即 3 连红
- **VALIDATE**: `pytest tests/test_webapp_smoke.py -v`（整个文件全绿，包含 retained + 新增）

### Task 6: 扩展 `tests/test_token_audit.py`

- **ACTION**: 以「未 trim 的 raw envelope」为基线，测 Phase 3 摘要的真实节省（对齐 PRD 60% 目标）
- **IMPLEMENT**:
  - 新 helper `_envelope_summary(summary_dict, meta=None) -> dict`
  - **关键改动**：baseline 从 `phase2_trimmed` **改为** `_envelope_untrimmed`（existing helper）— PRD 成功指标是「对比瘦身前后同类查询 token 消耗」，而瘦身前 = Phase 0 未 trim，不是 Phase 2 trim。用 trim 做基线会低估 Phase 3 的真实节省
  - 对 `query_trends` 和 `query_latest`：
    - `before_raw` = 原始 DB 行数组 envelope（`_envelope_untrimmed(rows)`）
    - `after_trimmed` = Phase 2 trim 行数组 envelope（保留作为参考）
    - `after_summary` = Phase 3 摘要 envelope（`data` 是 dict）
    - 三者分别过 `_count_tokens_for_tool_result`
    - 写 `output/token_audit.json` 新字段：`{raw, trimmed, summary, pct_saved_vs_raw, pct_saved_vs_trimmed}`
    - **主断言**：`(raw - summary) / raw >= 0.60`（贴合 PRD 60% 目标）
    - **辅断言**：`(trimmed - summary) / trimmed >= 0.30`（增量节省 sanity check）
- **MIRROR**: 现有 `test_token_audit.py` fixture 和 `_envelope_untrimmed`
- **IMPORTS**: 无新增
- **GOTCHA**:
  1. 测试 `@pytest.mark.network`，CI 自动 skip — 不是回归门，是人工验证通道
  2. `count_tokens` 要求 tool_use.id 匹配 tool_result.tool_use_id — 沿用现有 fixture
  3. **PRD 60% 是 per-query 包含 system + tools + history 的端到端节省**，单 tool_result 的节省通常 >60%（data field 是主项）；所以 60% 阈值对单 tool_result 是可达的保守目标
  4. `query_latest` 的 baseline 行数据通常只有 ~10 rows（跨产品），节省比例可能低于 `query_trends`（87 rows）；对两者分开断言或用 `query_trends` 作主要门槛
- **VALIDATE**: `ANTHROPIC_API_KEY=... pytest tests/test_token_audit.py -m network -v`

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `_rows_to_xlsx_bytes([])` | empty rows | Non-empty bytes, `(empty)` cell | ✅ Empty |
| `_rows_to_xlsx_bytes(wide_rows)` | 1 row, 32 cols | xlsx with 32 headers alphabetical | Normal |
| `_safe_filename(["a","b/c","",None])` | mixed/None | `a_b-c_<date>.xlsx`, no `/`, no whitespace | ✅ None |
| `_build_summary([])` | empty | `{count: 0, file_attached}`, no `date_range` | ✅ Empty |
| `_build_summary(ts_rows)` | 87 rows with `date` | `count=87`, `date_range="min to max"`, preview ≤3 | Normal |
| `summarize_for_llm` on ok=True | trimmed rows | Envelope with `data=summary dict` | Happy path |
| `summarize_for_llm` on ok=False | failure envelope | Passthrough unchanged, no session writes | ✅ Failure |
| `_attach_file_to_session` no-context | no chainlit | logs debug, no raise | ✅ No session |
| `_log_query` no-context | no chainlit | logs debug, no raise | ✅ No session |
| `query_trends` dispatcher | `{product, marketplace}` | Summary data, pending_files=1, query_log=1 | Normal |
| `query_latest` wide row → xlsx | 32-col row | xlsx contains `title`/`url`/`id` | ✅ Full rows |
| 2 consecutive queries | 2 dispatch calls | query_log=2, pending_files=2 | ✅ Accumulation |
| `query_ranking` failure | ok=False envelope | No summary, no file, session clean | ✅ Failure |
| `test_tool_schema_size_reduced` | TOOL_SCHEMAS | ≤5,500 chars | Regression |
| `on_chat_start` session init | new session | `query_log=[]`, `pending_files=[]` | Init |
| `on_message` pending drain | after run_chat_turn | `pending_files=[]`, elements passed | Flush |
| `on_message` exception drain | forced exception | `pending_files=[]`, error sent | ✅ Error |

### Edge Cases Checklist
- [x] Empty input (no rows)
- [x] Large input (87-180 rows typical, tested via wide_row)
- [x] Invalid types — `_rows_to_xlsx_bytes` tolerates missing keys
- [ ] Concurrent access — Chainlit isolates per session; N/A
- [ ] Network failure — api.py handles
- [x] Missing chainlit session context (tests, CLI)
- [x] Failure envelope passthrough (no session writes)
- [x] Exception during run_chat_turn (pending_files drained)
- [x] Filename with special chars (`/`, whitespace, Chinese)

---

## Validation Commands

### Static Analysis
```bash
ruff check webapp/ src/amz_scout/ tests/
ruff format --check webapp/ src/amz_scout/ tests/
```
EXPECT: Zero errors

### Unit Tests — webapp boundary
```bash
pytest tests/test_webapp_smoke.py -v
```
EXPECT: All existing + new `TestQueryPassthrough` tests pass

### Unit Tests — llm_trim preservation guard
```bash
pytest tests/test_llm_trim.py -v
```
EXPECT: No regression — `_llm_trim` module untouched

### Full Test Suite (excluding network)
```bash
pytest -v --ignore=tests/test_token_audit.py
```
EXPECT: All unit/integration tests pass

### CLAUDE.md Size Guard
```bash
pytest tests/test_claude_md_size.py -v
wc -c CLAUDE.md  # expect ≤10,000 chars (Phase 1 budget)
```

### Token Audit (local, needs ANTHROPIC_API_KEY)
```bash
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY pytest tests/test_token_audit.py -m network -v
cat output/token_audit.json | jq '.[] | {tool, phase2_trimmed, phase3_summary, pct_saved}'
```
EXPECT: ≥50% savings on row-emitting tool_results

### Manual Browser Validation
```bash
chainlit run webapp/app.py -w
```
Test flows (record `resp.usage.output_tokens` from server logs):
- [ ] "查 Slate 7 UK 过去 90 天价格走势" → 消息里看到 count + date_range + cl.File 下载卡
- [ ] 打开 Excel 确认包含 `title`、`url` 等 trim-dropped 字段
- [ ] 连续 3 个查询 → 每条消息都有独立的 xlsx 附件
- [ ] "数据多久没更新了" → `check_freshness` 仍返回 matrix（未被改动）
- [ ] 触发失败（查不存在产品）→ LLM 看到 error，无附件
- [ ] 同一查询 Phase 2 vs. Phase 3 的 `output_tokens` 对比

### Manual Validation Checklist
- [ ] `ruff check` clean
- [ ] `pytest tests/test_webapp_smoke.py tests/test_llm_trim.py tests/test_claude_md_size.py` 全绿
- [ ] CLAUDE.md ≤10,000 chars
- [ ] 浏览器烟雾测试 3 类查询
- [ ] `output/token_audit.json` 的 `pct_saved` ≥ 50%

---

## Acceptance Criteria

- [ ] All 6 tasks completed
- [ ] All validation commands pass
- [ ] `webapp/summaries.py` 新建，单元测试覆盖
- [ ] `webapp/tools.py` 7 row-emitting wrappers 使用 `summarize_for_llm`
- [ ] `webapp/app.py` 初始化 + 刷新 session 状态
- [ ] CLAUDE.md 新增 Key Behavior #14
- [ ] `tests/test_webapp_smoke.py::TestQueryPassthrough` 5+ 用例全绿
- [ ] `tests/test_token_audit.py` 新增 Phase 3 对比行
- [ ] Token 节省 ≥50%（单个 tool_result）
- [ ] 现有功能零回归（freshness/budget/auth/tool dispatch/trim）
- [ ] cl.File 在浏览器里可下载并打开

## Completion Checklist

- [ ] 代码遵循现有模式（装饰器 + 不可变 envelope）
- [ ] 错误处理沿用 `ok=False` 直通 + logger.debug
- [ ] 日志沿用 `logger.info` / `logger.debug` 分级
- [ ] 测试遵循 `_set_fake_env` + `_reset_webapp_modules` + monkey-patch `cl.*`
- [ ] 无硬编码路径（文件名由 `_safe_filename` 构造）
- [ ] 文档同步（CLAUDE.md Key Behavior）
- [ ] 未引入新依赖（`openpyxl` 已在 pyproject）
- [ ] 无 scope 越界（未实现项目模式 UI / 未持久化 query_log / 未改 api.py）
- [ ] 自包含 — 所有决策在 plan 内闭环

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Chainlit 2.11 `cl.File(content=bytes)` 行为与文档不符 | Low | Medium | Task 1 先写最小 POC；源码 `chainlit/element.py:88` 已确认 `content: Optional[Union[bytes, str]]` |
| xlsx 大数据量（>10k rows）内存暴增 | Low | Medium | 当前查询上限 ~1k rows（90 天 × 多 series）；真遇到时加 fallback CSV |
| 装饰器顺序错导致 xlsx 字段被 trim | Medium | High | Task 2 明确「不叠加 `@trim_for_llm`」，在 summarize 装饰器内部用 preview_trimmer 生成前 3 行；测试 `test_full_rows_land_in_xlsx_not_trimmed` 作为 fail-loud 守护 |
| LLM 看不到 preview 后误判"无数据"反复重试 | Low | Medium | summary.preview 保留 1-3 行 + 明确 `count` + `file_attached`；CLAUDE.md 第 14 条指示不应重试 |
| **`cl.File(...)` 构造在 chainlit context 外 crash**（原 audit Critical #1） | **High** | **High** | 已在 Task 1 GOTCHA #2 + Task 5 `_FakeFile` dataclass stub 处理：测试必须 `monkeypatch.setattr(cl, "File", _FakeFile)`，否则 `context.session.thread_id` 访问炸断言不可达 |
| **现有 `TestWebappTrimBoundary` 3 个 row-shape 测试首跑即崩**（原 audit Critical #2） | **High** | **High** | 已在 Task 5 Part A 显式列出 3 个 DELETE + 1 个 KEEP/重命名；覆盖面由 `TestQueryPassthrough` 接管 |
| `query_deals` 用 Keepa 编码整数当日期生成 garbage `date_range`（原 audit Open Q #2） | High（若不修）| Medium | 已在 Task 2 改为 `date_field=None`；decode Keepa start_time 留给未来迭代 |
| token audit 用 trim 作基线低估真实节省（原 audit High #3）| High（若不改）| Medium | 已在 Task 6 改为 `_envelope_untrimmed` baseline，主断言 `pct_saved_vs_raw >= 0.60` 对齐 PRD |
| 同日二次查询同工具文件名冲突（原 audit Medium #6）| Medium | Low | 已在 Task 1 `_safe_filename` 改为 `YYYY-MM-DD_HHMMSS`（秒级时间戳），`pending_files` 不再碰撞 |
| `pending_files` 内存累积（原 audit Open Q #1）| Low | Low | 当前规模 ~3.6 MB/日/进程，server 重启前累积，可接受；已在 Task 1 GOTCHA #8 记录，规模扩大时改磁盘 + TTL |
| `test_token_audit.py` 在 CI 不跑 → 无自动化节省守护 | Medium | Low | `test_tool_schema_size_reduced` 以 6,000 chars 作为 schema-level regression guard；真实 token 节省在 PR 描述里手测报告 |
| CLAUDE.md 越过 10,000 chars | Low | Low | Task 4 限定新增 <500 chars；现有 `test_claude_md_size` 是硬门 |
| LLM 见 summary 后仍试图逐条分析 preview → output 膨胀 | Medium | Low | `MAX_PREVIEW_ROWS=3`；CLAUDE.md 第 14 条明确「不应逐条分析」；必要时 `webapp/config.py` 的 `SYSTEM_PROMPT` 追加一句 |

## Notes

### 为什么 summarize 装饰器内嵌 preview_trimmer 而不叠加 `@trim_for_llm`

叠加模式（summarize 外侧 + trim 内侧）会导致 summarize 装饰器只看到 trimmed 行，xlsx 字段不全；PRD Decisions Log 明确要求「Excel 全量 DB 字段（不经过 `_llm_trim`）」。把 trim 移进 summarize 装饰器内部、只用于生成 preview，是唯一同时满足两个需求的方案。

### 为什么保留 `_llm_trim.py`

1. **Preview 字段**：summary 里带 1-3 行代表性样本，LLM 可引用具体值；用 `trim_*` 生成 preview 避免 token 浪费
2. **回归安全阀**：`summarize_for_llm` 未来如被改/拆，`trim_for_llm` 是 fallback
3. **CLI/admin 不受影响**：`amz_scout.api` 继续返回全量行；trim 只在 webapp 边界
4. **`tests/test_llm_trim.py` 的 schema 守护**：`competitive_snapshots` 加列时会 fail-loud 提示 LLM-safe allow-list 过期

### 与「项目分析模式」的接口契约

Phase 3 只写 `query_log` 数据结构，不做 UI。未来项目模式读取：
```python
log = cl.user_session.get("query_log", [])
# 用户："把我刚才查的这些产品做对比分析"
# → LLM 读 log，生成 project definition
```
shape 稳定后再谈持久化（PRD Open Q4）。

### 不改 `amz_scout.api` 的原因

Phase 3 变更是**表现层**的：LLM 看什么、用户看什么。业务层（api 返回全量行）不变：
- CLI `amz-scout query_trends` 继续打印全量 rows
- 单元测试 `test_api.py` 零改动
- 回归边界清晰：失败时只需回滚 webapp/

### Phase 3 不做的「项目模式」具体边界

- ❌ 不加 mode 切换 UI（`/project` command）
- ❌ 不把 `query_log` 写入 SQLite
- ❌ 不做「根据 query_log 生成分析」prompt 工程
- ✅ 只做内存 `query_log` 数据结构 + append 钩子
