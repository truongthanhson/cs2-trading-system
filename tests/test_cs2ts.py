import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cs2ts import paper
from cs2ts.__main__ import main
from cs2ts.collect import merge
from cs2ts.config import Config, load_config
from cs2ts.db import connect, save_snapshot
from cs2ts.scan import evaluate, scan

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
    cfg = Config()

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

    def test_sorted_by_roi(self):
        rows = [row("a", price=8.0), row("b", price=6.0), row("c", price=9.5)]
        self.assertEqual([s.name for s in scan(rows, self.cfg)], ["b", "a"])


class PaperTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(db_path=":memory:", paper_budget=30.0,
                          paper_max_positions=2, paper_max_per_position=20.0)
        self.conn = connect(":memory:")

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


class CliTest(unittest.TestCase):
    def test_daily_with_mocked_api(self):
        items = json.loads((FIXTURES / "items.json").read_text())
        history = json.loads((FIXTURES / "sales_history.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps({"db_path": str(Path(tmp) / "db.sqlite")}))
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


class ConfigTest(unittest.TestCase):
    def test_example_config_loads(self):
        cfg = load_config(Path(__file__).parent.parent / "config.example.json")
        self.assertEqual(cfg.sell_fee, 0.08)

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            p.write_text('{"sel_fee": 0.1}')
            with self.assertRaises(ValueError):
                load_config(p)


if __name__ == "__main__":
    unittest.main()
