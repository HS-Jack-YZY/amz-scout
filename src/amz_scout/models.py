"""Immutable data models for amz-scout."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Product:
    """A product to track across Amazon marketplaces."""

    category: str
    brand: str
    model: str
    default_asin: str
    search_keywords: str = ""
    marketplace_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def asin_for(self, marketplace: str) -> str:
        """Return marketplace-specific ASIN, falling back to default."""
        override = self.marketplace_overrides.get(marketplace, {})
        return override.get("asin", self.default_asin)

    def note_for(self, marketplace: str) -> str | None:
        """Return any warning note for this marketplace (e.g., 'AU ASIN is a USB adapter')."""
        return self.marketplace_overrides.get(marketplace, {}).get("note")


@dataclass(frozen=True)
class CompetitiveData:
    """Current Amazon product page data for one product on one marketplace."""

    date: str
    site: str
    category: str
    brand: str
    model: str
    asin: str
    title: str
    price: str
    rating: str
    review_count: str
    bought_past_month: str
    bsr: str
    available: str
    url: str
    # Inventory fields
    stock_status: str = ""        # "In stock", "Only 2 left in stock", "Currently unavailable"
    stock_count: str = ""         # "2", "6", "" (empty = plenty or unavailable)
    sold_by: str = ""             # Seller name
    other_offers: str = ""        # "New & Used (26) from £81.78"


@dataclass(frozen=True)
class PriceHistory:
    """Keepa API price history for one product on one marketplace."""

    date: str
    site: str
    category: str
    brand: str
    model: str
    asin: str
    # Buy Box (what the customer actually sees — most important)
    buybox_current: float | None = None
    buybox_lowest: float | None = None
    buybox_highest: float | None = None
    buybox_avg90: float | None = None
    # Amazon direct
    amz_current: float | None = None
    amz_lowest: float | None = None
    amz_highest: float | None = None
    amz_avg90: float | None = None
    # 3rd party new
    new_current: float | None = None
    new_lowest: float | None = None
    new_highest: float | None = None
    new_avg90: float | None = None
    # Sales rank
    sales_rank: int | None = None


@dataclass(frozen=True)
class ScrapeResult:
    """Result of scraping one product on one marketplace."""

    product: Product
    marketplace: str
    success: bool
    competitive_data: CompetitiveData | None = None
    price_history: PriceHistory | None = None
    error: str | None = None
    screenshot_path: str | None = None
