import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from datetime import date

from cs2ts import csfloat, csmarket, paper
from cs2ts.__main__ import main
from cs2ts.collect import merge
from cs2ts.config import Config, load_config
from cs2ts.db import connect, save_snapshot, snapshot, update_snapshot
from cs2ts.scan import check, evaluate, scan

FIXTURES = Path(__file__).parent / "fixtures"


def row(name="AK-47 | Slate (Field-Tested)", price=8.0, m7=10.0, m30=10.0,
        vol30=200, listings=50):
    return {
        "name": name, "min_price": price, "listings": listings,
        "median_24h": m7, "volume_24h": vol30 // 30,
        "median_7d": m7, "volume_7d": vol30 // 4,
        "median_30d": m30, "volume_30d": vol30,
        "median_90d": m30, "volume_90d": vol30 * 3,
    }


class MergeTest(unittest.TestCase):
    def test_merges_items_and_history(self):
        items = json.loads((FIXTURES / "items.json").read_text())
        history = json.loads((FIXTURES / "sales_history.json").read_text())
        rows = {r["name"]: r for r in merge(items, history)}
        slate = rows["AK-47 | Slate (Field-Tested)"]
        self.assertEqual(slate["min_price"], 2.6)
        self.assertEqual(slate["listings"], 1200)
        self.assertEqual(slate["median_30d"], 3.1)
        self.assertEqual(slate["volume_30d"], 49000)
        # An item with no sales history still gets a row, with empty stats.
        self.assertIsNone(rows["Sticker | Nobody (Holo)"]["median_30d"])


class ScanTest(unittest.TestCase):
    cfg = Config(sell_fee=0.08)

    def test_discounted_liquid_item_is_a_signal(self):
        sig = evaluate(row(price=8.0, m30=10.0), self.cfg)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.net_profit, 10.0 * 0.92 - 8.0)
        self.assertAlmostEqual(sig.discount, 0.2)

    def test_profit_must_survive_fee(self):
        # 10% below median, but the 8% fee leaves only ~2% profit.
        self.assertIsNone(evaluate(row(price=9.0, m30=10.0), self.cfg))

    def test_illiquid_item_is_skipped(self):
        self.assertIsNone(evaluate(row(vol30=10), self.cfg))
        self.assertIsNone(evaluate(row(listings=1), self.cfg))

    def test_falling_item_is_skipped(self):
        self.assertIsNone(evaluate(row(m7=8.5, m30=10.0), self.cfg))

    def test_price_range(self):
        self.assertIsNone(evaluate(row(price=1.0, m7=2.0, m30=2.0), self.cfg))
        self.assertIsNone(evaluate(row(price=400.0, m7=600.0, m30=600.0), self.cfg))

    def test_missing_data_is_skipped(self):
        self.assertIsNone(evaluate(row(m30=None), self.cfg))

    def test_watchlist_names_failed_filters(self):
        from cs2ts.scan import watchlist
        rows = [row("deal", price=8.0), row("close", price=9.2), row("falling", price=8.0, m7=8.5),
                row("empty", m30=None)]
        w = {x["name"]: x for x in watchlist(rows, self.cfg)}
        self.assertEqual(list(w), ["deal", "falling", "close"])
        self.assertEqual(w["deal"]["failed"], [])
        self.assertEqual(w["close"]["failed"], ["discount", "roi"])
        self.assertEqual(w["falling"]["failed"], ["falling"])

    def test_sorted_by_roi(self):
        rows = [row("a", price=8.0), row("b", price=6.0), row("c", price=9.5)]
        self.assertEqual([s.name for s in scan(rows, self.cfg)], ["b", "a"])


class PaperTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(db_path=":memory:", sell_fee=0.08, paper_budget=30.0,
                          paper_max_positions=2, paper_max_per_position=20.0)
        self.conn = connect(":memory:")
        self.addCleanup(self.conn.close)

    def day(self, d, rows):
        save_snapshot(self.conn, d, rows)
        return paper.step(self.conn, d, self.cfg)

    def test_buys_within_budget_and_slots(self):
        result = self.day("2026-11-01", [
            row("a", price=8.0), row("b", price=6.0),
            row("c", price=7.0), row("pricey", price=25.0, m7=40.0, m30=40.0),
        ])
        names = [n for n, _ in result["bought"]]
        self.assertEqual(names, ["b", "c"])
        self.assertAlmostEqual(paper.cash(self.conn, self.cfg), 30.0 - 13.0)

    def test_holds_through_trade_lock_then_sells_at_target(self):
        self.day("2026-11-01", [row("a", price=8.0)])
        # Price recovers on day 3, but the item is still trade locked.
        result = self.day("2026-11-03", [row("a", price=10.5)])
        self.assertEqual(result["sold"], [])
        result = self.day("2026-11-08", [row("a", price=10.5)])
        (name, reason, pnl), = result["sold"]
        self.assertEqual((name, reason), ("a", "target"))
        self.assertAlmostEqual(pnl, 10.5 * 0.99 * 0.92 - 8.0)

    def test_stop_loss(self):
        self.day("2026-11-01", [row("a", price=8.0)])
        result = self.day("2026-11-09", [row("a", price=6.0, m7=6.5, m30=10.0)])
        self.assertEqual(result["sold"][0][1], "stop_loss")

    def test_max_hold(self):
        self.day("2026-11-01", [row("a", price=8.0)])
        result = self.day("2026-12-20", [row("a", price=8.5, m7=9.0, m30=9.0)])
        self.assertEqual(result["sold"][0][1], "max_hold")

    def test_report(self):
        self.day("2026-11-01", [row("a", price=8.0), row("b", price=6.0)])
        self.day("2026-11-08", [row("a", price=10.5), row("b", price=6.0, m7=6.0, m30=6.2)])
        r = paper.report(self.conn, "2026-11-08", self.cfg)
        self.assertEqual(r["closed_trades"], 1)
        self.assertEqual(r["win_rate"], 1.0)
        self.assertEqual(len(r["open"]), 1)
        expected_realized = 10.5 * 0.99 * 0.92 - 8.0
        expected_unrealized = 6.0 * 0.99 * 0.92 - 6.0
        self.assertAlmostEqual(r["realized_pnl"], expected_realized)
        self.assertAlmostEqual(r["equity"], 30.0 + expected_realized + expected_unrealized)

    def test_equity_curve_tracks_realized_and_open_positions(self):
        self.day("2026-11-01", [row("a", price=8.0)])
        self.day("2026-11-08", [row("a", price=10.5)])
        curve = paper.equity_curve(self.conn, self.cfg)
        self.assertEqual([c["day"] for c in curve], ["2026-11-01", "2026-11-08"])
        # Day 1: holding "a", marked at today's exit price after fee.
        self.assertAlmostEqual(curve[0]["equity"], 30.0 + 8.0 * 0.99 * 0.92 - 8.0)
        self.assertEqual(curve[0]["open_positions"], 1)
        # Day 8: sold at target; the final equity matches the report.
        report = paper.report(self.conn, "2026-11-08", self.cfg)
        self.assertAlmostEqual(curve[-1]["equity"], report["equity"])
        self.assertAlmostEqual(curve[-1]["realized"], 10.5 * 0.99 * 0.92 - 8.0)


class DashboardTest(unittest.TestCase):
    def test_endpoints_over_http(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from cs2ts.dashboard import make_handler

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(db_path=str(Path(tmp) / "db.sqlite"))
            conn = connect(cfg.db_path)
            save_snapshot(conn, "2026-11-01", [row("a", price=8.0), row("b", price=9.9)])
            paper.step(conn, "2026-11-01", cfg)
            conn.close()

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            get = lambda path: urllib.request.urlopen(base + path).read()
            try:
                self.assertIn(b"CS2 Trading Dashboard", get("/"))
                summary = json.loads(get("/api/summary"))
                self.assertEqual(summary["day"], "2026-11-01")
                self.assertFalse(summary["demo"])
                self.assertEqual([p["name"] for p in summary["open"]], ["a"])
                self.assertEqual(len(json.loads(get("/api/equity"))), 1)
                self.assertEqual([s["name"] for s in json.loads(get("/api/signals"))], ["a"])
                watch = json.loads(get("/api/watchlist"))
                self.assertEqual([(w["name"], w["failed"]) for w in watch],
                                 [("a", []), ("b", ["discount", "roi"])])
                history = json.loads(get("/api/history?name=a"))
                self.assertEqual(history[0]["min_price"], 8.0)
            finally:
                server.shutdown()
                server.server_close()


class CsfloatTest(unittest.TestCase):
    def test_pick_universe_filters_price_and_sorts_by_listings(self):
        price_list = [
            {"market_hash_name": "cheap", "quantity": 9000, "min_price": 150},
            {"market_hash_name": "a", "quantity": 100, "min_price": 500},
            {"market_hash_name": "b", "quantity": 4000, "min_price": 350},
            {"market_hash_name": "thin", "quantity": 2, "min_price": 800},
            {"market_hash_name": "c", "quantity": 700, "min_price": 2500},
        ]
        picked = csfloat.pick_universe(price_list, 2.0, 300.0, 3, size=2)
        self.assertEqual([p["market_hash_name"] for p in picked], ["b", "c"])

    def test_summarize_graph(self):
        graph = json.loads((FIXTURES / "csfloat_graph.json").read_text())
        s = csfloat.summarize_graph(graph, date(2026, 10, 26))
        self.assertEqual(s["volume_24h"], 12)
        self.assertEqual(s["median_24h"], 3.52)
        self.assertEqual(s["volume_7d"], 12 + 40 + 38)
        self.assertEqual(s["median_7d"], 3.55)
        # The 2026-09-20 point is outside 30 days but inside 90.
        self.assertEqual(s["volume_30d"], 90)
        self.assertEqual(s["volume_90d"], 90 + 50)

    def test_fair_price_ignores_premium_copies(self):
        sales = json.loads((FIXTURES / "csfloat_sales.json").read_text())
        # The $38.10 and $2099 sales (stickers, rare pattern) are left out.
        self.assertEqual(csfloat.fair_price(sales), 24.10)
        self.assertIsNone(csfloat.fair_price(sales[:4]))

    def test_build_row_uses_fair_price_not_graph_average(self):
        graph = json.loads((FIXTURES / "csfloat_graph.json").read_text())
        sales = json.loads((FIXTURES / "csfloat_sales.json").read_text())
        item = {"market_hash_name": "AK-47 | Redline (Field-Tested)", "quantity": 900, "min_price": 2373}
        r = csfloat.build_row(item, graph, sales, date(2026, 10, 26))
        self.assertEqual(r["min_price"], 23.73)
        self.assertEqual(r["median_30d"], 24.10)
        # No earlier snapshots yet: no trend.
        self.assertEqual(r["median_7d"], 24.10)
        self.assertEqual(r["volume_30d"], 90)
        # Buying at 23.73 to sell around 24.10 is not a deal.
        self.assertIsNone(evaluate(r, Config()))

    def test_trend_comes_from_own_fair_price_history(self):
        graph = json.loads((FIXTURES / "csfloat_graph.json").read_text())
        sales = json.loads((FIXTURES / "csfloat_sales.json").read_text())
        item = {"market_hash_name": "x", "quantity": 900, "min_price": 2000}
        # Fair price slid from ~27.50 to 24.10 over the week: a falling item.
        r = csfloat.build_row(item, graph, sales, date(2026, 10, 26), [28.0, 27.5, 27.0])
        self.assertEqual(r["median_30d"], 24.10)
        momentum = r["median_7d"] / r["median_30d"] - 1
        self.assertAlmostEqual(momentum, 24.10 / 27.5 - 1, places=3)
        # 20% under the fair price, but falling >10%, so the scanner skips it.
        self.assertIsNone(evaluate(r, Config()))
        # The same listing on a steady item is a signal.
        steady = csfloat.build_row(item, graph, sales, date(2026, 10, 26), [24.0, 24.2])
        self.assertIsNotNone(evaluate(steady, Config()))

    def test_collect_stops_before_rate_limit(self):
        price_list = [{"market_hash_name": f"item {i}", "quantity": 100 - i, "min_price": 400}
                      for i in range(5)]
        graph = json.loads((FIXTURES / "csfloat_graph.json").read_text())
        sales = json.loads((FIXTURES / "csfloat_sales.json").read_text())
        remaining = iter([200, 100, 25, 10, 5])
        cfg = Config(csfloat_universe_size=5)
        with mock.patch("cs2ts.csfloat.fetch_price_list", return_value=price_list), \
             mock.patch("cs2ts.csfloat.fetch_graph", return_value=(graph, 300)), \
             mock.patch("cs2ts.csfloat.fetch_sales", side_effect=lambda n: (sales, next(remaining))):
            rows = csfloat.collect(cfg, date(2026, 10, 26), sleep=lambda s: None, log=lambda m: None)
        self.assertEqual([r["name"] for r in rows], ["item 0", "item 1", "item 2"])
        self.assertEqual(rows[0]["min_price"], 4.0)
        self.assertEqual(rows[0]["listings"], 100)
        self.assertEqual(rows[0]["volume_30d"], 90)


class SchedulerTest(unittest.TestCase):
    def test_next_run(self):
        from datetime import datetime
        from cs2ts.scheduler import next_run
        self.assertEqual(next_run(datetime(2026, 10, 27, 9, 0), "12:15"), datetime(2026, 10, 27, 12, 15))
        self.assertEqual(next_run(datetime(2026, 10, 27, 12, 15), "12:15"), datetime(2026, 10, 28, 12, 15))

    def test_run_once_skips_when_done_and_survives_errors(self):
        from cs2ts.scheduler import run_once
        calls, logs = [], []
        run_once(lambda: calls.append(1), lambda day: True, logs.append)
        self.assertEqual(calls, [])

        def boom():
            raise RuntimeError("api down")
        run_once(boom, lambda day: False, logs.append)
        self.assertIn("api down", logs[-1])


class CliTest(unittest.TestCase):
    def test_daily_with_mocked_api(self):
        items = json.loads((FIXTURES / "items.json").read_text())
        history = json.loads((FIXTURES / "sales_history.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"source": "skinport", "sell_fee": 0.08,
                                            "db_path": str(Path(tmp) / "db.sqlite")}))
            out = io.StringIO()
            with mock.patch("cs2ts.skinport.fetch_items", return_value=items), \
                 mock.patch("cs2ts.skinport.fetch_sales_history", return_value=history), \
                 contextlib.redirect_stdout(out):
                main(["--config", str(cfg_path), "daily", "--day", "2026-11-01"])
                main(["--config", str(cfg_path), "scan", "--csv", str(Path(tmp) / "s.csv")])
            text = out.getvalue()
            self.assertIn("Saved 4 items for 2026-11-01.", text)
            self.assertIn("BUY  M4A1-S | Black Lotus (Field-Tested)", text)
            self.assertIn("Paper account on 2026-11-01", text)
            self.assertTrue((Path(tmp) / "s.csv").exists())


class FakeResponse:
    def __init__(self, data, remaining):
        self.data, self.headers = data, {"x-quota-remaining": str(remaining)}

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeHttp:
    """Answers CSMarketAPI calls, counting down credits like the real API."""
    def __init__(self, credits=10_000):
        self.credits, self.calls = credits, []

    def get(self, url, params, timeout):
        params = dict(p for p in params if p[0] != "markets") | {
            "markets": [v for k, v in params if k == "markets"]}
        self.calls.append((url.rsplit("/v2/", 1)[1], params))
        name = params["market_hash_name"]
        if "sales" in url:
            self.credits -= 1
            data = {"market_hash_name": name, "sales": [
                {"market": "CSFLOAT", "mean_price": 99.0, "median_price": None, "volume": 50},
                {"market": "STEAMCOMMUNITY", "median_price": 12.0, "volume": 500},
                {"market": "SIH", "median_price": 11.5, "volume": 400},
                {"market": "SKINPORT", "median_price": 9.0, "volume": 10},
                {"market": "MARKETCSGO", "median_price": 9.5, "volume": 30},
            ]}
        else:
            self.credits -= len(params["markets"])
            data = {"market_hash_name": name, "listings": [
                {"market": "BUFF163", "ask_price": 7.1}, {"market": "YOUPIN", "ask_price": 7.3}]}
        return FakeResponse(data, self.credits)


class CsMarketTest(unittest.TestCase):
    def test_reference_median_weights_by_volume_and_skips_mean_only_markets(self):
        sales = FakeHttp().get("x/v2/sales/latest/aggregate", [("market_hash_name", "a")], 1).json()["sales"]
        self.assertEqual(csmarket.reference_median(sales), (9.5, 40))
        self.assertEqual(csmarket.reference_median([]), (None, 0))

    def test_enrich_checks_sales_for_all_and_asks_for_closest(self):
        http = FakeHttp()
        rows = [row("far", price=9.9), row("near", price=7.0), row("mid", price=8.5)]
        csmarket.enrich(csmarket.Client("k", http), rows, watch_size=2, log=lambda *a: None)
        by = {r["name"]: r for r in rows}
        self.assertEqual(by["far"]["ref_median"], 9.5)
        self.assertEqual(by["near"]["buff_ask"], 7.1)
        self.assertEqual(by["mid"]["youpin_ask"], 7.3)
        self.assertNotIn("buff_ask", by["far"])
        asked = [p["market_hash_name"] for path, p in http.calls if path.startswith("listings")]
        self.assertEqual(asked, ["near", "mid"])
        self.assertEqual([p["markets"] for path, p in http.calls if path.startswith("listings")][0],
                         ["BUFF163", "YOUPIN"])
        self.assertEqual(http.credits, 10_000 - 3 - 4)

    def test_enrich_stops_at_credit_reserve(self):
        http = FakeHttp(credits=csmarket.CREDIT_RESERVE + 2)
        rows = [row(f"i{n}") for n in range(5)]
        logs = []
        csmarket.enrich(csmarket.Client("k", http), rows, watch_size=5, log=logs.append)
        self.assertEqual(len(http.calls), 2)
        self.assertIn("stopping early", logs[0])

    def test_load_key_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {}, clear=True):
            env = Path(tmp) / ".env"
            self.assertIsNone(csmarket.load_key(env))
            env.write_text("OTHER=1\nCSMARKETAPI_KEY='abc'\n")
            self.assertEqual(csmarket.load_key(env), "abc")

    def test_target_far_above_other_markets_fails_ref_gap(self):
        cfg = Config()
        _, failed = check(row(price=8.0) | {"ref_median": 8.5}, cfg)
        self.assertIn("ref_gap", failed)
        sig, failed = check(row(price=8.0) | {"ref_median": 9.5}, cfg)
        self.assertEqual(failed, [])
        self.assertEqual(sig.ref_median, 9.5)

    def test_old_database_gets_new_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE snapshots (day TEXT, name TEXT, min_price REAL, listings INTEGER,"
                        " median_24h REAL, volume_24h INTEGER, median_7d REAL, volume_7d INTEGER,"
                        " median_30d REAL, volume_30d INTEGER, median_90d REAL, volume_90d INTEGER,"
                        " PRIMARY KEY (day, name))")
            old.commit()
            old.close()
            conn = connect(path)
            save_snapshot(conn, "2026-10-27", [row("a")])
            update_snapshot(conn, "2026-10-27", "a", ref_median=9.0, buff_ask=7.0)
            conn.commit()
            r = snapshot(conn, "2026-10-27")["a"]
            self.assertEqual((r["ref_median"], r["buff_ask"], r["youpin_ask"]), (9.0, 7.0, None))


class ConfigTest(unittest.TestCase):
    def test_example_config_loads(self):
        cfg = load_config(Path(__file__).parent.parent / "config.example.json")
        self.assertEqual(cfg.source, "csfloat")
        self.assertEqual(cfg.sell_fee, 0.045)

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text('{"sel_fee": 0.1}')
            with self.assertRaises(ValueError):
                load_config(p)


if __name__ == "__main__":
    unittest.main()
