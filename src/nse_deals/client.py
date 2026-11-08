"""HTTP client for NSE India bulk/block/short-selling data.

Two sources are supported:

* **Snapshot CSVs** on ``nsearchives.nseindia.com`` -- the latest trading
  day's bulk and block deals. No cookies needed; generally reliable.
* **Historical JSON** on ``www.nseindia.com/api/historicalOR/...`` -- any
  range up to one year. Requires cookies from a prior page visit and is
  frequently blocked (HTTP 401/403/503) by NSE's bot protection.

Please be polite: the client enforces a minimum interval between requests,
and you should cache results rather than polling.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from typing import List, Optional, Union

import requests

from .models import Deal
from .parse import parse_date, parse_historical_json, parse_snapshot_csv

BULK_CSV_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_CSV_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"
HOME_URL = "https://www.nseindia.com"
REPORT_PAGE_URL = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
HISTORICAL_API_URL = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"

OPTION_TYPES = {"bulk": "bulk_deals", "block": "block_deals", "short": "short_selling"}
MAX_RANGE_DAYS = 365

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

DateLike = Union[str, _dt.date]


class NSEError(RuntimeError):
    """Raised when NSE cannot be reached or returns an unusable response."""


class NSEClient:
    """Small, polite client for NSE deal data.

    :param timeout: per-request timeout in seconds.
    :param min_interval: minimum seconds between consecutive requests.
    :param retries: attempts for the historical API (which is flaky).
    :param backoff: base seconds for exponential backoff between retries.
    :param session: optional pre-configured :class:`requests.Session`.
    """

    def __init__(
        self,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        retries: int = 3,
        backoff: float = 2.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = max(1, retries)
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._last_request = 0.0
        self._lock = threading.Lock()
        self._warmed_up = False

    # -- internals ---------------------------------------------------------

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    # -- snapshot CSVs -----------------------------------------------------

    def _snapshot(self, url: str, kind: str) -> List[Deal]:
        try:
            resp = self._get(url)
        except requests.RequestException as exc:
            raise NSEError(f"Could not download {url}: {exc}") from exc
        if resp.status_code != 200:
            raise NSEError(f"{url} returned HTTP {resp.status_code}")
        resp.encoding = resp.encoding or "utf-8"
        return parse_snapshot_csv(resp.text, kind=kind)

    def latest_bulk(self) -> List[Deal]:
        """Bulk deals for the latest trading day (snapshot CSV)."""
        return self._snapshot(BULK_CSV_URL, "bulk")

    def latest_block(self) -> List[Deal]:
        """Block deals for the latest trading day (snapshot CSV)."""
        return self._snapshot(BLOCK_CSV_URL, "block")

    def latest(self, kind: str = "bulk") -> List[Deal]:
        """Dispatch to :meth:`latest_bulk` / :meth:`latest_block`."""
        if kind == "bulk":
            return self.latest_bulk()
        if kind == "block":
            return self.latest_block()
        raise ValueError("Snapshot CSVs exist only for 'bulk' and 'block'; use historical() for 'short'.")

    # -- historical JSON ---------------------------------------------------

    def _warm_up(self) -> None:
        """Visit the report page so NSE sets the cookies the API requires."""
        for url in (HOME_URL, REPORT_PAGE_URL):
            try:
                self._get(url, headers={"Accept": "text/html,application/xhtml+xml"})
            except requests.RequestException:
                pass  # the API call below will surface a clear error
        self._warmed_up = True

    def historical(self, kind: str, from_date: DateLike, to_date: DateLike) -> List[Deal]:
        """Deals of ``kind`` ('bulk' | 'block' | 'short') between two dates, inclusive.

        Dates may be :class:`datetime.date` or ``DD-MM-YYYY`` strings. The range
        may not exceed one year (an NSE limit).
        """
        if kind not in OPTION_TYPES:
            raise ValueError(f"kind must be one of {sorted(OPTION_TYPES)}")
        start, end = parse_date(from_date), parse_date(to_date)
        if start > end:
            raise ValueError("from_date must be on or before to_date")
        if (end - start).days > MAX_RANGE_DAYS:
            raise ValueError("NSE allows at most a one-year range; split the request.")

        params = {
            "optionType": OPTION_TYPES[kind],
            "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"),
        }
        headers = {"Referer": REPORT_PAGE_URL, "Accept": "application/json, text/plain, */*"}

        last_problem = "unknown error"
        for attempt in range(self.retries):
            if not self._warmed_up or attempt > 0:
                self._warm_up()
            try:
                resp = self._get(HISTORICAL_API_URL, params=params, headers=headers)
            except requests.RequestException as exc:
                last_problem = str(exc)
            else:
                if resp.status_code == 200:
                    try:
                        return parse_historical_json(resp.json(), kind=kind)
                    except ValueError:
                        last_problem = "response was not valid JSON (likely a bot-protection page)"
                else:
                    last_problem = f"HTTP {resp.status_code}"
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (2 ** attempt))

        raise NSEError(
            f"NSE historical API failed after {self.retries} attempt(s): {last_problem}. "
            "This endpoint is session-gated and often blocked for scripts or cloud IPs. "
            "For the latest trading day use latest_bulk()/latest_block() (snapshot CSVs, "
            "`nse-deals latest`), and build history by archiving those daily."
        )
