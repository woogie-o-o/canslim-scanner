import sqlite3


class WatchlistDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS watchlist ("
            "ticker TEXT PRIMARY KEY,"
            "note TEXT DEFAULT '',"
            "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        self._conn.commit()

    def list(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT ticker FROM watchlist ORDER BY added_at"
        ).fetchall()
        return [row[0] for row in rows]

    def add(self, ticker: str, note: str = "") -> bool:
        try:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO watchlist (ticker, note) VALUES (?, ?)",
                (ticker, note),
            )
            self._conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error:
            return False

    def remove(self, ticker: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM watchlist WHERE ticker = ?", (ticker,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
