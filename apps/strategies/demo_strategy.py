"""Demo Strategy — illustrative only.

This file exists to show the REAL plumbing every strategy in this
codebase shares: idempotent signal creation, fan-out to subscribed
users, feed-account fallback, per-strategy LIVE/DRY mode, and structured
logging. The actual entry/exit decision logic below is a deliberately
simple placeholder (a fixed hold duration) -- it is NOT the real trading
logic used in production. The real strategies (entry conditions, exit
thresholds, strike selection, timing) are the actual trading edge and
aren't published here.

Everything from `create_signal(...)` onward -- the idempotency guard,
the fan_out_signal() call, the mode field, the payload shape -- is
exactly how the real system works.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from apps.execution.executor import fan_out_signal
from apps.execution.signals import create_signal
from apps.marketdata.expiry import get_nearest_expiry
from apps.marketdata.instruments import INDEX_FNO_EXCHANGE
from apps.marketdata.strikes import find_atm_strike
from apps.marketdata.feed_rest import get_index_ltp
from apps.marketdata.feed_ws import AngelWSStream
from core.angel.feed_login import login_feed_account
from core.conditions import can_strategy_run, is_trading_day

logger = logging.getLogger("strategy.demo")

STRATEGY_ID   = 99
STRATEGY_NAME = "Demo Strategy"
DEFAULT_INDEX = "NIFTY"

# Placeholder parameters -- NOT real trading thresholds.
DEMO_HOLD_SECONDS = 60


class DemoStrategy:
    """Buys an ATM call + put, holds for a fixed (placeholder) duration,
    then exits. Illustrative only -- see module docstring."""

    def __init__(self, instrument: str = DEFAULT_INDEX, mode: str = "DRY"):
        self.instrument = instrument.upper()
        self.mode       = mode.upper()
        self._stop      = asyncio.Event()
        self._stream: AngelWSStream | None = None
        self._entry_ce = None
        self._entry_pe = None

    async def run(self) -> None:
        gate = can_strategy_run(STRATEGY_NAME)
        if not gate.allowed:
            logger.info("Demo strategy blocked: %s", gate.reason)
            return

        result = login_feed_account(preferred_index=0)
        if result is None:
            logger.error("All feed accounts failed to log in — demo strategy aborted")
            return
        creds, login_res = result
        td = login_res["data"]
        api_key = creds["api_key"]
        jwt     = td["jwt_token"]

        fno_exch = INDEX_FNO_EXCHANGE.get(self.instrument, "NFO")
        expiry   = get_nearest_expiry(self.instrument, "OPTIDX")
        if not expiry:
            logger.error("No expiry found for %s", self.instrument)
            return

        spot = get_index_ltp(self.instrument, api_key, jwt)
        atm  = find_atm_strike(self.instrument, spot, expiry)
        if not atm:
            logger.error("ATM strike not found")
            return

        ce_inst, pe_inst = atm["ce_token"], atm["pe_token"]
        strike           = atm["strike"]
        lot_size         = int(ce_inst.get("lotsize", 1))

        logger.info("Demo strategy ENTRY setup: %s ATM=%s expiry=%s", self.instrument, strike, expiry)

        today = date.today()
        signal = create_signal(
            strategy_id=STRATEGY_ID, trading_day=today,
            kind="ENTRY", instrument=self.instrument,
            payload={
                "index": self.instrument, "expiry": expiry, "strike": strike,
                "ce_symbol": ce_inst["symbol"], "ce_token": ce_inst["token"],
                "pe_symbol": pe_inst["symbol"], "pe_token": pe_inst["token"],
                "lot_size": lot_size, "lots": 1,
                "entry_ce": 0.0, "entry_pe": 0.0,  # placeholder -- real version waits for live ticks
                "strategy_name": STRATEGY_NAME,
                "order_exchange": fno_exch,
                "mode": self.mode,
            },
        )
        if signal is None:
            # Idempotency guard: a duplicate ENTRY for this strategy/day/
            # instrument was correctly rejected. Stop here -- do NOT fall
            # through to monitoring, or a phantom EXIT can be written
            # against the wrong entry (see BUG_LOG.md #15).
            logger.info("Demo strategy ENTRY already fired today for %s", self.instrument)
            return

        signal["strategy_name"] = STRATEGY_NAME
        await fan_out_signal(signal)
        logger.info("Demo strategy ENTRY fanned out")

        # Placeholder exit condition -- a fixed hold duration, not real logic.
        await asyncio.sleep(DEMO_HOLD_SECONDS)

        exit_signal = create_signal(
            strategy_id=STRATEGY_ID, trading_day=today,
            kind="EXIT", instrument=self.instrument,
            payload={
                "index": self.instrument, "expiry": expiry, "strike": strike,
                "ce_symbol": ce_inst["symbol"], "ce_token": ce_inst["token"],
                "pe_symbol": pe_inst["symbol"], "pe_token": pe_inst["token"],
                "lot_size": lot_size, "lots": 1,
                "entry_ce": 0.0, "entry_pe": 0.0,
                "exit_ce": 0.0, "exit_pe": 0.0,
                "exit_reason": "DEMO_TIMER",
                "strategy_name": STRATEGY_NAME,
                "order_exchange": fno_exch,
                "mode": self.mode,
            },
        )
        if exit_signal is None:
            logger.info("Demo strategy EXIT already fired today")
            return

        exit_signal["strategy_name"] = STRATEGY_NAME
        await fan_out_signal(exit_signal)
        logger.info("Demo strategy EXIT fanned out")


async def run_demo_strategy(instrument: str = DEFAULT_INDEX, mode: str = "DRY") -> None:
    if not is_trading_day():
        logger.info("Not a trading day — demo strategy skipped")
        return
    await DemoStrategy(instrument=instrument, mode=mode).run()
