"""
engine_adapter.py — quant_nexus_v20.py 엔진을 tkinter 없이 사용하는 어댑터
Flask 웹앱이 이 클래스를 통해 스캔 기능을 호출한다.
"""
import sys
import os
import time
import random
import threading
import logging
import concurrent.futures
from collections import OrderedDict, defaultdict

# ── 프로세스-전역 VIX 캐시 ────────────────────────────────────────────────
# KR/US 어댑터가 거의 동시에 생성될 때 ^VIX 를 중복 호출해 429를 자초하던 문제 해결.
# TTL 5분, 실패 시 한 번 지수 backoff 재시도.
_VIX_CACHE: dict = {"value": None, "ts": 0.0}
_VIX_CACHE_LOCK = threading.Lock()
_VIX_TTL_SEC = 300.0
_VIX_BG_INFLIGHT = {"on": False}
_VIX_BG_LOCK = threading.Lock()

# 프로젝트 경로 추가 (quant_nexus_v20.py가 있는 디렉토리)
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# quant_nexus_v20 import
# Windows에서 tkinter는 import만으로 GUI를 띄우지 않음 — 안전하게 import 가능
import quant_nexus_v20 as _qn
from speculative_themes import apply_speculative_correction, apply_to_row
from micro_outlier import annotate as _annotate_micro_outlier

try:
    import bottleneck_screen as _bottleneck
except Exception:
    _bottleneck = None  # type: ignore

# 선제적 레짐 파악 모듈 (모듈1~4). import 실패해도 스캐너는 정상 동작.
try:
    import regime_integration as _regime
except Exception as _e:  # pragma: no cover
    _regime = None  # type: ignore
    logging.info("[Adapter] regime_integration 미사용: %s", _e)


def _attach_regime(rows: list[dict], market: str) -> None:
    """레짐 전환확률·OFI·리드래그 부착 + RegimeEntryScore 산출 (never throw)."""
    if _regime is None:
        return
    try:
        _regime.attach_all(rows, market)
    except Exception as e:  # noqa: BLE001
        logging.debug("[Adapter] regime attach 실패: %s", e)


def _scan_sort_key(row: dict):
    """REGIME_RANK=1 이면 RegimeEntryScore, 아니면 기존 TotalScore 정렬."""
    if _regime is not None:
        try:
            return _regime.rank_key(row)
        except Exception:
            pass
    return row.get("TotalScore", 0)

try:
    from web_app.valuation_context import attach_valuation_context as _attach_val_ctx
except Exception:
    try:
        from valuation_context import attach_valuation_context as _attach_val_ctx
    except Exception:
        _attach_val_ctx = None  # type: ignore


def _attach_valuation_context(rows: list[dict]) -> None:
    """각 종목에 밸류에이션 맥락 지표(ValPctile, SectorRelPE, PriceInLevel)를 부착."""
    if _attach_val_ctx is None:
        return
    # 섹터별 피어 그룹 구성
    sector_groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = r.get("Sector") or ""
        if s:
            sector_groups[s].append(r)
    for r in rows:
        try:
            s = r.get("Sector") or ""
            peers = sector_groups.get(s, [])
            _attach_val_ctx(r, peers)
        except Exception:
            r.setdefault("ValPctile", None)
            r.setdefault("SectorRelPE", None)
            r.setdefault("PriceInLevel", None)


def _attach_bottleneck(rows: list[dict]) -> None:
    """각 종목에 공급망 병목 근접도(BottleneckScore/Layers/Top)를 부착.

    사업설명·섹터 텍스트를 희소층 키워드와 매칭하는 '초벌 패스' 프록시.
    증거기반 심층 판단이 아니라 후보 발굴용 — 종목별 심층은 별도 브리프로.
    """
    if _bottleneck is None:
        return
    for r in rows:
        try:
            txt = " ".join(str(r.get(k, "") or "") for k in
                           ("Desc", "Industry", "About", "CompanyInfo"))
            bp = _bottleneck.bottleneck_proximity(txt, r.get("Sector", ""))
            r["BottleneckScore"] = bp["score"]
            r["BottleneckLayers"] = bp["layers"]
            r["BottleneckTop"] = bp["top_layer"]
            # 병목 ∩ 진입타이밍 게이트 — 폭등 꼭대기/과매수 분리
            sig = _bottleneck.bottleneck_entry_signal(
                bottleneck_score=bp["score"],
                entry_score=r.get("EntryScore"),
                rsi=r.get("RSI"),
                rs_rating=r.get("RSRating"),
                mom_3m=r.get("_Mom3M"),
                regime=r.get("Regime"),
            )
            r["BottleneckEntry"] = sig["label"]
            r["BottleneckEntryPass"] = sig["pass_gate"]
            r["BottleneckEntryReasons"] = sig["reasons"]
        except Exception:
            r.setdefault("BottleneckScore", 0)
            r.setdefault("BottleneckLayers", [])
            r.setdefault("BottleneckTop", None)
            r.setdefault("BottleneckEntry", None)
            r.setdefault("BottleneckEntryPass", False)
            r.setdefault("BottleneckEntryReasons", [])
try:
    # web_app 디렉토리 보장 (engine_adapter 가 외부에서 import 될 때 대비)
    _WEB_APP_DIR = os.path.dirname(os.path.abspath(__file__))
    if _WEB_APP_DIR not in sys.path:
        sys.path.insert(0, _WEB_APP_DIR)
    from symbol_alias import filter_symbols as _filter_symbols  # type: ignore
except Exception as _e:  # pragma: no cover
    logging.warning("[Adapter] symbol_alias import failed → DELISTED filter disabled: %s", _e)
    def _filter_symbols(xs):  # fallback no-op
        return list(xs)


# ── 주가지수 구성종목 매핑 ────────────────────────────────────────────────
# scripts/build_index_membership.py 가 생성한 index_membership.json 을 읽어
# ticker → ["SP500","NDX",...] 역인덱스를 만든다. 파일이 없으면 빈 맵으로 동작
# (지수 필터가 단순히 비활성화될 뿐, 스캔 자체에는 영향 없음).
_INDEX_KEYS = ("SP500", "SP400", "SP600", "NDX")  # 표시 우선순위
_INDEX_REVERSE: dict[str, list[str]] | None = None
_INDEX_META: dict = {}  # 명단 생성일·신선도 (point-in-time 규율용)
_INDEX_LOCK = threading.Lock()  # _INDEX_REVERSE lazy 초기화 동시성 보호(US/KR 동시 생성)
# 분기 리밸런싱 주기 → 100일 넘으면 명단이 늙은 것으로 간주(권고3: 갱신 캐던스 고정)
_INDEX_STALE_DAYS = 100


def index_membership_meta() -> dict:
    """명단 기준일·신선도 메타 반환 (UI '명단 기준일' 표시 + 갱신 알림용)."""
    _load_index_membership()
    return dict(_INDEX_META)


def _load_index_membership() -> dict[str, list[str]]:
    """ticker → 편입 지수 리스트(표시 우선순위 정렬) 역인덱스를 lazy 로드."""
    global _INDEX_REVERSE, _INDEX_META
    if _INDEX_REVERSE is not None:
        return _INDEX_REVERSE
    with _INDEX_LOCK:  # 이중확인 락 — US/KR 어댑터 동시 생성 시 중복 로드·경고 이중출력 방지
        if _INDEX_REVERSE is not None:
            return _INDEX_REVERSE
        rev: dict[str, list[str]] = {}
        meta: dict = {"generated": None, "stale_days": None, "is_stale": False, "tickers": 0}
        try:
            import json
            from datetime import datetime as _dt
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index_membership.json")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for key in _INDEX_KEYS:
                for sym in data.get(key, []):
                    rev.setdefault(str(sym).upper(), []).append(key)
            # 우선순위대로 정렬 (SP500 → SP400 → SP600 → NDX)
            order = {k: i for i, k in enumerate(_INDEX_KEYS)}
            for sym in rev:
                rev[sym].sort(key=lambda k: order.get(k, 99))
            # 권고3: 명단 생성일 기준 신선도 점검 — 분기 리밸런싱을 놓치면 경고.
            gen = (data.get("_meta") or {}).get("generated")
            meta["generated"] = gen
            meta["tickers"] = len(rev)
            meta["counts"] = {k: len(data.get(k, [])) for k in _INDEX_KEYS}
            if gen:
                try:
                    gd = _dt.strptime(str(gen)[:10], "%Y-%m-%d")
                    days = (_dt.now() - gd).days
                    meta["stale_days"] = days
                    meta["is_stale"] = days > _INDEX_STALE_DAYS
                    if meta["is_stale"]:
                        logging.warning(
                            "[Adapter] 지수 명단이 %d일 경과(>%d) — "
                            "scripts/build_index_membership.py 재실행 권장",
                            days, _INDEX_STALE_DAYS,
                        )
                except Exception:  # noqa: BLE001
                    pass
            logging.info("[Adapter] index membership loaded: %d tickers (기준일 %s)", len(rev), gen)
        except FileNotFoundError:
            logging.warning("[Adapter] index_membership.json 없음 → 지수 필터 비활성화")
        except Exception as e:  # noqa: BLE001
            logging.warning("[Adapter] index membership 로드 실패: %s", e)
        _INDEX_REVERSE = rev
        _INDEX_META = meta
        return rev


def _attach_midcap_alpha(rows: list[dict]) -> None:
    """SP400 편입 종목에 미드캡 알파 시그널을 부착한다.

    기존 row dict 필드(TotalScore, RSRating, _VolRatio, _MarketCap 등)에서
    경량 파생하여 SEC EDGAR 호출 없이 빠르게 계산한다.
    """
    for r in rows:
        indices = r.get("Indices") or []
        if "SP400" not in indices:
            continue

        ts = r.get("TotalScore") or 0
        rs = r.get("RSRating") or 0
        mcap = r.get("_MarketCap") or 0
        vol_ratio = r.get("_VolRatio") or 1.0
        eps = r.get("_EPS")
        moat = r.get("MoatBonus") or 0

        # 1) 승격 충족도: 시총 접근도 + 수익성 + RS 모멘텀
        sp500_floor = 18e9
        mcap_prox = min(40, max(0, (mcap / sp500_floor) * 40)) if mcap > 0 else 0
        profit = 30 if (eps is not None and eps > 0) else (10 if eps is None else 0)
        rs_part = min(15, rs / 100 * 15) if rs > 0 else 0
        promo = min(100, round(mcap_prox + profit + rs_part + min(15, moat * 5)))

        # 2) 매집 패턴: 거래량 배수 + 점수 기반 프록시
        if vol_ratio > 1.5 and ts > 55:
            accum = min(100, round(30 + (vol_ratio - 1) * 30 + (ts - 50) * 0.5))
        elif vol_ratio > 1.2:
            accum = min(80, round(20 + (vol_ratio - 1) * 25 + (ts - 40) * 0.3))
        else:
            accum = min(60, round(max(0, (vol_ratio - 0.8) * 50 + (ts - 40) * 0.2)))

        # 3) 복합 점수
        alpha = round(promo * 0.4 + accum * 0.3 + min(100, ts) * 0.3)

        # 4) 라벨
        labels = []
        if promo >= 70:
            labels.append("승격 임박")
        if accum >= 60:
            labels.append("매집 초기")
        if rs >= 80 and ts >= 65:
            labels.append("성장 가속")
        label = " + ".join(labels) if labels else "모니터링"

        r["MidcapAlpha"] = alpha
        r["MidcapPromotion"] = promo
        r["MidcapAccum"] = accum
        r["MidcapLabel"] = label


def _attach_index_membership(rows: list[dict], *, compute_bucket: bool = True) -> None:
    """각 종목 row 에 Indices(편입 지수) + RSBucket(버킷 내 size-중립 상대강도)을 부착.

    권고1: 전체 유니버스가 아니라 같은 지수 버킷 안에서 RS를 백분위 랭크해
    size 베타에 오염된 가짜 주도주를 걸러낸다. 기존 RSRating 은 보존(추가 필드).

    compute_bucket: RSBucket(버킷 내 백분위)은 rows 가 '전체 유니버스'일 때만
    의미가 있다. 단일 섹터 스캔(scan_sector)은 (섹터∩지수) 소표본이라 백분위가
    왜곡되므로 False 로 호출해 RSBucket 계산을 생략한다(Indices 는 항상 부착).
    """
    rev = _load_index_membership()
    if not rev:
        return

    # 1) Indices 부착 + 대표 버킷 결정 (우선순위 첫 번째, 미편입은 OTHER)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sym = str(r.get("Ticker", "")).upper()
        ix = rev.get(sym, [])
        r["Indices"] = ix
        bucket = ix[0] if ix else "OTHER"
        r["RSBucketName"] = bucket
        groups[bucket].append(r)

    if not compute_bucket:
        return  # 섹터 스캔: 소표본 백분위는 오해를 부르므로 생략

    # 2) 버킷 내 RS 백분위 (1~99, 높을수록 동일-시총군 내 상대강도 우위)
    #    동률은 평균 순위로 처리해 같은 RS 가 같은 백분위를 받도록 한다.
    def _rs_key(r: dict):
        v = r.get("RS_WeightedRet")
        if v is None:
            v = r.get("MomentumScore")
        # 값 없으면 최약체로 정렬
        return (v is None, v if v is not None else -9e18)

    for bucket, members in groups.items():
        n = len(members)
        if n < 2:
            for r in members:
                r["RSBucket"] = 50
            continue
        ranked = sorted(members, key=_rs_key)  # 약 → 강
        i = 0
        while i < n:
            j = i
            kv = _rs_key(ranked[i])
            while j + 1 < n and _rs_key(ranked[j + 1]) == kv:
                j += 1  # 동률 구간 [i, j]
            pct = int(round(((i + j) / 2.0) / (n - 1) * 98)) + 1  # 평균 순위 → 1~99
            for t in range(i, j + 1):
                ranked[t]["RSBucket"] = pct
            i = j + 1


class ScanAdapter:
    """
    QuantNexusApp.analyze_ticker()를 tkinter 없이 실행하는 어댑터.
    analyze_ticker가 self.*로 접근하는 모든 속성을 직접 보유하여
    unbound method 호출(_qn.QuantNexusApp.analyze_ticker(self, ticker))이 동작한다.
    """

    def __init__(self, market: str = "KR", strategy: str = "BALANCED") -> None:
        self._market = market
        self._strategy = strategy

        # ── analyze_ticker가 사용하는 속성 (QuantNexusApp 인터페이스 호환) ──
        self.cache          = _qn.DataCache(os.path.join(_BASE, "cache_v19"))
        self.engine         = _qn.WallStreetQuantStrategies()
        # C1: VIX fetch는 cold start를 막지 않는다.
        # 캐시가 비어 있으면 20.0으로 출발하고 백그라운드에서 채운다.
        self.vix_value      = self._fetch_vol_index_nonblocking(market)
        self._scan_strategy = strategy
        self._scan_market   = market
        self._stats_lock    = threading.Lock()
        # yfinance 429 글로벌 cooldown 게이트 (원본 엔진과 동일 인터페이스)
        self._yf_cooldown_until = 0.0
        self._yf_cooldown_lock  = threading.Lock()
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

        # ── 병렬 초기화: pickle 로드 2건 + 섹터 데이터는 독립적이므로 동시 실행 ──
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as _init_ex:
            _f1 = _init_ex.submit(_qn.QuantNexusApp._load_naver_cache, self)
            _f2 = _init_ex.submit(_qn.QuantNexusApp._load_naver_fund_cache, self)
            _f3 = _init_ex.submit(_qn.QuantNexusApp._init_sector_data, self)
            _f1.result()
            _f2.result()
            _f3.result()
        # 클래스 속성 인스턴스에 직접 복사 (_analyze_ticker에서 self.*로 접근)
        self.KR_NAMES = _qn.QuantNexusApp.KR_NAMES
        self.US_DESC  = _qn.QuantNexusApp.US_DESC
        self.KR_DESC  = _qn.QuantNexusApp.KR_DESC

        # market별 flat 섹터 dict {섹터명: [ticker, ...]} 빌드
        self._sectors: dict[str, list[str]] = {}
        self._build_sectors()

    # ── 내부 초기화 ───────────────────────────────────────────────────────

    @classmethod
    def _fetch_vol_index_nonblocking(cls, market: str) -> float:
        """캐시 hit이면 즉시 반환. 없으면 20.0 fallback + 백그라운드로 fetch.
        cold start API 응답이 ^VIX 네트워크 5~25s에 묶이지 않게 한다.
        """
        now = time.time()
        with _VIX_CACHE_LOCK:
            cached = _VIX_CACHE["value"]
            cached_ts = _VIX_CACHE["ts"]
            if cached is not None and (now - cached_ts) < _VIX_TTL_SEC:
                return float(cached)
        # 중복 BG 호출 방지
        with _VIX_BG_LOCK:
            if not _VIX_BG_INFLIGHT["on"]:
                _VIX_BG_INFLIGHT["on"] = True
                threading.Thread(
                    target=cls._vix_bg_worker,
                    args=(market,),
                    daemon=True,
                    name="vix-bg-fetch",
                ).start()
        # stale 캐시라도 있으면 활용
        if cached is not None:
            return float(cached)
        return 20.0

    @classmethod
    def _vix_bg_worker(cls, market: str) -> None:
        try:
            cls._fetch_vol_index(market)
        finally:
            with _VIX_BG_LOCK:
                _VIX_BG_INFLIGHT["on"] = False

    @staticmethod
    def _fetch_vol_index(market: str) -> float:
        """VIX 종가. 실패 시 20.0 fallback.

        KR/US 모두 ^VIX를 사용한다 — 점수계의 VIX smooth band(12~45)는
        양쪽 시장에 동일하게 적용되며, ^VKOSPI는 Yahoo Finance에서 제거됨.

        프로세스-전역 캐시(TTL 5분) — KR/US 어댑터가 거의 동시에 생성될 때
        ^VIX 중복 호출로 자초한 429를 막는다. 실패 시 1회 backoff 재시도 후
        그래도 실패면 직전 캐시값(없으면 20.0)을 반환.
        """
        now = time.time()
        with _VIX_CACHE_LOCK:
            cached = _VIX_CACHE["value"]
            cached_ts = _VIX_CACHE["ts"]
            if cached is not None and (now - cached_ts) < _VIX_TTL_SEC:
                return float(cached)

        import yfinance as _yf
        # 모듈-레벨 yfinance cooldown 게이트 — _analyze_ticker와 동일 quota 공유.
        try:
            _qn._yf_cooldown_wait()
        except Exception:
            pass
        for attempt in range(3):
            if attempt == 1:
                time.sleep(random.uniform(1.0, 2.0))
            elif attempt == 2:
                time.sleep(random.uniform(3.0, 5.0))
            try:
                v = _yf.Ticker("^VIX").history(period="5d")
                if not v.empty:
                    val = float(v["Close"].iloc[-1])
                    with _VIX_CACHE_LOCK:
                        _VIX_CACHE["value"] = val
                        _VIX_CACHE["ts"] = time.time()
                    return val
            except Exception as e:
                msg = str(e)
                if "rate" in msg or "Too Many" in msg or "429" in msg:
                    logging.warning("[Adapter] vol index rate-limited (%s, attempt %d): %s", market, attempt, e)
                    try:
                        _qn._yf_mark_rate_limited(30.0)
                    except Exception:
                        pass
                else:
                    logging.warning("[Adapter] vol index fetch failed (%s): %s", market, e)
        if cached is not None and (now - cached_ts) < 900.0:
            logging.info("[VIX] stale cache used (%.1fmin old)", (now - cached_ts) / 60.0)
            return float(cached)
        if cached is not None:
            return float(cached)
        return 20.0

    def _build_sectors(self) -> None:
        raw = self.kr_sectors if self._market == "KR" else self.us_sectors
        sub_kr = getattr(self, 'us_sector_labels_kr', {}) if self._market != "KR" else {}
        for cat_data in raw.values():
            for subcat, tickers in cat_data.items():
                # normalize aliases (FB→META) + drop DELISTED (ATVI/TWTR/VMW/…)
                self._sectors[sub_kr.get(subcat, subcat)] = _filter_symbols(list(tickers))
        if self._market == "US":
            self._augment_index_universe()

    def _augment_index_universe(self) -> None:
        """지수 구성종목 중 큐레이션 유니버스에 빠진 종목을 보강한다.

        index_membership.json 을 기준으로, 어느 섹터에도 없는 지수 편입 종목을
        '지수보강' 서브섹터에 채워 넣어 S&P500/400/600·나스닥100 버킷이
        실제 편입 종목 수와 일치하도록 만든다. 한 종목은 우선순위가 가장 높은
        지수 하나에만 배정한다(SP500 > SP400 > SP600 > NDX). 명단이 갱신되면
        자동으로 동기화되므로 소스에 정적 목록을 손으로 넣지 않는다.
        """
        rev = _load_index_membership()
        if not rev:
            return
        have = {t for ts in self._sectors.values() for t in ts}
        label = {
            "SP500": "🗂️ S&P500 (지수보강)",
            "SP400": "🗂️ S&P400 미드캡 (지수보강)",
            "SP600": "🗂️ S&P600 스몰캡 (지수보강)",
            "NDX":   "🗂️ 나스닥100 (지수보강)",
        }
        # S&P600 스몰캡(저유동성 다수)은 INDEX_AUGMENT_SP600=0 으로 보강 제외 가능.
        skip_sp600 = os.environ.get("INDEX_AUGMENT_SP600", "1").strip().lower() in ("0", "false", "no")
        buckets: dict[str, list[str]] = {k: [] for k in label}
        for sym, idxs in rev.items():
            if sym in have or not idxs:
                continue
            primary = idxs[0]  # rev 는 우선순위 정렬되어 있음
            if primary in buckets:
                buckets[primary].append(sym)
        added = 0
        for key, syms in buckets.items():
            if key == "SP600" and skip_sp600:
                continue
            syms = _filter_symbols(sorted(syms))  # alias 정규화 + DELISTED 제거
            if syms:
                self._sectors[label[key]] = syms
                added += len(syms)
        if added:
            logging.info("[Adapter] 지수 유니버스 보강: +%d 종목 (빠진 구성종목 채움%s)",
                         added, ", S&P600 제외" if skip_sp600 else "")

    # ── QuantNexusApp이 사용하는 메서드 (tkinter 콜백 대체) ──────────────

    def _log(self, msg: str) -> None:
        logging.debug("[ScanAdapter] %s", msg)

    def _pre_build_scan_caches(self, tickers: list[str], *, cache_only: bool = False) -> None:
        """스캔 루프 전 1회 실행 — F5(종목명 dict) + F2b(KR 재무 병렬 사전 로드)."""
        # F5: 종목명 사전 구축
        _kr_names_d = getattr(self, "KR_NAMES", {})
        _us_names_d = getattr(_qn.QuantNexusApp, "US_NAMES", {})
        _sw = _qn._SWING_SCAN_STOCK_NAMES
        _name_pre: dict[str, str] = {}
        for _nt in tickers:
            _is_kr_nt = _nt.endswith(".KS") or _nt.endswith(".KQ")
            _nn = None
            if _is_kr_nt and _sw is not None:
                try:
                    _c6n = _nt.split(".")[0].zfill(6)
                    _nn2 = _sw.get_name(_c6n)
                    if _nn2 and _nn2 != _c6n:
                        _nn = _nn2
                except Exception:
                    pass
            if not _nn:
                _nn = _kr_names_d.get(_nt) if _is_kr_nt else _us_names_d.get(_nt)
            if _nn:
                _name_pre[_nt] = _nn
        self._ticker_name_cache = _name_pre
        # F2b: KR 재무 데이터 사전 병렬 로드
        # F2b: cache_only 모드에서는 네트워크 I/O 스킵 — quick-warm 속도 향상
        if self._market == "KR" and not cache_only:
            _fetch_fund = _qn.QuantNexusApp._fetch_naver_fundamentals
            _kr_uncached = [
                t for t in tickers
                if (t.endswith(".KS") or t.endswith(".KQ"))
                and t.split(".")[0] not in self._naver_fund_cache
            ]
            if _kr_uncached:
                logging.debug("[ScanAdapter] KR 재무 사전 로드 %d개", len(_kr_uncached))
                # max_workers 12 — urllib3 PoolManager(maxsize=16) 한도 내, KR fundamentals 사전 로드 가속
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as _nex:
                    list(_nex.map(lambda t: _fetch_fund(self, t), _kr_uncached))

    def _fetch_naver_fundamentals(self, ticker: str):
        """네이버 재무 데이터 — 원본 엔진 메서드 위임."""
        return _qn.QuantNexusApp._fetch_naver_fundamentals(self, ticker)

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
            # _analyze_ticker(quant_nexus_v20.py:4684)와 동일한 dated 키 포맷.
            # 키 포맷 불일치 시 cache_only 분기에서 종목이 대량 누락되어
            # /api/scan 이 일부 universe만 반환하던 버그를 잡는다.
            # 오늘 캐시가 없으면 최대 7일 이전까지 fallback — 주말·공휴일 대응.
            from datetime import datetime as _dt, timedelta as _td
            for _days_back in range(8):
                _date = (_dt.now() - _td(days=_days_back)).strftime("%Y%m%d")
                strategy_key = f"{ticker}__{self._scan_strategy}__{_date}"
                cached = self.cache.get(strategy_key, max_age_minutes=60 * 24 * (_days_back + 1))
                if cached:
                    return apply_to_row(cached)
            if cache_only:
                return None
        result = _qn.QuantNexusApp._analyze_ticker(self, ticker)
        return apply_to_row(result) if result else result

    def scan_sector(self, sector: str, *, max_workers: int = int(os.environ.get("SCAN_WORKERS", "8")), prefer_cache: bool = False, cache_only: bool = False) -> list[dict]:
        """특정 섹터 종목을 병렬 분석 후 TotalScore 내림차순 반환."""
        tickers = self._sectors.get(sector, [])
        self._pre_build_scan_caches(tickers, cache_only=cache_only)
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
        apply_speculative_correction(results)
        _annotate_micro_outlier(results)
        _attach_bottleneck(results)
        _attach_index_membership(results, compute_bucket=False)  # 섹터 스캔: 소표본 백분위 왜곡 방지
        _attach_midcap_alpha(results)
        _attach_valuation_context(results)
        _attach_regime(results, self._market)  # 모듈1~4: 레짐 전환확률·OFI·리드래그 → RegimeEntryScore
        results.sort(key=_scan_sort_key, reverse=True)
        return results

    def scan_all(self, *, max_workers: int = int(os.environ.get("SCAN_WORKERS", "8")), prefer_cache: bool = False, cache_only: bool = False) -> list[dict]:
        """전체 섹터 종목을 병렬 분석 (중복 ticker 제거) 후 TotalScore 내림차순 반환."""
        ticker_sector: dict[str, str] = {}
        for sector, tickers in self._sectors.items():
            for t in tickers:
                if t not in ticker_sector:
                    ticker_sector[t] = sector

        all_tickers = list(ticker_sector.keys())
        self._pre_build_scan_caches(all_tickers, cache_only=cache_only)
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
        apply_speculative_correction(results)
        _annotate_micro_outlier(results)
        _attach_bottleneck(results)
        _attach_index_membership(results)
        _attach_midcap_alpha(results)
        _attach_valuation_context(results)
        _attach_regime(results, self._market)  # 모듈1~4: 레짐 전환확률·OFI·리드래그 → RegimeEntryScore
        results.sort(key=_scan_sort_key, reverse=True)
        # forward IC 추적: BOTTLENECK_SNAPSHOT=1 일 때만 오늘 병목 등급 스냅샷 적재
        if os.environ.get("BOTTLENECK_SNAPSHOT") == "1":
            try:
                import datetime as _dt
                import bottleneck_ic as _bic
                n = _bic.record_snapshot(results, date=_dt.date.today())
                logging.info("[bottleneck_ic] 스냅샷 %d건 적재", n)
            except Exception as e:
                logging.debug("[bottleneck_ic] 스냅샷 실패: %s", e)
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
