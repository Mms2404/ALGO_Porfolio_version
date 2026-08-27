"""Manual single-leg test order — Test strategy, strategy_id=8.

Not a real trading strategy: no ATM/OTM math, no profit target, no stop
loss, no monitoring. It places exactly ONE order (BUY or SELL) for a
specific NIFTY/SENSEX strike + CE/PE, chosen directly by the admin, and
goes through the exact same signal -> fan_out -> executor pipeline every
other strategy uses. Built to verify the live order-placement path
end-to-end (e.g. confirming Angel's IP whitelist / broker connectivity)
without waiting for a real strategy's entry condition to fire.

Requires a row in the Supabase `strategies` table: {id: 8, name: "Test",
is_enabled: true} -- same as every other strategy, `can_strategy_run()`
checks it and blocks (fail-safe) if it's missing.

Admin-triggered only via admin_api. Not on any Celery Beat schedule.

IMPORTANT: because this uses the same `signals` table idempotency as
every other strategy, only ONE test BUY and ONE test SELL can fire per
instrument per day (e.g. one NIFTY test-BUY and one NIFTY test-SELL).
A second same-day attempt with the same instrument+side gets silently
skipped by create_signal()'s duplicate-key guard -- same protection that
stops accidental double-firing in the real strategies. If you need to
fire more than one test per instrument per day, say so and this can be
changed to bypass that check for Test specifically.
"""

from __future__ import annotations

import logging
from datetime import date

from apps.execution.executor import fan_out_signal
from apps.execution.signals import create_signal
from apps.marketdata.expiry import get_nearest_expiry
from apps.marketdata.feed_rest import get_ltp_by_token
from apps.marketdata.instruments import INDEX_FNO_EXCHANGE
from apps.marketdata.strikes import find_exact_option
from core.angel.feed_login import login_feed_account
from core.conditions import can_strategy_run, is_trading_day

logger = logging.getLogger("strategy.test")

STRATEGY_ID   = 8
STRATEGY_NAME = "Test"


async def run_test_order(instrument: str, strike: float, option_type: str, side: str, mode: str = "LIVE") -> dict:
    """Place ONE test order. instrument: 'NIFTY'|'SENSEX'. option_type:
    'CE'|'PE'. side: 'BUY'|'SELL'. mode: 'LIVE'|'DRY' (defaults LIVE --
    Test exists to verify the real order path). Returns a small status dict."""
    instrument  = instrument.upper()
    option_type = option_type.upper()
    side        = side.upper()
    mode        = mode.upper()

    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if option_type not in ("CE", "PE"):
        raise ValueError("option_type must be CE or PE")
    if mode not in ("LIVE", "DRY"):
        raise ValueError("mode must be LIVE or DRY")
    if instrument not in INDEX_FNO_EXCHANGE:
        raise ValueError(f"Unknown instrument: {instrument}. Valid: {list(INDEX_FNO_EXCHANGE)}")

    if not is_trading_day():
        logger.info("Not a trading day — test order skipped")
        return {"skipped": True, "reason": "not a trading day"}

    gate = can_strategy_run(STRATEGY_NAME)
    if not gate.allowed:
        logger.info("Test order blocked: %s", gate.reason)
        return {"skipped": True, "reason": gate.reason}

    result = login_feed_account(preferred_index=0)
    if result is None:
        logger.error("All feed accounts failed to log in — test order aborted")
        return {"status": "error", "error": "feed login failed"}
    creds, login_res = result
    td      = login_res["data"]
    api_key = creds["api_key"]
    jwt     = td["jwt_token"]

    fno_exch = INDEX_FNO_EXCHANGE.get(instrument, "NFO")
    expiry   = get_nearest_expiry(instrument, "OPTIDX")
    if not expiry:
        logger.error("No expiry found for %s", instrument)
        return {"status": "error", "error": f"no expiry found for {instrument}"}

    match = find_exact_option(instrument, strike, option_type, expiry)
    if not match:
        logger.error("No %s contract found for %s %s expiry=%s", option_type, instrument, strike, expiry)
        return {"status": "error", "error": f"no {option_type} contract found at strike {strike}"}

    token    = match["token"]
    symbol   = match["symbol"]
    lot_size = int(match.get("lotsize", 1))

    try:
        ltp = get_ltp_by_token(fno_exch, token, api_key, jwt)
    except Exception as exc:
        logger.warning("Could not fetch LTP for %s: %s — proceeding with price=0", symbol, exc)
        ltp = 0.0

    logger.info(
        "TEST %s: %s %s %s symbol=%s expiry=%s lot=%s ltp=%s",
        side, instrument, strike, option_type, symbol, expiry, lot_size, ltp,
    )

    today = date.today()
    kind  = "ENTRY" if side == "BUY" else "EXIT"

    price_field  = f"{'entry' if kind == 'ENTRY' else 'exit'}_{option_type.lower()}"
    symbol_field = f"{option_type.lower()}_symbol"
    token_field  = f"{option_type.lower()}_token"

    payload = {
        "strategy_name": STRATEGY_NAME,
        "index":         instrument,
        "expiry":        expiry,
        "strike":        strike,
        symbol_field:    symbol,
        token_field:     token,
        price_field:     ltp,
        "lot_size":      lot_size,
        "lots":          1,
        "order_exchange": fno_exch,
        "mode":          mode,
    }
    if kind == "EXIT":
        payload["exit_reason"] = "MANUAL_TEST"

    signal = create_signal(
        strategy_id=STRATEGY_ID, trading_day=today,
        kind=kind, instrument=instrument,
        payload=payload,
    )
    if signal is None:
        logger.info("Test %s already fired today for %s %s %s", kind, instrument, strike, option_type)
        return {"skipped": True, "reason": "duplicate signal for today (one test BUY + one test SELL per instrument per day)"}

    signal["strategy_name"] = STRATEGY_NAME
    await fan_out_signal(signal)
    return {
        "status":    "completed",
        "signal_id": signal.get("id"),
        "symbol":    symbol,
        "token":     token,
        "ltp":       ltp,
        "exchange":  fno_exch,
    }
