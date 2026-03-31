"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from amz_scout.config import (
    load_marketplace_config,
    load_project_config,
    validate_config,
)

CONFIG_DIR = Path(__file__).parent.parent / "config"


class TestMarketplaceConfig:
    def test_load(self):
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        assert "UK" in mp
        assert mp["UK"].keepa_domain == "GB"
        assert mp["DE"].currency_symbol == "€"
        assert mp["AU"].delivery_city == "SYDNEY"

    def test_all_sites(self):
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        assert set(mp.keys()) == {"UK", "DE", "FR", "IT", "ES", "NL", "CA", "AU"}


class TestProjectConfig:
    def test_load(self):
        proj = load_project_config(CONFIG_DIR / "BE10000.yaml")
        assert proj.project.name == "BE10000"
        assert len(proj.products) == 17
        assert proj.target_marketplaces == ["UK", "DE", "FR", "IT", "ES", "NL", "CA", "AU"]

    def test_products_have_asins(self):
        proj = load_project_config(CONFIG_DIR / "BE10000.yaml")
        for p in proj.products:
            assert len(p.default_asin) == 10, f"{p.model} has invalid ASIN"

    def test_to_product(self):
        proj = load_project_config(CONFIG_DIR / "BE10000.yaml")
        product = proj.products[0].to_product()
        assert product.brand == "ASUS"
        assert product.asin_for("CA") == "B0FSPQSJGF"


class TestValidation:
    def test_valid_config(self):
        proj = load_project_config(CONFIG_DIR / "BE10000.yaml")
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        errors = validate_config(proj, mp)
        assert errors == []

    def test_invalid_marketplace(self):
        proj = load_project_config(CONFIG_DIR / "BE10000.yaml")
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        proj = proj.model_copy(update={"target_marketplaces": ["UK", "JP"]})
        errors = validate_config(proj, mp)
        assert any("JP" in e for e in errors)
