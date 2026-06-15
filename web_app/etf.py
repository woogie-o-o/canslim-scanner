"""
etf.py — 인기 ETF 현황 수집 모듈 (미국 + 한국 동시)

별도 'ETF 현황' 탭에 표시할 대표 ETF들을 한 번에 모은다:
    각 ETF별 현재가, 전일 대비 등락률, 거래량(+평균 대비 배수), 52주 고저 위치

설계 원칙 (macro.py 와 동일):
  - yfinance 1회 배치 다운로드(period=1y) → 한 호출로 미·한 전부 수집
  - 종목별 독립 try/except → 일부 실패해도 나머지는 정상 표시
  - 인메모리 TTL 캐시 + stale-while-error (네트워크 실패 시 직전 값 유지)
  - 절대 예외를 밖으로 던지지 않는다 — 최악의 경우 빈 리스트 + stale 표기
  - /api/scan 과 완전히 분리 — 이 모듈의 어떤 예외도 스캔을 깨뜨리지 않는다
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta

_LOG = logging.getLogger("etf")

_KST = timezone(timedelta(hours=9))

# 거래시간(KST 09~16시 한국 / 22~07시 미국, 평일)엔 15분, 그 외 60분
_TTL_TRADING_SEC = 15 * 60
_TTL_OFF_SEC = 60 * 60

# ── 캐시 ──────────────────────────────────────────────────────────────
_CACHE_LOCK = threading.Lock()
_CACHE: dict | None = None          # 마지막으로 성공 조립한 payload
_CACHE_TS: float = 0.0              # 캐시 적재 시각(epoch sec)

# ── 인기 ETF 목록 (티커, 한글 라벨) ───────────────────────────────────
# 리테일이 실제로 많이 거래하는 레버리지·테마(반도체·AI·빅테크) 중심 + 핵심 지수.
# ⚠ 3X/2X/인버스는 고위험 파생 상품 — 라벨에 배수 명시.
# 미국 — 여러 종목이 섞인 바스켓(지수·섹터·테마·해외·채권)만. 단일종목 추종(NVDL 등)·
# 단일자산(비트코인·금 등)은 제외. 레버리지/인버스도 '지수' 기반이면 바스켓이므로 포함.
# 카테고리별 그룹 → 프론트 소제목으로 묶임.
_US_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("핵심 지수", [
        ("SPY", "S&P 500"), ("QQQ", "나스닥 100"), ("DIA", "다우존스"),
        ("IWM", "러셀2000 (중소형)"), ("VTI", "美 전체시장"), ("RSP", "S&P 동일가중"),
    ]),
    ("반도체", [("SMH", "반도체"), ("SOXX", "반도체 (필라델피아)")]),
    ("레버리지 (지수 기반)", [
        ("SOXL", "반도체 3X"), ("SOXS", "반도체 -3X"), ("TQQQ", "나스닥 3X"),
        ("SQQQ", "나스닥 -3X"), ("SPXL", "S&P 3X"), ("TNA", "러셀2000 3X"),
        ("FNGU", "빅테크 3X"), ("TECL", "기술주 3X"),
    ]),
    ("섹터", [
        ("XLK", "기술"), ("XLF", "금융"), ("XLE", "에너지"), ("XLV", "헬스케어"),
        ("XLY", "경기소비재"), ("XLP", "필수소비재"), ("XLI", "산업재"),
        ("XLU", "유틸리티"), ("XLB", "소재"), ("XLRE", "부동산"), ("XLC", "커뮤니케이션"),
    ]),
    ("스타일·배당", [
        ("VUG", "성장주"), ("VTV", "가치주"), ("SCHD", "배당성장"),
        ("VYM", "고배당"), ("JEPI", "커버드콜 인컴"),
    ]),
    ("테마", [
        ("ARKK", "혁신성장 (ARK)"), ("BOTZ", "로봇·AI"), ("AIQ", "AI"),
        ("SKYY", "클라우드"), ("IGV", "소프트웨어"), ("HACK", "사이버보안"),
        ("XBI", "바이오"), ("KWEB", "중국 인터넷"), ("JETS", "항공"), ("LIT", "리튬·배터리"),
    ]),
    ("해외", [("VEA", "선진국"), ("VWO", "신흥국"), ("INDA", "인도"), ("MCHI", "중국")]),
    ("채권", [("TLT", "美 국채 20년+"), ("HYG", "하이일드 회사채"), ("LQD", "투자등급 회사채")]),
]

# 한국: 네이버 ETF 순위(시총·거래대금) 상위 다종목 ETF만 (.KS).
# 제외: 단일종목 추종(삼성전자·SK하이닉스 직접), MMF/채권/금리·금 등 단일자산.
_KR_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("국내 지수", [
        ("069500.KS", "KODEX 200"), ("102110.KS", "TIGER 200"),
        ("229200.KS", "KODEX 코스닥150"),
    ]),
    ("레버리지·인버스", [
        ("122630.KS", "KODEX 레버리지"), ("252670.KS", "KODEX 인버스2X"),
        ("233740.KS", "KODEX 코스닥150 레버리지"), ("114800.KS", "KODEX 인버스"),
    ]),
    ("반도체·AI", [
        ("396500.KS", "TIGER Fn반도체TOP10"), ("0167A0.KS", "SOL AI반도체TOP2 Plus"),
        ("091160.KS", "KODEX 반도체"), ("395160.KS", "KODEX AI반도체핵심장비"),
        ("139260.KS", "TIGER 200 IT"),
    ]),
    ("미국 지수", [
        ("360750.KS", "TIGER 미국S&P500"), ("133690.KS", "TIGER 미국나스닥100"),
        ("381180.KS", "TIGER 미국필라델피아반도체"),
    ]),
    ("테마", [("102780.KS", "KODEX 삼성그룹")]),
]

# 평탄화 → (ticker, label, category) 3-튜플 리스트
_US_ETFS: list[tuple[str, str, str]] = [
    (t, l, cat) for cat, items in _US_GROUPS for (t, l) in items
]
_KR_ETFS: list[tuple[str, str, str]] = [
    (t, l, cat) for cat, items in _KR_GROUPS for (t, l) in items
]


# ── 레버리지/인버스 판별 (G) ──────────────────────────────────────────
# 라벨에 아래 토큰이 있으면 일일 리밸런싱 파생형으로 간주 → 장기보유 경고.
# "선물"은 단독으로 레버리지가 아님(원유선물·금선물 등 비레버리지 다수) → 제외.
# 레버리지/인버스는 배수(2X·3X) 또는 명시 키워드로만 판별.
_LEV_TOKENS = ("3X", "2X", "-3X", "-2X", "-1X", "레버리지", "인버스", "곱버스")


def _is_leveraged(label: str) -> bool:
    """라벨 기반 레버리지/인버스 판별. 절대 예외 안 던짐."""
    try:
        s = (label or "").upper().replace(" ", "")
        return any(tok.upper() in s for tok in _LEV_TOKENS)
    except Exception:
        return False


def _ttl_now() -> int:
    now = datetime.now(_KST)
    weekday = now.weekday() < 5
    kr_open = 9 <= now.hour < 16
    us_open = now.hour >= 22 or now.hour < 7
    trading = weekday and (kr_open or us_open)
    return _TTL_TRADING_SEC if trading else _TTL_OFF_SEC


def _row_from_sub(ticker: str, label: str, sub, category: str = "") -> dict | None:
    """yfinance 종목 서브프레임 → ETF 카드 1개 dict. 데이터 부족 시 None."""
    try:
        closes = sub["Close"].dropna()
        if len(closes) == 0:
            return None
        last = float(closes.iloc[-1])
        if last <= 0:
            return None
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
        change_pct = ((last - prev) / prev * 100.0) if prev else 0.0

        # 52주(가용 일봉 전체) 고·저 및 현재 위치(0~100%)
        hi52 = float(closes.max())
        lo52 = float(closes.min())
        span = hi52 - lo52
        pos52 = ((last - lo52) / span * 100.0) if span > 0 else 50.0
        pos52 = max(0.0, min(100.0, pos52))

        # 거래량 + 최근 20일 평균 대비 배수
        volume = None
        avg_vol = None
        vol_ratio = None
        try:
            vols = sub["Volume"].dropna()
            if len(vols):
                volume = int(vols.iloc[-1])
                tail = vols.tail(20)
                if len(tail):
                    avg_vol = int(tail.mean())
                    if avg_vol > 0:
                        vol_ratio = round(volume / avg_vol, 2)
        except Exception as ve:
            _LOG.debug("etf volume parse %s: %s", ticker, ve)

        return {
            "ticker": ticker,
            "label": label,
            "price": round(last, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume,
            "avg_vol": avg_vol,
            "vol_ratio": vol_ratio,
            "high52": round(hi52, 2),
            "low52": round(lo52, 2),
            "pos52": round(pos52, 1),
            "is_leveraged": _is_leveraged(label),
            "category": category or "",
        }
    except Exception as e:
        _LOG.warning("etf: row parse %s failed: %s", ticker, e)
        return None


def _fetch() -> dict:
    """미·한 ETF 전부 1회 배치 호출 → {"us":[...], "kr":[...]}. 부분 실패 허용."""
    us_out: list[dict] = []
    kr_out: list[dict] = []

    all_pairs = ([("US", t, l, cat) for t, l, cat in _US_ETFS]
                 + [("KR", t, l, cat) for t, l, cat in _KR_ETFS])
    symbols = [p[1] for p in all_pairs]

    try:
        import yfinance as yf
        # auto_adjust=False → 배당 역조정 없는 실제 체결가(원가). 52주 고저를
        # 실제 거래 가격대로 표시하기 위함 (조정가의 소수점 KRW 회피).
        df = yf.download(
            symbols, period="1y", interval="1d",
            progress=False, group_by="ticker", threads=True,
            auto_adjust=False,
        )
    except Exception as e:
        _LOG.warning("etf: yfinance batch failed: %s", e)
        raise

    level0 = set(df.columns.get_level_values(0)) if hasattr(df.columns, "get_level_values") else set()
    for region, ticker, label, cat in all_pairs:
        try:
            sub = df[ticker] if ticker in level0 else None
            if sub is None:
                continue
            row = _row_from_sub(ticker, label, sub, cat)
            if row is None:
                continue
            (us_out if region == "US" else kr_out).append(row)
        except Exception as e:
            _LOG.warning("etf: %s parse failed: %s", ticker, e)

    return {"us": us_out, "kr": kr_out}


def _build() -> dict:
    data = _fetch()
    return {
        "us": data["us"],
        "kr": data["kr"],
        "ts": datetime.now(_KST).isoformat(timespec="seconds"),
        "stale": False,
    }


def get_etfs(force: bool = False) -> dict:
    """
    인기 ETF 현황 반환. TTL 캐시 + stale-while-error.

    절대 예외를 던지지 않는다 — 최악의 경우 빈 리스트 + stale 표기.
    """
    global _CACHE, _CACHE_TS
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE
        fresh = cached is not None and (now - _CACHE_TS) < _ttl_now()
        if fresh and not force:
            return copy.deepcopy(cached)   # 캐시 별칭 차단 — 호출부 변형이 캐시 오염 못함

    try:
        payload = _build()
        # 양쪽 모두 비면 수집 실패로 간주 → stale 폴백 시도
        if not payload["us"] and not payload["kr"]:
            raise RuntimeError("empty result")
        with _CACHE_LOCK:
            _CACHE = payload
            _CACHE_TS = time.time()
        return copy.deepcopy(payload)
    except Exception as e:
        _LOG.warning("etf: build failed, serving stale: %s", e)
        with _CACHE_LOCK:
            if _CACHE is not None:
                stale = copy.deepcopy(_CACHE)
                stale["stale"] = True
                return stale
        return {
            "us": [], "kr": [],
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "stale": True,
        }


# ── 섹터 비중(sector weighting) — 카드 클릭 시 지연 로딩 ───────────────────
# 메인 /api/etf 배치를 느리게 만들지 않기 위해, 섹터 비중은 ETF 카드를
# 클릭한 순간에 per-ticker 로 따로 가져온다.
#   - 미국: yfinance funds_data.sector_weightings (영문 키 → 한글 라벨 매핑)
#   - 한국: 네이버 모바일증권 ETF 분석 API(sectorPortfolioList) 스크래핑
# 절대 예외를 밖으로 던지지 않는다 — 실패 시 빈 리스트(+stale) 반환.

_SECTOR_TTL_SEC = 6 * 60 * 60          # 섹터 구성은 거의 안 변함 → 6시간
_SECTOR_CACHE_MAX = 300                # 무제한 증가 차단(FIFO eviction)
_SECTOR_LOCK = threading.Lock()
_SECTOR_CACHE: dict[str, tuple[float, list]] = {}   # ticker -> (ts, sectors)

# yfinance funds_data.sector_weightings 영문 키 → 한글 라벨
_US_SECTOR_KR = {
    "technology":             "기술",
    "financial_services":     "금융",
    "healthcare":             "헬스케어",
    "consumer_cyclical":      "경기소비재",
    "consumer_defensive":     "필수소비재",
    "communication_services": "커뮤니케이션",
    "industrials":            "산업재",
    "energy":                 "에너지",
    "basic_materials":        "소재",
    "utilities":              "유틸리티",
    "realestate":             "부동산",
    "real_estate":            "부동산",
}

# 네이버 sectorPortfolioList detailTypeCode → 한글 라벨
_KR_SECTOR_KR = {
    "IT":                     "IT",
    "FINANCIALS":             "금융",
    "HEALTHCARE":             "헬스케어",
    "CONSUMER_DISCRETIONARY": "경기소비재",
    "CONSUMER_STAPLES":       "필수소비재",
    "COMMUNICATION":          "커뮤니케이션",
    "INDUSTRIALS":            "산업재",
    "ENERGY":                 "에너지",
    "MATERIALS":              "소재",
    "UTILITIES":              "유틸리티",
    "REAL_ESTATE":            "부동산",
    "UNCLASSIFIED":           "기타",
}


# ── 상세 메타(meta) 기본 골격 ─────────────────────────────────────────
# KR/US 가 채울 수 있는 것만 채우고 나머지는 None → 프론트는 None이면 숨김.
def _empty_meta() -> dict:
    return {
        "fee_pct": None,            # A. 총보수 %
        "returns": {"m1": None, "m3": None, "y1": None},  # B. 기간 수익률 %
        "deviation_pct": None,      # C. 괴리율 %(KR만)
        "tracking_err_pct": None,   # C. 추적오차 %(KR만)
        "net_inflow": None,         # D. 최근 순유입 {text, positive}(KR best-effort)
    }


def _safe_float(v):
    """문자열/숫자 → float, 실패 시 None. 절대 예외 안 던짐."""
    try:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace("%", "").replace(",", "").strip()
            if not v:
                return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _us_detail(ticker: str) -> dict:
    """yfinance funds_data → {"sectors":[...], "holdings":[...], "meta":{...}}.
    한 번의 Ticker 조회로 섹터 비중 + 상위 보유종목 + 메타를 동시에 추출."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    fd = tk.funds_data
    meta = _empty_meta()
    # A. 총보수 — fund_operations 의 'Annual Report Expense Ratio' 행 × 100
    try:
        fo = fd.fund_operations
        if fo is not None and "Annual Report Expense Ratio" in fo.index:
            col = ticker if ticker in fo.columns else fo.columns[0]
            er = _safe_float(fo.loc["Annual Report Expense Ratio", col])
            if er is not None and er >= 0:
                meta["fee_pct"] = round(er * 100.0, 2)
    except Exception as e:
        _LOG.debug("etf us fee %s: %s", ticker, e)
    # B. 기간 수익률 — history(1y) 종가에서 거래일 기준 계산
    try:
        hist = tk.history(period="1y", interval="1d", auto_adjust=True)
        closes = hist["Close"].dropna() if hist is not None and "Close" in hist else None
        if closes is not None and len(closes) >= 2:
            last = float(closes.iloc[-1])
            def _ret(days):
                if last <= 0 or len(closes) <= days:
                    return None
                base = float(closes.iloc[-1 - days])
                if base <= 0:
                    return None
                return round((last - base) / base * 100.0, 2)
            meta["returns"]["m1"] = _ret(21)
            meta["returns"]["m3"] = _ret(63)
            meta["returns"]["y1"] = _ret(252)
            # 1년 데이터가 252봉 미만이면 가장 오래된 봉 대비를 y1 로
            if meta["returns"]["y1"] is None and len(closes) >= 2:
                base = float(closes.iloc[0])
                if base > 0:
                    meta["returns"]["y1"] = round((last - base) / base * 100.0, 2)
    except Exception as e:
        _LOG.debug("etf us returns %s: %s", ticker, e)
    # C. 괴리율/추적오차 — US 생략(None)
    # 섹터 비중
    sectors = []
    for key, w in (fd.sector_weightings or {}).items():
        try:
            pct = float(w) * 100.0
        except (TypeError, ValueError):
            continue
        if pct <= 0:
            continue
        sectors.append({"label": _US_SECTOR_KR.get(str(key).lower(), str(key)),
                        "weight_pct": round(pct, 1)})
    sectors.sort(key=lambda r: r["weight_pct"], reverse=True)
    # 상위 보유종목 (Name + Holding Percent)
    holdings = []
    try:
        th = fd.top_holdings
        if th is not None and len(th):
            for sym, row in th.iterrows():
                try:
                    pct = float(row["Holding Percent"]) * 100.0
                except (TypeError, ValueError, KeyError):
                    continue
                if pct <= 0:
                    continue
                holdings.append({"name": str(row.get("Name") or sym),
                                 "ticker": str(sym), "weight_pct": round(pct, 2)})
    except Exception as e:
        _LOG.debug("etf us holdings %s: %s", ticker, e)
    return {"sectors": sectors, "holdings": holdings, "meta": meta}


def _kr_detail(ticker: str) -> dict:
    """네이버 ETF 분석 API → {"sectors":[...], "holdings":[...]}.
    sectorPortfolioList(섹터) + etfTop10MajorConstituentAssets(상위 종목)를 한 호출로."""
    code = ticker.split(".")[0].upper()
    # 한국 ETF 종목코드는 6자리 영숫자 — 신규 ETF는 0167A0 처럼 알파벳 포함.
    # (이전 isdigit() 가드는 알파벳 코드를 잘못 막아 상세가 비었음)
    if not (len(code) == 6 and code.isalnum()):
        return {"sectors": [], "holdings": []}
    url = "https://m.stock.naver.com/api/stock/%s/etfAnalysis" % code
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) scanner-etf",
            "Referer": "https://m.stock.naver.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    meta = _empty_meta()
    # A. 총보수
    meta["fee_pct"] = _safe_float(data.get("totalFee"))
    # C. 괴리율 / 추적오차 (KR만)
    meta["deviation_pct"] = _safe_float(data.get("deviationRate"))
    meta["tracking_err_pct"] = _safe_float(data.get("chaseErrorRate"))
    # B. 기간 수익률 — returnPerformanceList: [{periodTypeCode, value}]
    try:
        rmap = {}
        for r in (data.get("returnPerformanceList") or []):
            code_ = str(r.get("periodTypeCode") or "")
            rmap[code_] = _safe_float(r.get("value"))
        meta["returns"]["m1"] = rmap.get("M1")
        meta["returns"]["m3"] = rmap.get("M3")
        meta["returns"]["y1"] = rmap.get("Y1")
    except Exception as e:
        _LOG.debug("etf kr returns %s: %s", code, e)
    # D. 자금 순유입(best-effort) — cumulativeNetInflowList dict, 최근 1개월(1m) 사용.
    #    값은 "1조 9,645억" / "-230억" 같은 한글 포맷 문자열 → 부호만 신뢰해 표기.
    try:
        ni = data.get("cumulativeNetInflowList")
        if isinstance(ni, dict):
            txt = ni.get("cumulativeNetInflow1m") or ni.get("cumulativeNetInflow1w") \
                or ni.get("cumulativeNetInflow1d")
            if txt:
                s = str(txt).strip()
                positive = not s.lstrip().startswith("-")
                meta["net_inflow"] = {"text": s, "positive": positive,
                                      "period": "1개월"}
    except Exception as e:
        _LOG.debug("etf kr netinflow %s: %s", code, e)
    # 섹터
    sectors = []
    for r in (data.get("sectorPortfolioList") or []):
        try:
            pct = float(r.get("weight"))
        except (TypeError, ValueError):
            continue
        if pct <= 0:
            continue
        code_ = str(r.get("detailTypeCode") or "")
        sectors.append({"label": _KR_SECTOR_KR.get(code_, code_ or "기타"),
                        "weight_pct": round(pct, 1)})
    sectors.sort(key=lambda r: r["weight_pct"], reverse=True)
    # 상위 보유종목
    holdings = []
    for r in (data.get("etfTop10MajorConstituentAssets") or []):
        try:
            # etfWeight 는 "35.32%" 형태 문자열 — % / 콤마 제거 후 파싱
            pct = float(str(r.get("etfWeight") or "").replace("%", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if pct <= 0:
            continue
        nm = str(r.get("itemName") or r.get("itemCode") or "").strip()
        if not nm:
            continue
        holdings.append({"name": nm, "ticker": str(r.get("itemCode") or ""),
                         "weight_pct": round(pct, 2)})
    holdings.sort(key=lambda r: r["weight_pct"], reverse=True)
    return {"sectors": sectors, "holdings": holdings, "meta": meta}


def get_etf_sectors(ticker: str) -> dict:
    """
    ETF 한 종목의 구성 정보(섹터 비중 + 상위 보유종목) 반환. per-ticker TTL 캐시.

    절대 예외를 던지지 않는다 — 실패 시 빈 리스트 + stale.
    반환: {"ticker", "sectors":[{label,weight_pct}], "holdings":[{name,ticker,weight_pct}], "stale"}
    """
    ticker = (ticker or "").strip()
    empty = {"ticker": ticker, "sectors": [], "holdings": [],
             "meta": _empty_meta(), "stale": True}
    if not ticker:
        return empty

    def _pack(d, stale):
        # 캐시 별칭 차단 — 내부 리스트/딕트를 깊은 복사해서 반환
        return {"ticker": ticker,
                "sectors": copy.deepcopy(d.get("sectors", [])),
                "holdings": copy.deepcopy(d.get("holdings", [])),
                "meta": copy.deepcopy(d.get("meta") or _empty_meta()),
                "stale": stale}

    now = time.time()
    with _SECTOR_LOCK:
        hit = _SECTOR_CACHE.get(ticker)
        if hit is not None and (now - hit[0]) < _SECTOR_TTL_SEC:
            return _pack(hit[1], False)

    is_kr = ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")
    try:
        detail = _kr_detail(ticker) if is_kr else _us_detail(ticker)
    except Exception as e:
        _LOG.warning("etf: detail %s failed: %s", ticker, e)
        # stale-while-error: 직전 캐시가 있으면 그대로 반환
        with _SECTOR_LOCK:
            hit = _SECTOR_CACHE.get(ticker)
            if hit is not None:
                return _pack(hit[1], True)
        return empty

    with _SECTOR_LOCK:
        # FIFO eviction — 캐시 무한 증가 방지
        if len(_SECTOR_CACHE) >= _SECTOR_CACHE_MAX and ticker not in _SECTOR_CACHE:
            try:
                oldest = min(_SECTOR_CACHE, key=lambda k: _SECTOR_CACHE[k][0])
                _SECTOR_CACHE.pop(oldest, None)
            except ValueError:
                pass
        _SECTOR_CACHE[ticker] = (now, detail)
    return _pack(detail, False)


# ── F. 섹터 로테이션 히트맵 ──────────────────────────────────────────────
# 미국 섹터 SPDR + 한국 테마 ETF 의 M1/M3 수익률을 1회 배치로 모아 색 강도용
# 숫자만 제공. B 의 수익률 로직(거래일 기준)을 재사용한다.
# 신규 데이터 소스 없음(yfinance history 배치). 자체 TTL 캐시 + stale.
_ROTATION_US: list[tuple[str, str]] = [
    ("XLK", "기술"), ("XLF", "금융"), ("XLE", "에너지"), ("XLV", "헬스케어"),
    ("XLY", "경기소비재"), ("XLP", "필수소비재"), ("XLI", "산업재"),
    ("XLB", "소재"), ("XLU", "유틸리티"), ("XLRE", "부동산"),
    ("XLC", "커뮤니케이션"), ("SMH", "반도체"),
]
_ROTATION_KR: list[tuple[str, str]] = [
    ("091160.KS", "반도체"), ("305720.KS", "2차전지"), ("466920.KS", "조선"),
    ("396500.KS", "Fn반도체"), ("465580.KS", "빅테크TOP7"), ("069500.KS", "코스피200"),
]

_ROTATION_TTL_SEC = 60 * 60          # 1시간(수익률은 자주 안 변함)
_ROTATION_LOCK = threading.Lock()
_ROTATION_CACHE: dict | None = None
_ROTATION_CACHE_TS: float = 0.0


def _rotation_returns_from_sub(sub) -> dict:
    """yfinance 서브프레임 종가 → {m1, m3} 거래일 기준 수익률%. 실패 시 None."""
    out = {"m1": None, "m3": None}
    try:
        closes = sub["Close"].dropna()
        if len(closes) < 2:
            return out
        last = float(closes.iloc[-1])
        if last <= 0:
            return out
        def _ret(days):
            if len(closes) <= days:
                return None
            base = float(closes.iloc[-1 - days])
            if base <= 0:
                return None
            return round((last - base) / base * 100.0, 2)
        out["m1"] = _ret(21)
        out["m3"] = _ret(63)
    except Exception:
        pass
    return out


def _build_rotation() -> dict:
    pairs = [("US", t, l) for t, l in _ROTATION_US] + \
            [("KR", t, l) for t, l in _ROTATION_KR]
    symbols = [t for _, t, _ in pairs]
    import yfinance as yf
    df = yf.download(symbols, period="6mo", interval="1d", progress=False,
                     group_by="ticker", threads=True, auto_adjust=True)
    level0 = set(df.columns.get_level_values(0)) if hasattr(df.columns, "get_level_values") else set()
    us_out, kr_out = [], []
    for region, ticker, label in pairs:
        try:
            sub = df[ticker] if ticker in level0 else None
            if sub is None:
                continue
            r = _rotation_returns_from_sub(sub)
            if r["m1"] is None and r["m3"] is None:
                continue
            (us_out if region == "US" else kr_out).append(
                {"ticker": ticker, "label": label, "m1": r["m1"], "m3": r["m3"]})
        except Exception as e:
            _LOG.debug("etf rotation %s: %s", ticker, e)
    return {
        "us": us_out, "kr": kr_out,
        "ts": datetime.now(_KST).isoformat(timespec="seconds"),
        "stale": False,
    }


def get_etf_rotation(force: bool = False) -> dict:
    """섹터 로테이션 히트맵 데이터. TTL 캐시 + stale-while-error. 절대 예외 안 던짐."""
    global _ROTATION_CACHE, _ROTATION_CACHE_TS
    now = time.time()
    with _ROTATION_LOCK:
        cached = _ROTATION_CACHE
        fresh = cached is not None and (now - _ROTATION_CACHE_TS) < _ROTATION_TTL_SEC
        if fresh and not force:
            return cached
    try:
        payload = _build_rotation()
        if not payload["us"] and not payload["kr"]:
            raise RuntimeError("empty rotation")
        with _ROTATION_LOCK:
            _ROTATION_CACHE = payload
            _ROTATION_CACHE_TS = time.time()
        return payload
    except Exception as e:
        _LOG.warning("etf: rotation build failed, serving stale: %s", e)
        with _ROTATION_LOCK:
            if _ROTATION_CACHE is not None:
                stale = dict(_ROTATION_CACHE)
                stale["stale"] = True
                return stale
        return {"us": [], "kr": [],
                "ts": datetime.now(_KST).isoformat(timespec="seconds"),
                "stale": True}
