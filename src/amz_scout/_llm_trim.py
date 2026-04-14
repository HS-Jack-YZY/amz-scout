"""Private trim helpers that shrink API envelopes before they hit the LLM.

The CLI and admin callers of ``amz_scout.api`` want full DB rows for debugging
and reparse. The webapp's LLM only needs a small subset — the rest is token
bloat. These helpers produce *new* row dicts containing only allow-listed
fields, following the immutable-transform convention in ``api._add_dates``.

This module is deliberately underscore-prefixed: it is an internal helper,
not part of the public ``amz_scout.api`` contract. CLI code must not import
from it.
"""

import functools

# Competitive snapshot rows come from ``db.query_latest`` / ``query_cross_market``
# / ``query_bsr_ranking`` / ``query_availability``. The source table has 32
# columns; only these 13 carry information the LLM will ever cite to a user.
# Dropped fields (and why):
#   id, created_at, project       — book-keeping, not product data
#   title                         — long free text; duplicates brand/model for
#                                   decisions and eats tokens fast
#   url                           — LLM should never link out
#   stock_status, stock_count     — ``available`` already encodes this
#   sold_by, other_offers, coupon,
#       is_prime, fulfillment     — seller details available via query_sellers
#   star_distribution             — JSON blob; ``rating`` is the summary
#   image_count, qa_count         — vanity metrics
#   price_raw, rating_raw,
#       review_count_raw, bsr_raw — unparsed scraper strings, kept for reparse
LLM_SAFE_COMPETITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "site",
        "category",
        "brand",
        "model",
        "asin",
        "price_cents",
        "currency",
        "rating",
        "review_count",
        "bought_past_month",
        "bsr",
        "available",
        "scraped_at",
    }
)

# Keepa time-series rows: ``query_price_trends`` returns ``keepa_ts``, ``value``,
# ``fetched_at``; ``api._add_dates`` adds a human ``date``. The LLM only needs
# ``date`` + ``value``. ``keepa_ts`` is redundant after ``_add_dates``; the
# ``fetched_at`` wall-clock timestamp is metadata the caller does not need.
LLM_SAFE_TIMESERIES_FIELDS: frozenset[str] = frozenset({"date", "value"})

# Buy Box seller history rows from ``query_seller_history`` — same logic as
# time-series: drop ``keepa_ts`` and ``fetched_at`` after ``_add_dates`` has
# injected ``date``.
LLM_SAFE_SELLER_FIELDS: frozenset[str] = frozenset({"date", "seller_id"})

# Deal rows from ``query_deals_history`` — ``keepa_deals`` columns, minus
# ``access_type`` and ``fetched_at`` which the LLM never surfaces.
LLM_SAFE_DEAL_FIELDS: frozenset[str] = frozenset(
    {
        "asin",
        "site",
        "deal_type",
        "badge",
        "percent_claimed",
        "deal_status",
        "start_time",
        "end_time",
    }
)


def trim(rows: list[dict], allow: frozenset[str]) -> list[dict]:
    """Return a new list of row dicts containing only allow-listed keys.

    Pure function: never mutates ``rows`` or the dicts inside it. Missing
    keys are silently absent in the output (schema drift safe).
    """
    return [{k: v for k, v in r.items() if k in allow} for r in rows]


trim_competitive_rows = functools.partial(trim, allow=LLM_SAFE_COMPETITIVE_FIELDS)
trim_timeseries_rows = functools.partial(trim, allow=LLM_SAFE_TIMESERIES_FIELDS)
trim_seller_rows = functools.partial(trim, allow=LLM_SAFE_SELLER_FIELDS)
trim_deals_rows = functools.partial(trim, allow=LLM_SAFE_DEAL_FIELDS)
