"""CamelCamelCamel price history extraction (fallback when Keepa unavailable).

Uses browser-use CLI to scrape CCC product pages. Slower than Keepa API
but doesn't require tokens and covers some markets Keepa doesn't support.
"""

import logging
import re
import time

from amz_scout.browser import BrowserError, BrowserSession
from amz_scout.models import PriceHistory, Product
from amz_scout.utils import parse_price, today_iso

logger = logging.getLogger(__name__)

# CamelCamelCamel domain mapping
CCC_DOMAINS = {
    "UK": "uk.camelcamelcamel.com",
    "DE": "de.camelcamelcamel.com",
    "FR": "fr.camelcamelcamel.com",
    "IT": "it.camelcamelcamel.com",
    "ES": "es.camelcamelcamel.com",
    "CA": "ca.camelcamelcamel.com",
    "AU": "au.camelcamelcamel.com",
    # NL not supported by CCC
}

# JS to extract price table from CCC page
EXTRACT_JS = r"""(function() {
    var body = document.body.innerText;
    var lines = body.split('\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.length > 0; });
    var result = {amazon: {}, thirdPartyNew: {}, thirdPartyUsed: {}};
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.match(/^Amazon\t/)) {
            var parts = line.split('\t');
            if (parts.length >= 5) {
                result.amazon = {lowest: parts[1], highest: parts[2], current: parts[3], average: parts[4]};
            }
        }
        if (line.match(/^3rd Party New\t/) || line.match(/^Neu ab\t/) || line.match(/^Drittanbieter Neu\t/)) {
            var parts = line.split('\t');
            if (parts.length >= 5) {
                result.thirdPartyNew = {lowest: parts[1], highest: parts[2], current: parts[3], average: parts[4]};
            }
        }
        if (line.match(/^3rd Party Used\t/) || line.match(/^Drittanbieter Gebraucht\t/)) {
            var parts = line.split('\t');
            if (parts.length >= 5) {
                result.thirdPartyUsed = {lowest: parts[1], highest: parts[2], current: parts[3], average: parts[4]};
            }
        }
    }
    return JSON.stringify(result);
})()"""


def fetch_price_history_ccc(
    browser: BrowserSession,
    products: list[Product],
    site: str,
) -> list[PriceHistory]:
    """Fetch price history from CamelCamelCamel for multiple products.

    Requires an active browser session. Much slower than Keepa (one page load per product).
    """
    ccc_domain = CCC_DOMAINS.get(site)
    if not ccc_domain:
        logger.warning("[%s] CamelCamelCamel not available for this marketplace", site)
        return [_empty(p, site) for p in products]

    logger.info("[%s] Fetching CamelCamelCamel data for %d products", site, len(products))
    results = []

    for product in products:
        asin = product.asin_for(site)
        if not asin or len(asin) != 10:
            results.append(_empty(product, site))
            continue

        history = _scrape_one(browser, product, site, ccc_domain, asin)
        results.append(history)
        time.sleep(2)

    return results


def _scrape_one(
    browser: BrowserSession,
    product: Product,
    site: str,
    ccc_domain: str,
    asin: str,
) -> PriceHistory:
    """Scrape a single product's price history from CamelCamelCamel."""
    url = f"https://{ccc_domain}/product/{asin}"
    logger.debug("[%s] CCC: %s %s (%s)", site, product.brand, product.model, asin)

    try:
        browser.open(url)
        time.sleep(3)

        result = browser.evaluate(EXTRACT_JS)

        amz = result.get("amazon", {})
        new = result.get("thirdPartyNew", {})

        amz_lo = _extract_price(amz.get("lowest", ""))
        amz_hi = _extract_price(amz.get("highest", ""))
        amz_cur = _extract_price(amz.get("current", ""))
        amz_avg = _extract_price(amz.get("average", ""))
        new_lo = _extract_price(new.get("lowest", ""))
        new_hi = _extract_price(new.get("highest", ""))
        new_cur = _extract_price(new.get("current", ""))
        new_avg = _extract_price(new.get("average", ""))

        # Derive Buy Box from Amazon or New 3P
        bb_cur = amz_cur or new_cur
        bb_lo = _min_none(amz_lo, new_lo)
        bb_hi = _max_none(amz_hi, new_hi)
        bb_avg = amz_avg or new_avg

        has_data = any(v is not None for v in [amz_lo, amz_hi, new_lo, new_hi])
        if has_data:
            logger.debug("[%s] CCC: %s amz=%s/%s new=%s/%s",
                          site, product.model, amz_lo, amz_hi, new_lo, new_hi)

        return PriceHistory(
            date=today_iso(), site=site, category=product.category,
            brand=product.brand, model=product.model, asin=asin,
            buybox_current=bb_cur, buybox_lowest=bb_lo,
            buybox_highest=bb_hi, buybox_avg90=bb_avg,
            amz_current=amz_cur, amz_lowest=amz_lo,
            amz_highest=amz_hi, amz_avg90=amz_avg,
            new_current=new_cur, new_lowest=new_lo,
            new_highest=new_hi, new_avg90=new_avg,
        )

    except BrowserError as e:
        logger.warning("[%s] CCC error for %s: %s", site, product.model, e)
        return _empty(product, site)


def _extract_price(s: str) -> float | None:
    """Extract price value from CCC string like '£84.90 (Dec 11, 2025)' or '83,99€'."""
    if not s or s.strip() == "-":
        return None
    # Remove date portion in parentheses
    price_part = re.sub(r"\([^)]*\)", "", s).strip()
    return parse_price(price_part)


def _empty(product: Product, site: str) -> PriceHistory:
    return PriceHistory(
        date=today_iso(), site=site, category=product.category,
        brand=product.brand, model=product.model, asin=product.asin_for(site),
    )


def _min_none(a: float | None, b: float | None) -> float | None:
    if a is None: return b
    if b is None: return a
    return min(a, b)


def _max_none(a: float | None, b: float | None) -> float | None:
    if a is None: return b
    if b is None: return a
    return max(a, b)
