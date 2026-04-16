"""Regression guard: CLAUDE.md must stay under the token budget."""

from pathlib import Path

import pytest

CLAUDE_MD = Path(__file__).parent.parent / "CLAUDE.md"
MAX_CHARS = 10_000  # approx 2,500 tokens (mix of Chinese + English)


@pytest.mark.unit
def test_claude_md_char_budget():
    text = CLAUDE_MD.read_text()
    assert len(text) <= MAX_CHARS, (
        f"CLAUDE.md is {len(text)} chars (budget: {MAX_CHARS}). "
        f"Move developer docs to docs/DEVELOPER.md."
    )


@pytest.mark.unit
def test_claude_md_no_forced_asin_discovery():
    """PRD decision: forced ASIN discovery via WebSearch is removed."""
    text = CLAUDE_MD.read_text()
    assert "禁止跳过此步骤" not in text
    assert "后台 ASIN 补全" not in text
