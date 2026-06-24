#!/usr/bin/env python3
"""
종목 진입 타이밍 판단기
사용법:  python stock_judge.py 000660.KS
         python stock_judge.py NVDA
         python stock_judge.py 005930.KS --name 삼성전자
"""
from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import argparse
import numpy as np
import yfinance as yf

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SEP  = "═" * 54
LINE = "─" * 54

try:
    from regime_classifier import get_market_regime, R_BULL, R_BEAR, R_CHOP
    _HAS_REGIME = True
except Exception:
    _HAS_REGIME = False


# ── 시나리오 정의 ─────────────────────────────────────────────────────
# 각 시나리오: label, desc(진단 이유), timing, premark, color, risk
SCENARIOS = {
    # 위험 시나리오
    "CLIMAX": {
        "label": "클라이맥스 매도",
        "timing": "진입 금지 — 단기 반전 대기",
        "premark": False, "color": "🔴", "risk": "EXTREME",
    },
    "EXHAUSTION": {
        "label": "추세 소진",
        "timing": "관망 — 거래량 회복 확인 후 재진입 검토",
        "premark": False, "color": "🔴", "risk": "HIGH",
    },
    "DOWNTREND": {
        "label": "하락 추세",
        "timing": "관망 — 반등 시 매도 구간",
        "premark": False, "color": "🔴", "risk": "HIGH",
    },
    "PULLBACK_RISKY": {
        "label": "위험한 눌림",
        "timing": "관망 — 지지 여부 확인 후 재판단",
        "premark": False, "color": "🔴", "risk": "HIGH",
    },
    # 중립/주의 시나리오
    "OVERBOUGHT": {
        "label": "과매수 과열",
        "timing": "본장 눌림목 대기 — MA20 회귀 후 재진입",
        "premark": False, "color": "🟡", "risk": "HIGH",
    },
    "BASE": {
        "label": "베이스 구축 중",
        "timing": "거래량 폭발 돌파 신호 대기",
        "premark": False, "color": "🟡", "risk": "LOW",
    },
    "RECOVERY": {
        "label": "반등 초기",
        "timing": "소량 분할 진입 — 추세 전환 확인 전까지 신중",
        "premark": False, "color": "🟡", "risk": "MED",
    },
    "NEUTRAL": {
        "label": "중립",
        "timing": "방향 확인 후 진입",
        "premark": False, "color": "🟡", "risk": "MED",
    },
    # 기회 시나리오
    "BREAKOUT": {
        "label": "돌파 진행 중",
        "timing": "돌파 직후 소량 진입 가능 — 손절 명확히",
        "premark": True, "color": "🟢", "risk": "MED",
    },
    "TREND_STRONG": {
        "label": "강한 추세 중",
        "timing": "본장 눌림목 공략 — MA20 지지 확인 후 진입",
        "premark": False, "color": "🟢", "risk": "MED",
    },
    "PULLBACK_HEALTHY": {
        "label": "건전한 눌림",
        "timing": "매수 준비 — 거래량 감소 끝나는 지점 진입",
        "premark": True, "color": "🟢", "risk": "LOW",
    },
}


# ── 기술적 지표 계산 ──────────────────────────────────────────────────
def compute_technicals(ticker: str) -> dict:
    t  = yf.Ticker(ticker)
    df = t.history(period="6mo", interval="1d")
    if df.empty or len(df) < 20:
        raise ValueError(f"데이터 부족: {ticker}")

    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)
    vol   = df["Volume"].astype(float)
    ret   = close.pct_change()

    # MA
    ma5  = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # ATR (14일)
    tr_  = np.maximum(high - low,
           np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr  = tr_.rolling(14).mean()

    # 실현변동성 (20일 연율화)
    rvol = ret.rolling(20).std() * (252 ** 0.5)

    # 60일 고점 대비 낙폭
    dd60 = close / close.rolling(60).max() - 1

    # 거래량 지표
    vol_20avg = vol.rolling(20).mean()
    vol_5avg  = vol.iloc[-5:].mean()
    vol_ratio = float(vol.iloc[-1] / vol_20avg.iloc[-1])   # 오늘 vs 20일 평균
    vol_trend = float(vol_5avg / vol_20avg.iloc[-1] - 1)   # 5일 평균 vs 20일 평균

    last  = float(close.iloc[-1])
    prev  = float(close.iloc[-2])

    def _chg(n):
        return (last / float(close.iloc[-n]) - 1) if len(close) >= n else np.nan

    # RSI(14) 근사 — Wilder 방식 대신 단순 up/down 비율
    ret14 = ret.iloc[-14:]
    ups   = float(ret14[ret14 > 0].mean() or 0)
    downs = float(abs(ret14[ret14 < 0].mean()) or 1e-9)
    rsi   = 100 - 100 / (1 + ups / downs)

    # 연속 양봉/음봉 스트릭
    streak_up = streak_dn = 0
    for r in ret.iloc[-1:-8:-1]:
        if np.isfinite(r) and r > 0: streak_up += 1
        else: break
    for r in ret.iloc[-1:-8:-1]:
        if np.isfinite(r) and r < 0: streak_dn += 1
        else: break

    ma20v = float(ma20.iloc[-1])
    ma60v = float(ma60.iloc[-1]) if len(close) >= 60 and np.isfinite(ma60.iloc[-1]) else np.nan
    ma5v  = float(ma5.iloc[-1])

    return {
        "close":     last,
        "chg1d":     last / prev - 1,
        "chg5d":     _chg(6),
        "chg20d":    _chg(21),
        "ma5":       ma5v,
        "ma20":      ma20v,
        "ma60":      ma60v,
        "ma20_dev":  last / ma20v - 1,
        "ma60_dev":  last / ma60v - 1 if np.isfinite(ma60v) else np.nan,
        "atr":       float(atr.iloc[-1]),
        "rvol":      float(rvol.iloc[-1]),
        "dd60":      float(dd60.iloc[-1]),
        "vol_ratio": vol_ratio,
        "vol_trend": vol_trend,
        "rsi":       rsi,
        "streak_up": streak_up,
        "streak_dn": streak_dn,
        # raw series (출력용)
        "tail":      df.tail(5).copy(),
        "ret_ser":   ret,
        "close_ser": close,
        "vol_ser":   vol,
    }


# ── 시나리오 분류 ─────────────────────────────────────────────────────
def classify(tc: dict) -> tuple[str, list[str]]:
    """
    (시나리오 키, 근거 문장 리스트) 반환
    순서가 중요 — 위에 있는 조건이 먼저 매칭됨
    """
    d60       = tc["ma60_dev"]
    d20       = tc["ma20_dev"]
    chg5      = tc["chg5d"]
    dd60      = tc["dd60"]
    rvol      = tc["rvol"]
    vr        = tc["vol_ratio"]
    vt        = tc["vol_trend"]   # 5일 거래량 추세
    rsi       = tc["rsi"]
    streak_up = tc["streak_up"]
    streak_dn = tc["streak_dn"]
    ma5, ma20v, ma60v = tc["ma5"], tc["ma20"], tc["ma60"]

    c5 = chg5 if np.isfinite(chg5) else 0.0

    # MA 배열
    if np.isfinite(ma60v):
        bull_align = ma5 > ma20v > ma60v
        bear_align = ma5 < ma20v < ma60v
    else:
        bull_align = ma5 > ma20v
        bear_align = ma5 < ma20v

    at_high = dd60 > -0.03
    price_up = c5 > 0.01

    # 가격-거래량 divergence: 가격 오르는데 거래량 추세 빠짐
    divergence = price_up and vt < -0.20

    # 극단 과매수 기준
    extreme_ob = (np.isfinite(d60) and d60 > 0.45) or c5 > 0.20
    moderate_ob = not extreme_ob and (
        (np.isfinite(d60) and d60 > 0.15) or c5 > 0.08
    )

    reasons: list[str] = []

    # ── 1. CLIMAX: 극단 과매수 + 거래량 폭발 + 연속 양봉 ─────────────
    if extreme_ob and vr > 3.0 and streak_up >= 4:
        reasons += [
            f"5일 누적 {c5:+.1%} / MA60 대비 {d60:+.1%} — 극단 과매수",
            f"거래량 {vr:.1f}x 폭발 + {streak_up}일 연속 양봉",
            "과열 매수세 소진 후 급반전 위험",
        ]
        return "CLIMAX", reasons

    # ── 2. EXHAUSTION: 극단 과매수 + 거래량 감소 (매수세 빠짐) ─────────
    if extreme_ob and divergence:
        reasons += [
            f"MA60 대비 {d60:+.1%} — 극단 과매수 구간",
            f"5일 거래량 추세 {vt:+.0%} — 매수세 빠지는 중",
            "가격은 유지되나 수급이 먼저 이탈",
        ]
        return "EXHAUSTION", reasons

    # ── 3. 극단 과매수 + 거래량 보통 ───────────────────────────────────
    if extreme_ob:
        reasons += [
            f"MA60 대비 {d60:+.1%} / 5일 누적 {c5:+.1%}",
            "단기 급등으로 평균 대비 크게 이탈 — 눌림 구간 필요",
        ]
        return "OVERBOUGHT", reasons

    # ── 4. BREAKOUT: 신고가 + 거래량 폭발 + MA 정배열 ──────────────────
    if at_high and vr > 2.0 and bull_align and not extreme_ob:
        reasons += [
            f"60일 신고가 돌파 (고점 대비 {dd60:+.1%})",
            f"거래량 {vr:.1f}x — 강한 매수세 유입",
            "MA 정배열 유지 — 추세 건전",
        ]
        return "BREAKOUT", reasons

    # ── 5. TREND_STRONG: MA 정배열 + 거래량 건전(최대 -20% 감소까지 허용) + 적정 과매수 ─────
    if bull_align and vt > -0.20 and moderate_ob:
        vol_comment = f"5일 거래량 추세 {vt:+.0%} — {'소폭 감소 (추세 이상 없음)' if vt < 0 else '수급 건전'}"
        reasons += [
            "MA 5 > 20 > 60 정배열 — 상승 추세 유효",
            vol_comment,
            f"MA60 대비 {d60:+.1%} — 추세 중간 구간",
        ]
        return "TREND_STRONG", reasons

    # ── 6. PULLBACK_HEALTHY: 단기 하락 + 거래량 감소 + 중기 추세 유지 ──
    mid_trend_ok = (np.isfinite(d60) and d60 > 0.05) or (not bear_align)
    if c5 < -0.03 and vt < -0.15 and mid_trend_ok:
        reasons += [
            f"5일 누적 {c5:+.1%} — 단기 조정",
            f"거래량 추세 {vt:+.0%} — 매도세 약함 (거래량 빠지며 하락)",
            "중기 추세 훼손 없음 — 전형적인 눌림목",
        ]
        return "PULLBACK_HEALTHY", reasons

    # ── 7. PULLBACK_RISKY: 단기 하락 + 거래량 증가 ─────────────────────
    if c5 < -0.03 and vt > 0.15:
        reasons += [
            f"5일 누적 {c5:+.1%} — 단기 하락",
            f"거래량 추세 {vt:+.0%} — 매도세 강한 하락",
            "거래량 실린 하락은 추가 하락 가능성",
        ]
        return "PULLBACK_RISKY", reasons

    # ── 8. DOWNTREND: MA 역배열 + 하락 모멘텀 ──────────────────────────
    if bear_align and c5 < 0:
        reasons += [
            "MA 5 < 20 < 60 역배열 — 하락 추세",
            f"5일 누적 {c5:+.1%} — 하락 모멘텀 지속",
        ]
        return "DOWNTREND", reasons

    # ── 9. BASE: 저변동성 횡보 ──────────────────────────────────────────
    if rvol < 0.30 and abs(d20) < 0.05:
        reasons += [
            f"실현변동성 {rvol:.0%} — 낮은 에너지 상태",
            f"MA20 대비 {d20:+.1%} — 평균 수렴",
            "에너지 축적 후 방향성 돌파 대기",
        ]
        return "BASE", reasons

    # ── 10. RECOVERY: 역배열에서 단기 반등 ─────────────────────────────
    if not bull_align and c5 > 0.03 and (np.isfinite(d60) and d60 < 0.05):
        reasons += [
            "하락 추세 중 단기 반등 시작",
            "추세 전환 여부 아직 불확실",
        ]
        return "RECOVERY", reasons

    # ── 11. 기본값: 혼재 상태 진단 ─────────────────────────────────────
    if bull_align:
        reasons.append("MA 정배열이나 추세 강도 부족 — 방향성 탐색 중")
    elif bear_align:
        reasons.append("MA 역배열이나 낙폭 크지 않음 — 하락 강도 약함")
    else:
        reasons.append("MA 배열 중립 — 방향성 미확립")
    if abs(vt) < 0.10:
        reasons.append(f"거래량 추세 {vt:+.0%} — 뚜렷한 수급 변화 없음")
    reasons.append(f"RSI {rsi:.0f} / MA20 대비 {d20:+.1%} / 5일 누적 {_pct(chg5)}")
    return "NEUTRAL", reasons


# ── Bear 레짐 격하 ────────────────────────────────────────────────────
def apply_regime(scenario: str, regime_state: str | None, tc: dict) -> tuple[str, bool]:
    """
    Bear 레짐이면 일부 시나리오를 한 단계 보수화.
    (격하 여부) bool 반환
    """
    if not _HAS_REGIME or regime_state != R_BEAR:
        return scenario, False

    downgrade_map = {
        "BREAKOUT":         "OVERBOUGHT",      # 레짐 역행 돌파는 페이크 가능성
        "TREND_STRONG":     "OVERBOUGHT",      # Bear 중 강세 = 역행, 눌림 깊을 수 있음
        "PULLBACK_HEALTHY": "NEUTRAL",         # Bear에서 눌림은 하락 가속 가능
    }
    if scenario in downgrade_map:
        return downgrade_map[scenario], True
    return scenario, False


# ── 출력 ─────────────────────────────────────────────────────────────
def _pct(v):
    return f"{v:+.2%}" if np.isfinite(v) else "  N/A"

def _bar(v, width=14):
    v = max(0, min(v, 100))
    n = int(round(v / 100 * width))
    return "█" * n + "░" * (width - n)

def print_report(ticker, name, tc, scenario, reasons, downgraded,
                 regime_state, regime_conf):
    info = SCENARIOS[scenario]
    print()
    print(SEP)
    print(f"  {name}  ({ticker})")
    print(SEP)

    # 가격
    cur = tc["close"]
    fmt = f"{cur:,.0f}" if cur > 100 else f"{cur:.2f}"
    print(f"  현재가   {fmt}   {_pct(tc['chg1d'])}")
    print(LINE)

    # 기술 지표
    print("  [기술적 지표]")
    d20 = tc["ma20_dev"]
    d60 = tc["ma60_dev"]

    def _tag(v, lo, hi, rev=False):
        if not np.isfinite(v): return ""
        over = v >= hi if not rev else v <= -hi
        mild = (lo <= v < hi) if not rev else (-hi < v <= -lo)
        return " ⚠" if over else (" △" if mild else "")

    print(f"    MA20 대비    {_pct(d20)}{_tag(d20, 0.10, 0.20)}")
    if np.isfinite(d60):
        print(f"    MA60 대비    {_pct(d60)}{_tag(d60, 0.15, 0.45)}")
    print(f"    60일 고점    {_pct(tc['dd60'])}")
    print(f"    RSI(14)      {tc['rsi']:.0f}{' ⚠ 과매수' if tc['rsi'] > 70 else (' △ 과매도' if tc['rsi'] < 30 else '')}")
    print(f"    실현변동성   {tc['rvol']:.0%}{_tag(tc['rvol'], 0.60, 0.90)}")
    print(f"    거래량(오늘) {tc['vol_ratio']:.1f}x  |  5일 추세 {tc['vol_trend']:+.0%}")
    print(f"    5일 누적     {_pct(tc['chg5d'])}{_tag(tc['chg5d'], 0.08, 0.20)}")
    if tc["streak_up"] >= 2:
        print(f"    연속 양봉    {tc['streak_up']}거래일")
    elif tc["streak_dn"] >= 2:
        print(f"    연속 음봉    {tc['streak_dn']}거래일")
    print(LINE)

    # 레짐
    if _HAS_REGIME and regime_state:
        _rn = {R_BULL: "Bull (저변동 상승)", R_BEAR: "Bear (고변동 하락)", R_CHOP: "Chop (횡보)"}
        _re = {R_BULL: "🟢", R_BEAR: "🔴", R_CHOP: "🟡"}
        print(f"  [시장 레짐]  {_re[regime_state]} {_rn[regime_state]}  {regime_conf:.0%}")
        if downgraded:
            print("    ↓ Bear 레짐으로 시나리오 한 단계 하향")
        print(LINE)

    # 시나리오 판단
    print(f"  [시나리오]  {info['color']} {info['label']}")
    for r in reasons:
        print(f"    • {r}")
    print(LINE)

    print("  [진입 전략]")
    print(f"    위험도    {info['risk']}")
    print(f"    타이밍    {info['timing']}")
    print(f"    프리장    {'가능' if info['premark'] else '비추천'}")
    print(LINE)

    # 최근 5거래일
    print("  [최근 5거래일]")
    ret_s = tc["ret_ser"]
    for dt, row in tc["tail"].iterrows():
        r_ser = ret_s.reindex([dt])
        rv = float(r_ser.iloc[0]) if len(r_ser) and np.isfinite(r_ser.iloc[0]) else 0.0
        arrow = "▲" if rv >= 0 else "▼"
        c = float(row["Close"])
        cf = f"{c:>10,.0f}" if c > 100 else f"{c:>10.2f}"
        vols = f"{row['Volume']/1e6:.1f}M주" if row["Volume"] < 1e9 else f"{row['Volume']/1e8:.0f}백만주"
        print(f"    {dt.date()}  {cf}  {arrow} {rv:+.2%}  {vols}")

    print(SEP)
    print()


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="종목 진입 타이밍 판단기")
    parser.add_argument("ticker")
    parser.add_argument("--name", default="")
    parser.add_argument("--no-regime", action="store_true")
    args = parser.parse_args()

    ticker = args.ticker
    name   = args.name or ticker

    print(f"▶ {ticker} 데이터 수집 중...")
    try:
        tc = compute_technicals(ticker)
    except Exception as e:
        print(f"[오류] {e}")
        sys.exit(1)

    regime_state = None
    regime_conf  = 0.0
    if _HAS_REGIME and not args.no_regime:
        market = "KR" if ticker.upper().endswith((".KS", ".KQ")) else "US"
        print(f"▶ 시장 레짐 분석 중 ({market})...")
        try:
            res          = get_market_regime(market)
            regime_state = res.state
            regime_conf  = res.probs.get(res.state, 0.0)
        except Exception as e:
            print(f"  레짐 분석 실패 ({e}) — 생략")

    scenario, reasons   = classify(tc)
    scenario, downgraded = apply_regime(scenario, regime_state, tc)

    print_report(ticker, name, tc, scenario, reasons, downgraded,
                 regime_state, regime_conf)


if __name__ == "__main__":
    main()
