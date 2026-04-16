"""SQLite database layer for amz-scout.

Design principle: store raw facts only, aggregate on demand.
- keepa_time_series: every data point from Keepa csv[], monthlySoldHistory, salesRanks
- keepa_products: product metadata snapshot (updated on each fetch)
- competitive_snapshots: browser scrape observations (one per asin/site/date)
- keepa_buybox_history, keepa_coupon_history, keepa_deals: specialized time series
"""

import json as json_mod
import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from amz_scout.models import CompetitiveData
from amz_scout.utils import parse_bsr_routers, parse_price, parse_rating, parse_reviews

logger = logging.getLogger(__name__)


# ─── Series type constants (csv[] indices 0-35, then extensions) ──────

SERIES_AMAZON = 0
SERIES_NEW = 1
SERIES_USED = 2
SERIES_SALES_RANK = 3
SERIES_LISTPRICE = 4
SERIES_COLLECTIBLE = 5
SERIES_REFURBISHED = 6
SERIES_NEW_FBM_SHIPPING = 7
SERIES_LIGHTNING_DEAL = 8
SERIES_WAREHOUSE = 9
SERIES_NEW_FBA = 10
SERIES_COUNT_NEW = 11
SERIES_COUNT_USED = 12
SERIES_COUNT_REFURBISHED = 13
SERIES_COUNT_COLLECTIBLE = 14
SERIES_EXTRA_INFO = 15
SERIES_RATING = 16
SERIES_COUNT_REVIEWS = 17
SERIES_BUY_BOX_SHIPPING = 18
SERIES_USED_NEW_SHIPPING = 19
SERIES_USED_VERY_GOOD = 20
SERIES_USED_GOOD = 21
SERIES_USED_ACCEPTABLE = 22
SERIES_COLLECTIBLE_NEW = 23
SERIES_COLLECTIBLE_VERY_GOOD = 24
SERIES_COLLECTIBLE_GOOD = 25
SERIES_COLLECTIBLE_ACCEPTABLE = 26
SERIES_COUNT_NEW_FBM = 27
SERIES_NEW_PRICE_IS_MAP = 28
SERIES_USED_LIKE_NEW = 29
SERIES_COUNT_USED_NEW = 30
SERIES_COUNT_USED_VERY_GOOD = 31
SERIES_COUNT_USED_GOOD = 32
SERIES_COUNT_USED_ACCEPTABLE = 33
SERIES_COUNT_COLLECTIBLE_NEW = 34
SERIES_TRADE_IN = 35

SERIES_MONTHLY_SOLD = 100
SERIES_SALES_RANK_BASE = 200  # 200 + category_index in salesRanks dict

SERIES_NAMES = {
    0: "AMAZON",
    1: "NEW",
    2: "USED",
    3: "SALES_RANK",
    4: "LISTPRICE",
    5: "COLLECTIBLE",
    6: "REFURBISHED",
    7: "NEW_FBM_SHIPPING",
    8: "LIGHTNING_DEAL",
    9: "WAREHOUSE",
    10: "NEW_FBA",
    11: "COUNT_NEW",
    12: "COUNT_USED",
    13: "COUNT_REFURBISHED",
    14: "COUNT_COLLECTIBLE",
    15: "EXTRA_INFO",
    16: "RATING",
    17: "COUNT_REVIEWS",
    18: "BUY_BOX_SHIPPING",
    19: "USED_NEW_SHIPPING",
    20: "USED_VERY_GOOD",
    21: "USED_GOOD",
    22: "USED_ACCEPTABLE",
    23: "COLLECTIBLE_NEW",
    24: "COLLECTIBLE_VERY_GOOD",
    25: "COLLECTIBLE_GOOD",
    26: "COLLECTIBLE_ACCEPTABLE",
    27: "COUNT_NEW_FBM",
    28: "NEW_PRICE_IS_MAP",
    29: "USED_LIKE_NEW",
    30: "COUNT_USED_NEW",
    31: "COUNT_USED_VERY_GOOD",
    32: "COUNT_USED_GOOD",
    33: "COUNT_USED_ACCEPTABLE",
    34: "COUNT_COLLECTIBLE_NEW",
    35: "TRADE_IN",
    100: "MONTHLY_SOLD",
}

SCHEMA_VERSION = 4

# ─── Connection management ────────────────────────────────────────────


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open or create a SQLite database with optimized settings."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -32000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 134217728")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


@contextmanager
def open_db(db_path: Path):
    """Context manager for DB connection lifecycle."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def resolve_db_path(output_dir: str | None = None) -> Path:
    """Resolve the shared database path.

    The DB lives at ``output/amz_scout.db`` (one level above per-project dirs).
    If *output_dir* is an explicit project path like ``output/BE10000``, the DB
    is placed in its parent (``output/``).
    """
    if output_dir:
        return Path(output_dir).parent / "amz_scout.db"
    return Path("output") / "amz_scout.db"


_schema_initialized: set[str] = set()  # cache: file-backed db paths that passed migration check


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't exist. Idempotent."""
    db_path = conn.execute("PRAGMA database_list").fetchone()[2] or ""
    # Only cache file-backed databases; skip :memory: and temp DBs
    if db_path and db_path in _schema_initialized:
        return

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    )
    if cur.fetchone() is not None:
        _migrate(conn)
        if db_path:
            _schema_initialized.add(db_path)
        return  # Schema exists — migrations applied if needed

    conn.executescript(_SCHEMA_SQL)
    if db_path:
        _schema_initialized.add(db_path)
    logger.info("Database schema initialized (version %d)", SCHEMA_VERSION)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations."""
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    current = row["v"] if row and row["v"] is not None else 0

    if current >= SCHEMA_VERSION:
        return

    try:
        with conn:
            if current < 2:
                # v2: add project column to competitive_snapshots
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(competitive_snapshots)")]
                if "project" not in cols:
                    conn.execute(
                        "ALTER TABLE competitive_snapshots "
                        "ADD COLUMN project TEXT NOT NULL DEFAULT ''"
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, description) "
                    "VALUES (2, 'add project column to competitive_snapshots')"
                )
                logger.info("Migrated schema to version 2")

            if current < 3:
                # v3: add product registry tables
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "products" not in tables:
                    conn.execute("""
                        CREATE TABLE products (
                            id              INTEGER PRIMARY KEY AUTOINCREMENT,
                            category        TEXT NOT NULL,
                            brand           TEXT NOT NULL,
                            model           TEXT NOT NULL,
                            search_keywords TEXT NOT NULL DEFAULT '',
                            created_at      TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                            updated_at      TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                            UNIQUE(brand, model)
                        )
                    """)
                    conn.execute("""
                        CREATE TABLE product_asins (
                            product_id      INTEGER NOT NULL
                                REFERENCES products(id) ON DELETE CASCADE,
                            marketplace     TEXT NOT NULL,
                            asin            TEXT NOT NULL,
                            status          TEXT NOT NULL DEFAULT 'unverified'
                                CHECK(status IN (
                                    'unverified','verified','wrong_product',
                                    'not_listed','unavailable'
                                )),
                            notes           TEXT NOT NULL DEFAULT '',
                            last_checked    TEXT,
                            created_at      TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                            updated_at      TEXT NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                            PRIMARY KEY (product_id, marketplace)
                        )
                    """)
                    conn.execute("CREATE INDEX idx_pa_asin ON product_asins(asin)")
                    conn.execute("""
                        CREATE TABLE product_tags (
                            product_id  INTEGER NOT NULL
                                REFERENCES products(id) ON DELETE CASCADE,
                            tag         TEXT NOT NULL,
                            PRIMARY KEY (product_id, tag)
                        ) WITHOUT ROWID
                    """)
                    conn.execute("CREATE INDEX idx_pt_tag ON product_tags(tag)")
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, description) "
                    "VALUES (3, 'add product registry tables')"
                )
                logger.info("Migrated schema to version 3")

            if current < 4:
                # v4: add fetch_mode to keepa_products
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(keepa_products)")]
                if "fetch_mode" not in cols:
                    conn.execute(
                        "ALTER TABLE keepa_products "
                        "ADD COLUMN fetch_mode TEXT NOT NULL DEFAULT 'basic'"
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, description) "
                    "VALUES (4, 'add fetch_mode to keepa_products')"
                )
                logger.info("Migrated schema to version 4")
    except Exception:
        logger.exception(
            "Schema migration failed at version %d. Database may need manual repair.",
            current,
        )
        raise


_SCHEMA_SQL = """
CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO schema_migrations (version, description) VALUES (1, 'initial schema');
INSERT INTO schema_migrations (version, description)
    VALUES (2, 'add project column to competitive_snapshots');
INSERT INTO schema_migrations (version, description) VALUES (3, 'add product registry tables');
INSERT INTO schema_migrations (version, description) VALUES (4, 'add fetch_mode to keepa_products');

CREATE TABLE competitive_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at      TEXT NOT NULL,
    site            TEXT NOT NULL,
    category        TEXT NOT NULL,
    brand           TEXT NOT NULL,
    model           TEXT NOT NULL,
    asin            TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    price_cents     INTEGER,
    currency        TEXT NOT NULL DEFAULT '',
    rating          REAL,
    review_count    INTEGER,
    bought_past_month INTEGER,
    bsr             INTEGER,
    available       INTEGER NOT NULL DEFAULT 1,
    url             TEXT NOT NULL DEFAULT '',
    stock_status    TEXT NOT NULL DEFAULT '',
    stock_count     INTEGER,
    sold_by         TEXT NOT NULL DEFAULT '',
    other_offers    TEXT NOT NULL DEFAULT '',
    coupon          TEXT NOT NULL DEFAULT '',
    is_prime        INTEGER,
    star_distribution TEXT NOT NULL DEFAULT '',
    image_count     INTEGER,
    qa_count        INTEGER,
    fulfillment     TEXT NOT NULL DEFAULT '',
    price_raw       TEXT NOT NULL DEFAULT '',
    rating_raw      TEXT NOT NULL DEFAULT '',
    review_count_raw TEXT NOT NULL DEFAULT '',
    bsr_raw         TEXT NOT NULL DEFAULT '',
    project         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(asin, site, scraped_at)
);
CREATE INDEX idx_cs_site_date ON competitive_snapshots(site, scraped_at);
CREATE INDEX idx_cs_brand_model ON competitive_snapshots(brand, model);

CREATE TABLE keepa_time_series (
    asin            TEXT NOT NULL,
    site            TEXT NOT NULL,
    series_type     INTEGER NOT NULL,
    keepa_ts        INTEGER NOT NULL,
    value           INTEGER NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (asin, site, series_type, keepa_ts)
) WITHOUT ROWID;
CREATE INDEX idx_kts_site_type ON keepa_time_series(site, series_type);
CREATE INDEX idx_kts_fetched ON keepa_time_series(fetched_at, asin, site);

CREATE TABLE keepa_buybox_history (
    asin            TEXT NOT NULL,
    site            TEXT NOT NULL,
    keepa_ts        INTEGER NOT NULL,
    seller_id       TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (asin, site, keepa_ts)
) WITHOUT ROWID;
CREATE INDEX idx_kbb_seller ON keepa_buybox_history(seller_id, asin, site);

CREATE TABLE keepa_coupon_history (
    asin            TEXT NOT NULL,
    site            TEXT NOT NULL,
    keepa_ts        INTEGER NOT NULL,
    amount          INTEGER NOT NULL,
    coupon_type     INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (asin, site, keepa_ts)
) WITHOUT ROWID;

CREATE TABLE keepa_deals (
    asin            TEXT NOT NULL,
    site            TEXT NOT NULL,
    start_time      INTEGER NOT NULL,
    end_time        INTEGER,
    deal_type       TEXT NOT NULL,
    access_type     TEXT NOT NULL DEFAULT 'ALL',
    badge           TEXT NOT NULL DEFAULT '',
    percent_claimed INTEGER NOT NULL DEFAULT 0,
    deal_status     TEXT NOT NULL DEFAULT 'ACTIVE'
                    CHECK(deal_status IN ('ACTIVE', 'ENDED', 'UNKNOWN')),
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (asin, site, start_time)
) WITHOUT ROWID;

CREATE TABLE keepa_products (
    asin                TEXT NOT NULL,
    site                TEXT NOT NULL,
    title               TEXT,
    brand               TEXT,
    manufacturer        TEXT,
    model               TEXT,
    part_number         TEXT,
    binding             TEXT,
    product_group       TEXT,
    product_type        TEXT,
    color               TEXT,
    size                TEXT,
    item_weight         INTEGER,
    item_height         INTEGER,
    item_length         INTEGER,
    item_width          INTEGER,
    package_weight      INTEGER,
    package_height      INTEGER,
    package_length      INTEGER,
    package_width       INTEGER,
    features            TEXT,
    images_csv          TEXT,
    image_count         INTEGER,
    included_components TEXT,
    special_features    TEXT,
    recommended_uses    TEXT,
    root_category       INTEGER,
    category_tree       TEXT,
    categories          TEXT,
    sales_rank_ref      INTEGER,
    ean_list            TEXT,
    upc_list            TEXT,
    listed_since        INTEGER,
    tracking_since      INTEGER,
    fba_pick_pack_fee   INTEGER,
    fba_fee_updated     INTEGER,
    referral_fee_pct    REAL,
    availability_amazon INTEGER,
    has_reviews         INTEGER,
    is_adult            INTEGER,
    is_sns              INTEGER,
    new_price_is_map    INTEGER,
    buybox_eligible_counts TEXT,
    last_update         INTEGER,
    last_price_change   INTEGER,
    last_rating_update  INTEGER,
    last_sold_update    INTEGER,
    fetched_at          TEXT NOT NULL,
    fetch_mode          TEXT NOT NULL DEFAULT 'basic',
    PRIMARY KEY (asin, site)
);

CREATE TABLE products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    brand           TEXT NOT NULL,
    model           TEXT NOT NULL,
    search_keywords TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(brand, model)
);

CREATE TABLE product_asins (
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    marketplace     TEXT NOT NULL,
    asin            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'unverified'
                    CHECK(status IN (
                        'unverified','verified','wrong_product','not_listed','unavailable'
                    )),
    notes           TEXT NOT NULL DEFAULT '',
    last_checked    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (product_id, marketplace)
);
CREATE INDEX idx_pa_asin ON product_asins(asin);

CREATE TABLE product_tags (
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (product_id, tag)
) WITHOUT ROWID;
CREATE INDEX idx_pt_tag ON product_tags(tag);
"""


# ─── Write: Keepa raw JSON → DB ──────────────────────────────────────


def store_keepa_product(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
    fetch_mode: str = "basic",
) -> dict | None:
    """Parse a full Keepa product JSON and write all tables in one transaction.

    Returns a dict describing auto-registration result, or *None* if the
    ASIN was already registered.
    """
    with conn:
        _upsert_keepa_product(conn, asin, site, raw, fetched_at, fetch_mode)
        _insert_time_series(conn, asin, site, raw, fetched_at)
        _insert_buybox_history(conn, asin, site, raw, fetched_at)
        _insert_coupon_history(conn, asin, site, raw, fetched_at)
        _insert_deals(conn, asin, site, raw, fetched_at)
    return _auto_register_from_keepa(conn, asin, site, raw)


def _try_register_product(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    brand: str,
    title: str,
    model: str,
    category: str,
) -> dict | None:
    """Register a product if brand and title are present.

    Normalises inputs, falls back model → ASIN, calls ``register_product``
    + ``register_asin``.  Returns registration details or *None* when
    validation fails (empty brand/title).
    """
    brand = brand.strip()
    title = title.strip()
    model = model.strip() or asin
    category = category or "uncategorized"

    if not brand or not title:
        return None

    product_id, is_new = register_product(conn, category, brand, model)
    register_asin(conn, product_id, site, asin, status="unverified")

    logger.info(
        "%s %s/%s → product %d (%s %s)",
        "New product" if is_new else "Associated",
        site,
        asin,
        product_id,
        brand,
        model,
    )
    return {
        "product_id": product_id,
        "brand": brand,
        "model": model,
        "category": category,
        "asin": asin,
        "marketplace": site,
        "new_product": is_new,
    }


def _auto_register_from_keepa(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
) -> dict | None:
    """Auto-register a product from Keepa metadata if not already registered.

    Skips silently when the ASIN is already in ``product_asins`` for this
    marketplace, or when the Keepa data lacks *brand* or *title*.

    Returns a dict with registration details, or *None* if skipped.
    """
    existing = conn.execute(
        "SELECT product_id FROM product_asins WHERE asin = ? AND marketplace = ?",
        (asin, site),
    ).fetchone()
    if existing:
        return None

    result = _try_register_product(
        conn,
        asin,
        site,
        brand=raw.get("brand") or "",
        title=raw.get("title") or "",
        model=raw.get("model") or "",
        category=raw.get("productGroup") or "",
    )
    if result is None:
        logger.debug(
            "Skip auto-register %s/%s: missing brand or title",
            site,
            asin,
        )
    return result


def _upsert_keepa_product(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
    fetch_mode: str = "basic",
) -> None:
    fba = raw.get("fbaFees") or {}
    images = raw.get("images") or []
    conn.execute(
        """INSERT OR REPLACE INTO keepa_products (
            asin, site, title, brand, manufacturer, model, part_number,
            binding, product_group, product_type, color, size,
            item_weight, item_height, item_length, item_width,
            package_weight, package_height, package_length, package_width,
            features, images_csv, image_count, included_components,
            special_features, recommended_uses,
            root_category, category_tree, categories, sales_rank_ref,
            ean_list, upc_list, listed_since, tracking_since,
            fba_pick_pack_fee, fba_fee_updated, referral_fee_pct,
            availability_amazon, has_reviews, is_adult, is_sns, new_price_is_map,
            buybox_eligible_counts,
            last_update, last_price_change, last_rating_update, last_sold_update,
            fetched_at, fetch_mode
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?,
            ?, ?, ?, ?,
            ?, ?
        )""",
        (
            asin,
            site,
            raw.get("title"),
            raw.get("brand"),
            raw.get("manufacturer"),
            raw.get("model"),
            raw.get("partNumber"),
            raw.get("binding"),
            raw.get("productGroup"),
            raw.get("type"),
            raw.get("color"),
            raw.get("size"),
            raw.get("itemWeight"),
            raw.get("itemHeight"),
            raw.get("itemLength"),
            raw.get("itemWidth"),
            raw.get("packageWeight"),
            raw.get("packageHeight"),
            raw.get("packageLength"),
            raw.get("packageWidth"),
            _json_or_none(raw.get("features")),
            raw.get("imagesCSV"),
            len(images) if images else None,
            raw.get("includedComponents"),
            _json_or_none(raw.get("specialFeatures")),
            raw.get("recommendedUsesForProduct"),
            raw.get("rootCategory"),
            _json_or_none(raw.get("categoryTree")),
            _json_or_none(raw.get("categories")),
            raw.get("salesRankReference"),
            _json_or_none(raw.get("eanList")),
            _json_or_none(raw.get("upcList")),
            raw.get("listedSince"),
            raw.get("trackingSince"),
            fba.get("pickAndPackFee"),
            fba.get("lastUpdate"),
            raw.get("referralFeePercentage"),
            raw.get("availabilityAmazon"),
            int(raw.get("hasReviews", False)),
            int(raw.get("isAdultProduct", False)),
            int(raw.get("isSNS", False)),
            int(raw.get("newPriceIsMAP", False)),
            _json_or_none(raw.get("buyBoxEligibleOfferCounts")),
            raw.get("lastUpdate"),
            raw.get("lastPriceChange"),
            raw.get("lastRatingUpdate"),
            raw.get("lastSoldUpdate"),
            fetched_at,
            fetch_mode,
        ),
    )


def _insert_time_series(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
) -> None:
    rows: list[tuple] = []

    # csv[] arrays (indices 0-35)
    csv_data = raw.get("csv") or []
    for type_idx in range(min(len(csv_data), 36)):
        arr = csv_data[type_idx]
        if not arr:
            continue
        for i in range(0, len(arr) - 1, 2):
            ts, val = arr[i], arr[i + 1]
            if isinstance(ts, int) and isinstance(val, int):
                rows.append((asin, site, type_idx, ts, val, fetched_at))

    # monthlySoldHistory
    msh = raw.get("monthlySoldHistory") or []
    for i in range(0, len(msh) - 1, 2):
        ts, val = msh[i], msh[i + 1]
        if isinstance(ts, int) and isinstance(val, int):
            rows.append((asin, site, SERIES_MONTHLY_SOLD, ts, val, fetched_at))

    # salesRanks (dict of category_id → [ts, val, ...])
    sales_ranks = raw.get("salesRanks") or {}
    for cat_idx, (_, arr) in enumerate(sorted(sales_ranks.items())):
        series_type = SERIES_SALES_RANK_BASE + cat_idx
        for i in range(0, len(arr) - 1, 2):
            ts, val = arr[i], arr[i + 1]
            if isinstance(ts, int) and isinstance(val, int):
                rows.append((asin, site, series_type, ts, val, fetched_at))

    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO keepa_time_series "
            "(asin, site, series_type, keepa_ts, value, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def _insert_buybox_history(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
) -> None:
    bbh = raw.get("buyBoxSellerIdHistory")
    if not bbh or not isinstance(bbh, list):
        return
    rows = []
    for i in range(0, len(bbh) - 1, 2):
        ts = bbh[i]
        seller = bbh[i + 1]
        if isinstance(ts, int) and isinstance(seller, str) and seller:
            rows.append((asin, site, ts, seller, fetched_at))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO keepa_buybox_history "
            "(asin, site, keepa_ts, seller_id, fetched_at) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def _insert_coupon_history(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
) -> None:
    ch = raw.get("couponHistory")
    if not ch or not isinstance(ch, list):
        return
    rows = []
    for i in range(0, len(ch) - 2, 3):
        ts, amount, ctype = ch[i], ch[i + 1], ch[i + 2]
        if isinstance(ts, int) and isinstance(amount, int):
            rows.append(
                (asin, site, ts, amount, ctype if isinstance(ctype, int) else 0, fetched_at)
            )
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO keepa_coupon_history "
            "(asin, site, keepa_ts, amount, coupon_type, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def _insert_deals(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    raw: dict,
    fetched_at: str,
) -> None:
    deals = raw.get("deals")
    if not deals or not isinstance(deals, list):
        return
    for d in deals:
        start = d.get("startTime")
        if not isinstance(start, int):
            continue
        end = d.get("endTime")
        status = "ENDED" if isinstance(end, int) and end > 0 else "ACTIVE"
        conn.execute(
            "INSERT OR IGNORE INTO keepa_deals "
            "(asin, site, start_time, end_time, deal_type, access_type, "
            "badge, percent_claimed, deal_status, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asin,
                site,
                start,
                end if isinstance(end, int) else None,
                d.get("dealType", "UNKNOWN"),
                d.get("accessType", "ALL"),
                d.get("badge", ""),
                d.get("percentClaimed", 0),
                status,
                fetched_at,
            ),
        )


# ─── Write: CompetitiveData → DB ─────────────────────────────────────


def upsert_competitive(
    conn: sqlite3.Connection,
    rows: list[CompetitiveData],
    project: str = "",
) -> int:
    """Insert or replace competitive snapshots. Returns rows affected."""
    if not rows:
        return 0
    db_rows = [_competitive_to_db_row(r, project) for r in rows]
    with conn:
        conn.executemany(
            """INSERT OR REPLACE INTO competitive_snapshots (
                scraped_at, site, category, brand, model, asin, title,
                price_cents, currency, rating, review_count,
                bought_past_month, bsr, available, url,
                stock_status, stock_count, sold_by, other_offers,
                coupon, is_prime, star_distribution, image_count, qa_count,
                fulfillment, price_raw, rating_raw, review_count_raw, bsr_raw,
                project
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?
            )""",
            db_rows,
        )
    return len(db_rows)


def _competitive_to_db_row(r: CompetitiveData, project: str = "") -> tuple:
    """Convert CompetitiveData to a DB-ready tuple with type conversions."""
    price_val = parse_price(r.price)
    price_cents = round(price_val * 100) if price_val is not None else None

    # Extract currency symbol
    currency = ""
    if r.price and r.price not in ("N/A", "", "-"):
        for sym in ("£", "€", "$", "¥"):
            if sym in r.price:
                currency = sym
                break

    rating_val = parse_rating(r.rating)
    review_val = parse_reviews(r.review_count)
    bsr_val = parse_bsr_routers(r.bsr)

    bought = None
    if r.bought_past_month and r.bought_past_month != "N/A":
        m = re.search(r"(\d[\d,]*)", r.bought_past_month.replace(",", ""))
        if m:
            bought = int(m.group(1))

    available = 0 if r.available in ("Not listed", "Out of stock", "No") else 1

    stock_ct = None
    if r.stock_count and r.stock_count.isdigit():
        stock_ct = int(r.stock_count)

    is_prime = None
    if r.is_prime == "True":
        is_prime = 1
    elif r.is_prime == "False":
        is_prime = 0

    img_ct = int(r.image_count) if r.image_count and r.image_count.isdigit() else None

    qa_ct = None
    if r.qa_count:
        m = re.search(r"(\d+)", r.qa_count)
        if m:
            qa_ct = int(m.group(1))

    return (
        r.date,
        r.site,
        r.category,
        r.brand,
        r.model,
        r.asin,
        r.title,
        price_cents,
        currency,
        rating_val,
        review_val,
        bought,
        bsr_val,
        available,
        r.url,
        r.stock_status,
        stock_ct,
        r.sold_by,
        r.other_offers,
        r.coupon,
        is_prime,
        r.star_distribution,
        img_ct,
        qa_ct,
        r.fulfillment,
        r.price,
        r.rating,
        r.review_count,
        r.bsr,
        project,
    )


# ─── Query functions ──────────────────────────────────────────────────


def query_latest(
    conn: sqlite3.Connection,
    site: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Latest competitive snapshot per (asin, site)."""
    sql = """
        SELECT cs.* FROM competitive_snapshots cs
        INNER JOIN (
            SELECT asin, site, MAX(scraped_at) AS max_date
            FROM competitive_snapshots
            GROUP BY asin, site
        ) latest ON cs.asin = latest.asin
                 AND cs.site = latest.site
                 AND cs.scraped_at = latest.max_date
        WHERE 1=1
    """
    params: list = []
    if site:
        sql += " AND cs.site = ?"
        params.append(site)
    if category:
        sql += " AND cs.category = ?"
        params.append(category)
    sql += " ORDER BY cs.category, cs.brand, cs.model, cs.site"
    return [dict(row) for row in conn.execute(sql, params)]


def query_price_trends(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
    series_type: int = SERIES_NEW,
    days: int = 90,
) -> list[dict]:
    """Time series data points for one product, one series type."""
    if days:
        sql = """
            SELECT keepa_ts, value, fetched_at
            FROM keepa_time_series
            WHERE asin = ? AND site = ? AND series_type = ?
              AND keepa_ts >= (
                SELECT MAX(keepa_ts) - ? FROM keepa_time_series
                WHERE asin = ? AND site = ? AND series_type = ?
              )
            ORDER BY keepa_ts DESC
        """
        cutoff_minutes = days * 24 * 60
        params = (asin, site, series_type, cutoff_minutes, asin, site, series_type)
    else:
        sql = """
            SELECT keepa_ts, value, fetched_at
            FROM keepa_time_series
            WHERE asin = ? AND site = ? AND series_type = ?
            ORDER BY keepa_ts DESC
        """
        params = (asin, site, series_type)
    return [dict(r) for r in conn.execute(sql, params)]


def query_cross_market(
    conn: sqlite3.Connection,
    model: str,
    date: str | None = None,
) -> list[dict]:
    """Compare one product across all marketplaces."""
    if date:
        sql = """
            SELECT * FROM competitive_snapshots
            WHERE model LIKE ? AND scraped_at = ?
            ORDER BY site
        """
        params = [f"%{model}%", date]
    else:
        sql = """
            SELECT cs.* FROM competitive_snapshots cs
            INNER JOIN (
                SELECT asin, site, MAX(scraped_at) AS max_date
                FROM competitive_snapshots
                WHERE model LIKE ?
                GROUP BY asin, site
            ) latest ON cs.asin = latest.asin
                     AND cs.site = latest.site
                     AND cs.scraped_at = latest.max_date
            ORDER BY cs.site
        """
        params = [f"%{model}%"]
    return [dict(row) for row in conn.execute(sql, params)]


def query_bsr_ranking(
    conn: sqlite3.Connection,
    site: str,
    category: str | None = None,
) -> list[dict]:
    """Products ranked by BSR for a marketplace (latest snapshot)."""
    sql = """
        SELECT cs.* FROM competitive_snapshots cs
        INNER JOIN (
            SELECT asin, site, MAX(scraped_at) AS max_date
            FROM competitive_snapshots
            WHERE site = ?
            GROUP BY asin, site
        ) latest ON cs.asin = latest.asin
                 AND cs.site = latest.site
                 AND cs.scraped_at = latest.max_date
        WHERE cs.bsr IS NOT NULL
    """
    params: list = [site]
    if category:
        sql += " AND cs.category = ?"
        params.append(category)
    sql += " ORDER BY cs.bsr ASC"
    return [dict(row) for row in conn.execute(sql, params)]


def query_availability(
    conn: sqlite3.Connection,
    date: str | None = None,
) -> list[dict]:
    """Availability matrix: all products × all sites."""
    if date:
        sql = """
            SELECT brand, model, asin, site, available, price_cents, currency
            FROM competitive_snapshots
            WHERE scraped_at = ?
            ORDER BY brand, model, site
        """
        params = [date]
    else:
        sql = """
            SELECT cs.brand, cs.model, cs.asin, cs.site, cs.available,
                   cs.price_cents, cs.currency
            FROM competitive_snapshots cs
            INNER JOIN (
                SELECT asin, site, MAX(scraped_at) AS max_date
                FROM competitive_snapshots
                GROUP BY asin, site
            ) latest ON cs.asin = latest.asin
                     AND cs.site = latest.site
                     AND cs.scraped_at = latest.max_date
            ORDER BY cs.brand, cs.model, cs.site
        """
        params = []
    return [dict(row) for row in conn.execute(sql, params)]


def query_review_growth(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> list[dict]:
    """Review count time series (csv[17] = COUNT_REVIEWS)."""
    return query_price_trends(conn, asin, site, SERIES_COUNT_REVIEWS, days=0)


def query_seller_history(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> list[dict]:
    """Buy Box seller history for one product."""
    sql = """
        SELECT keepa_ts, seller_id, fetched_at
        FROM keepa_buybox_history
        WHERE asin = ? AND site = ?
        ORDER BY keepa_ts
    """
    return [dict(r) for r in conn.execute(sql, (asin, site))]


def query_monthly_sales(
    conn: sqlite3.Connection,
    asin: str,
    site: str,
) -> list[dict]:
    """Monthly sold history (series_type=100)."""
    return query_price_trends(conn, asin, site, SERIES_MONTHLY_SOLD, days=0)


def query_deals_history(
    conn: sqlite3.Connection,
    asin: str | None = None,
    site: str | None = None,
) -> list[dict]:
    """Deal/promotion records."""
    sql = "SELECT * FROM keepa_deals WHERE 1=1"
    params: list = []
    if asin:
        sql += " AND asin = ?"
        params.append(asin)
    if site:
        sql += " AND site = ?"
        params.append(site)
    sql += " ORDER BY start_time DESC"
    return [dict(r) for r in conn.execute(sql, params)]


_STAT_TABLES = frozenset(
    {
        "competitive_snapshots",
        "keepa_time_series",
        "keepa_buybox_history",
        "keepa_coupon_history",
        "keepa_deals",
        "keepa_products",
    }
)


def query_stats(conn: sqlite3.Connection) -> dict:
    """Database statistics."""
    stats: dict = {}
    for table in sorted(_STAT_TABLES):
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()  # noqa: S608
        stats[table] = row["cnt"]

    # Date range
    row = conn.execute(
        "SELECT MIN(scraped_at) AS min_date, MAX(scraped_at) AS max_date FROM competitive_snapshots"
    ).fetchone()
    stats["date_range"] = f"{row['min_date'] or '—'} to {row['max_date'] or '—'}"

    # Distinct products and sites
    row = conn.execute(
        "SELECT COUNT(DISTINCT asin) AS products, COUNT(DISTINCT site) AS sites "
        "FROM keepa_time_series"
    ).fetchone()
    stats["distinct_products"] = row["products"]
    stats["distinct_sites"] = row["sites"]

    return stats


# ─── Freshness queries ───────────────────────────────────────────────


def query_keepa_fetched_at(
    conn: sqlite3.Connection,
    asin_site_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], tuple[str, str] | None]:
    """Look up fetched_at + fetch_mode for (asin, site) pairs from keepa_products.

    Returns dict mapping each (asin, site) -> (fetched_at, fetch_mode) tuple,
    or None if no record exists.
    """
    if not asin_site_pairs:
        return {}

    conditions = " OR ".join(["(asin = ? AND site = ?)"] * len(asin_site_pairs))
    params = [v for pair in asin_site_pairs for v in pair]
    rows = conn.execute(
        f"SELECT asin, site, fetched_at, fetch_mode FROM keepa_products WHERE {conditions}",
        params,
    ).fetchall()

    result: dict[tuple[str, str], tuple[str, str] | None] = {pair: None for pair in asin_site_pairs}
    for row in rows:
        result[(row["asin"], row["site"])] = (row["fetched_at"], row["fetch_mode"] or "basic")
    return result


# ─── Product registry CRUD ───────────────────────────────────────────


def register_product(
    conn: sqlite3.Connection,
    category: str,
    brand: str,
    model: str,
    search_keywords: str = "",
) -> tuple[int, bool]:
    """Insert a product, or return existing id if (brand, model) already exists.

    Returns ``(product_id, is_new)`` where *is_new* is True when the row was
    just inserted.
    """
    row = conn.execute(
        "SELECT id FROM products WHERE brand = ? AND model = ?", (brand, model)
    ).fetchone()
    if row:
        return row["id"], False
    with conn:
        cur = conn.execute(
            "INSERT INTO products (category, brand, model, search_keywords) VALUES (?, ?, ?, ?)",
            (category, brand, model, search_keywords),
        )
    return cur.lastrowid, True  # type: ignore[return-value]


def register_asin(
    conn: sqlite3.Connection,
    product_id: int,
    marketplace: str,
    asin: str,
    status: str = "unverified",
    notes: str = "",
) -> None:
    """Set or update the ASIN for a product on a marketplace."""
    with conn:
        conn.execute(
            "INSERT INTO product_asins (product_id, marketplace, asin, status, notes) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(product_id, marketplace) DO UPDATE SET "
            "asin=excluded.asin, status=excluded.status, notes=excluded.notes, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            (product_id, marketplace, asin, status, notes),
        )


def tag_product(
    conn: sqlite3.Connection,
    product_id: int,
    tag: str,
) -> None:
    """Add a tag to a product. No-op if already tagged."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO product_tags (product_id, tag) VALUES (?, ?)",
            (product_id, tag),
        )


def sync_registry_from_keepa(conn: sqlite3.Connection) -> list[dict]:
    """Register orphan ASINs from keepa_products that are missing from product_asins.

    Only registers products with non-empty brand and title.
    Returns a list of dicts describing each registration.
    """
    orphans = conn.execute(
        """SELECT kp.asin, kp.site, kp.brand, kp.model, kp.title,
                  kp.product_group
           FROM keepa_products kp
           WHERE NOT EXISTS (
               SELECT 1 FROM product_asins pa
               WHERE pa.asin = kp.asin AND pa.marketplace = kp.site
           )"""
    ).fetchall()

    results: list[dict] = []
    for row in orphans:
        reg = _try_register_product(
            conn,
            asin=row["asin"],
            site=row["site"],
            brand=row["brand"] or "",
            title=row["title"] or "",
            model=row["model"] or "",
            category=row["product_group"] or "",
        )
        if reg:
            results.append({"registered": True, **reg})
        else:
            results.append(
                {
                    "asin": row["asin"],
                    "marketplace": row["site"],
                    "registered": False,
                    "reason": "missing brand or title",
                }
            )

    return results


def remove_product(conn: sqlite3.Connection, product_id: int) -> None:
    """Delete a product and cascade to product_asins and product_tags."""
    with conn:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))


def list_registered_products(
    conn: sqlite3.Connection,
    category: str | None = None,
    brand: str | None = None,
    marketplace: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    """List products with their ASIN mappings, with optional filters."""
    sql = """
        SELECT DISTINCT p.id, p.category, p.brand, p.model, p.search_keywords,
               pa.marketplace, pa.asin, pa.status, pa.notes
        FROM products p
        LEFT JOIN product_asins pa ON p.id = pa.product_id
        LEFT JOIN product_tags pt ON p.id = pt.product_id
        WHERE 1=1
    """
    params: list = []
    if category:
        sql += " AND p.category = ?"
        params.append(category)
    if brand:
        sql += " AND p.brand = ?"
        params.append(brand)
    if marketplace:
        sql += " AND pa.marketplace = ?"
        params.append(marketplace)
    if tag:
        sql += " AND pt.tag = ?"
        params.append(tag)
    sql += " ORDER BY p.brand, p.model, pa.marketplace"
    return [dict(r) for r in conn.execute(sql, params)]


def load_products_from_db(
    conn: sqlite3.Connection,
    category: str | None = None,
    brand: str | None = None,
    marketplace: str | None = None,
) -> list:
    """Materialize Product objects from the product registry tables.

    Returns a list of Product dataclasses with marketplace_overrides
    populated from product_asins rows, matching the same shape as
    products loaded from YAML via ProductEntry.to_product().
    """
    from amz_scout.models import Product

    # Get all matching products
    sql = "SELECT DISTINCT p.id, p.category, p.brand, p.model, p.search_keywords FROM products p"
    joins = []
    wheres = []
    params: list = []

    if marketplace:
        joins.append("JOIN product_asins pa ON p.id = pa.product_id")
        wheres.append("pa.marketplace = ? AND pa.status != 'wrong_product'")
        params.append(marketplace)

    if category:
        wheres.append("p.category = ?")
        params.append(category)
    if brand:
        wheres.append("p.brand = ?")
        params.append(brand)

    if joins:
        sql += " " + " ".join(joins)
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY p.brand, p.model"

    product_rows = conn.execute(sql, params).fetchall()
    if not product_rows:
        return []

    # Get all ASIN mappings for these products
    product_ids = [r["id"] for r in product_rows]
    placeholders = ",".join("?" * len(product_ids))
    asin_rows = conn.execute(
        f"SELECT product_id, marketplace, asin, status "
        f"FROM product_asins WHERE product_id IN ({placeholders}) "
        f"AND status != 'wrong_product'",
        product_ids,
    ).fetchall()

    # Group ASINs by product_id
    asins_by_product: dict[int, dict[str, dict[str, str]]] = {}
    for ar in asin_rows:
        pid = ar["product_id"]
        if pid not in asins_by_product:
            asins_by_product[pid] = {}
        asins_by_product[pid][ar["marketplace"]] = {"asin": ar["asin"]}

    # Build Product objects
    results = []
    for pr in product_rows:
        overrides = asins_by_product.get(pr["id"], {})
        # Pick the first ASIN as default (any marketplace)
        first_asin = ""
        if overrides:
            first_asin = next(iter(overrides.values()))["asin"]

        results.append(
            Product(
                category=pr["category"],
                brand=pr["brand"],
                model=pr["model"],
                default_asin=first_asin,
                search_keywords=pr["search_keywords"] or "",
                marketplace_overrides=overrides,
            )
        )
    return results


def update_asin_status(
    conn: sqlite3.Connection,
    product_id: int,
    marketplace: str,
    status: str,
    notes: str = "",
) -> None:
    """Update the status and notes for a product/marketplace ASIN mapping."""
    with conn:
        conn.execute(
            "UPDATE product_asins SET status = ?, notes = ?, last_checked = "
            "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
            "WHERE product_id = ? AND marketplace = ?",
            (status, notes, product_id, marketplace),
        )


def get_unverified_asins(
    conn: sqlite3.Connection,
    marketplace: str | None = None,
) -> list[dict]:
    """Return product/ASIN pairs with status 'unverified'."""
    sql = """
        SELECT p.id AS product_id, p.brand, p.model, pa.marketplace, pa.asin
        FROM products p
        JOIN product_asins pa ON p.id = pa.product_id
        WHERE pa.status = 'unverified'
    """
    params: list = []
    if marketplace:
        sql += " AND pa.marketplace = ?"
        params.append(marketplace)
    sql += " ORDER BY p.brand, p.model, pa.marketplace"
    return [dict(r) for r in conn.execute(sql, params)]


def find_product_exact(
    conn: sqlite3.Connection,
    brand: str,
    model: str,
) -> dict | None:
    """Find a product by exact brand + model match. Returns dict with id or None."""
    row = conn.execute(
        "SELECT id FROM products WHERE brand = ? AND model = ?",
        (brand, model),
    ).fetchone()
    return dict(row) if row else None


def find_product(
    conn: sqlite3.Connection,
    query_str: str,
    marketplace: str | None = None,
) -> dict | None:
    """Find a product by model substring or ASIN. Returns dict or None."""
    # Try ASIN lookup first
    if len(query_str) == 10 and query_str.isascii() and query_str.isalnum():
        sql = """
            SELECT p.id, p.category, p.brand, p.model, pa.asin, pa.marketplace
            FROM products p
            JOIN product_asins pa ON p.id = pa.product_id
            WHERE pa.asin = ?
        """
        params: list = [query_str]
        if marketplace:
            sql += " AND pa.marketplace = ?"
            params.append(marketplace)
        sql += " LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if row:
            return dict(row)

    # Model, search_keywords, or Keepa title substring match
    like = f"%{query_str}%"
    sql = """
        SELECT p.id, p.category, p.brand, p.model, pa.asin, pa.marketplace
        FROM products p
        LEFT JOIN product_asins pa ON p.id = pa.product_id
        WHERE (
            p.model LIKE ?
            OR p.search_keywords LIKE ?
            OR EXISTS (
                SELECT 1 FROM keepa_products kp
                JOIN product_asins pa2 ON kp.asin = pa2.asin
                WHERE pa2.product_id = p.id AND kp.title LIKE ?
            )
        )
    """
    params: list = [like, like, like]
    if marketplace:
        sql += " AND pa.marketplace = ?"
        params.append(marketplace)
    sql += " LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


# ─── Migration helpers ────────────────────────────────────────────────


def import_from_raw_json(
    conn: sqlite3.Connection,
    raw_dir: Path,
    products: list,
    site: str,
    fetched_at: str | None = None,
    fetch_mode: str = "basic",
) -> tuple[int, int]:
    """Import Keepa data from raw JSON files. Returns (ok, failed) counts.

    Per-file ``fetch_mode`` upgrade: callers that pass the default
    ``"basic"`` (e.g. ``admin migrate`` / ``admin reparse``, which import
    historical raw JSONs of mixed origin) get an auto-upgrade to
    ``"detailed"`` for any individual file whose raw JSON contains either
    the ``stats`` key or the ``offers`` key. This check is based on key
    presence, so ``offers: []`` still triggers the upgrade, and files with
    ``stats`` but no offers do as well. These fields are treated as
    per-file signals that the original Keepa request included detailed
    data, avoiding permanently mis-tagging rows that should be
    ``"detailed"``. Explicit ``fetch_mode="detailed"`` callers are honored
    as-is and never downgraded.
    """
    from amz_scout.utils import today_iso

    fetched = fetched_at or today_iso()
    ok, fail = 0, 0
    for prod in products:
        asin = prod.asin_for(site)
        json_path = raw_dir / f"{site.lower()}_{asin}.json"
        if not json_path.exists():
            continue
        try:
            with open(json_path) as f:
                raw = json_mod.load(f)
            # Auto-upgrade only when the caller did not explicitly say "detailed".
            effective_mode = fetch_mode
            if effective_mode != "detailed" and ("stats" in raw or "offers" in raw):
                effective_mode = "detailed"
            store_keepa_product(conn, asin, site, raw, fetched, fetch_mode=effective_mode)
            ok += 1
        except Exception:
            logger.exception("Import failed for %s/%s", site, asin)
            fail += 1
    return ok, fail


def import_from_csv(
    conn: sqlite3.Connection,
    csv_path: Path,
) -> int:
    """Import competitive data from an existing CSV file. Returns row count."""
    from amz_scout.csv_io import read_competitive_data

    rows = read_competitive_data(csv_path)
    return upsert_competitive(conn, rows)


# ─── Helpers ──────────────────────────────────────────────────────────


def _json_or_none(val) -> str | None:
    """Serialize a value to JSON string, or return None if empty."""
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return json_mod.dumps(val, ensure_ascii=False) if val else None
    return str(val)
