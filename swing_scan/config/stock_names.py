"""config/stock_names.py — 종목코드 → 종목명 단일 조회 유틸리티.

서버 기동 시 종목명을 한 번에 로드해 메모리 캐시에 저장.
이후 요청은 캐시만 조회 → 네트워크 콜 0회.

우선순위:
  1. FinanceDataReader (StockListing) — 안정적, 빠름
  2. pykrx get_market_ticker_list — KRX API 응답 문제 시 실패할 수 있음
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class _PykrxNoiseFilter(logging.Filter):
    """pykrx의 comm/util.py가 root 로거로 호출하는 깨진
    ``logging.info(args, kwargs)`` 레코드를 차단한다.

    pykrx 내부 버그: 포맷 인자 불일치로 ``record.getMessage()``가
    TypeError를 던져 매 호출마다 '--- Logging error ---' 스택을 토해낸다.
    이름 없는 root 로거로 찍히므로 ``getLogger("pykrx").setLevel()``로는
    못 막는다 → root 로거에 pathname 기준 필터를 건다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        pathname = (getattr(record, "pathname", "") or "").replace("\\", "/")
        return "/pykrx/" not in pathname


logging.getLogger().addFilter(_PykrxNoiseFilter())

_cache: dict[str, str] = {}
_loaded = False
_lock = threading.Lock()
_ALIASES: dict[str, str] = {
    "089030": "테크윙",
    "131290": "티에스이",
}


def _normalize_keys(code: str) -> list[str]:
    raw = str(code or "").strip().upper()
    if not raw:
        return []
    keys = [raw]
    base = raw
    for suf in (".KS", ".KQ"):
        if base.endswith(suf):
            base = base[:-len(suf)]
            break
    if base and base not in keys:
        keys.append(base)
    if base.isdigit():
        padded = base.zfill(6)
        for key in (padded, f"{padded}.KS", f"{padded}.KQ"):
            if key not in keys:
                keys.append(key)
    return keys


def _load_fdr() -> bool:
    """FinanceDataReader로 KRX 전종목 이름 로드. 성공 시 True 반환."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        if df is None or df.empty:
            return False
        # 컬럼명 정규화 (Code/Symbol → 코드, Name → 이름)
        col_code = next((c for c in df.columns if c.lower() in ("code", "symbol", "종목코드")), None)
        col_name = next((c for c in df.columns if c.lower() in ("name", "종목명")), None)
        if col_code is None or col_name is None:
            logger.debug("[stock_names] FDR 컬럼 불명: %s", list(df.columns))
            return False
        count = 0
        for _, row in df.iterrows():
            code = str(row[col_code]).strip()
            code6 = code.zfill(6) if code.isdigit() else code
            name = str(row[col_name]).strip()
            if code6 and name and name != code6:
                for key in _normalize_keys(code6):
                    _cache[key] = name
                count += 1
        logger.info("[stock_names] FDR %d개 종목명 로드 완료", count)
        return count > 0
    except Exception as e:
        logger.debug("[stock_names] FDR 로드 실패: %s", e)
        return False


def _load_pykrx() -> bool:
    """pykrx로 KOSPI/KOSDAQ 전종목 이름 로드. 성공 시 True 반환.

    pykrx의 root 로거 스팸은 모듈 로드 시 설치한 _PykrxNoiseFilter가 차단.
    """
    try:
        from pykrx import stock as krx
        count = 0
        for market in ("KOSPI", "KOSDAQ"):
            try:
                tickers = krx.get_market_ticker_list(market=market)
                for t in tickers:
                    name = krx.get_market_ticker_name(t)
                    if name and name != t:
                        for key in _normalize_keys(t):
                            _cache[key] = name
                        count += 1
            except Exception as e:
                logger.debug("[stock_names] pykrx %s 로드 실패: %s", market, e)
        if count > 0:
            logger.info("[stock_names] pykrx %d개 종목명 로드 완료", count)
        return count > 0
    except Exception as e:
        logger.debug("[stock_names] pykrx 로드 실패: %s", e)
        return False


def _load_all() -> None:
    """FDR → pykrx 순으로 시도해 전종목 이름 로드."""
    global _loaded
    try:
        ok = _load_fdr()
        if not ok:
            logger.debug("[stock_names] FDR 실패 → pykrx 시도")
            _load_pykrx()
    finally:
        _loaded = True


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        with _lock:
            if not _loaded:
                _load_all()


def get_name(code: str) -> str:
    """종목코드 → 종목명. 없으면 코드 그대로 반환."""
    if not code:
        return code
    _ensure_loaded()
    for key in _normalize_keys(code):
        if key in _cache:
            return _cache[key]
    base = _normalize_keys(code)
    if base:
        short = base[0].split(".")[0]
        if short in _ALIASES:
            return _ALIASES[short]
    return code


def preload_async() -> None:
    """서버 기동 직후 백그라운드 스레드로 미리 로드."""
    t = threading.Thread(target=_ensure_loaded, daemon=True, name="stock-names-preload")
    t.start()
