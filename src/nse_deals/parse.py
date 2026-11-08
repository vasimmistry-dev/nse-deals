"""Parsers for the two NSE data formats.

1. Snapshot CSVs from ``nsearchives.nseindia.com`` (``bulk.csv`` / ``block.csv``).
2. JSON from the session-gated ``/api/historicalOR/bulk-block-short-deals`` API.

Both formats are messy in small ways (padded headers, Indian-style comma
grouping in numbers, ``-`` or ``null`` remarks), so every helper here is
deliberately forgiving and every parser is pure (no network), which makes
them easy to test with fixtures.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Union

from .models import Deal

_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d-%B-%Y")
_EMPTY = {"", "-", "--", "na", "n/a", "nil", "null", "none"}


def parse_date(value: Any) -> _dt.date:
    """Parse the date formats NSE uses (``07-Nov-2026``, ``07-NOV-2026``, ``07-11-2026``...)."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            # %b matching is case-insensitive in CPython, so "NOV" works too.
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {value!r}")


def parse_int(value: Any) -> int:
    """``"1,25,000"`` -> ``125000``. Empty / dash values become 0."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = re.sub(r"[,\s]", "", str(value))
    if s.lower() in _EMPTY:
        return 0
    return int(float(s))


def parse_float(value: Any) -> float:
    """``"1,234.50"`` -> ``1234.5``. Empty / dash values become 0.0."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[,\s₹]", "", str(value))
    if s.lower() in _EMPTY:
        return 0.0
    return float(s)


def clean_text(value: Any) -> str:
    """Strip whitespace; map ``None`` / ``-`` / ``null`` to ``""``."""
    if value is None:
        return ""
    s = " ".join(str(value).split())
    return "" if s.lower() in _EMPTY else s


def normalize_side(value: Any) -> str:
    """Map ``BUY``/``B``/``Purchase`` -> ``BUY`` and ``SELL``/``S``/``Sale`` -> ``SELL``."""
    s = clean_text(value).upper()
    if s in ("BUY", "B", "PURCHASE", "BOUGHT"):
        return "BUY"
    if s in ("SELL", "S", "SALE", "SOLD"):
        return "SELL"
    raise ValueError(f"Unrecognised buy/sell value: {value!r}")


# ---------------------------------------------------------------------------
# Snapshot CSV
# ---------------------------------------------------------------------------

def _header_key(header: str) -> Optional[str]:
    """Map a raw CSV header to a Deal field name, tolerating NSE's variations."""
    h = re.sub(r"[^a-z]", "", header.lower())
    if h == "date":
        return "date"
    if h == "symbol":
        return "symbol"
    if "security" in h or "scrip" in h:
        return "security_name"
    if "client" in h:
        return "client_name"
    if "buy" in h and "sell" in h:
        return "side"
    if "quantity" in h or h.startswith("qty"):
        return "quantity"
    if "price" in h:
        return "price"
    if "remark" in h:
        return "remarks"
    return None


def parse_snapshot_csv(text: str, kind: str = "bulk") -> List[Deal]:
    """Parse NSE's ``bulk.csv`` / ``block.csv`` text into :class:`Deal` objects.

    Rows that cannot be parsed (e.g. footer lines, "No Records") are skipped.
    """
    text = text.lstrip("﻿")  # BOM
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    keys = [_header_key(h) for h in rows[0]]
    required = {"date", "symbol", "side", "quantity", "price"}
    missing = required - set(keys)
    if missing:
        raise ValueError(f"CSV is missing expected columns: {sorted(missing)}; got headers {rows[0]}")

    deals: List[Deal] = []
    for raw in rows[1:]:
        rec: Dict[str, str] = {}
        for key, cell in zip(keys, raw):
            if key:
                rec[key] = cell.strip()
        try:
            deals.append(
                Deal(
                    date=parse_date(rec["date"]),
                    symbol=clean_text(rec["symbol"]).upper(),
                    security_name=clean_text(rec.get("security_name")),
                    client_name=clean_text(rec.get("client_name")),
                    side=normalize_side(rec["side"]),
                    quantity=parse_int(rec["quantity"]),
                    price=parse_float(rec["price"]),
                    remarks=clean_text(rec.get("remarks")),
                    kind=kind,
                )
            )
        except (KeyError, ValueError):
            continue
    return deals


# ---------------------------------------------------------------------------
# Historical JSON
# ---------------------------------------------------------------------------

# Field names observed in the historicalOR API (BD_ prefix). Matching is done
# on the part after the first underscore so a different prefix (e.g. for the
# short-selling report) still maps, but see README: short-selling field names
# have not been verified against a live response.
_JSON_FIELDS = {
    "DT_DATE": "date",
    "SYMBOL": "symbol",
    "SCRIP_NAME": "security_name",
    "CLIENT_NAME": "client_name",
    "BUY_SELL": "side",
    "QTY_TRD": "quantity",
    "TP_WATP": "price",
    "REMARKS": "remarks",
}


def _strip_prefix(key: str) -> str:
    return key.split("_", 1)[1] if "_" in key else key


def parse_historical_json(payload: Union[str, bytes, Dict[str, Any], Iterable[Dict[str, Any]]],
                          kind: str = "bulk") -> List[Deal]:
    """Parse the historicalOR JSON (``{"data": [...]}``) into :class:`Deal` objects."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        records = payload.get("data") or []
    else:
        records = list(payload)

    deals: List[Deal] = []
    for item in records:
        rec: Dict[str, Any] = {}
        for k, v in item.items():
            target = _JSON_FIELDS.get(_strip_prefix(k))
            if target and target not in rec:
                rec[target] = v
        try:
            side_raw = rec.get("side")
            # Short-selling rows may have no buy/sell column: a short sale is a sell.
            side = normalize_side(side_raw) if clean_text(side_raw) else ("SELL" if kind == "short" else None)
            if side is None:
                raise ValueError("missing side")
            deals.append(
                Deal(
                    date=parse_date(rec["date"]),
                    symbol=clean_text(rec["symbol"]).upper(),
                    security_name=clean_text(rec.get("security_name")),
                    client_name=clean_text(rec.get("client_name")),
                    side=side,
                    quantity=parse_int(rec.get("quantity")),
                    price=parse_float(rec.get("price")),
                    remarks=clean_text(rec.get("remarks")),
                    kind=kind,
                )
            )
        except (KeyError, ValueError):
            continue
    return deals


def load_file(path: str, kind: str = "bulk") -> List[Deal]:
    """Load deals from a local file: NSE CSV, NSE historical JSON, or JSON written by ``nse-deals --json``."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        payload = json.loads(text)
        deals_list = payload.get("deals") if isinstance(payload, dict) else None
        if deals_list is not None:
            return [Deal.from_dict(d) for d in deals_list]
        if isinstance(payload, list) and payload and "symbol" in payload[0]:
            return [Deal.from_dict(d) for d in payload]
        return parse_historical_json(payload, kind=kind)
    return parse_snapshot_csv(text, kind=kind)
