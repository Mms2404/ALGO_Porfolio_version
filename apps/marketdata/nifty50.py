"""Nifty 50 constituent stocks with NSE trading symbols.

Used by admin-triggered strategies that allow stock selection from this list.
Grouped by sector for easy reference.

Usage:
    from apps.marketdata.nifty50 import NIFTY50_SYMBOLS, get_symbol, NIFTY50_BY_SECTOR

    symbol = get_symbol("HDFC Bank")       # -> "HDFCBANK"
    all_symbols = NIFTY50_SYMBOLS          # -> ["HDFCBANK", "ICICIBANK", ...]
"""

NIFTY50_BY_SECTOR: dict[str, dict[str, str]] = {
    "Financial Services": {
        "HDFC Bank":              "HDFCBANK",
        "ICICI Bank":             "ICICIBANK",
        "State Bank of India":    "SBIN",
        "Axis Bank":              "AXISBANK",
        "Bajaj Finance":          "BAJFINANCE",
        "Bajaj Finserv":          "BAJAJFINSV",
        "Shriram Finance":        "SHRIRAMFIN",
        "Kotak Mahindra Bank":    "KOTAKBANK",
        "SBI Life Insurance":     "SBILIFE",
        "HDFC Life Insurance":    "HDFCLIFE",
    },
    "Information Technology": {
        "Tata Consultancy Services": "TCS",
        "Infosys":                   "INFY",
        "HCL Technologies":          "HCLTECH",
        "Tech Mahindra":             "TECHM",
        "Wipro":                     "WIPRO",
    },
    "Oil, Gas & Energy": {
        "Reliance Industries":           "RELIANCE",
        "Oil and Natural Gas Corporation": "ONGC",
        "Power Grid Corporation":        "POWERGRID",
        "NTPC":                          "NTPC",
    },
    "Automobile": {
        "Mahindra & Mahindra": "M&M",
        "Maruti Suzuki":       "MARUTI",
        "Tata Motors":         "TATAMOTORS",
        "Bajaj Auto":          "BAJAJ-AUTO",
        "Eicher Motors":       "EICHERMOT",
        "Hero MotoCorp":       "HEROMOTOCO",
    },
    "Consumer Goods": {
        "Hindustan Unilever":    "HINDUNILVR",
        "ITC":                   "ITC",
        "Nestle India":          "NESTLEIND",
        "Tata Consumer Products": "TATACONSUM",
    },
    "Healthcare": {
        "Sun Pharmaceutical":      "SUNPHARMA",
        "Cipla":                   "CIPLA",
        "Dr. Reddy's Laboratories": "DRREDDY",
        "Apollo Hospitals":        "APOLLOHOSP",
        "Divi's Laboratories":     "DIVISLAB",
    },
    "Metals & Mining": {
        "Tata Steel":        "TATASTEEL",
        "JSW Steel":         "JSWSTEEL",
        "Hindalco Industries": "HINDALCO",
        "Coal India":        "COALINDIA",
    },
    "Construction & Infrastructure": {
        "Larsen & Toubro":   "LT",
        "UltraTech Cement":  "ULTRACEMCO",
        "Grasim Industries": "GRASIM",
        "Adani Ports":       "ADANIPORTS",
    },
    "Services & Others": {
        "Bharti Airtel":          "BHARTIARTL",
        "Adani Enterprises":      "ADANIENT",
        "Bharat Electronics Ltd": "BEL",
        "Titan Company":          "TITAN",
        "Zomato":                 "ETERNAL",
        "Jio Financial Services": "JIOFIN",
    },
}

# Flat list of all NSE symbols
NIFTY50_SYMBOLS: list[str] = [
    symbol
    for sector in NIFTY50_BY_SECTOR.values()
    for symbol in sector.values()
]

# Reverse map: symbol -> company name
SYMBOL_TO_NAME: dict[str, str] = {
    symbol: name
    for sector in NIFTY50_BY_SECTOR.values()
    for name, symbol in sector.items()
}

# Flat name -> symbol map
NAME_TO_SYMBOL: dict[str, str] = {
    name: symbol
    for sector in NIFTY50_BY_SECTOR.values()
    for name, symbol in sector.items()
}


def get_symbol(company_name: str) -> str | None:
    """Return NSE symbol for a company name (case-insensitive). Returns None if not found."""
    for name, symbol in NAME_TO_SYMBOL.items():
        if name.lower() == company_name.lower():
            return symbol
    return None


def get_name(symbol: str) -> str | None:
    """Return company name for an NSE symbol (case-insensitive). Returns None if not found."""
    return SYMBOL_TO_NAME.get(symbol.upper())


def is_valid_symbol(symbol: str) -> bool:
    """Check if a symbol is in the Nifty 50 list."""
    return symbol.upper() in NIFTY50_SYMBOLS


if __name__ == "__main__":
    print(f"Total Nifty 50 stocks: {len(NIFTY50_SYMBOLS)}")
    for sector, stocks in NIFTY50_BY_SECTOR.items():
        print(f"\n{sector} ({len(stocks)}):")
        for name, sym in stocks.items():
            print(f"  {sym:15s} {name}")
