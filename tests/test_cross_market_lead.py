# -*- coding: utf-8 -*-
"""
test_cross_market_lead.py — 모듈3 크로스마켓 리드래그 단위 테스트

네트워크 미접속 — 합성 가격 시리즈를 _basket 으로 주입하거나 fetch 를
monkeypatch 한다. 데실 게이트 / VIX 게이트 / 캘린더 roll / 견고성 검증.

실행: py -3.13 -m pytest tests/test_cross_market_lead.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cross_market_lead as cml  # noqa: E402


# =====================================================================
# 합성 시리즈 빌더
# =====================================================================
def _flat_series(n: int = 90, price: float = 100.0, vol: float = 1_000_000.0):
    """변동 없는 평탄한 시리즈 (rs≈0, vol_z≈0, cnh=0.5)."""
    closes = [price] * n
    return {
        "close": closes,
        "open": closes[:],
        "high": [p * 1.001 for p in closes],
        "low": [p * 0.999 for p in closes],
        "volume": [vol] * n,
        "dates": _dates(n),
    }


def _dates(n: int, last: date = date(2026, 6, 8)):
    # 단순 연속 영업일 근사 — 테스트에선 날짜 값 자체가 중요치 않은 경우가 대부분.
    out = []
    d = last
    for _ in range(n):
        out.append(d)
        d = date.fromordinal(d.toordinal() - 1)
    out.reverse()
    return out


def _spy(n: int = 90):
    """SPY: 완전 평탄 (일별 수익률 0 → RS 분모). rs_series 가 깨끗이 0."""
    closes = [100.0] * n
    return {
        "close": closes,
        "open": closes[:],
        "high": [p * 1.002 for p in closes],
        "low": [p * 0.998 for p in closes],
        "volume": [1_000_000.0] * n,
        "dates": _dates(n),
    }


def _strong_etf(n: int = 90, spike: float = 0.06, strong_close: bool = True,
                vol_spike: float = 5.0):
    """
    마지막 날 SPY 대비 큰 초과수익(spike) → 높은 rs_z.
    strong_close → cnh≈1.0, vol_spike → 높은 vol_z.
    """
    closes = [100.0 * (1.001 ** i) for i in range(n)]
    closes[-1] = closes[-2] * (1.0 + spike)  # 마지막 날 급등
    hi = [p * 1.002 for p in closes]
    lo = [p * 0.998 for p in closes]
    if strong_close:
        # 마지막 봉: 종가가 고가에 붙음 → cnh≈1
        lo[-1] = closes[-1] * 0.95
        hi[-1] = closes[-1] * 1.0005
    vol = [1_000_000.0] * n
    if vol_spike:
        vol[-1] = 1_000_000.0 * vol_spike
    return {
        "close": closes,
        "open": closes[:],
        "high": hi,
        "low": lo,
        "volume": vol,
        "dates": _dates(n),
    }


def _vix(level: float, chg: float, n: int = 90):
    closes = [16.0] * n
    closes[-2] = level - chg
    closes[-1] = level
    return {
        "close": closes,
        "open": closes[:],
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "volume": [0.0] * n,
        "dates": _dates(n),
    }


def _basket(**etfs):
    """기본 평탄 바스켓 + 오버라이드."""
    cfg = cml.LEADLAG_CONFIG
    b = {"SPY": _spy()}
    for tk in cfg["us_tickers"]:
        if tk in ("SPY", "^VIX"):
            continue
        b[tk] = _flat_series()
    b["^VIX"] = _vix(15.0, -0.5)  # falling, calm
    b.update(etfs)
    return b


# =====================================================================
# 1. 데실 게이트
# =====================================================================
def test_decile_gate_below_threshold_neutral():
    """평탄 바스켓(rs_z≈0) → 모든 테마 neutral 0.5, fired False."""
    res = cml.compute_leadlag(_basket=_basket())
    for theme, r in res.items():
        assert r["fired"] is False, f"{theme} should not fire"
        assert abs(r["transfer"] - 0.5) < 1e-9, f"{theme} transfer != 0.5"
        assert r["direction"] == 0


def test_decile_gate_extreme_fires_bullish():
    """반도체(SMH/SOXX) 강한 초과수익 + 확인 → transfer>0.5, fired True, dir=1."""
    b = _basket(
        SMH=_strong_etf(spike=0.07, strong_close=True, vol_spike=6.0),
        SOXX=_strong_etf(spike=0.07, strong_close=True, vol_spike=6.0),
    )
    b["^VIX"] = _vix(14.0, -1.0)  # falling → full gate
    res = cml.compute_leadlag(_basket=b)
    semi = res["반도체/HBM"]
    assert semi["fired"] is True
    assert semi["transfer"] > 0.5
    assert semi["direction"] == 1


def test_decile_gate_macro_theme_requires_confirmation():
    """매크로 테마: 극단 rs_z 지만 거래량/종가 확인 실패 → neutral."""
    # 급등은 있으나 vol_spike 없음 + 종가 약함(cnh 낮음)
    weak = _strong_etf(spike=0.07, strong_close=False, vol_spike=0.0)
    # 마지막 봉 종가를 저가권으로 → cnh 낮게
    weak["low"][-1] = weak["close"][-1] * 0.999
    weak["high"][-1] = weak["close"][-1] * 1.05
    b = _basket(SMH=weak, SOXX=weak)
    b["^VIX"] = _vix(14.0, -1.0)
    res = cml.compute_leadlag(_basket=b)
    assert res["반도체/HBM"]["fired"] is False
    assert abs(res["반도체/HBM"]["transfer"] - 0.5) < 1e-9


def test_idiosyncratic_theme_fires_without_vix_confirm():
    """방산(ITA, confirm_vix=False): 극단 rs_z 만으로 발화 (이중확인 불필요)."""
    b = _basket(ITA=_strong_etf(spike=0.07, strong_close=False, vol_spike=0.0))
    res = cml.compute_leadlag(_basket=b)
    assert res["방산/우주"]["fired"] is True
    assert res["방산/우주"]["transfer"] > 0.5


# =====================================================================
# 2. VIX 게이트
# =====================================================================
def test_vix_falling_full_strength():
    """VIX 하락 → 게이트 1.0 (full transfer)."""
    b = _basket(
        SMH=_strong_etf(spike=0.07, vol_spike=6.0),
        SOXX=_strong_etf(spike=0.07, vol_spike=6.0),
    )
    b["^VIX"] = _vix(14.0, -1.0)
    full = cml.compute_leadlag(_basket=b)["반도체/HBM"]["transfer"]

    b2 = _basket(
        SMH=_strong_etf(spike=0.07, vol_spike=6.0),
        SOXX=_strong_etf(spike=0.07, vol_spike=6.0),
    )
    b2["^VIX"] = _vix(20.0, 0.6)  # mild rise → 0.6 gate
    mild = cml.compute_leadlag(_basket=b2)["반도체/HBM"]["transfer"]

    assert full > mild > 0.5, f"full={full} mild={mild}"


def test_vix_spike_damps_macro_theme_to_neutral():
    """VIX 급등 → 매크로 테마 이중확인 차단 → neutral (carry 안됨)."""
    b = _basket(
        SMH=_strong_etf(spike=0.07, vol_spike=6.0),
        SOXX=_strong_etf(spike=0.07, vol_spike=6.0),
    )
    b["^VIX"] = _vix(28.0, 4.0)  # spike, vix not >30 but chg large
    res = cml.compute_leadlag(_basket=b)["반도체/HBM"]
    assert res["fired"] is False
    assert abs(res["transfer"] - 0.5) < 1e-9


def test_vix_panic_caps_idiosyncratic():
    """idiosyncratic 테마는 VIX 게이트 미적용 → 급등 VIX 에도 발화 유지."""
    # 방산은 confirm_vix=False → VIX 무관하게 rs 만으로 발화
    b = _basket(ITA=_strong_etf(spike=0.08, vol_spike=6.0))
    b["^VIX"] = _vix(35.0, 8.0)  # panic
    res = cml.compute_leadlag(_basket=b)["방산/우주"]
    assert res["fired"] is True
    assert res["transfer"] > 0.5


# =====================================================================
# 3. 캘린더 정렬
# =====================================================================
def test_calendar_friday_maps_to_monday():
    """US 금요일 → 다음 KR 거래일 = 월요일."""
    fri = date(2026, 6, 5)  # 금요일
    tgt = cml.kr_target_date(fri)
    assert tgt.weekday() == 0, f"expected Monday, got {tgt} (wd={tgt.weekday()})"
    assert tgt == date(2026, 6, 8)


def test_calendar_weekday_maps_same_day():
    """US 화요일 → KR 같은 캘린더 날짜 (거래일)."""
    tue = date(2026, 6, 9)  # 화요일 (비휴장)
    tgt = cml.kr_target_date(tue)
    assert tgt == tue


def test_calendar_rolls_over_kr_holiday():
    """타깃이 KR 휴장일이면 다음 거래일로 roll-forward."""
    # 2026-06-05(금) → 토 roll → 6/8(월)은 거래일. 휴장일 케이스 검증:
    # 2026-06-05 직전, US 목 6/4 → KR 금 6/5 (거래일). 휴일 가정 추가 검증.
    # 광복절 대체/현충일 등 _KRX_HOLIDAYS 사용: 6/6(현충일, 토) 부근.
    # US 금 6/19 마감 → 토 roll. 6/22(월) 거래일 가정.
    us_fri = date(2026, 6, 19)
    tgt = cml.kr_target_date(us_fri)
    assert cml._is_kr_trading_day(tgt)
    assert tgt.weekday() == 0


def test_expired_signal_returns_neutral(monkeypatch, tmp_path):
    """target_kr_date 가 과거면 leadlag_for_theme 가 neutral 반환."""
    stale = {
        "반도체/HBM": {
            "transfer": 0.8, "target_kr_date": "2020-01-01",
            "fired": True, "direction": 1,
        }
    }
    monkeypatch.setattr(cml, "compute_leadlag", lambda **kw: stale)
    r = cml.leadlag_for_theme("반도체/HBM")
    assert r["fired"] is False
    assert abs(r["transfer"] - 0.5) < 1e-9
    assert r["direction"] == 0


def test_absent_theme_returns_neutral(monkeypatch):
    monkeypatch.setattr(cml, "compute_leadlag", lambda **kw: {})
    r = cml.leadlag_for_theme("존재하지않는테마")
    assert r["fired"] is False
    assert abs(r["transfer"] - 0.5) < 1e-9


# =====================================================================
# 4. 견고성
# =====================================================================
def test_fetch_failure_all_neutral_no_exception(monkeypatch):
    """fetch 가 예외를 던져도 전부 neutral, 예외 전파 없음."""
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(cml, "_fetch_us_basket", _boom)
    # 캐시 우회를 위해 오늘 캐시 파일이 없다고 가정 — _read_cache 도 None 강제
    monkeypatch.setattr(cml, "_read_cache", lambda day: None)
    written = {}
    monkeypatch.setattr(cml, "_write_cache", lambda day, payload: written.update(payload))

    res = cml.compute_leadlag()
    assert res, "should return populated neutral dict"
    for theme, r in res.items():
        assert r["fired"] is False
        assert abs(r["transfer"] - 0.5) < 1e-9
        assert r["direction"] == 0
    # 모든 매핑 테마 + 리스크 테마 포함
    for th in cml.LEADLAG_CONFIG["us_kr_map"]:
        assert th in res


def test_empty_basket_neutral(monkeypatch):
    """SPY 없는 빈 바스켓 → neutral."""
    monkeypatch.setattr(cml, "_read_cache", lambda day: None)
    monkeypatch.setattr(cml, "_write_cache", lambda day, p: None)
    res = cml.compute_leadlag(_basket={})
    for r in res.values():
        assert r["fired"] is False
        assert abs(r["transfer"] - 0.5) < 1e-9


def test_config_is_spec_verbatim():
    """LEADLAG_CONFIG 핵심 키 존재 + 값 검증 (스펙 §5)."""
    c = cml.LEADLAG_CONFIG
    assert c["trailing_window"] == 60
    assert c["decile_threshold_z"] == 1.3
    assert c["vol_z_confirm"] == 1.0
    assert c["cnh_confirm"] == 0.70
    assert c["weights"] == {"rs": 0.30, "vol": 0.15, "cnh": 0.05, "base": 0.50}
    assert c["vix_bands"] == {"calm": 16, "normal": 22, "stressed": 30}
    assert c["risk_appetite_source"] == "ARKK"
    assert "^VIX" in c["us_tickers"]
    assert set(c["us_kr_map"]) == {
        "반도체/HBM", "AI 반도체/인프라", "AI SW/플랫폼",
        "EV/모빌리티", "방산/우주", "바이오", "원자력/SMR",
    }


def test_direction_sign_consistency():
    """fired 시 direction == sign(transfer-0.5)."""
    b = _basket(ITA=_strong_etf(spike=0.08, vol_spike=6.0))
    res = cml.compute_leadlag(_basket=b)
    for r in res.values():
        if r["fired"]:
            exp = 1 if r["transfer"] > 0.5 else (-1 if r["transfer"] < 0.5 else 0)
            assert r["direction"] == exp


def test_bearish_extreme_fires_negative():
    """강한 음의 초과수익 → transfer<0.5, direction=-1 (대칭성)."""
    b = _basket(ITA=_strong_etf(spike=-0.08, strong_close=False, vol_spike=6.0))
    res = cml.compute_leadlag(_basket=b)["방산/우주"]
    assert res["fired"] is True
    assert res["transfer"] < 0.5
    assert res["direction"] == -1
