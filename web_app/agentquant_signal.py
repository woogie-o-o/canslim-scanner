"""AgentQuant 진입 타이밍 신호 어댑터.

vendor/AgentQuant 의 regime/feature 모듈을 import 해서 우리 스캐너 detail 페이지가
사용할 수 있는 가벼운 JSON 시그널을 만들어 준다.

- yfinance 로 종목 OHLCV + 시장 벤치 OHLCV + VIX 를 받아 features 를 계산
- AgentQuant 의 detect_regime_full() 로 시장 레짐 라벨 산출
- 종목 자체에 대해서도 동일 피처를 계산해 진입 타이밍 verdict 를 생성
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import asdict
from typing import Optional

logger = logging.getLogger(__name__)

# vendor 경로 등록
_VENDOR_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "vendor", "AgentQuant")
)
if _VENDOR_PATH not in sys.path:
    sys.path.insert(0, _VENDOR_PATH)


def _ensure_scipy_stub() -> None:
    """scipy 미설치 환경(특히 win32 파이썬)에서 numpy 기반 stub 주입."""
    try:
        import scipy.stats  # noqa: F401
        return
    except Exception:
        pass
    import types
    import numpy as _np

    def percentileofscore(arr, score, kind="rank"):
        a = _np.asarray(arr, dtype=float)
        a = a[~_np.isnan(a)]
        if a.size == 0:
            return 50.0
        return float((a <= float(score)).mean() * 100.0)

    scipy_mod = types.ModuleType("scipy")
    stats_mod = types.ModuleType("scipy.stats")
    stats_mod.percentileofscore = percentileofscore
    scipy_mod.stats = stats_mod
    sys.modules["scipy"] = scipy_mod
    sys.modules["scipy.stats"] = stats_mod


_ensure_scipy_stub()

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_TTL_SEC = 60 * 30  # 30분


def _resolve_yf_ticker(ticker: str, market: str) -> str:
    raw = (ticker or "").strip().upper()
    if not raw:
        return raw
    if market != "KR":
        return raw
    # KR: 6자리 코드를 .KS 로 (.KQ 폴백은 호출부에서 시도)
    base = raw
    for suf in (".KS", ".KQ"):
        if base.endswith(suf):
            return base
    digits = "".join(c for c in base if c.isdigit())
    if digits:
        return digits.zfill(6) + ".KS"
    return base


def _fetch_ohlcv(yf_ticker: str, period: str = "3y"):
    import yfinance as yf
    import pandas as pd
    try:
        df = yf.Ticker(yf_ticker).history(period=period, auto_adjust=False)
        if df is None or df.empty:
            return None
        # tz 제거 + 날짜 단위 정규화 (인덱스 정합)
        try:
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass
        df.index = pd.to_datetime(df.index).normalize()
        df = df[~df.index.duplicated(keep="last")]
        return df
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", yf_ticker, exc)
        return None


def _verdict_from_signals(sig, latest_row) -> dict:
    """RSI/MACD/BB + 레짐 라벨을 종합해 진입 점수(0~100) 와 권고를 만든다."""
    score = 50.0
    reasons: list[str] = []

    # 1) 추세 (가중치 25)
    if sig.above_200sma:
        score += 12
        reasons.append("200일선 위")
    else:
        score -= 12
        reasons.append("200일선 아래")
    if sig.above_50sma:
        score += 8
    else:
        score -= 8

    # 2) RSI (가중치 15)
    rsi = float(latest_row.get("rsi_14", 50.0) or 50.0)
    if 40 <= rsi <= 60:
        score += 8
        reasons.append(f"RSI 중립({rsi:.0f})")
    elif 30 <= rsi < 40:
        score += 12
        reasons.append(f"RSI 과매도 회복({rsi:.0f})")
    elif rsi < 30:
        score += 6
        reasons.append(f"RSI 과매도({rsi:.0f})")
    elif rsi > 75:
        score -= 12
        reasons.append(f"RSI 과열({rsi:.0f})")
    elif rsi > 65:
        score -= 4

    # 3) MACD (가중치 15)
    macd_hist = float(latest_row.get("macd_hist", 0.0) or 0.0)
    if macd_hist > 0:
        score += 10
        reasons.append("MACD 양전환")
    else:
        score -= 6

    # 4) 볼린저 위치 (가중치 10)
    bb_pct = float(latest_row.get("bb_pct_b", 0.5) or 0.5)
    if bb_pct < 0.2:
        score += 8
        reasons.append("BB 하단(눌림)")
    elif bb_pct > 0.85:
        score -= 8
        reasons.append("BB 상단(과확장)")

    # 5) 레짐 컨텍스트 (가중치 ±15)
    label = sig.regime_label or "Unknown"
    if "Crisis" in label:
        score -= 15
        reasons.append("위기 레짐")
    elif "HighVol-Bear" in label:
        score -= 12
    elif "LowVol-Bull" in label:
        score += 10
        reasons.append("저변동성 강세")
    elif "MidVol-Bull" in label:
        score += 6
    elif "Bear" in label:
        score -= 6

    score = max(0.0, min(100.0, score))

    if score >= 70:
        verdict = "BUY"
        verdict_kr = "매수 적기"
        icon = "🟢"
    elif score >= 55:
        verdict = "ACCUMULATE"
        verdict_kr = "분할 매집"
        icon = "🟡"
    elif score >= 40:
        verdict = "WATCH"
        verdict_kr = "관망"
        icon = "⚪"
    else:
        verdict = "AVOID"
        verdict_kr = "회피"
        icon = "🔴"

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "verdict_kr": verdict_kr,
        "icon": icon,
        "reasons": reasons[:6],
        "rsi": round(rsi, 1),
        "macd_hist": round(macd_hist, 4),
        "bb_pct_b": round(bb_pct, 3),
    }


def get_regime_signal(ticker: str, market: str = "US") -> Optional[dict]:
    """진입 타이밍 + 시장 레짐 시그널 반환. 실패 시 None."""
    key = (market.upper(), ticker.upper())
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1]

    try:
        from src.features.engine import compute_features
        from src.features.regime import detect_regime_full
    except Exception as exc:
        logger.warning("AgentQuant import failed: %s", exc)
        return None

    yf_tk = _resolve_yf_ticker(ticker, market)
    if not yf_tk:
        return None

    # 벤치마크/VIX를 종목과 병렬로 fetch (yfinance I/O 대기 최소화)
    bench_ticker = "^KS11" if market == "KR" else "SPY"
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_stock = pool.submit(_fetch_ohlcv, yf_tk)
        f_bench = pool.submit(_fetch_ohlcv, bench_ticker)
        f_vix   = pool.submit(_fetch_ohlcv, "^VIX")
        stock_df = f_stock.result()
        bench_df = f_bench.result()
        vix_df   = f_vix.result()

    # KR .KS 실패시 .KQ 폴백
    if (stock_df is None or stock_df.empty) and market == "KR" and yf_tk.endswith(".KS"):
        yf_tk = yf_tk[:-3] + ".KQ"
        stock_df = _fetch_ohlcv(yf_tk)
    if stock_df is None or stock_df.empty:
        return None

    payload: dict = {"ticker": ticker, "market": market, "yf_ticker": yf_tk}

    # 종목 자체 피처 + 진입 점수
    try:
        ohlcv = {yf_tk: stock_df, "^VIX": vix_df if vix_df is not None else stock_df.iloc[0:0]}
        feats_stock = compute_features(ohlcv, ref_asset_ticker=yf_tk, vix_ticker="^VIX")
        if not feats_stock.empty:
            sig_stock = detect_regime_full(feats_stock)
            latest = feats_stock.iloc[-1]
            payload["stock"] = _verdict_from_signals(sig_stock, latest)
            payload["stock"]["regime_label"] = sig_stock.regime_label
            payload["stock"]["confidence"] = round(sig_stock.regime_confidence, 3)
            payload["stock"]["momentum_63d"] = round(sig_stock.momentum_63d, 4)
            payload["stock"]["drawdown_52w"] = round(sig_stock.drawdown_from_52w_high, 4)
    except Exception as exc:
        logger.warning("stock feature compute failed (%s): %s", yf_tk, exc)

    # 시장 레짐
    try:
        if bench_df is not None and not bench_df.empty:
            ohlcv_m = {bench_ticker: bench_df, "^VIX": vix_df if vix_df is not None else bench_df.iloc[0:0]}
            feats_m = compute_features(ohlcv_m, ref_asset_ticker=bench_ticker, vix_ticker="^VIX")
            if not feats_m.empty:
                sig_m = detect_regime_full(feats_m)
                payload["market"] = {
                    "label": sig_m.regime_label,
                    "vix_level": round(sig_m.vix_level, 2),
                    "vix_percentile_252d": round(sig_m.vix_percentile_252d, 1),
                    "momentum_63d": round(sig_m.momentum_63d, 4),
                    "confidence": round(sig_m.regime_confidence, 3),
                    "above_200sma": sig_m.above_200sma,
                    "drawdown_52w": round(sig_m.drawdown_from_52w_high, 4),
                }
    except Exception as exc:
        logger.warning("market feature compute failed: %s", exc)

    if "stock" not in payload and "market" not in payload:
        return None

    _cache[key] = (now, payload)
    return payload
