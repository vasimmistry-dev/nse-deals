import datetime as dt
from pathlib import Path

import pytest

from nse_deals.models import Deal
from nse_deals.parse import (
    load_file,
    normalize_side,
    parse_date,
    parse_float,
    parse_historical_json,
    parse_int,
    parse_snapshot_csv,
)

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


# -- helpers ---------------------------------------------------------------

@pytest.mark.parametrize("raw", ["06-NOV-2026", "06-Nov-2026", "06-11-2026", "2026-11-06", " 06-nov-2026 "])
def test_parse_date_formats(raw):
    assert parse_date(raw) == dt.date(2026, 11, 6)


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_date("yesterday")


@pytest.mark.parametrize("raw,expected", [("12,50,000", 1250000), ("1,250,000", 1250000),
                                          (420000, 420000), (" 500 ", 500), ("-", 0), (None, 0)])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


def test_parse_float():
    assert parse_float("1,245.60") == pytest.approx(1245.6)
    assert parse_float(72.4) == pytest.approx(72.4)
    assert parse_float("") == 0.0


def test_normalize_side():
    assert normalize_side(" buy ") == "BUY"
    assert normalize_side("S") == "SELL"
    with pytest.raises(ValueError):
        normalize_side("HOLD")


# -- snapshot CSV ----------------------------------------------------------

def test_bulk_csv_parses_all_rows_with_padded_headers():
    deals = parse_snapshot_csv(read("bulk.csv"), kind="bulk")
    assert len(deals) == 8
    first = deals[0]
    assert first == Deal(
        date=dt.date(2026, 11, 6), symbol="ACMEIND", security_name="Acme Industries Limited",
        client_name="ALPHA GROWTH FUND LLP", side="BUY", quantity=1_250_000, price=412.35,
        remarks="", kind="bulk",
    )
    assert first.value == pytest.approx(515_437_500.0)


def test_bulk_csv_comma_price_and_remarks():
    deals = parse_snapshot_csv(read("bulk.csv"))
    chandra_sell = [d for d in deals if d.symbol == "CHANDRAPWR" and d.side == "SELL"][0]
    assert chandra_sell.price == pytest.approx(1248.0)
    assert chandra_sell.quantity == 120_000
    assert chandra_sell.remarks == "Weighted Avg Price"
    assert all(d.remarks in ("", "Weighted Avg Price") for d in deals)


def test_block_csv_with_bom():
    deals = parse_snapshot_csv(read("block.csv").lstrip(), kind="block")  # BOM handled by parser too
    raw = (FIX / "block.csv").read_bytes().decode("utf-8")  # keeps BOM
    assert raw.startswith("﻿")
    deals_bom = parse_snapshot_csv(raw, kind="block")
    assert len(deals) == len(deals_bom) == 3
    assert {d.kind for d in deals_bom} == {"block"}
    assert deals_bom[2].price == pytest.approx(1530.0)


def test_csv_skips_bad_rows_and_empty_input():
    text = read("bulk.csv") + "No Records Found,,,,,,,\n"
    assert len(parse_snapshot_csv(text)) == 8
    assert parse_snapshot_csv("") == []


def test_csv_missing_columns_raises():
    with pytest.raises(ValueError, match="missing expected columns"):
        parse_snapshot_csv("Foo,Bar\n1,2\n")


# -- historical JSON -------------------------------------------------------

def test_historical_json():
    deals = parse_historical_json(read("historical_bulk.json"), kind="bulk")
    assert len(deals) == 7
    d0 = deals[0]
    assert d0.date == dt.date(2026, 11, 3)
    assert d0.quantity == 420_000          # "4,20,000" string
    assert d0.remarks == ""                # JSON null
    assert deals[1].remarks == ""          # "-"
    assert deals[2].price == pytest.approx(409.75)  # numeric string
    assert deals[6].remarks == "Weighted Avg Price"


def test_historical_json_tolerates_bad_records_and_short_without_side():
    payload = {"data": [
        {"BD_DT_DATE": "06-Nov-2026", "BD_SYMBOL": "X", "BD_BUY_SELL": None, "BD_QTY_TRD": 1, "BD_TP_WATP": 1},
        {"BD_SYMBOL": "NODATE", "BD_BUY_SELL": "BUY"},
    ]}
    assert parse_historical_json(payload, kind="bulk") == []
    short = parse_historical_json(payload, kind="short")
    assert len(short) == 1 and short[0].side == "SELL" and short[0].kind == "short"
    assert parse_historical_json({"data": None}) == []


# -- file loading / round trip --------------------------------------------

def test_load_file_detects_format(tmp_path):
    assert len(load_file(str(FIX / "bulk.csv"))) == 8
    assert len(load_file(str(FIX / "block.csv"), kind="block")) == 3
    assert len(load_file(str(FIX / "historical_bulk.json"))) == 7


def test_deal_dict_round_trip():
    d = parse_snapshot_csv(read("bulk.csv"))[0]
    assert Deal.from_dict(d.to_dict()) == d
    assert d.to_dict()["date"] == "2026-11-06"


def test_deal_validation():
    with pytest.raises(ValueError):
        Deal(dt.date(2026, 1, 1), "X", "", "", "HOLD", 1, 1.0)
    with pytest.raises(ValueError):
        Deal(dt.date(2026, 1, 1), "X", "", "", "BUY", 1, 1.0, kind="odd")
