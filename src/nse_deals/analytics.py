"""Pure-Python analytics over lists of :class:`~nse_deals.models.Deal`.

Nothing here touches the network, so it works equally on live data, archived
files and test fixtures.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Deal

CRORE = 1e7


def to_crore(rupees: float, ndigits: int = 2) -> float:
    """Convert rupees to ₹ crore (1 crore = 1,00,00,000)."""
    return round(rupees / CRORE, ndigits)


def filter_deals(
    deals: Iterable[Deal],
    symbol: Optional[str] = None,
    client: Optional[str] = None,
    side: Optional[str] = None,
    min_value: float = 0.0,
    kind: Optional[str] = None,
) -> List[Deal]:
    """Filter deals.

    ``symbol`` matches exactly (case-insensitive); ``client`` is a
    case-insensitive substring match; ``min_value`` is in rupees.
    """
    sym = symbol.strip().upper() if symbol else None
    cli = client.strip().lower() if client else None
    sd = side.strip().upper() if side else None
    out = []
    for d in deals:
        if sym and d.symbol != sym:
            continue
        if cli and cli not in d.client_name.lower():
            continue
        if sd and d.side != sd:
            continue
        if kind and d.kind != kind:
            continue
        if d.value < min_value:
            continue
        out.append(d)
    return out


@dataclass
class NetPosition:
    """Aggregated buys and sells for one key (a symbol or a client)."""

    key: str
    buy_qty: int = 0
    sell_qty: int = 0
    buy_value: float = 0.0
    sell_value: float = 0.0
    deals: int = 0

    @property
    def net_qty(self) -> int:
        return self.buy_qty - self.sell_qty

    @property
    def net_value(self) -> float:
        return round(self.buy_value - self.sell_value, 2)

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "deals": self.deals,
            "buy_qty": self.buy_qty,
            "sell_qty": self.sell_qty,
            "net_qty": self.net_qty,
            "buy_value": round(self.buy_value, 2),
            "sell_value": round(self.sell_value, 2),
            "net_value": self.net_value,
            "net_value_cr": to_crore(self.net_value),
        }


def _aggregate(deals: Iterable[Deal], keyfunc) -> List[NetPosition]:
    acc: Dict[str, NetPosition] = {}
    for d in deals:
        k = keyfunc(d)
        p = acc.setdefault(k, NetPosition(key=k))
        p.deals += 1
        if d.side == "BUY":
            p.buy_qty += d.quantity
            p.buy_value += d.value
        else:
            p.sell_qty += d.quantity
            p.sell_value += d.value
    # Largest absolute net value first; ties broken alphabetically for stable output.
    return sorted(acc.values(), key=lambda p: (-abs(p.net_value), p.key))


def net_by_symbol(deals: Iterable[Deal]) -> List[NetPosition]:
    """Net buy/sell per symbol, sorted by absolute net value (largest first)."""
    return _aggregate(deals, lambda d: d.symbol)


def net_by_client(deals: Iterable[Deal]) -> List[NetPosition]:
    """Net buy/sell per client, sorted by absolute net value (largest first)."""
    return _aggregate(deals, lambda d: d.client_name)


def top_deals(deals: Iterable[Deal], n: int = 10) -> List[Deal]:
    """The ``n`` largest deals by value."""
    return sorted(deals, key=lambda d: (-d.value, d.symbol))[:n]


@dataclass
class RepeatBuyer:
    """A client that bought the same symbol on more than one day."""

    client_name: str
    symbol: str
    dates: List[_dt.date]
    total_qty: int
    total_value: float

    @property
    def days(self) -> int:
        return len(self.dates)

    def to_dict(self) -> Dict[str, object]:
        return {
            "client_name": self.client_name,
            "symbol": self.symbol,
            "days": self.days,
            "dates": [d.isoformat() for d in self.dates],
            "total_qty": self.total_qty,
            "total_value": round(self.total_value, 2),
            "total_value_cr": to_crore(self.total_value),
        }


def repeat_buyers(deals: Iterable[Deal], min_days: int = 2) -> List[RepeatBuyer]:
    """Clients buying the same symbol on at least ``min_days`` distinct days.

    Useful on historical ranges; on a single-day snapshot this is always empty.
    """
    groups: Dict[Tuple[str, str], List[Deal]] = defaultdict(list)
    for d in deals:
        if d.side == "BUY":
            groups[(d.client_name, d.symbol)].append(d)
    out = []
    for (client, sym), items in groups.items():
        dates = sorted({d.date for d in items})
        if len(dates) >= min_days:
            out.append(
                RepeatBuyer(
                    client_name=client,
                    symbol=sym,
                    dates=dates,
                    total_qty=sum(d.quantity for d in items),
                    total_value=sum(d.value for d in items),
                )
            )
    return sorted(out, key=lambda r: (-r.days, -r.total_value, r.symbol))


@dataclass
class RoundTrip:
    """A client that both bought and sold the same symbol on the same day."""

    date: _dt.date
    client_name: str
    symbol: str
    buy_qty: int
    sell_qty: int
    buy_value: float
    sell_value: float

    @property
    def matched_qty(self) -> int:
        """Quantity that offsets within the day (min of buy and sell)."""
        return min(self.buy_qty, self.sell_qty)

    def to_dict(self) -> Dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "client_name": self.client_name,
            "symbol": self.symbol,
            "buy_qty": self.buy_qty,
            "sell_qty": self.sell_qty,
            "matched_qty": self.matched_qty,
            "buy_value": round(self.buy_value, 2),
            "sell_value": round(self.sell_value, 2),
        }


def same_day_round_trips(deals: Iterable[Deal]) -> List[RoundTrip]:
    """Flag (date, client, symbol) groups with both a BUY and a SELL.

    These are often intraday or arbitrage trades rather than a genuine change
    in holding, so many readers exclude them when gauging "smart money".
    """
    groups: Dict[Tuple[_dt.date, str, str], Dict[str, list]] = defaultdict(lambda: {"BUY": [], "SELL": []})
    for d in deals:
        groups[(d.date, d.client_name, d.symbol)][d.side].append(d)
    out = []
    for (date, client, sym), sides in groups.items():
        if sides["BUY"] and sides["SELL"]:
            out.append(
                RoundTrip(
                    date=date,
                    client_name=client,
                    symbol=sym,
                    buy_qty=sum(d.quantity for d in sides["BUY"]),
                    sell_qty=sum(d.quantity for d in sides["SELL"]),
                    buy_value=sum(d.value for d in sides["BUY"]),
                    sell_value=sum(d.value for d in sides["SELL"]),
                )
            )
    return sorted(out, key=lambda r: (r.date, r.symbol, r.client_name))
