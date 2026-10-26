"""Paper trading: act on scan signals with fake money and track the result.

Rules, run once per daily snapshot:
- An open position cannot be sold until the trade lock has passed.
- After that, sell when the lowest listing reaches the target, when the
  position is down more than the stop loss, or when it has been held too long.
- We assume we sell by undercutting the lowest listing by 1%, and the sell
  fee comes out of that.
- With free cash and free slots, buy one unit of each top signal not held yet.
"""

import sqlite3
from datetime import date

from .config import Config
from .db import snapshot
from .scan import scan

UNDERCUT = 0.99


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def exit_price(row) -> float:
    return row["min_price"] * UNDERCUT


def open_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM positions WHERE sell_day IS NULL").fetchall()


def cash(conn: sqlite3.Connection, cfg: Config) -> float:
    realized = conn.execute(
        "SELECT COALESCE(SUM(net_proceeds - buy_price), 0) FROM positions"
        " WHERE sell_day IS NOT NULL"
    ).fetchone()[0]
    locked = conn.execute(
        "SELECT COALESCE(SUM(buy_price), 0) FROM positions WHERE sell_day IS NULL"
    ).fetchone()[0]
    return cfg.paper_budget + realized - locked


def step(conn: sqlite3.Connection, day: str, cfg: Config) -> dict:
    snap = snapshot(conn, day)
    sold, bought = [], []

    for pos in open_positions(conn):
        row = snap.get(pos["name"])
        if row is None or not row["min_price"]:
            continue
        held = _days_between(pos["buy_day"], day)
        if held < cfg.trade_lock_days:
            continue
        price = exit_price(row)
        net = price * (1 - cfg.sell_fee)
        if price >= pos["target_price"]:
            reason = "target"
        elif net <= pos["buy_price"] * (1 - cfg.stop_loss):
            reason = "stop_loss"
        elif held >= cfg.max_hold_days:
            reason = "max_hold"
        else:
            continue
        conn.execute(
            "UPDATE positions SET sell_day=?, sell_price=?, net_proceeds=?, exit_reason=?"
            " WHERE id=?",
            (day, price, net, reason, pos["id"]),
        )
        sold.append((pos["name"], reason, net - pos["buy_price"]))

    held_names = {p["name"] for p in open_positions(conn)}
    slots = cfg.paper_max_positions - len(held_names)
    available = cash(conn, cfg)
    for sig in scan(snap.values(), cfg):
        if slots <= 0:
            break
        if sig.name in held_names:
            continue
        if sig.buy_price > min(available, cfg.paper_max_per_position):
            continue
        conn.execute(
            "INSERT INTO positions (name, buy_day, buy_price, target_price)"
            " VALUES (?, ?, ?, ?)",
            (sig.name, day, sig.buy_price, sig.target_price),
        )
        bought.append((sig.name, sig.buy_price))
        held_names.add(sig.name)
        available -= sig.buy_price
        slots -= 1

    conn.commit()
    return {"sold": sold, "bought": bought}


def report(conn: sqlite3.Connection, day: str, cfg: Config) -> dict:
    closed = conn.execute(
        "SELECT * FROM positions WHERE sell_day IS NOT NULL ORDER BY sell_day"
    ).fetchall()
    snap = snapshot(conn, day) if day else {}

    realized = sum(p["net_proceeds"] - p["buy_price"] for p in closed)
    wins = sum(1 for p in closed if p["net_proceeds"] > p["buy_price"])

    open_rows = []
    unrealized = 0.0
    for pos in open_positions(conn):
        row = snap.get(pos["name"])
        mark = exit_price(row) * (1 - cfg.sell_fee) if row and row["min_price"] else pos["buy_price"]
        unrealized += mark - pos["buy_price"]
        open_rows.append({
            "name": pos["name"], "buy_day": pos["buy_day"],
            "buy_price": pos["buy_price"], "mark": mark,
            "days_held": _days_between(pos["buy_day"], day) if day else 0,
        })

    return {
        "closed_trades": len(closed),
        "win_rate": wins / len(closed) if closed else None,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "equity": cfg.paper_budget + realized + unrealized,
        "return_pct": (realized + unrealized) / cfg.paper_budget,
        "cash": cash(conn, cfg),
        "open": open_rows,
        "closed": [dict(p) for p in closed],
    }
