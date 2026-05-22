# -*- coding: utf-8 -*-
"""naver_quarter.py — 네이버 모바일 분기 재무 API → TTM (최근 4분기) 구성.

네이버 모바일 `finance/quarter` 는 직전 보고분기 + 다음 분기 컨센서스(isConsensus='Y')
까지 포함하므로, 최신 4 개 분기를 합산해 trailing-twelve-month 입력을 만든다.

공개 API:
    get_ttm_financials(code) -> dict
        {
          "operating_income": float,  # 원 (TTM 합계)
          "net_income":       float,
          "revenue":          float,
          "ebitda":           float,  # 영업이익을 보수적 프록시로 사용
          "eps":              float,  # 4분기 EPS 합산
          "bps":              float,  # 최신 분기
          "roe":              float,
          "debt_ratio":       float,
          "shares_outstanding": float,
          "fiscal_period":    str,    # 예: "TTM 202506~202603(E)"
          "has_consensus":    bool,   # 컨센서스 분기 포함 여부
          "source":           "Naver-Q",
          "available":        bool,
        }
"""
from __future__ import annotations

import json
import time
import threading
import urllib.request
from typing import Any, Dict, List, Optional

_URL_TMPL = "https://m.stock.naver.com/api/stock/{code}/finance/quarter"
_HEADERS  = {"User-Agent": "Mozilla/5.0"}
_TTL      = 43200  # 12h
_cache: Dict[str, tuple[Dict[str, Any], float]] = {}
_lock     = threading.Lock()

# 네이버 row title → 표준 키 매핑 (부분 일치)
_FIELD_MAP = [
    ("매출액",       "revenue"),
    ("영업이익",     "operating_income"),
    ("당기순이익",   "net_income"),
    ("ROE",          "roe"),
    ("부채비율",     "debt_ratio"),
    ("EPS",          "eps"),
    ("BPS",          "bps"),
]


def _to_float(x: Any) -> float:
    try:
        s = str(x).replace(",", "").strip()
        if s in ("", "-", "N/A"):
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _fetch_raw(code: str) -> Dict[str, Any]:
    """code: 6자리 코드. 네이버 quarter API 원본 JSON."""
    url = _URL_TMPL.format(code=code)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _extract_value(rowlist: List[Dict[str, Any]], title_kw: str, period_key: str) -> float:
    """rowList 에서 title 에 키워드를 포함하는 row 의 특정 분기 값."""
    for row in rowlist:
        if title_kw in str(row.get("title", "")):
            cols = row.get("columns", {}) or {}
            cell = cols.get(period_key) or {}
            return _to_float(cell.get("value"))
    return 0.0


def get_ttm_financials(code: str) -> Dict[str, Any]:
    """TTM 재무 데이터 (네이버 모바일 분기 API 기반).

    최신 분기(컨센서스 포함) 부터 역순으로 4 개 분기를 합산.
    flow 항목(매출/영업이익/순이익/EPS): 4분기 합산.
    stock 항목(BPS/ROE/부채비율): 가장 최근 actual 분기 값.
    """
    code6 = code.split(".")[0].zfill(6)
    now = time.time()
    with _lock:
        cached = _cache.get(code6)
        if cached and (now - cached[1]) < _TTL:
            return cached[0]

    out: Dict[str, Any] = {"source": "Naver-Q", "available": False}
    try:
        raw = _fetch_raw(code6)
        fi = raw.get("financeInfo", {}) or {}
        titles = fi.get("trTitleList", []) or []
        rows   = fi.get("rowList", []) or []
        if not titles or not rows:
            return out

        # trTitleList 는 시간 오름차순. TTM은 '실제 보고된' 직전 4분기만 사용.
        # 컨센서스(추정) 분기를 합산하면 net_income / EPS 가 부풀려져 다른 지표
        # (PER, EPS성장률, ROE 산정 분모)에 연쇄 왜곡이 발생한다.
        actual_titles = [t for t in titles if t.get("isConsensus") != "Y"]
        if len(actual_titles) < 4:
            return out
        last4 = actual_titles[-4:]  # actual 직전 4분기
        period_keys = [t.get("key") for t in last4]
        has_consensus = any(t.get("isConsensus") == "Y" for t in titles[-4:])

        # flow 항목 합산
        def _sum(title_kw: str) -> float:
            return sum(_extract_value(rows, title_kw, pk) for pk in period_keys)

        revenue = _sum("매출액") * 1e8  # 억원→원
        op_inc  = _sum("영업이익") * 1e8 if any("영업이익" in r.get("title","") for r in rows) else 0.0
        # "영업이익률" 도 매칭되므로 더 엄격한 매칭 필요
        op_inc = 0.0
        for r in rows:
            t = str(r.get("title",""))
            if t.strip() == "영업이익" or (t.startswith("영업이익") and "률" not in t):
                cols = r.get("columns", {})
                op_inc = sum(_to_float((cols.get(pk) or {}).get("value")) for pk in period_keys) * 1e8
                break
        # 지배주주순이익 우선 (한국 회계기준 EPS 산정 기준). 없으면 당기순이익 폴백.
        net_inc = 0.0
        for target in ("지배주주순이익", "당기순이익"):
            for r in rows:
                t = str(r.get("title","")).strip()
                if t == target:
                    cols = r.get("columns", {})
                    vals = [(cols.get(pk) or {}).get("value") for pk in period_keys]
                    if any(v not in (None, "-", "") for v in vals):
                        net_inc = sum(_to_float(v) for v in vals) * 1e8
                        break
            if net_inc:
                break
        eps_ttm = _sum("EPS")  # EPS 는 원/주, 단위 환산 없음

        # ── EPS 성장률 (CAN SLIM C 원칙용) ────────────────────────
        # 연간 프록시: 최근 4분기 EPS 합 vs 직전 4분기 EPS 합
        # 분기 YoY  : 최신 분기 EPS vs 4분기 전(전년 동기) EPS
        # 분모가 0/음수면 성장률 정의 불가 → None (호출부에서 '미존재' 처리)
        eps_growth = None
        eps_qoq_growth = None
        try:
            eps_cols: Dict[str, Any] = {}
            for r in rows:
                if "EPS" in str(r.get("title", "")):
                    eps_cols = r.get("columns", {}) or {}
                    break
            # actual 분기만 사용 (컨센서스 분기 제외)
            actual_keys = [t.get("key") for t in titles if t.get("isConsensus") != "Y"]

            def _eps(k: Optional[str]) -> float:
                return _to_float((eps_cols.get(k) or {}).get("value"))

            if len(actual_keys) >= 8:
                ttm_now  = sum(_eps(k) for k in actual_keys[-4:])
                ttm_prev = sum(_eps(k) for k in actual_keys[-8:-4])
                if ttm_prev > 1e-9:
                    eps_growth = (ttm_now - ttm_prev) / ttm_prev
            if len(actual_keys) >= 5:
                q_now = _eps(actual_keys[-1])
                q_yoy = _eps(actual_keys[-5])
                if q_yoy > 1e-9:
                    eps_qoq_growth = (q_now - q_yoy) / q_yoy
        except Exception:
            pass

        # stock 항목: 최신 actual 분기에서 추출 (컨센서스는 보통 '-')
        latest_actual_key = None
        for t in reversed(titles):
            if t.get("isConsensus") != "Y":
                latest_actual_key = t.get("key")
                break
        latest_actual_key = latest_actual_key or last4[-1].get("key")

        bps  = _extract_value(rows, "BPS", latest_actual_key)
        roe  = _extract_value(rows, "ROE", latest_actual_key)
        debt = _extract_value(rows, "부채비율", latest_actual_key)

        # 발행주식수: net_income (TTM, 원) / EPS (TTM, 원/주)
        # 적자 종목도 |net_inc|/|eps| 로 역산 (둘 다 같은 부호이므로 부호 무관)
        shares = 0.0
        if abs(eps_ttm) > 1e-9 and abs(net_inc) > 1e-9:
            shares = abs(net_inc) / abs(eps_ttm)

        out.update({
            "revenue":           revenue,
            "operating_income":  op_inc,
            "net_income":        net_inc,
            "ebitda":            op_inc,  # 보수적: D&A 미합산
            "eps":               eps_ttm,
            "eps_growth":        eps_growth,      # 연간 EPS 성장률 (None=미존재)
            "eps_qoq_growth":    eps_qoq_growth,  # 분기 YoY EPS 성장률
            "bps":               bps,
            "roe":               roe,
            "debt_ratio":        debt,
            "shares_outstanding": shares,
            "fiscal_period":     f"TTM {period_keys[0]}~{period_keys[-1]}",
            "has_consensus":     has_consensus,
            "available":         bool(op_inc or net_inc or eps_ttm),
        })
        with _lock:
            _cache[code6] = (out, now)
    except Exception as e:
        out["error"] = str(e)
    return out


if __name__ == "__main__":
    for c in ("005930", "000660", "035420"):
        r = get_ttm_financials(c)
        print(c, json.dumps(r, ensure_ascii=False, indent=2))
