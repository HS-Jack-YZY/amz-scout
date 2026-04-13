# amz-scout webapp image — Phase 6 deployment.
# Single-stage build optimized for layer caching, not for final image size.
# Order matters: slowest layers (system + Chromium) come first so they stay
# cached when only application code changes.

FROM python:3.12-slim-bookworm

# ── Layer group A: system deps + browser-use + Chromium ────────────────
# Slow (~8 min on first build), rarely changes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install browser-use directly via pip into the system Python. This pulls
# `playwright` as a transitive dependency, putting BOTH `browser-use` and
# `playwright` on PATH via the system Python's bin/ dir. We deliberately do
# NOT use `uv tool install browser-use` here: that would isolate browser-use
# into its own venv and only expose `browser-use` (not `playwright`) on PATH,
# making the next layer's `playwright install` fail with exit 127.
RUN pip install --no-cache-dir browser-use

# Install Chromium that matches the playwright version pip just pulled in
# alongside browser-use. Because both come from the same install, the
# revision matches at runtime — no cryptic launch failures.
RUN playwright install chromium --with-deps

# ── Layer group B: Python dependencies + source ────────────────────────
# Medium speed (~1 min). Triggered by pyproject.toml or source changes.
WORKDIR /app

# Hatchling needs the source tree to build an editable install, so src/ and
# webapp/ must be present before `pip install -e`.
COPY pyproject.toml README.md ./
COPY src/ src/
COPY webapp/ webapp/

RUN pip install --no-cache-dir -e ".[web]"

# ── Layer group C: static assets ───────────────────────────────────────
# Fast (seconds). Triggered by config or chainlit asset changes.
COPY config/ config/
COPY .chainlit/ .chainlit/
COPY chainlit.md ./

EXPOSE 8000

# `-h` is --headless (no auto-open browser on host).
# Bind 0.0.0.0 so the container is reachable from the Docker network.
# Do NOT use `-w` (watch mode) — that's dev-only.
CMD ["chainlit", "run", "webapp/app.py", "-h", "--host", "0.0.0.0", "--port", "8000"]
