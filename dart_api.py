# -*- coding: utf-8 -*-
"""dart_api.py — 금융감독원 DART Open API 클라이언트.

환경변수:
    DART_API_KEY — DART Open API 인증키 (https://opendart.fss.or.kr/)

공개 API:
    get_corp_code(stock_code)       → str  (고유번호)
    get_filings(stock_code, n=20)  → list[dict]  (공시 목록)
    get_financials(stock_code)      → dict  (단일회사 재무제표)
    is_available()                  → bool
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse
from typing import Any, Dict, List, Optional
from functools import lru_cache

# .env 자동 로드
try:
    import _env_loader  # noqa: F401
except Exception:
    pass

_BASE = "https://opendart.fss.or.kr/api"


_CONFIG_JSON_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _key() -> str:
    k = os.environ.get("DART_API_KEY", "").strip()
    if k:
        return k
    # config.json 폴백 (설정 UI 제거 후에도 키 자동 로드)
    try:
        with open(_CONFIG_JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        k = (data.get("DART_API_KEY") or "").strip()
        if k:
            os.environ["DART_API_KEY"] = k
        return k
    except (OSError, json.JSONDecodeError):
        return ""


def is_available() -> bool:
    return bool(_key())


def _get(path: str, params: dict) -> dict:
    params["crtfc_key"] = _key()
    qs  = urllib.parse.urlencode(params)
    url = f"{_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


_stock_to_corp: Dict[str, str] = {}  # stock_code(6자리) → corp_code 캐시


def _load_corp_code_map() -> Dict[str, str]:
    """DART corpCode.xml(zip)을 다운로드해 stock_code→corp_code 매핑을 구축."""
    global _stock_to_corp
    if _stock_to_corp:
        return _stock_to_corp

    import zipfile
    import io
    import xml.etree.ElementTree as ET

    url = f"{_BASE}/corpCode.xml?crtfc_key={_key()}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        zdata = r.read()
    with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
        xml_name = zf.namelist()[0]
        tree = ET.parse(zf.open(xml_name))
    for el in tree.getroot().iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        cc = (el.findtext("corp_code") or "").strip()
        if sc and cc:
            _stock_to_corp[sc] = cc
    return _stock_to_corp


@lru_cache(maxsize=512)
def get_corp_code(stock_code: str) -> Optional[str]:
    """주식코드 → DART 고유번호 (corp_code).

    DART corpCode.xml에서 매핑을 다운로드(첫 호출 시 1회)하여 변환한다.
    실패 시 None 반환.
    """
    if not is_available():
        return None
    code = stock_code.split(".")[0].zfill(6)
    try:
        mapping = _load_corp_code_map()
        return mapping.get(code)
    except Exception:
        pass
    return None


def get_filings(
    stock_code: str,
    count: int = 20,
    report_type: str = "",
) -> List[Dict[str, Any]]:
    """최근 공시 목록 조회.

    Args:
        stock_code:  종목코드 (예: "005930" 또는 "005930.KS")
        count:       최대 조회 건수 (max 100)
        report_type: 보고서 유형 필터 (예: "A"=사업보고서, ""=전체)

    Returns:
        [{"date": "20241231", "title": "...", "url": "...", "corp_name": "..."}, ...]
    """
    if not is_available():
        return []
    code = stock_code.split(".")[0].zfill(6)
    corp_code = get_corp_code(code)

    import datetime
    # bgn_de 필수 — 최근 1년 범위로 조회
    one_year_ago = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y%m%d")
    params: dict = {"bgn_de": one_year_ago, "page_count": min(count, 100), "sort": "date", "sort_mth": "desc"}
    if corp_code:
        params["corp_code"] = corp_code
    else:
        return []  # corp_code 없이는 조회 불가
    if report_type:
        params["pblntf_ty"] = report_type

    try:
        d = _get("list.json", params)
        if d.get("status") != "000":
            return []
        items = d.get("list", [])
        result = []
        for item in items:
            rcp_no = item.get("rcept_no", "")
            result.append({
                "date":      item.get("rcept_dt", ""),
                "title":     item.get("report_nm", ""),
                "corp_name": item.get("corp_name", ""),
                "url":       f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}" if rcp_no else "",
                "rcp_no":    rcp_no,
                "type":      item.get("pblntf_ty", ""),
            })
        return result
    except Exception:
        return []


def get_financials(
    stock_code: str,
    year: Optional[int] = None,
    report_code: str = "11011",
) -> Dict[str, Any]:
    """단일회사 재무제표 (연결 기준, 최근 사업연도).

    Args:
        stock_code:  종목코드
        year:        사업연도 (None=최근 3년 자동)
        report_code: "11011"=사업보고서, "11012"=반기, "11013"=1분기, "11014"=3분기

    Returns:
        {
          "year": 2023,
          "IS": [{"account_nm": "매출액", "thstrm_amount": "336341028000", ...}],
          "BS": [...],
          "CF": [...],
          "available": True,
        }
    """
    if not is_available():
        return {"available": False, "error": "DART_API_KEY 미설정"}

    code      = stock_code.split(".")[0].zfill(6)
    corp_code = get_corp_code(code)
    if not corp_code:
        return {"available": False, "error": f"corp_code 조회 실패: {code}"}

    import datetime
    current_year = datetime.date.today().year
    years_to_try = [year] if year else [current_year - 1, current_year - 2, current_year - 3]

    for yr in years_to_try:
        try:
            d = _get("fnlttSinglAcntAll.json", {
                "corp_code":  corp_code,
                "bsns_year":  str(yr),
                "reprt_code": report_code,
                "fs_div":     "CFS",  # 연결재무제표 우선
            })
            if d.get("status") == "000" and d.get("list"):
                rows = d["list"]
                result: Dict[str, Any] = {"year": yr, "available": True, "IS": [], "BS": [], "CF": []}
                for row in rows:
                    sj = row.get("sj_div", "")
                    entry = {
                        "account_nm":      row.get("account_nm", ""),
                        "thstrm_amount":   row.get("thstrm_amount", ""),
                        "frmtrm_amount":   row.get("frmtrm_amount", ""),
                        "bfefrmtrm_amount": row.get("bfefrmtrm_amount", ""),
                    }
                    if sj == "IS":
                        result["IS"].append(entry)
                    elif sj == "BS":
                        result["BS"].append(entry)
                    elif sj == "CF":
                        result["CF"].append(entry)
                    else:
                        result.setdefault(sj, []).append(entry)
                return result
        except Exception:
            continue

    return {"available": False, "error": f"재무데이터 없음 ({code})"}


def get_summary(stock_code: str) -> Dict[str, Any]:
    """기업 개요 (회사명·업종·대표자 등)."""
    if not is_available():
        return {"available": False, "error": "DART_API_KEY 미설정"}
    code = stock_code.split(".")[0].zfill(6)
    try:
        d = _get("company.json", {"stock_code": code})
        if d.get("status") == "000":
            return {"available": True, "data": d}
        return {"available": False, "error": d.get("message", "API 오류")}
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── 셀프테스트 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not is_available():
        print("[dart_api] DART_API_KEY 환경변수를 설정하세요.")
    else:
        print("[dart_api] 삼성전자 공시:", get_filings("005930", count=3))
        print("[dart_api] 삼성전자 재무:", get_financials("005930"))
