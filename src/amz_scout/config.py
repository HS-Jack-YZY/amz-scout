"""YAML config loading and validation for amz-scout."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from amz_scout.models import Product


class MarketplaceConfig(BaseModel):
    """Configuration for one Amazon marketplace."""

    amazon_domain: str
    keepa_domain: str
    keepa_domain_code: int  # Numeric Keepa API domain code
    currency_code: str
    currency_symbol: str
    price_format: str = "standard"
    region: str  # e.g. "eu", "na", "apac"
    delivery_postcode: str
    delivery_city: str | None = None  # AU needs city selection


class Settings(BaseModel):
    """Scraping behavior settings."""

    retry_count: int = 3
    page_load_wait: int = 3
    inter_product_delay: int = 2
    screenshot_on_error: bool = True
    headed_mode: bool = False
    keepa_stats_days: int = 90


class ProjectInfo(BaseModel):
    """Project metadata."""

    name: str
    description: str = ""
    output_dir: str = "output"


class ProductEntry(BaseModel):
    """Product entry in YAML config."""

    category: str
    brand: str
    model: str
    default_asin: str
    search_keywords: str = ""
    marketplace_overrides: dict[str, dict[str, str]] = {}

    @field_validator("default_asin")
    @classmethod
    def validate_asin(cls, v: str) -> str:
        if len(v) != 10 or not v.isalnum():
            raise ValueError(f"Invalid ASIN format: {v}")
        return v

    def to_product(self) -> Product:
        keywords = self.search_keywords or f"{self.brand} {self.model}"
        return Product(
            category=self.category,
            brand=self.brand,
            model=self.model,
            default_asin=self.default_asin,
            search_keywords=keywords,
            marketplace_overrides=self.marketplace_overrides,
        )


class ProjectConfig(BaseModel):
    """Full project configuration."""

    project: ProjectInfo
    target_marketplaces: list[str]
    settings: Settings = Settings()
    products: list[ProductEntry]


def load_marketplace_config(path: Path) -> dict[str, MarketplaceConfig]:
    """Load marketplace definitions from YAML."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return {k: MarketplaceConfig(**v) for k, v in raw["marketplaces"].items()}


def load_project_config(path: Path) -> ProjectConfig:
    """Load project configuration from YAML."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return ProjectConfig(**raw)


def validate_config(
    project: ProjectConfig, marketplaces: dict[str, MarketplaceConfig]
) -> list[str]:
    """Validate project config against marketplace definitions. Returns list of error strings."""
    errors = []
    for mp in project.target_marketplaces:
        if mp not in marketplaces:
            errors.append(f"Marketplace '{mp}' not defined in marketplaces.yaml")
    for i, p in enumerate(project.products):
        if not p.default_asin:
            errors.append(f"Product #{i} ({p.brand} {p.model}) missing default_asin")
        for mp, override in p.marketplace_overrides.items():
            asin = override.get("asin", "")
            if asin and (len(asin) != 10 or not asin.isalnum()):
                errors.append(
                    f"Product {p.model}: invalid ASIN '{asin}' for marketplace {mp}"
                )
    return errors


def update_marketplace_override(
    config_path: Path, model: str, marketplace: str, asin: str
) -> None:
    """Update a product's marketplace_overrides in the YAML config file.

    Uses atomic temp-file write to prevent data loss on crash.
    Preserves original file content via YAML round-trip (load + dump).
    """
    import tempfile

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    for product in raw.get("products", []):
        if product.get("model") == model:
            overrides = product.setdefault("marketplace_overrides", {})
            overrides.setdefault(marketplace, {})["asin"] = asin
            break

    # Atomic write: write to temp file, then rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=config_path.parent, suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        Path(tmp_path).replace(config_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
