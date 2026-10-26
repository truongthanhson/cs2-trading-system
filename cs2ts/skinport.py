"""Skinport public API client.

Docs: https://docs.skinport.com/
Both endpoints are public (no API key). Skinport caches them for 5 minutes and
allows about 8 requests per 5 minutes, so one fetch per run is plenty.
The API requires Brotli encoding, which is why `brotli` is a dependency.
"""

import requests

BASE_URL = "https://api.skinport.com/v1"
CS2_APP_ID = 730


def _get(path: str, currency: str) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/{path}",
        params={"app_id": CS2_APP_ID, "currency": currency},
        headers={"Accept-Encoding": "br"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_items(currency: str = "USD") -> list[dict]:
    """Current listings: min_price, quantity, suggested_price, ... per item."""
    return _get("items", currency)


def fetch_sales_history(currency: str = "USD") -> list[dict]:
    """Aggregated sales (min/max/avg/median/volume) for 24h, 7d, 30d, 90d."""
    return _get("sales/history", currency)
