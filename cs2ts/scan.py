"""Find items listed well below their recent sale price that still sell often.

The idea: buy the lowest listing, wait out the 7-day trade lock, and sell
near the 30-day median. An item only counts if the profit survives the sell
fee and the item is liquid enough to sell again.
"""

from dataclasses import asdict, dataclass

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
    ref_median: float | None = None  # sale median on other marketplaces
    buff_ask: float | None = None
    youpin_ask: float | None = None


def _optional(row, key):
    """row[key], or None for rows saved before the column existed."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def check(row, cfg: Config) -> tuple[Signal | None, list[str]]:
    """The item's numbers and the names of the filters it fails.

    Returns (None, ["no_data"]) when there are no prices to compute from.
    """
    price, m7, m30 = row["min_price"], row["median_7d"], row["median_30d"]
    if not price or not m7 or not m30:
        return None, ["no_data"]

    discount = 1 - price / m30
    momentum = m7 / m30 - 1
    net_profit = m30 * (1 - cfg.sell_fee) - price
    net_roi = net_profit / price
    sig = Signal(
        name=row["name"], buy_price=price, target_price=m30,
        net_profit=net_profit, net_roi=net_roi, discount=discount,
        momentum=momentum, volume_30d=row["volume_30d"], listings=row["listings"],
        ref_median=_optional(row, "ref_median"), buff_ask=_optional(row, "buff_ask"),
        youpin_ask=_optional(row, "youpin_ask"),
    )

    failed = []
    if not cfg.min_price <= price <= cfg.max_price:
        failed.append("price_range")
    if row["volume_30d"] < cfg.min_volume_30d or row["listings"] < cfg.min_listings:
        failed.append("liquidity")
    if discount < cfg.min_discount:
        failed.append("discount")
    # A cheap listing on an item that is falling is a trap, not a deal.
    if momentum < -cfg.max_momentum_drop:
        failed.append("falling")
    if net_roi < cfg.min_net_roi:
        failed.append("roi")
    # Our target is only CSFloat's view; if other marketplaces sell the item
    # well below it, the target is probably too optimistic.
    if sig.ref_median and m30 > sig.ref_median * (1 + cfg.max_ref_gap):
        failed.append("ref_gap")
    return sig, failed


def evaluate(row, cfg: Config) -> Signal | None:
    sig, failed = check(row, cfg)
    return None if failed else sig


def scan(rows, cfg: Config) -> list[Signal]:
    signals = [s for r in rows if (s := evaluate(r, cfg))]
    return sorted(signals, key=lambda s: s.net_roi, reverse=True)


def watchlist(rows, cfg: Config) -> list[dict]:
    """Every item with prices, closest to a buy signal (largest discount) first."""
    out = []
    for r in rows:
        sig, failed = check(r, cfg)
        if sig:
            out.append({**asdict(sig), "failed": failed})
    return sorted(out, key=lambda w: w["discount"], reverse=True)
