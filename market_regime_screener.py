"""
MarketRegimeScreener
====================
HMM + PCA 고유값 이상 탐지를 융합한 선제적 시장 국면 감지 엔진

참고 문헌 / 개념:
  [1] Hamilton (1989) "A New Approach to the Economic Analysis of
      Nonstationary Time Series and the Business Cycle"
      → GaussianHMM 상태 전이 / Forward 알고리즘
  [2] Plerou et al. (1999) "Universal and Nonuniversal Properties of
      Cross Correlations in Financial Time Series" — Random Matrix Theory
      → 공분산 고유값 집중도가 마르첸코-파스투르 상한 초과 시 쏠림 시그널
  [3] Chow et al. (2011) "A Quantitative Approach to Tactical Asset
      Allocation" — Volatility Regime 레이블링
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

# ── 선택 의존성 ──────────────────────────────────────────────────────────────
try:
    from hmmlearn.hmm import GaussianHMM as _GaussianHMM
    _HAS_HMM = True
except ImportError:                                        # pragma: no cover
    _GaussianHMM = None                                    # type: ignore
    _HAS_HMM = False
    warnings.warn(
        "hmmlearn not found. Rule-based fallback will be used for regime detection. "
        "Install via: pip install hmmlearn  (requires 64-bit Python + C compiler)",
        stacklevel=2,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 내부 유틸 — 수치 안정 log-sum-exp
# ─────────────────────────────────────────────────────────────────────────────

def _logsumexp(a: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    amax = np.max(a, axis=axis, keepdims=True)
    amax = np.where(np.isfinite(amax), amax, 0.0)
    out = np.log(np.sum(np.exp(a - amax), axis=axis, keepdims=True)) + amax
    return out.reshape(()) if axis is None else np.squeeze(out, axis=axis)


# ─────────────────────────────────────────────────────────────────────────────
# MarketRegimeScreener
# ─────────────────────────────────────────────────────────────────────────────

class MarketRegimeScreener:
    """
    선제적 시장 국면(Regime) 감지 엔진

    두 가지 독립 모듈 융합:
      Module A — GaussianHMM
        : 단일 벤치마크(예: KOSPI)의 수익률·변동성 피처로
          현재 레짐 및 t+1 전이 확률을 계산한다.
      Module B — PCA Eigenvalue Anomaly
        : 다자산 롤링 공분산 행렬의 PC1 설명력 집중도가
          historical baseline을 벗어나면 이상 스코어를 상승시킨다.

    Usage:
        screener = MarketRegimeScreener(n_states=3)
        screener.fit(benchmark_prices, multi_prices)     # 오프라인 학습
        result = screener.score(benchmark_prices, multi_prices)  # 온라인 스코어링
    """

    _LABEL_PRIORITY = ("bear", "bull", "chop")  # 결정적 매핑 우선순위

    def __init__(
        self,
        n_states: int = 3,
        hmm_fit_window: int = 756,    # HMM 학습에 사용할 최대 바 수 (약 3년)
        anomaly_window: int = 60,     # 공분산 계산 롤링 윈도우 (60 거래일)
        anomaly_lookback: int = 252,  # 이상 스코어 정규화 기준 기간 (1년)
        n_restarts: int = 6,          # HMM 다중 재시작 횟수 (local optima 회피)
        random_state: int = 42,
    ) -> None:
        self.n_states = n_states
        self.hmm_fit_window = hmm_fit_window
        self.anomaly_window = anomaly_window
        self.anomaly_lookback = anomaly_lookback
        self.n_restarts = n_restarts
        self.random_state = random_state

        self._hmm: Optional[Any] = None
        self._state_map: Dict[int, str] = {}   # latent state idx → 'bull'/'bear'/'chop'
        self._pc1_baseline: Optional[np.ndarray] = None
        self._fitted = False

    # =========================================================================
    # PUBLIC — 오프라인 학습
    # =========================================================================

    def fit(
        self,
        price_series: pd.Series,
        multi_prices: Optional[pd.DataFrame] = None,
    ) -> "MarketRegimeScreener":
        """
        오프라인 학습 (배치 실행, 장 마감 후 1회)

        Args:
            price_series : 단일 벤치마크 종가 시계열 (pd.Series, 양수값)
            multi_prices : 다자산 종가 DataFrame (선택 — PCA 이상 탐지용)

        Returns:
            self  (메서드 체이닝 가능)
        """
        prices = self._clean_prices(price_series, "price_series")
        X = self._build_hmm_features(prices)

        if _HAS_HMM and len(X) >= 100:
            self._fit_hmm(X)

        if multi_prices is not None:
            mp = self._clean_multi(multi_prices, "multi_prices")
            self._fit_pca_baseline(mp)

        self._fitted = True
        return self

    # =========================================================================
    # PUBLIC — 온라인 스코어링
    # =========================================================================

    def score(
        self,
        price_series: pd.Series,
        multi_prices: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        온라인 스코어링 — 스크리너 필터 조건문에 바로 사용 가능

        Returns:
            {
              'current_regime'             : int    # 레짐 인덱스 (0=bull,1=bear,2=chop)
              'current_regime_label'       : str    # 'bull' / 'bear' / 'chop'
              'regime_probs'               : dict   # {'bull': 0.7, 'bear': 0.2, 'chop': 0.1}
              'next_regime_transition_prob': float  # 다음 스텝에 레짐 바뀔 확률
              'p_next'                     : dict   # t+1 예측 확률 분포
              'market_anomaly_score'       : float  # 0~1  (높을수록 구조적 이상)
              'pc1_concentration'          : float  # PC1 설명력 (이상 스코어 원점)
              'is_fitted'                  : bool
            }
        """
        if not self._fitted:
            return self._empty_result()

        prices = self._clean_prices(price_series, "price_series")
        X = self._build_hmm_features(prices)
        hmm_out = self._infer_hmm(X)

        anomaly, pc1 = 0.0, float("nan")
        if multi_prices is not None:
            mp = self._clean_multi(multi_prices, "multi_prices")
            anomaly, pc1 = self._score_pca(mp)

        return {
            "current_regime":               hmm_out["current_regime"],
            "current_regime_label":         hmm_out["current_regime_label"],
            "regime_probs":                 hmm_out["regime_probs"],
            "next_regime_transition_prob":  hmm_out["next_regime_transition_prob"],
            "p_next":                       hmm_out["p_next"],
            "market_anomaly_score":         round(float(anomaly), 4),
            "pc1_concentration":            round(float(pc1), 4) if np.isfinite(pc1) else None,
            "is_fitted":                    True,
        }

    # =========================================================================
    # PRIVATE — Module A: GaussianHMM
    # =========================================================================

    def _build_hmm_features(self, prices: pd.Series) -> np.ndarray:
        """
        HMM emission vector 생성

        벡터: [log_ret, rvol_20, vol_ratio, drawdown_60]
          log_ret    : 일간 로그 수익률                  (추세 방향)
          rvol_20    : 20일 실현 변동성 (연율화)         (변동성 레짐)
          vol_ratio  : rvol_10 / rvol_20                 (변동성 구조 변화)
          drawdown_60: 60일 고점 대비 낙폭               (누적 손실 상태)

        Expanding z-score (min_periods=60) 적용 → 미래 데이터 누수 방지
        """
        p = prices.astype(float)
        ret = np.log(p / p.shift(1))
        ann = 252 ** 0.5

        rvol_10  = ret.rolling(10,  min_periods=10).std()  * ann
        rvol_20  = ret.rolling(20,  min_periods=20).std()  * ann
        volratio = (rvol_10 / rvol_20.replace(0.0, np.nan)).clip(0.2, 5.0)
        drawdown = (p / p.rolling(60, min_periods=1).max() - 1.0).clip(-1.0, 0.0)

        feat = pd.DataFrame(
            {"ret": ret, "rvol_20": rvol_20, "volratio": volratio, "drawdown": drawdown}
        ).dropna()

        # 각 피처에 expanding z-score 적용 (NOT rolling, NOT full-sample)
        z = pd.DataFrame(index=feat.index)
        for col in feat.columns:
            s  = feat[col]
            mu = s.expanding(min_periods=60).mean()
            sd = s.expanding(min_periods=60).std().replace(0.0, np.nan)
            z[col] = ((s - mu) / sd).clip(-5.0, 5.0)

        return z.dropna().to_numpy()

    def _fit_hmm(self, X: np.ndarray) -> None:
        """
        Multi-restart GaussianHMM 학습 (Hamilton 1989 EM 알고리즘)

        n_restarts 번 반복 중 log-likelihood 최대이고 퇴화 없는 모델 채택.
        퇴화 기준: 어느 상태의 샘플 비율이 전체의 2% 미만.
        """
        Xfit = X[-self.hmm_fit_window:] if len(X) > self.hmm_fit_window else X
        rng  = np.random.RandomState(self.random_state)
        seeds = rng.randint(0, 9999, size=self.n_restarts).tolist()

        best, best_score = None, -np.inf
        for seed in seeds:
            try:
                m = _GaussianHMM(
                    n_components=self.n_states,
                    covariance_type="diag",
                    n_iter=150,
                    tol=1e-4,
                    random_state=int(seed),
                )
                m.fit(Xfit)
                if not getattr(m.monitor_, "converged", True):
                    continue
                sc = m.score(Xfit)
                if not np.isfinite(sc) or sc <= best_score:
                    continue
                counts = np.bincount(m.predict(Xfit), minlength=self.n_states)
                if (counts < max(2, int(0.02 * len(Xfit)))).any():
                    continue   # 퇴화 거부
                best, best_score = m, sc
            except Exception:
                continue

        if best is None:
            return

        self._hmm      = best
        self._state_map = self._make_state_map(best, Xfit)

    def _make_state_map(self, model: Any, X: np.ndarray) -> Dict[int, str]:
        """
        결정적 상태 라벨 매핑 — label-switching 해소

        정렬 기준 (Hamilton 1989 에서 경험적으로 검증):
          1. rvol_20 최대 → 'bear'
          2. 잔여 중 log_ret 최대 → 'bull'
          3. 나머지 → 'chop'
        """
        try:
            states = model.predict(X)
        except Exception:
            return {i: self._LABEL_PRIORITY[min(i, 2)] for i in range(self.n_states)}

        stats = []
        for s in range(self.n_states):
            mask = states == s
            rv = float(np.nanmean(X[mask, 1])) if mask.any() else np.inf
            rt = float(np.nanmean(X[mask, 0])) if mask.any() else -np.inf
            stats.append((s, rv, rt))

        mapping: Dict[int, str] = {}
        remaining = list(stats)

        # rvol 최대 → bear
        bear_s = max(remaining, key=lambda t: t[1])
        mapping[bear_s[0]] = "bear"
        remaining = [t for t in remaining if t[0] != bear_s[0]]

        if remaining:
            # 잔여 중 ret 최대 → bull
            bull_s = max(remaining, key=lambda t: t[2])
            mapping[bull_s[0]] = "bull"
            remaining = [t for t in remaining if t[0] != bull_s[0]]

        for t in remaining:
            mapping[t[0]] = "chop"

        return mapping

    def _forward_filter(self, X: np.ndarray) -> np.ndarray:
        """
        Forward-only 필터링 (Hamilton 1989 §3, Eq.16~22)

        alpha_t = P(s_t | o_{1..t})
        — 현재 시점까지의 관측만 사용, 미래 관측 또는 Viterbi smoothing 금지.
        라이브 시스템에서 미래 누수를 원천 차단하는 핵심 설계.

        수치 안정성: log-domain 계산 후 exp 변환.
        """
        model       = self._hmm
        log_emit    = model._compute_log_likelihood(X)      # (T, K)
        T, K        = log_emit.shape
        log_pi      = np.log(np.clip(model.startprob_,  1e-12, None))  # (K,)
        log_A       = np.log(np.clip(model.transmat_,   1e-12, None))  # (K, K)

        alpha = np.zeros((T, K))
        la    = log_pi + log_emit[0]
        la   -= _logsumexp(la)
        alpha[0] = np.exp(la)

        for t in range(1, T):
            # 예측 단계: log Σ_i alpha_{t-1,i} * A_{i,j}  (행렬 broadcast)
            pred = _logsumexp(la[:, None] + log_A, axis=0)  # (K,)
            la   = pred + log_emit[t]
            la  -= _logsumexp(la)
            alpha[t] = np.exp(la)

        return alpha  # (T, K)

    def _infer_hmm(self, X: np.ndarray) -> Dict[str, Any]:
        """HMM 추론: 현재 레짐 사후 확률 + t+1 전이 확률."""
        if self._hmm is None or len(X) < 5:
            return self._rule_based_fallback(X)

        try:
            alpha       = self._forward_filter(X)   # (T, K)
            alpha_last  = alpha[-1]                  # 현재 bar 사후 확률 (K,)

            # t+1 예측: p_next_states = alpha_t @ A  (Hamilton 1989 Eq.22)
            A             = np.asarray(self._hmm.transmat_)
            p_next_states = alpha_last @ A           # (K,)

            # latent state → regime label 집계
            regime_probs: Dict[str, float] = {}
            p_next:       Dict[str, float] = {}
            for s in range(len(alpha_last)):
                lbl = self._state_map.get(s, f"state_{s}")
                regime_probs[lbl] = regime_probs.get(lbl, 0.0) + float(alpha_last[s])
                p_next[lbl]       = p_next.get(lbl, 0.0)       + float(p_next_states[s])

            cur_label = max(regime_probs, key=regime_probs.get)
            cur_idx   = next(
                (k for k, v in self._state_map.items() if v == cur_label), 0
            )

            # 전이 확률 = 1 - P(현재 레짐 유지)
            p_stay         = p_next.get(cur_label, 0.0)
            transition_prob = round(float(1.0 - p_stay), 4)

            return {
                "current_regime":              cur_idx,
                "current_regime_label":        cur_label,
                "regime_probs":                {k: round(v, 4) for k, v in regime_probs.items()},
                "next_regime_transition_prob": transition_prob,
                "p_next":                      {k: round(v, 4) for k, v in p_next.items()},
            }
        except Exception:
            return self._rule_based_fallback(X)

    def _rule_based_fallback(self, X: np.ndarray) -> Dict[str, Any]:
        """HMM 불가 시 룰 기반 폴백 (rvol + drawdown 임계)."""
        if len(X) == 0:
            label, idx = "chop", 2
        else:
            rvol, dd = float(X[-1, 1]), float(X[-1, 3])
            if rvol > 0.8 and dd < -0.4:
                label, idx = "bear", 1
            elif rvol < -0.3 and dd > -0.1:
                label, idx = "bull", 0
            else:
                label, idx = "chop", 2

        conf = 0.75
        rest = (1.0 - conf) / 2
        probs = {"bull": rest, "bear": rest, "chop": rest}
        probs[label] = conf

        return {
            "current_regime":              idx,
            "current_regime_label":        label,
            "regime_probs":                probs,
            "next_regime_transition_prob": 0.15,
            "p_next":                      probs,
        }

    # =========================================================================
    # PRIVATE — Module B: PCA Eigenvalue Anomaly
    # =========================================================================

    def _rolling_pc1(self, log_ret: pd.DataFrame) -> np.ndarray:
        """
        롤링 공분산 행렬의 PC1 설명력 시계열 계산

        PC1 집중도 = λ_max / Σλ_i
        Random Matrix Theory(RMT): 이 비율이 마르첸코-파스투르 이론치를
        초과하면 시장에 공통 인자(패닉 / 버블)가 지배적임을 의미 [Plerou 1999]
        간소화 구현: 절대 임계 대신 historical z-score로 이상 스코어화.
        """
        n = len(log_ret)
        if n < self.anomaly_window + 5:
            return np.array([])

        results = []
        for end in range(self.anomaly_window, n + 1):
            W = log_ret.iloc[end - self.anomaly_window:end].dropna(axis=1)
            if W.shape[1] < 2:
                continue
            cov = W.cov().to_numpy()
            if not _is_valid_cov(cov):
                continue
            eigenvalues = np.clip(np.linalg.eigvalsh(cov), 0.0, None)  # 수치 오차 음수 제거
            total = eigenvalues.sum()
            if total <= 0:
                continue
            # eigvalsh는 오름차순 → 마지막이 최대 고유값
            results.append(float(eigenvalues[-1] / total))

        return np.array(results)

    def _fit_pca_baseline(self, multi_prices: pd.DataFrame) -> None:
        """PCA 이상 스코어 정규화 baseline 학습."""
        log_ret = np.log(multi_prices / multi_prices.shift(1)).dropna()
        pc1_series = self._rolling_pc1(log_ret)
        if len(pc1_series) >= 10:
            self._pc1_baseline = pc1_series

    def _score_pca(self, multi_prices: pd.DataFrame) -> Tuple[float, float]:
        """
        현재 PC1 집중도 이상 스코어 계산

        z = (현재 PC1 - baseline 평균) / baseline 표준편차
        sigmoid(z) → 0~1 이상 스코어
        z > 0 : 집중도 평균 이상 → 시장 쏠림 / 상관관계 급등 경보
        """
        log_ret = np.log(multi_prices / multi_prices.shift(1)).dropna()
        recent  = log_ret.tail(self.anomaly_window + 10)
        pc1_arr = self._rolling_pc1(recent)

        if len(pc1_arr) == 0:
            return 0.0, float("nan")

        current_pc1 = pc1_arr[-1]

        if self._pc1_baseline is not None and len(self._pc1_baseline) > 10:
            mu  = float(np.mean(self._pc1_baseline))
            std = float(np.std(self._pc1_baseline)) or 1e-8
            z   = (current_pc1 - mu) / std
        else:
            z = (current_pc1 - 0.5) * 4.0   # baseline 없을 때 선형 추정

        anomaly_score = float(1.0 / (1.0 + np.exp(-z)))   # sigmoid
        return round(anomaly_score, 4), round(current_pc1, 4)

    # =========================================================================
    # PRIVATE — 데이터 검증
    # =========================================================================

    @staticmethod
    def _clean_prices(s: Any, name: str) -> pd.Series:
        """단일 가격 시계열 정제: NaN 제거, 양수 강제."""
        if not isinstance(s, pd.Series):
            s = pd.Series(s)
        s = s.dropna()
        s = s[s > 0]
        if len(s) == 0:
            raise ValueError(f"'{name}': no valid positive values after cleaning.")
        return s.astype(float)

    @staticmethod
    def _clean_multi(df: Any, name: str) -> pd.DataFrame:
        """
        다자산 DataFrame 정제

        1. NaN 비율 50% 초과 열 제거
        2. 잔여 NaN은 forward-fill 후 dropna
        """
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"'{name}' must be a pd.DataFrame.")
        df = df.dropna(how="all")
        valid_cols = df.columns[df.isna().mean() < 0.5]
        df = df[valid_cols].ffill().dropna()
        return df.astype(float)

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "current_regime":               -1,
            "current_regime_label":         "unknown",
            "regime_probs":                 {},
            "next_regime_transition_prob":  float("nan"),
            "p_next":                       {},
            "market_anomaly_score":         float("nan"),
            "pc1_concentration":            None,
            "is_fitted":                    False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 모듈 수준 유틸
# ─────────────────────────────────────────────────────────────────────────────

def _is_valid_cov(cov: np.ndarray) -> bool:
    """
    공분산 행렬 유효성 3단계 검사

    1. 모든 원소가 유한값인가?
    2. 대칭 행렬인가?  (A ≈ A^T)
    3. 양반정치(PSD)인가?  (최소 고유값 ≥ -1e-6, 수치 오차 허용)
    """
    if not np.all(np.isfinite(cov)):
        return False
    if not np.allclose(cov, cov.T, atol=1e-8):
        return False
    return float(np.linalg.eigvalsh(cov).min()) >= -1e-6


# ─────────────────────────────────────────────────────────────────────────────
# __main__ — 가상 데이터 시연
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("  MarketRegimeScreener — 가상 데이터 시연")
    print("=" * 60)

    rng = np.random.default_rng(2024)
    N   = 1000   # 약 4년치 거래일

    # ── 3구간 레짐 가상 가격 생성 ───────────────────────────────────────────
    # 구간 A (0~399)  : 저변동 상승 (Bull)
    # 구간 B (400~599): 고변동 하락 (Bear)
    # 구간 C (600~999): 중간 횡보  (Chop)
    def _make_price(drifts, vols, lengths, seed=42):
        rng2 = np.random.default_rng(seed)
        rets = np.concatenate([
            rng2.normal(d, v, l) for d, v, l in zip(drifts, vols, lengths)
        ])
        return pd.Series(
            100.0 * np.exp(np.cumsum(rets)),
            index=pd.bdate_range("2020-01-02", periods=N),
        )

    benchmark = _make_price(
        drifts  = [ 0.0006, -0.0015,  0.0001],
        vols    = [ 0.008,   0.025,   0.012],
        lengths = [400,      200,      400],
    )

    # ── 다자산 가상 데이터 (5개 섹터 ETF 대용) ──────────────────────────────
    def _make_multi(benchmark: pd.Series, n_assets: int = 5) -> pd.DataFrame:
        idx    = benchmark.index
        prices = {"asset_0": benchmark}
        for i in range(1, n_assets):
            beta  = rng.uniform(0.5, 1.5)
            noise = rng.normal(0.0, 0.005 * rng.uniform(1, 3), len(idx))
            log_bm = np.log(benchmark / benchmark.shift(1)).fillna(0.0).to_numpy()
            ret_i  = beta * log_bm + noise
            prices[f"asset_{i}"] = pd.Series(
                100.0 * np.exp(np.cumsum(ret_i)), index=idx
            )
        return pd.DataFrame(prices)

    multi = _make_multi(benchmark)

    # ── fit ─────────────────────────────────────────────────────────────────
    screener = MarketRegimeScreener(n_states=3, n_restarts=5)
    print("\n▶ fit() 실행 중...")
    screener.fit(benchmark, multi_prices=multi)
    print("  완료\n")

    # ── score (전체 기간 / 최근 데이터) ────────────────────────────────────
    result = screener.score(benchmark, multi_prices=multi)

    print("=" * 60)
    print("  score() 반환값")
    print("=" * 60)
    for k, v in result.items():
        print(f"  {k:<36} : {v}")

    # ── 스크리너 조건문 예시 ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  스크리너 조건문 활용 예시")
    print("=" * 60)

    ANOMALY_THRESHOLD      = 0.70
    TRANSITION_THRESHOLD   = 0.35

    if result["market_anomaly_score"] > ANOMALY_THRESHOLD:
        print("  ⚠️  [ALERT] 시장 공분산 구조 이상 감지 — 포지션 축소 고려")

    if result["next_regime_transition_prob"] > TRANSITION_THRESHOLD:
        print(f"  🔄 [SIGNAL] 레짐 전환 확률 {result['next_regime_transition_prob']:.0%} "
              f"— 현재: {result['current_regime_label'].upper()}")

    if result["current_regime_label"] == "bull":
        print("  🟢 [REGIME] Bull — 풀 포지션 유지 가능")
    elif result["current_regime_label"] == "bear":
        print("  🔴 [REGIME] Bear — 현금 비중 확대 고려")
    else:
        print("  🟡 [REGIME] Chop — 보수적 운용 (50% 이하)")

    print()
