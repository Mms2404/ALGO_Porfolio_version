"""Trading symbol builder for Angel One SmartAPI.

Naming conventions (from Angel One docs):

  Equity Spot          : SBIN-EQ
  Equity Futures       : CROMPTON30NOV23FUT       Name+Date+Month+Year+FUT
  Equity Options       : BAJFINANCE28DEC237650PE   Name+Date+Month+Year+Strike+CE/PE
  Index Futures        : BANKNIFTY28AUG24FUT       Name+Date+Month+Year+FUT
  Index Options        : NIFTY26JUN2524500CE       Name+Date+Month+Year+Strike+CE/PE
  Commodity Futures    : NICKEL23DECFUT            Name+Year+Month+FUT
  Commodity Options    : CRUDEOIL23DEC6450CE       Name+Year+Month+Strike+CE/PE

Usage:
  from apps.marketdata.symbol_builder import build_symbol

  build_symbol("equity_spot",      name="SBIN")
  build_symbol("equity_futures",   name="CROMPTON", day=30, month="NOV", year=23)
  build_symbol("equity_options",   name="BAJFINANCE", day=28, month="DEC", year=23, strike=7650, opt_type="PE")
  build_symbol("index_futures",    name="BANKNIFTY", day=28, month="AUG", year=24)
  build_symbol("index_options",    name="NIFTY", day=26, month="JUN", year=25, strike=24500, opt_type="CE")
  build_symbol("commodity_futures",name="NICKEL", year=23, month="DEC")
  build_symbol("commodity_options",name="CRUDEOIL", year=23, month="DEC", strike=6450, opt_type="CE")
"""

from __future__ import annotations

from datetime import date, datetime


def _fmt_month(month: str | int) -> str:
    """Normalize month to 3-letter uppercase: 6 -> 'JUN', 'june' -> 'JUN'."""
    if isinstance(month, int):
        return datetime(2000, month, 1).strftime("%b").upper()
    return month.upper()[:3]


def _fmt_year2(year: int) -> str:
    """Normalize year to 2-digit string: 2025 -> '25', 25 -> '25'."""
    if year > 100:
        return str(year)[-2:]
    return f"{year:02d}"


def _fmt_year4(year: int) -> str:
    """Normalize year to 4-digit string: 25 -> '2025', 2025 -> '2025'."""
    if year < 100:
        return f"20{year:02d}"
    return str(year)


def _strike_str(strike: float | int) -> str:
    """Format strike price: 24500.0 -> '24500', 7650 -> '7650'."""
    return str(int(strike)) if float(strike) == int(strike) else str(strike)


def build_symbol(
    instrument_type: str,
    *,
    name: str,
    day: int | None = None,
    month: str | int | None = None,
    year: int | None = None,
    strike: float | int | None = None,
    opt_type: str | None = None,   # "CE" or "PE"
) -> str:
    """Build an Angel One trading symbol string.

    Args:
        instrument_type : one of the keys in INSTRUMENT_TYPES below
        name            : instrument name e.g. "NIFTY", "SBIN", "CRUDEOIL"
        day             : expiry day (equity/index only)
        month           : expiry month as int (6) or str ("JUN"/"june")
        year            : expiry year as 2-digit (25) or 4-digit (2025)
        strike          : strike price (options only)
        opt_type        : "CE" or "PE" (options only)

    Returns:
        Trading symbol string e.g. "NIFTY26JUN2524500CE"

    Raises:
        ValueError if required args are missing for the given type.
    """
    t = instrument_type.lower().replace(" ", "_").replace("-", "_")
    n = name.upper()

    if t == "equity_spot":
        return f"{n}-EQ"

    if t in ("equity_futures", "index_futures"):
        _require(day=day, month=month, year=year)
        return f"{n}{day:02d}{_fmt_month(month)}{_fmt_year2(year)}FUT"

    if t in ("equity_options", "index_options"):
        _require(day=day, month=month, year=year, strike=strike, opt_type=opt_type)
        return f"{n}{day:02d}{_fmt_month(month)}{_fmt_year2(year)}{_strike_str(strike)}{opt_type.upper()}"

    if t == "commodity_futures":
        _require(month=month, year=year)
        return f"{n}{_fmt_year2(year)}{_fmt_month(month)}FUT"

    if t == "commodity_options":
        _require(month=month, year=year, strike=strike, opt_type=opt_type)
        return f"{n}{_fmt_year2(year)}{_fmt_month(month)}{_strike_str(strike)}{opt_type.upper()}"

    raise ValueError(
        f"Unknown instrument_type '{instrument_type}'. "
        f"Valid: equity_spot, equity_futures, equity_options, "
        f"index_futures, index_options, commodity_futures, commodity_options"
    )


def build_symbol_from_expiry(
    instrument_type: str,
    *,
    name: str,
    expiry: str,          # Angel format: "26JUN2025"
    strike: float | int | None = None,
    opt_type: str | None = None,
) -> str:
    """Convenience wrapper — parses Angel expiry string and calls build_symbol.

    Args:
        expiry : Angel expiry format "26JUN2025" or "25AUG2026"
    """
    d = datetime.strptime(expiry.upper(), "%d%b%Y")
    return build_symbol(
        instrument_type,
        name=name,
        day=d.day,
        month=d.strftime("%b"),
        year=d.year,
        strike=strike,
        opt_type=opt_type,
    )


def _require(**kwargs) -> None:
    missing = [k for k, v in kwargs.items() if v is None]
    if missing:
        raise ValueError(f"Missing required arguments: {', '.join(missing)}")


# ---------------------------------------------------------------------------
# Quick sanity check (run directly: python symbol_builder.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        (build_symbol("equity_spot",       name="SBIN"),                                          "SBIN-EQ"),
        (build_symbol("equity_futures",    name="CROMPTON", day=30, month="NOV", year=23),         "CROMPTON30NOV23FUT"),
        (build_symbol("equity_options",    name="BAJFINANCE", day=28, month="DEC", year=23, strike=7650, opt_type="PE"), "BAJFINANCE28DEC237650PE"),
        (build_symbol("index_futures",     name="BANKNIFTY", day=28, month="AUG", year=24),        "BANKNIFTY28AUG24FUT"),
        (build_symbol("index_options",     name="NIFTY", day=26, month="JUN", year=25, strike=24500, opt_type="CE"), "NIFTY26JUN2524500CE"),
        (build_symbol("commodity_futures", name="NICKEL", year=23, month="DEC"),                   "NICKEL23DECFUT"),
        (build_symbol("commodity_options", name="CRUDEOIL", year=23, month="DEC", strike=6450, opt_type="CE"), "CRUDEOIL23DEC6450CE"),
        (build_symbol_from_expiry("index_options", name="NIFTY", expiry="26JUN2025", strike=24500, opt_type="CE"), "NIFTY26JUN2524500CE"),
    ]
    all_pass = True
    for got, expected in tests:
        status = "✓" if got == expected else "✗"
        if got != expected:
            all_pass = False
        print(f"  {status}  {got!r:45s}  expected {expected!r}")
    print("\nAll tests passed ✓" if all_pass else "\nSome tests FAILED ✗")
