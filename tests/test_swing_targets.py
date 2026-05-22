"""
스윙 손절/목표가 백테스트
=========================
330종목 일봉 데이터로 다양한 손절/목표 전략을 시뮬레이션하여
승률·수익률·프로핏팩터가 가장 높은 조합을 찾는다.

전략 후보:
  손절: ATR 배수(1.0~3.0), 스윙저점-버퍼, MA지지-버퍼
  목표: ATR 배수(2.0~6.0), 스윙고점, 피보나치확장, 저항선
  보유기간: 5~20일
"""
import os
import glob
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Tuple

DATA_DIR = r"C:\Users\new123\Downloads\scalping_final\data\daily"


@dataclass
class TradeResult:
    entry: float
    stop: float
    t1: float
    outcome: str   # 'win', 'loss', 'timeout'
    pnl_pct: float
    days_held: int


def load_all_daily() -> dict:
    """일봉 CSV 전부 로드."""
    files = glob.glob(os.path.join(DATA_DIR, "*_1d.csv"))
    data = {}
    for f in files:
        ticker = os.path.basename(f).replace("_1d.csv", "")
        try:
            df = pd.read_csv(f, parse_dates=["date"])
            if len(df) < 100:
                continue
            df = df.sort_values("date").reset_index(drop=True)
            data[ticker] = df
        except Exception:
            continue
    return data


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR 계산."""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def find_swing_lows(lows: np.ndarray, order: int = 5) -> List[Tuple[int, float]]:
    """로컬 스윙 저점 탐색."""
    pts = []
    for i in range(order, len(lows) - order):
        if all(lows[i] <= lows[i - j] for j in range(1, order + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, order + 1)):
            pts.append((i, float(lows[i])))
    return pts


def find_swing_highs(highs: np.ndarray, order: int = 5) -> List[Tuple[int, float]]:
    """로컬 스윙 고점 탐색."""
    pts = []
    for i in range(order, len(highs) - order):
        if all(highs[i] >= highs[i - j] for j in range(1, order + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, order + 1)):
            pts.append((i, float(highs[i])))
    return pts


# ── 손절 전략 ──

def stop_atr(cur, atr, mult):
    return cur - mult * atr

def stop_swing_low(cur, atr, lows_arr, idx, lookback=20, buffer_mult=0.3):
    """최근 lookback일 스윙 저점 - ATR 버퍼."""
    start = max(0, idx - lookback)
    pts = find_swing_lows(lows_arr[start:idx+1], order=3)
    if pts:
        swing = min(p[1] for p in pts)
        stop = swing - buffer_mult * atr
        dist = (cur - stop) / cur
        if 0.02 <= dist <= 0.12:
            return stop
    return None  # 폴백 필요

def stop_ma_support(cur, atr, close_arr, idx, period=20, buffer_mult=0.5):
    """이동평균 지지선 - ATR 버퍼."""
    if idx < period:
        return None
    ma = float(np.mean(close_arr[idx-period:idx]))
    if ma < cur:
        stop = ma - buffer_mult * atr
        dist = (cur - stop) / cur
        if 0.02 <= dist <= 0.12:
            return stop
    return None


# ── 목표 전략 ──

def target_atr(cur, atr, mult):
    return cur + mult * atr

def target_swing_high(cur, atr, highs_arr, idx, lookback=20):
    """최근 lookback일 스윙 고점."""
    start = max(0, idx - lookback)
    pts = find_swing_highs(highs_arr[start:idx+1], order=3)
    if pts:
        swing = max(p[1] for p in pts)
        if swing > cur * 1.02:
            return swing
    return None

def target_fib_ext(cur, lows_arr, highs_arr, idx, lookback=30, level=1.618):
    """피보나치 확장."""
    start = max(0, idx - lookback)
    sl = float(np.min(lows_arr[start:idx+1]))
    sh = float(np.max(highs_arr[start:idx+1]))
    rng = sh - sl
    if rng > 0:
        t = sh + rng * (level - 1.0)
        if t > cur * 1.02:
            return t
    return None


# ── 시뮬레이션 ──

def simulate_trade(highs, lows, closes, entry_idx, entry_px, stop_px, t1_px, max_hold):
    """진입 후 max_hold일 내에 T1 도달 vs 손절 도달."""
    n = len(closes)
    for d in range(1, min(max_hold + 1, n - entry_idx)):
        j = entry_idx + d
        # 손절 먼저 체크 (보수적)
        if lows[j] <= stop_px:
            pnl = (stop_px - entry_px) / entry_px
            return TradeResult(entry_px, stop_px, t1_px, 'loss', pnl, d)
        if highs[j] >= t1_px:
            pnl = (t1_px - entry_px) / entry_px
            return TradeResult(entry_px, stop_px, t1_px, 'win', pnl, d)
    # 타임아웃: 마지막 종가로 청산
    last_px = closes[min(entry_idx + max_hold, n - 1)]
    pnl = (last_px - entry_px) / entry_px
    outcome = 'win' if pnl > 0 else 'loss'
    return TradeResult(entry_px, stop_px, t1_px, f'timeout_{outcome}', pnl, max_hold)


def run_backtest():
    print("데이터 로딩 중...")
    all_data = load_all_daily()
    print(f"{len(all_data)}종목 로드 완료")

    # 테스트할 전략 조합
    strategies = []

    # 1) ATR 기반 (기존 방식 변형)
    for s_mult in [1.5, 2.0, 2.5, 3.0]:
        for t_mult in [2.0, 3.0, 4.0, 5.0, 6.0]:
            if t_mult / s_mult < 1.5:
                continue  # R:R 너무 낮음
            strategies.append({
                "name": f"ATR_S{s_mult}_T{t_mult}",
                "stop_type": "atr", "stop_param": s_mult,
                "target_type": "atr", "target_param": t_mult,
            })

    # 2) 스윙저점 손절 + ATR 목표
    for t_mult in [3.0, 4.0, 5.0]:
        strategies.append({
            "name": f"SwingLow_T{t_mult}",
            "stop_type": "swing_low", "stop_param": 0.3,
            "target_type": "atr", "target_param": t_mult,
        })

    # 3) 스윙저점 손절 + 스윙고점 목표
    strategies.append({
        "name": "SwingLow_SwingHigh",
        "stop_type": "swing_low", "stop_param": 0.3,
        "target_type": "swing_high", "target_param": 0,
    })

    # 4) 스윙저점 손절 + 피보나치 1.618
    strategies.append({
        "name": "SwingLow_Fib1618",
        "stop_type": "swing_low", "stop_param": 0.3,
        "target_type": "fib", "target_param": 1.618,
    })

    # 5) MA20 지지 손절 + ATR 목표
    for t_mult in [3.0, 4.0, 5.0]:
        strategies.append({
            "name": f"MA20_T{t_mult}",
            "stop_type": "ma20", "stop_param": 0.5,
            "target_type": "atr", "target_param": t_mult,
        })

    # 6) MA20 지지 손절 + 스윙고점 목표
    strategies.append({
        "name": "MA20_SwingHigh",
        "stop_type": "ma20", "stop_param": 0.5,
        "target_type": "swing_high", "target_param": 0,
    })

    holding_days = [5, 10, 15, 20]

    results = []

    for strat in strategies:
        for max_hold in holding_days:
            trades: List[TradeResult] = []

            for ticker, df in all_data.items():
                n = len(df)
                if n < 100:
                    continue

                closes = df["close"].values
                highs = df["high"].values
                lows = df["low"].values
                atr_s = calc_atr(df).values

                # 최근 250일 구간에서 5일 간격으로 진입 시뮬
                start_idx = max(60, n - 250)
                for i in range(start_idx, n - max_hold - 1, 5):
                    cur = closes[i]
                    atr = atr_s[i]
                    if cur <= 0 or atr <= 0:
                        continue

                    # 손절가
                    if strat["stop_type"] == "atr":
                        sp = stop_atr(cur, atr, strat["stop_param"])
                    elif strat["stop_type"] == "swing_low":
                        sp = stop_swing_low(cur, atr, lows, i, buffer_mult=strat["stop_param"])
                        if sp is None:
                            sp = stop_atr(cur, atr, 2.0)  # 폴백
                    elif strat["stop_type"] == "ma20":
                        sp = stop_ma_support(cur, atr, closes, i, period=20, buffer_mult=strat["stop_param"])
                        if sp is None:
                            sp = stop_atr(cur, atr, 2.0)
                    else:
                        continue

                    # 목표가
                    if strat["target_type"] == "atr":
                        tp = target_atr(cur, atr, strat["target_param"])
                    elif strat["target_type"] == "swing_high":
                        tp = target_swing_high(cur, atr, highs, i)
                        if tp is None:
                            tp = target_atr(cur, atr, 3.0)
                    elif strat["target_type"] == "fib":
                        tp = target_fib_ext(cur, lows, highs, i, level=strat["target_param"])
                        if tp is None:
                            tp = target_atr(cur, atr, 3.0)
                    else:
                        continue

                    # R:R 체크
                    risk = cur - sp
                    reward = tp - cur
                    if risk <= 0 or reward / risk < 1.5:
                        continue

                    trade = simulate_trade(highs, lows, closes, i, cur, sp, tp, max_hold)
                    trades.append(trade)

            if len(trades) < 100:
                continue

            wins = sum(1 for t in trades if t.outcome == 'win')
            losses = sum(1 for t in trades if t.outcome == 'loss')
            timeouts = sum(1 for t in trades if t.outcome.startswith('timeout'))
            total = len(trades)
            win_rate = wins / total * 100
            avg_pnl = np.mean([t.pnl_pct for t in trades]) * 100
            avg_win = np.mean([t.pnl_pct for t in trades if t.outcome == 'win']) * 100 if wins else 0
            avg_loss = np.mean([t.pnl_pct for t in trades if t.outcome == 'loss']) * 100 if losses else 0
            profit_factor = abs(avg_win * wins / (avg_loss * losses)) if losses and avg_loss else 0
            avg_days = np.mean([t.days_held for t in trades])

            results.append({
                "strategy": strat["name"],
                "hold": max_hold,
                "trades": total,
                "wins": wins,
                "losses": losses,
                "timeouts": timeouts,
                "win_rate": round(win_rate, 1),
                "avg_pnl": round(avg_pnl, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2),
                "avg_days": round(avg_days, 1),
            })

    # 결과 정렬 (profit_factor 기준)
    results.sort(key=lambda x: -x["profit_factor"])

    print("\n" + "=" * 100)
    print(f"{'전략':<25} {'보유일':>4} {'거래수':>6} {'승률':>6} {'평균PnL':>8} {'평균익':>8} {'평균손':>8} {'PF':>6} {'평균일':>5}")
    print("=" * 100)
    for r in results[:30]:
        print(f"{r['strategy']:<25} {r['hold']:>4}d {r['trades']:>6} {r['win_rate']:>5.1f}% {r['avg_pnl']:>+7.2f}% {r['avg_win']:>+7.2f}% {r['avg_loss']:>+7.2f}% {r['profit_factor']:>5.2f} {r['avg_days']:>5.1f}")

    # CSV 저장
    out = pd.DataFrame(results)
    out_path = os.path.join(os.path.dirname(DATA_DIR), "swing_target_backtest.csv")
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 저장: {out_path}")

    # 최고 전략 출력
    if results:
        best = results[0]
        print(f"\n*** 최고 전략: {best['strategy']} (보유 {best['hold']}일)")
        print(f"    승률 {best['win_rate']}% | 평균PnL {best['avg_pnl']:+.2f}% | PF {best['profit_factor']:.2f}")


if __name__ == "__main__":
    run_backtest()
