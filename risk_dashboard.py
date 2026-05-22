from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


MIN_OBSERVATIONS = 60
TRADING_DAYS = 252


def fetch_returns(tickers: list[str], *, period: str = "1y") -> pd.DataFrame:
    """Fetch daily close-to-close returns and drop rows with missing values."""
    clean_tickers = _normalize_tickers(tickers)
    if not clean_tickers:
        return pd.DataFrame()

    try:
        data = yf.download(
            clean_tickers,
            period=period,
            auto_adjust=True,
            progress=False,
            group_by="column",
            threads=False,
        )
    except Exception:
        return pd.DataFrame(columns=clean_tickers, dtype=float)

    closes = _extract_close_frame(data, clean_tickers)
    if closes.empty:
        return pd.DataFrame(columns=clean_tickers, dtype=float)

    returns = closes.pct_change()
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    return returns.astype(float)


def portfolio_var(
    weights: dict[str, float], *, alpha: float = 0.95, period: str = "1y"
) -> dict[str, Any]:
    """
    Calculate historical VaR/CVaR and simple risk statistics for a weighted portfolio.
    """
    tickers, weight_values = _normalize_weights(weights)
    result = {
        "tickers": tickers,
        "weights": weight_values,
        "daily_var": None,
        "daily_cvar": None,
        "annual_vol": None,
        "sharpe": None,
        "max_dd": None,
        "summary_text": "",
    }
    if not tickers:
        result["summary_text"] = "No valid portfolio weights were provided."
        return result

    returns = fetch_returns(tickers, period=period)
    portfolio_returns = _portfolio_returns(returns, tickers, weight_values)
    if portfolio_returns is None or len(portfolio_returns) < MIN_OBSERVATIONS:
        result["summary_text"] = (
            f"Insufficient clean return history for {', '.join(tickers)} "
            f"(need at least {MIN_OBSERVATIONS} daily observations)."
        )
        return result

    values = portfolio_returns.to_numpy(dtype=float)
    tail_q = float(np.quantile(values, 1.0 - alpha))
    tail_losses = values[values <= tail_q]

    daily_var = max(0.0, -tail_q)
    daily_cvar = max(0.0, -float(tail_losses.mean())) if tail_losses.size else daily_var
    annual_vol = float(np.std(values, ddof=1) * math.sqrt(TRADING_DAYS))

    mean_daily = float(np.mean(values))
    std_daily = float(np.std(values, ddof=1))
    sharpe = None
    if std_daily > 0 and math.isfinite(std_daily):
        sharpe = float((mean_daily / std_daily) * math.sqrt(TRADING_DAYS))

    equity_curve = (1.0 + portfolio_returns).cumprod()
    running_peak = equity_curve.cummax()
    drawdowns = (equity_curve / running_peak) - 1.0
    max_dd = float(drawdowns.min())

    if not _all_finite([daily_var, daily_cvar, annual_vol, max_dd]) or (
        sharpe is not None and not math.isfinite(sharpe)
    ):
        result["summary_text"] = "Risk metrics could not be computed from the downloaded data."
        return result

    sharpe_text = f"{sharpe:.2f}" if sharpe is not None else "n/a"

    result.update(
        {
            "daily_var": daily_var,
            "daily_cvar": daily_cvar,
            "annual_vol": annual_vol,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "summary_text": (
                f"{', '.join(tickers)} 1Y historical daily VaR is {daily_var:.2%} and "
                f"CVaR is {daily_cvar:.2%}.\n"
                f"Annualized volatility is {annual_vol:.2%}, Sharpe is "
                f"{sharpe_text}, and max drawdown is {max_dd:.2%}."
            ),
        }
    )
    if sharpe is None:
        result["summary_text"] = (
            f"{', '.join(tickers)} 1Y historical daily VaR is {daily_var:.2%} and "
            f"CVaR is {daily_cvar:.2%}.\n"
            f"Annualized volatility is {annual_vol:.2%} and max drawdown is {max_dd:.2%}."
        )
    return result


def correlation_matrix(tickers: list[str], *, period: str = "1y") -> pd.DataFrame:
    """Return a NaN-safe correlation matrix with 1.0 on the diagonal."""
    clean_tickers = _normalize_tickers(tickers)
    if not clean_tickers:
        return pd.DataFrame()

    returns = fetch_returns(clean_tickers, period=period)
    if returns.empty or len(returns) < 2:
        corr = pd.DataFrame(
            np.eye(len(clean_tickers), dtype=float),
            index=clean_tickers,
            columns=clean_tickers,
        )
        return corr

    corr = returns.corr()
    corr = corr.reindex(index=clean_tickers, columns=clean_tickers)
    corr = corr.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    for ticker in clean_tickers:
        corr.loc[ticker, ticker] = 1.0
    return corr.astype(float)


def from_portfolio_tracker(pt) -> dict[str, float]:
    """Convert PortfolioTracker.positions() into normalized portfolio weights."""
    rows = pt.positions()
    if not isinstance(rows, list):
        return {}

    values: dict[str, float] = {}
    total_value = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker", "")).strip().upper()
        qty = _safe_float(row.get("qty"))
        market_price = _safe_float(row.get("market_price"))
        if not ticker or qty is None or market_price is None:
            continue
        market_value = qty * market_price
        if market_value <= 0:
            continue
        values[ticker] = values.get(ticker, 0.0) + market_value
        total_value += market_value

    if total_value <= 0:
        return {}
    return {ticker: value / total_value for ticker, value in values.items()}


def _normalize_tickers(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for ticker in tickers:
        if ticker is None:
            continue
        value = str(ticker).strip().upper()
        if not value or value in seen:
            continue
        clean.append(value)
        seen.add(value)
    return clean


def _normalize_weights(weights: dict[str, float]) -> tuple[list[str], list[float]]:
    valid: list[tuple[str, float]] = []
    for ticker, weight in weights.items():
        name = str(ticker).strip().upper()
        value = _safe_float(weight)
        if not name or value is None or value <= 0:
            continue
        valid.append((name, value))

    total = sum(weight for _, weight in valid)
    if total <= 0:
        return [], []

    tickers = [ticker for ticker, _ in valid]
    normalized = [weight / total for _, weight in valid]
    return tickers, normalized


def _extract_close_frame(data: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=tickers, dtype=float)

    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            closes = data["Close"]
        elif "Adj Close" in data.columns.get_level_values(0):
            closes = data["Adj Close"]
        else:
            return pd.DataFrame(columns=tickers, dtype=float)
    else:
        if "Close" in data.columns:
            closes = data[["Close"]].rename(columns={"Close": tickers[0]})
        elif "Adj Close" in data.columns:
            closes = data[["Adj Close"]].rename(columns={"Adj Close": tickers[0]})
        else:
            closes = data.copy()
            if len(tickers) == 1 and closes.shape[1] == 1:
                closes.columns = tickers
            else:
                return pd.DataFrame(columns=tickers, dtype=float)

    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=tickers[0])
    closes = closes.reindex(columns=tickers)
    closes = closes.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    return closes.astype(float)


def _portfolio_returns(
    returns: pd.DataFrame, tickers: list[str], weight_values: list[float]
) -> pd.Series | None:
    if returns.empty:
        return None
    frame = returns.reindex(columns=tickers)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if frame.empty:
        return None
    weighted = frame.to_numpy(dtype=float) @ np.array(weight_values, dtype=float)
    series = pd.Series(weighted, index=frame.index, name="portfolio_return")
    series = series.replace([np.inf, -np.inf], np.nan).dropna()
    return series if not series.empty else None


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _all_finite(values: list[float]) -> bool:
    return all(math.isfinite(value) for value in values)


if __name__ == "__main__":
    corr = correlation_matrix(["AAPL", "MSFT", "NVDA"])
    for ticker in ["AAPL", "MSFT", "NVDA"]:
        if ticker in corr.index and ticker in corr.columns:
            assert abs(float(corr.loc[ticker, ticker]) - 1.0) < 1e-6

    risk = portfolio_var({"AAPL": 0.5, "MSFT": 0.5})
    assert "daily_var" in risk
    assert risk["daily_var"] is None or isinstance(risk["daily_var"], float)
    assert isinstance(risk["summary_text"], str) and risk["summary_text"].strip()

    print("RISK_DASHBOARD OK")
