"""Amazon search for cross-marketplace ASIN resolution + auto-writeback."""

import logging
import time
from pathlib import Path

from amz_scout.browser import BrowserError, BrowserSession
from amz_scout.config import MarketplaceConfig, update_marketplace_override
from amz_scout.models import Product

logger = logging.getLogger(__name__)


def resolve_asin_via_search(
    browser: BrowserSession,
    product: Product,
    site: str,
    config: MarketplaceConfig,
    config_path: Path | None = None,
) -> str | None:
    """Search Amazon for a product and return the matching ASIN.

    If found and config_path is provided, auto-writes the ASIN back to the YAML config.
    Returns the found ASIN or None.
    """
    keywords = product.search_keywords or f"{product.brand} {product.model}"
    model_key = _extract_model_key(product.model)

    logger.info("[%s] Searching for '%s' (key: %s)", site, keywords, model_key)

    search_url = f"https://www.{config.amazon_domain}/s?k={keywords.replace(' ', '+')}"

    try:
        browser.open(search_url)
        time.sleep(3)

        # Dismiss cookie popup if it reappears
        try:
            browser.evaluate("document.querySelector('#sp-cc-accept')?.click()")
        except BrowserError:
            pass
        time.sleep(0.5)

        # Search for the product by matching model key in title
        js = f"""(function() {{
            var target = "{model_key}".toLowerCase();
            var results = document.querySelectorAll('[data-asin]');
            for (var i = 0; i < results.length; i++) {{
                var asin = results[i].getAttribute('data-asin');
                if (!asin || asin.length !== 10) continue;
                var titleEl = results[i].querySelector('h2 a span, .a-text-normal');
                var title = titleEl ? titleEl.innerText.trim() : '';
                if (title.toLowerCase().indexOf(target) !== -1) {{
                    return JSON.stringify({{asin: asin, title: title, found: true}});
                }}
            }}
            return JSON.stringify({{found: false}});
        }})()"""

        result = browser.evaluate(js)

        if result.get("found") and result.get("asin"):
            found_asin = result["asin"]
            found_title = result.get("title", "")[:80]
            logger.info("[%s] Found ASIN %s: %s", site, found_asin, found_title)

            # Auto-writeback to YAML config
            if config_path:
                update_marketplace_override(config_path, product.model, site, found_asin)
                logger.info("[%s] Auto-saved ASIN %s to config", site, found_asin)

            return found_asin

        logger.info("[%s] No matching product found for '%s'", site, keywords)
        return None

    except BrowserError as e:
        logger.error("[%s] Search error: %s", site, e)
        return None


def _extract_model_key(model: str) -> str:
    """Extract the distinctive part of a model name for title matching.

    'RT-BE58' → 'RT-BE58'
    'TL-WR3602BE (BE3600)' → 'WR3602BE'
    'Archer BE550 (BE9300)' → 'BE550'
    'Nighthawk RS300 (BE9300)' → 'RS300'
    'GL-Beryl 7 (GL-MT3600BE)' → 'MT3600BE'
    """
    # If parenthesized part contains a model number, prefer it
    import re

    paren = re.search(r"\(([^)]+)\)", model)
    if paren:
        inner = paren.group(1)
        # Look for an alphanumeric model number like GL-MT3600BE, BE9300
        m = re.search(r"[A-Z]{1,3}[-]?[A-Z]*\d{2,}[A-Z]*", inner)
        if m:
            return m.group(0)

    # Otherwise use the model name before any parenthesis
    base = model.split("(")[0].strip()
    # Take the last word that looks like a model number
    parts = base.split()
    for p in reversed(parts):
        if re.search(r"\d{2,}", p):
            return p.lstrip("-")
    return base
