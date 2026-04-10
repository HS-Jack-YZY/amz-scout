"""Shared test fixtures for amz_scout tests."""

import json
from pathlib import Path

import pytest

from tests.fixtures.keepa_raw import make_raw_keepa_product

# Path to real Keepa raw JSON (may not exist in CI)
_REAL_RAW_JSON = (
    Path(__file__).parent.parent
    / "output" / "BE10000" / "data" / "eu" / "raw" / "uk_B0F2MR53D6.json"
)


@pytest.fixture
def raw_data():
    """Keepa raw product JSON — uses synthetic fixture, falls back to real file."""
    if _REAL_RAW_JSON.exists():
        with open(_REAL_RAW_JSON) as f:
            return json.load(f)
    return make_raw_keepa_product()
