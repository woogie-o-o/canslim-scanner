#!/usr/bin/env python3
"""Midcap alpha signal engine: 4-axis scoring + composite MidcapAlphaScore."""
import sys
import json
import argparse
import math
from pathlib import Path
from datetime import datetime

# Signal weights (default)
W_PROMOTION = 0.30
W_INSTITUTIONAL = 0.30
W_INSIDER = 0.20
W_GROWTH = 0.20

# Regime circuit breaker
VIX_DAMPENING_THRESHOLD = 25.0
VIX_DAMPENING_FACTOR = 0.50

# Concentration limit
MAX_PER_SECTOR = 3

# S&P 500 promotion thresholds (2026 estimates)
SP500_MIN_MCAP_B = 18.0  # $18B minimum market cap
SP500_MIN_VOLUME_RATIO = 0.5  # vs SP500 median daily volume
CONSECUTIVE_PROFIT_QUARTERS = 4


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _fetch_vix() -> float:
    """Fetch current VIX level via yfinance."""
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX").fast_info
        return getattr(vix, "last_price", 20.0) or 20.0
    except Exception:
        return 20.0  # default to normal


def _fetch_price_data(ticker: str) -> dict:
    """Fetch price/volume data for signal computation."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        hist = tk.history(period="1y")
        if hist.empty:
            return {"status": "no_data"}

        info = tk.info or {}
        close = hist["Close"]
        volume = hist["Volume"]

        # Volume acceleration: 20d avg / 60d avg
        vol_20 = volume.tail(20).mean()
        vol_60 = volume.tail(60).mean()
        vol_accel = vol_20 / max(1, vol_60)

        # Price compression: Bollinger Band width (20d)
        rolling_mean = close.rolling(20).mean()
        rolling_std = close.rolling(20).std()
        bb_width = (rolling_std / rolling_mean).iloc[-1] if len(close) >= 20 else 0.05
        bb_width_6m_pctl = 0.5  # simplified; ideally compute percentile of bb_width over 6M

        # ATR ratio: 20d ATR / 60d ATR
        high = hist["High"]
        low = hist["Low"]
        tr = (high - low).abs()
        atr_20 = tr.tail(20).mean()
        atr_60 = tr.tail(60).mean()
        atr_ratio = atr_20 / max(0.01, atr_60)

        # 12-month return for RS-like score
        if len(close) >= 252:
            ret_12m = (close.iloc[-1] / close.iloc[0]) - 1
        else:
            ret_12m = (close.iloc[-1] / close.iloc[0]) - 1

        return {
            "status": "ok",
            "marketCap": info.get("marketCap", 0),
            "marketCapB": round(info.get("marketCap", 0) / 1e9, 2),
            "sector": info.get("sector", "Unknown"),
            "trailingEps": info.get("trailingEps", None),
            "earningsQuarterlyGrowth": info.get("earningsQuarterlyGrowth", None),
            "revenueGrowth": info.get("revenueGrowth", None),
            "averageVolume": info.get("averageDailyVolume10Day", 0),
            "vol_accel": round(vol_accel, 3),
            "bb_width": round(bb_width, 4),
            "atr_ratio": round(atr_ratio, 3),
            "ret_12m": round(ret_12m, 4),
        }
    except Exception as e:
        print(f"WARNING: price data failed for {ticker}: {e}", file=sys.stderr)
        return {"status": "error"}


def score_promotion(price_data: dict, sec_data: dict) -> dict:
    """S&P500 Promotion Readiness score (0~100)."""
    if price_data.get("status") != "ok":
        return {"score": 0, "detail": "데이터 없음", "basis": "proxy"}

    mcap_b = price_data.get("marketCapB", 0)
    trailing_eps = price_data.get("trailingEps")
    avg_vol = price_data.get("averageVolume", 0)

    # Market cap proximity (0~40): how close to SP500 minimum
    if mcap_b >= SP500_MIN_MCAP_B:
        mcap_score = 40
    elif mcap_b >= SP500_MIN_MCAP_B * 0.8:
        mcap_score = 30 + 10 * (mcap_b - SP500_MIN_MCAP_B * 0.8) / (SP500_MIN_MCAP_B * 0.2)
    elif mcap_b >= SP500_MIN_MCAP_B * 0.5:
        mcap_score = 10 + 20 * (mcap_b - SP500_MIN_MCAP_B * 0.5) / (SP500_MIN_MCAP_B * 0.3)
    else:
        mcap_score = 10 * mcap_b / (SP500_MIN_MCAP_B * 0.5)

    # Profitability (0~30): trailing EPS positive = basic requirement
    if trailing_eps is not None and trailing_eps > 0:
        eps_growth = price_data.get("earningsQuarterlyGrowth")
        if eps_growth is not None and eps_growth > 0:
            profit_score = 30
        else:
            profit_score = 20
    elif trailing_eps is None:
        profit_score = 10  # data insufficient, not penalized as loss
    else:
        profit_score = 0

    # Liquidity (0~15)
    liq_score = min(15, avg_vol / 1e6 * 3)  # ~$5M daily = 15 points

    # RS Momentum (0~15)
    ret = price_data.get("ret_12m", 0)
    if ret > 0.5:
        rs_score = 15
    elif ret > 0.3:
        rs_score = 12
    elif ret > 0.15:
        rs_score = 8
    elif ret > 0:
        rs_score = 5
    else:
        rs_score = 0

    total = _clamp(mcap_score + profit_score + liq_score + rs_score)

    detail_parts = []
    if mcap_b >= SP500_MIN_MCAP_B:
        detail_parts.append(f"시총 ${mcap_b:.1f}B (기준 초과)")
    else:
        detail_parts.append(f"시총 ${mcap_b:.1f}B (기준 ${SP500_MIN_MCAP_B}B의 {mcap_b/SP500_MIN_MCAP_B*100:.0f}%)")
    if trailing_eps is not None and trailing_eps > 0:
        detail_parts.append("EPS 흑자")
    elif trailing_eps is None:
        detail_parts.append("EPS 데이터 부족")
    else:
        detail_parts.append("EPS 적자")

    return {
        "score": round(total),
        "detail": " / ".join(detail_parts),
        "components": {"mcap": round(mcap_score), "profit": round(profit_score), "liquidity": round(liq_score, 1), "rs": round(rs_score)},
        "basis": "proxy",
    }


def score_institutional(price_data: dict, sec_data: dict) -> dict:
    """Institutional Accumulation score (0~100) from SEC 13F + volume patterns."""
    sec_13f = sec_data.get("13f", {})
    if price_data.get("status") != "ok":
        return {"score": 0, "detail": "데이터 없음", "basis": "proxy"}

    # 13F institutional momentum (0~30)
    if sec_13f.get("status") == "ok":
        momentum = sec_13f.get("inst_momentum", 0)
        recent = sec_13f.get("recent_6m_filings", 0)
        inst_score = _clamp(momentum * 10 + recent * 3, 0, 30)
    else:
        inst_score = 0

    # Volume acceleration (0~30): 20d/60d volume ratio
    vol_accel = price_data.get("vol_accel", 1.0)
    atr_ratio = price_data.get("atr_ratio", 1.0)
    # "Volume up but price calm" = accumulation pattern
    if vol_accel > 1.3 and atr_ratio < 1.1:
        vol_score = 30
    elif vol_accel > 1.15:
        vol_score = 20
    elif vol_accel > 1.0:
        vol_score = 10
    else:
        vol_score = 0

    # Price compression / squeeze (0~20): narrow BB width
    bb_width = price_data.get("bb_width", 0.05)
    if bb_width < 0.02:
        squeeze_score = 20
    elif bb_width < 0.03:
        squeeze_score = 15
    elif bb_width < 0.04:
        squeeze_score = 10
    else:
        squeeze_score = 0

    # Insider confirmation from Form 4 (0~20)
    form4 = sec_data.get("form4", {})
    if form4.get("status") == "ok":
        activity_ratio = form4.get("insider_activity_ratio", 1.0)
        insider_score = _clamp((activity_ratio - 1.0) * 40, 0, 20)
    else:
        insider_score = 0

    total = _clamp(inst_score + vol_score + squeeze_score + insider_score)

    detail_parts = []
    if inst_score > 15:
        detail_parts.append(f"기관 관심 증가 (6M filing {sec_13f.get('recent_6m_filings', 0)}건)")
    if vol_score > 15:
        detail_parts.append(f"거래량 가속 (x{vol_accel:.2f})")
    if squeeze_score > 10:
        detail_parts.append(f"가격 압축 (BB {bb_width:.3f})")

    return {
        "score": round(total),
        "detail": " / ".join(detail_parts) if detail_parts else "특이 패턴 미감지",
        "components": {"inst_13f": round(inst_score), "volume": round(vol_score), "squeeze": round(squeeze_score), "insider_conf": round(insider_score)},
        "basis": "proxy",
    }


def score_insider(price_data: dict, sec_data: dict) -> dict:
    """Insider Net Purchase score (0~100) from SEC Form 4."""
    form4 = sec_data.get("form4", {})
    if form4.get("status") != "ok":
        return {"score": 0, "detail": "Form 4 데이터 없음", "basis": "proxy"}

    count_90d = form4.get("form4_90d_count", 0)
    activity_ratio = form4.get("insider_activity_ratio", 1.0)

    # Filing frequency score (0~50)
    freq_score = _clamp(count_90d * 10, 0, 50)

    # Activity acceleration (0~50)
    accel_score = _clamp((activity_ratio - 0.5) * 50, 0, 50)

    total = _clamp(freq_score + accel_score)

    detail = f"90일 내 Form 4 {count_90d}건, 활동 비율 x{activity_ratio:.1f}"
    if activity_ratio > 2.0:
        detail += " (내부자 활동 급증)"

    return {
        "score": round(total),
        "detail": detail,
        "components": {"frequency": round(freq_score), "acceleration": round(accel_score)},
        "basis": "proxy",
    }


def score_growth(price_data: dict, sec_data: dict) -> dict:
    """Growth Momentum score (0~100) from RS + revenue acceleration."""
    if price_data.get("status") != "ok":
        return {"score": 0, "detail": "데이터 없음", "basis": "proxy"}

    # RS momentum (0~50): 12M return based
    ret = price_data.get("ret_12m", 0)
    if ret > 0.6:
        rs_score = 50
    elif ret > 0.4:
        rs_score = 40
    elif ret > 0.2:
        rs_score = 30
    elif ret > 0.1:
        rs_score = 20
    elif ret > 0:
        rs_score = 10
    else:
        rs_score = 0

    # Revenue growth (0~30)
    rev_growth = price_data.get("revenueGrowth")
    if rev_growth is not None:
        if rev_growth > 0.3:
            rev_score = 30
        elif rev_growth > 0.15:
            rev_score = 20
        elif rev_growth > 0.05:
            rev_score = 10
        else:
            rev_score = 0
    else:
        rev_score = 10  # data insufficient, neutral

    # EPS acceleration (0~20)
    eps_growth = price_data.get("earningsQuarterlyGrowth")
    if eps_growth is not None:
        if eps_growth > 0.25:
            eps_score = 20
        elif eps_growth > 0.1:
            eps_score = 15
        elif eps_growth > 0:
            eps_score = 10
        else:
            eps_score = 0
    else:
        eps_score = 5  # data insufficient, neutral

    total = _clamp(rs_score + rev_score + eps_score)

    detail_parts = []
    if ret > 0.2:
        detail_parts.append(f"12M 수익률 +{ret*100:.0f}%")
    if rev_growth is not None and rev_growth > 0.1:
        detail_parts.append(f"매출 성장 +{rev_growth*100:.0f}%")
    if eps_growth is not None and eps_growth > 0.1:
        detail_parts.append(f"EPS 성장 +{eps_growth*100:.0f}%")

    return {
        "score": round(total),
        "detail": " / ".join(detail_parts) if detail_parts else "성장 지표 보통",
        "components": {"rs_momentum": round(rs_score), "revenue": round(rev_score), "eps": round(eps_score)},
        "basis": "proxy",
    }


def compute_orthogonalized_weights(signals: dict) -> dict:
    """Adjust weights if signals are too correlated (correlation > 0.7)."""
    # Simplified: use score similarity as a proxy for correlation
    scores = [signals["promotion"]["score"], signals["institutional"]["score"],
              signals["insider"]["score"], signals["growth"]["score"]]
    weights = {"promotion": W_PROMOTION, "institutional": W_INSTITUTIONAL,
               "insider": W_INSIDER, "growth": W_GROWTH}

    # Check pairwise similarity (|diff| < 15 points = potentially correlated)
    pairs = [("promotion", "institutional"), ("promotion", "growth"),
             ("institutional", "insider"), ("insider", "growth")]
    score_map = {"promotion": scores[0], "institutional": scores[1],
                 "insider": scores[2], "growth": scores[3]}

    for a, b in pairs:
        if abs(score_map[a] - score_map[b]) < 10 and score_map[a] > 50:
            # Signals moving together at high levels — dampen the lighter-weighted one
            if weights[a] < weights[b]:
                weights[a] *= 0.7
            else:
                weights[b] *= 0.7

    # Renormalize
    total_w = sum(weights.values())
    return {k: round(v / total_w, 3) for k, v in weights.items()}


def compute_composite(signals: dict, vix: float) -> dict:
    """Compute MidcapAlphaScore with regime dampening and orthogonalization."""
    weights = compute_orthogonalized_weights(signals)

    raw_score = (
        signals["promotion"]["score"] * weights["promotion"] +
        signals["institutional"]["score"] * weights["institutional"] +
        signals["insider"]["score"] * weights["insider"] +
        signals["growth"]["score"] * weights["growth"]
    )

    # Regime circuit breaker
    dampened = False
    if vix > VIX_DAMPENING_THRESHOLD:
        raw_score *= VIX_DAMPENING_FACTOR
        dampened = True

    return {
        "MidcapAlphaScore": round(_clamp(raw_score)),
        "weights_used": weights,
        "vix": round(vix, 1),
        "regime_dampened": dampened,
    }


def label_signals(signals: dict) -> str:
    """Generate Korean label for the dominant signal."""
    labels = []
    if signals["promotion"]["score"] > 70:
        labels.append("승격 임박")
    if signals["institutional"]["score"] > 60:
        labels.append("매집 초기")
    if signals["insider"]["score"] > 65:
        labels.append("내부자 확신")
    if signals["growth"]["score"] > 70:
        labels.append("성장 가속")
    return " + ".join(labels) if labels else "모니터링"


def main():
    parser = argparse.ArgumentParser(description="Compute midcap alpha signals")
    parser.add_argument("--universe", required=True, help="Path to midcap_universe.json")
    parser.add_argument("--sec-data", required=True, help="Path to sec_data.json")
    parser.add_argument("--output", default="midcap_scores.json", help="Output path")
    args = parser.parse_args()

    with open(args.universe, encoding="utf-8") as f:
        universe = json.load(f)
    with open(args.sec_data, encoding="utf-8") as f:
        sec_all = json.load(f)

    tickers = universe.get("tickers", [])
    sec_data_map = sec_all.get("data", {})

    print(f"Computing signals for {len(tickers)} tickers...", file=sys.stderr)

    vix = _fetch_vix()
    print(f"Current VIX: {vix:.1f}" + (" (DAMPENING ACTIVE)" if vix > VIX_DAMPENING_THRESHOLD else ""), file=sys.stderr)

    results = []
    for i, tk_info in enumerate(tickers):
        ticker = tk_info["ticker"]
        print(f"  [{i+1}/{len(tickers)}] {ticker}...", file=sys.stderr)

        price_data = _fetch_price_data(ticker)
        sec_data = sec_data_map.get(ticker, {"13f": {}, "form4": {}})

        signals = {
            "promotion": score_promotion(price_data, sec_data),
            "institutional": score_institutional(price_data, sec_data),
            "insider": score_insider(price_data, sec_data),
            "growth": score_growth(price_data, sec_data),
        }

        composite = compute_composite(signals, vix)
        label = label_signals(signals)

        results.append({
            "ticker": ticker,
            "shortName": tk_info.get("shortName", ticker),
            "sector": tk_info.get("sector", price_data.get("sector", "Unknown")),
            "marketCapB": tk_info.get("marketCapB", price_data.get("marketCapB", 0)),
            "MidcapAlphaScore": composite["MidcapAlphaScore"],
            "label": label,
            "signals": signals,
            "composite": composite,
            "basis": "proxy",
        })

    # Sort by score descending
    results.sort(key=lambda x: x["MidcapAlphaScore"], reverse=True)

    # Apply sector concentration limit
    sector_counts = {}
    filtered = []
    for r in results:
        sector = r["sector"]
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if sector_counts[sector] <= MAX_PER_SECTOR:
            filtered.append(r)

    output = {
        "generated": datetime.now().isoformat(),
        "vix": vix,
        "regime_dampened": vix > VIX_DAMPENING_THRESHOLD,
        "total_scored": len(results),
        "after_concentration_filter": len(filtered),
        "disclaimer": "이 분석은 SEC EDGAR 공시 및 yfinance 무료 데이터 기반의 참고 지표입니다. "
                       "S&P 지수위원회의 편입 결정에는 정량 기준 외 정성적 재량이 포함되며, "
                       "기관 매집 패턴은 프록시 추정입니다. 투자 판단의 근거로 단독 사용하지 마세요.",
        "results": filtered,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary table
    print("\n" + "=" * 80, file=sys.stderr)
    print(f"{'Ticker':<8} {'Sector':<20} {'Score':>5} {'Label':<20} {'Top Signal'}", file=sys.stderr)
    print("-" * 80, file=sys.stderr)
    for r in filtered[:15]:
        top_signal = max(r["signals"].items(), key=lambda x: x[1]["score"])
        print(f"{r['ticker']:<8} {r['sector']:<20} {r['MidcapAlphaScore']:>5} {r['label']:<20} {top_signal[0]}({top_signal[1]['score']})", file=sys.stderr)
    print("=" * 80, file=sys.stderr)

    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
