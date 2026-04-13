"""Phase 6 deployment smoke test — runs against a real deployed instance.

This test is integration-only and skips by default. It never imports the
webapp package, never touches the database, and never tries to log in. It
makes a single HTTP GET against the configured URL and asserts the response
is the Chainlit login page (not Caddy's default welcome screen).

Activate by setting AMZ_SCOUT_DEPLOY_URL in the environment, or via the
wrapper at scripts/smoke_deploy.sh.
"""

import os

import pytest

# httpx lives in [dev] extras — skip the whole module if it isn't installed
# rather than failing pytest collection.
httpx = pytest.importorskip("httpx")

DEPLOY_URL_ENV = "AMZ_SCOUT_DEPLOY_URL"


def _deploy_url() -> str | None:
    """Return the configured deployment URL with any trailing slash trimmed."""
    raw = os.environ.get(DEPLOY_URL_ENV, "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


@pytest.mark.integration
class TestDeployment:
    """Probe a live amz-scout webapp deployment over HTTP."""

    def test_skipped_without_env(self) -> None:
        """Running this test without AMZ_SCOUT_DEPLOY_URL set must skip cleanly.

        We deliberately don't read the env at module import time so this test
        can document the contract even when it would otherwise be skipped at
        the class level.
        """
        if _deploy_url() is None:
            pytest.skip(
                f"{DEPLOY_URL_ENV} is not set — deployment smoke is skipped by default. "
                "Set it to e.g. http://localhost or https://amz-scout.example.internal."
            )

    def test_login_page_reachable(self) -> None:
        """GET / must return 200 and the body must contain 'Chainlit'.

        Asserting on the literal body string (not just status 200) is critical:
        Caddy serves its own default welcome page with status 200 when the
        upstream is down, so a status-only check would silently pass against
        a broken deploy.
        """
        url = _deploy_url()
        if url is None:
            pytest.skip(f"{DEPLOY_URL_ENV} is not set")

        response = httpx.get(f"{url}/", follow_redirects=True, timeout=10.0)
        assert response.status_code == 200, (
            f"expected HTTP 200, got {response.status_code} — body: {response.text[:200]}"
        )

        body = response.text
        assert "Chainlit" in body or "amz-scout" in body, (
            "response body did not contain 'Chainlit' or 'amz-scout' — "
            "Caddy may be serving its default welcome page (upstream down?)"
        )
