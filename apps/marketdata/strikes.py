"""ATM / OTM / bracket strike selection (index + stock), built on instruments.py.

Scrip-master strikes are stored in paise (e.g. "2450000.000000" == 24500), so
target strikes are compared in that x100 form. The debug print() spam from the
old tokens_service has been replaced with logging.
"""

from __future__ import annotations

import logging
import math

from apps.marketdata.instruments import (
    INDEX_STRIKE_STEPS,
    get_options_by_index,
    get_stock_future,
    get_stock_options,
    get_stock_strike_step,
)

logger = logging.getLogger(__name__)


def _match(options: list, target_strike_rupees: float, suffix: str) -> dict | None:
    """Return the option whose strike == target (compared in paise) and whose
    symbol ends with `suffix` ('CE' or 'PE'); else None."""
    target_angel = target_strike_rupees * 100
    for opt in options:
        if abs(float(opt.get("strike", 0)) - target_angel) < 1 and opt.get("symbol", "").endswith(suffix):
            return opt
    return None


def find_exact_option(index_name: str, strike: float, option_type: str, expiry: str) -> dict | None:
    """Find one specific CE or PE contract at an exact strike (not ATM/OTM
    math -- used by manual/test triggers where the strike is given
    directly). Routes to BFO/NFO automatically via get_options_by_index."""
    options = get_options_by_index(index_name, expiry)
    if not options:
        return None
    return _match(options, float(strike), option_type.upper())


# --- index ---
def find_atm_strike(index_name: str, spot_price: float, expiry: str) -> dict | None:
    """ATM CE+PE for an index. Returns {strike, ce_token, pe_token} or None."""
    options = get_options_by_index(index_name, expiry)
    if not options:
        return None
    step = INDEX_STRIKE_STEPS.get(index_name.upper(), 100)
    atm = round(spot_price / step) * step
    ce, pe = _match(options, atm, "CE"), _match(options, atm, "PE")
    if ce and pe:
        return {"strike": atm, "ce_token": ce, "pe_token": pe}
    return None


def find_strangle_strikes(index_name: str, spot_price: float, expiry: str) -> dict | None:
    """
    Strangle: CE just above spot, PE just below spot — cheaper than ATM straddle.

    Example: spot=24465, step=50 → CE=24500, PE=24450
    If spot lands exactly on a strike (e.g. 24500):
        CE = 24550 (one step above), PE = 24500

    Returns {ce_strike, ce_token, pe_strike, pe_token} or None.
    """
    options = get_options_by_index(index_name, expiry)
    if not options:
        logger.warning("[%s] no options found for expiry %s", index_name, expiry)
        return None

    step = INDEX_STRIKE_STEPS.get(index_name.upper(), 100)

    pe_strike = math.floor(spot_price / step) * step   # nearest strike at or below spot
    ce_strike = math.ceil(spot_price  / step) * step   # nearest strike at or above spot

    # If spot is exactly on a strike both would be equal → push CE one step up
    if ce_strike == pe_strike:
        ce_strike += step

    ce = _match(options, ce_strike, "CE")
    pe = _match(options, pe_strike, "PE")

    if not ce or not pe:
        logger.warning(
            "[%s] strangle tokens missing: CE@%s=%s PE@%s=%s",
            index_name, ce_strike, ce, pe_strike, pe,
        )
        return None

    logger.info(
        "[%s] strangle: spot=%.2f CE@%s PE@%s",
        index_name, spot_price, ce_strike, pe_strike,
    )
    return {
        "ce_strike": ce_strike, "ce_token": ce,
        "pe_strike": pe_strike, "pe_token": pe,
    }


def find_strikes_near_atm(index_name: str, spot_price: float, expiry: str, count: int = 3) -> list:
    """`count` strikes either side of ATM, each with CE+PE tokens."""
    options = get_options_by_index(index_name, expiry)
    if not options:
        return []
    step = INDEX_STRIKE_STEPS.get(index_name.upper(), 100)
    atm = round(spot_price / step) * step
    results = []
    for i in range(-count, count + 1):
        target = atm + i * step
        ce, pe = _match(options, target, "CE"), _match(options, target, "PE")
        if ce and pe:
            results.append({"strike": target, "ce_token": ce, "pe_token": pe})
    return results


# --- stock ---
def find_stock_atm_strike(stock_name: str, spot_price: float, expiry: str) -> dict | None:
    options = get_stock_options(stock_name, expiry)
    if not options:
        logger.warning("[%s] no options for expiry %s", stock_name, expiry)
        return None
    step = get_stock_strike_step(stock_name)
    atm = round(spot_price / step) * step
    ce, pe = _match(options, atm, "CE"), _match(options, atm, "PE")
    if ce and pe:
        return {"strike": atm, "ce_token": ce, "pe_token": pe}
    logger.warning("[%s] ATM strike %s not found in chain", stock_name, atm)
    return None


def find_stock_otm_strikes(stock_name: str, spot_price: float, expiry: str, otm_step_multiplier: int = 2) -> dict | None:
    """OTM CE (above ATM) + OTM PE (below ATM), `otm_step_multiplier` steps out."""
    options = get_stock_options(stock_name, expiry)
    if not options:
        return None
    step = get_stock_strike_step(stock_name)
    atm = round(spot_price / step) * step
    otm = step * otm_step_multiplier
    ce = _match(options, atm + otm, "CE")
    pe = _match(options, atm - otm, "PE")
    if ce and pe:
        return {
            "otm_ce_strike": atm + otm, "otm_ce_token": ce,
            "otm_pe_strike": atm - otm, "otm_pe_token": pe,
        }
    return None


def get_stock_hedge_tokens(stock_name: str, spot_price: float, expiry: str) -> dict | None:
    """Future + ATM PE + OTM CE + OTM PE for the stock_future_hedge strategy."""
    future = get_stock_future(stock_name, expiry)
    if not future:
        logger.warning("[%s] no future for expiry %s", stock_name, expiry)
        return None
    atm = find_stock_atm_strike(stock_name, spot_price, expiry)
    if not atm:
        return None
    otm = find_stock_otm_strikes(stock_name, spot_price, expiry)
    if not otm:
        return None
    return {
        "future": future,
        "atm_pe": atm["pe_token"],
        "atm_strike": atm["strike"],
        "otm_ce": otm["otm_ce_token"],
        "otm_ce_strike": otm["otm_ce_strike"],
        "otm_pe": otm["otm_pe_token"],
        "otm_pe_strike": otm["otm_pe_strike"],
    }


def find_stock_bracket_strikes(stock_name: str, spot_price: float, expiry: str) -> dict | None:
    """Strangle bracket: PE at floor(spot/step)*step, CE at ceil(spot/step)*step.
    If spot lands exactly on a strike, PE strike == CE strike (collapses to a
    straddle) -- caller can detect that."""
    options = get_stock_options(stock_name, expiry)
    if not options:
        logger.warning("[%s] no options for expiry %s", stock_name, expiry)
        return None
    step = get_stock_strike_step(stock_name)
    pe_strike = math.floor(spot_price / step) * step
    ce_strike = math.ceil(spot_price / step) * step
    ce = _match(options, ce_strike, "CE")
    pe = _match(options, pe_strike, "PE")
    if ce and pe:
        return {"ce_strike": ce_strike, "ce_token": ce, "pe_strike": pe_strike, "pe_token": pe}
    logger.warning("[%s] bracket strikes PE=%s/CE=%s not found", stock_name, pe_strike, ce_strike)
    return None