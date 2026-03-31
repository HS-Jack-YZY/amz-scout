"""Shared utility functions for amz-scout.

Parse functions extracted from the proven generate_report.py codebase.
"""

import functools
import re
import time
from datetime import date
from typing import TypeVar

T = TypeVar("T")


# ─── Price & Data Parsers ───────────────────────────────────────────


def parse_price(s: str) -> float | None:
    """Extract numeric price from strings like '£104.50', '€94,99', '$129.99', '83,99€'."""
    if not s or s == "N/A" or s.strip() == "-" or "unavailable" in s.lower():
        return None
    cleaned = re.sub(r"(?:CAD|AUD|USD|EUR|MXN|JPY|CA\$|C\$|A\$|US\$|MX\$|[£€$¥\s])", "", s.strip())
    if not cleaned or cleaned == "-":
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." not in cleaned:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            cleaned = parts[0] + "." + parts[1]
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_rating(s: str) -> float | None:
    """Extract rating from '3.8 out of 5 stars' → 3.8."""
    if not s or s == "N/A":
        return None
    m = re.search(r"(\d+\.?\d*)", s)
    return float(m.group(1)) if m else None


def parse_reviews(s: str) -> int | None:
    """Extract review count from '(1,115)' → 1115."""
    if not s or s == "N/A":
        return None
    cleaned = re.sub(r"[(),.\s]", "", s)
    m = re.search(r"(\d+)", cleaned)
    return int(m.group(1)) if m else None


def parse_bsr_routers(s: str) -> int | None:
    """Extract Routers sub-category BSR from full BSR string."""
    if not s or s == "N/A":
        return None
    m = re.search(r"#?(\d[\d,]*)\s+in\s+(?:Network\s+)?Routers?", s)
    if m:
        return int(m.group(1).replace(",", ""))
    m2 = re.search(r"\)\s+#?(\d[\d,]*)\s+in\b(?!\s*(?:Computers|Electronics|Computer))", s)
    if m2:
        return int(m2.group(1).replace(",", ""))
    m3 = re.search(r"\)\s+#?(\d[\d,]*)\s*(?:i\b|\s*$)", s)
    if m3:
        return int(m3.group(1).replace(",", ""))
    return None


def parse_monthly_sales(s: str) -> str:
    """Normalize monthly sales string."""
    if not s or s == "N/A":
        return "-"
    m = re.search(r"(\d+\+?)", s)
    return m.group(1) if m else "-"


def parse_history_price(s: str) -> tuple[float | None, str]:
    """Extract price and date from '£84.90 (Dec 11, 2025)' or '83,99€ (Mar 09, 2026)'."""
    if not s or s == "N/A" or s.strip() == "-":
        return None, ""
    date_m = re.search(r"\(([^)]+)\)", s)
    date_str = date_m.group(1) if date_m else ""
    price_part = re.sub(r"\([^)]*\)", "", s).strip()
    price = parse_price(price_part)
    return price, date_str


# ─── Keepa Helpers ──────────────────────────────────────────────────


def cents_to_price(v: int | None) -> float | None:
    """Convert Keepa cents value to price. Returns None for -1 or None."""
    if v is None or v == -1:
        return None
    return round(v / 100, 2)


# ─── General Utilities ──────────────────────────────────────────────


def today_iso() -> str:
    """Return today's date as YYYY-MM-DD."""
    return date.today().isoformat()


def retry(
    max_attempts: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Retry decorator with exponential backoff."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_attempts:
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_error  # type: ignore[misc]

        return wrapper

    return decorator


def sanitize_filename(s: str) -> str:
    """Convert string to a safe filename."""
    return re.sub(r"[^\w\-.]", "_", s).strip("_")
