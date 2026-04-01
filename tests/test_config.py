"""Tests for config loading and validation."""

from pathlib import Path

import pytest

from amz_scout.config import (
    MarketplaceConfig,
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
        assert set(mp.keys()) == {
            "UK", "DE", "FR", "IT", "ES", "NL",
            "US", "CA", "MX",
            "IN", "BR",
            "JP", "AU",
        }


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
        proj = proj.model_copy(update={"target_marketplaces": ["UK", "KR"]})
        errors = validate_config(proj, mp)
        assert any("KR" in e for e in errors)


class TestKeepaDomainValidation:
    def test_browser_only_marketplaces_have_null_domain_code(self):
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        assert mp["AU"].keepa_domain_code is None
        assert mp["NL"].keepa_domain_code is None

    def test_keepa_supported_marketplaces_have_domain_code(self):
        mp = load_marketplace_config(CONFIG_DIR / "marketplaces.yaml")
        for site in ("UK", "DE", "FR", "IT", "ES", "US", "CA", "MX", "JP", "IN", "BR"):
            assert mp[site].keepa_domain_code is not None, f"{site} should have a domain code"

    def test_invalid_keepa_domain_code_rejected(self):
        with pytest.raises(ValueError, match="Invalid keepa_domain_code"):
            MarketplaceConfig(
                amazon_domain="amazon.com.au",
                keepa_domain="AU",
                keepa_domain_code=14,
                currency_code="AUD",
                currency_symbol="$",
                region="apac",
                delivery_postcode="2000",
            )

    def test_null_keepa_domain_code_accepted(self):
        mc = MarketplaceConfig(
            amazon_domain="amazon.nl",
            keepa_domain="NL",
            keepa_domain_code=None,
            currency_code="EUR",
            currency_symbol="€",
            region="eu",
            delivery_postcode="1012",
        )
        assert mc.keepa_domain_code is None
