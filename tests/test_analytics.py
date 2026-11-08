import datetime as dt
from pathlib import Path

import pytest

from nse_deals import analytics as an
from nse_deals.parse import load_file

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def bulk():
    return load_file(str(FIX / "bulk.csv"))


@pytest.fixture
def history():
    return load_file(str(FIX / "historical_bulk.json"))


def test_to_crore():
    assert an.to_crore(1e7) == 1.0
    assert an.to_crore(622_800_000) == 62.28
    assert an.to_crore(515_437_500, ndigits=4) == 51.5438


def test_filter_by_symbol_client_side_value(bulk):
    assert len(an.filter_deals(bulk, symbol="acmeind")) == 2
    assert len(an.filter_deals(bulk, client="quantedge")) == 2
    assert {d.side for d in an.filter_deals(bulk, side="sell")} == {"SELL"}
    big = an.filter_deals(bulk, min_value=50 * 1e7)  # >= ₹50 Cr
    assert sorted(d.symbol for d in big) == ["ACMEIND", "ACMEIND", "CHANDRAPWR"]
    assert an.filter_deals(bulk, symbol="ACMEIND", side="BUY", client="alpha")[0].quantity == 1_250_000
    assert an.filter_deals(bulk, kind="block") == []


def test_net_by_symbol(bulk):
    net = an.net_by_symbol(bulk)
    assert [p.key for p in net] == ["CHANDRAPWR", "BHARATTEX", "ACMEIND", "DEVAPHARMA"]
    chandra = net[0]
    assert chandra.buy_qty == 500_000 and chandra.sell_qty == 120_000
    assert chandra.net_qty == 380_000
    assert chandra.net_value == pytest.approx(622_800_000 - 149_760_000)
    acme = net[2]
    assert acme.net_value == pytest.approx(-62_500.0)
    assert net[3].net_value == 0
    assert chandra.to_dict()["net_value_cr"] == 47.30


def test_net_by_client(bulk):
    net = {p.key: p for p in an.net_by_client(bulk)}
    assert net["QUANTEDGE TRADING LLP"].deals == 2
    assert net["QUANTEDGE TRADING LLP"].net_qty == 2_500
    assert net["GOLDEN PEAK SECURITIES"].net_qty == -120_000


def test_top_deals(bulk):
    top = an.top_deals(bulk, 3)
    assert [d.symbol for d in top] == ["CHANDRAPWR", "ACMEIND", "ACMEIND"]
    assert top[1].side == "SELL"  # 412.40 > 412.35
    assert len(an.top_deals(bulk, 100)) == 8


def test_repeat_buyers(history, bulk):
    rb = an.repeat_buyers(history)
    assert len(rb) == 1
    r = rb[0]
    assert (r.client_name, r.symbol, r.days) == ("ALPHA GROWTH FUND LLP", "ACMEIND", 3)
    assert r.dates == [dt.date(2026, 11, 3), dt.date(2026, 11, 5), dt.date(2026, 11, 6)]
    assert r.total_qty == 1_970_000
    assert an.repeat_buyers(history, min_days=4) == []
    assert an.repeat_buyers(bulk) == []  # single-day snapshot
    assert r.to_dict()["dates"][0] == "2026-11-03"


def test_same_day_round_trips(bulk, history):
    rt = an.same_day_round_trips(bulk)
    assert len(rt) == 1
    assert (rt[0].client_name, rt[0].symbol) == ("QUANTEDGE TRADING LLP", "BHARATTEX")
    assert rt[0].matched_qty == 308_000

    rt_h = an.same_day_round_trips(history)
    assert [(r.date, r.client_name, r.symbol) for r in rt_h] == [
        (dt.date(2026, 11, 5), "RAJ TRADERS", "GANGAMET")
    ]
    # Different clients on opposite sides of the same stock are NOT round trips.
    assert all(r.symbol != "ACMEIND" for r in rt)
