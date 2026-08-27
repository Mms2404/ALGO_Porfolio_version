"""Shared pre-conditions for every strategy.

Two scopes:
  can_strategy_run(...)  -> STRATEGY-level (run the strategy at all?)
       C1 holiday        [ACTIVE]
       C2 market hours   [ACTIVE]  09:15-15:30 IST, weekdays only
       C3 expiry week    [deferred - needs an expiry resolver]
  can_user_execute(...)  -> PER-USER (does THIS user trade? others still do)
       min_capital floor [ACTIVE]
       C4 margin <= 70%  [deferred]

Holidays are hardcoded (fill HOLIDAYS each year from the NSE calendar).
All time logic is in IST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from core.supabase.client import get_client

import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE market session (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# --- C1: NSE trading holidays (hardcoded; fill yearly from the NSE calendar) ---
# ISO date strings "YYYY-MM-DD".
HOLIDAYS: set[str] = {
    # NSE 2026 trading holidays
    "2026-01-15",  # Makar Sankranti
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Maha Shivaratri
    "2026-03-26",  # Holi
    "2026-03-31",  # Id-Ul-Fitr (Ramzan Eid)
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti / Ram Navami
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Buddha Pournima
    "2026-06-26",  # Eid ul-Adha (Bakri Eid)
    "2026-09-14",  # Milad-un-Nabi
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali Laxmi Pujan
    "2026-11-24",  # Gurunanak Jayanti
    "2026-12-25",  # Christmas
}

# --- feature flags for deferred conditions ---
ENABLE_EXPIRY_WEEK_GUARD = False   # C3
ENABLE_MARGIN_GUARD = False        # C4


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""


def _now_ist(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        return IST.localize(now)
    return now.astimezone(IST)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def is_holiday(d: date) -> bool:
    return d.isoformat() in HOLIDAYS


def is_trading_day(d: date | None = None) -> bool:
    """True if d (default: today IST) is a weekday and not an NSE holiday."""
    if d is None:
        d = datetime.now(IST).date()
    return not is_weekend(d) and not is_holiday(d)


def is_market_hours(now: datetime | None = None) -> bool:
    t = _now_ist(now).time()
    return MARKET_OPEN <= t <= MARKET_CLOSE

def is_strategy_active(strategy_name: str) -> bool:
    try:
        resp = get_client().table("strategies").select("is_enabled")\
            .eq("name", strategy_name).single().execute()
        return resp.data.get("is_enabled", True) if resp.data else False  # ← False on no row
    except Exception:
        print("is_strategy_active DB query failed — blocking strategy")
        return False  # ← fail safe


# --- strategy-level gate ---
def can_strategy_run(strategy_name: str, now: datetime | None = None) -> GateResult:
    now_ist = _now_ist(now)
    today = now_ist.date()

    if is_weekend(today):
        return GateResult(False, "weekend")
    if is_holiday(today):
        return GateResult(False, "market holiday")
    if not is_market_hours(now_ist):
        return GateResult(False, "outside market hours (09:15-15:30 IST)")
    if not is_strategy_active(strategy_name):
        return GateResult(False, "strategy is disabled")

    if ENABLE_EXPIRY_WEEK_GUARD:
        # TODO: resolve this strategy's relevant expiry and block if
        # (expiry - today).days <= 5. Needs an expiry resolver (weekly vs monthly).
        pass

    return GateResult(True)


# --- per-user gate ---
def can_user_execute(
    user_id: str,
    strategy_name: str,
    balance: float,
    min_capital: float = 0.0,
    required_margin: float | None = None,
) -> GateResult:
    # min_capital floor (from strategies.min_capital, passed in by the executor)
    if balance < min_capital:
        return GateResult(False, f"balance {balance:.2f} < min_capital {min_capital:.2f}")

    # Can the user actually afford this trade at all? Always on -- this is
    # a hard "do you have the cash" check, independent of the feature-
    # flagged 70%-cushion guard below (which stays disabled, per instruction).
    if required_margin is not None and required_margin > balance:
        return GateResult(
            False, f"required {required_margin:.2f} > available balance {balance:.2f}"
        )

    if ENABLE_MARGIN_GUARD and required_margin is not None:
        if required_margin > 0.70 * balance:
            return GateResult(
                False, f"required margin {required_margin:.2f} > 70% of balance {balance:.2f}"
            )

    return GateResult(True)