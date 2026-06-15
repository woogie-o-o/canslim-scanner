# -*- coding: utf-8 -*-
"""
cross_market_lead.py — 모듈3: US→KR 크로스마켓 리드래그 전이 신호

미국 섹터 ETF의 직전 일봉 마감(T, 05:00 KST)이 같은 캘린더 날짜의 한국 개장을
약 4시간 선행한다. 이 모듈은 US 바스켓을 1회 배치로 받아 테마별 0..1 전이 점수
(transfer)를 계산하고, 다음 KR 세션에 대해 1세션만 유효한 nudge로 캐시한다.

설계 원칙 (macro.py / etf.py 와 동일):
  - yfinance 1회 배치 다운로드 → 한 호출로 US 바스켓 전부 수집
  - 절대 예외를 밖으로 던지지 않는다 — fetch 실패/비발화 시 전부 neutral 0.5
  - 일 1회 파일 캐시 (regime_cache/) — 스캔 성능 영향 최소
  - 스캐너를 게이팅하지 않고 nudge만 한다 (US→KR 방향, KR 스캔에만 적용)

연구 스펙: .kkirikkiri/research/leadlag_spec.md (§3 공식, §5 설정) 충실 구현.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

_LOG = logging.getLogger("cross_market_lead")

_KST = timezone(timedelta(hours=9))

# ── 캐시 디렉터리 (프로젝트 루트/regime_cache) ──────────────────────────
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime_cache")


# =====================================================================
# 연구 스펙 §5 — LEADLAG_CONFIG (verbatim)
# =====================================================================
LEADLAG_CONFIG: dict[str, Any] = {
    # ── US tickers (one yfinance batch; reuse macro.py/etf.py) ──
    "us_tickers": [
        "SPY", "QQQ",                 # baselines (RS denominator)
        "SMH", "SOXX", "XLK",         # semis + big-tech/AI
        "LIT", "XLE",                 # EV/battery, energy
        "ITA", "XBI", "URA",          # defense, biotech, nuclear
        "ARKK",                       # risk-appetite multiplier
        "^VIX",                       # volatility confirm gate
    ],

    # ── US sector → KR theme map (names match theme_stocks.txt) ──
    "us_kr_map": {
        "반도체/HBM":       {"us": ["SMH", "SOXX"], "confirm_vix": True},
        "AI 반도체/인프라":  {"us": ["SMH", "XLK"],  "confirm_vix": True},
        "AI SW/플랫폼":      {"us": ["XLK", "QQQ"],  "confirm_vix": True},
        "EV/모빌리티":       {"us": ["LIT", "XLE"],  "confirm_vix": True},
        "방산/우주":         {"us": ["ITA"],         "confirm_vix": False},
        "바이오":            {"us": ["XBI"],         "confirm_vix": False},
        "원자력/SMR":        {"us": ["URA"],         "confirm_vix": False},
    },
    # ARKK feeds a global risk multiplier on speculative_themes.py
    "risk_appetite_source": "ARKK",
    "risk_appetite_themes": ["밈/리테일 화제", "양자컴퓨팅", "크립토/비트코인 마이너"],

    # ── Signal computation ──
    "trailing_window": 60,            # trading days for z-scores / decile
    "decile_threshold_z": 1.3,        # |rs_z| must exceed to fire (≈ top/bottom decile)
    "require_confirm": True,          # extreme RS AND (vol_z>=1 OR cnh>=0.7)
    "vol_z_confirm": 1.0,
    "cnh_confirm": 0.70,

    # ── Strength weights (rs dominates) ──
    "weights": {"rs": 0.30, "vol": 0.15, "cnh": 0.05, "base": 0.50},

    # ── VIX confirmation gate ──
    "vix_bands": {"calm": 16, "normal": 22, "stressed": 30},  # >30 = panic
    "vix_gate": {"falling": 1.0, "mild_rise": 0.6, "spike": 0.3, "panic_mult": 0.5},
    "vix_mild_rise_max": 1.0,         # vix_chg <= +1.0 → mild

    # ── Decay / validity ──
    "validity_sessions": 1,           # valid only for next KR session
    "decay": "hard",                  # expire at next KR close
    "neutral_score": 0.5,             # no-fire / expired default
    "kr_holiday_calendar": "KRX",     # roll target date forward over holidays
}

# 캘린더가 없을 때 weekday 폴백으로도 걸러줄 알려진 KRX 휴장일 (best-effort, 미완전 가능).
_KRX_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-03-03",
    "2025-05-05", "2025-05-06", "2025-06-06", "2025-08-15", "2025-10-03",
    "2025-10-06", "2025-10-07", "2025-10-08", "2025-10-09", "2025-12-25",
    "2025-12-31",
    # 2026
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02",
    "2026-05-05", "2026-05-25", "2026-06-06", "2026-08-17", "2026-09-24",
    "2026-09-25", "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31",
}


# =====================================================================
# 소형 헬퍼 (numpy 불필요 — 순수 파이썬, 폴백 안전)
# =====================================================================
def _clip(x: float, lo: float, hi: float) -> float:
    if x != x:  # NaN
        return lo
    return lo if x < lo else (hi if x > hi else x)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


# =====================================================================
# 캘린더 정렬 (연구 스펙 §3)
# =====================================================================
def _is_kr_holiday(d: date) -> bool:
    return d.strftime("%Y-%m-%d") in _KRX_HOLIDAYS


def _is_kr_trading_day(d: date) -> bool:
    # 평일(월~금) AND 비휴장일
    return d.weekday() < 5 and not _is_kr_holiday(d)


def _roll_forward_kr(d: date) -> date:
    """주어진 날짜가 KR 거래일이 아니면(주말/휴장) 다음 거래일로 roll-forward."""
    cur = d
    for _ in range(14):  # 안전 상한
        if _is_kr_trading_day(cur):
            return cur
        cur = cur + timedelta(days=1)
    return cur


def kr_target_date(us_session_date: date) -> date:
    """
    US 세션 날짜 T (05:00 KST 마감) → KR 타깃 세션 날짜.

    - US 월~목 마감 → KR 같은 캘린더 날짜 개장.
    - US 금 마감(토 05:00 KST) → 다음 KR 거래일 = 월.
    - KR 휴장일이면 다음 거래일로 roll-forward (weekday 폴백 포함).
    """
    if us_session_date.weekday() == 4:  # 금요일 → 같은 날짜는 토요일 → roll → 월
        target = us_session_date + timedelta(days=1)  # 토요일부터 roll
    else:
        target = us_session_date
    return _roll_forward_kr(target)


# =====================================================================
# US 바스켓 fetch (1회 yfinance 배치)
# =====================================================================
def _fetch_us_basket(tickers: list[str], *, period: str = "90d") -> dict[str, dict[str, list]]:
    """
    yfinance 1회 배치 다운로드 → {ticker: {"close":[...], "high":[...], "low":[...],
    "open":[...], "volume":[...], "dates":[date,...]}}.

    실패 시 예외를 던진다(상위 compute_leadlag 가 삼켜 neutral 처리).
    """
    import yfinance as yf  # 지연 import — 모듈 import 시 yfinance 부재해도 안전

    df = yf.download(
        tickers, period=period, interval="1d",
        progress=False, group_by="ticker", threads=True,
        auto_adjust=False,
    )
    out: dict[str, dict[str, list]] = {}
    has_multi = hasattr(df.columns, "get_level_values")
    level0 = set(df.columns.get_level_values(0)) if has_multi else set()

    for tk in tickers:
        try:
            if has_multi:
                if tk not in level0:
                    continue
                sub = df[tk]
            else:
                sub = df  # 단일 티커일 때 (실사용에선 항상 다종목)
            rec = _extract_series(sub)
            if rec is not None:
                out[tk] = rec
        except Exception as e:  # noqa: BLE001 — per-ticker 격리
            _LOG.debug("leadlag: ticker %s extract failed: %s", tk, e)
            continue
    return out


def _extract_series(sub) -> dict[str, list] | None:
    """yfinance 종목 서브프레임 → 시리즈 dict. NaN 행 제거. 데이터 부족 시 None."""
    try:
        closes = sub["Close"].dropna()
        if len(closes) < 5:
            return None
        idx = closes.index
        def col(name: str) -> list[float]:
            s = sub[name].reindex(idx)
            return [float(v) for v in s.tolist()]
        dates = []
        for ts in idx:
            try:
                dates.append(ts.date())
            except Exception:  # noqa: BLE001
                dates.append(None)
        return {
            "close": [float(v) for v in closes.tolist()],
            "high": col("High"),
            "low": col("Low"),
            "open": col("Open"),
            "volume": col("Volume"),
            "dates": dates,
        }
    except Exception:  # noqa: BLE001
        return None


# =====================================================================
# Per-ETF 신호 (연구 스펙 §1, §3 Step1)
# =====================================================================
def _returns_1d(close: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(close)):
        prev = close[i - 1]
        out.append((close[i] - prev) / prev if prev else 0.0)
    return out


def _etf_signals(rec: dict[str, list], spy_ret: list[float], window: int) -> dict | None:
    """
    한 ETF 의 최신 일봉 신호 산출:
      rs_z = z-score over window of (etf_ret_1d - spy_ret_1d)
      vol_z = (vol - mean60) / std60
      cnh  = (C - L) / (H - L)
    spy_ret 은 SPY 의 일별 수익률 리스트(_returns_1d 결과)와 정렬되어야 한다.
    """
    close = rec.get("close") or []
    if len(close) < window + 2:
        # 데이터가 짧으면 가용 범위로 best-effort (그래도 부족하면 None)
        if len(close) < 8:
            return None

    etf_ret = _returns_1d(close)
    # SPY 수익률과 길이 정렬 (tail 기준 — 같은 날짜로 맞추기 위해 뒤에서 자른다)
    n = min(len(etf_ret), len(spy_ret))
    if n < 5:
        return None
    e = etf_ret[-n:]
    s = spy_ret[-n:]
    rs_series = [e[i] - s[i] for i in range(n)]

    w = min(window, len(rs_series))
    rs_win = rs_series[-w:]
    mu = _mean(rs_win)
    sd = _std(rs_win)
    rs_latest = rs_series[-1]
    rs_z = (rs_latest - mu) / sd if sd > 0 else 0.0

    # 거래량 z-score
    vol = rec.get("volume") or []
    vol_z = 0.0
    if len(vol) >= 2:
        wv = min(window, len(vol) - 1)
        vol_win = vol[-(wv + 1):-1]  # 직전 window (당일 제외)
        if len(vol_win) >= 2:
            vmu = _mean(vol_win)
            vsd = _std(vol_win)
            vol_z = (vol[-1] - vmu) / vsd if vsd > 0 else 0.0

    # 종가위치 cnh = (C - L) / (H - L)
    hi = (rec.get("high") or [0.0])[-1]
    lo = (rec.get("low") or [0.0])[-1]
    c = close[-1]
    rng = hi - lo
    cnh = (c - lo) / rng if rng > 0 else 0.5

    return {"rs_z": rs_z, "vol_z": vol_z, "cnh": cnh}


def _strength_from_signals(sig: dict, weights: dict) -> float:
    """연구 스펙 §3 Step1 — per-ETF raw strength 0..1."""
    rs_z = sig["rs_z"]
    vol_z = sig["vol_z"]
    cnh = sig["cnh"]
    strength = (
        weights["base"]
        + weights["rs"] * _clip(rs_z / 2.0, -1.0, 1.0)
        + weights["vol"] * _clip(vol_z / 2.0, -1.0, 1.0)
        + weights["cnh"] * (cnh - 0.5) * 2.0
    )
    return _clip(strength, 0.0, 1.0)


# =====================================================================
# VIX 게이트 (연구 스펙 §3 Step2)
# =====================================================================
def _vix_state(vix_rec: dict | None) -> tuple[float, float]:
    """VIX rec → (vix_level, vix_chg). 없으면 (calm-ish, 0)."""
    if not vix_rec:
        return (15.0, 0.0)
    close = vix_rec.get("close") or []
    if len(close) < 2:
        lvl = close[-1] if close else 15.0
        return (float(lvl), 0.0)
    return (float(close[-1]), float(close[-1] - close[-2]))


def _vix_gate(vix_level: float, vix_chg: float, cfg: dict) -> float:
    g = cfg["vix_gate"]
    if vix_chg <= 0:
        gate = g["falling"]
    elif vix_chg <= cfg["vix_mild_rise_max"]:
        gate = g["mild_rise"]
    else:
        gate = g["spike"]
    if vix_level > cfg["vix_bands"]["stressed"]:  # >30 panic
        gate *= g["panic_mult"]
    return gate


def _vix_spiking(vix_chg: float, cfg: dict) -> bool:
    """이중확인용: VIX 급등 여부 (mild_rise_max 초과면 spiking)."""
    return vix_chg > cfg["vix_mild_rise_max"]


# =====================================================================
# 테마별 전이 점수 (연구 스펙 §3, §4)
# =====================================================================
def _theme_transfer(
    mapping: dict,
    signals_by_etf: dict[str, dict],
    vix_level: float,
    vix_chg: float,
    cfg: dict,
) -> dict:
    """
    한 테마의 매핑된 ETF 신호를 집계 → {transfer, fired, direction}.
    데실 게이트 + (매크로 테마) 이중확인 + VIX 게이트 적용.
    """
    neutral = {"transfer": cfg["neutral_score"], "fired": False, "direction": 0}
    us_list = mapping.get("us") or []
    confirm_vix = bool(mapping.get("confirm_vix", False))

    etf_sigs = [signals_by_etf[t] for t in us_list if t in signals_by_etf]
    if not etf_sigs:
        return dict(neutral)

    # 평균 strength
    strengths = [_strength_from_signals(s, cfg["weights"]) for s in etf_sigs]
    avg_strength = _mean(strengths)

    # 데실 발화 게이트: 매핑된 ETF 중 |rs_z| >= 임계가 하나라도 있어야 발화.
    thr = cfg["decile_threshold_z"]
    extreme = [s for s in etf_sigs if abs(s["rs_z"]) >= thr]
    if not extreme:
        return dict(neutral)

    # 발화 방향: 가장 극단적인 rs_z 의 부호
    lead = max(etf_sigs, key=lambda s: abs(s["rs_z"]))

    # 매크로 민감 테마 이중확인: 극단 rs_z AND (vol_z>=1 OR cnh>=0.7) AND VIX not spiking
    if confirm_vix and cfg.get("require_confirm", True):
        conf_ok = any(
            (s["vol_z"] >= cfg["vol_z_confirm"] or s["cnh"] >= cfg["cnh_confirm"])
            for s in extreme
        )
        if not conf_ok or _vix_spiking(vix_chg, cfg):
            return dict(neutral)

    # VIX 게이트 (confirm_vix 테마에만 적용)
    gate = _vix_gate(vix_level, vix_chg, cfg) if confirm_vix else 1.0

    transfer = 0.5 + (avg_strength - 0.5) * gate
    transfer = _clip(transfer, 0.0, 1.0)

    direction = _sign(transfer - 0.5)
    return {"transfer": transfer, "fired": True, "direction": direction}


# =====================================================================
# 캐시 I/O
# =====================================================================
def _cache_path(day: str) -> str:
    return os.path.join(_CACHE_DIR, f"leadlag_{day}.json")


def _today_kst_str() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _read_cache(day: str) -> dict | None:
    try:
        path = _cache_path(day)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        _LOG.debug("leadlag: cache read failed: %s", e)
        return None


def _write_cache(day: str, payload: dict) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(day)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        _LOG.debug("leadlag: cache write failed: %s", e)


def _neutral_all(cfg: dict) -> dict:
    """모든 매핑 테마에 대해 neutral 0.5 결과."""
    n = cfg["neutral_score"]
    out: dict[str, dict] = {}
    themes = list(cfg["us_kr_map"].keys()) + list(cfg.get("risk_appetite_themes") or [])
    for th in themes:
        out[th] = {
            "transfer": n, "target_kr_date": "", "fired": False, "direction": 0,
        }
    return out


# =====================================================================
# 공개 API
# =====================================================================
def compute_leadlag(*, config: dict | None = None, _basket: dict | None = None) -> dict:
    """
    US 바스켓 → 테마별 전이 신호 산출. 일 1회 파일 캐시 (regime_cache/).

    반환: { kr_theme: {transfer: 0..1, target_kr_date: str,
                       fired: bool, direction: int(-1/0/1)} }

    _basket: 테스트 주입용(이미 fetch 된 시리즈 dict). None 이면 yfinance fetch.
    절대 예외를 던지지 않는다 — 실패 시 전부 neutral 0.5.
    """
    cfg = config or LEADLAG_CONFIG
    day = _today_kst_str()

    # 1) 캐시 우선 (실주입 _basket 이 없을 때만 캐시 사용)
    if _basket is None:
        cached = _read_cache(day)
        if cached is not None:
            return cached

    # 2) fetch (never throw)
    try:
        basket = _basket if _basket is not None else _fetch_us_basket(
            cfg["us_tickers"], period="90d"
        )
        if not basket or "SPY" not in basket:
            result = _neutral_all(cfg)
            if _basket is None:
                _write_cache(day, result)
            return result

        result = _compute_from_basket(basket, cfg)
    except Exception as e:  # noqa: BLE001 — never throw into caller
        _LOG.warning("leadlag: compute failed, neutral fallback: %s", e)
        result = _neutral_all(cfg)

    if _basket is None:
        _write_cache(day, result)
    return result


def _compute_from_basket(basket: dict[str, dict], cfg: dict) -> dict:
    """fetch 된 바스켓 → 테마별 결과 dict (캘린더 스탬프 포함)."""
    window = cfg["trailing_window"]

    spy = basket.get("SPY")
    if not spy:
        return _neutral_all(cfg)
    spy_ret = _returns_1d(spy.get("close") or [])
    if len(spy_ret) < 5:
        return _neutral_all(cfg)

    # US 세션 날짜 T = SPY 마지막 바의 날짜
    us_dates = spy.get("dates") or []
    us_t = None
    for d in reversed(us_dates):
        if isinstance(d, date):
            us_t = d
            break
    if us_t is None:
        us_t = datetime.now(_KST).date()
    target = kr_target_date(us_t)
    target_str = target.strftime("%Y-%m-%d")

    # 모든 ETF 신호 산출
    signals_by_etf: dict[str, dict] = {}
    for tk, rec in basket.items():
        if tk in ("^VIX", "SPY"):
            continue
        sig = _etf_signals(rec, spy_ret, window)
        if sig is not None:
            signals_by_etf[tk] = sig

    vix_level, vix_chg = _vix_state(basket.get("^VIX"))

    out: dict[str, dict] = {}

    # 섹터 테마
    for theme, mapping in cfg["us_kr_map"].items():
        res = _theme_transfer(mapping, signals_by_etf, vix_level, vix_chg, cfg)
        res["target_kr_date"] = target_str
        out[theme] = res

    # ARKK 리스크-식욕 승수 → speculative 테마 (idiosyncratic, confirm_vix=False 취급)
    src = cfg.get("risk_appetite_source")
    risk_themes = cfg.get("risk_appetite_themes") or []
    arkk_map = {"us": [src] if src else [], "confirm_vix": False}
    for theme in risk_themes:
        res = _theme_transfer(arkk_map, signals_by_etf, vix_level, vix_chg, cfg)
        res["target_kr_date"] = target_str
        out[theme] = res

    return out


def leadlag_for_theme(theme: str, *, config: dict | None = None) -> dict:
    """
    단일 테마 조회. 테마 부재/만료 시 neutral {transfer:0.5, fired:False, direction:0}.

    만료 판정: target_kr_date 가 오늘(KST) 이전이면 1세션 hard decay 로 만료.
    """
    cfg = config or LEADLAG_CONFIG
    neutral = {"transfer": cfg["neutral_score"], "fired": False, "direction": 0,
               "target_kr_date": ""}
    try:
        data = compute_leadlag(config=cfg)
        entry = data.get(theme)
        if not entry:
            return dict(neutral)

        # 만료 검사: target_kr_date < 오늘(KST) → 만료(neutral)
        tgt = entry.get("target_kr_date") or ""
        if tgt:
            try:
                tgt_d = datetime.strptime(tgt, "%Y-%m-%d").date()
                today = datetime.now(_KST).date()
                if tgt_d < today:
                    return dict(neutral)
            except Exception:  # noqa: BLE001
                pass
        return dict(entry)
    except Exception as e:  # noqa: BLE001 — never throw
        _LOG.debug("leadlag_for_theme(%s) failed: %s", theme, e)
        return dict(neutral)
