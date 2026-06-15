# -*- coding: utf-8 -*-
"""확률적 레짐 분류기 (모듈1) — Daily-bar Gaussian-HMM regime detection.

3-레짐(low_vol_uptrend / high_vol_downtrend / range_chop) 확률 분류 + 선행 전환신호.
연구 스펙 `.kkirikkiri/research/hmm_regime_spec.md` 채택.

핵심 원칙:
- expanding z-score 표준화(min_periods=252, NOT rolling/full-sample) — 누수 방지.
- forward-only filtering(확장-예측 루프) — Viterbi/.predict()/.predict_proba() 라이브 금지.
- 결정적 라벨 매핑(상태별 mean(mom)/mean(rvol_20) 정렬) — label-switching 해소.
- graceful fallback: hmmlearn 부재/미수렴/퇴화/<500bar → rule_based. 절대 raise 안 함.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# optional dependency isolation — 32-bit 런타임에서 hmmlearn/scipy 휠 없음
# ---------------------------------------------------------------------------
try:
    from hmmlearn.hmm import GaussianHMM  # type: ignore
    _HAS_HMM = True
except Exception as _e:  # pragma: no cover - depends on interpreter
    GaussianHMM = None  # type: ignore
    _HAS_HMM = False
    log.info("hmmlearn unavailable — regime_classifier falls back to rule_based (%s)", _e)

try:
    from sklearn.cluster import KMeans  # type: ignore
    _HAS_KMEANS = True
except Exception:  # pragma: no cover
    KMeans = None  # type: ignore
    _HAS_KMEANS = False


# ---------------------------------------------------------------------------
# Config — 연구 스펙 §6 verbatim
# ---------------------------------------------------------------------------
REGIME_CONFIG: Dict[str, Any] = {
    "data": "daily_ohlcv",            # DAILY bars only
    "annualization": 252,
    # --- HMM ---
    "model": "GaussianHMM",
    "n_states": 3,                    # 4 only if BIC wins + maps to stress state
    "covariance_type": "diag",
    "n_iter": 150,
    "tol": 1e-4,
    "min_covar": 1e-3,
    "n_init_restarts": 8,             # keep best .score()
    "random_state": 42,
    "init_params": "stc",             # warm-start means via KMeans/GMM externally
    # --- features (HMM emission vector) ---
    "features": ["ret", "rvol_20", "volratio", "skew", "dd", "eff"],
    "windows": {
        "ret": 1, "mom": 20, "rvol_short": 10, "rvol_long": 20,
        "skew": 20, "atr": 14, "drawdown": 60, "vol_z": 20, "eff": 20,
    },
    "standardize": "expanding_zscore",   # min_periods=252; NOT rolling, NOT full-sample
    "winsorize": (0.01, 0.99),
    # --- labeling ---
    "label_by": {"sort_keys": ["mom", "rvol_20"]},  # deterministic state->regime
    # --- inference (leak-free) ---
    "inference": "forward_filter",   # forward-only alpha; AVOID Viterbi/smoothing live
    "refit_cadence_bars": 21,        # monthly EM
    "rolling_fit_window_bars": 756,  # 3yr
    "min_fit_bars": 500,
    # --- early transition signal ---
    "thresholds": {
        "arm": 0.40, "enter": 0.50, "exit": 0.50, "exit_hysteresis": 0.45,
        "forecast_next": 0.55, "rising_days": 2, "volratio_long_gate": 1.0,
    },
    "fallback": ["more_seeds", "two_state_hmm", "rule_based"],
}

# canonical regime labels
R_BULL = "low_vol_uptrend"
R_BEAR = "high_vol_downtrend"
R_CHOP = "range_chop"
_REGIMES = (R_BULL, R_BEAR, R_CHOP)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime_cache")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class RegimeResult:
    state: str                                   # 'low_vol_uptrend'|'high_vol_downtrend'|'range_chop'
    probs: Dict[str, float] = field(default_factory=dict)        # filtered posterior P(state_t|obs_1..t)
    p_next: Dict[str, float] = field(default_factory=dict)       # 1-step forecast alpha_t · A
    transition_signal: Dict[str, Any] = field(default_factory=dict)  # early_long/early_exit/strength/fresh
    model_status: str = "rule_based"             # 'hmm'|'two_state'|'rule_based'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _empty_signal() -> Dict[str, Any]:
    return {"early_long": False, "early_exit": False, "strength": 0.0, "fresh": 0.0}


# ---------------------------------------------------------------------------
# Feature engineering (daily OHLCV) — all trailing, leak-free
# ---------------------------------------------------------------------------
def _safe_close(ohlcv: pd.DataFrame) -> pd.Series:
    """Return a Close series regardless of column casing."""
    for c in ("Close", "close", "Adj Close", "adj_close"):
        if c in ohlcv.columns:
            return pd.to_numeric(ohlcv[c], errors="coerce")
    # fall back to last numeric column
    return pd.to_numeric(ohlcv.iloc[:, -1], errors="coerce")


def _col(ohlcv: pd.DataFrame, *names: str) -> Optional[pd.Series]:
    for n in names:
        if n in ohlcv.columns:
            return pd.to_numeric(ohlcv[n], errors="coerce")
    return None


def compute_features(ohlcv: pd.DataFrame, config: dict = REGIME_CONFIG) -> pd.DataFrame:
    """Compute the raw (unstandardized) feature frame incl. labeling helpers.

    Columns: ret, mom, rvol_10, rvol_20, volratio, skew, dd, eff (+ atr_pct, vol_z helpers).
    """
    w = config["windows"]
    ann = float(config.get("annualization", 252)) ** 0.5

    close = _safe_close(ohlcv).astype(float)
    high = _col(ohlcv, "High", "high")
    low = _col(ohlcv, "Low", "low")
    vol = _col(ohlcv, "Volume", "volume")
    if high is None:
        high = close
    if low is None:
        low = close

    out = pd.DataFrame(index=ohlcv.index)
    ret = np.log(close / close.shift(1))
    out["ret"] = ret
    out["mom"] = np.log(close / close.shift(int(w["mom"])))
    out["rvol_10"] = ret.rolling(int(w["rvol_short"]), min_periods=int(w["rvol_short"])).std() * ann
    out["rvol_20"] = ret.rolling(int(w["rvol_long"]), min_periods=int(w["rvol_long"])).std() * ann
    out["volratio"] = out["rvol_10"] / out["rvol_20"].replace(0.0, np.nan)
    out["skew"] = ret.rolling(int(w["skew"]), min_periods=int(w["skew"])).skew()

    # drawdown vs trailing 60-bar peak
    nd = int(w["drawdown"])
    cummax = close.rolling(nd, min_periods=1).max()
    out["dd"] = close / cummax - 1.0

    # efficiency ratio (chop separator)
    ne = int(w["eff"])
    direction = (close - close.shift(ne)).abs()
    volatility = close.diff().abs().rolling(ne, min_periods=ne).sum()
    out["eff"] = direction / volatility.replace(0.0, np.nan)

    # helpers (post-hoc only, not in emission vector)
    na = int(w["atr"])
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(na, min_periods=na).mean()
    out["atr_pct"] = atr / close.replace(0.0, np.nan)
    if vol is not None:
        nv = int(w["vol_z"])
        mu = vol.rolling(nv, min_periods=nv).mean()
        sd = vol.rolling(nv, min_periods=nv).std()
        out["vol_z"] = (vol - mu) / sd.replace(0.0, np.nan)
    else:
        out["vol_z"] = 0.0

    return out


def _winsorize(s: pd.Series, lo: float, hi: float) -> pd.Series:
    if s.notna().sum() < 5:
        return s
    ql, qh = s.quantile(lo), s.quantile(hi)
    return s.clip(lower=ql, upper=qh)


def standardize_features(feat: pd.DataFrame, config: dict = REGIME_CONFIG) -> pd.DataFrame:
    """Expanding z-score (min_periods=252) — leak-free. Winsorize bounded eff/skew.

    Returns standardized emission matrix (only `features` columns), NaN warmup rows kept.
    """
    cols = list(config["features"])
    lo, hi = config.get("winsorize", (0.01, 0.99))
    mp = 252
    z = pd.DataFrame(index=feat.index)
    for c in cols:
        if c not in feat.columns:
            z[c] = 0.0
            continue
        s = feat[c].astype(float)
        if c in ("eff", "skew"):
            s = _winsorize(s, lo, hi)
        # expanding stats use only past+current -> leak free
        em = s.expanding(min_periods=mp).mean()
        es = s.expanding(min_periods=mp).std()
        zc = (s - em) / es.replace(0.0, np.nan)
        z[c] = zc
    return z


# ---------------------------------------------------------------------------
# HMM fit + deterministic label mapping
# ---------------------------------------------------------------------------
def _fit_hmm(X: np.ndarray, n_states: int, config: dict, seeds) -> Optional[Any]:
    """Multi-restart GaussianHMM fit. Returns best-scoring converged model or None."""
    if not _HAS_HMM:
        return None
    best = None
    best_score = -np.inf
    for seed in seeds:
        try:
            model = GaussianHMM(
                n_components=n_states,
                covariance_type=config.get("covariance_type", "diag"),
                n_iter=int(config.get("n_iter", 150)),
                tol=float(config.get("tol", 1e-4)),
                min_covar=float(config.get("min_covar", 1e-3)),
                random_state=int(seed),
                init_params=config.get("init_params", "stc"),
            )
            # warm-start means via KMeans to avoid degenerate locals
            if _HAS_KMEANS and "m" in model.init_params:
                try:
                    km = KMeans(n_clusters=n_states, n_init=4,
                                random_state=int(seed)).fit(X)
                    model.init_params = model.init_params.replace("m", "")
                    model.means_ = km.cluster_centers_
                except Exception:
                    pass
            model.fit(X)
            if not getattr(model.monitor_, "converged", True):
                continue
            sc = model.score(X)
            if np.isfinite(sc) and sc > best_score:
                best_score = sc
                best = model
        except Exception as e:  # noqa: BLE001
            log.debug("HMM fit seed=%s failed: %s", seed, e)
            continue
    return best


def _is_degenerate(model: Any, X: np.ndarray, n_states: int) -> bool:
    """Reject singular covars or near-empty states."""
    try:
        states = model.predict(X)  # fit-time labeling only (not the live signal)
        counts = np.bincount(states, minlength=n_states)
        if (counts < max(2, 0.02 * len(X))).any():
            return True
        # diag-covariance variances: hmmlearn returns full matrices; inspect the
        # diagonal (the actual per-feature variances). Off-diagonals are 0 for diag.
        cov = np.asarray(model.covars_, dtype=float)
        if not np.all(np.isfinite(cov)):
            return True
        if cov.ndim == 3:  # (n_states, n_feat, n_feat)
            variances = np.array([np.diag(c) for c in cov])
        else:              # (n_states, n_feat)
            variances = cov
        if (variances <= 0).any():
            return True
    except Exception:
        return True
    return False


def _build_state_map(model: Any, feat: pd.DataFrame, X: np.ndarray,
                     n_states: int) -> Dict[int, str]:
    """Deterministic state->regime mapping via per-state mean(mom)/mean(rvol_20)."""
    try:
        states = model.predict(X)
    except Exception:
        # uniform fallback
        return {i: _REGIMES[min(i, 2)] for i in range(n_states)}

    mom = feat["mom"].to_numpy()
    rvol = feat["rvol_20"].to_numpy()
    stats = []
    for s in range(n_states):
        mask = states == s
        if mask.sum() == 0:
            stats.append((s, -np.inf, np.inf))
            continue
        # all-NaN 슬라이스 방어: nanmean 경고/NaN 키로 인한 라벨 오배정 방지.
        # 빈/NaN 상태는 mom=-inf(약세), rvol=+inf(고변동) 센티넬로 결정적 처리.
        mom_s = mom[mask]
        rvol_s = rvol[mask]
        mm = float(np.nanmean(mom_s)) if np.isfinite(mom_s).any() else -np.inf
        rv = float(np.nanmean(rvol_s)) if np.isfinite(rvol_s).any() else np.inf
        stats.append((s, mm, rv))

    mapping: Dict[int, str] = {}
    remaining = list(stats)

    # high_vol_downtrend = highest rvol
    bear = max(remaining, key=lambda t: (t[2] if np.isfinite(t[2]) else -np.inf))
    mapping[bear[0]] = R_BEAR
    remaining = [t for t in remaining if t[0] != bear[0]]

    # low_vol_uptrend = highest mom (lowest rvol among rest)
    bull = max(remaining, key=lambda t: (t[1], -t[2]))
    mapping[bull[0]] = R_BULL
    remaining = [t for t in remaining if t[0] != bull[0]]

    # remaining -> range_chop
    for t in remaining:
        mapping[t[0]] = R_CHOP
    return mapping


# ---------------------------------------------------------------------------
# Forward-only filtering (leak-free live posterior) — NO Viterbi / smoothing
# ---------------------------------------------------------------------------
def _forward_posteriors(model: Any, X: np.ndarray) -> np.ndarray:
    """Filtered posteriors alpha_t = P(state_t | obs_1..t) via forward recursion.

    Uses model._compute_log_likelihood + manual normalized forward pass. Each row t
    depends ONLY on obs[:t+1] -> no lookahead. Returns (T, n_states) array.
    """
    framelogprob = model._compute_log_likelihood(X)  # (T, n_states), per-frame emission ll
    T, n = framelogprob.shape
    log_startprob = np.log(np.clip(model.startprob_, 1e-12, None))
    log_transmat = np.log(np.clip(model.transmat_, 1e-12, None))

    alpha = np.zeros((T, n))
    # t = 0
    la = log_startprob + framelogprob[0]
    la -= _logsumexp(la)
    alpha[0] = np.exp(la)
    prev = la
    for t in range(1, T):
        # predict: log sum_i prev[i] * A[i,j]
        m = prev[:, None] + log_transmat  # (n, n)
        pred = _logsumexp(m, axis=0)       # (n,)
        la = pred + framelogprob[t]
        la -= _logsumexp(la)
        alpha[t] = np.exp(la)
        prev = la
    return alpha


def _logsumexp(a: np.ndarray, axis: Optional[int] = None) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    amax = np.max(a, axis=axis, keepdims=True)
    amax = np.where(np.isfinite(amax), amax, 0.0)
    out = np.log(np.sum(np.exp(a - amax), axis=axis, keepdims=True)) + amax
    if axis is None:
        return out.reshape(())
    return np.squeeze(out, axis=axis)


# ---------------------------------------------------------------------------
# Early transition signal (§3)
# ---------------------------------------------------------------------------
def _hard_state_from_probs(prob_row: Dict[str, float]) -> str:
    return max(prob_row, key=prob_row.get)


def _compute_transition_signal(prob_hist: pd.DataFrame, pnext: Dict[str, float],
                               config: dict) -> Dict[str, Any]:
    """early_long/early_exit/strength/fresh from filtered-posterior history.

    prob_hist: DataFrame indexed by time, columns = regime labels (filtered posteriors).
    pnext: 1-step forecast for the latest bar.
    """
    th = config["thresholds"]
    arm = float(th["arm"]); enter = float(th["enter"])
    exit_lvl = float(th["exit"]); fc = float(th["forecast_next"])
    rising_days = int(th["rising_days"])

    sig = _empty_signal()
    if prob_hist is None or len(prob_hist) == 0:
        return sig

    pb = prob_hist[R_BULL].to_numpy()
    pr = prob_hist[R_BEAR].to_numpy()
    pc = prob_hist[R_CHOP].to_numpy()
    n = len(pb)
    last = {R_BULL: float(pb[-1]), R_BEAR: float(pr[-1]), R_CHOP: float(pc[-1])}
    # The lead fires WHILE the trend was still bear/chop. Gate on the PRIOR-bar
    # committed (hard) state so a fresh upward cross through `enter` is captured
    # even when the latest bar's argmax has just flipped to the new regime.
    if n >= 2:
        prev_row = {R_BULL: float(pb[-2]), R_BEAR: float(pr[-2]), R_CHOP: float(pc[-2])}
    else:
        prev_row = last
    hard = _hard_state_from_probs(prev_row)

    def _rising(arr: np.ndarray) -> bool:
        if n <= rising_days:
            return False
        return all(arr[-i] - arr[-i - 1] > 0 for i in range(1, rising_days + 1))

    def _cross_up(arr: np.ndarray, lvl: float) -> bool:
        return n >= 2 and arr[-2] < lvl <= arr[-1]

    # --- early LONG ---
    p_bull = last[R_BULL]
    bull_rising = _rising(pb)
    long_trigger = _cross_up(pb, enter) or float(pnext.get(R_BULL, 0.0)) >= fc
    early_long = (hard in (R_BEAR, R_CHOP) and p_bull >= arm
                  and bull_rising and long_trigger)

    # --- early EXIT (symmetric) ---
    p_bear = last[R_BEAR]
    bear_rising = _rising(pr)
    exit_trigger = _cross_up(pr, exit_lvl) or float(pnext.get(R_BEAR, 0.0)) >= fc
    early_exit = (hard == R_BULL and p_bear >= arm and bear_rising and exit_trigger)

    sig["early_long"] = bool(early_long)
    sig["early_exit"] = bool(early_exit)
    # strength는 방향에 맞는 확률로 산출 — exit은 p_bear, 그 외는 p_bull.
    # (이전: 항상 p_bull → exit 페널티가 엉뚱한 확률로 스케일되던 버그)
    if early_exit and not early_long:
        sig["strength"] = float(max(p_bear - 0.5, 0.0) * 2.0)
    else:
        sig["strength"] = float(max(p_bull - 0.5, 0.0) * 2.0)

    # fresh: decays from 1.0 at the cross to 0 after rising_days bars.
    # cross는 방향별 임계(enter/exit_lvl)로 측정 — exit이 enter와 다를 때의 오측정 수정.
    fresh = 0.0
    if early_long or early_exit:
        arr = pb if early_long else pr
        lvl = enter if early_long else exit_lvl
        # bars since the value crossed the directional level upward
        bars_since = None
        for k in range(1, min(n, rising_days + 2)):
            if n - 1 - k >= 0 and arr[-1 - k] < lvl <= arr[-k]:
                bars_since = k - 1
                break
        if bars_since is None:
            bars_since = 0
        fresh = max(0.0, 1.0 - bars_since / max(1, rising_days))
    sig["fresh"] = float(fresh)
    return sig


# ---------------------------------------------------------------------------
# Rule-based fallback (§5)
# ---------------------------------------------------------------------------
def _rule_based(feat: pd.DataFrame) -> RegimeResult:
    """rvol_20 percentile + dd_60 + eff_20 thresholds → 3-regime approximation."""
    valid = feat.dropna(subset=["rvol_20", "dd", "eff"])
    if len(valid) == 0:
        return RegimeResult(state=R_CHOP, probs={R_BULL: 1/3, R_BEAR: 1/3, R_CHOP: 1/3},
                            p_next={R_BULL: 1/3, R_BEAR: 1/3, R_CHOP: 1/3},
                            transition_signal=_empty_signal(), model_status="rule_based")
    rvol = valid["rvol_20"]
    last = valid.iloc[-1]
    rvol_pct = float((rvol <= last["rvol_20"]).mean())  # percentile of latest rvol
    dd = float(last["dd"])
    eff = float(last["eff"]) if np.isfinite(last["eff"]) else 0.0
    mom = float(last["mom"]) if np.isfinite(last.get("mom", np.nan)) else 0.0

    # decision
    if rvol_pct >= 0.70 and dd <= -0.05:
        state = R_BEAR
    elif eff < 0.30 and rvol_pct < 0.70:
        state = R_CHOP
    elif mom > 0 and rvol_pct < 0.60 and dd > -0.10:
        state = R_BULL
    elif dd <= -0.08:
        state = R_BEAR
    else:
        state = R_CHOP

    # soft probs around the decision (heuristic confidence)
    probs = {R_BULL: 0.2, R_BEAR: 0.2, R_CHOP: 0.2}
    probs[state] = 0.6
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    return RegimeResult(state=state, probs=probs, p_next=dict(probs),
                        transition_signal=_empty_signal(), model_status="rule_based")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def classify_regime(ohlcv: pd.DataFrame, *, config: dict = REGIME_CONFIG) -> RegimeResult:
    """Classify the current daily regime. Never raises — always returns RegimeResult."""
    try:
        if ohlcv is None or len(ohlcv) == 0:
            return RegimeResult(state=R_CHOP, probs={r: 1/3 for r in _REGIMES},
                                p_next={r: 1/3 for r in _REGIMES},
                                transition_signal=_empty_signal(),
                                model_status="rule_based")
        feat = compute_features(ohlcv, config)
    except Exception as e:  # noqa: BLE001
        log.warning("feature computation failed: %s", e)
        return RegimeResult(state=R_CHOP, probs={r: 1/3 for r in _REGIMES},
                            p_next={r: 1/3 for r in _REGIMES},
                            transition_signal=_empty_signal(), model_status="rule_based")

    min_fit = int(config.get("min_fit_bars", 500))
    z = standardize_features(feat, config)
    cols = list(config["features"])
    Z = z[cols].dropna()

    # fallback: hmmlearn absent OR not enough standardized bars
    if (not _HAS_HMM) or len(Z) < min_fit:
        return _rule_based(feat)

    try:
        result = _classify_hmm(feat, z, Z, config)
        if result is not None:
            return result
    except Exception as e:  # noqa: BLE001
        log.warning("HMM classification failed, rule_based fallback: %s", e)
    return _rule_based(feat)


def _classify_hmm(feat: pd.DataFrame, z: pd.DataFrame, Z: pd.DataFrame,
                  config: dict) -> Optional[RegimeResult]:
    """Core HMM path. Returns None to signal caller to use rule_based fallback."""
    cols = list(config["features"])
    # 점-인-타임 안정성(§2.4): 최근 rolling_fit_window_bars(기본 756=3yr)로만 적합·추론.
    # 전체 시계열로 적합하면 미래 바가 추가될 때 과거 day-t posterior가 바뀌어
    # 백테스트 재현성이 깨진다(파라미터 드리프트). 윈도우 슬라이스로 이를 방지.
    win = int(config.get("rolling_fit_window_bars", 756) or 756)
    if win > 0 and len(Z) > win:
        Z = Z.iloc[-win:]
    X = Z.to_numpy()
    feat_aligned = feat.loc[Z.index]

    n_states = int(config.get("n_states", 3))
    base_seed = int(config.get("random_state", 42))
    n_restarts = int(config.get("n_init_restarts", 8))
    seeds = [base_seed + i for i in range(n_restarts)]

    model = _fit_hmm(X, n_states, config, seeds)
    model_status = "hmm"

    # degenerate/non-converged → 2-state HMM
    if model is None or _is_degenerate(model, X, n_states):
        model2 = _fit_hmm(X, 2, config, seeds)
        if model2 is None or _is_degenerate(model2, X, 2):
            return None  # → rule_based
        model = model2
        n_states = 2
        model_status = "two_state"

    state_map = _build_state_map(model, feat_aligned, X, n_states)

    # forward-only filtered posteriors (leak-free) for the whole series
    alpha = _forward_posteriors(model, X)  # (T, n_states)

    # aggregate latent-state posteriors into regime posteriors
    prob_hist = _alpha_to_regime_df(alpha, state_map, Z.index)

    last_probs = {r: float(prob_hist[r].iloc[-1]) for r in _REGIMES}

    # 1-step forecast: p_next = alpha_t · A, then map states->regimes
    A = np.asarray(model.transmat_)
    pnext_states = alpha[-1] @ A
    pnext = _state_vec_to_regime(pnext_states, state_map)

    state = max(last_probs, key=last_probs.get)
    sig = _compute_transition_signal(prob_hist, pnext, config)

    return RegimeResult(state=state, probs=last_probs, p_next=pnext,
                        transition_signal=sig, model_status=model_status)


def _alpha_to_regime_df(alpha: np.ndarray, state_map: Dict[int, str],
                        index) -> pd.DataFrame:
    """Sum latent-state posteriors into regime columns."""
    n_states = alpha.shape[1]
    df = pd.DataFrame(0.0, index=index, columns=list(_REGIMES))
    for s in range(n_states):
        r = state_map.get(s, R_CHOP)
        df[r] = df[r].to_numpy() + alpha[:, s]
    return df


def _state_vec_to_regime(vec: np.ndarray, state_map: Dict[int, str]) -> Dict[str, float]:
    out = {r: 0.0 for r in _REGIMES}
    for s, v in enumerate(vec):
        out[state_map.get(s, R_CHOP)] += float(v)
    tot = sum(out.values())
    if tot > 0:
        out = {k: v / tot for k, v in out.items()}
    return out


# ---------------------------------------------------------------------------
# Market / sector regime — yfinance + daily file cache
# ---------------------------------------------------------------------------
_MARKET_INDEX = {
    "KR": "^KS11",
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "US": "^GSPC",
}


def _cache_path(key: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    day = dt.date.today().isoformat()
    return os.path.join(_CACHE_DIR, f"{safe}_{day}.json")


def _read_cache(key: str) -> Optional[RegimeResult]:
    p = _cache_path(key)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return RegimeResult(**d)
    except Exception as e:  # noqa: BLE001
        log.debug("cache read failed %s: %s", key, e)
    return None


def _write_cache(key: str, res: RegimeResult) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.debug("cache write failed %s: %s", key, e)


def _fetch_daily(ticker: str, years: int = 3) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        period = f"{max(1, years)}y"
        h = yf.Ticker(ticker).history(period=period, interval="1d")
        if h is None or len(h) == 0:
            return None
        return h
    except Exception as e:  # noqa: BLE001
        log.warning("daily fetch failed %s: %s", ticker, e)
        return None


def get_market_regime(market: str) -> RegimeResult:
    """Market-level regime for ^KS11 (KR) / ^GSPC (US). File-cached once per day."""
    mkt = (market or "KR").upper()
    ticker = _MARKET_INDEX.get(mkt, "^KS11")
    key = f"market_{mkt}_{ticker}"
    cached = _read_cache(key)
    if cached is not None:
        return cached

    ohlcv = _fetch_daily(ticker, years=3)
    if ohlcv is None or len(ohlcv) == 0:
        res = RegimeResult(state=R_CHOP, probs={r: 1/3 for r in _REGIMES},
                           p_next={r: 1/3 for r in _REGIMES},
                           transition_signal=_empty_signal(), model_status="rule_based")
        return res
    res = classify_regime(ohlcv)
    _write_cache(key, res)
    return res


# sector_key -> yfinance index/ETF proxy. Empty → fall back to market regime.
_SECTOR_INDEX: Dict[str, Dict[str, str]] = {
    "US": {
        "tech": "XLK", "semi": "SMH", "energy": "XLE", "bio": "XBI",
        "defense": "ITA", "ev": "LIT", "nuclear": "URA", "financials": "XLF",
        "healthcare": "XLV", "industrials": "XLI",
    },
    "KR": {
        # KR sector indices are sparse on yfinance; default to market fallback.
    },
}


def get_sector_regime(sector_key: str, market: str) -> RegimeResult:
    """Sector regime via a sector index/ETF proxy. Falls back to market regime."""
    mkt = (market or "KR").upper()
    proxy = _SECTOR_INDEX.get(mkt, {}).get((sector_key or "").lower())
    if not proxy:
        return get_market_regime(mkt)

    key = f"sector_{mkt}_{sector_key}_{proxy}"
    cached = _read_cache(key)
    if cached is not None:
        return cached

    ohlcv = _fetch_daily(proxy, years=3)
    if ohlcv is None or len(ohlcv) == 0:
        return get_market_regime(mkt)
    res = classify_regime(ohlcv)
    _write_cache(key, res)
    return res


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    rng = np.random.default_rng(0)
    n = 800
    up = np.cumsum(rng.normal(0.001, 0.008, n // 3))
    dn = up[-1] + np.cumsum(rng.normal(-0.002, 0.03, n // 3))
    fl = dn[-1] + np.cumsum(rng.normal(0.0, 0.006, n - 2 * (n // 3)))
    price = np.exp(np.concatenate([up, dn, fl]))
    idx = pd.date_range("2021-01-01", periods=len(price), freq="B")
    df = pd.DataFrame({"Close": price, "High": price * 1.01,
                       "Low": price * 0.99, "Volume": 1e6}, index=idx)
    r = classify_regime(df)
    print("state:", r.state, "status:", r.model_status)
    print("probs:", {k: round(v, 3) for k, v in r.probs.items()})
    print("p_next:", {k: round(v, 3) for k, v in r.p_next.items()})
    print("signal:", r.transition_signal)
