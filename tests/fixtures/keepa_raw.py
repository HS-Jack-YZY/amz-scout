"""Synthetic Keepa raw JSON fixture for CI-portable tests.

Mimics the structure of a real Keepa /product response with minimal data:
- csv[] arrays for price time series (indices 0-35)
- monthlySoldHistory
- salesRanks (2 categories)
- couponHistory (2 entries)
- deals
- buyBoxSellerIdHistory
- Product metadata (brand, title, model, etc.)
"""

# Keepa timestamps (minutes since 2011-01-01)
# These represent dates around March-April 2026
_TS_BASE = 8_000_000  # ~2026-03-15

ASIN = "B0F2MR53D6"
SITE = "UK"


def make_raw_keepa_product() -> dict:
    """Create a minimal but realistic Keepa raw product JSON."""
    # csv[0] = AMAZON, csv[1] = NEW, csv[3] = SALES_RANK, csv[7] = NEW_COUNT,
    # csv[16] = RATING, csv[17] = COUNT_REVIEWS
    csv = [None] * 36

    # Amazon price (csv[0]): 3 data points in cents
    csv[0] = [
        _TS_BASE, 11729,
        _TS_BASE + 1440, 11999,
        _TS_BASE + 2880, 11729,
    ]

    # New price (csv[1]): 3 data points
    csv[1] = [
        _TS_BASE, 11729,
        _TS_BASE + 1440, 12499,
        _TS_BASE + 2880, 11500,
    ]

    # Sales rank (csv[3]): 4 data points
    csv[3] = [
        _TS_BASE, 2591,
        _TS_BASE + 720, 2100,
        _TS_BASE + 1440, 3050,
        _TS_BASE + 2160, 2800,
    ]

    # New offer count (csv[7]): 2 data points
    csv[7] = [
        _TS_BASE, 5,
        _TS_BASE + 1440, 6,
    ]

    # Rating (csv[16]): 2 data points (value = rating * 10, so 46 = 4.6)
    csv[16] = [
        _TS_BASE, 46,
        _TS_BASE + 1440, 46,
    ]

    # Review count (csv[17]): 2 data points
    csv[17] = [
        _TS_BASE, 1100,
        _TS_BASE + 1440, 1117,
    ]

    # monthlySoldHistory: [ts, units, ts, units, ...]
    monthly_sold = [
        _TS_BASE, 150,
        _TS_BASE + 43200, 180,  # ~1 month later
    ]

    # salesRanks: dict of category_id → [ts, rank, ...]
    sales_ranks = {
        "11052681": [
            _TS_BASE, 2591,
            _TS_BASE + 1440, 2100,
        ],
        "430568031": [
            _TS_BASE, 45,
            _TS_BASE + 1440, 38,
        ],
    }

    # couponHistory: [ts, discount_type, discount_value, ...]  (triples)
    coupon_history = [
        _TS_BASE, 0, 500,       # 500 cents off
        _TS_BASE + 1440, 1, 5,  # 5% off
    ]

    # buyBoxSellerIdHistory: [ts, seller_id, ...]
    buybox_seller_history = [
        _TS_BASE, "A3P5ROKL5A1OLE",
        _TS_BASE + 1440, "A3P5ROKL5A1OLE",
    ]

    # deals (Keepa format: startTime/endTime as ints, dealType, accessType)
    deals = [
        {
            "startTime": _TS_BASE,
            "endTime": _TS_BASE + 2880,
            "dealType": "COUNTDOWN_ENDS_IN",
            "accessType": "ALL",
            "badge": "deal-of-the-day",
            "percentClaimed": 45,
        }
    ]

    return {
        "asin": ASIN,
        "title": "GL.iNet GL-BE3600 (Slate 7) WiFi 7 Travel Router",
        "brand": "GL.iNet",
        "model": "GL-BE3600",
        "manufacturer": "GL.iNet",
        "productGroup": "Network Device",
        "partNumber": "GL-BE3600",
        "binding": "Electronics",
        "type": "SINGLE_PRODUCT",
        "color": "Black",
        "size": None,
        "rootCategory": 340832031,
        "categoryTree": [
            {"catId": 340832031, "name": "Electronics"},
            {"catId": 11052681, "name": "Routers"},
        ],
        "categories": [11052681, 430568031],
        "salesRankReference": 11052681,
        "eanList": ["0850018166010"],
        "upcList": ["850018166010"],
        "listedSince": _TS_BASE - 50000,
        "trackingSince": _TS_BASE - 40000,
        "imagesCSV": "41abc.jpg,51def.jpg,61ghi.jpg",
        "features": [
            "WiFi 7 Travel Router",
            "2.5G WAN Port",
            "OpenWrt pre-installed",
        ],
        "includedComponents": "Router, USB-C Cable, Travel Pouch",
        "specialFeatures": ["VPN Client", "AdGuard Home"],
        "recommendedUsesForProduct": "Travel, Home",
        "itemWeight": 210,
        "itemHeight": 12,
        "itemLength": 98,
        "itemWidth": 74,
        "packageWeight": 380,
        "packageHeight": 45,
        "packageLength": 140,
        "packageWidth": 120,
        "hasReviews": True,
        "isAdultProduct": False,
        "isSNS": False,
        "newPriceIsMAP": False,
        "referralFeePercentage": 8,
        "availabilityAmazon": 0,
        "buyBoxEligibleOfferCounts": [{"condition": 0, "count": 5}],
        "lastUpdate": _TS_BASE + 2880,
        "lastPriceChange": _TS_BASE + 2880,
        "lastRatingUpdate": _TS_BASE + 1440,
        "lastSoldUpdate": _TS_BASE + 43200,
        "fbaFees": {
            "pickAndPackFee": 375,
            "lastUpdate": _TS_BASE,
        },
        "csv": csv,
        "monthlySoldHistory": monthly_sold,
        "salesRanks": sales_ranks,
        "couponHistory": coupon_history,
        "buyBoxSellerIdHistory": buybox_seller_history,
        "deals": deals,
    }
