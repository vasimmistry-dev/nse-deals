"""Command-line interface: ``nse-deals latest | history | summary``."""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import sys
from typing import List, Optional, Sequence

from . import __version__
from .analytics import filter_deals, net_by_client, net_by_symbol, to_crore, top_deals
from .client import NSEClient, NSEError
from .models import Deal
from .parse import load_file

CSV_FIELDS = ["date", "kind", "symbol", "security_name", "client_name", "side",
              "quantity", "price", "value", "remarks"]


# ---------------------------------------------------------------------------
# Formatting helpers (no third-party table library)
# ---------------------------------------------------------------------------

def indian_number(n: float, decimals: int = 0) -> str:
    """Format with Indian digit grouping: 12345678 -> '1,23,45,678'."""
    neg = n < 0
    s = f"{abs(n):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = whole + ("." + frac if frac else "")
    return "-" + out if neg else out


def _truncate(s: str, width: int) -> str:
    return s if len(s) <= width else s[: width - 1] + "…"


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]],
                 align_right: Sequence[bool] = ()) -> str:
    """Render a simple box-free text table."""
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    right = list(align_right) + [False] * (len(headers) - len(align_right))

    def fmt(row):
        return "  ".join(c.rjust(w) if right[i] else c.ljust(w)
                         for i, (c, w) in enumerate(zip(row, widths))).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def deals_table(deals: Sequence[Deal]) -> str:
    headers = ["Date", "Symbol", "Client", "Side", "Qty", "Price", "Value ₹Cr"]
    rows = [
        [d.date.strftime("%d-%b-%Y"), d.symbol, _truncate(d.client_name, 34), d.side,
         indian_number(d.quantity), f"{d.price:,.2f}", f"{to_crore(d.value):,.2f}"]
        for d in deals
    ]
    return render_table(headers, rows, align_right=[False, False, False, False, True, True, True])


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(deals: Sequence[Deal], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for d in deals:
            row = d.to_dict()
            w.writerow({k: row[k] for k in CSV_FIELDS})


def _json_envelope(kind: str, items: list, **extra) -> str:
    payload = {
        "source": "nse-deals",
        "version": __version__,
        "kind": kind,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _load(args, client_factory) -> List[Deal]:
    if args.input:
        return load_file(args.input, kind=args.kind)
    client = client_factory()
    if getattr(args, "from_date", None) or getattr(args, "to_date", None):
        if not (args.from_date and args.to_date):
            raise SystemExit("error: --from and --to must be given together")
        return client.historical(args.kind, args.from_date, args.to_date)
    return client.latest(args.kind)


def _apply_filters(deals: List[Deal], args) -> List[Deal]:
    return filter_deals(
        deals,
        symbol=args.symbol,
        client=args.client,
        side=args.side,
        min_value=(args.min_value_cr or 0) * 1e7,
    )


def _emit_deals(deals: List[Deal], args, out) -> None:
    if args.csv:
        _write_csv(deals, args.csv)
        print(f"Wrote {len(deals)} deals to {args.csv}", file=sys.stderr)
    if args.json:
        print(_json_envelope(args.kind, deals, deals=[d.to_dict() for d in deals]), file=out)
    elif not args.csv:
        if not deals:
            print("No deals found.", file=out)
        else:
            print(deals_table(deals), file=out)
            total = sum(d.value for d in deals)
            print(f"\n{len(deals)} deal(s), total value ₹{to_crore(total):,.2f} Cr", file=out)


def cmd_deals(args, client_factory, out) -> int:
    deals = _apply_filters(_load(args, client_factory), args)
    if args.top:
        deals = top_deals(deals, args.top)
    else:
        deals = sorted(deals, key=lambda d: (d.date, d.symbol, d.client_name))
    _emit_deals(deals, args, out)
    return 0


def cmd_summary(args, client_factory, out) -> int:
    deals = _apply_filters(_load(args, client_factory), args)
    positions = net_by_client(deals) if args.by == "client" else net_by_symbol(deals)
    if args.top:
        positions = positions[: args.top]
    if args.json:
        print(_json_envelope(args.kind, positions, by=args.by,
                             summary=[p.to_dict() for p in positions]), file=out)
        return 0
    if not positions:
        print("No deals found.", file=out)
        return 0
    headers = ["Symbol" if args.by == "symbol" else "Client", "Deals", "Buy Qty", "Sell Qty",
               "Net Qty", "Net ₹Cr"]
    rows = [
        [_truncate(p.key, 34), str(p.deals), indian_number(p.buy_qty), indian_number(p.sell_qty),
         indian_number(p.net_qty), f"{to_crore(p.net_value):,.2f}"]
        for p in positions
    ]
    print(render_table(headers, rows, align_right=[False, True, True, True, True, True]), file=out)
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nse-deals",
        description="NSE India bulk / block / short-selling deals from the command line.",
        epilog="Data © NSE India. Informational use only, not investment advice.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--kind", choices=["bulk", "block", "short"], default="bulk",
                        help="deal type (default: bulk; 'short' needs --from/--to)")
    common.add_argument("--input", metavar="FILE",
                        help="read a local NSE CSV / JSON file instead of downloading")
    common.add_argument("--symbol", help="only this symbol (exact match)")
    common.add_argument("--client", help="only clients whose name contains this text")
    common.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], help="only buys or sells")
    common.add_argument("--min-value-cr", type=float, metavar="CR",
                        help="only deals worth at least this many ₹ crore")
    common.add_argument("--top", type=int, metavar="N", help="show only the N largest")
    common.add_argument("--json", action="store_true", help="print JSON instead of a table")
    common.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds")

    latest = sub.add_parser("latest", parents=[common],
                            help="latest trading day's deals (NSE snapshot CSV)")
    latest.add_argument("--csv", metavar="FILE", help="also write deals to a CSV file")

    hist = sub.add_parser("history", parents=[common],
                          help="deals over a date range (NSE historical API, max 1 year)")
    hist.add_argument("--from", dest="from_date", required=True, metavar="DD-MM-YYYY")
    hist.add_argument("--to", dest="to_date", required=True, metavar="DD-MM-YYYY")
    hist.add_argument("--csv", metavar="FILE", help="also write deals to a CSV file")

    summ = sub.add_parser("summary", parents=[common],
                          help="net buy/sell per symbol (or per client)")
    summ.add_argument("--by", choices=["symbol", "client"], default="symbol")
    summ.add_argument("--from", dest="from_date", metavar="DD-MM-YYYY",
                      help="use historical API from this date (needs --to)")
    summ.add_argument("--to", dest="to_date", metavar="DD-MM-YYYY")
    return p


def main(argv: Optional[Sequence[str]] = None, client_factory=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if args.kind == "short" and args.command == "latest" and not args.input:
        print("error: there is no snapshot CSV for short selling; use `nse-deals history --kind short`.",
              file=sys.stderr)
        return 2
    if client_factory is None:
        def client_factory():
            return NSEClient(timeout=args.timeout)
    try:
        if args.command == "summary":
            return cmd_summary(args, client_factory, out)
        return cmd_deals(args, client_factory, out)
    except NSEError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
