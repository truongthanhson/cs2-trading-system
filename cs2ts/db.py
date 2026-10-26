"""SQLite storage: daily price snapshots and paper-trading positions."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    day          TEXT NOT NULL,
    name         TEXT NOT NULL,
    min_price    REAL,
    listings     INTEGER,
    median_24h   REAL,
    volume_24h   INTEGER,
    median_7d    REAL,
    volume_7d    INTEGER,
    median_30d   REAL,
    volume_30d   INTEGER,
    median_90d   REAL,
    volume_90d   INTEGER,
    PRIMARY KEY (day, name)
);

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    buy_day      TEXT NOT NULL,
    buy_price    REAL NOT NULL,
    target_price REAL NOT NULL,
    sell_day     TEXT,
    sell_price   REAL,
    net_proceeds REAL,
    exit_reason  TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(conn: sqlite3.Connection, day: str, rows: list[dict]) -> int:
    conn.executemany(
        """
        INSERT OR REPLACE INTO snapshots VALUES (
            :day, :name, :min_price, :listings,
            :median_24h, :volume_24h, :median_7d, :volume_7d,
            :median_30d, :volume_30d, :median_90d, :volume_90d
        )
        """,
        [{**r, "day": day} for r in rows],
    )
    conn.commit()
    return len(rows)


def latest_day(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(day) FROM snapshots").fetchone()
    return row[0]


def snapshot(conn: sqlite3.Connection, day: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM snapshots WHERE day = ?", (day,))
    return {r["name"]: r for r in rows}
