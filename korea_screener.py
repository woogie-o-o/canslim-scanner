"""
한국장 종목 스크리너 — 151 Trading Strategies 기반
=======================================================
Kakushadze & Serur (2018) "151 Trading Strategies" 논문에서
한국 시장(KOSPI/KOSDAQ)에 실증적으로 잘 통하는 전략 5개를 구현.

전략 목록:
  1. momentum   — 12-1개월 가격 모멘텀 (Strategy 3.1)
  2. value      — 저PBR + 고ROE 복합 가치 (Strategy 3.3 + 3.6)
  3. low_vol    — 저변동성 이상현상 (Strategy 3.4)
  4. earnings   — EPS/영업이익 가속 모멘텀 (Strategy 3.2)
  5. breakout   — 거래량 폭발 + MA 정배열 돌파 (Strategy 3.11-3.14)

사용법:
  python korea_screener.py [전략명] [옵션]

  python korea_screener.py momentum --top 20 --market all
  python korea_screener.py value    --top 20 --market kospi
  python korea_screener.py low_vol  --top 20
  python korea_screener.py earnings --top 20
  python korea_screener.py breakout --top 20
  python korea_screener.py all      --top 10   # 5전략 종합

옵션:
  --top N        상위 N종목 출력 (기본 20)
  --market       kospi | kosdaq | all (기본 all)
  --min-cap      최소 시가총액 억원 (기본 500)
  --min-vol      최소 일평균 거래대금 억원 (기본 10)
  --csv          CSV 파일로 저장
"""
from __future__ import annotations

import re
import sys
import csv
import time
import math
import urllib.request
import html as _html
import argparse
from datetime import datetime, timedelta
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 프로젝트 내부 모듈 ──────────────────────────────────────────────────
try:
    import naver_finance as _nf
    _NF_OK = True
except ImportError:
    _NF_OK = False

try:
    import dart_api as _dart
    _DART_OK = _dart.is_available()
except ImportError:
    _DART_OK = False

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_TAG = re.compile(r"<[^>]+>")


# ══════════════════════════════════════════════════════════════════════════
#  저수준 HTTP 헬퍼
# ══════════════════════════════════════════════════════════════════════════

def _fetch(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception:
        return ""
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", _html.unescape(s or ""))).strip()


def _num(s: str) -> Optional[float]:
    m = re.search(r"-?[\d,\.]+", (s or "").replace(",", ""))
    try:
        return float(m.group()) if m else None
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════
#  유니버스 수집 — Naver 시가총액 정렬 페이지
# ══════════════════════════════════════════════════════════════════════════

def _get_market_page(sosok: int, page: int) -> list[dict]:
    """KOSPI(0) 또는 KOSDAQ(1) 시장 요약 한 페이지.

    Naver Finance tbody 구조:
      td[0]=순위, td[1]=종목명(+코드링크), td[2]=현재가,
      td[3]=전일대비, td[4]=등락률, td[5]=액면가,
      td[6]=거래량, td[7]=시가총액(억)
    """
    url = (
        f"https://finance.naver.com/sise/sise_market_sum.naver"
        f"?sosok={sosok}&page={page}"
    )
    html = _fetch(url)
    if not html:
        return []
    # tbody 내 tr만 파싱
    tbody = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    body = tbody.group(1) if tbody else html
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    out: list[dict] = []
    for row in rows:
        m = re.search(r"code=(\d{6})", row)
        if not m:
            continue
        code = m.group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 7:
            continue
        name_m = re.search(r"<a[^>]+>([^<]+)</a>", cells[1])
        name = _strip(name_m.group(1)) if name_m else ""
        out.append({
            "code":       code,
            "name":       name,
            "market":     "KOSPI" if sosok == 0 else "KOSDAQ",
            "price":      _num(_strip(cells[2])),
            "change_pct": _num(_strip(cells[4])),
            "volume":     _num(_strip(cells[6])),
            "market_cap": _num(_strip(cells[7])) if len(cells) > 7 else None,
            # per/pbr/roe는 enrich_stock에서 get_quote로 정확히 가져옴
            "per":  None,
            "pbr":  None,
            "roe":  None,
        })
    return out


def get_universe(
    market: str = "all",
    min_cap: float = 500,
    max_pages: int = 30,
) -> list[dict]:
    """KOSPI/KOSDAQ 유니버스 수집. min_cap 억원 이상만."""
    print(f"[Universe] 종목 목록 수집 중... (시총 ≥ {min_cap}억, market={market})")
    markets = []
    if market in ("all", "kospi"):
        markets.append(0)
    if market in ("all", "kosdaq"):
        markets.append(1)

    stocks: list[dict] = []
    for sosok in markets:
        for page in range(1, max_pages + 1):
            rows = _get_market_page(sosok, page)
            if not rows:
                break
            # 시총 필터 + 0원/음수 제외
            valid = [r for r in rows if (r["market_cap"] or 0) >= min_cap and (r["price"] or 0) > 0]
            stocks.extend(valid)
            if len(valid) < len(rows):
                break  # 마지막 페이지 (시총 소기업 구간)
            time.sleep(0.05)
        mname = "KOSPI" if sosok == 0 else "KOSDAQ"
        print(f"  {mname}: {sum(1 for s in stocks if s['market'] == mname)}종목")
    print(f"  총 {len(stocks)}종목 수집 완료")
    return stocks


# ══════════════════════════════════════════════════════════════════════════
#  역사적 가격 — Naver 일봉
# ══════════════════════════════════════════════════════════════════════════

def _get_price_history(code: str, pages: int = 13) -> list[dict]:
    """일봉 데이터 최대 pages×10 행 (약 130 거래일 ≈ 6개월)."""
    rows: list[dict] = []
    for page in range(1, pages + 1):
        url = (
            f"https://finance.naver.com/item/sise_day.naver"
            f"?code={code}&page={page}"
        )
        html = _fetch(url)
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        found = False
        for tr in trs:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            vals = [_strip(c) for c in cells]
            if len(vals) < 6:
                continue
            date = vals[0]
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date):
                continue
            close = _num(vals[1])
            vol   = _num(vals[5])
            if close and close > 0:
                rows.append({"date": date, "close": close, "volume": vol or 0})
                found = True
        if not found:
            break
        time.sleep(0.02)
    return rows


def _returns(history: list[dict]) -> dict[str, Optional[float]]:
    """1개월(21일), 3개월(63일), 6개월(126일), 12개월(252일) 수익률."""
    prices = [r["close"] for r in history if r.get("close")]
    if not prices:
        return {}
    cur = prices[0]

    def _ret(n: int) -> Optional[float]:
        if len(prices) > n:
            return (cur / prices[n] - 1) * 100
        return None

    return {
        "ret_1m":  _ret(21),
        "ret_3m":  _ret(63),
        "ret_6m":  _ret(126),
        "ret_12m": _ret(252),
        # 12-1 모멘텀: 12개월 수익률에서 최근 1개월 제거 (skip-1)
        "mom_12_1": (
            (_ret(252) - _ret(21))
            if _ret(252) is not None and _ret(21) is not None
            else None
        ),
    }


def _volatility(history: list[dict], window: int = 60) -> Optional[float]:
    """일간 수익률 표준편차(60일)를 연율화."""
    prices = [r["close"] for r in history[:window + 1] if r.get("close")]
    if len(prices) < 10:
        return None
    daily = [(prices[i] / prices[i + 1] - 1) for i in range(len(prices) - 1)]
    n = len(daily)
    mean = sum(daily) / n
    var = sum((x - mean) ** 2 for x in daily) / (n - 1)
    return math.sqrt(var) * math.sqrt(252) * 100  # 연율화 %


def _ma(history: list[dict], period: int) -> Optional[float]:
    prices = [r["close"] for r in history[:period] if r.get("close")]
    return sum(prices) / len(prices) if len(prices) == period else None


def _vol_ratio(history: list[dict], ma_days: int = 20) -> Optional[float]:
    """최근 거래량 / MA(20일) 거래량."""
    vols = [r["volume"] for r in history if r.get("volume") is not None]
    if len(vols) < ma_days + 1:
        return None
    avg = sum(vols[1: ma_days + 1]) / ma_days
    return (vols[0] / avg) if avg > 0 else None


# ══════════════════════════════════════════════════════════════════════════
#  재무 데이터 — DART API
# ══════════════════════════════════════════════════════════════════════════

def _parse_amount(s: str) -> Optional[float]:
    """DART 금액 문자열 → float (원 단위)."""
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def get_earnings_data(code: str) -> dict[str, Any]:
    """DART에서 영업이익/매출/EPS 당기 vs 전기 성장률."""
    out: dict[str, Any] = {"available": False}
    if not _DART_OK:
        return out
    try:
        fin = _dart.get_financials(code)
    except Exception:
        return out
    if not fin.get("available"):
        return out

    def _acct(rows: list, *names: str) -> tuple[Optional[float], Optional[float]]:
        for row in rows:
            nm = row.get("account_nm", "")
            if any(n in nm for n in names):
                curr = _parse_amount(row.get("thstrm_amount", ""))
                prev = _parse_amount(row.get("frmtrm_amount", ""))
                return curr, prev
        return None, None

    is_rows = fin.get("IS", [])
    rev_c,  rev_p  = _acct(is_rows, "매출액", "수익")
    op_c,   op_p   = _acct(is_rows, "영업이익")
    net_c,  net_p  = _acct(is_rows, "당기순이익")

    def _growth(c, p) -> Optional[float]:
        if c is None or p is None or p == 0:
            return None
        return (c / p - 1) * 100

    out.update({
        "available":   True,
        "rev_growth":  _growth(rev_c, rev_p),
        "op_growth":   _growth(op_c, op_p),
        "net_growth":  _growth(net_c, net_p),
        "op_margin":   (op_c / rev_c * 100) if op_c and rev_c and rev_c > 0 else None,
    })
    return out


# ══════════════════════════════════════════════════════════════════════════
#  개별 종목 데이터 통합
# ══════════════════════════════════════════════════════════════════════════

def enrich_stock(
    s: dict,
    fetch_history: bool = True,
    fetch_earnings: bool = False,
    history_pages: int = 14,
) -> dict:
    """종목 기본 데이터에 가격 이력/재무 추가.

    history_pages: 가격 이력 페이지 수 (페이지당 ~10거래일)
      - 6개월: 14페이지  /  12개월 모멘텀: 27페이지
    """
    code = s["code"]
    result = dict(s)

    # 네이버 금융 quote — 개별 종목 페이지에서 정확한 per/pbr 취득
    if _NF_OK:
        try:
            q = _nf.get_quote(code)
            for k in ("pbr", "per", "foreign_pct"):
                if q.get(k) is not None:
                    result[k] = q[k]  # 항상 덮어씀 (유니버스 페이지보다 정확)
            if q.get("market_cap_oku") is not None:
                result["market_cap"] = q["market_cap_oku"]
        except Exception:
            pass

    if fetch_history:
        hist = _get_price_history(code, pages=history_pages)
        result["_history"] = hist
        if hist:
            result.update(_returns(hist))
            result["volatility"]  = _volatility(hist)
            result["vol_ratio"]   = _vol_ratio(hist)
            result["ma5"]         = _ma(hist, 5)
            result["ma20"]        = _ma(hist, 20)
            result["ma60"]        = _ma(hist, 60)
            result["ma120"]       = _ma(hist, 120)
            # 52주 고점 대비 현재 위치
            prices_52w = [r["close"] for r in hist[:252] if r.get("close")]
            if prices_52w:
                high52 = max(prices_52w)
                result["pct_from_52w_high"] = (
                    (result["price"] / high52 - 1) * 100 if result.get("price") else None
                )

    if fetch_earnings:
        result.update(get_earnings_data(code))

    return result


# ══════════════════════════════════════════════════════════════════════════
#  전략 1: 가격 모멘텀 (Strategy 3.1)
# ══════════════════════════════════════════════════════════════════════════

def momentum_screen(
    universe: list[dict],
    top: int = 20,
    min_daily_vol_oku: float = 10,
    workers: int = 8,
) -> list[dict]:
    """
    12-1 모멘텀 전략.

    근거 (Jegadeesh & Titman 1993, Asness et al. 2013):
    - 지난 12개월 수익률에서 최근 1개월 제거 → 단기 반전 회피
    - 한국 시장에서 6~12개월 모멘텀 효과 실증 확인
    - 거래대금 필터로 비유동 종목 제거
    """
    print(f"\n[전략1: 모멘텀] 데이터 수집 중 ({len(universe)}종목)...")

    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True, history_pages=27)  # 12개월 필요
        mom = r.get("mom_12_1")
        if mom is None:
            return None
        # 거래대금 필터: 거래량×주가 ≥ min_daily_vol_oku 억원
        price = r.get("price") or 0
        vol   = r.get("volume") or 0
        daily_oku = price * vol / 1e8
        if daily_oku < min_daily_vol_oku:
            return None
        r["score"] = mom
        r["signal"] = (
            f"12-1M 모멘텀 {mom:+.1f}% | "
            f"6M {r.get('ret_6m', 0) or 0:+.1f}% | "
            f"3M {r.get('ret_3m', 0) or 0:+.1f}%"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


# ══════════════════════════════════════════════════════════════════════════
#  전략 2: 복합 가치 (Strategy 3.3 + 3.6)
# ══════════════════════════════════════════════════════════════════════════

def value_screen(
    universe: list[dict],
    top: int = 20,
    max_pbr: float = 1.5,
    min_roe: float = 5.0,
    workers: int = 8,
) -> list[dict]:
    """
    저PBR + 고ROE 복합 가치 전략.

    근거 (Fama & French 1992, Piotroski 2000):
    - 한국 시장: PBR < 1.0 종목군 상당수 → 가치 프리미엄 존재
    - ROE 필터로 가치함정(value trap) 제거
    - PBR 순위 + ROE 순위 합산 → 복합 점수
    - 이동평균 상향 필터로 하락 종목 제거
    """
    print(f"\n[전략2: 가치] 데이터 수집 중 ({len(universe)}종목)...")

    def _process(s: dict) -> Optional[dict]:
        # enrich_stock이 naver get_quote로 pbr/per 보강
        r = enrich_stock(s, fetch_history=True)
        pbr = r.get("pbr")
        per = r.get("per")
        roe = r.get("roe")  # 유니버스 페이지에서 올 경우 사용

        # PBR 필수 (get_quote에서 가져옴)
        if pbr is None or pbr <= 0 or pbr > max_pbr:
            return None
        # 적자 제외 (PER 음수)
        if per is not None and per < 0:
            return None
        # ROE 필터: 있을 때만 적용
        if roe is not None and roe < min_roe:
            return None
        # 주가가 MA20 위에 있어야 함 (하락 추세 제외)
        price = r.get("price") or 0
        ma20  = r.get("ma20") or 0
        if ma20 > 0 and price < ma20 * 0.95:
            return None

        # ROE가 없을 때는 PER 역수로 대체
        roe_eff = roe if roe is not None else (100 / per if per and per > 0 else 5.0)
        r["score"] = roe_eff / pbr  # ROE/PBR (높을수록 좋음)
        r["signal"] = (
            f"PBR {pbr:.2f} | "
            + (f"ROE {roe:.1f}% | " if roe is not None else "")
            + (f"PER {per:.1f} | " if per is not None else "")
            + f"ROE/PBR {r['score']:.1f}"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


# ══════════════════════════════════════════════════════════════════════════
#  전략 3: 저변동성 (Strategy 3.4)
# ══════════════════════════════════════════════════════════════════════════

def low_vol_screen(
    universe: list[dict],
    top: int = 20,
    max_vol_pct: float = 30.0,
    workers: int = 8,
) -> list[dict]:
    """
    저변동성 이상현상 전략.

    근거 (Ang et al. 2006, Baker et al. 2011):
    - 저변동성 주식이 장기적으로 고변동성 주식보다 우수한 위험조정 수익률
    - 한국 시장에서도 저베타/저변동성 효과 실증 (박종원 외 2013)
    - 조건: 연율화 변동성 < max_vol_pct% + 6개월 수익률 양수
    """
    print(f"\n[전략3: 저변동성] 데이터 수집 중 ({len(universe)}종목)...")

    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True)
        vol = r.get("volatility")
        ret_6m = r.get("ret_6m")
        if vol is None or vol <= 0:
            return None
        if vol > max_vol_pct:
            return None
        if ret_6m is not None and ret_6m < -10:
            return None  # 강한 하락 추세 제외
        pbr = r.get("pbr") or 0
        roe = r.get("roe") or 0
        r["score"] = -vol  # 낮을수록 좋음
        r["signal"] = (
            f"연율화변동성 {vol:.1f}% | "
            f"6M수익률 {ret_6m or 0:+.1f}% | "
            f"PBR {pbr:.2f} | ROE {roe:.1f}%"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)  # score = -vol, 크면 low_vol
    return results[:top]


# ══════════════════════════════════════════════════════════════════════════
#  전략 4: 이익 모멘텀 (Strategy 3.2)
# ══════════════════════════════════════════════════════════════════════════

def earnings_screen(
    universe: list[dict],
    top: int = 20,
    min_op_growth: float = 10.0,
    workers: int = 4,  # DART API 부하 제한
) -> list[dict]:
    """
    영업이익/EPS 성장 가속 전략.

    근거 (Ball & Brown 1968, Bernard & Thomas 1989):
    - SUE(표준화 예측 외 이익): 전기 대비 이익 서프라이즈 효과
    - 한국 시장: 실적 발표 후 1~3개월 drift 존재
    - DART API 키 필요; 없으면 네이버 ROE 변화로 대체
    """
    print(f"\n[전략4: 이익모멘텀] 데이터 수집 중 ({len(universe)}종목)...")
    if not _DART_OK:
        print("  [주의] DART_API_KEY 미설정 — ROE 기반으로 대체 실행")
        return _earnings_screen_fallback(universe, top, workers)

    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True, fetch_earnings=True)
        op_g  = r.get("op_growth")
        rev_g = r.get("rev_growth")
        net_g = r.get("net_growth")
        if op_g is None or op_g < min_op_growth:
            return None
        if rev_g is not None and rev_g < 0:
            return None  # 매출 감소 제외
        # 가격 모멘텀 확인 (이익 개선이 주가에 반영 시작)
        mom_3m = r.get("ret_3m") or 0
        r["score"] = op_g * 0.5 + (rev_g or 0) * 0.3 + mom_3m * 0.2
        r["signal"] = (
            f"영업이익성장 {op_g:+.1f}% | "
            f"매출성장 {rev_g or 0:+.1f}% | "
            f"순이익성장 {net_g or 0:+.1f}% | "
            f"3M주가 {mom_3m:+.1f}%"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


def _earnings_screen_fallback(
    universe: list[dict],
    top: int,
    workers: int,
) -> list[dict]:
    """DART 없을 때: 고ROE + 주가 모멘텀 조합."""
    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True)
        roe = r.get("roe") or 0
        ret_3m = r.get("ret_3m") or 0
        ret_6m = r.get("ret_6m") or 0
        if roe < 10:
            return None
        per = r.get("per") or 0
        if per < 0 or per > 50:
            return None
        r["score"] = roe * 0.5 + ret_3m * 0.3 + ret_6m * 0.2
        r["signal"] = (
            f"ROE {roe:.1f}% | PER {per:.1f} | "
            f"3M {ret_3m:+.1f}% | 6M {ret_6m:+.1f}%"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


# ══════════════════════════════════════════════════════════════════════════
#  전략 5: 기술적 돌파 (Strategy 3.11-3.14)
# ══════════════════════════════════════════════════════════════════════════

def breakout_screen(
    universe: list[dict],
    top: int = 20,
    min_vol_ratio: float = 1.5,
    workers: int = 8,
) -> list[dict]:
    """
    거래량 폭발 + MA 정배열 + 52주 고점 근접 돌파 전략.

    근거 (151 Strategies 3.11-3.14, O'Neil CAN SLIM):
    - 이동평균 정배열(5>20>60): 추세 확인
    - 거래량 ≥ 20일 평균 1.5배: 기관/세력 개입 신호
    - 52주 고점 10% 이내: 저항선 돌파 임박
    - 한국 시장: 개인 투자자 비중 높아 기술적 분석 자기실현 효과 강함
    """
    print(f"\n[전략5: 기술적돌파] 데이터 수집 중 ({len(universe)}종목)...")

    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True)
        price  = r.get("price") or 0
        ma5    = r.get("ma5") or 0
        ma20   = r.get("ma20") or 0
        ma60   = r.get("ma60") or 0
        ma120  = r.get("ma120") or 0
        vr     = r.get("vol_ratio") or 0
        pct_52 = r.get("pct_from_52w_high")

        # MA 정배열 확인 (5 > 20 > 60)
        if not (ma5 > 0 and ma20 > 0 and ma60 > 0):
            return None
        if not (ma5 > ma20 > ma60):
            return None
        # 120일 MA도 상향 추세
        if ma120 > 0 and price < ma120:
            return None
        # 거래량 폭발
        if vr < min_vol_ratio:
            return None
        # 52주 고점 근접 (10% 이내 또는 신고가)
        if pct_52 is not None and pct_52 < -15:
            return None

        # 점수: 거래량비율 + 52주고점근접도 + MA 정배열 강도
        ma_strength = (ma5 - ma60) / ma60 * 100 if ma60 > 0 else 0
        score_parts = [
            vr * 20,
            max(0, 10 + (pct_52 or -10)) * 2,  # 신고가에 가까울수록 높음
            min(ma_strength, 30),
        ]
        r["score"] = sum(score_parts)
        r["signal"] = (
            f"거래량비율 {vr:.1f}x | "
            f"52주고점대비 {pct_52 or 0:+.1f}% | "
            f"MA정배열 강도 {ma_strength:+.1f}% | "
            f"MA5/20/60: {ma5:,.0f}/{ma20:,.0f}/{ma60:,.0f}"
        )
        return r

    results = _parallel_process(universe, _process, workers)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


# ══════════════════════════════════════════════════════════════════════════
#  종합 스코어 (5전략 결합)
# ══════════════════════════════════════════════════════════════════════════

def all_screen(
    universe: list[dict],
    top: int = 10,
    workers: int = 6,
) -> list[dict]:
    """
    5개 전략 점수를 백분위 정규화 후 동일가중 합산.

    각 전략이 독립적인 팩터를 측정하므로 결합 시 분산화 효과 발생.
    (151 Strategies §3.6 Multifactor Portfolio 접근법)
    """
    print(f"\n[종합] 5전략 동시 수집 중 ({len(universe)}종목)...")

    def _process(s: dict) -> Optional[dict]:
        r = enrich_stock(s, fetch_history=True)
        price  = r.get("price") or 0
        ma20   = r.get("ma20") or 0
        if price <= 0 or ma20 <= 0:
            return None
        return r

    enriched = _parallel_process(universe, _process, workers)
    if not enriched:
        return []

    def _pct_rank(vals: list[Optional[float]], reverse: bool = True) -> list[float]:
        """백분위 순위 0~100."""
        valid = [(v, i) for i, v in enumerate(vals) if v is not None]
        valid.sort(key=lambda x: x[0], reverse=reverse)
        ranks = [0.0] * len(vals)
        n = len(valid)
        for rank, (_, idx) in enumerate(valid):
            ranks[idx] = (1 - rank / max(n - 1, 1)) * 100
        return ranks

    # 팩터별 원시 점수
    mom_raw   = [r.get("mom_12_1") for r in enriched]
    val_raw   = [
        (r.get("roe") or 0) / max(r.get("pbr") or 99, 0.01)
        if r.get("pbr") and r.get("roe") else None
        for r in enriched
    ]
    low_v_raw = [-(r.get("volatility") or 999) for r in enriched]
    roe_raw   = [r.get("roe") for r in enriched]
    bk_raw    = [
        (r.get("vol_ratio") or 0) * 20
        + max(0, 10 + (r.get("pct_from_52w_high") or -20)) * 2
        for r in enriched
    ]

    mom_r   = _pct_rank(mom_raw)
    val_r   = _pct_rank(val_raw)
    low_v_r = _pct_rank(low_v_raw)
    roe_r   = _pct_rank(roe_raw)
    bk_r    = _pct_rank(bk_raw)

    for i, r in enumerate(enriched):
        composite = (
            mom_r[i] * 0.25
            + val_r[i] * 0.25
            + low_v_r[i] * 0.15
            + roe_r[i]  * 0.15
            + bk_r[i]   * 0.20
        )
        r["score"] = composite
        r["mom_rank"]   = mom_r[i]
        r["val_rank"]   = val_r[i]
        r["low_v_rank"] = low_v_r[i]
        r["roe_rank"]   = roe_r[i]
        r["bk_rank"]    = bk_r[i]
        r["signal"] = (
            f"종합 {composite:.0f}점 | "
            f"모멘텀 {mom_r[i]:.0f} | 가치 {val_r[i]:.0f} | "
            f"저변동 {low_v_r[i]:.0f} | 이익 {roe_r[i]:.0f} | 돌파 {bk_r[i]:.0f}"
        )

    enriched.sort(key=lambda x: x["score"], reverse=True)
    return enriched[:top]


# ══════════════════════════════════════════════════════════════════════════
#  병렬 처리 헬퍼
# ══════════════════════════════════════════════════════════════════════════

def _parallel_process(
    items: list[dict],
    fn,
    workers: int = 8,
) -> list[dict]:
    results: list[dict] = []
    done = 0
    total = len(items)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, s): s for s in items}
        for fut in as_completed(futs):
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  진행: {done}/{total}", end="\r")
            try:
                r = fut.result()
                if r is not None:
                    results.append(r)
            except Exception:
                pass
    print()
    return results


# ══════════════════════════════════════════════════════════════════════════
#  출력 / 저장
# ══════════════════════════════════════════════════════════════════════════

_COLS = ["rank", "code", "name", "market", "price", "market_cap", "signal"]

def print_results(results: list[dict], strategy: str) -> None:
    label = {
        "momentum": "가격 모멘텀 (12-1M)",
        "value":    "복합 가치 (저PBR+고ROE)",
        "low_vol":  "저변동성 이상현상",
        "earnings": "이익 모멘텀",
        "breakout": "기술적 돌파",
        "all":      "5전략 종합 복합 점수",
    }.get(strategy, strategy)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*80}")
    print(f"  한국장 스크리너 — {label}")
    print(f"  기준시각: {now}  |  결과: {len(results)}종목")
    print(f"{'='*80}")

    for i, r in enumerate(results, 1):
        cap = r.get("market_cap") or r.get("market_cap_oku") or 0
        cap_str = f"{cap/10000:.1f}조" if cap >= 10000 else f"{cap:.0f}억"
        price = r.get("price") or 0
        mkt = r.get("market", "")
        print(
            f"\n  [{i:02d}] {r.get('name','?')} ({r.get('code','')}) "
            f"[{mkt}]  {price:>9,.0f}원  시총 {cap_str}"
        )
        print(f"       {r.get('signal','')}")

    print(f"\n{'='*80}\n")


def save_csv(results: list[dict], strategy: str) -> str:
    fname = f"screen_{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fields = [
        "rank", "code", "name", "market", "price", "market_cap",
        "per", "pbr", "roe", "foreign_pct",
        "mom_12_1", "ret_6m", "ret_3m", "ret_1m",
        "volatility", "vol_ratio", "pct_from_52w_high",
        "ma5", "ma20", "ma60", "score", "signal",
    ]
    with open(fname, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(results, 1):
            row = dict(r)
            row["rank"] = i
            row.pop("_history", None)
            w.writerow(row)
    return fname


# ══════════════════════════════════════════════════════════════════════════
#  CLI 진입점
# ══════════════════════════════════════════════════════════════════════════

STRATEGIES = {
    "momentum": momentum_screen,
    "value":    value_screen,
    "low_vol":  low_vol_screen,
    "earnings": earnings_screen,
    "breakout": breakout_screen,
    "all":      all_screen,
}


def main() -> None:
    # Windows 콘솔 UTF-8 출력 보장
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="한국장 종목 스크리너 (151 Trading Strategies 기반)"
    )
    parser.add_argument(
        "strategy",
        choices=list(STRATEGIES.keys()),
        help="전략 선택",
    )
    parser.add_argument("--top",     type=int,   default=20,   help="상위 N종목 (기본 20)")
    parser.add_argument("--market",  default="all", choices=["all", "kospi", "kosdaq"])
    parser.add_argument("--min-cap", type=float, default=500,  help="최소 시가총액 억원 (기본 500)")
    parser.add_argument("--min-vol", type=float, default=10,   help="최소 일평균 거래대금 억원 (기본 10)")
    parser.add_argument("--workers", type=int,   default=8,    help="병렬 스레드 수 (기본 8)")
    parser.add_argument("--csv",     action="store_true",      help="CSV 파일 저장")
    parser.add_argument(
        "--max-pages", type=int, default=20,
        help="유니버스 수집 최대 페이지 (기본 20, 1페이지 약 50종목)",
    )
    args = parser.parse_args()

    t0 = time.time()
    universe = get_universe(
        market=args.market,
        min_cap=args.min_cap,
        max_pages=args.max_pages,
    )

    fn = STRATEGIES[args.strategy]
    # 전략별 인자 조정
    kwargs: dict[str, Any] = {"top": args.top, "workers": args.workers}
    if args.strategy == "momentum":
        kwargs["min_daily_vol_oku"] = args.min_vol
    elif args.strategy == "value":
        pass
    elif args.strategy == "earnings":
        kwargs["workers"] = min(args.workers, 4)  # DART API 부하 제한

    results = fn(universe, **kwargs)
    print_results(results, args.strategy)

    if args.csv:
        path = save_csv(results, args.strategy)
        print(f"CSV 저장 완료: {path}")

    elapsed = time.time() - t0
    print(f"소요 시간: {elapsed:.0f}초")


if __name__ == "__main__":
    main()
