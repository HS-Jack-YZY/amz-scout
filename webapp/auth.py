"""Chainlit password auth callback with @gl-inet.com email whitelist."""

import logging
import secrets

import chainlit as cl

from webapp.config import ALLOWED_EMAIL_DOMAIN, APP_PASSWORD

logger = logging.getLogger(__name__)


@cl.password_auth_callback
def auth_callback(username: str, password: str) -> cl.User | None:
    """Authenticate a user by email domain + shared password.

    Phase 1 MVP: all @gl-inet.com emails share a single APP_PASSWORD.
    Phase 6 will replace this with per-user bcrypt hashes.

    Notes:
        - ``ALLOWED_EMAIL_DOMAIN`` is normalized in ``webapp.config`` to always
          start with "@" and to be lowercased, so a plain ``endswith`` here is
          safe against the ``attacker@evilgl-inet.com`` lookalike attack.
        - Password comparison goes through ``secrets.compare_digest`` to remove
          the timing side channel that ``!=`` would expose.
    """
    email = username.strip().lower()

    if not email.endswith(ALLOWED_EMAIL_DOMAIN):
        logger.warning(
            "Auth rejected: email %r not in allowed domain %s",
            email,
            ALLOWED_EMAIL_DOMAIN,
        )
        return None

    if not APP_PASSWORD:
        logger.error("APP_PASSWORD is empty — all auth will fail")
        return None

    if not secrets.compare_digest(password, APP_PASSWORD):
        logger.warning("Auth rejected: wrong password for %s", email)
        return None

    logger.info("Auth OK for %s", email)
    return cl.User(
        identifier=email,
        metadata={"role": "user", "domain": ALLOWED_EMAIL_DOMAIN},
    )
