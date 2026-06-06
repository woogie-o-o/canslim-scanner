from __future__ import annotations

import sqlite3
import time
from typing import Any


class WatchlistDB:
    """Tiny SQLite-backed watchlist store used by the web and Tk frontends."""

    def __init__(self, path: str = "watchlist.db") -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker TEXT PRIMARY KEY,
                note TEXT NOT NULL DEFAULT '',
                last_score REAL,
                last_phase TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        cols = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(watchlist)").fetchall()
        }
        migrations = {
            "note": "TEXT NOT NULL DEFAULT ''",
            "last_score": "REAL",
            "last_phase": "TEXT",
            "created_at": "REAL",
            "updated_at": "REAL",
        }
        for col, spec in migrations.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {spec}")
        now = time.time()
        self.conn.execute("UPDATE watchlist SET created_at = COALESCE(created_at, ?)", (now,))
        self.conn.execute("UPDATE watchlist SET updated_at = COALESCE(updated_at, ?)", (now,))
        self.conn.commit()

    def add(self, ticker: str, note: str = "") -> bool:
        ticker = self._ticker(ticker)
        if not ticker:
            return False
        now = time.time()
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO watchlist
                (ticker, note, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (ticker, note or "", now, now),
        )
        if cur.rowcount:
            self.conn.commit()
            return True
        return False

    def remove(self, ticker: str) -> bool:
        ticker = self._ticker(ticker)
        if not ticker:
            return False
        cur = self.conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        if cur.rowcount:
            self.conn.commit()
            return True
        return False

    def list(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT ticker FROM watchlist ORDER BY created_at ASC, ticker ASC"
        ).fetchall()
        return [str(r["ticker"]) for r in rows]

    def get(self, ticker: str) -> dict[str, Any] | None:
        ticker = self._ticker(ticker)
        if not ticker:
            return None
        row = self.conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        return dict(row) if row else None

    def update_metrics(self, ticker: str, score: float | None = None, phase: str | None = None) -> bool:
        ticker = self._ticker(ticker)
        if not ticker:
            return False
        if self.get(ticker) is None:
            self.add(ticker)
        self.conn.execute(
            """
            UPDATE watchlist
               SET last_score = COALESCE(?, last_score),
                   last_phase = COALESCE(?, last_phase),
                   updated_at = ?
             WHERE ticker = ?
            """,
            (score, phase, time.time(), ticker),
        )
        self.conn.commit()
        return True

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _ticker(ticker: str) -> str:
        return str(ticker or "").strip().upper()
