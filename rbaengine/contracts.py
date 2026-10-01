"""ASX 24 contract valuation, DV01 and position sizing.

Bond futures (YT 3-year, XT 10-year) are quoted as 100 minus yield and valued
as a notional 6% semi-annual coupon bond with $100,000 face value:

    value = 1000 * (c * (1 - v^n) / i + 100 * v^n)
    i = yield / 200,  v = 1 / (1 + i),  c = 3,  n = 6 (YT) or 20 (XT)

Because value is convex in yield, the dollar value of a 0.01 price move
changes with the level of yields. The 30-day interbank contract (IB) is
linear: $3m notional, so 1bp = 3,000,000 * 0.0001 * 30/365 = $24.66.
"""
import math

IB_DV01 = 3_000_000 * 0.0001 * 30 / 365
BOND_PERIODS = {"YT": 6, "XT": 20}


def product_of(symbol: str) -> str:
    return symbol[:2]


def contract_value(symbol: str, price: float) -> float:
    """Dollar value of one contract at `price`."""
    product = product_of(symbol)
    if product == "IB":
        return IB_DV01 * price * 100
    n = BOND_PERIODS[product]
    i = (100 - price) / 200
    v = 1 / (1 + i)
    return 1000 * (3 * (1 - v ** n) / i + 100 * v ** n)


def dv01(symbol: str, price: float) -> float:
    """Dollar change in one contract for a 1bp fall in yield (0.01 rise in price)."""
    return contract_value(symbol, price + 0.01) - contract_value(symbol, price)


def pnl(symbol: str, direction: str, contracts: int, entry: float, mark: float) -> float:
    sign = 1 if direction.upper() == "LONG" else -1
    return sign * contracts * (contract_value(symbol, mark) - contract_value(symbol, entry))


def size_by_risk(symbol: str, price: float, stop_bp: float, risk_budget: float) -> int:
    """Contracts such that being stopped out `stop_bp` away loses at most `risk_budget`."""
    return math.floor(risk_budget / (dv01(symbol, price) * stop_bp))


def curve_ratio(front_symbol: str, front_price: float, back_symbol: str, back_price: float) -> float:
    """Front contracts per back contract for a DV01-neutral curve trade."""
    return dv01(back_symbol, back_price) / dv01(front_symbol, front_price)
