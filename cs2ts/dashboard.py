"""Local web dashboard: python -m cs2ts dashboard, then open http://localhost:8000

Serves one HTML page plus a few JSON endpoints read straight from the SQLite
database. It only reads; nothing on the page changes data.
"""

import json
import sqlite3
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import paper
from .config import Config
from .db import connect, latest_day, snapshot
from .demo import DEMO_DB
from .scan import scan


def api_summary(conn: sqlite3.Connection, cfg: Config, query: dict) -> dict:
    day = latest_day(conn)
    r = paper.report(conn, day, cfg)
    return {"day": day, "budget": cfg.paper_budget, "sell_fee": cfg.sell_fee,
            "trade_lock_days": cfg.trade_lock_days, "demo": cfg.db_path == DEMO_DB, **r}


def api_equity(conn, cfg, query):
    return paper.equity_curve(conn, cfg)


def api_signals(conn, cfg, query):
    day = latest_day(conn)
    if day is None:
        return []
    limit = int(query.get("limit", ["100"])[0])
    return [asdict(s) for s in scan(snapshot(conn, day).values(), cfg)[:limit]]


def api_history(conn, cfg, query):
    name = query.get("name", [""])[0]
    rows = conn.execute(
        "SELECT day, min_price, median_7d, median_30d, volume_30d FROM snapshots"
        " WHERE name = ? ORDER BY day", (name,))
    return [dict(r) for r in rows]


ROUTES = {
    "/api/summary": api_summary,
    "/api/equity": api_equity,
    "/api/signals": api_signals,
    "/api/history": api_history,
}


def make_handler(cfg: Config):
    page = resources.files("cs2ts").joinpath("static/dashboard.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            route = ROUTES.get(url.path)
            if route is None:
                return self._send(404, b"not found", "text/plain")
            # A fresh connection per request: sqlite connections are not thread safe.
            conn = connect(cfg.db_path)
            try:
                data = route(conn, cfg, parse_qs(url.query))
            finally:
                conn.close()
            self._send(200, json.dumps(data).encode(), "application/json")

        def log_message(self, *args):
            pass

    return Handler


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(cfg))
    print(f"Dashboard: http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
