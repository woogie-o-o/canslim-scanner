# -*- coding: utf-8 -*-
"""Watchlist 영속화 — SQLite 기반 별표 종목 관리."""
from __future__ import annotations
import sqlite3
import datetime as dt
from typing import List, Optional, Dict, Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    last_score  INTEGER,
    last_phase  TEXT,
    note        TEXT,
    updated_at  TEXT
);
"""


class WatchlistDB:
    def __init__(self, path: str = "watchlist.db"):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def add(self, ticker: str, note: str = "") -> bool:
        ticker = ticker.upper().strip()
        now = dt.datetime.now().isoformat(timespec="seconds")
        try:
            self._conn.execute(
                "INSERT INTO watchlist(ticker, added_at, note, updated_at) VALUES (?,?,?,?)",
                (ticker, now, note, now)
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # 이미 존재

    def remove(self, ticker: str) -> bool:
        ticker = ticker.upper().strip()
        cur = self._conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
        self._conn.commit()
        return cur.rowcount > 0

    def list(self) -> List[str]:
        cur = self._conn.execute("SELECT ticker FROM watchlist ORDER BY added_at DESC")
        return [r[0] for r in cur.fetchall()]

    def get(self, ticker: str) -> Optional[Dict[str, Any]]:
        ticker = ticker.upper().strip()
        cur = self._conn.execute(
            "SELECT ticker,added_at,last_score,last_phase,note,updated_at "
            "FROM watchlist WHERE ticker=?", (ticker,))
        r = cur.fetchone()
        if not r: return None
        return {"ticker": r[0], "added_at": r[1], "last_score": r[2],
                "last_phase": r[3], "note": r[4], "updated_at": r[5]}

    def update_metrics(self, ticker: str, score: int | None = None,
                        phase: str | None = None) -> bool:
        ticker = ticker.upper().strip()
        now = dt.datetime.now().isoformat(timespec="seconds")
        cur = self._conn.execute(
            "UPDATE watchlist SET last_score=COALESCE(?,last_score), "
            "last_phase=COALESCE(?,last_phase), updated_at=? WHERE ticker=?",
            (score, phase, now, ticker)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def all_with_metrics(self) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT ticker,added_at,last_score,last_phase,note,updated_at "
            "FROM watchlist ORDER BY last_score DESC NULLS LAST, added_at DESC")
        return [{"ticker": r[0], "added_at": r[1], "last_score": r[2],
                 "last_phase": r[3], "note": r[4], "updated_at": r[5]}
                for r in cur.fetchall()]

    def close(self):
        self._conn.close()


if __name__ == "__main__":
    db = WatchlistDB(":memory:")
    assert db.add("AAPL")
    assert not db.add("AAPL")  # duplicate
    assert db.add("NVDA")
    assert "AAPL" in db.list() and "NVDA" in db.list()
    assert db.update_metrics("AAPL", score=80, phase="강한 상승")
    assert db.get("AAPL")["last_score"] == 80
    assert db.get("AAPL")["last_phase"] == "강한 상승"
    assert db.remove("AAPL")
    assert "AAPL" not in db.list()
    print("[OK] watchlist:", db.list())
