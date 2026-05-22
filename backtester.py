from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf


VALID_ENTRIES = {"20MA_breakout", "rsi_reversal"}


def _sanitize_history(hist: pd.DataFrame) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close", "Volume"]
    if hist is None or hist.empty:
        raise ValueError("No price history returned from yfinance.")

    missing = [col for col in required if col not in hist.columns]
    if missing:
        raise ValueError(f"Price history missing required columns: {missing}")

    clean = hist.loc[:, required].copy()
    clean = clean.replace([np.inf, -np.inf], np.nan)
    clean = clean.dropna(subset=required)
    clean = clean[(clean["Close"] > 0) & (clean["High"] > 0) & (clean["Low"] > 0)]

    if clean.empty:
        raise ValueError("Price history is empty after cleaning NaN/Inf values.")

    return clean


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain != 0, 0.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi.fillna(50.0)


def _max_drawdown(equity: pd.Series) -> float:
    running_peak = equity.cummax()
    drawdown = equity / running_peak - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def _sharpe_ratio(equity: pd.Series) -> float:
    daily_returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if daily_returns.empty:
        return 0.0
    std = float(daily_returns.std(ddof=0))
    if std == 0.0:
        return 0.0
    return float(np.sqrt(252.0) * daily_returns.mean() / std)


def _build_summary(
    ticker: str,
    entry: str,
    trades: int,
    win_rate: float,
    equity_final: float,
    max_dd: float,
) -> str:
    strategy_name = "20MA breakout" if entry == "20MA_breakout" else "RSI reversal"
    if trades == 0:
        return (
            f"{ticker} {strategy_name} backtest found no completed trades. "
            f"Final equity stayed at {equity_final:.2f} with max drawdown {max_dd:.1%}."
        )
    return (
        f"{ticker} {strategy_name} completed {trades} trades with {win_rate:.1%} win rate. "
        f"Final equity was {equity_final:.2f} and max drawdown was {max_dd:.1%}."
    )


def backtest(
    ticker: str,
    *,
    period: str = "5y",
    entry: str = "20MA_breakout",
    stop_pct: float = 0.05,
    take_pct: float = 0.15,
) -> dict:
    """
    return {
        "ticker": str, "period": str, "trades": int,
        "win_rate": float,
        "avg_return": float,
        "payoff_ratio": float,
        "sharpe": float,
        "max_dd": float,
        "equity_final": float,
        "summary_text": str,
    }
    """
    if not ticker or not str(ticker).strip():
        raise ValueError("Ticker must be a non-empty string.")
    if entry not in VALID_ENTRIES:
        raise ValueError(f"Unsupported entry strategy: {entry}")
    if stop_pct <= 0 or take_pct <= 0:
        raise ValueError("stop_pct and take_pct must be positive.")

    hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    data = _sanitize_history(hist)

    data["ma20"] = data["Close"].rolling(20).mean()
    data["vol20"] = data["Volume"].rolling(20).mean()
    data["rsi14"] = _rsi(data["Close"], 14)

    realized_equity = 1.0
    equity_curve: list[float] = []
    trade_returns: list[float] = []

    in_position = False
    entry_price = 0.0
    stop_price = 0.0
    take_price = 0.0
    oversold_armed = False

    for i, (_, row) in enumerate(data.iterrows()):
        close = float(row["Close"])
        high = float(row["High"])
        low = float(row["Low"])
        current_equity = realized_equity

        if in_position:
            exit_price = None

            # Conservative tie-break: if both levels hit on the same bar, assume stop first.
            if low <= stop_price:
                exit_price = stop_price
            elif high >= take_price:
                exit_price = take_price

            if exit_price is not None:
                trade_return = exit_price / entry_price - 1.0
                realized_equity *= 1.0 + trade_return
                trade_returns.append(float(trade_return))
                in_position = False
                current_equity = realized_equity
            else:
                current_equity = realized_equity * (close / entry_price)

        if entry == "rsi_reversal" and float(row["rsi14"]) < 30.0:
            oversold_armed = True

        if not in_position and i > 0:
            prev = data.iloc[i - 1]

            if entry == "20MA_breakout":
                signal = (
                    pd.notna(prev["ma20"])
                    and pd.notna(row["ma20"])
                    and pd.notna(row["vol20"])
                    and float(prev["Close"]) <= float(prev["ma20"])
                    and close > float(row["ma20"])
                    and float(row["Volume"]) > float(row["vol20"]) * 1.3
                )
            else:
                prev_rsi = float(prev["rsi14"])
                curr_rsi = float(row["rsi14"])
                signal = oversold_armed and prev_rsi <= 35.0 and curr_rsi > 35.0

            if signal:
                entry_price = close
                stop_price = entry_price * (1.0 - stop_pct)
                take_price = entry_price * (1.0 + take_pct)
                in_position = True
                current_equity = realized_equity
                if entry == "rsi_reversal":
                    oversold_armed = False

        equity_curve.append(float(current_equity))

    if in_position:
        final_close = float(data["Close"].iloc[-1])
        final_return = final_close / entry_price - 1.0
        realized_equity *= 1.0 + final_return
        trade_returns.append(float(final_return))
        equity_curve[-1] = float(realized_equity)

    trades = len(trade_returns)
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r < 0]

    win_rate = float(len(wins) / trades) if trades else 0.0
    avg_return = float(np.mean(trade_returns)) if trades else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff_ratio = float(avg_win / abs(avg_loss)) if losses and avg_loss != 0 else float("inf") if wins else 0.0

    equity_series = pd.Series(equity_curve, index=data.index, dtype=float)
    equity_series = equity_series.replace([np.inf, -np.inf], np.nan).ffill().fillna(1.0)

    max_dd = _max_drawdown(equity_series)
    result = {
        "ticker": str(ticker),
        "period": str(period),
        "trades": int(trades),
        "win_rate": float(win_rate),
        "avg_return": float(avg_return),
        "payoff_ratio": float(payoff_ratio),
        "sharpe": float(_sharpe_ratio(equity_series)),
        "max_dd": float(max_dd),
        "equity_final": float(realized_equity),
        "equity_curve": [float(v) for v in equity_series.tolist()],
        "equity_dates": [d.strftime("%Y-%m-%d") for d in equity_series.index],
        "summary_text": _build_summary(
            ticker=str(ticker),
            entry=entry,
            trades=trades,
            win_rate=win_rate,
            equity_final=realized_equity,
            max_dd=max_dd,
        ),
    }
    return result


if __name__ == "__main__":
    r = backtest("AAPL")
    assert 0 <= r["win_rate"] <= 1
    assert r["trades"] >= 0
    assert "max_dd" in r
    print(r)
    print(r["summary_text"])
