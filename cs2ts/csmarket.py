"""CSMarketAPI client: cross-market checks on top of the CSFloat snapshot.

Needs CSMARKETAPI_KEY in the environment (or in a .env file next to
config.json). Every call costs credits, reported in the x-quota-cost and
x-quota-remaining headers; the free plan has 20,000 a month:
- /v2/sales/latest/aggregate: 1 credit. Yesterday's sales per marketplace
  ({market, day, median_price, mean_price, volume}). CSFloat and DMarket
  only report a mean, which premium copies inflate, so they are ignored;
  Steam is ignored because its 15% fee and locked wallet inflate prices.
- /v2/listings/latest/aggregate with markets=...: 1 credit per market.
  Lowest ask per marketplace.

Each daily run checks sales for every item in the snapshot and listings on
BUFF163 and YouPin for the items closest to a buy signal.
"""

import os
from pathlib import Path

import requests

BASE_URL = "https://api.csmarketapi.com/v2"
# Never let a daily run drain the monthly quota below this.
CREDIT_RESERVE = 500
# Steam is listed as STEAMCOMMUNITY; SIH data also comes from the Steam market.
SKIP_SALES_MARKETS = {"CSFLOAT", "DMARKET", "STEAM", "STEAMCOMMUNITY", "SIH"}
ASK_MARKETS = ("BUFF163", "YOUPIN")


class OutOfCredits(Exception):
    pass


def load_key(env_file: str | Path = ".env") -> str | None:
    if key := os.environ.get("CSMARKETAPI_KEY"):
        return key
    path = Path(env_file)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "CSMARKETAPI_KEY" and value.strip():
                return value.strip().strip("'\"")
    return None


class Client:
    def __init__(self, key: str, session=None):
        self.key = key
        self.http = session or requests.Session()
        self.remaining: int | None = None

    def _get(self, path: str, params: list[tuple[str, str]]) -> dict:
        if self.remaining is not None and self.remaining <= CREDIT_RESERVE:
            raise OutOfCredits(f"{self.remaining} credits left")
        resp = self.http.get(f"{BASE_URL}/{path}", params=[*params, ("key", self.key)], timeout=60)
        resp.raise_for_status()
        if (left := resp.headers.get("x-quota-remaining")) is not None:
            self.remaining = int(left)
        return resp.json()

    def sales_latest(self, name: str) -> list[dict]:
        return self._get("sales/latest/aggregate", [("market_hash_name", name)]).get("sales") or []

    def asks(self, name: str, markets=ASK_MARKETS) -> dict[str, float]:
        data = self._get("listings/latest/aggregate",
                         [("market_hash_name", name), *(("markets", m) for m in markets)])
        return {l["market"]: l["ask_price"] for l in data.get("listings") or []
                if l.get("ask_price") is not None}


def reference_median(sales: list[dict]) -> tuple[float | None, int]:
    """Volume-weighted median of other marketplaces' sale medians."""
    points = sorted((s["median_price"], s.get("volume") or 0) for s in sales
                    if s.get("median_price") is not None and s.get("volume")
                    and s.get("market") not in SKIP_SALES_MARKETS)
    total = sum(v for _, v in points)
    if not total:
        return None, 0
    seen = 0
    for price, volume in points:
        seen += volume
        if seen * 2 >= total:
            return round(price, 2), total
    return None, 0  # unreachable


def enrich(client: Client, rows: list[dict], watch_size: int, log=print) -> None:
    """Add ref_median/ref_volume to every row, and BUFF/YouPin asks to the
    watch_size rows with the deepest discount, in place. Stops early, keeping
    what it has, when credits run low or the API fails.
    """
    def discount(r):
        return 1 - r["min_price"] / r["median_30d"] if r.get("min_price") and r.get("median_30d") else -1

    checked = 0
    try:
        for row in rows:
            row["ref_median"], row["ref_volume"] = reference_median(client.sales_latest(row["name"]))
            checked += 1
        for row in sorted(rows, key=discount, reverse=True)[:watch_size]:
            asks = client.asks(row["name"])
            row["buff_ask"], row["youpin_ask"] = asks.get("BUFF163"), asks.get("YOUPIN")
    except (OutOfCredits, requests.RequestException) as err:
        log(f"CSMarketAPI: stopping early: {err}")
    log(f"CSMarketAPI: sales checked for {checked} items, {client.remaining} credits left.")
