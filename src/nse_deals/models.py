"""Data model for a single NSE bulk / block / short-selling deal."""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

KINDS = ("bulk", "block", "short")
SIDES = ("BUY", "SELL")


@dataclass(frozen=True)
class Deal:
    """One reported deal.

    ``value`` is computed as ``quantity * price`` (in rupees) and does not
    need to be passed explicitly.
    """

    date: _dt.date
    symbol: str
    security_name: str
    client_name: str
    side: str  # 'BUY' or 'SELL'
    quantity: int
    price: float
    remarks: str = ""
    kind: str = "bulk"  # 'bulk' | 'block' | 'short'
    value: float = field(init=False)

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {self.side!r}")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        # frozen dataclass: use object.__setattr__ for the derived field
        object.__setattr__(self, "value", round(self.quantity * self.price, 2))

    @property
    def value_cr(self) -> float:
        """Deal value in ₹ crore (1 crore = 10,000,000 rupees)."""
        return self.value / 1e7

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict (date as ISO string)."""
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Deal":
        """Inverse of :meth:`to_dict`. ``value`` is recomputed, not trusted."""
        date = d["date"]
        if isinstance(date, str):
            date = _dt.date.fromisoformat(date)
        return cls(
            date=date,
            symbol=d["symbol"],
            security_name=d.get("security_name", ""),
            client_name=d.get("client_name", ""),
            side=d["side"],
            quantity=int(d["quantity"]),
            price=float(d["price"]),
            remarks=d.get("remarks") or "",
            kind=d.get("kind", "bulk"),
        )
