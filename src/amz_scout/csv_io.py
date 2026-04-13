"""CSV read/write matching established data schemas."""

import csv
from dataclasses import asdict, fields
from pathlib import Path

from amz_scout.models import CompetitiveData, PriceHistory

COMPETITIVE_FIELDS = [f.name for f in fields(CompetitiveData)]
PRICE_HISTORY_FIELDS = [f.name for f in fields(PriceHistory)]


def write_competitive_data(rows: list[CompetitiveData], path: Path) -> None:
    """Write competitive data to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COMPETITIVE_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def write_price_history(rows: list[PriceHistory], path: Path) -> None:
    """Write price history to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PRICE_HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def read_competitive_data(path: Path) -> list[CompetitiveData]:
    """Read competitive data from CSV. Handles legacy UK format (no site/available columns)."""
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            # Handle legacy UK CSV that lacks 'site' and 'available' columns
            if "site" not in raw:
                raw["site"] = _infer_site_from_path(path)
            if "available" not in raw:
                raw["available"] = "Yes"
            rows.append(CompetitiveData(**{k: raw.get(k, "") for k in COMPETITIVE_FIELDS}))
    return rows


def read_price_history(path: Path) -> list[PriceHistory]:
    """Read price history from CSV."""
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if "site" not in raw:
                raw["site"] = _infer_site_from_path(path)
            # Convert numeric fields
            parsed = {}
            _STR_FIELDS = {
                "date",
                "site",
                "category",
                "brand",
                "model",
                "asin",
                "buybox_is_amazon",
                "buybox_is_fba",
                "buybox_seller_id",
            }
            _INT_FIELDS = {"sales_rank", "monthly_sold", "seller_count", "fba_seller_count"}
            for fld in PRICE_HISTORY_FIELDS:
                val = raw.get(fld, "")
                if fld in _STR_FIELDS:
                    parsed[fld] = val
                elif fld in _INT_FIELDS:
                    parsed[fld] = (
                        int(float(val)) if val and val not in ("", "None", "N/A") else None
                    )
                else:
                    parsed[fld] = _to_float(val)
            rows.append(PriceHistory(**parsed))
    return rows


def merge_competitive(
    existing: list[CompetitiveData], new: list[CompetitiveData]
) -> list[CompetitiveData]:
    """Merge new rows into existing. New rows replace existing same-key rows."""
    new_keys = {(r.date, r.site, r.model) for r in new}
    kept = [r for r in existing if (r.date, r.site, r.model) not in new_keys]
    return kept + list(new)


def merge_price_history(
    existing: list[PriceHistory], new: list[PriceHistory]
) -> list[PriceHistory]:
    """Merge new rows into existing. New rows replace existing same-key rows."""
    new_keys = {(r.date, r.site, r.model) for r in new}
    kept = [r for r in existing if (r.date, r.site, r.model) not in new_keys]
    return kept + list(new)


def _infer_site_from_path(path: Path) -> str:
    """Infer marketplace site code from file path.

    Supports both old (data/eu/uk_*.csv) and new (data/eu/uk_*.csv) structures.
    Extracts the site code from the filename prefix (e.g., 'uk' from 'uk_competitive_data.csv').
    """
    # Try extracting from filename: uk_competitive_data.csv → UK
    stem = path.stem.split("_")[0].upper()
    if stem in ("UK", "DE", "CA", "AU"):
        return stem
    # Fallback: legacy mapping from parent directory
    parent = path.parent.name.upper()
    mapping = {"EU": "UK", "DE": "DE", "CA": "CA", "AU": "AU", "NA": "CA", "APAC": "AU"}
    return mapping.get(parent, parent)


def _to_float(val: str) -> float | None:
    """Convert string to float, returning None for empty/None/N/A."""
    if not val or val in ("", "None", "N/A", "-"):
        return None
    try:
        return float(val)
    except ValueError:
        return None
