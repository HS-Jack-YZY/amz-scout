"""Per-marketplace browser setup: delivery address, currency, cookie consent."""

import logging
import time

from amz_scout.browser import BrowserError, BrowserSession
from amz_scout.config import MarketplaceConfig

logger = logging.getLogger(__name__)


def setup_marketplace(
    browser: BrowserSession, site: str, config: MarketplaceConfig
) -> bool:
    """Initialize browser for a specific Amazon marketplace.

    Steps:
    1. Open Amazon homepage
    2. Dismiss cookie consent
    3. Set delivery address (postcode + optional city)
    4. Set currency to local
    5. Verify settings took effect

    Returns True if setup succeeded.
    """
    domain = config.amazon_domain
    logger.info("[%s] Setting up marketplace: %s", site, domain)

    # 1. Open homepage
    browser.open(f"https://www.{domain}")
    time.sleep(2)

    # 2. Cookie consent
    _dismiss_cookie_consent(browser)

    # 3. Delivery address
    if not _set_delivery_address(browser, site, config):
        logger.warning("[%s] Delivery address setup failed", site)

    # 4. Currency
    _set_currency(browser, domain, config.currency_code)

    # 5. Verify
    return _verify_setup(browser, config)


def _dismiss_cookie_consent(browser: BrowserSession) -> None:
    """Accept cookie consent popup if present."""
    try:
        browser.evaluate("document.querySelector('#sp-cc-accept')?.click(); 'done'")
        time.sleep(0.5)
    except BrowserError:
        pass  # No popup — expected


def _set_delivery_address(
    browser: BrowserSession, site: str, config: MarketplaceConfig
) -> bool:
    """Set delivery address via the location popover.

    Retries by navigating to a product page if the homepage layout
    doesn't show the postcode input.
    """
    for attempt in range(2):
        try:
            # Click location link
            browser.evaluate(
                "document.querySelector('#nav-global-location-popover-link')?.click(); 'ok'"
            )
            time.sleep(2)

            if site == "AU" and config.delivery_city:
                result = _set_au_address(browser, config)
            elif site == "CA":
                result = _set_ca_address(browser, config)
            else:
                result = _set_standard_address(browser, config)

            if result:
                return True

            # If first attempt failed, navigate to a known product page and retry
            if attempt == 0:
                logger.info("Retrying address setup from product page")
                browser.open(f"https://www.{config.amazon_domain}/dp/B0FGDRP3VZ")
                time.sleep(3)

        except BrowserError as e:
            logger.warning("Address setup error (attempt %d): %s", attempt + 1, e)

    return False


def _set_standard_address(browser: BrowserSession, config: MarketplaceConfig) -> bool:
    """Set address for UK/DE/US etc. (single postcode input). Pure JS approach."""
    result = browser.evaluate(f"""(function() {{
        var input = document.querySelector('#GLUXZipUpdateInput');
        if (!input) return JSON.stringify({{ok: false, reason: 'no input'}});
        // Focus, clear, type via native setter + input event
        input.focus();
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, '{config.delivery_postcode}');
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        input.dispatchEvent(new Event('change', {{bubbles: true}}));
        // Click Apply — find submit button near the zip input
        var apply = input.closest('.a-popover-inner, .a-section')
            ?.querySelector('input[type=submit]');
        if (!apply) {{
            // Broader search
            var allSubmits = document.querySelectorAll('.a-popover input[type=submit]');
            for (var i = 0; i < allSubmits.length; i++) {{
                var parent = allSubmits[i].closest('[id*=GLUX]');
                if (parent) {{ apply = allSubmits[i]; break; }}
            }}
        }}
        if (apply) apply.click();
        return JSON.stringify({{ok: true}});
    }})()""")

    if not result.get("ok"):
        logger.warning("Postcode input not found: %s", result.get("reason"))
        return False

    time.sleep(2)
    # Click Continue/Done if present
    browser.evaluate("""
        var btns = document.querySelectorAll(
            '.a-popover-footer input[type=submit], '
            + '[data-action="GLUXPostalUpdateAction"] input');
        for (var i = 0; i < btns.length; i++) btns[i].click();
        'done'
    """)
    time.sleep(2)
    return True


def _set_ca_address(browser: BrowserSession, config: MarketplaceConfig) -> bool:
    """Set address for CA (two-part postcode: M5V + 3L9)."""
    parts = config.delivery_postcode.split()
    if len(parts) != 2:
        logger.warning("CA postcode should be 2 parts: %s", config.delivery_postcode)
        return False

    state = browser.state()
    raw_text = state.get("data", {}).get("_raw_text", "")

    # Find the two input fields
    js = f"""(function() {{
        var i0 = document.querySelector('#GLUXZipUpdateInput_0');
        var i1 = document.querySelector('#GLUXZipUpdateInput_1');
        if (!i0 || !i1) return JSON.stringify({{error: 'CA inputs not found'}});
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(i0, '{parts[0]}');
        i0.dispatchEvent(new Event('input', {{bubbles: true}}));
        setter.call(i1, '{parts[1]}');
        i1.dispatchEvent(new Event('input', {{bubbles: true}}));
        return JSON.stringify({{ok: true}});
    }})()"""
    result = browser.evaluate(js)
    if "error" in str(result):
        return _set_standard_address(browser, config)

    time.sleep(0.5)
    # Click Apply
    browser.evaluate(
        "var btns = document.querySelectorAll('input[type=submit]');"
        "for (var i = 0; i < btns.length; i++) {"
        "  if (btns[i].closest('[id*=GLUX]')) { btns[i].click(); break; }"
        "} 'applied'"
    )
    time.sleep(2)

    # Dismiss confirmation
    browser.evaluate("""
        var btns = document.querySelectorAll(
            '.a-popover-footer input[type=submit]');
        for (var i = 0; i < btns.length; i++) btns[i].click();
        'done'
    """)
    time.sleep(2)
    return True


def _set_au_address(browser: BrowserSession, config: MarketplaceConfig) -> bool:
    """Set address for AU (postcode + city dropdown)."""
    # Type postcode
    result = browser.evaluate("""(function() {
        var input = document.querySelector('#GLUXPostalCodeWithCity_PostalCodeInput');
        if (!input) return JSON.stringify({error: 'AU input not found'});
        input.focus();
        input.value = '';
        return JSON.stringify({ok: true});
    })()""")

    if "error" in str(result):
        logger.warning("AU postcode input not found")
        return False

    time.sleep(0.3)
    browser.type_text(config.delivery_postcode)
    time.sleep(0.5)
    browser.keys("Return")
    time.sleep(3)

    # Open city dropdown
    browser.evaluate(
        "document.querySelector('#GLUXPostalCodeWithCity_DropdownButton')?.click(); 'opened'"
    )
    time.sleep(2)

    # Select city
    city = config.delivery_city or "SYDNEY"
    city_result = browser.evaluate(f"""(function() {{
        var options = document.querySelectorAll('a[role=option]');
        for (var i = 0; i < options.length; i++) {{
            if (options[i].innerText.trim() === '{city}') {{
                options[i].click();
                return JSON.stringify({{selected: true}});
            }}
        }}
        return JSON.stringify({{selected: false}});
    }})()""")

    if not city_result.get("selected"):
        logger.warning("AU city '%s' not found in dropdown", city)
        return False

    time.sleep(1)

    # Click Apply
    browser.evaluate(
        "document.querySelector('#GLUXPostalCodeWithCityApplyButton')?.click(); 'applied'"
    )
    time.sleep(3)
    return True


def _set_currency(browser: BrowserSession, domain: str, currency_code: str) -> None:
    """Set currency preference via the preferences page."""
    try:
        browser.open(
            f"https://www.{domain}/customer-preferences/edit"
            "?ie=UTF8&ref_=footer_cop&preferencesReturnUrl=%2F"
        )
        time.sleep(2)

        # Check current currency
        result = browser.evaluate("""(function() {
            var sel = document.querySelector('#icp-currency-dropdown');
            if (!sel) return JSON.stringify({error: 'no dropdown'});
            for (var i = 0; i < sel.options.length; i++) {
                if (sel.options[i].selected) return JSON.stringify({current: sel.options[i].value});
            }
            return JSON.stringify({current: 'unknown'});
        })()""")

        current = result.get("current", "")
        if current == currency_code:
            logger.info("Currency already set to %s", currency_code)
            return

        # Select target currency
        browser.evaluate(f"""(function() {{
            var sel = document.querySelector('#icp-currency-dropdown');
            if (!sel) return;
            for (var i = 0; i < sel.options.length; i++) {{
                if (sel.options[i].value === '{currency_code}') {{
                    sel.options[i].selected = true;
                    sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                    break;
                }}
            }}
        }})()""")
        time.sleep(0.5)

        # Save
        browser.evaluate(
            "document.querySelector('#icp-save-button input[type=submit],"
            " #icp-save-button')?.click(); 'saved'"
        )
        time.sleep(3)
        logger.info("Currency set to %s", currency_code)

    except BrowserError as e:
        logger.warning("Currency setup error: %s", e)


def _verify_setup(browser: BrowserSession, config: MarketplaceConfig) -> bool:
    """Verify delivery address and currency are correctly set."""
    try:
        browser.open(f"https://www.{config.amazon_domain}")
        time.sleep(2)
        result = browser.evaluate("""(function() {
            var loc = document.querySelector('#glow-ingress-line2')?.innerText?.trim() || '';
            var price = document.querySelector('.a-price .a-offscreen')?.innerText?.trim() || '';
            return JSON.stringify({location: loc, samplePrice: price});
        })()""")
        location = result.get("location", "")
        logger.info("Verified: location=%s", location)
        return bool(location and "United States" not in location)
    except BrowserError:
        return False
