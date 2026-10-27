"""Turn the two Skinport responses into one row per item."""


def _window(history: dict, key: str) -> tuple[float | None, int]:
    w = history.get(key) or {}
    return w.get("median"), w.get("volume") or 0


def merge(items: list[dict], history: list[dict]) -> list[dict]:
    by_name = {h["market_hash_name"]: h for h in history}
    rows = []
    for item in items:
        name = item["market_hash_name"]
        h = by_name.get(name, {})
        m24, v24 = _window(h, "last_24_hours")
        m7, v7 = _window(h, "last_7_days")
        m30, v30 = _window(h, "last_30_days")
        m90, v90 = _window(h, "last_90_days")
        rows.append({
            "name": name,
            "min_price": item.get("min_price"),
            "listings": item.get("quantity") or 0,
            "median_24h": m24, "volume_24h": v24,
            "median_7d": m7, "volume_7d": v7,
            "median_30d": m30, "volume_30d": v30,
            "median_90d": m90, "volume_90d": v90,
        })
    return rows
