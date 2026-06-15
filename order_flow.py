# -*- coding: utf-8 -*-
"""
모듈2 — 일봉 OFI / 스마트머니 프록시 (REGIME_SPEC.md §3)

일봉 OHLCV만으로 매수/매도 압력 불균형(OFI), 스마트머니 체결강도,
은밀 매집 변곡점(accumulation), VWAP 대비 종가 압력을 근사한다.

원칙: numpy/pandas only · 절대 예외를 던지지 않음(불량 입력 → 중립 기본값) ·
      four_axis_analyzer 의 보조지표 스타일(_vwap_rolling/_obv 등)을 그대로 답습.

공개 API:
    compute_ofi(ohlcv: pd.DataFrame, *, window: int = 20) -> dict
    ofi_from_row(row: dict) -> dict
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional


# ───────── 중립 기본값 ────────────────────────────────────────────
def _neutral(reasons: Optional[List[str]] = None) -> Dict[str, Any]:
    """입력이 불량하거나 부족할 때 반환하는 중립 결과."""
    return {
        "ofi": 0.0,
        "smart_money": 0.5,
        "accumulation": False,
        "vwap_pressure": 0.0,
        "reasons": list(reasons or []),
    }


# ───────── 보조 지표 (four_axis 스타일 재사용) ────────────────────
def _clv(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """종가위치(close-location-value) ∈ [-1, 1]. H==L 가드 → 0."""
    rng = (high - low)
    clv = ((close - low) - (high - close)) / rng.replace(0, np.nan)
    return clv.fillna(0.0).clip(-1.0, 1.0)


def _vwap_rolling(high, low, close, volume, n=20):
    """four_axis_analyzer._vwap_rolling 과 동일."""
    tp = (high + low + close) / 3
    pv = tp * volume
    return pv.rolling(n).sum() / volume.rolling(n).sum()


def _obv(close, volume):
    """four_axis_analyzer._obv 과 동일."""
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _bb_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    """볼린저 밴드 폭(four_axis._bb 의 width 와 동일 정의)."""
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return (upper - lower) / mid.replace(0, np.nan)


def _ols_slope(x) -> float:
    """유한값만으로 1차 회귀 기울기. (four_axis._ols_slope 와 동일 안전로직)"""
    arr = np.asarray(x, dtype=float)
    mask = np.isfinite(arr)
    if mask.sum() < 2:
        return float("nan")
    t = np.arange(arr.size, dtype=float)[mask]
    y = arr[mask]
    t -= t.mean()
    denom = float((t * t).sum())
    if denom <= 0.0:
        return float("nan")
    return float((t * (y - y.mean())).sum() / denom)


def _squash(z: float) -> float:
    """z-score → (0,1) 로지스틱 압착."""
    if not np.isfinite(z):
        return 0.5
    return float(1.0 / (1.0 + np.exp(-z)))


# ───────── 풀버전: DataFrame OFI ──────────────────────────────────
def compute_ofi(ohlcv: pd.DataFrame, *, window: int = 20) -> Dict[str, Any]:
    """일봉 OHLCV 로부터 OFI/스마트머니/매집/ VWAP 압력을 산출한다.

    반환 dict 키: ofi(-1..1), smart_money(0..1), accumulation(bool),
                  vwap_pressure(-1..1), reasons(list[str]).
    불량/부족 입력 시 절대 예외를 던지지 않고 중립 기본값을 반환한다.
    """
    try:
        if ohlcv is None or not isinstance(ohlcv, pd.DataFrame) or ohlcv.empty:
            return _neutral()

        win = int(window) if (window and int(window) > 0) else 20

        # 컬럼 대소문자 무관 매핑
        cols = {str(c).lower(): c for c in ohlcv.columns}
        need = ("high", "low", "close", "volume")
        if not all(k in cols for k in need):
            return _neutral()

        high = pd.to_numeric(ohlcv[cols["high"]], errors="coerce")
        low = pd.to_numeric(ohlcv[cols["low"]], errors="coerce")
        close = pd.to_numeric(ohlcv[cols["close"]], errors="coerce")
        volume = pd.to_numeric(ohlcv[cols["volume"]], errors="coerce").fillna(0.0)
        volume = volume.clip(lower=0.0)

        n = len(close)
        if n < 2 or close.notna().sum() < 2:
            return _neutral()

        reasons: List[str] = []

        # 1) CLV + 거래량가중 압력(OFI) ------------------------------------
        clv = _clv(high, low, close)
        mfv = clv * volume
        eff = min(win, n)  # 데이터가 window 미만이면 가용 구간으로 축소
        vol_sum = volume.rolling(eff, min_periods=1).sum()
        mfv_sum = mfv.rolling(eff, min_periods=1).sum()
        ofi_series = mfv_sum / vol_sum.replace(0, np.nan)
        ofi_val = ofi_series.iloc[-1]
        ofi = float(np.clip(ofi_val, -1.0, 1.0)) if np.isfinite(ofi_val) else 0.0

        # 2) VWAP 압력 ----------------------------------------------------
        vwap = _vwap_rolling(high, low, close, volume, n=eff)
        c_last = close.iloc[-1]
        v_last = vwap.iloc[-1]
        if np.isfinite(c_last) and np.isfinite(v_last) and v_last != 0:
            # (C - vwap)/vwap 를 약 ±5% 풀스케일로 [-1,1] 매핑
            rel = (c_last - v_last) / abs(v_last)
            vwap_pressure = float(np.clip(rel / 0.05, -1.0, 1.0))
        else:
            vwap_pressure = 0.0

        # 3) 스마트머니: 강한 종가(clv>0.5) 의 거래량비중 z-score → 0..1 -----
        strong = (clv > 0.5).astype(float)
        seg_vol = volume.iloc[-eff:]
        seg_strong = strong.iloc[-eff:]
        tot_v = float(seg_vol.sum())
        if tot_v > 0:
            strong_frac = float((seg_strong * seg_vol).sum() / tot_v)  # 0..1
        else:
            strong_frac = 0.0
        # 무작위(기대 0.5) 대비 편차를 z 근사 후 squash. 표본수로 스케일.
        z = (strong_frac - 0.5) * np.sqrt(max(eff, 1)) * 2.0
        smart_money = float(np.clip(_squash(z), 0.0, 1.0))

        # 4) 은밀 매집 변곡점 --------------------------------------------
        accumulation = False
        # range_bound: 최근 window 고저 폭 ≤ ~8% AND BBwidth 하위 40% 백분위
        seg_hi = high.iloc[-eff:]
        seg_lo = low.iloc[-eff:]
        hi_max = float(seg_hi.max())
        lo_min = float(seg_lo.min())
        if np.isfinite(hi_max) and np.isfinite(lo_min) and lo_min > 0:
            hl_range = (hi_max - lo_min) / lo_min
        else:
            hl_range = np.inf

        bbw = _bb_width(close, n=min(20, max(2, eff)))
        bbw_last = bbw.iloc[-1]
        bbw_hist = bbw.dropna()
        if len(bbw_hist) >= 3 and np.isfinite(bbw_last):
            pctl = float((bbw_hist <= bbw_last).mean())  # 자체 이력 내 백분위
            bb_low = pctl <= 0.40
        else:
            # 이력이 짧으면 BB 조건은 통과시키되(보수적이지 않게) range 로만 판단
            bb_low = True

        range_bound = (hl_range <= 0.08) and bb_low

        # stealth: OBV 기울기 > 0 AND ofi > 0.15
        obv = _obv(close, volume)
        obv_slope = _ols_slope(obv.iloc[-eff:].values)
        stealth = (np.isfinite(obv_slope) and obv_slope > 0) and (ofi > 0.15)

        accumulation = bool(range_bound and stealth)

        # reasons --------------------------------------------------------
        if accumulation:
            reasons.append("횡보 밴드 내 은밀 매집 — OBV↑ & OFI+")
        if ofi >= 0.30:
            reasons.append(f"매수 압력 우위 (OFI {ofi:+.2f})")
        elif ofi <= -0.30:
            reasons.append(f"매도 압력 우위 (OFI {ofi:+.2f})")
        if smart_money >= 0.65:
            reasons.append(f"스마트머니 체결강도 강함 ({smart_money:.2f})")
        if vwap_pressure >= 0.50:
            reasons.append("VWAP 상단 종가 마감")
        elif vwap_pressure <= -0.50:
            reasons.append("VWAP 하단 종가 마감")

        return {
            "ofi": ofi,
            "smart_money": smart_money,
            "accumulation": accumulation,
            "vwap_pressure": vwap_pressure,
            "reasons": reasons,
        }
    except Exception:
        # never throw into scan
        return _neutral()


# ───────── 경량: 스캔 row 프록시 ──────────────────────────────────
def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def ofi_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """DataFrame 이 없을 때 스캔 row 의 기존 필드로 OFI 를 경량 근사한다.

    사용 필드(있을 때만): High/Low/Close(또는 _High 등) 로 CLV,
    _VolRatio(거래량 배수), RSI(모멘텀 대용). 핵심 입력이 모두 없으면 중립 기본값.
    """
    try:
        if not isinstance(row, dict):
            return _neutral()

        def pick(*keys):
            for k in keys:
                if k in row:
                    f = _to_float(row[k])
                    if f is not None:
                        return f
            return None

        high = pick("High", "_High", "high")
        low = pick("Low", "_Low", "low")
        close = pick("Close", "_Close", "close", "Price", "_Price")
        vol_ratio = pick("_VolRatio", "VolRatio")
        rsi = pick("RSI", "_RSI")

        # 사용할 만한 입력이 전혀 없으면 중립
        if high is None and low is None and close is None and vol_ratio is None and rsi is None:
            return _neutral()

        reasons: List[str] = []

        # 1) CLV (H/L/C 가 모두 있을 때) ---------------------------------
        clv = None
        if high is not None and low is not None and close is not None and high > low:
            clv = ((close - low) - (high - close)) / (high - low)
            clv = float(np.clip(clv, -1.0, 1.0))

        # 2) ofi 근사: CLV 방향 × 거래량배수 신뢰 -------------------------
        if clv is not None:
            conf = 1.0
            if vol_ratio is not None:
                conf = float(np.clip((vol_ratio - 0.5) / 1.5, 0.2, 1.0))
            ofi = float(np.clip(clv * conf, -1.0, 1.0))
        elif rsi is not None:
            # RSI 50 중심을 압력 프록시로
            ofi = float(np.clip((rsi - 50.0) / 50.0, -1.0, 1.0))
        else:
            ofi = 0.0

        # 3) smart_money: CLV 강세 + 거래량배수 + RSI 합성 → 0..1 --------
        if clv is None and vol_ratio is None and rsi is None:
            smart_money = 0.5
        else:
            parts, wts = [], []
            if clv is not None:
                parts.append((clv + 1.0) / 2.0); wts.append(0.5)
            if vol_ratio is not None:
                parts.append(float(np.clip((vol_ratio - 0.8) / 1.2, 0.0, 1.0))); wts.append(0.3)
            if rsi is not None:
                parts.append(float(np.clip(rsi / 100.0, 0.0, 1.0))); wts.append(0.2)
            wsum = sum(wts)
            smart_money = float(np.clip(sum(p * w for p, w in zip(parts, wts)) / wsum, 0.0, 1.0)) if wsum > 0 else 0.5

        # 4) vwap_pressure 경량 대용: CLV 가 있으면 그대로, 없으면 0 ------
        vwap_pressure = float(clv) if clv is not None else 0.0

        if ofi >= 0.30:
            reasons.append(f"row 프록시 매수 압력 (OFI {ofi:+.2f})")
        elif ofi <= -0.30:
            reasons.append(f"row 프록시 매도 압력 (OFI {ofi:+.2f})")

        return {
            "ofi": ofi,
            "smart_money": smart_money,
            "accumulation": False,  # 경량 프록시는 매집 판정 안 함
            "vwap_pressure": vwap_pressure,
            "reasons": reasons,
        }
    except Exception:
        return _neutral()
