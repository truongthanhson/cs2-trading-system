"""CSFloat public API client and snapshot builder.

The endpoints used here need no API key:
- /listings/price-list: lowest buy-now price (cents) and listing count for
  every item, in one call.
- /history/<name>/graph: one point per day with the average sale price
  (cents) and number of sales, newest first.
- /history/<name>/sales: the last 40 sales, each with its price and
  CSFloat's reference `base_price` for a plain copy of the item (cents).

Why two history calls per item: the graph's daily average includes premium
copies (stickers, low float, rare patterns, Doppler phases), which sell far
above an ordinary copy and drag the average up. Buying the cheapest listing
and expecting it to resell at that average is a trap. So the price we expect
to sell at (the "fair price") comes from recent sales of ordinary copies:
sales priced within PREMIUM_CUTOFF of their own base_price. The graph is
still used for sales volume.

The graph's daily average is still skewed by premium copies, so it is too
noisy for a trend (a few sticker crafts can move it 40%). The trend instead
comes from our own history of fair prices, saved with every daily snapshot.

In the snapshot rows, median_30d holds today's fair price (the target).
median_7d is set so that the scanner's momentum, median_7d / median_30d - 1,
equals today's fair price against the median fair price of the previous six
snapshot days: negative when the item is getting cheaper. With no earlier
snapshots, median_7d equals today's fair price (no trend filter).

CSFloat has no bulk sales-history endpoint, so history is fetched per item
for a shortlist (the "universe"): items in the configured price range with
the most listings. The history endpoints have their own rate limit (500 per
window at the time of writing), so the collector watches the
remaining-requests header and stops early instead of running into it.
"""

import statistics
import time
from datetime import date, timedelta
from urllib.parse import quote

import requests

BASE_URL = "https://csfloat.com/api/v1"
# Leave some headroom in the rate limit for manual use of the site.
RATE_LIMIT_RESERVE = 25
# A sale counts as an ordinary copy if it sold at most this much above its base price.
PREMIUM_CUTOFF = 1.15
# Fewer ordinary sales than this and the fair price is too noisy to trust.
MIN_ORDINARY_SALES = 5


def _get(path: str) -> tuple[object, int | None]:
    resp = requests.get(f"{BASE_URL}/{path}", timeout=60)
    resp.raise_for_status()
    remaining = resp.headers.get("x-ratelimit-remaining")
    return resp.json(), int(remaining) if remaining is not None else None


def fetch_price_list() -> list[dict]:
    """[{market_hash_name, quantity, min_price (cents)}, ...] for every item."""
    return _get("listings/price-list")[0]


def fetch_graph(name: str) -> tuple[list[dict], int | None]:
    """Daily [{day, avg_price (cents), count}], plus remaining rate limit."""
    return _get(f"history/{quote(name, safe='')}/graph")


def fetch_sales(name: str) -> tuple[list[dict], int | None]:
    """Last 40 sales [{price, sold_at, reference: {base_price}, ...}], plus remaining rate limit."""
    return _get(f"history/{quote(name, safe='')}/sales")


def pick_universe(price_list: list[dict], min_price: float, max_price: float,
                  min_listings: int, size: int) -> list[dict]:
    """The most-listed items in the price range; listings stand in for liquidity."""
    lo, hi = min_price * 100, max_price * 100
    eligible = [p for p in price_list
                if p.get("min_price") and lo <= p["min_price"] <= hi
                and (p.get("quantity") or 0) >= min_listings]
    eligible.sort(key=lambda p: p["quantity"], reverse=True)
    return eligible[:size]


def fair_price(sales: list[dict]) -> float | None:
    """Median price (USD) of recent sales of ordinary copies."""
    ordinary = []
    for s in sales:
        base = (s.get("reference") or {}).get("base_price")
        if s.get("price") and base and s["price"] <= base * PREMIUM_CUTOFF:
            ordinary.append(s["price"] / 100)
    if len(ordinary) < MIN_ORDINARY_SALES:
        return None
    return round(statistics.median(ordinary), 2)


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


def build_row(item: dict, graph: list[dict], sales: list[dict], today: date,
              past_fair: list[float] = ()) -> dict:
    """past_fair: this item's fair prices from earlier snapshots in the last 6 days."""
    g = summarize_graph(graph, today)
    fair = fair_price(sales)
    trend = fair / statistics.median(past_fair) if fair and past_fair else 1.0
    return {
        "name": item["market_hash_name"],
        "min_price": item["min_price"] / 100,
        "listings": item["quantity"],
        "median_24h": g["median_24h"], "volume_24h": g["volume_24h"],
        "median_7d": round(fair * trend, 2) if fair else None,
        "volume_7d": g["volume_7d"],
        "median_30d": fair, "volume_30d": g["volume_30d"],
        "median_90d": g["median_90d"], "volume_90d": g["volume_90d"],
    }


def collect(cfg, today: date | None = None, sleep=time.sleep, log=print,
            past_fair: dict[str, list[float]] | None = None) -> list[dict]:
    """Build snapshot rows (same shape as the Skinport collector) from CSFloat.

    past_fair maps item name to its fair prices from the previous 6 days.
    """
    past_fair = past_fair or {}
    today = today or date.today()
    price_list = fetch_price_list()
    universe = pick_universe(price_list, cfg.min_price, cfg.max_price,
                             cfg.min_listings, cfg.csfloat_universe_size)
    log(f"CSFloat: {len(price_list)} items listed, fetching sales history for {len(universe)}.")

    rows = []
    for i, item in enumerate(universe):
        name = item["market_hash_name"]
        try:
            graph, _ = fetch_graph(name)
            sleep(cfg.csfloat_request_delay)
            sales, remaining = fetch_sales(name)
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 429:
                log(f"Rate limited after {i} items; keeping what we have.")
                break
            log(f"Skipping {name}: {err}")
            continue
        rows.append(build_row(item, graph, sales, today, past_fair.get(name, [])))
        if remaining is not None and remaining <= RATE_LIMIT_RESERVE:
            log(f"Rate limit nearly used up after {i + 1} items; stopping early.")
            break
        sleep(cfg.csfloat_request_delay)
    return rows
