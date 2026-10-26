"""CSFloat public API client and snapshot builder.

The endpoints used here need no API key:
- /listings/price-list: lowest buy-now price (cents) and listing count for
  every item, in one call.
- /history/<name>/graph: one point per day with the average sale price
  (cents) and number of sales, newest first.

CSFloat has no bulk sales-history endpoint, so the graph is fetched per item
for a shortlist (the "universe"): items in the configured price range with
the most listings. The graph endpoint has its own rate limit (500 per window
at the time of writing), so the collector watches the remaining-requests
header and stops early instead of running into it.
"""

import statistics
import time
from datetime import date, timedelta
from urllib.parse import quote

import requests

BASE_URL = "https://csfloat.com/api/v1"
# Leave some headroom in the rate limit for manual use of the site.
RATE_LIMIT_RESERVE = 25


def _get(path: str) -> requests.Response:
    resp = requests.get(f"{BASE_URL}/{path}", timeout=60)
    resp.raise_for_status()
    return resp


def fetch_price_list() -> list[dict]:
    """[{market_hash_name, quantity, min_price (cents)}, ...] for every item."""
    return _get("listings/price-list").json()


def fetch_graph(name: str) -> tuple[list[dict], int | None]:
    """Daily [{day, avg_price (cents), count}], plus remaining rate limit."""
    resp = _get(f"history/{quote(name, safe='')}/graph")
    remaining = resp.headers.get("x-ratelimit-remaining")
    return resp.json(), int(remaining) if remaining is not None else None


def pick_universe(price_list: list[dict], min_price: float, max_price: float,
                  min_listings: int, size: int) -> list[dict]:
    """The most-listed items in the price range; listings stand in for liquidity."""
    lo, hi = min_price * 100, max_price * 100
    eligible = [p for p in price_list
                if p.get("min_price") and lo <= p["min_price"] <= hi
                and (p.get("quantity") or 0) >= min_listings]
    eligible.sort(key=lambda p: p["quantity"], reverse=True)
    return eligible[:size]


def summarize_graph(graph: list[dict], today: date) -> dict:
    """Median of daily average prices (USD) and total sales for 1/7/30/90 days."""
    points = []
    for g in graph:
        day = date.fromisoformat(g["day"][:10])
        if g.get("avg_price") is not None:
            points.append((day, g["avg_price"] / 100, g.get("count") or 0))

    out = {}
    for label, days in (("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90)):
        since = today - timedelta(days=days - 1)
        window = [(p, c) for d, p, c in points if since <= d <= today]
        out[f"median_{label}"] = round(statistics.median(p for p, _ in window), 2) if window else None
        out[f"volume_{label}"] = sum(c for _, c in window)
    return out


def collect(cfg, today: date | None = None, sleep=time.sleep, log=print) -> list[dict]:
    """Build snapshot rows (same shape as the Skinport collector) from CSFloat."""
    today = today or date.today()
    price_list = fetch_price_list()
    universe = pick_universe(price_list, cfg.min_price, cfg.max_price,
                             cfg.min_listings, cfg.csfloat_universe_size)
    log(f"CSFloat: {len(price_list)} items listed, fetching sales history for {len(universe)}.")

    rows = []
    for i, item in enumerate(universe):
        name = item["market_hash_name"]
        try:
            graph, remaining = fetch_graph(name)
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 429:
                log(f"Rate limited after {i} items; keeping what we have.")
                break
            log(f"Skipping {name}: {err}")
            continue
        rows.append({"name": name, "min_price": item["min_price"] / 100,
                     "listings": item["quantity"], **summarize_graph(graph, today)})
        if remaining is not None and remaining <= RATE_LIMIT_RESERVE:
            log(f"Rate limit nearly used up after {i + 1} items; stopping early.")
            break
        sleep(cfg.csfloat_request_delay)
    return rows
