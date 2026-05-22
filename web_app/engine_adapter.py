"""
engine_adapter.py — quant_nexus_v20.py 엔진을 tkinter 없이 사용하는 어댑터
Flask 웹앱이 이 클래스를 통해 스캔 기능을 호출한다.
"""
import sys
import os
import threading
import logging
import concurrent.futures
from collections import OrderedDict

# 프로젝트 경로 추가 (quant_nexus_v20.py가 있는 디렉토리)
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# quant_nexus_v20 import
# Windows에서 tkinter는 import만으로 GUI를 띄우지 않음 — 안전하게 import 가능
import quant_nexus_v20 as _qn


class ScanAdapter:
    """
    QuantNexusApp.analyze_ticker()를 tkinter 없이 실행하는 어댑터.
    analyze_ticker가 self.*로 접근하는 모든 속성을 직접 보유하여
    unbound method 호출(_qn.QuantNexusApp.analyze_ticker(self, ticker))이 동작한다.
    """

    def __init__(self, market: str = "US", strategy: str = "BALANCED") -> None:
        self._market = market
        self._strategy = strategy

        # ── analyze_ticker가 사용하는 속성 (QuantNexusApp 인터페이스 호환) ──
        self.cache          = _qn.DataCache()
        self.engine         = _qn.WallStreetQuantStrategies()
        self.vix_value      = self._fetch_vol_index(market)
        self._scan_strategy = strategy
        self._scan_market   = market
        self._stats_lock    = threading.Lock()
        self.stats          = {
            "cache_hits": 0, "cache_misses": 0,
            "scanned": 0, "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0,
        }
        self._naver_target_cache: dict        = {}
        self._naver_target_meta:  dict        = {}
        self._naver_fund_cache:   dict        = {}
        self._committee_cache:    OrderedDict = OrderedDict()
        self._committee_cache_max              = 1000

        # ── 네이버 캐시 파일 경로 (원본 엔진과 동일 위치) ──
        self._naver_cache_path = os.path.join(_BASE, "naver_target_cache.pkl")
        self._naver_fund_cache_path = os.path.join(_BASE, "naver_fund_cache.pkl")
        # 기존 캐시 로드
        _qn.QuantNexusApp._load_naver_cache(self)
        _qn.QuantNexusApp._load_naver_fund_cache(self)

        # ── 섹터·종목 데이터 초기화 ──────────────────────────────────────────
        _qn.QuantNexusApp._init_sector_data(self)
        # 클래스 속성 인스턴스에 직접 복사 (_analyze_ticker에서 self.*로 접근)
        self.KR_NAMES = _qn.QuantNexusApp.KR_NAMES
        self.US_DESC  = _qn.QuantNexusApp.US_DESC
        self.KR_DESC  = _qn.QuantNexusApp.KR_DESC

        # market별 flat 섹터 dict {섹터명: [ticker, ...]} 빌드
        self._sectors: dict[str, list[str]] = {}
        self._build_sectors()

    # ── 내부 초기화 ───────────────────────────────────────────────────────

    @staticmethod
    def _fetch_vol_index(market: str) -> float:
        """VIX 종가. 실패 시 20.0 fallback.

        KR/US 모두 ^VIX를 사용한다 — 점수계의 VIX smooth band(12~45)는
        양쪽 시장에 동일하게 적용되며, ^VKOSPI는 Yahoo Finance에서 제거됨.
        """
        try:
            import yfinance as _yf
            v = _yf.Ticker("^VIX").history(period="5d")
            if not v.empty:
                return float(v["Close"].iloc[-1])
        except Exception as e:
            logging.warning("[Adapter] vol index fetch failed (%s): %s", market, e)
        return 20.0

    def _build_sectors(self) -> None:
        raw = self.kr_sectors if self._market == "KR" else self.us_sectors
        sub_kr = getattr(self, 'us_sector_labels_kr', {}) if self._market != "KR" else {}
        for cat_data in raw.values():
            for subcat, tickers in cat_data.items():
                self._sectors[sub_kr.get(subcat, subcat)] = tickers

    # ── QuantNexusApp이 사용하는 메서드 (tkinter 콜백 대체) ──────────────

    def _log(self, msg: str) -> None:
        logging.debug("[ScanAdapter] %s", msg)

    def _fetch_naver_target(self, ticker: str):
        """(DEPRECATED) DCF 목표가로 대체됨 — 호환성 유지용."""
        return _qn.QuantNexusApp._fetch_naver_target(self, ticker)

    def _fetch_naver_fundamentals(self, ticker: str):
        """네이버 재무 데이터 — 원본 엔진 메서드 위임."""
        return _qn.QuantNexusApp._fetch_naver_fundamentals(self, ticker)

    def _save_naver_cache(self):
        """(DEPRECATED) DCF 목표가로 대체됨 — 호환성 유지용."""
        _qn.QuantNexusApp._save_naver_cache(self)

    def _save_naver_fund_cache(self):
        """네이버 재무 캐시 저장 — 원본 엔진 메서드 위임."""
        _qn.QuantNexusApp._save_naver_fund_cache(self)


    def _nomura_sector_hint(self, ticker: str, info: dict) -> str:
        """Forward QuantNexusApp's sector routing helper onto the adapter instance."""
        return _qn.QuantNexusApp._nomura_sector_hint(self, ticker, info)

    def _resolve_display_name(self, ticker: str, current_name: str = "") -> str:
        """Forward QuantNexusApp's display name resolver onto the adapter instance."""
        return _qn.QuantNexusApp._resolve_display_name(self, ticker, current_name)
    # ── 공개 API ─────────────────────────────────────────────────────────

    def get_sectors(self) -> dict[str, list[str]]:
        """market별 섹터→종목 매핑 반환 (flat)."""
        return self._sectors

    def get_sector_groups(self) -> dict[str, list[str]]:
        """카테고리 → 서브섹터 리스트 반환 (사이드바 그룹 표시용)."""
        raw = self.kr_sectors if self._market == "KR" else self.us_sectors
        if self._market == "KR":
            return {cat: list(subsectors.keys()) for cat, subsectors in raw.items()}
        cat_kr = getattr(self, 'us_sector_category_kr', {})
        sub_kr = getattr(self, 'us_sector_labels_kr', {})
        result = {}
        for cat, subsectors in raw.items():
            translated_cat = cat
            for en, kr in cat_kr.items():
                if en in cat:
                    translated_cat = cat.replace(en, kr)
                    break
            result[translated_cat] = [sub_kr.get(s, s) for s in subsectors.keys()]
        return result

    def analyze_ticker(self, ticker: str, *, prefer_cache: bool = False, cache_only: bool = False) -> dict | None:
        """단일 종목 분석 — 캐시 우선/캐시 전용 모드를 지원한다."""
        if prefer_cache:
            cached = self.cache.get(f"{ticker}__{self._scan_strategy}", max_age_minutes=60 * 24 * 7)
            if cached:
                return cached
            if cache_only:
                return None
        return _qn.QuantNexusApp._analyze_ticker(self, ticker)

    def scan_sector(self, sector: str, *, max_workers: int = int(os.environ.get("SCAN_WORKERS", "4")), prefer_cache: bool = False, cache_only: bool = False) -> list[dict]:
        """특정 섹터 종목을 병렬 분석 후 TotalScore 내림차순 반환."""
        tickers = self._sectors.get(sector, [])
        results: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self.analyze_ticker, t, prefer_cache=prefer_cache, cache_only=cache_only): t
                for t in tickers
            }
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                    if r:
                        r["Sector"] = sector
                        results.append(r)
                except Exception as e:
                    logging.error("scan_sector error: %s", e)
        self._attach_sector_residual(results)
        results.sort(key=lambda x: x.get("TotalScore", 0), reverse=True)
        return results

    def scan_all(self, *, max_workers: int = int(os.environ.get("SCAN_WORKERS", "4")), prefer_cache: bool = False, cache_only: bool = False) -> list[dict]:
        """전체 섹터 종목을 병렬 분석 (중복 ticker 제거) 후 TotalScore 내림차순 반환."""
        ticker_sector: dict[str, str] = {}
        for sector, tickers in self._sectors.items():
            for t in tickers:
                if t not in ticker_sector:
                    ticker_sector[t] = sector

        results: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self.analyze_ticker, t, prefer_cache=prefer_cache, cache_only=cache_only): (t, s)
                for t, s in ticker_sector.items()
            }
            for fut in concurrent.futures.as_completed(futures):
                ticker, sector = futures[fut]
                try:
                    r = fut.result()
                    if r:
                        # 표시 섹터는 큐레이션된 내부 분류로 고정.
                        # (_analyze_ticker가 노무라용으로 채운 yfinance
                        #  영문 섹터가 새어나오지 않도록 scan_sector와 동일하게 덮어쓴다)
                        r["Sector"] = sector
                        results.append(r)
                except Exception as e:
                    logging.error("scan_all [%s] error: %s", ticker, e)
        self._attach_sector_residual(results)
        results.sort(key=lambda x: x.get("TotalScore", 0), reverse=True)
        return results

    @staticmethod
    def _attach_sector_residual(rows: list[dict]) -> None:
        """각 종목의 TotalScore에서 동일 섹터 평균을 차감한 값을 추가.

        Fama-French 스타일 sector-neutral residual — 섹터 전체 강세에 묻혀
        진짜 alpha가 보이지 않는 문제를 보정한다. TotalScore는 그대로 두고
        SectorResidual 필드만 부착해 UI 차원에서 선택적으로 활용한다.
        """
        from collections import defaultdict
        bucket: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            s = r.get("Sector") or ""
            ts = r.get("TotalScore")
            if isinstance(ts, (int, float)):
                bucket[s].append(float(ts))
        means = {s: (sum(v) / len(v)) for s, v in bucket.items() if v}
        for r in rows:
            s = r.get("Sector") or ""
            ts = r.get("TotalScore")
            if isinstance(ts, (int, float)) and s in means:
                r["SectorResidual"] = round(float(ts) - means[s], 2)
