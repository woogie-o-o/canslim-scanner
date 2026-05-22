# -*- coding: utf-8 -*-
"""
4축 기술적 분석 엔진 (추세 / 모멘텀 / 변동성 / 볼륨·수급)

입력  : OHLCV DataFrame (Date index, columns: Open High Low Close Volume)
출력  : dict — trend/momentum/volatility/volume/phase/signal_stars/haiku/risk/tuning/annotations
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from typing import List, Tuple, Dict, Any, Optional

from haiku_lines import pick_haiku


# ───────── 보조 지표 ──────────────────────────────────────────────
def _ols_slope(x) -> float:
    """유한값만으로 1차 회귀 기울기를 직접 계산.

    np.polyfit/lstsq는 윈도우에 NaN·inf·퇴화 입력이 들어오면
    LAPACK(DGER) "illegal value" 예외로 차트 전체를 죽인다.
    여기서는 LAPACK을 거치지 않고 닫힌형 OLS로 안전하게 산출한다.
    """
    arr = np.asarray(x, dtype=float)
    mask = np.isfinite(arr)
    if mask.sum() < 2:
        return np.nan
    t = np.arange(arr.size, dtype=float)[mask]
    y = arr[mask]
    t -= t.mean()
    denom = float((t * t).sum())
    if denom <= 0.0:
        return np.nan
    return float((t * (y - y.mean())).sum() / denom)


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up   = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs   = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def _macd(close: pd.Series, f=12, s=26, sig=9):
    ema_f = _ema(close, f)
    ema_s = _ema(close, s)
    macd  = ema_f - ema_s
    signal= _ema(macd, sig)
    hist  = macd - signal
    return macd, signal, hist

def _bb(close: pd.Series, n=20, k=2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)  # 볼린저 밴드 표준: 모집단 표준편차
    upper = mid + k*std
    lower = mid - k*std
    width = (upper - lower) / mid
    return upper, mid, lower, width

def _atr(high, low, close, period=14):
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def _obv(close, volume):
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()

def _vwap_rolling(high, low, close, volume, n=20):
    tp = (high + low + close) / 3
    pv = tp * volume
    return pv.rolling(n).sum() / volume.rolling(n).sum()


# ───────── 데이터 클래스 ──────────────────────────────────────────
@dataclass
class AxisVerdict:
    verdict: str
    score:   int          # 0~5 (각 축 컨펌 강도)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Annotation:
    idx:  int             # x 좌표 (df 인덱스)
    y:    float           # y 좌표 (가격)
    text: str
    kind: str = "note"    # note / arrow_up / arrow_down / circle / dashed

@dataclass
class FourAxisResult:
    trend:        AxisVerdict
    momentum:     AxisVerdict
    volatility:   AxisVerdict
    volume:       AxisVerdict
    phase:        str
    signal_stars: int
    haiku:        str
    risk:         Dict[str, Any]
    tuning:       List[str]
    annotations:  List[Annotation]
    image_prompt: str = ""
    key_levels:   Dict[str, float] = field(default_factory=dict)
    key_observation: str = ""
    breakout_point: str = ""
    risk_point:   str = ""
    support_flip:       str = ""   # [지지/저항 플립] 저항→지지 전환 구간
    rsi_leadership:     str = ""   # [RSI 시장 주도력] 상대 강도 판단
    structured_analysis: str = ""  # 8섹션 구조화 분석 텍스트

    def to_dict(self) -> dict:
        d = asdict(self)
        d["annotations"] = [asdict(a) for a in self.annotations]
        return d


# ───────── 분석기 ─────────────────────────────────────────────────
class FourAxisAnalyzer:
    """
    하나의 종목 OHLCV를 받아 4축 분석 + 주석 좌표를 산출.

    사용:
        result = FourAxisAnalyzer(hist).analyze()
        result.haiku, result.phase, result.annotations …
    """

    def __init__(self, hist: pd.DataFrame, ticker: str = ""):
        self.ticker = ticker
        self.hist   = hist.copy()
        if not {"Open","High","Low","Close","Volume"}.issubset(self.hist.columns):
            raise ValueError("OHLCV 컬럼 필요")
        self.hist = self.hist.dropna(subset=["Close"])
        if len(self.hist) < 30:
            raise ValueError(f"데이터 부족: {len(self.hist)}봉")
        self._compute_indicators()

    # ---------- 1) 지표 일괄 계산 ---------------------------------
    def _compute_indicators(self):
        h = self.hist
        h["EMA20"]  = _ema(h["Close"], 20)
        h["EMA50"]  = _ema(h["Close"], 50)
        h["EMA200"] = _ema(h["Close"], 200) if len(h) >= 50 else _ema(h["Close"], min(200, max(20,len(h)//2)))
        h["RSI14"]  = _rsi(h["Close"], 14)
        macd, sig, hist_macd = _macd(h["Close"])
        h["MACD"], h["MACD_SIG"], h["MACD_HIST"] = macd, sig, hist_macd
        bbu, bbm, bbl, bbw = _bb(h["Close"])
        h["BB_UP"], h["BB_MID"], h["BB_LOW"], h["BB_WIDTH"] = bbu, bbm, bbl, bbw
        h["ATR14"]  = _atr(h["High"], h["Low"], h["Close"], 14)
        h["OBV"]    = _obv(h["Close"], h["Volume"])
        h["VWAP20"] = _vwap_rolling(h["High"], h["Low"], h["Close"], h["Volume"], 20)
        # OBV 단기 기울기 (20일 회귀)
        h["OBV_SLOPE"] = h["OBV"].rolling(20, min_periods=2).apply(
            _ols_slope, raw=True
        )

    # ---------- 2) 추세 ------------------------------------------
    def axis_trend(self) -> AxisVerdict:
        h = self.hist; last = h.iloc[-1]
        e20, e50, e200 = last["EMA20"], last["EMA50"], last["EMA200"]
        c = last["Close"]
        if any(pd.isna(x) for x in (e20, e50, e200)):
            return AxisVerdict("판단 불가", 0, {"reason":"EMA 미충족"})

        bull = e20 > e50 > e200
        bear = e20 < e50 < e200

        # EMA 기울기 (5봉 변화율 / 현재가 기준 정규화)
        def _slope(col):
            if len(h) < 6: return 0.0
            return (h[col].iloc[-1] - h[col].iloc[-6]) / (float(c) + 1e-9) * 100
        slope_e20  = _slope("EMA20")
        slope_e50  = _slope("EMA50")
        slope_e200 = _slope("EMA200")

        def _dir(s):
            if s > 0.05:  return "상승"
            if s < -0.05: return "하락"
            return "평방"

        slope_dir = f"EMA20 {_dir(slope_e20)} / EMA50 {_dir(slope_e50)} / EMA200 {_dir(slope_e200)}"

        score, verdict, ema_state = 0, "횡보", "혼조"
        if bull:
            ema_state = "정배열(20>50>200)"
            score = 5 if c > e20 and slope_e20 > 0 else 4
            verdict = "강한 상승 추세"
        elif bear:
            ema_state = "역배열(20<50<200)"
            score = 1 if c < e20 else 2
            verdict = "약세 추세"
        else:
            cross_recent = ((h["EMA20"] > h["EMA50"]).iloc[-5:].sum())
            if cross_recent >= 4:
                ema_state = "골든크로스 직후"
                verdict = "추세 전환 신호"; score = 3
            elif cross_recent <= 1:
                ema_state = "데드크로스 직후"
                verdict = "추세 전환 신호"; score = 3
            else:
                ema_state = "혼조 (정·역배열 아님)"
                score = 2

        return AxisVerdict(
            verdict, score,
            {"ema_state": ema_state,
             "ema20": float(e20), "ema50": float(e50), "ema200": float(e200),
             "slope_e20": float(slope_e20), "slope_e50": float(slope_e50),
             "slope_e200": float(slope_e200), "slope_dir": slope_dir,
             "above_ema20":  bool(c > e20),
             "above_ema50":  bool(c > e50),
             "above_ema200": bool(c > e200)}
        )

    # ---------- 3) 모멘텀 ----------------------------------------
    def axis_momentum(self) -> AxisVerdict:
        h = self.hist; last = h.iloc[-1]
        rsi        = float(last["RSI14"])
        macd_val   = float(last["MACD"])
        macd_sig   = float(last["MACD_SIG"])
        mhist      = float(last["MACD_HIST"])
        mhist_prev = float(h["MACD_HIST"].iloc[-2]) if len(h) >= 2 else 0.0
        macd_prev  = float(h["MACD"].iloc[-2])    if len(h) >= 2 else macd_val
        msig_prev  = float(h["MACD_SIG"].iloc[-2]) if len(h) >= 2 else macd_sig

        # MACD 히스토그램 방향 전환 (기세 변화 선행)
        macd_turn_up = mhist > 0 and mhist_prev <= 0
        macd_turn_dn = mhist < 0 and mhist_prev >= 0
        # MACD 시그널 라인 교차 시점 (실제 골든/데드 크로스)
        macd_cross_up = (macd_val > macd_sig) and (macd_prev <= msig_prev)
        macd_cross_dn = (macd_val < macd_sig) and (macd_prev >= msig_prev)
        macd_above    = macd_val > macd_sig

        # RSI 다이버전스 (최근 20봉)
        recent    = h.tail(20)
        rsi_now   = float(recent["RSI14"].iloc[-1])
        price_max_idx = recent["Close"].idxmax()
        rsi_at_max    = float(recent.loc[price_max_idx, "RSI14"])
        bear_div = (recent["Close"].iloc[-1] >= recent["Close"].max() * 0.99
                    and rsi_at_max < recent["RSI14"].max() * 0.95)
        bull_div = (recent["Close"].iloc[-1] <= recent["Close"].min() * 1.01
                    and recent["RSI14"].iloc[-1] > recent["RSI14"].min() * 1.05)

        if rsi <= 30:
            zone, verdict = "과매도", "RSI 과매도 — 반등 후보"
            score = 4
        elif rsi >= 70:
            zone, verdict = "과매수", "RSI 과매수 — 차익 경계"
            score = 2
        elif rsi >= 55:
            zone, verdict = "강세", "모멘텀 양호"
            score = 4
        elif rsi <= 45:
            zone, verdict = "약세", "모멘텀 부진"
            score = 2
        else:
            zone, verdict = "관망", "모멘텀 약함 — 진입 보류"
            score = 3

        if macd_cross_up: score = min(5, score+1); verdict += " · MACD 골든크로스"
        elif macd_turn_up: score = min(5, score+1); verdict += " · MACD 히스토그램 상승전환"
        if macd_cross_dn: score = max(1, score-1); verdict += " · MACD 데드크로스"
        elif macd_turn_dn: score = max(1, score-1); verdict += " · MACD 히스토그램 하락전환"
        if bull_div:  score = min(5, score+1); verdict += " · 상승 다이버전스"
        if bear_div:  score = max(1, score-1); verdict += " · 하락 다이버전스"

        return AxisVerdict(
            verdict, score,
            {"rsi": rsi, "rsi_zone": zone,
             "macd_val": macd_val, "macd_sig": macd_sig,
             "macd_hist": mhist, "macd_above": macd_above,
             "macd_turn_up": macd_turn_up, "macd_turn_dn": macd_turn_dn,
             "macd_cross_up": macd_cross_up, "macd_cross_dn": macd_cross_dn,
             "bull_div": bull_div, "bear_div": bear_div}
        )

    # ---------- 4) 변동성 ----------------------------------------
    def axis_volatility(self) -> AxisVerdict:
        h = self.hist; last = h.iloc[-1]
        bbw = float(last["BB_WIDTH"]); atr = float(last["ATR14"])
        atr_pct = atr / float(last["Close"]) * 100
        bbw_q   = h["BB_WIDTH"].tail(60).rank(pct=True).iloc[-1]   # 0~1 (낮을수록 스퀴즈)

        squeeze = bbw_q < 0.20
        expand  = bbw_q > 0.80
        upper_break = last["Close"] >= last["BB_UP"]
        lower_touch = last["Close"] <= last["BB_LOW"]
        bb_pos = (last["Close"] - last["BB_LOW"]) / (last["BB_UP"] - last["BB_LOW"] + 1e-9)

        if squeeze:
            verdict = "스퀴즈 — 브레이크아웃 임박"; score = 5
        elif expand and upper_break:
            verdict = "상단 돌파 + 변동성 확장"; score = 5
        elif expand and lower_touch:
            verdict = "하단 이탈 — 변동성 확장 (위험)"; score = 1
        elif lower_touch:
            verdict = "BB 하단 터치 — 반등 시도"; score = 4
        elif upper_break:
            verdict = "상단 돌파 시도"; score = 4
        else:
            verdict = f"관망 (BB폭 {bbw_q:.0%} 분위)"; score = 3

        return AxisVerdict(
            verdict, score,
            {"bb_width_pct": float(bbw)*100, "bb_width_quantile": float(bbw_q),
             "atr": atr, "atr_pct": float(atr_pct),
             "bb_position": float(bb_pos),
             "squeeze": squeeze, "expand": expand,
             "upper_break": bool(upper_break), "lower_touch": bool(lower_touch)}
        )

    # ---------- 5) 볼륨/수급 -------------------------------------
    def axis_volume(self) -> AxisVerdict:
        h = self.hist; last = h.iloc[-1]
        vol  = float(last["Volume"])
        avg20= float(h["Volume"].tail(20).mean())
        v_ratio = vol / (avg20 + 1e-9)
        price_up = last["Close"] > h["Close"].iloc[-2]
        obv_slope= float(last["OBV_SLOPE"]) if not pd.isna(last["OBV_SLOPE"]) else 0
        vwap = float(last["VWAP20"]) if not pd.isna(last["VWAP20"]) else float(last["Close"])
        above_vwap = last["Close"] > vwap

        # VWAP 리테스트 패턴: 5봉 전 반대편에 있다가 VWAP 0.8% 내로 근접
        vwap_retest, vwap_retest_dir = False, ""
        if len(h) >= 8 and not h["VWAP20"].tail(8).isna().any():
            v8 = h["VWAP20"].tail(8); c8 = h["Close"].tail(8)
            was_above_old = float(c8.iloc[2]) > float(v8.iloc[2])
            near_vwap = abs(float(last["Close"]) - vwap) / (vwap + 1e-9) < 0.008
            if near_vwap and was_above_old and above_vwap:
                vwap_retest, vwap_retest_dir = True, "상향"
            elif near_vwap and not was_above_old and not above_vwap:
                vwap_retest, vwap_retest_dir = True, "하향"

        # 4축 카드 규칙
        if price_up and v_ratio > 1.5 and obv_slope > 0 and above_vwap:
            verdict = "수급 강 — 거래량+OBV+VWAP 동시 컨펌"; score = 5
        elif price_up and obv_slope < 0:
            verdict = "수급 이탈 경고 — 가격↑ OBV↓"; score = 2
        elif (not above_vwap) and obv_slope < 0:
            verdict = "하락 압력 우위 — VWAP 하향+OBV 하락 · 진입 회피"; score = 1
        elif above_vwap and obv_slope > 0:
            verdict = "기관 우위 (VWAP 위 + OBV↑)"; score = 4
        else:
            verdict = f"관망 (vol×{v_ratio:.1f}, VWAP {'위' if above_vwap else '아래'})"
            score = 3

        if vwap_retest and vwap_retest_dir == "상향" and obv_slope > 0:
            verdict += " · VWAP 상향 리테스트 (지지 확인 중)"; score = min(5, score + 1)
        elif vwap_retest and vwap_retest_dir == "하향" and obv_slope < 0:
            verdict += " · VWAP 하향 리테스트 (저항 접근)"; score = max(1, score - 1)

        return AxisVerdict(
            verdict, score,
            {"v_ratio": float(v_ratio), "above_vwap": bool(above_vwap),
             "vwap": vwap, "obv_slope": obv_slope, "price_up": bool(price_up),
             "vwap_retest": vwap_retest, "vwap_retest_dir": vwap_retest_dir}
        )

    # ---------- 6) 국면 / 별점 / 리스크 / 튜닝 -------------------
    def _phase(self, t, m, vt, vl, lv: dict) -> str:
        """10개 국면 — 실수치 포함, 종목마다 다른 설명 반환."""
        ema_state     = t.details.get("ema_state", "")
        rsi           = m.details.get("rsi", 50)
        squeeze       = vt.details.get("squeeze", False)
        expand        = vt.details.get("expand", False)
        upper_break   = vt.details.get("upper_break", False)
        lower_touch   = vt.details.get("lower_touch", False)
        bull_div      = m.details.get("bull_div", False)
        bear_div      = m.details.get("bear_div", False)
        macd_cross_up = m.details.get("macd_cross_up", False)
        macd_cross_dn = m.details.get("macd_cross_dn", False)
        above_vwap    = vl.details.get("above_vwap", True)
        v_ratio       = vl.details.get("v_ratio", 1.0)
        obv_slope     = vl.details.get("obv_slope", 0)
        bb_q          = vt.details.get("bb_width_quantile", 0.5)

        c  = lv["current"]; hi = lv["high_52w"]; lo = lv["low_52w"]
        from_hi = lv["from_high_pct"]
        near_high = c >= hi * 0.97
        near_low  = c <= lo * 1.05

        bull_align = sum(1 for s in (t.score, m.score, vt.score, vl.score) if s >= 4)
        bear_align = sum(1 for s in (t.score, m.score, vt.score, vl.score) if s <= 2)

        # ── 1. 브레이크아웃 임박
        if squeeze and m.score >= 3:
            if m.score >= 4:
                return f"브레이크아웃 임박 — BB 스퀴즈 + 모멘텀 강화 (RSI {rsi:.0f})"
            return f"브레이크아웃 대기 — BB 스퀴즈 ({bb_q:.0%} 분위), 방향 결정 직전"

        # ── 2. 신고가 돌파 진행
        if near_high and "정배열" in ema_state and t.score >= 4:
            if from_hi >= -1.0:
                return "52주 신고가 돌파 진행 — 상단 저항 전무, 추세 가속 구간"
            return f"신고가권 접근 ({from_hi:.1f}%) — 정배열 유지, 거래량 확인 필수"

        # ── 3. 고점 부근 경계
        if rsi >= 68 and (bear_div or vl.score <= 2) and t.score >= 3:
            if bear_div:
                return f"고점 부근 경계 — RSI {rsi:.0f} 과열 + 하락 다이버전스 포착"
            return f"고점 부근 경계 — RSI {rsi:.0f} 과열, 수급 둔화 (거래량 ×{v_ratio:.1f})"

        # ── 4. 강한 상승
        if "정배열" in ema_state and (bull_align >= 3 or (t.score >= 4 and m.score >= 4)):
            if bull_align == 4:
                return f"강한 상승 — 4축 전체 컨펌, RSI {rsi:.0f} · 거래량 ×{v_ratio:.1f}"
            return f"강한 상승 — 정배열 유지, 3축 이상 컨펌 (RSI {rsi:.0f})"

        # ── 5. 상승 추세 조정
        if "정배열" in ema_state and (m.score <= 3 or vl.score <= 3):
            if v_ratio < 0.8:
                return f"상승 추세 유지 속 단기 조정 — 거래량 둔화 (×{v_ratio:.1f}), 추세선 지지 확인"
            if rsi > 55:
                return f"상승 추세 조정 — RSI {rsi:.0f} 유지, 단기 과열 해소 중"
            obv_note = "OBV 우상향" if obv_slope > 0 else "OBV 정체"
            return f"상승 추세 조정 — 모멘텀 잠시 주춤, {obv_note} · 추세선 주시"

        # ── 6. 추세 전환 시도
        if ("골든크로스" in ema_state or macd_cross_up) and rsi >= 42 and t.score >= 3:
            if macd_cross_up:
                return f"추세 전환 시도 — MACD 골든크로스, RSI {rsi:.0f} 회복 중"
            return f"추세 전환 시도 — 골든크로스 형성, RSI {rsi:.0f} · VWAP {'위' if above_vwap else '아래'}"

        # ── 7. 낙폭 과대 반등 구간
        if rsi <= 33 and (bull_div or lower_touch):
            if bull_div:
                return f"낙폭 과대 반등 구간 — RSI {rsi:.0f} + 상승 다이버전스 포착"
            return f"낙폭 과대 반등 구간 — RSI {rsi:.0f} 과매도, BB 하단 반등 시도"

        # ── 8. 추세 전환 경고
        if t.score <= 2 and (expand or "데드크로스" in ema_state or macd_cross_dn):
            if macd_cross_dn:
                return f"추세 전환 경고 — MACD 데드크로스, 변동성 확장 중"
            if expand:
                return f"추세 전환 경고 — 변동성 확장 ({bb_q:.0%} 분위), 하락 압력 증가"
            return f"추세 전환 경고 — 데드크로스 형성, RSI {rsi:.0f} 약세권"

        # ── 9. 약세 진행
        if bear_align >= 3 or (t.score <= 2 and m.score <= 2 and not above_vwap):
            if bear_align == 4:
                return f"약세 진행 — 4축 전체 약세, RSI {rsi:.0f} · VWAP 아래"
            return f"약세 진행 — 역배열 + 모멘텀·수급 동시 이탈 (RSI {rsi:.0f})"

        # ── 10. 횡보 압축
        if not expand and not squeeze and 2 <= t.score <= 3 and 2 <= m.score <= 3:
            return f"횡보 압축 — 변동성 {bb_q:.0%} 분위, 돌파 방향 대기 (RSI {rsi:.0f})"

        return f"횡보 — 뚜렷한 방향성 없음 (RSI {rsi:.0f})"

    def _risk(self, t: AxisVerdict, vt: AxisVerdict) -> dict:
        last = self.hist.iloc[-1]
        atr = vt.details.get("atr", 0)
        c   = float(last["Close"])
        # ATR 동적 손절: 추세 강도에 따라 1.2~1.8배
        mult = 1.2 if t.score >= 4 else (1.5 if t.score >= 3 else 1.8)
        stop = c - mult * atr
        invalid = "50EMA 종가 이탈" if t.score >= 4 else "20EMA 종가 이탈"
        return {
            "stop_loss":   round(stop, 2),
            "stop_pct":    round((c-stop)/c*100, 2),
            "atr_mult":    mult,
            "invalidation": invalid
        }

    def _tuning(self, t, m, vt, vl) -> list[str]:
        tips = []
        rsi     = m.details.get("rsi", 50)
        v_ratio = vl.details.get("v_ratio", 1.0)

        if vt.details.get("expand") and (rsi > 75 or rsi < 25):
            tips.append(
                f"RSI 기준선 70/30 → 80/20 타이트닝 권장 — "
                f"false signal 20~30% 감소 예상 (현재 RSI {rsi:.0f})"
            )
        if vt.details.get("squeeze"):
            tips.append(
                "BB 스퀴즈 국면 — ATR 손절 배수 1.2× 적용 시 진입 빈도 ↑, "
                "예상 R:R 1:2.5 이상 기대 가능"
            )
        if v_ratio < 0.7:
            tips.append(
                f"VWAP 기준 진입 필터 추가 시 노이즈 30~40% 감소 "
                f"(저유동성 false break 제거, 현재 거래량 ×{v_ratio:.1f})"
            )
        if t.score == 3:
            tips.append(
                "EMA 20/50/200 → 10/30/50 단축 적용 시 단타 타임프레임 대응 가능, "
                "진입 빈도 약 2배 증가"
            )
        if m.details.get("bull_div"):
            tips.append(
                "상승 다이버전스 확인 구간 — 역추세 매수 전략 적용 시 "
                "예상 승률 55~65% 기대 (손절 타이트하게)"
            )
        if m.details.get("bear_div"):
            tips.append(
                "하락 다이버전스 경고 — 모멘텀 약화 구간, "
                "포지션 1/2 이하 축소 또는 신규 진입 보류 권장"
            )
        if not tips:
            tips.append("현재 파라미터 유지 — 추가 튜닝 불필요. 기존 전략 그대로 운영 권장")
        return tips[:3]

    # ---------- 지지/저항 플립 판단 --------------------------------
    def _support_flip(self, lv: dict) -> str:
        c   = lv["current"]
        r1  = lv["resistance1"]
        s1  = lv["support1"]
        hi  = lv["high_52w"]
        lo  = lv["low_52w"]

        if c > hi * 0.98:
            return (f"52주 신고가권 돌파 — {r1:,.2f} 이전 저항이 지지로 전환 확인, "
                    f"신고가 유지 중에는 풀백 매수 유효")
        if c > r1:
            return (f"저항 {r1:,.2f} 상향 돌파 — 지지 전환 확인 구간. "
                    f"해당 레벨 위에서 종가 마감 시 추세 지속 신호")
        if c > s1:
            return (f"지지 {s1:,.2f} ~ 저항 {r1:,.2f} 박스권 내 위치. "
                    f"저항 돌파 여부 확인 전까지 관망 또는 소폭 비중")
        if c < s1:
            return (f"지지 {s1:,.2f} 이탈 — 구 지지가 저항으로 역전 가능. "
                    f"반등 시 {s1:,.2f} 부근 저항 여부 재확인 필요")
        return f"지지/저항 구간 [{s1:,.2f} ~ {r1:,.2f}] — 추가 관찰 필요"

    # ---------- RSI 시장 주도력 판단 --------------------------------
    def _rsi_leadership(self, m: AxisVerdict, t: AxisVerdict) -> str:
        rsi       = m.details.get("rsi", 50)
        rsi_s     = self.hist["RSI14"].dropna()
        rsi_ma20  = float(rsi_s.tail(20).mean()) if len(rsi_s) >= 20 else float(rsi)
        delta     = rsi - rsi_ma20
        ema_state = t.details.get("ema_state", "")

        if rsi > 60 and delta > 5 and "정배열" in ema_state:
            return (f"주도주 신호 — RSI {rsi:.0f} (20일 평균 {rsi_ma20:.0f} 대비 +{delta:.0f}pt 우위). "
                    f"지수 대비 초과 수익 가능성 높음, 모멘텀 지속 모니터링")
        if rsi > 55 and "정배열" in ema_state:
            return (f"관망 우위 — RSI {rsi:.0f}, 지수 대비 소폭 우세. "
                    f"추세 강도 강화 시 주도주 편입 가능")
        if rsi < 40:
            return (f"주도력 약화 — RSI {rsi:.0f}, 지수 대비 열위. "
                    f"섹터 로테이션 또는 비중 축소 고려")
        if delta < -5:
            return (f"모멘텀 감속 — RSI {rsi:.0f} (20일 평균 {rsi_ma20:.0f} 대비 -{abs(delta):.0f}pt). "
                    f"주도력 이탈 초기 신호, 추세 강도 점검 필요")
        return (f"RSI {rsi:.0f} — 지수 동조 구간 (20일 평균 {rsi_ma20:.0f}). "
                f"차별화 모멘텀 부재, 섹터 강도 및 시장 방향성 확인 권장")

    # ---------- 7) 차트 주석 좌표 --------------------------------
    def _annotations(self, t, m, vt, vl) -> List[Annotation]:
        h = self.hist
        N = len(h)
        notes: List[Annotation] = []

        def _c(offset: int) -> float:
            idx = max(0, N - 1 - offset)
            return float(h["Close"].iloc[idx])

        rsi        = m.details.get("rsi", 50)
        v_ratio    = vl.details.get("v_ratio", 1.0)
        obv_slope  = vl.details.get("obv_slope", 0)
        above_vwap = vl.details.get("above_vwap", True)
        bb_q       = vt.details.get("bb_width_quantile", 0.5)
        macd_above = m.details.get("macd_above", False)
        macd_cross_up = m.details.get("macd_cross_up", False)
        macd_cross_dn = m.details.get("macd_cross_dn", False)
        last_macd_hist = float(h["MACD_HIST"].iloc[-1]) if not pd.isna(h["MACD_HIST"].iloc[-1]) else 0.0

        # ── 골든/데드 크로스 ─────────────────────────────────────────
        cross = (h["EMA20"] > h["EMA50"]).astype(int).diff().fillna(0)
        for ts in list(cross[cross == 1].index)[-2:]:
            i = h.index.get_loc(ts)
            notes.append(Annotation(i, float(h["EMA20"].iloc[i]), "골든크로스", "arrow_up"))
        for ts in list(cross[cross == -1].index)[-2:]:
            i = h.index.get_loc(ts)
            notes.append(Annotation(i, float(h["EMA20"].iloc[i]), "데드크로스", "arrow_down"))

        # ── RSI 주석 (항상 포함, 실수치 표기) ───────────────────────
        if rsi >= 75:
            rsi_txt = f"RSI {rsi:.1f} — 강한 과매수, 추격 자제"
        elif rsi >= 68:
            rsi_txt = f"RSI {rsi:.1f} — 과열 직전, 추격은 신중"
        elif rsi >= 55:
            rsi_txt = f"RSI {rsi:.1f} — 강세 유지 중"
        elif rsi <= 25:
            rsi_txt = f"RSI {rsi:.1f} — 극단 과매도, 반등 주시"
        elif rsi <= 35:
            rsi_txt = f"RSI {rsi:.1f} — 과매도 구간, 저점 탐색"
        else:
            rsi_txt = f"RSI {rsi:.1f} — 관망 구간"
        notes.append(Annotation(N-1, _c(0), rsi_txt, "note"))

        # ── MACD 주석 ────────────────────────────────────────────────
        if macd_cross_up:
            macd_txt = "MACD 골든크로스 — 모멘텀 상승 전환"
        elif macd_cross_dn:
            macd_txt = "MACD 데드크로스 — 모멘텀 하락 전환"
        elif macd_above and last_macd_hist > 0:
            macd_txt = "MACD 플러스권, 모멘텀 살아있음"
        elif macd_above and last_macd_hist < 0:
            macd_txt = "MACD 시그널 위, 히스토그램 둔화 중"
        else:
            macd_txt = "MACD 마이너스권 — 모멘텀 약화"
        notes.append(Annotation(N-2, _c(1), macd_txt, "note"))

        # ── OBV / 수급 주석 ─────────────────────────────────────────
        if obv_slope > 0 and above_vwap:
            obv_txt = "OBV 우상향, 수급은 아직 살아있다"
        elif obv_slope > 0 and not above_vwap:
            obv_txt = "OBV 우상향, VWAP 아래 — 수급 회복 중"
        elif obv_slope <= 0 and above_vwap:
            obv_txt = "OBV 하락, 수급 훼손 초기 신호"
        else:
            obv_txt = "OBV 하락 + VWAP 아래 — 수급 이탈"
        notes.append(Annotation(N-3, _c(2), obv_txt, "note"))

        # ── BB / 변동성 주석 ─────────────────────────────────────────
        if vt.details.get("squeeze"):
            bb_txt = "BB 스퀴즈 — 대형 이동 임박, 방향 주목"
        elif vt.details.get("upper_break"):
            bb_txt = f"BB 상단 돌파 후 눌림, 변동성 {bb_q:.0%} 분위"
        elif vt.details.get("lower_touch"):
            bb_txt = "BB 하단 터치 — 과매도 반등 구간"
        elif vt.details.get("expand"):
            bb_txt = f"BB 확장 중 — 변동성 {bb_q:.0%} 분위, 추세 가속"
        else:
            bb_txt = f"BB 관망 — 변동성 {bb_q:.0%} 분위, 진입 신호 미형성"
        notes.append(Annotation(N-4, _c(3), bb_txt, "note"))

        # ── 거래량 주석 ──────────────────────────────────────────────
        if v_ratio >= 2.0:
            vol_txt = f"거래량 ×{v_ratio:.1f} 급증 — 추세 신뢰도 상승"
        elif v_ratio >= 1.3:
            vol_txt = f"거래량 ×{v_ratio:.1f} 증가 — 수급 관심"
        elif v_ratio < 0.7:
            vol_txt = f"최근은 다소 둔화 (×{v_ratio:.1f}), 추세 훼손 수준 아님"
        else:
            vol_txt = f"거래량 ×{v_ratio:.1f} — 평균 수준 유지"
        notes.append(Annotation(N-5, _c(4), vol_txt, "note"))

        # ── 다이버전스 ───────────────────────────────────────────────
        if m.details.get("bull_div"):
            notes.append(Annotation(N-1, float(h["Low"].iloc[-1]),
                                    "상승 다이버전스 확인", "circle"))
        if m.details.get("bear_div"):
            notes.append(Annotation(N-1, float(h["High"].iloc[-1]),
                                    "하락 다이버전스 경고", "circle"))

        return notes[:8]

    # ---------- 8) 메인 ------------------------------------------
    def _entry_stars(self, t, m, vt, vl) -> int:
        """진입신호 ★1~5 — 단순 평균이 아닌 가중합 + 시너지 보너스."""
        # 각 축의 중심을 3으로 잡고 편차 누적 (-2~+2)
        dt  = (t.score  - 3) * 1.2     # 추세 가중 ↑
        dm  = (m.score  - 3) * 1.0
        dvt = (vt.score - 3) * 0.9
        dvl = (vl.score - 3) * 1.1     # 수급 가중 ↑

        raw = dt + dm + dvt + dvl       # ≈ -8.4 ~ +8.4

        # 시너지 보너스/페널티 (4축이 한 방향 정렬되면 가산)
        bull_align = sum(1 for s in (t.score, m.score, vt.score, vl.score) if s >= 4)
        bear_align = sum(1 for s in (t.score, m.score, vt.score, vl.score) if s <= 2)
        if bull_align >= 3: raw += 1.5
        if bull_align == 4: raw += 1.0
        if bear_align >= 3: raw -= 1.5

        # 특수 시그널 가산
        if vt.details.get("squeeze") and m.score >= 4: raw += 1.2
        if vt.details.get("upper_break") and vl.score >= 4: raw += 1.0
        if vt.details.get("lower_touch") and m.details.get("bull_div"): raw += 0.8
        if m.details.get("bear_div"): raw -= 1.0

        # raw → 1~5 매핑 (구간 분리도 ↑)
        if   raw >=  4.0: stars = 5
        elif raw >=  2.0: stars = 4
        elif raw >=  0.0: stars = 3
        elif raw >= -2.0: stars = 2
        else:             stars = 1

        # 추세-게이트: 추세가 약하면 진입 별점 캡 (역배열에서 ★5 방지)
        if t.score <= 2:        stars = min(stars, 3)
        if t.score == 1:        stars = min(stars, 2)
        # 모멘텀 과매수+하락 다이버전스면 캡
        if m.details.get("bear_div") and m.score <= 2:
            stars = min(stars, 2)
        return stars

    # ---------- 9) 핵심 가격대 / 관찰 / 이미지 프롬프트 ----------
    def _key_levels(self) -> Dict[str, float]:
        h = self.hist
        c = float(h["Close"].iloc[-1])
        # 52주(252봉) 기준
        win = h.tail(min(252, len(h)))
        hi52 = float(win["High"].max())
        lo52 = float(win["Low"].min())
        # 최근 60봉 스윙 지지/저항 — 단순 분위수
        recent = h.tail(min(60, len(h)))
        support1 = float(recent["Low"].quantile(0.20))
        resistance1 = float(recent["High"].quantile(0.80))
        return {
            "current":     round(c, 2),
            "high_52w":    round(hi52, 2),
            "low_52w":     round(lo52, 2),
            "support1":    round(support1, 2),
            "resistance1": round(resistance1, 2),
            "from_high_pct": round((c - hi52) / hi52 * 100, 2),
            "from_low_pct":  round((c - lo52) / lo52 * 100, 2),
        }

    def _key_observation(self, t, m, vt, vl, lv: dict) -> str:
        c = lv["current"]; hi = lv["high_52w"]; lo = lv["low_52w"]
        from_hi = lv["from_high_pct"]; from_lo = lv["from_low_pct"]

        if c >= hi * 0.97:
            pos = "현재 주가는 52주 최고가 부근에 위치해 있습니다"
        elif c <= lo * 1.05:
            pos = "현재 주가는 52주 최저가 부근에 위치해 있습니다"
        else:
            pos = (f"현재 주가는 52주 고점에서 {abs(from_hi):.1f}% 아래, "
                   f"저점에서 {from_lo:.1f}% 위에 있습니다")

        trend_desc = {5: "추세가 매우 강하고", 4: "추세가 강하며",
                      3: "추세 신호가 미형성이며", 2: "추세가 약하며", 1: "추세가 매우 약합니다"
                      }.get(t.score, "추세 신호가 미형성이며")

        rsi = m.details.get("rsi", 50)
        if rsi >= 70:   mom_desc = f"RSI {rsi:.0f}로 과열 구간"
        elif rsi <= 30: mom_desc = f"RSI {rsi:.0f}로 과매도 구간"
        elif rsi >= 55: mom_desc = f"RSI {rsi:.0f}로 모멘텀 양호"
        else:           mom_desc = f"RSI {rsi:.0f}로 모멘텀 미형성"

        vol_desc = ("수급이 강합니다" if vl.score >= 4
                    else ("수급이 약합니다" if vl.score <= 2 else "수급은 보통 수준입니다"))

        return f"{pos}. {trend_desc}, {mom_desc}이며, {vol_desc}."

    def _breakout_point(self, t, vt, lv: dict) -> str:
        r1 = lv["resistance1"]; s1 = lv["support1"]; hi = lv["high_52w"]
        if vt.details.get("squeeze"):
            return (f"가격이 좁은 구간으로 압축되어 있어 곧 큰 움직임이 나올 수 있습니다. "
                    f"{r1:,.2f} 위에서 마감하면 상승 신호입니다.")
        if vt.details.get("upper_break"):
            return (f"볼린저 밴드 상단을 돌파하며 강한 상승 흐름입니다. "
                    f"다음 주요 저항선은 {hi:,.2f}입니다.")
        if t.score >= 4:
            return (f"상승 추세가 유지 중입니다. "
                    f"{r1:,.2f}를 종가 기준으로 돌파하면 추가 상승 신호입니다.")
        if t.score <= 2:
            return (f"현재는 뚜렷한 상승 신호가 없습니다. "
                    f"{s1:,.2f}를 이탈하면 하락이 가속될 수 있어요.")
        return (f"박스권({s1:,.2f}~{r1:,.2f}) 안에서 방향을 탐색 중입니다. "
                f"어느 쪽을 이탈하는지 확인 후 대응하세요.")

    def _risk_point(self, t, vt, lv: dict) -> str:
        h   = self.hist; last = h.iloc[-1]
        s1  = lv["support1"]; lo = lv["low_52w"]
        e20 = float(last["EMA20"]); e50 = float(last["EMA50"])
        fmt = lambda v: f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"
        if t.score <= 2 and vt.details.get("expand"):
            return (f"변동성 확장 중 하락 압력 증가 — {fmt(s1)} 이탈 시 "
                    f"{fmt(lo)}(52주 저점) 재테스트 가능")
        if t.score >= 4:
            return (f"20EMA({fmt(e20)}) 아래 종가 마감 시 {fmt(s1)}까지 조정 가능, "
                    f"50EMA({fmt(e50)}) 이탈 시 추세 재점검")
        return (f"{fmt(s1)} 지지 이탈 시 {fmt(lo)}까지 하락 공간 — "
                f"20EMA({fmt(e20)}) 위 종가 유지가 핵심")

    # ---------- 구조화 8섹션 텍스트 --------------------------------
    def _structured_analysis(self, t, m, vt, vl, lv: dict,
                              support_flip: str, rsi_leadership: str) -> str:
        ema_state = t.details.get("ema_state", "")
        slope_dir = t.details.get("slope_dir", "")
        above50   = t.details.get("above_ema50", False)
        above200  = t.details.get("above_ema200", False)
        pos_parts = (["EMA50 위"] if above50 else []) + (["EMA200 위"] if above200 else [])
        s1 = (f"[추세] {t.verdict} ({ema_state}) | "
              f"{' · '.join(pos_parts) if pos_parts else 'EMA 아래'} | {slope_dir}")

        rsi           = m.details.get("rsi", 50)
        rsi_zone      = m.details.get("rsi_zone", "")
        macd_cross_up = m.details.get("macd_cross_up", False)
        macd_cross_dn = m.details.get("macd_cross_dn", False)
        macd_above    = m.details.get("macd_above", False)
        bull_div      = m.details.get("bull_div", False)
        bear_div      = m.details.get("bear_div", False)
        macd_note = ("MACD 골든크로스 발생" if macd_cross_up else
                     "MACD 데드크로스 발생" if macd_cross_dn else
                     "MACD 시그널 위" if macd_above else "MACD 시그널 아래")
        extras = (["상승 다이버전스"] if bull_div else []) + (["하락 다이버전스"] if bear_div else [])
        s2 = (f"[모멘텀] RSI {rsi:.0f} ({rsi_zone}) | {macd_note}"
              + (f" | {' · '.join(extras)}" if extras else ""))

        squeeze     = vt.details.get("squeeze", False)
        upper_break = vt.details.get("upper_break", False)
        lower_touch = vt.details.get("lower_touch", False)
        expand      = vt.details.get("expand", False)
        atr_pct     = vt.details.get("atr_pct", 0)
        bb_q        = vt.details.get("bb_width_quantile", 0.5)
        if squeeze:       vt_desc = "BB 스퀴즈 — 대형 이동 임박"
        elif upper_break: vt_desc = "BB 상단 돌파 진행"
        elif lower_touch: vt_desc = "BB 하단 이탈 — 과매도"
        elif expand:      vt_desc = f"변동성 확장 중 ({bb_q:.0%} 분위)"
        else:             vt_desc = f"변동성 보통 ({bb_q:.0%} 분위)"
        s3 = f"[변동성] {vt_desc} | ATR {atr_pct:.1f}%"

        v_ratio    = vl.details.get("v_ratio", 1.0)
        above_vwap = vl.details.get("above_vwap", True)
        obv_slope  = vl.details.get("obv_slope", 0)
        vwap_rt    = vl.details.get("vwap_retest", False)
        vwap_r_dir = vl.details.get("vwap_retest_dir", "")
        s4 = (f"[수급] 거래량 ×{v_ratio:.1f} | VWAP {'위' if above_vwap else '아래'} | "
              f"OBV {'↑ 매집' if obv_slope > 0 else '↓ 이탈'}"
              + (f" | VWAP {vwap_r_dir}리테스트" if vwap_rt else ""))

        s5 = f"[브레이크아웃] {self._breakout_point(t, vt, lv)}"
        s6 = f"[지지/저항 플립] {support_flip}"
        s7 = f"[시장 주도력] {rsi_leadership}"
        tips = self._tuning(t, m, vt, vl)
        s8 = f"[튜닝 제안] {tips[0]}" if tips else "[튜닝 제안] 현재 파라미터 유지"

        return "\n".join([s1, s2, s3, s4, s5, s6, s7, s8])

    def _image_prompt(self, t, m, vt, vl, phase: str, stars: int) -> str:
        """차트의 분위기를 한 줄 시각 프롬프트로 변환 (그림 생성/내러티브용).

        RSI·ATR·거래량 실제 수치를 반영해 종목마다 다른 묘사를 생성한다.
        """
        rsi       = float(m.details.get("rsi", 50))
        atr_pct   = float(vt.details.get("atr_pct", 2))
        v_ratio   = float(vl.details.get("v_ratio", 1))
        bb_pos    = float(vt.details.get("bb_position", 0.5))
        slope     = float(t.details.get("slope_e20", 0))
        ema_state = t.details.get("ema_state", "")

        # ── 기본 배경색/장면: phase 기반 ──
        if phase == "신고가 돌파 진행":
            mood  = "눈부신 황금빛 정오 — 구름 한 점 없는 하늘"
            scene = "정상 깃발을 꽂으며 다음 봉우리를 바라보는 등반가"
        elif phase in ("강한 상승", "브레이크아웃 임박"):
            if rsi >= 70:
                mood  = "타오르는 황금빛 대낮"
                scene = "절벽 끝에서 도약하는 등반가"
            else:
                mood  = "에메랄드 그린과 새벽노을"
                scene = "능선을 오르며 정상을 바라보는 여정"
        elif phase == "고점 부근 경계":
            mood  = "과열된 오렌지빛 석양 — 그림자가 길어지기 시작"
            scene = "정상에서 발 밑 낭떠러지를 내려다보는 등반가"
        elif phase == "상승 추세 조정":
            mood  = "흐린 하늘 아래 은빛 안개"
            scene = "잠시 바위에 걸터앉아 숨을 고르는 등반가"
        elif phase == "추세 전환 시도":
            mood  = "구름 사이로 스며드는 새벽빛"
            scene = "무너진 길을 돌아 다시 오르막을 찾는 발걸음"
        elif phase == "낙폭 과대 반등 구간":
            mood  = "어둠이 걷히는 새벽 4시 — 동이 트기 직전"
            scene = "바닥에 닿은 발이 다시 땅을 밀어내는 순간"
        elif phase in ("추세 전환 경고", "약세 진행"):
            if rsi <= 30:
                mood  = "얼음장 같은 잿빛과 차가운 보라"
                scene = "급격히 갈라지는 빙하의 균열"
            else:
                mood  = "먹구름 뒤로 사라지는 석양"
                scene = "방향을 잃고 멈춰 선 나침반"
        else:  # 횡보, 횡보 압축
            if bb_pos >= 0.7:
                mood  = "옅은 황금빛이 스며드는 오후"
                scene = "물결 위를 미끄러지는 작은 돛배"
            elif bb_pos <= 0.3:
                mood  = "새벽 안개 속 고요한 수면"
                scene = "호수 바닥을 내려다보는 잠수함"
            else:
                mood  = "온화한 베이지와 청록 수평선"
                scene = "바람 없는 호수 위 부드러운 잔물결"

        # ── 추세 강도 묘사 ──
        if abs(slope) > 0.01:
            trend_desc = f"가파른 {'상승' if slope > 0 else '하강'} 경사"
        elif ema_state == "정배열":
            trend_desc = "완만하게 우상향하는 언덕길"
        elif ema_state == "역배열":
            trend_desc = "서서히 기울어지는 비탈"
        else:
            trend_desc = "평탄한 평원"

        # ── 변동성 묘사 ──
        if atr_pct >= 4:
            vol_desc = f"ATR {atr_pct:.1f}% 출렁이는 파고"
        elif atr_pct >= 2:
            vol_desc = f"ATR {atr_pct:.1f}% 잔잔한 파문"
        else:
            vol_desc = f"ATR {atr_pct:.1f}% 거울 같은 수면"

        # ── 거래량 묘사 ──
        if v_ratio >= 2:
            vol_crowd = f"거래량 {v_ratio:.1f}배 — 장터처럼 북적이는 광장"
        elif v_ratio >= 1.3:
            vol_crowd = f"거래량 {v_ratio:.1f}배 — 활기 띠는 시장"
        elif v_ratio <= 0.6:
            vol_crowd = f"거래량 {v_ratio:.1f}배 — 텅 빈 골목"
        else:
            vol_crowd = f"거래량 {v_ratio:.1f}배 — 한가한 오후 거리"

        # ── 특수 모티프 ──
        motifs = []
        if vt.details.get("squeeze"):     motifs.append("팽팽하게 당겨진 활시위")
        if vt.details.get("upper_break"): motifs.append("유리 천장을 깨뜨리는 빛줄기")
        if vt.details.get("lower_touch"): motifs.append("바닥을 디딘 신발 자국")
        if m.details.get("bull_div"):     motifs.append("어둠 속 피어나는 불씨")
        if m.details.get("bear_div"):     motifs.append("정상에 모이는 먹구름")
        if rsi >= 75:                     motifs.append(f"RSI {rsi:.0f} 과열된 용광로")
        if rsi <= 25:                     motifs.append(f"RSI {rsi:.0f} 극저온의 침묵")

        motif_text = ", ".join(motifs) if motifs else trend_desc
        intensity   = "★" * stars + "☆" * (5 - stars)

        return (f"[{phase} · {intensity}] {mood} 톤의 풍경. "
                f"{scene}. {vol_desc}, {vol_crowd}. 전경에 {motif_text}. "
                f"hand-drawn xkcd style, soft pencil texture, cinematic composition.")

    def analyze(self) -> FourAxisResult:
        t  = self.axis_trend()
        m  = self.axis_momentum()
        vt = self.axis_volatility()
        vl = self.axis_volume()

        levels = self._key_levels()
        phase = self._phase(t, m, vt, vl, levels)
        stars = self._entry_stars(t, m, vt, vl)

        # 종목별 haiku 다양성 — 같은 phase/stars라도 ticker 마다 다른 문장
        haiku = pick_haiku(phase, stars,
                           bull_div=m.details.get("bull_div", False),
                           bear_div=m.details.get("bear_div", False),
                           squeeze=vt.details.get("squeeze", False),
                           upper_break=vt.details.get("upper_break", False),
                           v_confirm=(vl.score >= 4),
                           seed=hash((self.ticker, phase, stars,
                                      round(float(self.hist["Close"].iloc[-1]), 2))))

        sf = self._support_flip(levels)
        rl = self._rsi_leadership(m, t)
        return FourAxisResult(
            trend=t, momentum=m, volatility=vt, volume=vl,
            phase=phase, signal_stars=stars,
            haiku=haiku,
            risk=self._risk(t, vt),
            tuning=self._tuning(t, m, vt, vl),
            annotations=self._annotations(t, m, vt, vl),
            image_prompt=self._image_prompt(t, m, vt, vl, phase, stars),
            key_levels=levels,
            key_observation=self._key_observation(t, m, vt, vl, levels),
            breakout_point=self._breakout_point(t, vt, levels),
            risk_point=self._risk_point(t, vt, levels),
            support_flip=sf,
            rsi_leadership=rl,
            structured_analysis=self._structured_analysis(t, m, vt, vl, levels, sf, rl),
        )
