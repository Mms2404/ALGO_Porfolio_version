"""Nearest expiry resolver.

Scrip master expiry format: "25AUG2026"  (DDMMMYYYY, uppercase month).
Parses all available expiries for a given instrument, filters to
today-or-future, and returns the nearest one in Angel's format string.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from apps.marketdata.instruments import filter_instruments, filter_bfo_instruments, INDEX_FNO_EXCHANGE


def _get_instruments(name: str, instrument_type: str) -> list:
    """Route to BFO for SENSEX, NFO for everything else."""
    exchange = INDEX_FNO_EXCHANGE.get(name.upper(), "NFO")
    if exchange == "BFO":
        return filter_bfo_instruments(name=name.upper(), instrumenttype=instrument_type)
    return filter_instruments(name=name.upper(), instrumenttype=instrument_type)

logger = logging.getLogger(__name__)

_EXPIRY_FMT = "%d%b%Y"   # "25AUG2026"


def _parse(expiry_str: str) -> date | None:
    try:
        return datetime.strptime(expiry_str.upper(), _EXPIRY_FMT).date()
    except ValueError:
        return None


def parse_expiry_date(expiry_str: str) -> date | None:
    """Public wrapper — parse an Angel expiry string ('26JUN2026') to a
    date, for callers outside this module (e.g. comparing two indices'
    expiries to pick which to trade)."""
    return _parse(expiry_str)


def get_nearest_expiry(name: str, instrument_type: str = "OPTIDX") -> str | None:
    """
    Return the nearest upcoming expiry string (Angel format e.g. '26JUN2026')
    for the given instrument (e.g. 'NIFTY') and instrument type.

    Returns None if the scrip master has no future expiry for this instrument.
    """
    today = date.today()
    instruments = _get_instruments(name, instrument_type)

    future: list[tuple[date, str]] = []
    seen: set[str] = set()
    for item in instruments:
        exp_str = item.get("expiry", "").strip()
        if not exp_str or exp_str in seen:
            continue
        seen.add(exp_str)
        d = _parse(exp_str)
        if d and d >= today:
            future.append((d, exp_str))

    if not future:
        logger.warning("No future expiry found for %s (%s)", name, instrument_type)
        return None

    future.sort(key=lambda x: x[0])
    nearest = future[0][1]
    logger.info("Nearest expiry for %s: %s", name, nearest)
    return nearest

from datetime import timedelta


def _in_expiry_week(expiry_date: date, today: date) -> bool:
    """True if today is Mon-Thu of the same calendar week as expiry_date."""
    expiry_monday = expiry_date - timedelta(days=expiry_date.weekday())
    today_monday  = today - timedelta(days=today.weekday())
    return today_monday == expiry_monday and today.weekday() <= 3  # Mon=0,Thu=3


def get_safe_expiry(name: str, instrument_type: str = "OPTIDX") -> str | None:
    """Return nearest expiry that avoids the current expiry week (Mon-Thu).

    Used by multi-day strategies (e.g. stock hedge) to avoid trading in the
    last days of an expiry where liquidity drops sharply.
    """
    today = date.today()
    instruments = _get_instruments(name, instrument_type)

    future: list[tuple[date, str]] = []
    seen: set[str] = set()
    for item in instruments:
        exp_str = item.get("expiry", "").strip()
        if not exp_str or exp_str in seen:
            continue
        seen.add(exp_str)
        d = _parse(exp_str)
        if d and d >= today:
            future.append((d, exp_str))

    future.sort(key=lambda x: x[0])

    for d, exp_str in future:
        if not _in_expiry_week(d, today):
            logger.info("Safe expiry for %s (%s): %s", name, instrument_type, exp_str)
            return exp_str

    logger.warning("No safe expiry found for %s (%s)", name, instrument_type)
    return None
