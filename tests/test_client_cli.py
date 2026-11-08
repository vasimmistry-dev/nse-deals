"""Client and CLI tests. No real network: a fake session serves fixture files."""

import io
import json
from pathlib import Path

import pytest
import requests

from nse_deals import cli
from nse_deals.client import (
    BLOCK_CSV_URL,
    BULK_CSV_URL,
    HISTORICAL_API_URL,
    REPORT_PAGE_URL,
    NSEClient,
    NSEError,
)

FIX = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status=200, text="", json_data=None):
        self.status_code = status
        self.text = text
        self.encoding = "utf-8"
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # url -> FakeResponse or list of them (consumed in order)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        r = self.routes.get(url, FakeResponse(404))
        if isinstance(r, Exception):
            raise r
        if isinstance(r, list):
            return r.pop(0) if len(r) > 1 else r[0]
        return r


def make_client(routes, **kw):
    kw.setdefault("min_interval", 0)
    kw.setdefault("backoff", 0)
    return NSEClient(session=FakeSession(routes), **kw)


def fixture_routes():
    return {
        BULK_CSV_URL: FakeResponse(text=(FIX / "bulk.csv").read_text(encoding="utf-8")),
        BLOCK_CSV_URL: FakeResponse(text=(FIX / "block.csv").read_text(encoding="utf-8")),
        HISTORICAL_API_URL: FakeResponse(json_data=json.loads((FIX / "historical_bulk.json").read_text())),
    }


# -- client ----------------------------------------------------------------

def test_latest_bulk_and_block_send_browser_user_agent():
    c = make_client(fixture_routes())
    assert len(c.latest_bulk()) == 8
    assert len(c.latest_block()) == 3
    assert "Mozilla" in c.session.headers["User-Agent"]
    assert all(kw.get("timeout") == c.timeout for _, kw in c.session.calls)


def test_latest_http_error_raises_nseerror():
    c = make_client({BULK_CSV_URL: FakeResponse(403)})
    with pytest.raises(NSEError, match="HTTP 403"):
        c.latest_bulk()
    c2 = make_client({BULK_CSV_URL: requests.ConnectionError("blocked")})
    with pytest.raises(NSEError, match="Could not download"):
        c2.latest_bulk()


def test_latest_short_not_available():
    with pytest.raises(ValueError):
        make_client({}).latest("short")


def test_historical_warms_up_and_sends_referer():
    c = make_client(fixture_routes())
    deals = c.historical("bulk", "01-11-2026", "07-11-2026")
    assert len(deals) == 7
    urls = [u for u, _ in c.session.calls]
    assert urls.index(REPORT_PAGE_URL) < urls.index(HISTORICAL_API_URL)
    _, kw = c.session.calls[urls.index(HISTORICAL_API_URL)]
    assert kw["headers"]["Referer"] == REPORT_PAGE_URL
    assert kw["params"] == {"optionType": "bulk_deals", "from": "01-11-2026", "to": "07-11-2026"}


def test_historical_retries_then_succeeds():
    routes = fixture_routes()
    good = routes[HISTORICAL_API_URL]
    routes[HISTORICAL_API_URL] = [FakeResponse(503), FakeResponse(200, text="<html>"), good]
    c = make_client(routes, retries=3)
    assert len(c.historical("bulk", "01-11-2026", "07-11-2026")) == 7


def test_historical_fails_with_helpful_message():
    c = make_client({HISTORICAL_API_URL: FakeResponse(503)}, retries=2)
    with pytest.raises(NSEError) as exc:
        c.historical("block", "01-10-2026", "31-10-2026")
    msg = str(exc.value)
    assert "HTTP 503" in msg and "2 attempt" in msg and "snapshot CSV" in msg


def test_historical_validates_range():
    c = make_client({})
    with pytest.raises(ValueError, match="one-year"):
        c.historical("bulk", "01-01-2025", "01-06-2026")
    with pytest.raises(ValueError, match="on or before"):
        c.historical("bulk", "10-11-2026", "01-11-2026")
    with pytest.raises(ValueError):
        c.historical("odd", "01-11-2026", "02-11-2026")


def test_rate_limit_spaces_requests(monkeypatch):
    sleeps = []
    monkeypatch.setattr("nse_deals.client.time.sleep", lambda s: sleeps.append(s))
    c = make_client(fixture_routes(), min_interval=5)
    c.latest_bulk()
    c.latest_bulk()
    assert sleeps and sleeps[-1] > 4


# -- CLI -------------------------------------------------------------------

def run_cli(argv, routes=None):
    out = io.StringIO()
    factory = lambda: make_client(routes or fixture_routes())  # noqa: E731
    code = cli.main(argv, client_factory=factory, out=out)
    return code, out.getvalue()


def test_indian_number():
    assert cli.indian_number(12345678) == "1,23,45,678"
    assert cli.indian_number(999) == "999"
    assert cli.indian_number(-1250000) == "-12,50,000"
    assert cli.indian_number(1234.5, 2) == "1,234.50"


def test_cli_latest_table_top():
    code, out = run_cli(["latest", "--kind", "bulk", "--top", "2"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("Date")
    assert "CHANDRAPWR" in lines[2] and "62.28" in lines[2]
    assert "2 deal(s)" in out


def test_cli_latest_json_and_filters():
    code, out = run_cli(["latest", "--kind", "block", "--json", "--side", "buy", "--min-value-cr", "100"])
    payload = json.loads(out)
    assert code == 0 and payload["kind"] == "block" and payload["count"] == 2
    assert {d["symbol"] for d in payload["deals"]} == {"EKTAFIN", "FALCONAUTO"}


def test_cli_history_writes_csv(tmp_path):
    target = tmp_path / "out.csv"
    code, _ = run_cli(["history", "--kind", "bulk", "--from", "01-11-2026", "--to", "07-11-2026",
                       "--csv", str(target)])
    assert code == 0
    rows = target.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("date,kind,symbol")
    assert len(rows) == 8


def test_cli_summary_from_input_file():
    code, out = run_cli(["summary", "--input", str(FIX / "bulk.csv")])
    assert code == 0
    assert out.splitlines()[2].startswith("CHANDRAPWR")
    code, out = run_cli(["summary", "--input", str(FIX / "historical_bulk.json"), "--by", "client", "--json"])
    payload = json.loads(out)
    assert payload["by"] == "client"
    assert payload["summary"][0]["key"] == "ALPHA GROWTH FUND LLP"


def test_cli_json_output_reloadable(tmp_path):
    code, out = run_cli(["latest", "--json"])
    path = tmp_path / "deals.json"
    path.write_text(out, encoding="utf-8")
    code, out2 = run_cli(["latest", "--input", str(path), "--json"])
    assert json.loads(out2)["deals"] == json.loads(out)["deals"]


def test_cli_error_paths(capsys):
    code, _ = run_cli(["history", "--from", "01-11-2026", "--to", "07-11-2026"],
                      routes={HISTORICAL_API_URL: FakeResponse(503)})
    assert code == 1
    assert "snapshot CSV" in capsys.readouterr().err
    code, _ = run_cli(["latest", "--kind", "short"])
    assert code == 2
