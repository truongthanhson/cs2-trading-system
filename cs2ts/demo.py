"""Fake price data so the dashboard can be tried before real history exists.

python -m cs2ts demo writes 60 days of random-walk prices for 40 made-up
items into data/demo.sqlite and runs paper trading over them. The numbers
mean nothing; they only exercise the charts and tables.
"""

import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from . import paper
from .config import Config
from .db import connect, save_snapshot

DEMO_DB = "data/demo.sqlite"

WEAPONS = ["AK-47", "M4A1-S", "M4A4", "AWP", "USP-S", "Glock-18", "Desert Eagle", "MP9"]
FINISHES = ["Slate", "Redline", "Neon Rider", "Asiimov", "Printstream", "Vulcan", "Fade", "Ice Coaled"]


def generate(cfg: Config, days: int = 60, seed: int = 7) -> Config:
    cfg = replace(cfg, db_path=DEMO_DB)
    Path(DEMO_DB).unlink(missing_ok=True)
    conn = connect(DEMO_DB)
    rng = random.Random(seed)

    items = []
    for i in range(40):
        name = f"{rng.choice(WEAPONS)} | {FINISHES[i % len(FINISHES)]} #{i} (Field-Tested)"
        items.append({"name": name, "fair": rng.uniform(3, 60), "vol": rng.randint(80, 3000)})

    history: dict[str, list[float]] = {it["name"]: [] for it in items}
    start = date.today() - timedelta(days=days - 1)
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        rows = []
        for it in items:
            it["fair"] *= 1 + rng.gauss(0, 0.015)
            sales = history[it["name"]]
            sales.append(it["fair"])
            # Now and then someone lists well under the going price.
            dip = rng.random() < 0.12
            price = it["fair"] * (rng.uniform(0.72, 0.88) if dip else rng.uniform(0.96, 1.02))
            med = lambda n: sorted(sales[-n:])[len(sales[-n:]) // 2]
            rows.append({
                "name": it["name"], "min_price": round(price, 2), "listings": rng.randint(5, 400),
                "median_24h": round(med(1), 2), "volume_24h": it["vol"] // 30,
                "median_7d": round(med(7), 2), "volume_7d": it["vol"] // 4,
                "median_30d": round(med(30), 2), "volume_30d": it["vol"],
                "median_90d": round(med(90), 2), "volume_90d": it["vol"] * 3,
            })
        save_snapshot(conn, day, rows)
        paper.step(conn, day, cfg)
    conn.close()
    return cfg
