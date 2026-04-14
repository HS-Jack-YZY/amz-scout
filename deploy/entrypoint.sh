#!/usr/bin/env bash
# amz-scout webapp container entrypoint.
#
# Why this script exists:
#   chainlit reads `.chainlit/config.toml` exactly once at server-module
#   import time and feeds `project.allow_origins` straight to Starlette's
#   CORSMiddleware. Patching the config from inside `webapp/app.py` is too
#   late — the middleware is already wired by the time our app module gets
#   imported. So we rewrite the toml HERE, before exec'ing chainlit.
#
# What it does:
#   1. If DOMAIN is set to a real public hostname, build an allow-list of
#      [https://$DOMAIN, http://localhost, http://127.0.0.1] and sed it into
#      the `allow_origins = ...` line in /app/.chainlit/config.toml.
#   2. If DOMAIN is empty / "localhost" / ":80" (dev or HTTP-only rehearsal),
#      leave the localhost-only default from the image as-is.
#   3. Exec chainlit so it inherits PID 1 and signal handling stays clean.
#
# Idempotent: safe to run on every container start.

set -euo pipefail

CONFIG_TOML="/app/.chainlit/config.toml"

if [[ ! -f "$CONFIG_TOML" ]]; then
    echo "[entrypoint] FATAL: $CONFIG_TOML not found" >&2
    exit 1
fi

DOMAIN="${DOMAIN:-}"

case "$DOMAIN" in
    "" | "localhost" | ":80")
        echo "[entrypoint] DOMAIN='$DOMAIN' — keeping localhost-only allow_origins from image"
        ;;
    *)
        # Build the JSON-ish array literal that TOML accepts.
        ALLOW_LIST="[\"https://${DOMAIN}\", \"http://localhost\", \"http://127.0.0.1\"]"
        # The sed pattern matches the exact line we shipped in .chainlit/config.toml.
        # We pin to `^allow_origins = ` so the comments above the line are untouched.
        sed -i "s|^allow_origins = .*|allow_origins = ${ALLOW_LIST}|" "$CONFIG_TOML"
        echo "[entrypoint] allow_origins rewritten to: ${ALLOW_LIST}"
        ;;
esac

exec chainlit run webapp/app.py -h --host 0.0.0.0 --port 8000
