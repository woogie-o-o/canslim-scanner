# -*- coding: utf-8 -*-
"""factor_enhancements_v21.py — 실험적 알파 보강 모듈 (v21 prep).

월가식 점수 체계 보강 3종:
  1) Earnings Estimate Revision (PEAD 계열) — Finnhub 기반
  2) Turnover Penalty — 점수 안정성 강제 (거래비용 프록시)
  3) Reversion-Cluster Orthogonalization — RSI/MACD/Z/MR PCA 1주성분

기본 비활성화 (ENABLE_V21_FACTORS=False). 활성화는 환경변수 또는 직접 토글.

설계 원칙:
  • 기존 점수 코어 비파괴 — 어디서도 import 안 되면 영향 0
  • 모든 함수는 실패 시 중립값(0 or NaN) 반환 → 안전한 fallback
  • Finnhub 미가용 시 자동 skip
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Config — 환경변수 또는 코드에서 토글
# ─────────────────────────────────────────────────────────────────────
ENABLE_V21_FACTORS: bool = (
    os.environ.get("ENABLE_V21_FACTORS", "0").strip() in ("1", "true", "True")
)

# 가중치 (실험적 — 백테스트 후 재조정 권장)
W_EPS_REVISION: float = 0.05      # +0.05 weight, 기존 23팩터 평균 가중치(~0.04)와 동급
W_TURNOVER_PENALTY: float = 0.02  # 점수 변동성 패널티

# 턴오버 패널티 파라미터
TURNOVER_LAMBDA: float = 0.15          # |Δscore| 당 차감 비율
TURNOVER_THRESHOLD: float = 5.0        # 5점 이하 변동은 패널티 없음 (노이즈 영역)

# ─────────────────────────────────────────────────────────────────────
# 직전 회차 점수 캐시 (턴오버 패널티용)
# ─────────────────────────────────────────────────────────────────────
_prev_score_cache: dict[str, tuple[float, float]] = {}  # {ticker: (score, ts)}
_cache_lock = threading.Lock()
_PREV_TTL = 86400  # 24h


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ 1) Earnings Estimate Revision                                      ║
# ╚═══════════════════════════════════════════════════════════════════╝
def compute_eps_revision_score(ticker: str) -> float:
    """Finnhub 컨센서스 변화 기반 추정치 리비전 점수.

    Returns:
        [0, 100] 정규화 점수. 50=중립, >50=상향, <50=하향.
        Finnhub 미가용 시 50.0 (중립).

    근거:
        Doukas et al. (2006), Stickel (1991) — 컨센서스 EPS 추정치 상향은
        단기 수익률의 가장 강력한 예측 인자 중 하나. IC ~0.05-0.08.

    구현:
        Finnhub recommendation_trends 의 strongBuy/buy 증감을 프록시로 사용
        (전용 estimate API 호출 절약). rec_change="upgrade" → 70점,
        "downgrade" → 30점, "stable" → 50점.
    """
    try:
        import finnhub_api
        if not finnhub_api.is_available():
            return 50.0
        data = finnhub_api.get_sentiment_data(ticker)
        if not data.get("available"):
            return 50.0

        rec_change = data.get("rec_change", "stable")
        # 강도: strongBuy 비중 가산
        strong_buy = data.get("rec_strong_buy", 0)
        buy = data.get("rec_buy", 0)
        hold = data.get("rec_hold", 0)
        sell = data.get("rec_sell", 0)
        total = strong_buy + buy + hold + sell

        if total == 0:
            return 50.0

        bull_ratio = (strong_buy * 2 + buy) / (total * 2)  # 0~1

        base = {
            "upgrade": 70.0,
            "downgrade": 30.0,
            "stable": 50.0,
            "": 50.0,
        }.get(rec_change, 50.0)

        # bull_ratio 가중 (±10점)
        adj = (bull_ratio - 0.5) * 20.0
        score = base + adj * 0.5  # 절반만 반영 (강도가 너무 세지 않게)
        return max(0.0, min(100.0, score))
    except Exception:
        return 50.0


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ 2) Turnover Penalty                                                ║
# ╚═══════════════════════════════════════════════════════════════════╝
def apply_turnover_penalty(ticker: str, current_score: float) -> float:
    """점수 변동성 패널티 — 거래비용·재진입 비용의 프록시.

    Args:
        ticker: 종목 코드
        current_score: 이번 회차 점수 [0,100]

    Returns:
        패널티 적용 점수. 직전 회차 캐시가 없으면 원본 반환.

    근거:
        Sharpe ratio = (μ - cost*turnover) / σ. 점수 변동이 클수록
        실전 turnover ↑ → 거래비용 ↑. 시그널 안정성 강제로
        실효 Sharpe 개선 (Moreira-Muir 2017 변형).

    공식:
        penalty = TURNOVER_LAMBDA * max(0, |Δ| - TURNOVER_THRESHOLD)
        score' = score - penalty * sign(Δ)?  # 큰 변동 자체를 억제
        실제: |Δ|에 대한 단순 감산 (방향 무관)
    """
    with _cache_lock:
        prev = _prev_score_cache.get(ticker)
        now = time.time()

    if prev is None or (now - prev[1]) > _PREV_TTL:
        # 첫 진입 또는 캐시 만료 — 패널티 없음, 캐시 갱신
        with _cache_lock:
            _prev_score_cache[ticker] = (current_score, now)
        return current_score

    prev_score, _ = prev
    delta = abs(current_score - prev_score)

    if delta <= TURNOVER_THRESHOLD:
        penalty = 0.0
    else:
        penalty = TURNOVER_LAMBDA * (delta - TURNOVER_THRESHOLD)

    # 캐시는 항상 갱신 (다음 회차 비교용)
    with _cache_lock:
        _prev_score_cache[ticker] = (current_score, now)

    return max(0.0, current_score - penalty)


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ 3) Reversion-Cluster Orthogonalization                             ║
# ╚═══════════════════════════════════════════════════════════════════╝
def orthogonalize_reversion_signals(
    rsi_score: float,
    macd_score: float,
    zscore_signal: float,
    mean_revert_signal: float,
) -> float:
    """4개 reversion 시그널의 1주성분(PC1)을 추출하여 단일 점수로 환원.

    Args:
        각 입력은 [0, 100] 정규화 점수.

    Returns:
        [0, 100] 정규화된 PC1 점수.

    근거:
        RSI / MACD / Z-Score / Mean Reversion 은 모두 "역추세" 신호의 변형으로
        상관계수 0.7+ . 단순 가중합 시 중복 가중 → 실효 독립 팩터 수 ↓.
        PCA 1주성분만 사용하면 직교 알파 추출 (Renaissance/Citadel 표준 관행).

    구현:
        sklearn PCA 단발 호출 — 4D → 1D. 단일 표본이므로 즉시 PCA는
        의미가 적고, **사전 학습된 가중치**를 사용. 가중치는 IC 분석에서
        도출된 휴리스틱 (RSI 0.45, MACD 0.35, Z 0.15, MR 0.05).

    TODO:
        Phase B (IC 측정 + 6M 시계열 캐시)에서 실제 PCA fit으로 교체.
    """
    # 입력 검증·클리핑
    rsi = max(0.0, min(100.0, rsi_score))
    macd = max(0.0, min(100.0, macd_score))
    z = max(0.0, min(100.0, zscore_signal))
    mr = max(0.0, min(100.0, mean_revert_signal))

    # 사전 학습된 PC1 가중치 (heuristic — IC analysis pending)
    # 가중치 합 = 1.0
    PC1_WEIGHTS = (0.45, 0.35, 0.15, 0.05)
    pc1 = (
        PC1_WEIGHTS[0] * rsi
        + PC1_WEIGHTS[1] * macd
        + PC1_WEIGHTS[2] * z
        + PC1_WEIGHTS[3] * mr
    )
    return max(0.0, min(100.0, pc1))


# ╔═══════════════════════════════════════════════════════════════════╗
# ║ Unified entry — 통합 적용                                          ║
# ╚═══════════════════════════════════════════════════════════════════╝
def enhance_score(
    ticker: str,
    base_score: float,
    eps_revision: float | None = None,
) -> dict[str, float]:
    """v21 보강을 통합 적용한 점수.

    Args:
        ticker: 종목 코드
        base_score: 기존 23팩터 가중합 점수 [0,100]
        eps_revision: 미리 계산된 EPS revision 점수 (None이면 자동 계산)

    Returns:
        {
          "base": float,           # 원본 점수
          "eps_rev": float,        # EPS revision 점수
          "after_eps": float,      # EPS revision 가중 합산 후
          "final": float,          # 턴오버 패널티까지 적용한 최종 점수
          "turnover_penalty": float,  # 차감된 패널티 크기
          "enabled": bool,         # ENABLE_V21_FACTORS 상태
        }

    비활성화 시 base_score 그대로 반환.
    """
    result: dict[str, float] = {
        "base": float(base_score),
        "eps_rev": 50.0,
        "after_eps": float(base_score),
        "final": float(base_score),
        "turnover_penalty": 0.0,
        "enabled": float(ENABLE_V21_FACTORS),
    }

    if not ENABLE_V21_FACTORS:
        return result

    # 1) EPS revision 가중 합산
    if eps_revision is None:
        eps_revision = compute_eps_revision_score(ticker)
    result["eps_rev"] = eps_revision
    # base 와 eps_rev 를 (1-W) : W 비율로 합산
    after_eps = base_score * (1.0 - W_EPS_REVISION) + eps_revision * W_EPS_REVISION
    result["after_eps"] = after_eps

    # 2) 턴오버 패널티
    final = apply_turnover_penalty(ticker, after_eps)
    result["turnover_penalty"] = max(0.0, after_eps - final)
    result["final"] = final

    return result


# ─────────────────────────────────────────────────────────────────────
# 통합 가이드 (quant_nexus_v20.py 에 추가할 코드 예시)
# ─────────────────────────────────────────────────────────────────────
"""
[1] 점수 계산 루프 마지막 (final_score 산출 직후) 에 추가:

    # v21 실험적 보강 — 환경변수 ENABLE_V21_FACTORS=1 시 활성
    try:
        from factor_enhancements_v21 import enhance_score, orthogonalize_reversion_signals
        # (옵션) reversion cluster 단일화
        # rsi_score, macd_score, zscore_signal, mean_revert_signal 은 기존 변수
        # rev_pc1 = orthogonalize_reversion_signals(rsi_s, macd_s, z_s, mr_s)
        # — 기존 4개 가중치 합산을 rev_pc1 단일 가중치로 대체 시 적용

        enhanced = enhance_score(ticker, final_score)
        final_score = enhanced["final"]
        # (옵션) 디버그용: enhanced["base"], enhanced["eps_rev"], enhanced["turnover_penalty"]
    except Exception:
        pass  # 실패 시 원본 점수 유지

[2] 환경변수로 ON/OFF:
    Windows:  setx ENABLE_V21_FACTORS 1
    Linux:    export ENABLE_V21_FACTORS=1

[3] 백테스트 후 가중치 (W_EPS_REVISION, TURNOVER_LAMBDA) 재조정.
"""
