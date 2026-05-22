"""
진입 타점 필터 백테스트 (최적화 버전)
=====================================
ATR S3.0 T5.0 / 15일 보유 전략에 진입 점수 필터를 적용했을 때
승률·PF·샤프가 얼마나 개선되는지 측정한다.

핵심 최적화: RSI/BB/MA/MACD를 종목별 1회만 계산 → 바별 인덱싱
"""
import os
import glob
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional

DATA_DIR = r"C:\Users\new123\Downloads\scalping_final\data\daily"

# ── 전략 파라미터 (이전 백테스트 최적값) ──
STOP_MULT = 3.0
TARGET_MULT = 5.0
MAX_HOLD = 15
MIN_RR = 1.5


@dataclass
class Trade:
    ticker: str
    entry_idx: int
    entry_px: float
    stop_px: float
    target_px: float
    outcome: str
    pnl_pct: float
    days_held: int
    entry_score: float


# ══════════════════════════════════════════
#  벡터화된 지표 계산 (종목당 1회)
# ══════════════════════════════════════════

def calc_atr(h, l, c, period=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    alpha = 1.0 / period
    atr = np.zeros_like(tr)
    atr[0] = tr[0]
    for i in range(1, len(tr)):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]
    return atr


def calc_rsi(close, period=14):
    """RSI를 벡터로 한 번에 계산."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    alpha = 1.0 / period
    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(close)):
        avg_gain[i] = alpha * gain[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i] + (1 - alpha) * avg_loss[i - 1]
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[:period] = 50.0
    return rsi


def calc_bb(close, period=20, num_std=2.0):
    """볼린저 밴드 %B를 벡터로 계산."""
    sma = np.convolve(close, np.ones(period) / period, mode='full')[:len(close)]
    sma[:period - 1] = close[:period - 1]
    # rolling std
    std = np.zeros_like(close)
    for i in range(period - 1, len(close)):
        std[i] = np.std(close[i - period + 1:i + 1])
    upper = sma + num_std * std
    lower = sma - num_std * std
    width = upper - lower
    pct_b = np.where(width > 0, (close - lower) / width, 0.5)
    pct_b[:period - 1] = 0.5
    return pct_b


def calc_sma(close, period):
    """SMA 벡터."""
    sma = np.convolve(close, np.ones(period) / period, mode='full')[:len(close)]
    sma[:period - 1] = np.nan
    return sma


def calc_ema(close, period):
    """EMA 벡터."""
    alpha = 2.0 / (period + 1)
    ema = np.zeros_like(close)
    ema[0] = close[0]
    for i in range(1, len(close)):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
    return ema


def calc_macd(close, fast=12, slow=26, signal=9):
    """MACD 히스토그램 벡터."""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    sig_line = calc_ema(macd_line, signal)
    hist = macd_line - sig_line
    return hist


def calc_volume_ratio(volume, period=20):
    """거래량 / 20일 평균 비율."""
    avg_vol = np.convolve(volume, np.ones(period) / period, mode='full')[:len(volume)]
    avg_vol[:period - 1] = volume[:period - 1]
    ratio = np.where(avg_vol > 0, volume / avg_vol, 1.0)
    return ratio


# ══════════════════════════════════════════
#  종목별 지표 사전 계산
# ══════════════════════════════════════════

def precompute_indicators(df) -> Dict[str, np.ndarray]:
    """종목 하나의 모든 지표를 한 번에 계산."""
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)

    return {
        "close": c,
        "high": h,
        "low": l,
        "volume": v,
        "atr": calc_atr(h, l, c),
        "rsi": calc_rsi(c),
        "bb_pct": calc_bb(c),
        "sma5": calc_sma(c, 5),
        "sma20": calc_sma(c, 20),
        "sma60": calc_sma(c, 60),
        "sma120": calc_sma(c, 120),
        "macd_hist": calc_macd(c),
        "vol_ratio": calc_volume_ratio(v),
    }


# ══════════════════════════════════════════
#  진입 점수 계산 (V4_HYBRID 재현)
# ══════════════════════════════════════════

def entry_score_at(ind: dict, i: int) -> float:
    """
    사전 계산된 지표 배열에서 i번째 바의 진입 점수를 O(1)로 계산.
    0~100 스케일. 높을수록 좋은 진입.
    """
    score = 0.0
    c = ind["close"][i]

    # 1) RSI 점수 (0~25) — 30~50 구간이 최적 (과매도 반등 구간)
    rsi = ind["rsi"][i]
    if 30 <= rsi <= 45:
        score += 25.0
    elif 25 <= rsi < 30:
        score += 20.0  # 과매도 — 반등 가능성 높지만 추가 하락 위험
    elif 45 < rsi <= 55:
        score += 15.0
    elif 55 < rsi <= 65:
        score += 8.0
    # 65 이상 또는 25 미만: 0점

    # 2) 볼린저 밴드 위치 (0~20) — 하단 근처가 좋음
    bb = ind["bb_pct"][i]
    if bb <= 0.2:
        score += 20.0  # 하단 밴드 근처 (강한 매수 시그널)
    elif bb <= 0.35:
        score += 15.0
    elif bb <= 0.5:
        score += 10.0
    elif bb <= 0.65:
        score += 5.0
    # 0.65 이상: 0점

    # 3) MA 정배열 (0~20)
    sma5 = ind["sma5"][i]
    sma20 = ind["sma20"][i]
    sma60 = ind["sma60"][i]
    sma120 = ind["sma120"][i]
    if not (np.isnan(sma60) or np.isnan(sma120)):
        # 단기 > 중기 > 장기 정배열
        if sma5 > sma20 > sma60:
            score += 15.0
            if sma60 > sma120:
                score += 5.0  # 완전 정배열 보너스
        elif sma5 > sma20:
            score += 8.0  # 부분 정배열
        # 역배열이면 0점

    # 4) MACD 히스토그램 (0~15) — 음→양 전환 or 양 증가
    hist = ind["macd_hist"]
    if i >= 1:
        cur_h = hist[i]
        prev_h = hist[i - 1]
        if prev_h < 0 and cur_h >= 0:
            score += 15.0  # 음→양 전환 (강한 매수)
        elif cur_h > 0 and cur_h > prev_h:
            score += 10.0  # 양 증가
        elif cur_h > 0:
            score += 5.0   # 양이지만 감소 중
        elif cur_h < 0 and cur_h > prev_h:
            score += 3.0   # 음이지만 감소폭 줄어듦

    # 5) 거래량 점프 (0~10) — 평균 대비 급증
    vr = ind["vol_ratio"][i]
    if vr >= 2.5:
        score += 10.0
    elif vr >= 1.8:
        score += 7.0
    elif vr >= 1.3:
        score += 4.0

    # 6) 가격이 20일선 위 (0~10) — 지지 확인
    if not np.isnan(sma20) and c > sma20:
        score += 7.0
        if not np.isnan(sma60) and c > sma60:
            score += 3.0  # 60일선도 위

    return min(score, 100.0)


# ══════════════════════════════════════════
#  시뮬레이션
# ══════════════════════════════════════════

def simulate_trade(highs, lows, closes, entry_idx, entry_px, stop_px, t1_px, max_hold):
    n = len(closes)
    for d in range(1, min(max_hold + 1, n - entry_idx)):
        j = entry_idx + d
        if lows[j] <= stop_px:
            pnl = (stop_px - entry_px) / entry_px
            return 'loss', pnl, d
        if highs[j] >= t1_px:
            pnl = (t1_px - entry_px) / entry_px
            return 'win', pnl, d
    last_px = closes[min(entry_idx + max_hold, n - 1)]
    pnl = (last_px - entry_px) / entry_px
    outcome = 'timeout_win' if pnl > 0 else 'timeout_loss'
    return outcome, pnl, max_hold


# ══════════════════════════════════════════
#  메트릭 계산
# ══════════════════════════════════════════

def calc_metrics(trades: List[Trade]) -> dict:
    if not trades:
        return {}
    total = len(trades)
    wins = [t for t in trades if t.outcome == 'win']
    losses = [t for t in trades if t.outcome == 'loss']
    timeouts = [t for t in trades if t.outcome.startswith('timeout')]

    pnls = np.array([t.pnl_pct for t in trades])
    win_pnls = np.array([t.pnl_pct for t in wins]) if wins else np.array([0.0])
    loss_pnls = np.array([t.pnl_pct for t in losses]) if losses else np.array([0.0])

    win_rate = len(wins) / total * 100
    avg_pnl = np.mean(pnls) * 100
    avg_win = np.mean(win_pnls) * 100
    avg_loss = np.mean(loss_pnls) * 100

    # Profit Factor
    gross_profit = np.sum(win_pnls) if len(wins) else 0
    gross_loss = abs(np.sum(loss_pnls)) if len(losses) else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    # Sharpe (일별 기준, 연환산)
    # 2% 리스크 고정 가정 → 실제 PnL per trade
    risk_per_trade = 0.02
    trade_returns = pnls * risk_per_trade / np.where(np.abs(pnls) > 0, np.abs(pnls), 1)
    # 단순히 pnl 분포의 sharpe
    if np.std(pnls) > 0:
        sharpe = np.mean(pnls) / np.std(pnls) * np.sqrt(252 / np.mean([t.days_held for t in trades]))
    else:
        sharpe = 0

    # MDD (시간순 누적)
    sorted_trades = sorted(trades, key=lambda t: t.entry_idx)
    equity = [1.0]
    for t in sorted_trades:
        eq = equity[-1] * (1 + t.pnl_pct * risk_per_trade)
        equity.append(eq)
    equity = np.array(equity)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    mdd = np.min(dd) * 100

    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "pf": round(pf, 2),
        "sharpe": round(sharpe, 2),
        "mdd": round(mdd, 2),
        "avg_days": round(np.mean([t.days_held for t in trades]), 1),
    }


# ══════════════════════════════════════════
#  메인 백테스트
# ══════════════════════════════════════════

def run():
    t0 = time.time()
    print("데이터 로딩 중...")
    files = glob.glob(os.path.join(DATA_DIR, "*_1d.csv"))
    all_data = {}
    for f in files:
        ticker = os.path.basename(f).replace("_1d.csv", "")
        try:
            df = pd.read_csv(f, parse_dates=["date"])
            if len(df) < 120:
                continue
            df = df.sort_values("date").reset_index(drop=True)
            all_data[ticker] = df
        except Exception:
            continue
    print(f"{len(all_data)}종목 로드 ({time.time()-t0:.1f}s)")

    # 전 종목 지표 사전 계산
    print("지표 사전 계산 중...")
    t1 = time.time()
    indicators = {}
    for ticker, df in all_data.items():
        indicators[ticker] = precompute_indicators(df)
    print(f"지표 계산 완료 ({time.time()-t1:.1f}s)")

    # 전 종목 트레이드 생성 (점수 포함)
    print("트레이드 시뮬레이션 중...")
    t2 = time.time()
    all_trades: List[Trade] = []

    for ticker, df in all_data.items():
        ind = indicators[ticker]
        n = len(ind["close"])
        c = ind["close"]
        h = ind["high"]
        l = ind["low"]
        atr = ind["atr"]

        start_idx = max(120, n - 250)  # 120일 워밍업 필요 (SMA120)
        for i in range(start_idx, n - MAX_HOLD - 1, 3):  # 3일 간격
            cur = c[i]
            a = atr[i]
            if cur <= 0 or a <= 0:
                continue

            # 손절/목표
            sp = cur - STOP_MULT * a
            tp = cur + TARGET_MULT * a

            # R:R 체크
            risk = cur - sp
            reward = tp - cur
            if risk <= 0 or reward / risk < MIN_RR:
                continue

            # 진입 점수 (O(1) — 인덱싱만)
            score = entry_score_at(ind, i)

            # 시뮬레이션
            outcome, pnl, days = simulate_trade(h, l, c, i, cur, sp, tp, MAX_HOLD)
            all_trades.append(Trade(
                ticker=ticker, entry_idx=i, entry_px=cur,
                stop_px=sp, target_px=tp,
                outcome=outcome, pnl_pct=pnl, days_held=days,
                entry_score=score,
            ))

    print(f"총 {len(all_trades)}건 트레이드 ({time.time()-t2:.1f}s)")

    # 점수 구간별 성과 비교
    thresholds = [
        ("ALL (필터없음)", 0),
        ("Score >= 40", 40),
        ("Score >= 50", 50),
        ("Score >= 60", 60),
        ("Score >= 65", 65),
        ("Score >= 70", 70),
        ("Score >= 75 (GREEN)", 75),
        ("Score >= 80", 80),
    ]

    print("\n" + "=" * 130)
    print(f"{'필터':<22} {'거래수':>6} {'승':>5} {'패':>5} {'TO':>5} {'승률':>7} {'평균PnL':>9} {'평균익':>9} {'평균손':>9} {'PF':>7} {'Sharpe':>7} {'MDD':>8} {'평균일':>5}")
    print("=" * 130)

    results = []
    for label, min_score in thresholds:
        filtered = [t for t in all_trades if t.entry_score >= min_score]
        if len(filtered) < 30:
            print(f"{label:<22} {'거래 부족':>6}")
            continue
        m = calc_metrics(filtered)
        results.append({"filter": label, "min_score": min_score, **m})
        print(
            f"{label:<22} {m['trades']:>6} {m['wins']:>5} {m['losses']:>5} {m['timeouts']:>5} "
            f"{m['win_rate']:>6.1f}% {m['avg_pnl']:>+8.2f}% {m['avg_win']:>+8.2f}% {m['avg_loss']:>+8.2f}% "
            f"{m['pf']:>6.2f} {m['sharpe']:>6.2f} {m['mdd']:>+7.2f}% {m['avg_days']:>5.1f}"
        )

    # 점수 분포 히스토그램
    scores = np.array([t.entry_score for t in all_trades])
    print(f"\n진입 점수 분포:")
    for lo in range(0, 90, 10):
        hi = lo + 10
        cnt = np.sum((scores >= lo) & (scores < hi))
        bar = "#" * (cnt // max(len(all_trades) // 80, 1))
        print(f"  {lo:>2}~{hi:<2}: {cnt:>5}건 {bar}")
    cnt90 = np.sum(scores >= 90)
    print(f"  90+  : {cnt90:>5}건")

    # 승률 vs 점수 세분화 (5점 단위)
    print(f"\n점수 구간별 승률 (5점 단위):")
    print(f"  {'구간':<10} {'거래수':>6} {'승률':>7} {'평균PnL':>9} {'PF':>7}")
    for lo in range(0, 95, 5):
        hi = lo + 5
        bucket = [t for t in all_trades if lo <= t.entry_score < hi]
        if len(bucket) < 20:
            continue
        bm = calc_metrics(bucket)
        print(f"  {lo:>2}~{hi:<2}점  {bm['trades']:>6} {bm['win_rate']:>6.1f}% {bm['avg_pnl']:>+8.2f}% {bm['pf']:>6.2f}")

    # CSV 저장
    if results:
        out = pd.DataFrame(results)
        out_path = os.path.join(os.path.dirname(DATA_DIR), "entry_filter_backtest.csv")
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n결과 저장: {out_path}")

    # 최적 필터 추천
    if results:
        # PF * Sharpe가 가장 높으면서 거래수 50 이상인 것
        viable = [r for r in results if r["trades"] >= 50 and r["pf"] > 0]
        if viable:
            best = max(viable, key=lambda r: r["pf"] * max(r["sharpe"], 0.1))
            print(f"\n*** 추천 필터: {best['filter']}")
            print(f"    승률 {best['win_rate']}% | PF {best['pf']:.2f} | Sharpe {best['sharpe']:.2f} | MDD {best['mdd']:+.2f}%")
            print(f"    거래수 {best['trades']}건 | 평균PnL {best['avg_pnl']:+.2f}%")

    total_time = time.time() - t0
    print(f"\n총 소요시간: {total_time:.1f}s")


if __name__ == "__main__":
    run()
