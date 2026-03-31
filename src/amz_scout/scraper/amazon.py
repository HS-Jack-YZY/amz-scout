"""Amazon product page data extraction."""

import logging
import time

from amz_scout.browser import BrowserError, BrowserSession
from amz_scout.config import MarketplaceConfig
from amz_scout.models import CompetitiveData, Product
from amz_scout.utils import today_iso

logger = logging.getLogger(__name__)

# JavaScript extraction with multiple fallback selectors (proven in manual workflow)
EXTRACT_JS = r"""(function() {
    var r = {};
    r.title = (document.getElementById('productTitle') || {}).innerText?.trim() || '';
    var body = document.body.innerText || '';

    // Detect non-product pages (404, blank, CAPTCHA, new US layout with no content)
    if (!r.title) {
        var isNotFound = body.indexOf('not a functioning page') !== -1
            || body.indexOf('nicht funktionierend') !== -1
            || body.indexOf('looking for') !== -1
            || body.indexOf('robot') !== -1
            || body.indexOf('CAPTCHA') !== -1
            || body.indexOf('Continue shopping') !== -1
            || body.length < 500;
        if (isNotFound) return JSON.stringify({error: 'not_found'});
    }

    // Price — multiple selectors in priority order
    var priceSelectors = [
        '#corePrice_feature_div .a-price .a-offscreen',
        '.a-price .a-offscreen',
        '#priceblock_ourprice',
        '#price_inside_buybox',
        '.a-color-price'
    ];
    r.price = '';
    for (var k = 0; k < priceSelectors.length; k++) {
        var el = document.querySelector(priceSelectors[k]);
        if (el && el.innerText.trim()) { r.price = el.innerText.trim(); break; }
    }
    if (!r.price) {
        var unavail = document.body.innerText.includes('Currently unavailable')
            || document.body.innerText.includes('Derzeit nicht verfügbar');
        r.price = unavail ? 'Currently unavailable' : 'N/A';
    }

    // Rating
    r.rating = (document.querySelector('#acrPopover .a-icon-alt') || {}).innerText?.trim() || 'N/A';

    // Review count
    r.reviewCount = (document.getElementById('acrCustomerReviewText') || {}).innerText?.trim() || 'N/A';

    // Monthly sales
    r.boughtPastMonth = (document.getElementById('social-proofing-faceout-title-tk_bought') || {}).innerText?.trim() || 'N/A';

    // BSR — search in product details (English + German)
    r.bsr = '';
    var rows = document.querySelectorAll(
        '#productDetails_detailBullets_sections1 tr, '
        + '#detailBullets_feature_div li, '
        + '.prodDetTable tr, '
        + '#detailBulletsWrapper_feature_div li'
    );
    for (var i = 0; i < rows.length; i++) {
        var t = rows[i].innerText;
        if (t && (t.indexOf('Best Sellers Rank') !== -1
            || t.indexOf('Amazon Bestsellers Rank') !== -1
            || t.indexOf('Amazon Bestseller-Rang') !== -1)) {
            r.bsr = t.replace(/Best Sellers Rank|Amazon Bestsellers Rank|Amazon Bestseller-Rang/g, '').trim();
            break;
        }
    }
    if (!r.bsr) {
        var s = document.getElementById('SalesRank');
        if (s) r.bsr = s.innerText.replace(/Best Sellers Rank|Amazon Bestseller-Rang/g, '').trim();
    }
    if (!r.bsr) r.bsr = 'N/A';

    // ASIN from URL
    var m = window.location.href.match(/\/dp\/([A-Z0-9]{10})/);
    r.asin = m ? m[1] : '';

    // ─── Inventory data ───
    var availEl = document.querySelector('#availability span, #availability');
    r.stockStatus = availEl ? availEl.innerText.trim() : '';
    var stockMatch = (r.stockStatus || '').match(/(\d+)\s+left/i);
    r.stockCount = stockMatch ? stockMatch[1] : '';

    // Sold by
    var soldByEl = document.querySelector('#tabular-buybox-truncate-0 span, #merchant-info a, #sellerProfileTriggerId');
    r.soldBy = soldByEl ? soldByEl.innerText.trim() : '';

    // Other offers — clean up price formatting artifacts
    var offersEl = document.querySelector('#olpLinkWidget_feature_div a, #mbc-action-panel-wrapper a');
    if (offersEl) {
        var rawOffers = offersEl.innerText.trim().replace(/\n/g, ' ').replace(/\s+/g, ' ');
        // Remove duplicated price like "£81.78 £81 . 78" → "£81.78"
        rawOffers = rawOffers.replace(/([£€$¥]\d[\d,.]+)\s+[£€$¥]?\d+\s*\.\s*\d+/g, '$1');
        // Remove "& FREE Delivery ."
        rawOffers = rawOffers.replace(/\s*&\s*FREE\s+Delivery\s*\.?/gi, '').trim();
        r.otherOffers = rawOffers;
    } else {
        r.otherOffers = '';
    }

    // ─── Listing quality data ───

    // Coupon / promotion
    var couponEl = document.querySelector('#couponBadgeRegularVpc, #vpcButton, [class*=couponBadge]');
    r.coupon = couponEl ? couponEl.innerText.trim().replace(/\n/g, ' ').replace(/\s+/g, ' ') : '';

    // Prime badge
    r.isPrime = !!(document.querySelector('#prime-badge, .prime-badge, i.a-icon-prime, [class*=prime-icon]'));

    // Rating distribution (1-5 star percentages)
    var starDist = {};
    // Method 1: search for "X star  Y%" pattern in review sections
    var reviewSections = document.querySelectorAll('#cm_cr-review_list, #reviewsMedley, [data-hook], #histogramTable');
    reviewSections.forEach(function(el) {
        var matches = (el.innerText || '').match(/(\d)\s*star\s+(\d+%)/gi);
        if (matches) matches.forEach(function(m) {
            var parts = m.match(/(\d)\s*star\s*(\d+%)/i);
            if (parts) starDist[parts[1] + '_star'] = parts[2];
        });
    });
    // Method 2: aria-label on histogram links
    if (Object.keys(starDist).length === 0) {
        document.querySelectorAll('a[class*=histogram]').forEach(function(a) {
            var label = a.getAttribute('aria-label') || '';
            var m = label.match(/(\d)\s*star.*?(\d+)/i);
            if (m) starDist[m[1] + '_star'] = m[2] + '%';
        });
    }
    r.starDistribution = JSON.stringify(starDist);

    // Image count
    var images = document.querySelectorAll('#altImages .a-button-thumbnail, #altImages li.item');
    r.imageCount = images.length || '';

    // Q&A count — search all links for "answered" pattern
    r.qaCount = '';
    var allLinks = document.querySelectorAll('a');
    for (var q = 0; q < allLinks.length; q++) {
        var qtext = allLinks[q].innerText.trim();
        if (qtext.match(/\d+\s*answered/i)) { r.qaCount = qtext; break; }
        var qhref = allLinks[q].href || '';
        if (qhref.includes('ask/questions') && qtext) { r.qaCount = qtext; break; }
    }

    // FBA / FBM — search body text for dispatch/fulfillment info
    var bodyLower = body.toLowerCase();
    if (bodyLower.includes('dispatches from amazon') || bodyLower.includes('ships from amazon')
        || bodyLower.includes('fulfilled by amazon')) {
        r.fulfillment = 'FBA';
    } else if (bodyLower.includes('dispatches from') || bodyLower.includes('ships from')) {
        r.fulfillment = 'FBM';
    } else {
        r.fulfillment = '';
    }

    return JSON.stringify(r);
})()"""


def scrape_product_page(
    browser: BrowserSession,
    product: Product,
    site: str,
    config: MarketplaceConfig,
) -> CompetitiveData | None:
    """Extract competitive data from an Amazon product page.

    Returns CompetitiveData on success, None if product not found.
    """
    asin = product.asin_for(site)
    url = f"https://www.{config.amazon_domain}/dp/{asin}"
    logger.info("[%s] Scraping %s %s (ASIN: %s)", site, product.brand, product.model, asin)

    try:
        browser.open(url)
        time.sleep(3)

        # Handle Amazon bot detection ("Continue shopping" interstitial)
        _dismiss_interstitials(browser)

        result = browser.evaluate(EXTRACT_JS)

        if result.get("error") == "not_found":
            logger.info("[%s] ASIN %s not found on this marketplace", site, asin)
            return None

        available = "Yes"
        price = result.get("price", "N/A")
        if "unavailable" in price.lower() or "nicht verfügbar" in price.lower():
            available = "Out of stock"

        return CompetitiveData(
            date=today_iso(),
            site=site,
            category=product.category,
            brand=product.brand,
            model=product.model,
            asin=result.get("asin", asin),
            title=result.get("title", ""),
            price=price,
            rating=result.get("rating", "N/A"),
            review_count=result.get("reviewCount", "N/A"),
            bought_past_month=result.get("boughtPastMonth", "N/A"),
            bsr=result.get("bsr", "N/A"),
            available=available,
            url=url,
            stock_status=result.get("stockStatus", ""),
            stock_count=result.get("stockCount", ""),
            sold_by=result.get("soldBy", ""),
            other_offers=result.get("otherOffers", ""),
            coupon=result.get("coupon", ""),
            is_prime=str(result.get("isPrime", False)),
            star_distribution=result.get("starDistribution", ""),
            image_count=str(result.get("imageCount", "")),
            qa_count=result.get("qaCount", ""),
            fulfillment=result.get("fulfillment", ""),
        )

    except BrowserError as e:
        logger.error("[%s] Error scraping %s: %s", site, product.model, e)
        return None


def _dismiss_interstitials(browser: BrowserSession) -> None:
    """Handle Amazon bot detection and cookie consent pages."""
    try:
        result = browser.evaluate(r"""(function() {
            var body = document.body.innerText || '';
            var actions = [];

            // Bot detection: "Click the button below to continue shopping"
            if (body.includes('continue shopping') || body.includes('Continue shopping')) {
                var btn = document.querySelector('input[type=submit], a.a-button-text, a[href="/"]');
                if (!btn) btn = document.querySelector('a');
                if (btn) { btn.click(); actions.push('clicked_continue'); }
            }

            // Cookie consent
            var cookie = document.querySelector('#sp-cc-accept');
            if (cookie) { cookie.click(); actions.push('accepted_cookies'); }

            return JSON.stringify({actions: actions});
        })()""")

        if result.get("actions") and "clicked_continue" in result["actions"]:
            logger.info("Dismissed Amazon bot detection page")
            time.sleep(3)  # Wait for redirect after clicking continue

    except BrowserError:
        pass
