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
import re
import json
import time
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
                # K-IFRS 채택 기업은 IS 대신 CIS(포괄손익계산서)만 제공.
                # IS 비어 있으면 CIS로 폴백해 EPS·순이익을 같은 자리에서 꺼낼 수 있게.
                if not result["IS"] and result.get("CIS"):
                    result["IS"] = list(result["CIS"])
                return result
        except Exception:
            continue

    return {"available": False, "error": f"재무데이터 없음 ({code})"}


# ── 12h 인메모리 캐시 (재무가치 등급 Phase 2a 에서 동일 종목 중복 호출 방지) ──
_fin_cache: Dict[str, tuple] = {}
_FIN_TTL = 43200  # 12h


def get_financials_cached(stock_code: str, **kwargs) -> Dict[str, Any]:
    """get_financials 와 동일하되 12 시간 인메모리 캐시."""
    code = stock_code.split(".")[0].zfill(6)
    cache_key = f"{code}:{kwargs.get('year', '')}:{kwargs.get('report_code', '11011')}"
    now = time.time()
    cached = _fin_cache.get(cache_key)
    if cached and (now - cached[1]) < _FIN_TTL:
        return cached[0]
    result = get_financials(stock_code, **kwargs)
    if result.get("available"):
        _fin_cache[cache_key] = (result, now)
    return result


def _extract_eps(rows: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """IS/CIS 행에서 보통주 기본 EPS(원/주) 당기·전기·전전기 값을 뽑는다.

    DART account_nm 변형(우선주/희석/손실/계속영업/'기본 및 희석' 합본 등)을
    모두 처리. 보통주 기본을 최우선, 손실 표기·합본 표기도 수용.
    """
    def _f(v: Any) -> Optional[float]:
        s = str(v or "").replace(",", "").strip()
        if s in ("", "-"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    # 점수: 높을수록 우선. 우선주/중단영업은 제외, 계속영업은 차순위 수용.
    best_score = -(10**9)
    best_row: Optional[Dict[str, Any]] = None
    for r in rows:
        nm_raw = r.get("account_nm") or ""
        nm = nm_raw.replace(" ", "")
        if "주당" not in nm:
            continue
        if not any(k in nm for k in ("이익", "손실", "손익", "순이익")):
            continue
        if "우선주" in nm:
            continue
        if "중단영업" in nm:
            continue  # 중단사업 EPS는 going-forward 분석에 부적합
        score = 0
        if "보통주" in nm:
            score += 4
        if "기본" in nm:
            score += 2
        if "희석" in nm and "기본" not in nm:
            score += 1   # 희석만 단독이면 차순위로
        if "계속영업" in nm:
            score -= 3   # 분할표기 종목에서 차순위 — 단, 다른 행이 없으면 채택
        # 합본 "기본 및 희석주당이익" 도 자연히 +6 → 충분히 선호
        if score > best_score:
            best_score = score
            best_row = r

    if best_row is None:
        return None
    return {
        "ths": _f(best_row.get("thstrm_amount")),
        "frm": _f(best_row.get("frmtrm_amount")),
        "bf":  _f(best_row.get("bfefrmtrm_amount")),
    }


def extract_fields(
    financials: Dict[str, Any],
    sheet: str,
    field_patterns: Dict[str, List[str]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """DART 재무제표에서 복수 필드를 regex 기반으로 추출.

    Args:
        financials: get_financials() 결과.
        sheet: ``"BS"``, ``"IS"``, ``"CF"`` 등.
        field_patterns: ``{output_key: [regex_pattern, ...]}``.
            패턴 순서 = 우선순위. 첫 매칭 채택.

    Returns:
        ``{key: {"ths": float|None, "frm": float|None, "bf": float|None}}``
    """
    def _p(v: Any) -> Optional[float]:
        s = str(v or "").replace(",", "").strip()
        if s in ("", "-"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    rows = financials.get(sheet, [])
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for key, patterns in field_patterns.items():
        for pat in patterns:
            rgx = re.compile(pat)
            matched = False
            for row in rows:
                nm = (row.get("account_nm") or "").replace(" ", "")
                if rgx.search(nm):
                    out[key] = {
                        "ths": _p(row.get("thstrm_amount")),
                        "frm": _p(row.get("frmtrm_amount")),
                        "bf":  _p(row.get("bfefrmtrm_amount")),
                    }
                    matched = True
                    break
            if matched:
                break
        if key not in out:
            out[key] = {"ths": None, "frm": None, "bf": None}
    return out


def get_annual_eps_growth(stock_code: str) -> Dict[str, Any]:
    """DART 사업보고서(연결, 최신) 기본 EPS의 연간 성장률.

    Returns:
        {
          "available": bool,
          "eps_growth": float | None,   # (당기 - 전기) / |전기|
          "eps_ths":    float | None,   # 당기 EPS (원/주)
          "eps_frm":    float | None,   # 전기 EPS
          "year":       int,            # 사업보고서 연도
          "source":     "DART-사업보고서",
        }
    """
    out: Dict[str, Any] = {"available": False, "source": "DART-사업보고서"}
    fin = get_financials(stock_code)
    if not fin.get("available"):
        return out
    eps = _extract_eps(fin.get("IS") or [])
    if eps is None:
        eps = _extract_eps(fin.get("CIS") or [])
    if eps is None or eps.get("ths") is None or eps.get("frm") is None:
        return out
    ths, frm = eps["ths"], eps["frm"]
    if abs(frm) < 1e-9:
        return out
    out.update({
        "available":  True,
        "eps_growth": (ths - frm) / abs(frm),
        "eps_ths":    ths,
        "eps_frm":    frm,
        "year":       fin.get("year"),
    })
    return out


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
