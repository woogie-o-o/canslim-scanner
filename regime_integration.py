# -*- coding: utf-8 -*-
"""스캐너 통합 + 동적 가중치 (모듈4).

engine_adapter.py 의 `_attach_*` 체인에 꽂는 어댑터 함수 모음.
모듈1(regime_classifier) · 모듈2(order_flow) · 모듈3(cross_market_lead)의
공개 API를 호출해 스캔 row에 신규 필드를 부착하고,
레짐 전환확률을 기존 점수에 곱해 RegimeEntryScore를 산출한다.

설계 원칙(REGIME_SPEC.md §5,§6):
- 기존 TotalScore/EntryScore는 변경하지 않는다 — 신규 필드만 부착.
- 정렬 전환은 env REGIME_RANK=1 일 때만 → 켜기 전 동작 100% 보존.
- 모든 함수는 예외를 삼키고 안전 폴백(never throw into scan loop).
- 레짐은 시장/섹터 레벨 1회 계산 후 배치 내 memoize → 종목별 HMM 금지.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── optional module isolation ──────────────────────────────────────────────
try:
    import regime_classifier as _rc
    _HAS_RC = True
except Exception as _e:  # pragma: no cover
    _rc = None  # type: ignore
    _HAS_RC = False
    log.info("regime_classifier unavailable: %s", _e)

try:
    import order_flow as _of
    _HAS_OF = True
except Exception as _e:  # pragma: no cover
    _of = None  # type: ignore
    _HAS_OF = False
    log.info("order_flow unavailable: %s", _e)

try:
    import cross_market_lead as _ll
    _HAS_LL = True
except Exception as _e:  # pragma: no cover
    _ll = None  # type: ignore
    _HAS_LL = False
    log.info("cross_market_lead unavailable: %s", _e)


# ── 설정 (env override 가능) ────────────────────────────────────────────────
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


W_REGIME = _envf("W_REGIME", 0.30)
W_OFI = _envf("W_OFI", 0.12)
W_LEADLAG = _envf("W_LEADLAG", 0.08)

_KR_REGIME_LABEL = {
    "low_vol_uptrend": "저변동성 상승",
    "high_vol_downtrend": "고변동성 하락",
    "range_chop": "횡보",
}

# 표시 섹터명 → US 섹터 ETF 프록시 키워드 (US 스캔 한정; 매칭 실패 시 시장 레짐 폴백)
_SECTOR_KEYWORDS = {
    "semi": ("semicon", "반도체", "chip"),
    "tech": ("tech", "software", "소프트", "it", "internet", "ai"),
    "energy": ("energy", "에너지", "oil"),
    "bio": ("bio", "바이오", "pharma", "health"),
    "defense": ("defense", "방산", "aero"),
    "ev": ("ev", "battery", "전기차", "배터리", "mobility"),
    "nuclear": ("nuclear", "원자력", "uranium", "smr"),
    "financials": ("financ", "금융", "bank"),
    "industrials": ("industrial", "산업"),
}


def _disabled() -> bool:
    return os.environ.get("REGIME_DISABLE", "").strip().lower() in ("1", "true", "yes")


def _sector_key(sector: str) -> Optional[str]:
    s = (sector or "").lower()
    for key, kws in _SECTOR_KEYWORDS.items():
        if any(k in s for k in kws):
            return key
    return None


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ── 모듈1: 레짐 전환확률 부착 ────────────────────────────────────────────────
def attach_regime(rows: List[dict], market: str = "KR") -> None:
    """각 row에 Regime/RegimeProbs/RegimeSignal/RegimeModel 부착.

    시장/섹터 레짐은 배치 내에서 (market, sector_key) 단위로 1회만 계산(memoize).
    regime_classifier가 일자 파일 캐시까지 갖고 있어 실제 비용은 사실상 0.
    """
    if _disabled() or not _HAS_RC:
        for r in rows:
            r.setdefault("Regime", None)
            r.setdefault("RegimeProbs", None)
            r.setdefault("RegimeSignal", None)
            r.setdefault("RegimeModel", None)
        return

    mkt = (market or "KR").upper()
    cache: Dict[Optional[str], Any] = {}

    def _regime_for(sector: str):
        sk = _sector_key(sector) if mkt == "US" else None
        if sk in cache:
            return cache[sk]
        try:
            res = _rc.get_sector_regime(sk, mkt) if sk else _rc.get_market_regime(mkt)
        except Exception as e:  # noqa: BLE001
            log.debug("regime fetch failed (%s/%s): %s", mkt, sector, e)
            res = None
        cache[sk] = res
        return res

    for r in rows:
        try:
            res = _regime_for(r.get("Sector") or "")
            if res is None:
                r["Regime"] = None
                r["RegimeProbs"] = None
                r["RegimeSignal"] = None
                r["RegimeModel"] = None
                continue
            r["Regime"] = _KR_REGIME_LABEL.get(res.state, res.state)
            r["RegimeState"] = res.state
            r["RegimeProbs"] = {k: round(float(v), 3) for k, v in (res.probs or {}).items()}
            r["RegimeSignal"] = res.transition_signal
            r["RegimeModel"] = res.model_status
        except Exception as e:  # noqa: BLE001
            log.debug("attach_regime row failed: %s", e)
            r.setdefault("Regime", None)
            r.setdefault("RegimeProbs", None)
            r.setdefault("RegimeSignal", None)
            r.setdefault("RegimeModel", None)


# ── 모듈2: OFI 경량 프록시 부착 ──────────────────────────────────────────────
def attach_order_flow(rows: List[dict]) -> None:
    """각 row에 OFIScore/OFIRaw/Accumulation 부착 (row 기존 필드 기반 경량 프록시)."""
    if _disabled() or not _HAS_OF:
        for r in rows:
            r.setdefault("OFIScore", 0.5)
            r.setdefault("OFIRaw", 0.0)
            r.setdefault("Accumulation", False)
            r.setdefault("SmartMoney", 0.5)
        return
    for r in rows:
        try:
            d = _of.ofi_from_row(r)
            ofi = float(d.get("ofi", 0.0) or 0.0)
            r["OFIRaw"] = round(ofi, 4)
            r["OFIScore"] = round(0.5 + 0.5 * _clip(ofi, -1.0, 1.0), 4)
            r["Accumulation"] = bool(d.get("accumulation", False))
            r["SmartMoney"] = round(float(d.get("smart_money", 0.5) or 0.5), 4)
        except Exception as e:  # noqa: BLE001
            log.debug("attach_order_flow row failed: %s", e)
            r.setdefault("OFIScore", 0.5)
            r.setdefault("OFIRaw", 0.0)
            r.setdefault("Accumulation", False)
            r.setdefault("SmartMoney", 0.5)


# ── 모듈3: 크로스마켓 리드래그 부착 (KR 스캔 한정) ────────────────────────────
def attach_leadlag(rows: List[dict], market: str = "KR") -> None:
    """KR row에 LeadLag(transfer/direction/fired) 부착. US 스캔은 no-op."""
    neutral = {"transfer": 0.5, "fired": False, "direction": 0}
    if _disabled() or not _HAS_LL or (market or "KR").upper() != "KR":
        for r in rows:
            r.setdefault("LeadLag", dict(neutral))
        return
    try:
        table = _ll.compute_leadlag()  # {theme: {...}}, 일 1회 캐시
    except Exception as e:  # noqa: BLE001
        log.debug("compute_leadlag failed: %s", e)
        table = {}

    def _match(row: dict) -> dict:
        # row가 명시적 테마를 들고 있으면 우선, 없으면 Sector/Industry 텍스트 매칭
        for field in ("Theme", "Themes", "Sector", "Industry"):
            v = row.get(field)
            if not v:
                continue
            cand = v if isinstance(v, (list, tuple)) else [v]
            for c in cand:
                cs = str(c)
                if cs in table:
                    return table[cs]
                for theme, sig in table.items():
                    if theme and (theme in cs or cs in theme):
                        return sig
        return dict(neutral)

    for r in rows:
        try:
            r["LeadLag"] = _match(r)
        except Exception as e:  # noqa: BLE001
            log.debug("attach_leadlag row failed: %s", e)
            r.setdefault("LeadLag", dict(neutral))


# ── 모듈4: 동적 가중치 → RegimeEntryScore ────────────────────────────────────
def apply_regime_weighting(rows: List[dict]) -> None:
    """RegimeEntryScore = TotalScore × 레짐승수 × OFI승수 × 리드래그승수.

    전환 초기(fresh≈1)일수록 레짐 가중이 커진다(스펙 §5.1).
    무신호 시 모든 승수 1.0 → RegimeEntryScore == TotalScore (불변식).
    """
    if _disabled():
        for r in rows:
            r.setdefault("RegimeEntryScore", r.get("TotalScore", 0))
        return
    for r in rows:
        try:
            base = float(r.get("TotalScore") or 0.0)
            reasons: List[str] = []

            # 1) 레짐 전환 승수
            regime_mult = 1.0
            sig = r.get("RegimeSignal") or {}
            if isinstance(sig, dict):
                strength = float(sig.get("strength", 0.0) or 0.0)
                fresh = float(sig.get("fresh", 0.0) or 0.0)
                dir_sign = 1.0 if sig.get("early_long") else (-1.0 if sig.get("early_exit") else 0.0)
                if dir_sign != 0.0:
                    regime_mult = 1.0 + W_REGIME * strength * (0.5 + 0.5 * fresh) * dir_sign
                    tag = "선행 진입" if dir_sign > 0 else "선행 이탈"
                    reasons.append(f"레짐 {tag}(강도 {strength:.2f}·신선도 {fresh:.2f})")

            # 2) OFI 승수
            of_mult = 1.0
            ofi_score = r.get("OFIScore")
            if ofi_score is not None:
                accum = 1.3 if r.get("Accumulation") else 1.0
                of_mult = 1.0 + W_OFI * (float(ofi_score) - 0.5) * 2.0 * accum
                if r.get("Accumulation"):
                    reasons.append("은밀 매집 변곡점")

            # 3) 리드래그 승수 (KR)
            ll_mult = 1.0
            ll = r.get("LeadLag") or {}
            if isinstance(ll, dict) and ll.get("fired"):
                transfer = float(ll.get("transfer", 0.5) or 0.5)
                ll_mult = 1.0 + W_LEADLAG * (transfer - 0.5) * 2.0
                arrow = "↑" if ll.get("direction", 0) > 0 else "↓"
                reasons.append(f"美→韓 리드래그 {arrow}")

            score = _clip(base * regime_mult * of_mult * ll_mult, 0.0, 100.0)
            r["RegimeEntryScore"] = round(score, 2)
            r["RegimeMultipliers"] = {
                "regime": round(regime_mult, 4),
                "ofi": round(of_mult, 4),
                "leadlag": round(ll_mult, 4),
            }
            r["RegimeReasons"] = reasons
        except Exception as e:  # noqa: BLE001
            log.debug("apply_regime_weighting row failed: %s", e)
            r.setdefault("RegimeEntryScore", r.get("TotalScore", 0))


# ── 통합 진입점 ─────────────────────────────────────────────────────────────
def attach_all(rows: List[dict], market: str = "KR") -> None:
    """4개 모듈을 순서대로 부착. engine_adapter가 이 함수 하나만 호출하면 된다."""
    if not rows:
        return
    attach_regime(rows, market)
    attach_order_flow(rows)
    attach_leadlag(rows, market)
    apply_regime_weighting(rows)


def rank_key(row: dict) -> float:
    """REGIME_RANK=1 이면 RegimeEntryScore, 아니면 TotalScore로 정렬."""
    if os.environ.get("REGIME_RANK", "").strip().lower() in ("1", "true", "yes"):
        v = row.get("RegimeEntryScore")
        if v is None:  # RegimeEntryScore==0.0 은 유효한 점수 — truthiness 폴백 금지
            v = row.get("TotalScore")
        return float(v if v is not None else 0.0)
    return float(row.get("TotalScore") or 0.0)
