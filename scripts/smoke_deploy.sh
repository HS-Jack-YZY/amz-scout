#!/usr/bin/env bash
# amz-scout deployment smoke test — Phase 6.
#
# Three steps, fail-fast:
#   1. HTTP status: GET $URL/ returns 200 or 302
#   2. Body check : response contains the literal string "Chainlit"
#                   (Caddy serves its own welcome page on 200 if upstream is
#                   down, so a body assertion is required for real coverage)
#   3. Pytest     : runs the integration smoke test against the same URL
#
# Usage:
#   scripts/smoke_deploy.sh https://amz-scout.example.internal
#   AMZ_SCOUT_DEPLOY_URL=http://localhost scripts/smoke_deploy.sh
#
# Compatible with bash 3.2 (default macOS) — no associative arrays, no `mapfile`.

set -euo pipefail

URL="${1:-${AMZ_SCOUT_DEPLOY_URL:-}}"

if [ -z "$URL" ]; then
    echo "ERROR: provide URL as first arg or AMZ_SCOUT_DEPLOY_URL env var" >&2
    echo "Usage: $0 https://amz-scout.example.internal" >&2
    exit 1
fi

# Strip trailing slash so we can append paths consistently.
URL="${URL%/}"

step() {
    printf '\n[smoke] %s\n' "$*"
}

# ── Step 1: HTTP status ────────────────────────────────────────────────
step "1/3 HTTP status check against $URL/"
HTTP_CODE="$(curl -fsS -o /dev/null -w '%{http_code}' "$URL/" || true)"
case "$HTTP_CODE" in
    200|302)
        echo "       OK ($HTTP_CODE)"
        ;;
    *)
        echo "       FAIL: expected 200 or 302, got '$HTTP_CODE'" >&2
        exit 1
        ;;
esac

# ── Step 2: body check ─────────────────────────────────────────────────
step "2/3 Body must mention Chainlit"
if curl -fsS "$URL/" | grep -q "Chainlit"; then
    echo "       OK"
else
    echo "       FAIL: body did not contain 'Chainlit'" >&2
    echo "       (Caddy may be serving its default welcome page — upstream down?)" >&2
    exit 1
fi

# ── Step 3: pytest integration smoke ───────────────────────────────────
step "3/3 pytest integration smoke"
AMZ_SCOUT_DEPLOY_URL="$URL" pytest tests/test_webapp_deployment_smoke.py -v -m integration

step "All checks passed"
