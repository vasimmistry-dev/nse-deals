"""nse-deals: NSE India bulk deals, block deals and short-selling data.

Quick start::

    from nse_deals import NSEClient, analytics

    deals = NSEClient().latest_bulk()
    for pos in analytics.net_by_symbol(deals)[:10]:
        print(pos.key, analytics.to_crore(pos.net_value))
"""

__version__ = "0.1.0"

from . import analytics  # noqa: E402
from .client import NSEClient, NSEError  # noqa: E402
from .models import Deal  # noqa: E402
from .parse import load_file, parse_historical_json, parse_snapshot_csv  # noqa: E402

__all__ = [
    "__version__",
    "analytics",
    "Deal",
    "NSEClient",
    "NSEError",
    "load_file",
    "parse_historical_json",
    "parse_snapshot_csv",
]
