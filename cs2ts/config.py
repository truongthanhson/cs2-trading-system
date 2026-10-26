"""Load settings from config.json, falling back to the defaults below."""

import json
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    currency: str = "USD"
    db_path: str = "data/cs2ts.sqlite"

    # Skinport takes a cut when you sell. Check the current rate on their site.
    sell_fee: float = 0.08
    # Only look at items in this price range (USD).
    min_price: float = 2.0
    max_price: float = 300.0
    # Liquidity filters: sales in the last 30 days and listings right now.
    min_volume_30d: int = 60
    min_listings: int = 3
    # Lowest listing must be at least this far below the 30-day median.
    min_discount: float = 0.10
    # Profit after the sell fee, as a fraction of the buy price.
    min_net_roi: float = 0.05
    # Skip items whose 7-day median fell more than this vs the 30-day median.
    max_momentum_drop: float = 0.10

    # Paper trading.
    paper_budget: float = 100.0
    paper_max_positions: int = 5
    paper_max_per_position: float = 25.0
    trade_lock_days: int = 7
    max_hold_days: int = 45
    stop_loss: float = 0.20


def load_config(path: str | Path = "config.json") -> Config:
    path = Path(path)
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    return Config(**raw)
