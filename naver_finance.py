"""Naver 금융 페이지 스크래핑 — 한국 종목 시세/수급/재무 보강.

외부 API 키 불필요. finance.naver.com HTML 직접 파싱.
의존성: 표준 라이브러리만 (urllib, re, html).
"""
from __future__ import annotations

import html as _html
import re
import urllib.request
from typing import Any

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TAG_RE = re.compile(r"<[^>]+>")
_NUM_RE = re.compile(r"-?[\d,\.]+")


def _strip(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _to_float(s: str) -> float | None:
    if not s:
        return None
    m = _NUM_RE.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read()
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _normalize_code(ticker: str) -> str | None:
    """KR 6자리 코드 추출. 'AAPL' 같은 미국 티커는 None."""
    s = str(ticker or "").strip().upper()
    s = s.replace(".KS", "").replace(".KQ", "")
    if s.isdigit() and len(s) == 6:
        return s
    return None


def get_quote(ticker: str) -> dict[str, Any]:
    """네이버 금융 메인 페이지에서 시세/거래량/시총/PER/PBR/외국인비율 추출.

    return:
        {ticker, code, name, price, change, change_pct, volume,
         market_cap_oku(억원), per, pbr, foreign_pct, source}
    실패 시 가능한 값만 채우고 누락은 None.
    """
    code = _normalize_code(ticker)
    if not code:
        return {"ticker": ticker, "code": None, "error": "not a KR 6-digit code"}
    out: dict[str, Any] = {
        "ticker": ticker,
        "code": code,
        "name": None,
        "price": None,
        "change": None,
        "change_pct": None,
        "volume": None,
        "market_cap_oku": None,
        "per": None,
        "pbr": None,
        "foreign_pct": None,
        "source": "finance.naver.com",
    }
    try:
        html = _fetch(f"https://finance.naver.com/item/main.naver?code={code}")
    except Exception as e:
        out["error"] = f"fetch failed: {e}"
        return out

    # 종목명
    m = re.search(r'<div class="wrap_company">\s*<h2>\s*<a[^>]*>([^<]+)</a>', html)
    if m:
        out["name"] = _strip(m.group(1))

    # 현재가 (no_today blind)
    m = re.search(r'<p class="no_today">.*?<span class="blind">([\d,\.]+)</span>', html, re.S)
    if m:
        out["price"] = _to_float(m.group(1))

    # 전일 대비 (no_exday blind 첫 두 개 = 부호+값, 등락률)
    blinds = re.findall(r'<p class="no_exday">.*?</p>', html, re.S)
    if blinds:
        nums = re.findall(r'<span class="blind">([^<]+)</span>', blinds[0])
        # 통상: [상승/하락, 변동값, 변동값소수점.., 등락률, 등락률소수점..]
        # 단순화: 모든 숫자 추출 후 첫 번째=change, 마지막=pct
        floats = [_to_float(n) for n in nums if _to_float(n) is not None]
        if len(floats) >= 1:
            out["change"] = floats[0]
        if len(floats) >= 2:
            out["change_pct"] = floats[-1]
        # 부호 판별
        if 'class="ico down"' in blinds[0] or "하락" in nums:
            if out["change"] is not None:
                out["change"] = -abs(out["change"])
            if out["change_pct"] is not None:
                out["change_pct"] = -abs(out["change_pct"])

    # 거래량
    m = re.search(r"거래량.*?<em[^>]*>\s*<span class=\"blind\">([\d,]+)</span>", html, re.S)
    if not m:
        m = re.search(r"거래량.*?id=\"_quant\"[^>]*>([\d,]+)</span>", html, re.S)
    if m:
        out["volume"] = _to_float(m.group(1))

    # 시가총액 (예: "382조 6,549억원")
    m = re.search(r"시가총액[^<]*</th>.*?<em[^>]*>([^<]+)</em>", html, re.S)
    if m:
        raw = _strip(m.group(1)).replace(",", "")
        cho = re.search(r"(\d+)\s*조", raw)
        oku = re.search(r"(\d+)\s*억", raw)
        total_oku = 0.0
        if cho:
            total_oku += float(cho.group(1)) * 10000
        if oku:
            total_oku += float(oku.group(1))
        if total_oku > 0:
            out["market_cap_oku"] = total_oku

    # PER / PBR — 우측 투자정보 박스가 렌더한 <em id="_per">42.81</em>.
    # (구 regex는 <em>…</em> 인접 가정이 깨져 항상 None을 반환했다.)
    m = re.search(r'<em id="_per">\s*([\d.,\-]+)\s*</em>', html)
    if m:
        out["per"] = _to_float(m.group(1))
    m = re.search(r'<em id="_pbr">\s*([\d.,\-]+)\s*</em>', html)
    if m:
        out["pbr"] = _to_float(m.group(1))

    # 외국인 보유비율
    m = re.search(r"외국인소진율[^<]*</th>.*?<em[^>]*>([\d\.,]+)</em>", html, re.S)
    if not m:
        m = re.search(r"외국인 ?비율[^<]*</th>.*?<em[^>]*>([\d\.,]+)</em>", html, re.S)
    if m:
        out["foreign_pct"] = _to_float(m.group(1))

    return out


def get_investor_flow(ticker: str) -> dict[str, Any]:
    """외국인/기관 순매수 동향 (frgn.naver). 단위: 주."""
    code = _normalize_code(ticker)
    if not code:
        return {"ticker": ticker, "error": "not a KR 6-digit code"}
    out: dict[str, Any] = {"ticker": ticker, "code": code, "rows": []}
    try:
        html = _fetch(
            f"https://finance.naver.com/item/frgn.naver?code={code}"
        )
    except Exception as e:
        out["error"] = f"fetch failed: {e}"
        return out
    # 표 행 파싱
    rows = re.findall(r"<tr[^>]*onmouseover[^>]*>(.*?)</tr>", html, re.S)
    parsed: list[dict] = []
    for r in rows[:10]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(cells) < 7:
            continue
        date = _strip(cells[0])
        if not date:
            continue
        parsed.append(
            {
                "date": date,
                "close": _to_float(_strip(cells[1])),
                "change_pct": _to_float(_strip(cells[3])),
                "volume": _to_float(_strip(cells[4])),
                "foreign_net": _to_float(_strip(cells[5])),
                "inst_net": _to_float(_strip(cells[6])),
            }
        )
    out["rows"] = parsed
    if parsed:
        latest = parsed[0]
        out["latest_date"] = latest["date"]
        out["foreign_net_latest"] = latest["foreign_net"]
        out["inst_net_latest"] = latest["inst_net"]
        # 5일 누적
        out["foreign_net_5d"] = sum(
            r["foreign_net"] or 0 for r in parsed[:5]
        )
        out["inst_net_5d"] = sum(r["inst_net"] or 0 for r in parsed[:5])
    return out


def build_summary_text(q: dict[str, Any]) -> str:
    if q.get("error"):
        return f"네이버 금융 조회 실패: {q['error']}"
    name = q.get("name") or q.get("code") or q.get("ticker")
    price = q.get("price")
    pct = q.get("change_pct")
    if price is None:
        return f"{name} 시세 데이터를 가져오지 못했습니다."
    arrow = "▲" if (pct or 0) > 0 else "▼" if (pct or 0) < 0 else "■"
    parts = [f"{name} {price:,.0f}원 {arrow}{abs(pct or 0):.2f}%"]
    if q.get("per"):
        parts.append(f"PER {q['per']:.1f}")
    if q.get("pbr"):
        parts.append(f"PBR {q['pbr']:.2f}")
    if q.get("foreign_pct"):
        parts.append(f"외국인 {q['foreign_pct']:.2f}%")
    if q.get("market_cap_oku"):
        cap = q["market_cap_oku"]
        if cap >= 10000:
            parts.append(f"시총 {cap/10000:.2f}조")
        else:
            parts.append(f"시총 {cap:.0f}억")
    return " · ".join(parts)


if __name__ == "__main__":
    # 삼성전자 005930
    q = get_quote("005930")
    assert q["code"] == "005930"
    print("QUOTE:", build_summary_text(q))
    f = get_investor_flow("005930")
    print("FLOW rows:", len(f.get("rows", [])))
    print("NAVER_FINANCE OK")
