"""Pick which index (NIFTY or SENSEX) the index-option strategies trade
each day (Strangle, Opening Bell, Jackpot -- anything that trades an
ATM/OTM CE+PE pair on an index, rather than a fixed instrument).

Compares both indices' nearest upcoming expiry date and picks whichever
is sooner. The result is cached in Supabase Storage for the trading day
-- same bucket/pattern as the scrip master cache in
apps/marketdata/instruments.py -- so any Celery worker process, on any
container, can read today's pick without recomputing it, and each
strategy's scheduled task just reads this instead of a hardcoded kwarg
in celery.py. Survives redeploys, unlike a local file.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from apps.marketdata.expiry import get_nearest_expiry, parse_expiry_date

logger = logging.getLogger(__name__)

_STORAGE_BUCKET = "algo-cache"
_INSTRUMENT_STORAGE_PATH = "daily_instrument.json"

DEFAULT_INSTRUMENT = "NIFTY"   # fallback if selection can't resolve either expiry


def _read_cached_instrument() -> str | None:
    from core.supabase.client import get_client
    try:
        raw = get_client().storage.from_(_STORAGE_BUCKET).download(_INSTRUMENT_STORAGE_PATH)
    except Exception:
        return None  # not found yet, or bucket/network issue -- caller recomputes

    try:
        cached = json.loads(raw)
    except Exception as exc:
        logger.warning("Daily instrument Storage cache unreadable (ignoring): %s", exc)
        return None

    if cached.get("date") != date.today().isoformat():
        return None  # yesterday's (or older) pick -- stale
    return cached.get("instrument")


def _write_cached_instrument(instrument: str) -> None:
    from core.supabase.client import get_client
    try:
        payload = json.dumps({"date": date.today().isoformat(), "instrument": instrument}).encode("utf-8")
        get_client().storage.from_(_STORAGE_BUCKET).upload(
            _INSTRUMENT_STORAGE_PATH, payload,
            file_options={"content-type": "application/json", "upsert": "true"},
        )
    except Exception as exc:
        logger.warning("Daily instrument Storage cache write failed (non-fatal): %s", exc)


def select_nearest_expiry_instrument() -> str:
    """Compare NIFTY's and SENSEX's nearest upcoming expiry and return
    whichever is sooner. A tie (both the same date) resolves to NIFTY."""
    logger.info("select_nearest_expiry_instrument: fetching NIFTY expiry")
    nifty_expiry_str  = get_nearest_expiry("NIFTY", "OPTIDX")
    logger.info("select_nearest_expiry_instrument: NIFTY expiry=%s, fetching SENSEX expiry", nifty_expiry_str)
    sensex_expiry_str = get_nearest_expiry("SENSEX", "OPTIDX")
    logger.info("select_nearest_expiry_instrument: SENSEX expiry=%s", sensex_expiry_str)

    nifty_date  = parse_expiry_date(nifty_expiry_str) if nifty_expiry_str else None
    sensex_date = parse_expiry_date(sensex_expiry_str) if sensex_expiry_str else None

    if nifty_date is None and sensex_date is None:
        logger.error(
            "Could not resolve expiry for NIFTY or SENSEX — defaulting to %s",
            DEFAULT_INSTRUMENT,
        )
        return DEFAULT_INSTRUMENT
    if nifty_date is None:
        chosen = "SENSEX"
    elif sensex_date is None:
        chosen = "NIFTY"
    elif sensex_date < nifty_date:
        chosen = "SENSEX"
    else:
        chosen = "NIFTY"   # NIFTY wins ties

    logger.info(
        "Strangle instrument selection: NIFTY expiry=%s, SENSEX expiry=%s -> chose %s",
        nifty_expiry_str, sensex_expiry_str, chosen,
    )
    return chosen


def get_todays_instrument() -> str:
    """What today's index-option strategies (Strangle, Opening Bell,
    Jackpot) should trade today.

    Reads the file cache written by select_daily_instrument_task (run
    earlier that same morning). If it's missing for any reason
    (selection task didn't run, first day after deploy, etc.), computes
    it fresh right here instead of silently defaulting to NIFTY.
    """
    cached = _read_cached_instrument()
    if cached:
        return cached

    logger.warning(
        "No cached Strangle instrument for today — computing it now "
        "instead of using this morning's selection task"
    )
    instrument = select_nearest_expiry_instrument()
    _write_cached_instrument(instrument)
    return instrument
