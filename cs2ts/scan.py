"""Find items listed well below their recent sale price that still sell often.

The idea: buy the lowest listing, wait out the 7-day trade lock, and sell
near the 30-day median. An item only counts if the profit survives the sell
fee and the item is liquid enough to sell again.
"""

from dataclasses import dataclass

from .config import Config


@dataclass
class Signal:
    name: str
    buy_price: float
    target_price: float  # expected gross sell price (30-day median)
    net_profit: float    # target after sell fee, minus buy price
    net_roi: float
    discount: float      # how far the lowest listing is below the 30d median
    momentum: float      # 7d median / 30d median - 1
    volume_30d: int
    listings: int


def evaluate(row, cfg: Config) -> Signal | None:
    price, m7, m30 = row["min_price"], row["median_7d"], row["median_30d"]
    if not price or not m7 or not m30:
        return None
    if not cfg.min_price <= price <= cfg.max_price:
        return None
    if row["volume_30d"] < cfg.min_volume_30d or row["listings"] < cfg.min_listings:
        return None

    discount = 1 - price / m30
    momentum = m7 / m30 - 1
    net_profit = m30 * (1 - cfg.sell_fee) - price
    net_roi = net_profit / price

    if discount < cfg.min_discount:
        return None
    # A cheap listing on an item that is falling is a trap, not a deal.
    if momentum < -cfg.max_momentum_drop:
        return None
    if net_roi < cfg.min_net_roi:
        return None

    return Signal(
        name=row["name"], buy_price=price, target_price=m30,
        net_profit=net_profit, net_roi=net_roi, discount=discount,
        momentum=momentum, volume_30d=row["volume_30d"], listings=row["listings"],
    )


def scan(rows, cfg: Config) -> list[Signal]:
    signals = [s for r in rows if (s := evaluate(r, cfg))]
    return sorted(signals, key=lambda s: s.net_roi, reverse=True)
