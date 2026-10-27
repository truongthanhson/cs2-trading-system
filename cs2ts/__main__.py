"""Command line: python -m cs2ts <command>

  collect   fetch today's prices (CSFloat or Skinport) and save a snapshot
  scan      list current buy signals from the latest snapshot
  paper     run one paper-trading step on the latest snapshot
  report    show paper-trading profit and loss
  daily     collect + paper + report (run this once a day)
  dashboard open the web dashboard on http://localhost:8000
  demo      write fake data to try the dashboard (dashboard --demo)
  serve     dashboard + run `daily` every day at --daily-at (for servers)
"""

import argparse
import csv
import sys
from pathlib import Path
from datetime import date, timedelta

from . import csfloat, csmarket, paper, skinport
from .collect import merge
from .config import load_config
from .db import (EXTRA_SNAPSHOT_COLUMNS, connect, latest_day, recent_values, save_snapshot,
                 snapshot, update_snapshot)
from .scan import scan


def cmd_collect(conn, cfg, args):
    day = args.day or date.today().isoformat()
    if cfg.source == "csfloat":
        today = date.fromisoformat(day)
        since = (today - timedelta(days=6)).isoformat()
        past = recent_values(conn, "median_30d", since, day)
        rows = csfloat.collect(cfg, today, past_fair=past)
    elif cfg.source == "skinport":
        rows = merge(skinport.fetch_items(cfg.currency), skinport.fetch_sales_history(cfg.currency))
    else:
        sys.exit(f"Unknown source {cfg.source!r}; use \"csfloat\" or \"skinport\".")
    n = save_snapshot(conn, day, rows)
    print(f"Saved {n} items for {day}.")
    key = csmarket.load_key(Path(args.config).parent / ".env")
    if key:
        csmarket.enrich(csmarket.Client(key), rows, cfg.csmarket_watch_size)
        for r in rows:
            update_snapshot(conn, day, r["name"], **{c: r.get(c) for c in EXTRA_SNAPSHOT_COLUMNS})
        conn.commit()


def _require_day(conn):
    day = latest_day(conn)
    if day is None:
        sys.exit("No snapshot yet. Run `python -m cs2ts collect` first.")
    return day


def cmd_scan(conn, cfg, args):
    day = _require_day(conn)
    signals = scan(snapshot(conn, day).values(), cfg)
    print(f"{len(signals)} signals on {day}\n")
    print(f"{'item':<55} {'buy':>8} {'target':>8} {'net':>7} {'roi':>6} {'disc':>6} {'mom':>6} {'vol30':>6}")
    for s in signals[: args.top]:
        print(f"{s.name[:55]:<55} {s.buy_price:>8.2f} {s.target_price:>8.2f} "
              f"{s.net_profit:>7.2f} {s.net_roi:>6.1%} {s.discount:>6.1%} "
              f"{s.momentum:>+6.1%} {s.volume_30d:>6}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["name", "buy_price", "target_price", "net_profit", "net_roi",
                        "discount", "momentum", "volume_30d", "listings"])
            for s in signals:
                w.writerow([s.name, s.buy_price, s.target_price, round(s.net_profit, 2),
                            round(s.net_roi, 4), round(s.discount, 4),
                            round(s.momentum, 4), s.volume_30d, s.listings])
        print(f"\nWrote {args.csv}")


def cmd_paper(conn, cfg, args):
    day = _require_day(conn)
    result = paper.step(conn, day, cfg)
    for name, reason, pnl in result["sold"]:
        print(f"SELL {name}  ({reason}, {pnl:+.2f})")
    for name, price in result["bought"]:
        print(f"BUY  {name}  @ {price:.2f}")
    if not result["sold"] and not result["bought"]:
        print("No paper trades today.")


def cmd_report(conn, cfg, args):
    day = latest_day(conn)
    r = paper.report(conn, day, cfg)
    win = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "-"
    print(f"Paper account on {day}")
    print(f"  Start budget    {cfg.paper_budget:>9.2f}")
    print(f"  Equity          {r['equity']:>9.2f}  ({r['return_pct']:+.1%})")
    print(f"  Realized P&L    {r['realized_pnl']:>+9.2f}  from {r['closed_trades']} trades, win rate {win}")
    print(f"  Unrealized P&L  {r['unrealized_pnl']:>+9.2f}")
    print(f"  Cash            {r['cash']:>9.2f}")
    if r["open"]:
        print("\n  Open positions")
        for p in r["open"]:
            print(f"    {p['name'][:50]:<50} bought {p['buy_price']:>7.2f} on {p['buy_day']}"
                  f"  now {p['mark']:>7.2f} net  ({p['days_held']}d)")


def cmd_daily(conn, cfg, args):
    cmd_collect(conn, cfg, args)
    cmd_paper(conn, cfg, args)
    print()
    cmd_report(conn, cfg, args)


def cmd_serve(cfg, args):
    from . import scheduler
    from .dashboard import serve

    def job():
        conn = connect(cfg.db_path)
        try:
            cmd_daily(conn, cfg, argparse.Namespace(day=None))
        finally:
            conn.close()

    def already_done(day):
        conn = connect(cfg.db_path)
        try:
            return latest_day(conn) == day
        finally:
            conn.close()

    scheduler.start(job, already_done, args.daily_at)
    serve(cfg, host=args.host, port=args.port)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cs2ts", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("collect", "daily"):
        p = sub.add_parser(name)
        p.add_argument("--day", help="override the snapshot date (YYYY-MM-DD)")
    p = sub.add_parser("scan")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--csv", help="also write every signal to this CSV file")
    sub.add_parser("paper")
    sub.add_parser("report")
    p = sub.add_parser("dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--demo", action="store_true", help="show the fake data from `demo`")
    sub.add_parser("demo")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--daily-at", default="12:15", help="local time HH:MM for the daily run")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "demo":
        from .demo import generate
        generate(cfg)
        print("Wrote fake data to data/demo.sqlite. View it: python -m cs2ts dashboard --demo")
        return
    if args.command == "dashboard":
        from dataclasses import replace
        from .dashboard import serve
        from .demo import DEMO_DB
        return serve(replace(cfg, db_path=DEMO_DB) if args.demo else cfg,
                     host=args.host, port=args.port)
    if args.command == "serve":
        return cmd_serve(cfg, args)
    conn = connect(cfg.db_path)
    try:
        {
            "collect": cmd_collect, "scan": cmd_scan, "paper": cmd_paper,
            "report": cmd_report, "daily": cmd_daily,
        }[args.command](conn, cfg, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
