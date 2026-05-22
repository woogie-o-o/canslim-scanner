from __future__ import annotations

import math
import sqlite3
from typing import Any

import yfinance as yf


CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    side   TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
    qty    REAL NOT NULL,
    price  REAL NOT NULL,
    fee    REAL DEFAULT 0,
    ts     TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class PortfolioTracker:
    def __init__(self, db_path: str = "portfolio.sqlite3") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute(CREATE_TRADES_SQL)
        self.conn.commit()

    def add_trade(
        self,
        ticker: str,
        side: str,
        qty: float,
        price: float,
        fee: float = 0,
    ) -> int:
        ticker = ticker.strip().upper()
        side = side.strip().upper()
        qty = float(qty)
        price = float(price)
        fee = float(fee)

        if not ticker:
            raise ValueError("ticker is required")
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if qty <= 0:
            raise ValueError("qty must be positive")
        if price < 0:
            raise ValueError("price must be non-negative")
        if fee < 0:
            raise ValueError("fee must be non-negative")

        if side == "SELL":
            current_qty = self._current_qty(ticker)
            if qty > current_qty + 1e-12:
                raise ValueError(f"cannot sell {qty} shares of {ticker}; held qty is {current_qty}")

        cursor = self.conn.execute(
            """
            INSERT INTO trades (ticker, side, qty, price, fee)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticker, side, qty, price, fee),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def positions(self) -> list[dict[str, Any]]:
        state = self._build_position_state()
        tickers = [ticker for ticker, item in state.items() if item["qty"] > 1e-12]
        prices = self._fetch_market_prices(tickers)

        rows: list[dict[str, Any]] = []
        for ticker in sorted(tickers):
            item = state[ticker]
            qty = float(item["qty"])
            avg_cost = float(item["avg_cost"])
            realized_pnl = float(item["realized_pnl"])
            market_price = self._sanitize_market_price(prices.get(ticker))
            market_value = qty * market_price
            cost_basis = qty * avg_cost
            unrealized_pnl = market_value - cost_basis
            total_cost = cost_basis
            total_pnl_pct = ((realized_pnl + unrealized_pnl) / total_cost * 100.0) if total_cost else 0.0
            rows.append(
                {
                    "ticker": ticker,
                    "qty": qty,
                    "avg_cost": avg_cost,
                    "realized_pnl": realized_pnl,
                    "market_price": market_price,
                    "unrealized_pnl": unrealized_pnl,
                    "total_pnl_pct": total_pnl_pct,
                }
            )
        return rows

    def summary(self) -> dict[str, float]:
        state = self._build_position_state()
        tickers = [ticker for ticker, item in state.items() if item["qty"] > 1e-12]
        prices = self._fetch_market_prices(tickers)

        total_cost = 0.0
        total_market_value = 0.0
        total_realized = 0.0
        for ticker, item in state.items():
            qty = float(item["qty"])
            avg_cost = float(item["avg_cost"])
            total_realized += float(item["realized_pnl"])
            if qty <= 1e-12:
                continue
            total_cost += qty * avg_cost
            total_market_value += qty * self._sanitize_market_price(prices.get(ticker))

        total_unrealized = total_market_value - total_cost
        denominator = total_cost
        return_pct = ((total_realized + total_unrealized) / denominator * 100.0) if denominator else 0.0
        return {
            "total_cost": total_cost,
            "total_market_value": total_market_value,
            "total_unrealized": total_unrealized,
            "total_realized": total_realized,
            "return_pct": return_pct,
        }

    def history(self, ticker: str | None = None) -> list[dict[str, Any]]:
        if ticker is None:
            cursor = self.conn.execute(
                "SELECT id, ticker, side, qty, price, fee, ts FROM trades ORDER BY id ASC"
            )
        else:
            cursor = self.conn.execute(
                "SELECT id, ticker, side, qty, price, fee, ts FROM trades WHERE ticker = ? ORDER BY id ASC",
                (ticker.strip().upper(),),
            )
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self.conn.close()

    def _current_qty(self, ticker: str) -> float:
        state = self._build_position_state()
        return float(state.get(ticker, {}).get("qty", 0.0))

    def _build_position_state(self) -> dict[str, dict[str, float]]:
        state: dict[str, dict[str, float]] = {}
        for trade in self.history():
            ticker = str(trade["ticker"]).upper()
            side = str(trade["side"]).upper()
            qty = float(trade["qty"])
            price = float(trade["price"])
            fee = float(trade["fee"] or 0.0)

            item = state.setdefault(
                ticker,
                {"qty": 0.0, "avg_cost": 0.0, "realized_pnl": 0.0},
            )

            if side == "BUY":
                prev_qty = item["qty"]
                new_qty = prev_qty + qty
                total_cost = (prev_qty * item["avg_cost"]) + (qty * price) + fee
                item["qty"] = new_qty
                item["avg_cost"] = total_cost / new_qty if new_qty else 0.0
                continue

            if qty > item["qty"] + 1e-12:
                raise ValueError(f"trade history oversold {ticker}: attempted {qty}, held {item['qty']}")
            item["realized_pnl"] += (price - item["avg_cost"]) * qty - fee
            item["qty"] -= qty
            if abs(item["qty"]) <= 1e-12:
                item["qty"] = 0.0
                item["avg_cost"] = 0.0

        return state

    def _fetch_market_prices(self, tickers: list[str]) -> dict[str, float | None]:
        if not tickers:
            return {}

        prices: dict[str, float | None] = {ticker: None for ticker in tickers}
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).fast_info
                price = None
                if hasattr(info, "get"):
                    price = info.get("lastPrice")
                    if price is None:
                        price = info.get("regularMarketPrice")
                prices[ticker] = self._sanitize_market_price(price, allow_none=True)
            except Exception:
                prices[ticker] = None
        return prices

    @staticmethod
    def _sanitize_market_price(value: Any, allow_none: bool = False) -> float | None:
        if value is None:
            return None if allow_none else 0.0
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None if allow_none else 0.0
        if math.isnan(price) or math.isinf(price):
            return None if allow_none else 0.0
        return price


def _assert_not_nan(value: Any) -> None:
    if isinstance(value, float):
        assert not math.isnan(value), f"unexpected NaN: {value}"


if __name__ == "__main__":
    tracker = PortfolioTracker(":memory:")
    tracker._fetch_market_prices = lambda tickers: {ticker: 125.0 for ticker in tickers}  # type: ignore[method-assign]

    tracker.add_trade("AAPL", "BUY", 10, 100)
    tracker.add_trade("AAPL", "BUY", 5, 110)
    state = tracker._build_position_state()
    assert round(state["AAPL"]["avg_cost"], 6) == round((10 * 100 + 5 * 110) / 15, 6)

    tracker.add_trade("AAPL", "SELL", 8, 120)
    state = tracker._build_position_state()
    expected_avg = (10 * 100 + 5 * 110) / 15
    expected_realized = (120 - expected_avg) * 8
    assert abs(state["AAPL"]["avg_cost"] - expected_avg) < 1e-9
    assert abs(state["AAPL"]["realized_pnl"] - expected_realized) < 1e-9
    assert abs(state["AAPL"]["qty"] - 7.0) < 1e-9

    positions = tracker.positions()
    summary = tracker.summary()
    assert len(positions) == 1
    position = positions[0]
    for value in position.values():
        _assert_not_nan(value)
    for value in summary.values():
        _assert_not_nan(value)

    assert position["ticker"] == "AAPL"
    assert abs(position["avg_cost"] - expected_avg) < 1e-9
    assert abs(position["realized_pnl"] - expected_realized) < 1e-9
    assert position["market_price"] == 125.0
    assert abs(position["unrealized_pnl"] - ((125.0 - expected_avg) * 7.0)) < 1e-9

    assert abs(summary["total_cost"] - (expected_avg * 7.0)) < 1e-9
    assert abs(summary["total_market_value"] - (125.0 * 7.0)) < 1e-9
    assert abs(summary["total_unrealized"] - ((125.0 - expected_avg) * 7.0)) < 1e-9
    assert abs(summary["total_realized"] - expected_realized) < 1e-9

    try:
        tracker.add_trade("AAPL", "SELL", 100, 120)
    except ValueError:
        pass
    else:
        raise AssertionError("oversell should raise ValueError")

    tracker.close()
    print("PORTFOLIO_TRACKER OK")
