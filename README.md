# nse-deals

[![tests](https://github.com/vasimmistry-dev/nse-deals/actions/workflows/tests.yml/badge.svg)](https://github.com/vasimmistry-dev/nse-deals/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

A small Python library, command-line tool and embeddable HTML viewer for **NSE India bulk deals, block deals and short-selling data**.

It is an open-source spin-off of [TickerKhoj](https://tickerkhoj.com), a free Indian stock analysis site. The site needed a dependable way to fetch, clean and summarise the daily deal disclosures, so this code is now a standalone project anyone can use.

- **Library**: typed `Deal` records, forgiving parsers for NSE's CSV and JSON formats, and a polite HTTP client.
- **Analytics**: net buying/selling per stock and per client, top deals, repeat buyers, and same-day buy+sell (likely intraday/arbitrage) flags.
- **CLI**: `nse-deals latest | history | summary`, with plain-text tables, `--json` and `--csv` output.
- **Widget**: `widget/index.html`, a single self-contained page (no build step, no external requests) for browsing a deals file on a phone or desktop.

The only runtime dependency is [`requests`](https://pypi.org/project/requests/).

## Why this data matters

SEBI requires large trades to be disclosed so that the market can see them:

- **Bulk deals**: when a client's total trades in a stock on one day add up to more than **0.5% of the company's listed shares**, the broker must report it to the exchange the same day, and the exchange publishes it after market hours.
- **Block deals**: large single trades executed in the exchanges' separate block-deal trading windows, subject to a SEBI minimum order size (₹25 crore under SEBI's circular of 8 October 2025).
- **Short selling**: exchanges publish reported short-sale data under SEBI's short-selling framework.

The disclosures are public, but the raw files are awkward to use: padded headers, Indian digit grouping (`12,50,000`), mixed date formats, `-` or `null` in empty fields, and an API that often blocks scripts. This project handles those details. SEBI rules change from time to time, so check SEBI and NSE for the current thresholds.

## Install

```bash
pip install git+https://github.com/vasimmistry-dev/nse-deals.git
# or, from a clone:
pip install -e ".[test]"
```

Requires Python 3.9 or newer.

## Command line

```bash
# Latest trading day's bulk deals, 20 largest by value
nse-deals latest --kind bulk --top 20

# Block deals for October 2026, saved to CSV (historical API, max 1 year per request)
nse-deals history --kind block --from 01-10-2026 --to 31-10-2026 --csv out.csv

# Net buy/sell per symbol for the latest bulk deals
nse-deals summary --kind bulk

# ...or per client, as JSON
nse-deals summary --kind bulk --by client --json

# Filters work on every command
nse-deals latest --kind bulk --side BUY --min-value-cr 10 --client "mutual fund"

# Work offline from a file you already have (NSE CSV, NSE JSON, or nse-deals JSON)
nse-deals summary --input bulk.csv
```

Example output (from the synthetic test fixture):

```text
$ nse-deals summary --input tests/fixtures/bulk.csv
Symbol      Deals    Buy Qty   Sell Qty   Net Qty  Net ₹Cr
----------  -----  ---------  ---------  --------  -------
CHANDRAPWR      2   5,00,000   1,20,000  3,80,000    47.30
BHARATTEX       2   3,10,500   3,08,000     2,500     0.01
ACMEIND         2  12,50,000  12,50,000         0    -0.01
DEVAPHARMA      2   2,75,000   2,75,000         0     0.00
```

`--json` prints `{"source": "nse-deals", "kind": ..., "generated_at": ..., "count": ..., "deals": [...]}`, which the widget can load directly. Exit codes: `0` success, `1` NSE/network error, `2` invalid input.

## Python

```python
from nse_deals import NSEClient, analytics as an

client = NSEClient(timeout=15, min_interval=1.0)   # at least 1 s between requests
deals = client.latest_bulk()                         # or latest_block()

for d in an.top_deals(deals, 5):
    print(d.date, d.symbol, d.client_name, d.side, d.quantity, d.price, an.to_crore(d.value))

# Net position per stock, largest absolute net value first
for pos in an.net_by_symbol(deals)[:10]:
    print(pos.key, pos.net_qty, an.to_crore(pos.net_value))

# Only buys of at least ₹10 crore by clients with "fund" in their name
big_fund_buys = an.filter_deals(deals, side="BUY", client="fund", min_value=10 * 1e7)

# Historical range (session-gated, see caveats below)
history = client.historical("bulk", "01-10-2026", "31-10-2026")
for r in an.repeat_buyers(history, min_days=2):
    print(r.client_name, "bought", r.symbol, "on", r.days, "days")
for rt in an.same_day_round_trips(history):
    print(rt.date, rt.client_name, rt.symbol, "matched qty", rt.matched_qty)
```

Each `Deal` has: `date` (`datetime.date`), `symbol`, `security_name`, `client_name`, `side` (`'BUY'`/`'SELL'`), `quantity` (int), `price` (float), `value` (₹, `quantity * price`), `remarks`, and `kind` (`'bulk'`/`'block'`/`'short'`). `Deal.to_dict()` and `Deal.from_dict()` convert to and from JSON-friendly dicts.

The parsers (`parse_snapshot_csv`, `parse_historical_json`, `load_file`) make no network calls, so you can use them on files you archive yourself.

## Data sources and how reliable they are

| Source | URL | Needs session? | Reliability |
|---|---|---|---|
| Snapshot CSV (latest day) | `nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv` | No (browser User-Agent sent) | Generally reliable. Contains only the most recent trading day. |
| Historical JSON | `www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals\|block_deals\|short_selling&from=DD-MM-YYYY&to=DD-MM-YYYY` | Yes: the client first loads the report page for cookies and sends it as `Referer` | Unreliable for scripts. Often returns HTTP 401/403/503 from bot protection, especially from cloud servers. Max range 1 year. |

Notes and caveats:

- **Historical data**: `historical()` retries with exponential backoff, then raises `NSEError` with a message pointing to the snapshot CSVs. For a dependable history, run `nse-deals latest --json` once a day (for example from cron after about 6 pm IST) and keep the files.
- **Short selling** is available only through the historical API. The parser maps fields by name after the prefix, but **the short-selling response format has not been checked against a live response**. Please open an issue if it does not parse.
- NSE can change URLs, headers or formats without notice. The parsers try to be tolerant (headers are matched loosely; bad rows are skipped instead of crashing), but check your output.
- **Limitations of this release**: the live NSE endpoints could not be tested in the development environment, which blocks NSE. All tests use synthetic fixtures based on the documented formats. If a live format differs, a bug report with a sample row is very welcome.

## The HTML widget

`widget/index.html` is a single file with no dependencies. Open it locally or host it anywhere static. It can:

- load an NSE `bulk.csv`/`block.csv`, an NSE historical JSON, or `nse-deals --json` output, using a file picker, drag and drop, or a URL;
- show a sortable, searchable table with Buy/Sell filters and a minimum ₹ crore filter;
- show **net by symbol**, **top deals by value in ₹ crore**, and an `RT` badge where the same client bought and sold the same stock on the same day;
- follow the system light/dark setting, with a manual toggle, and work on narrow phone screens;
- load data automatically with `index.html?src=/data/bulk.json`, which is useful for embedding in an `<iframe>`.

**Browsers cannot fetch from NSE directly.** NSE does not send CORS headers, and its bot protection blocks browser `fetch()` calls from other origins. Get the data on your own server instead, for example:

```bash
# cron, weekdays at 18:30 IST
nse-deals latest --kind bulk  --json > /var/www/site/data/bulk.json
nse-deals latest --kind block --json > /var/www/site/data/block.json
```

Then point the widget at `/data/bulk.json`. This is how TickerKhoj is designed to use it.

## Development

```bash
git clone https://github.com/vasimmistry-dev/nse-deals.git
cd nse-deals
pip install -e ".[test]"
pytest
```

Tests never touch the network. They use a fake HTTP session and the files in `tests/fixtures/`. **The fixture data is synthetic**: the companies, clients and trades are made up to match NSE's formats, and they are not real disclosures. CI runs on Python 3.10 to 3.13. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Project layout

```text
src/nse_deals/
  models.py     Deal dataclass
  parse.py      CSV / JSON parsers (pure, no network)
  client.py     NSEClient: snapshot CSVs, historical API, rate limiting, retries
  analytics.py  filters, net positions, top deals, repeat buyers, round trips
  cli.py        argparse CLI
widget/index.html  self-contained browser viewer
tests/             pytest suite + synthetic fixtures
```

## Disclaimer

- This project is **not affiliated with, endorsed by, or connected to the National Stock Exchange of India (NSE)** or SEBI. "NSE" is used only to describe the data source.
- The data is provided **for information and education only. It is not investment advice** and should not be your only basis for any trading decision. Disclosures can be delayed, revised or incomplete, and this software may parse them incorrectly. Check important figures against the official NSE website.
- **Follow NSE's terms of use and rate limits.** The client waits at least 1 second between requests by default. Do not lower that, run parallel scrapers or poll repeatedly. Fetch once a day and cache the result.
- The software is provided "as is" under the MIT License, without warranty of any kind.

## License

[MIT](LICENSE) © 2026 Vasim Mistry
