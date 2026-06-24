"""
app.py — 종목분석기 Flask 웹 서버
engine_adapter.ScanAdapter를 JSON API로 서빙하고 HTML 템플릿을 렌더링한다.

실행: python web_app/app.py
접속: http://localhost:5000
"""
from __future__ import annotations

import sys
import os
import io
import base64
import json
import html
import logging
import threading
import queue
import time
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))

# 웹앱 모드: tkinter GUI 스킵 — quant_nexus_v20 import 0.3~1초 단축
os.environ.setdefault("QN_HEADLESS", "1")

# 프로젝트 루트 경로 (four_axis_analyzer, handdrawn_renderer 접근용)
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from flask import Flask, request, jsonify, render_template, Response
from chat import socketio
from config_manager import apply_to_environ

from logging.handlers import RotatingFileHandler

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_app_fmt = logging.Formatter("%(levelname)s %(message)s")

# RotatingFileHandler (UTF-8) — 중복 방지
_app_log_path = 'quant_nexus_v20.log'
_app_fh_exists = any(
    isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '').endswith('quant_nexus_v20.log')
    for h in _root_logger.handlers
)
if not _app_fh_exists:
    _app_fh = RotatingFileHandler(_app_log_path, maxBytes=5_000_000, backupCount=3, encoding='utf-8', errors='replace')
    _app_fh.setLevel(logging.INFO)
    _app_fh.setFormatter(_app_fmt)
    _root_logger.addHandler(_app_fh)

# StreamHandler (콘솔) — 중복 방지
_app_sh_exists = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in _root_logger.handlers)
if not _app_sh_exists:
    _app_sh = logging.StreamHandler(sys.stderr)
    _app_sh.setLevel(logging.WARNING)
    _app_sh.setFormatter(_app_fmt)
    _root_logger.addHandler(_app_sh)

# 앱 시작 시 저장된 설정을 환경변수에 반영
apply_to_environ()

# (섹터 히트/핫 섹터 기능 제거됨 — yfinance rate-limit 부담 ↓, UI 정리)

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)


# ── 정적 자산 캐시 버스팅 ──
# 브라우저가 'Cache-Control: no-cache'를 응답에는 적용해도
# 디스크 캐시에서 미리 끌어다 쓰는 경우가 있어 mtime 기반 ?v= 쿼리를 강제.
_ASSET_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _asset_mtime(name: str) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(_ASSET_STATIC_DIR, name))))
    except OSError:
        return "0"


@app.context_processor
def _inject_asset_versions():
    return {
        "v_app_js": _asset_mtime("app.js"),
        "v_theme_css": _asset_mtime("theme.css"),
        "v_scanner_css": _asset_mtime("scanner.css"),
    }


# ── JSON Provider: NaN/Infinity → null 강제 변환 ──
# Python json.dumps 는 기본 allow_nan=True 라 'NaN','Infinity' 토큰을 그대로 출력하지만
# JSON 표준이 아니므로 브라우저 JSON.parse() 는 SyntaxError 를 던진다.
# → fetch().json() 실패 → app.js runScan catch 가 "HTTP 200" 로 잘못 표기하며 무한 재시도.
# 모든 응답에서 NaN/±Inf 를 null 로 치환해 silent regression 차단.
import math as _math
from flask.json.provider import DefaultJSONProvider as _DefaultJSONProvider


def _sanitize_nan(obj):
    # bool은 int 서브클래스라 먼저 체크 — str/int/bool/None은 대부분의 값이므로 즉시 반환
    if isinstance(obj, (bool, int, str)) or obj is None:
        return obj
    if isinstance(obj, float):
        return None if not _math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    return obj


class _SafeJSONProvider(_DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        kwargs["allow_nan"] = False
        return super().dumps(_sanitize_nan(obj), **kwargs)


app.json = _SafeJSONProvider(app)


# Gzip 압축 — JSON API 응답 크기 60~70% 절감
# (스캔 응답이 10MB+ 가 될 수 있어 모바일/원거리 클라이언트에서 비압축 시 타임아웃 발생)
# 알고리즘 br/gzip 만 허용 — zstd 는 Chrome 123+ 가 Accept-Encoding 에 광고하지만
# 일부 환경(Safari/구버전/프록시/확장)에서 디코딩 실패 → fetch network error → 무한 재시도.
_FLASK_COMPRESS_OK = False
try:
    from flask_compress import Compress as _Compress
    app.config.setdefault("COMPRESS_ALGORITHM", ["gzip"])  # br은 CPU 비용이 너무 높음
    app.config.setdefault("COMPRESS_LEVEL", 1)             # 최속 압축 (속도 우선)
    _Compress(app)
    _FLASK_COMPRESS_OK = True
except ImportError:
    logging.warning(
        "flask_compress NOT installed — JSON responses are not gzip compressed. "
        "11MB+ scan payloads may timeout on mobile/slow networks. "
        "Run: pip install -r requirements.txt"
    )


def _render_deployment() -> bool:
    return bool((os.environ.get("RENDER") or "").strip())



# ── 4축 차트 / 컨센서스 캐시 (성능 최적화) ──
_FOUR_AXIS_TTL_SEC = 1800  # 30분
_FOUR_AXIS_MAX = 200
# OrderedDict LRU — move_to_end로 hot entry 보존, popitem(last=False)로 cold eviction
from collections import OrderedDict as _OrderedDict
_four_axis_cache: "_OrderedDict[str, dict]" = _OrderedDict()
_four_axis_cache_lock = threading.Lock()

_CONSENSUS_TTL_SEC = 900  # 15분
_CONSENSUS_MAX = 200
_consensus_cache: dict[str, dict] = {}
_consensus_cache_lock = threading.Lock()
_BROKER_TARGET_TTL_SEC = 6 * 3600
_broker_target_cache: dict[str, dict] = {}
_broker_target_cache_lock = threading.Lock()

# ── 티커 상세 응답 캐시 (드로어 재오픈 시 즉시 응답) ──
_TICKER_DETAIL_TTL_SEC = 1800  # 30분
_TICKER_DETAIL_MAX = 200
_ticker_detail_cache: dict[str, dict] = {}
_ticker_detail_cache_lock = threading.Lock()


def _has_bf_breakdown(row: dict | None) -> bool:
    bd = row.get("Breakdown") if isinstance(row, dict) else None
    if not isinstance(bd, list):
        return False
    return any(
        isinstance(item, (list, tuple))
        and item
        and str(item[0]).startswith("[BF]")
        for item in bd
    )


_KR_DETAIL_DATA_SCHEMA = "kr_toss_price_naver_ttm_v1"


def _has_current_kr_detail_schema(row: dict | None) -> bool:
    return isinstance(row, dict) and row.get("_DataSchema") == _KR_DETAIL_DATA_SCHEMA


_scan_refresh_lock = threading.Lock()
_scan_refresh_inflight: set[tuple[str, str, str]] = set()
_SUPPORTED_MARKETS = frozenset({"KR"})

# ── 스캔 결과 전체 캐시 (API 레벨, pickle 재읽기 방지) ──
_SCAN_RESULTS_TTL_SEC = 300  # 5분
_scan_results_cache: dict[tuple[str, str, str], dict] = {}
_scan_results_cache_lock = threading.Lock()
_SCAN_SNAPSHOT_PATH = os.path.join(_BASE, "cache_v19", "_scan_snapshot.pkl")

# 스캔 JSON 응답에서 제거할 무거운 필드 (상세 패널은 /api/ticker 에서 별도 제공)
# Breakdown: 점수 분해 배열 (detail에서 /api/ticker로 재취득)
# Scores: 멀티 전략 점수 dict (Tkinter 전용, web frontend 미사용)
# Reason: 한줄 사유 문자열 (web frontend 미사용)
# About: 기업 설명 (~300자/종목, 상세 패널에서만 사용)
_SCAN_STRIP_FIELDS: frozenset = frozenset({"Breakdown", "Scores", "Reason", "About"})
# EntryPlan 서브필드 중 리스트 뷰에서 사용되는 것만 보존 (나머지는 /api/ticker에서 제공)
_ENTRY_PLAN_KEEP: frozenset = frozenset({
    "entry", "entry_discount", "atr_pct", "as_of_ts", "headline_action",
    "current", "stop", "t1", "t2", "rr", "rr_now", "vol_regime", "drawdown_pct",
    "mdd_current", "mdd_risk", "mdd_recovery", "size_suggestion", "cvar_95", "worst_day",
    "dd_velocity_5d", "dd_velocity_20d", "underwater_days", "calmar_ratio",
    "skewness", "excess_kurtosis", "downside_beta",
    "stress_2008", "stress_2020", "stress_2022",
    "composite_risk", "ac1", "halflife", "amihud", "liquidity_score",
    "factor_contrib",
})
# MoatData 서브필드 중 리스트 뷰 미사용 (scores=111B/종목, 상세 패널에서만 사용)
_MOAT_DATA_STRIP: frozenset = frozenset({"scores", "evidence_source", "story_risk"})

def _apply_moat_bonus(rows: list) -> None:
    """MoatBonus를 TotalScore에 반영한다. 모든 캐시 저장 경로에서 호출."""
    for r in rows:
        if not isinstance(r, dict) or r.get("_MoatBonusApplied"):
            continue
        bonus = r.get("MoatBonus", 0)
        if bonus and isinstance(r.get("TotalScore"), (int, float)):
            r["TotalScore"] = min(100.0, r["TotalScore"] + bonus)
            r["_MoatBonusApplied"] = True


def _strip_heavy(rows: list) -> list:
    if not rows:
        return rows
    out = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        d = {k: v for k, v in r.items() if k not in _SCAN_STRIP_FIELDS}
        ep = d.get("EntryPlan")
        if isinstance(ep, dict):
            d["EntryPlan"] = {k: v for k, v in ep.items() if k in _ENTRY_PLAN_KEEP}
        md = d.get("MoatData")
        if isinstance(md, dict):
            d["MoatData"] = {k: v for k, v in md.items() if k not in _MOAT_DATA_STRIP}
        out.append(d)
    return out

# ── 스캔 응답 사전 압축 캐시 — cache hit 시 재직렬화/재압축 제거 ──
# jsonify+flask_compress(brotli/gzip) 는 5.5MB 응답에 2~4s 소요 → 1회만 실행, 이후 bytes 직접 서빙
_scan_gz_cache: dict[tuple, bytes] = {}
_scan_gz_cache_lock = threading.Lock()

def _store_scan_cache(key: tuple, ts: int, rows: list) -> bytes:
    """rows를 _scan_results_cache + gzip 사전압축 캐시에 동시 저장. 압축 bytes 반환."""
    import gzip as _gz
    with _scan_results_cache_lock:
        _scan_results_cache[key] = {"_ts": ts, "data": rows}
    try:
        raw = json.dumps(_sanitize_nan(rows), ensure_ascii=False, allow_nan=False).encode("utf-8")
        compressed = _gz.compress(raw, compresslevel=1)
    except Exception:
        compressed = b""
    with _scan_gz_cache_lock:
        _scan_gz_cache[key] = compressed
    return compressed


def _mark_json_no_store(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _scan_universe(adapter) -> set[str]:
    try:
        return {t for ts in adapter.get_sectors().values() for t in ts}
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
        return set()


def _attach_scan_deltas(
    results: list,
    market: str,
    *,
    adapter=None,
    sector: str = "",
    save_snapshot: bool = False,
    async_snapshot: bool = False,
) -> list:
    """ScoreDelta/RankDelta를 캐시 저장 직전 기준으로 일관되게 부착한다."""
    if not results:
        return results
    try:
        import history
        results = history.annotate_deltas(results, market)
        if save_snapshot and not sector:
            universe = _scan_universe(adapter) if adapter is not None else None

            def _save(_r=list(results), _m=market, _u=universe):
                try:
                    import history as _h
                    _h.save_snapshot(_r, _m, universe=_u)
                except Exception as _e:
                    logging.warning("history snapshot save failed: %s", _e)

            if async_snapshot:
                threading.Thread(target=_save, daemon=True).start()
            else:
                _save()
    except Exception as he:
        logging.warning("history annotate/save failed: %s", he)
    return results


def _scan_rows_have_deltas(rows: list) -> bool:
    return any(isinstance(r, dict) and "ScoreDelta" in r for r in (rows or []))


_DETAIL_SCAN_SCORE_FIELDS: frozenset = frozenset({
    "TotalScore",
    "ScoreDelta",
    "ScoreDeltaState",
    "RankDelta",
    "RankDeltaState",
    "Rank",
    "DeltaDays",
    "IsNew",
    "SectorResidual",
    "RegimeEntryScore",
    "RegimeRank",
    "RegimeLabel",
    "BottleneckScore",
    "BottleneckGrade",
    "BottleneckDelta",
    "BottleneckLabel",
})


def _sync_detail_score_from_scan_cache(row: dict, market: str, strategy: str) -> bool:
    """상세 총점/변동 배지를 현재 리스트 스캔 캐시와 맞춘다."""
    if not isinstance(row, dict):
        return False
    ticker = str(row.get("Ticker") or "")
    if not ticker:
        return False
    market_key = (market or "KR").upper()
    strategy_key = (strategy or "BALANCED").upper()
    scan_row = None
    with _scan_results_cache_lock:
        for key in ((market_key, strategy_key, ""), (market_key, "BALANCED", "")):
            cached = _scan_results_cache.get(key)
            rows = cached.get("data") if cached else None
            if not rows:
                continue
            scan_row = next(
                (
                    r for r in rows
                    if isinstance(r, dict) and str(r.get("Ticker") or "") == ticker
                ),
                None,
            )
            if scan_row:
                break
    if not scan_row:
        return False
    changed = False
    for field in _DETAIL_SCAN_SCORE_FIELDS:
        if field in scan_row and row.get(field) != scan_row.get(field):
            row[field] = scan_row.get(field)
            changed = True
    if changed:
        row["_ScoreSyncedFromScan"] = True
    return changed


_scan_snapshot_lock = threading.Lock()


def _save_scan_snapshot():
    """in-memory 스캔 캐시를 단일 파일로 저장 — 서버 재시작 시 즉시 복원용."""
    if not _scan_snapshot_lock.acquire(blocking=False):
        return  # 다른 스레드가 이미 저장 중 → 스킵
    try:
        with _scan_results_cache_lock:
            snapshot = dict(_scan_results_cache)
        if not snapshot:
            return
        import pickle
        tmp = _SCAN_SNAPSHOT_PATH + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(snapshot, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _SCAN_SNAPSHOT_PATH)
        logging.info("scan snapshot saved: %d entries", len(snapshot))
    except Exception as e:
        logging.warning("scan snapshot save failed: %s", e)
    finally:
        _scan_snapshot_lock.release()


def _load_scan_snapshot() -> bool:
    """단일 스냅샷 파일에서 in-memory 캐시 즉시 복원. 성공 시 True."""
    try:
        if not os.path.exists(_SCAN_SNAPSHOT_PATH):
            return False
        import pickle
        with open(_SCAN_SNAPSHOT_PATH, "rb") as f:
            snapshot = pickle.load(f)
        if not snapshot:
            return False
        try:
            from quant_nexus_v20 import QuantNexusApp
            _us_nm_snap = getattr(QuantNexusApp, "US_NAMES", {})
        except Exception:
            _us_nm_snap = {}
        if _us_nm_snap:
            for _sk, _sv in snapshot.items():
                if not isinstance(_sk, tuple) or len(_sk) < 1 or _sk[0] != "US":
                    continue
                for _row in (_sv.get("data") or []):
                    _tk = _row.get("Ticker") or ""
                    _fixed = _us_nm_snap.get(_tk)
                    if _fixed and _row.get("Name") != _fixed:
                        _row["Name"] = _fixed
        with _scan_results_cache_lock:
            _scan_results_cache.update(snapshot)
        logging.info("scan snapshot loaded: %d entries (instant cold-start)", len(snapshot))
        return True
    except Exception as e:
        logging.warning("scan snapshot load failed: %s", e)
        return False


# ── 검색 인덱스 (앱 시작 시 1회 빌드 — 매 요청 선형 스캔 제거) ──
# 각 항목: (ticker, display_name, blob) — blob = ticker|name 소문자 결합 문자열
_SEARCH_IDX: dict[str, list] = {}
_SEARCH_IDX_LOCK = threading.Lock()


def _get_search_idx(market: str) -> list:
    market = (market or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return []
    if market in _SEARCH_IDX:
        return _SEARCH_IDX[market]
    with _SEARCH_IDX_LOCK:
        if market in _SEARCH_IDX:
            return _SEARCH_IDX[market]
        try:
            from quant_nexus_v20 import QuantNexusApp
            if market == "KR":
                idx: list = []
                for tk, nm in QuantNexusApp.KR_NAMES.items():
                    code = tk.split(".")[0]
                    idx.append((tk, nm, f"{tk.lower()}|{nm.lower()}|{code}"))
                _SEARCH_IDX["KR"] = idx
            else:
                us_names = getattr(QuantNexusApp, "US_NAMES", {}) or {}
                us_desc  = getattr(QuantNexusApp, "US_DESC", {}) or {}
                try:
                    from us_company_info import US_COMPANY_INFO as _uci
                except Exception:
                    _uci = {}
                seen: set[str] = set()
                idx = []
                for tk, nm in us_names.items():
                    label = nm or us_desc.get(tk) or _uci.get(tk) or tk
                    idx.append((tk, label, f"{tk.lower()}|{(label or '').lower()}"))
                    seen.add(tk)
                for tk, desc in _uci.items():
                    if tk not in seen:
                        idx.append((tk, desc, f"{tk.lower()}|{(desc or '').lower()}"))
                _SEARCH_IDX["US"] = idx
        except Exception as e:
            logging.warning("_get_search_idx failed: %s", e)
            _SEARCH_IDX[market] = []
        return _SEARCH_IDX.get(market, [])


def _configure_yf_cache() -> None:
    try:
        import yfinance as yf
        cache_dir = os.path.join(_BASE, ".yfinance-cache")
        os.makedirs(cache_dir, exist_ok=True)
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location(cache_dir)
    except Exception as e:
        logging.warning("yfinance cache init failed: %s", e)


def _resolve_kr_suffix(code6: str) -> str | None:
    """KR_NAMES 사전을 이용해 6자리 코드 → 정확한 접미사(.KS/.KQ) 결정.
    lookup miss 면 None — 폴백을 시도해야 할 종목."""
    try:
        from quant_nexus_v20 import QuantNexusApp
        names = getattr(QuantNexusApp, "KR_NAMES", {}) or {}
    except Exception:
        return None
    ks = f"{code6}.KS"
    kq = f"{code6}.KQ"
    if ks in names:
        return ".KS"
    if kq in names:
        return ".KQ"
    return None


def _build_yf_candidates(ticker: str, market: str) -> list[str]:
    raw = (ticker or "").strip()
    market = (market or "KR").upper()

    if market == "US":
        tu = raw.upper()
        candidates = [raw, tu]
        if "." in tu:
            candidates += [tu.replace(".", "-"), raw.replace(".", "-")]
        if "-" in tu:
            candidates += [tu.replace("-", "."), raw.replace("-", ".")]
    else:
        base = raw
        kept_suf = None
        for suf in (".KS", ".KQ", ".ks", ".kq"):
            if base.endswith(suf):
                kept_suf = suf.upper()
                base = base[:-len(suf)]
                break
        t6 = base.zfill(6) if base.isdigit() else base
        # KR_NAMES lookup 으로 접미사를 결정 — 호출자가 .KS 를 줬어도
        # 사전이 .KQ 라고 알면 .KQ 로 정정 (반대도 동일).
        resolved = _resolve_kr_suffix(t6) if t6.isdigit() and len(t6) == 6 else None
        if resolved:
            other = ".KQ" if resolved == ".KS" else ".KS"
            # 결정적 경로 — 폴백은 lookup miss 가 아닌 한 시도하지 않음.
            # 그래도 fetch 자체가 실패할 수 있어 최소 1개 폴백 유지.
            candidates = [f"{t6}{resolved}", f"{t6}{other}"]
        elif kept_suf in (".KS", ".KQ"):
            other = ".KQ" if kept_suf == ".KS" else ".KS"
            candidates = [f"{t6}{kept_suf}", f"{t6}{other}"]
        else:
            candidates = [f"{t6}.KS", f"{t6}.KQ"]

    seen = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _get_scan_adapter_cls():
    from engine_adapter import ScanAdapter
    return ScanAdapter


def _annotate_one_liners(results: list, force: bool = False):
    """results에 OneLiner/OneLinerTag/OneLinerData를 채운다.
    force=False면 이미 채워진 dict는 스킵해 BG/sync 중복 계산을 피한다."""
    from one_liner import annotate
    if not results:
        return results
    # Moat를 먼저 주입해야 one_liner._raw_bucket이 MoatCategory를 읽고
    # 해자 종목을 STORY_STOCK으로 잘못 라우팅하는 것을 차단할 수 있다.
    try:
        _annotate_moats(results, force=force)
    except Exception as e:
        logging.warning("moat annotate failed: %s", e)
    if force:
        annotate(results)
    else:
        pending = [r for r in results if isinstance(r, dict) and not r.get("OneLiner")]
        if pending:
            annotate(pending)
    return results


def _annotate_moats(results: list, force: bool = False):
    """results에 Moat/MoatCategory/MoatData를 채운다.
    캐시(cache_v19/moat/{ticker}.json, TTL 30일)로 중복 호출 차단."""
    from moat import annotate as _moat_annotate
    if not results:
        return results
    if force:
        # 강제 갱신: 기존 Moat 필드 제거 후 재생성
        for r in results:
            if isinstance(r, dict):
                r.pop("Moat", None)
                r.pop("MoatCategory", None)
                r.pop("MoatData", None)
                r.pop("_MoatBonusApplied", None)
    _moat_annotate(results)
    return results


def _kr_quote_symbol(ticker: str) -> str | None:
    raw = str(ticker or "").strip().upper()
    raw = raw.replace(".KS", "").replace(".KQ", "")
    if raw.isdigit():
        return raw.zfill(6)
    return None


def _quote_debug_enabled() -> bool:
    try:
        return request.args.get("quote_debug") == "1"
    except RuntimeError:
        return False


def _n01_score(raw: float, best: float = 35.0) -> float:
    try:
        return max(0.0, min(100.0, float(raw) / best * 100.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _cs_n_weight() -> tuple[float, str]:
    try:
        strategy = (request.args.get("strategy") or "BALANCED").upper()
    except RuntimeError:
        strategy = "BALANCED"
    try:
        from quant_nexus_v20 import STRATEGY_WEIGHTS
        weights = STRATEGY_WEIGHTS.get(strategy) or STRATEGY_WEIGHTS.get("BALANCED") or {}
        return float(weights.get("cs_n", 0.05)), strategy
    except Exception:
        return 0.05, strategy


def _as_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _find_n_breakdown(row: dict) -> tuple[int | None, list | None]:
    bd = row.get("Breakdown")
    if not isinstance(bd, list):
        return None, None
    for idx, item in enumerate(bd):
        if isinstance(item, (list, tuple)) and item and str(item[0]).startswith("[N]"):
            return idx, list(item)
    return None, None


def _strip_breakout_tags(signal: str) -> str:
    out = str(signal or "")
    for tag in (" 🔔[BREAKOUT]", "🔔[BREAKOUT]", " [PIVOT]", "[PIVOT]"):
        out = out.replace(tag, "")
    return " ".join(out.split())


def _sync_kr_realtime_new_high(row: dict) -> None:
    """상세 응답의 [N] 신고가·피벗 항목을 Toss 현재가/일봉 기준으로 보정한다."""
    if not isinstance(row, dict) or row.get("_QuoteSource") != "tossinvest":
        return
    ticker = str(row.get("Ticker") or "")
    current = _as_float(row.get("Price"))
    if not ticker or current is None or current <= 0:
        return
    try:
        import toss_invest
        candles = toss_invest.get_daily_candles(ticker, count=260)
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
        return
    if len(candles) < 25:
        return

    closes = [_as_float(c.get("close")) for c in candles]
    highs = [_as_float(c.get("high")) for c in candles]
    closes = [c for c in closes if c is not None and c > 0]
    highs = [h for h in highs if h is not None and h > 0]
    if len(closes) < 25 or not highs:
        return
    closes[-1] = current
    high_52w = max(max(highs), current)
    lows = [l for l in [_as_float(c.get("low")) for c in candles] if l is not None and l > 0]
    low_52w = min(lows) if lows else current
    if high_52w <= 0:
        return

    dist = max(0.0, (high_52w - current) / high_52w)
    near_high = dist <= 0.05
    n_raw = 20.0 if near_high else 10.0 if dist < 0.10 else 0.0

    prev_slice = closes[-25:-5]
    recent_slice = closes[-5:]
    pivot_breakout = False
    if prev_slice and recent_slice:
        prev_high = max(prev_slice)
        recent_high = max(recent_slice)
        pivot_breakout = bool(prev_high > 0 and recent_high > prev_high * 1.01 and current > prev_high * 1.005)
    if pivot_breakout:
        n_raw += 15.0

    idx, item = _find_n_breakdown(row)
    old_raw = _as_float(item[1], 0.0) if item and len(item) > 1 else None
    new_norm = _n01_score(n_raw)
    old_norm = _n01_score(old_raw or 0.0)
    weight, strategy = _cs_n_weight()

    if old_raw is not None and row.get("TotalScore") is not None:
        super_mult = _as_float(row.get("SuperMult"), 1.0) or 1.0
        delta = (new_norm - old_norm) * weight * super_mult
        total = _as_float(row.get("TotalScore"))
        if total is not None:
            row["TotalScore"] = round(max(0.0, min(100.0, total + delta)), 1)

    row["NearHighPass"] = near_high
    row["_RealtimeNRaw"] = round(n_raw, 1)
    row["_RealtimeNDist52w"] = round(dist, 4)
    row["_RealtimePivotBreakout"] = pivot_breakout
    row["_RealtimeHigh52w"] = round(high_52w, 2)
    row["_YearHigh"] = round(high_52w, 2)
    row["_YearLow"] = round(low_52w, 2)
    last_candle = candles[-1] if candles else {}
    last_vol = _as_float(last_candle.get("volume"))
    if last_vol is not None:
        row["Volume"] = last_vol
    last_high = _as_float(last_candle.get("high"))
    last_low = _as_float(last_candle.get("low"))
    if last_high is not None:
        row["_DayHigh"] = max(last_high, current)
    if last_low is not None:
        row["_DayLow"] = min(last_low, current)

    signal = _strip_breakout_tags(str(row.get("Signal") or ""))
    if near_high and bool(row.get("SConfirmed")):
        signal += " 🔔[BREAKOUT]"
    elif pivot_breakout:
        signal += " [PIVOT]"
    row["Signal"] = signal

    entry_plan = row.get("EntryPlan")
    if isinstance(entry_plan, dict):
        entry_plan["current"] = round(current, 2)
        entry_plan["drawdown_pct"] = round(-dist * 100.0, 1)

    if idx is not None and item is not None:
        title = item[0]
        summary = (
            f"52주 최고가에서 {dist:.0%} 아래에 있어요. "
            f"{'신고가 권역에 진입했어요.' if near_high else '아직 신고가까지 거리가 있어요.'}"
            f"{' 컵앤핸들 피벗 돌파가 감지됐어요.' if pivot_breakout else ''}"
            f"\n📐 점수 {new_norm:.1f}/100 × 가중치 {weight * 100:.1f}% = 기여도 {new_norm * weight:.1f}점"
        )
        detail = (
            "📊 입력 데이터\n"
            f"• 52주 최고가: {high_52w:,.0f}\n"
            f"• 52주 최고가 거리: {dist:.0%}\n"
            f"• 신고가 근접: {'예 ✓' if near_high else '아니오'}\n"
            f"• 피벗 돌파: {'예 ✓' if pivot_breakout else '아니오'}\n"
            "📐 계산 과정\n"
            f"① 원점수: {n_raw:.1f}\n"
            f"② 정규화: _n01({n_raw:.1f}, best=35) → {new_norm:.1f}/100\n"
            f"③ 가중치: {weight * 100:.1f}% ({strategy})\n"
            f"④ 기여도: {new_norm:.1f} × {weight * 100:.1f}% = {new_norm * weight:.1f}점"
        )
        row["Breakdown"][idx] = [title, round(n_raw, 1), summary, detail]


def _apply_kr_toss_stock_names(results: list) -> bool:
    """KR 결과의 표시 종목명을 Toss 종목 기본정보 기준으로 보정한다."""
    if not results:
        return False
    kr_items = [
        r for r in results
        if isinstance(r, dict) and isinstance(r.get("Ticker"), str)
        and _kr_quote_symbol(r["Ticker"])
    ]
    if not kr_items:
        return False
    try:
        import toss_invest
        stocks = toss_invest.get_stocks([r["Ticker"] for r in kr_items])
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
        return False
    if not stocks:
        return False

    changed = False
    for row in kr_items:
        symbol = _kr_quote_symbol(row.get("Ticker"))
        info = stocks.get(symbol) if symbol else None
        if not isinstance(info, dict):
            continue
        name = str(info.get("name") or "").strip()
        if not name or name == symbol:
            continue
        if row.get("Name") != name[:20]:
            row["Name"] = name[:20]
            changed = True
        market = str(info.get("market") or "").strip()
        if market:
            row["_TossMarket"] = market
        shares = _as_float(info.get("shares_outstanding"))
        price = _as_float(row.get("Price"))
        if shares and shares > 0:
            row["_SharesOutstanding"] = shares
            if price and price > 0 and not row.get("_MarketCap"):
                row["_MarketCap"] = shares * price
    return changed


def _parse_int_value(value) -> int:
    try:
        return int(str(value or "").replace(",", "").strip())
    except Exception:
        return 0


def _broker_target_scan_limit() -> int | None:
    raw = os.environ.get("BROKER_TARGET_SCAN_LIMIT", "1000")
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 1000
    return None if limit <= 0 else max(1, limit)


def _fetch_kr_consensus_target(ticker: str) -> dict:
    symbol = _kr_quote_symbol(ticker)
    if not symbol:
        return {}
    now = int(time.time())
    with _broker_target_cache_lock:
        cached = _broker_target_cache.get(symbol)
        if cached and now - cached.get("_ts", 0) < _BROKER_TARGET_TTL_SEC:
            return dict(cached.get("data") or {})

    out: dict = {}
    try:
        url = f"https://m.stock.naver.com/api/stock/{symbol}/integration"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ci = data.get("consensusInfo") or {}
        target = _parse_int_value(ci.get("priceTargetMean"))
        if target > 0:
            created = str(ci.get("createDate") or "").strip()
            source = "네이버증권 컨센서스 평균"
            if created:
                source += f" ({created})"
            out = {
                "target": float(target),
                "source": source,
                "count": _parse_int_value(
                    ci.get("targetPriceCount") or ci.get("consensusCount") or ci.get("stockFirmCount")
                ),
            }
    except Exception as _e:
        logging.debug("KR consensus target fetch failed for %s: %s", symbol, _e)

    with _broker_target_cache_lock:
        _broker_target_cache[symbol] = {"_ts": now, "data": dict(out)}
        if len(_broker_target_cache) > 1000:
            _broker_target_cache.pop(next(iter(_broker_target_cache)), None)
    return out


def _apply_kr_broker_target_fallback(results: list, *, limit=120) -> bool:
    """BrokerTarget이 비어 있으면 상세 컨센서스 평균값으로 채운다."""
    if not results:
        return False
    candidates = []
    for row in results:
        if not isinstance(row, dict) or not _kr_quote_symbol(str(row.get("Ticker") or "")):
            continue
        current = _as_float(row.get("BrokerTarget"), 0.0) or 0.0
        if current <= 0:
            candidates.append(row)
    if limit is not None:
        candidates = candidates[: max(0, int(limit))]
    if not candidates:
        return False

    changed = False

    def _fetch(row):
        return row, _fetch_kr_consensus_target(str(row.get("Ticker") or ""))

    try:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(8, max(1, len(candidates)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fetched = list(ex.map(_fetch, candidates))
    except Exception:
        fetched = [_fetch(row) for row in candidates]

    for row, data in fetched:
        target = _as_float((data or {}).get("target"), 0.0) or 0.0
        if target <= 0:
            continue
        row["BrokerTarget"] = target
        row["BrokerTargetSource"] = data.get("source") or "네이버증권 컨센서스 평균"
        count = _parse_int_value((data or {}).get("count"))
        if count:
            row["BrokerAnalystCount"] = count
        changed = True
    return changed


_kr_toss_basis_warm_lock = threading.Lock()
_kr_toss_basis_warm_inflight = False


def _schedule_kr_toss_basis_warm(results: list) -> None:
    global _kr_toss_basis_warm_inflight
    tickers = [
        row.get("Ticker") for row in results
        if isinstance(row, dict) and _kr_quote_symbol(row.get("Ticker"))
    ]
    if not tickers:
        return
    with _kr_toss_basis_warm_lock:
        if _kr_toss_basis_warm_inflight:
            return
        _kr_toss_basis_warm_inflight = True

    def _worker() -> None:
        global _kr_toss_basis_warm_inflight
        try:
            import toss_invest
            cached = toss_invest.get_cached_previous_closes(tickers)
            missing = [
                ticker for ticker in tickers
                if _kr_quote_symbol(ticker) not in cached
            ]
            if missing:
                toss_invest.get_previous_closes(missing, max_workers=8)
            with _scan_results_cache_lock:
                cached_items = [
                    (key, value)
                    for key, value in _scan_results_cache.items()
                    if key[0] == "KR" and key[2] == "" and value.get("data")
                ]
            for key, value in cached_items:
                rows = value.get("data") or []
                _override_kr_day_chg(rows, warm_toss_basis=False)
                ts = int(time.time())
                _store_scan_cache(key, ts, rows)
                _populate_sector_caches(key[0], key[1], rows, ts)
        except Exception as exc:
            logging.warning("Toss previous-close warm failed: %s", exc)
        finally:
            with _kr_toss_basis_warm_lock:
                _kr_toss_basis_warm_inflight = False

    threading.Thread(
        target=_worker,
        daemon=True,
        name="toss-previous-close-warm",
    ).start()


def _override_kr_day_chg(results: list, *, warm_toss_basis: bool = True) -> list:
    """KR 종목 Price/DayChg를 실시간 시세로 보정한다.

    yfinance KR 일봉이 장중에 전일 종가 기준으로 고착되는 문제 회피.
    Toss가 설정되어 있으면 현재가는 배치로 가져오고, 네이버 change_pct는
    퍼센트 단위 → DayChg 저장은 fraction이므로 /100.
    """
    if not results:
        return results
    kr_items = [
        r for r in results
        if isinstance(r, dict) and isinstance(r.get("Ticker"), str)
        and _kr_quote_symbol(r["Ticker"])
    ]
    if not kr_items:
        return results
    toss_quotes = {}
    toss_previous_closes = {}
    toss_configured = None
    toss_error = ""
    try:
        import toss_invest
        toss_configured = toss_invest.is_available()
        toss_quotes = toss_invest.get_prices([r["Ticker"] for r in kr_items])
        toss_previous_closes = toss_invest.get_cached_previous_closes(
            [r["Ticker"] for r in kr_items]
        )
        toss_error = toss_invest.get_last_error()
    except Exception as _e:
        try:
            toss_error = toss_invest.get_last_error() or str(_e.__class__.__name__)
        except Exception:
            toss_error = str(_e.__class__.__name__)
        logging.debug("silent except (app.py): %s", _e)

    def _fetch(r):
        symbol = _kr_quote_symbol(r.get("Ticker"))
        toss_quote = toss_quotes.get(symbol) if symbol else None
        toss_previous_close = toss_previous_closes.get(symbol) if symbol else None
        return _overlay_kr_realtime_quote(
            r,
            toss_quote=toss_quote,
            toss_previous_close=toss_previous_close,
            fetch_toss=False,
            toss_configured=toss_configured,
            toss_error=toss_error,
        )

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_fetch, kr_items))
    _apply_kr_toss_stock_names(results)
    _apply_kr_broker_target_fallback(results, limit=_broker_target_scan_limit())
    if warm_toss_basis and len(toss_previous_closes) < len(kr_items):
        _schedule_kr_toss_basis_warm(results)
    return results


def _overlay_kr_realtime_quote(
    row: dict,
    toss_quote: dict | None = None,
    toss_previous_close: float | None = None,
    fetch_toss: bool = True,
    toss_configured: bool | None = None,
    toss_error: str = "",
    sync_new_high: bool = False,
) -> dict:
    """단일 KR 결과의 Price/DayChg를 실시간 시세로 보정한다.

    Toss Open API가 설정되어 있으면 현재가를 먼저 사용한다. Toss가 제공하지
    않는 등락률/시총은 기존 Naver 보정값으로 채운다.
    """
    if not isinstance(row, dict):
        return row
    ticker = str(row.get("Ticker") or "")
    if not _kr_quote_symbol(ticker):
        return row
    toss_q = toss_quote
    if fetch_toss and toss_q is None:
        try:
            import toss_invest
            toss_configured = toss_invest.is_available()
            toss_q = toss_invest.get_quote(ticker)
            toss_previous_close = toss_invest.get_previous_close(ticker)
            toss_error = toss_invest.get_last_error()
        except Exception as _e:
            try:
                toss_error = toss_invest.get_last_error() or str(_e.__class__.__name__)
            except Exception:
                toss_error = str(_e.__class__.__name__)
            logging.debug("silent except (app.py): %s", _e)
    naver_q = {}
    try:
        from naver_finance import get_quote
        naver_q = get_quote(ticker) or {}
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
    try:
        q = naver_q
        naver_price = _as_float(q.get("price"))
        naver_change = _as_float(q.get("change"))
        naver_change_pct = _as_float(q.get("change_pct"))
        previous_close = _as_float(toss_previous_close)
        if naver_price and naver_price > 0:
            if previous_close is None and naver_change is not None:
                candidate = naver_price - naver_change
                if candidate > 0:
                    previous_close = candidate
            if (
                previous_close is None
                and naver_change_pct is not None
                and -95.0 < naver_change_pct < 1000.0
            ):
                candidate = naver_price / (1.0 + naver_change_pct / 100.0)
                if candidate > 0:
                    previous_close = candidate
        price_source = ""
        price_ts = None
        price = (toss_q or {}).get("price") if isinstance(toss_q, dict) else None
        if price is not None and price > 0:
            price_source = str((toss_q or {}).get("source") or "tossinvest")
            price_ts = (toss_q or {}).get("timestamp")
        else:
            price = q.get("price")
            if price is not None and price > 0:
                price_source = str(q.get("source") or "finance.naver.com")
        if price is not None and price > 0:
            row["Price"] = float(price)
            if price_source == "tossinvest" and previous_close:
                live_change = row["Price"] / previous_close - 1.0
                row["DayChg"] = live_change
                row["_DayChgPct"] = live_change * 100.0
            else:
                if naver_change_pct is not None:
                    row["DayChg"] = naver_change_pct / 100.0
                    row["_DayChgPct"] = naver_change_pct
            if price_source:
                row["_QuoteSource"] = price_source
            if price_ts:
                row["_QuoteTimestamp"] = price_ts
            target = row.get("TargetPrice")
            try:
                if target:
                    row["TargetUpside"] = (float(target) - row["Price"]) / row["Price"]
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        market_cap_oku = q.get("market_cap_oku")
        if market_cap_oku is not None and market_cap_oku > 0:
            row["_MarketCap"] = float(market_cap_oku) * 1e8
        if sync_new_high:
            _sync_kr_realtime_new_high(row)
        if _quote_debug_enabled():
            row["_TossConfigured"] = bool(toss_configured)
            row["_TossError"] = str(toss_error)[:240] if toss_error else ""
        else:
            row.pop("_TossConfigured", None)
            row.pop("_TossError", None)
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
    return row


def _render_static_template(name: str, replacements: dict[str, str] | None = None) -> Response:
    path = os.path.join(os.path.dirname(__file__), "templates", name)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    # 정적 자산 캐시버스터 주입 — 파일 mtime 기반. 브라우저 디스크 캐시 우회.
    content = (
        content
        .replace("{{ v_app_js }}", _asset_mtime("app.js"))
        .replace("{{ v_theme_css }}", _asset_mtime("theme.css"))
        .replace("{{ v_scanner_css }}", _asset_mtime("scanner.css"))
    )
    for src, dst in (replacements or {}).items():
        content = content.replace(src, dst)
    return Response(content, mimetype="text/html; charset=utf-8")


_adapter_pool: dict[tuple[str, str], object] = {}
_adapter_pool_lock = threading.Lock()
_ADAPTER_POOL_MAX = 4


def _make_adapter():
    market   = request.args.get("market",   "KR")
    strategy = request.args.get("strategy", "BALANCED")
    if market.upper() not in _SUPPORTED_MARKETS:
        market = "KR"
    key = (market.upper(), strategy.upper())
    with _adapter_pool_lock:
        if key in _adapter_pool:
            return _adapter_pool[key]
    adapter = _get_scan_adapter_cls()(market=market, strategy=strategy)
    with _adapter_pool_lock:
        if len(_adapter_pool) >= _ADAPTER_POOL_MAX:
            _adapter_pool.pop(next(iter(_adapter_pool)), None)
        _adapter_pool[key] = adapter
    return adapter


def _apply_curated_detail_sector(row: dict, market: str = "KR") -> dict:
    """상세 응답의 Sector도 목록과 같은 큐레이션 섹터로 맞춘다."""
    if not isinstance(row, dict) or (market or "").upper() != "KR":
        return row
    try:
        adapter = _make_adapter()
        apply_sector = getattr(adapter, "apply_curated_sector", None)
        if callable(apply_sector):
            apply_sector(row, row.get("Ticker", ""))
    except Exception as _e:
        logging.debug("silent except (app.py): %s", _e)
    return row


def _refresh_scan_background(market: str, strategy: str, sector: str) -> None:
    key = (market, strategy, sector)
    with _scan_refresh_lock:
        if key in _scan_refresh_inflight:
            return
        _scan_refresh_inflight.add(key)

    def _worker() -> None:
        try:
            adapter_cls = _get_scan_adapter_cls()
            adapter = adapter_cls(market=market, strategy=strategy)
            results = adapter.scan_sector(sector, prefer_cache=True, cache_only=True) if sector else adapter.scan_all(prefer_cache=True, cache_only=True, max_workers=20)
            # 네이버 KR 실시간 등락률 오버라이드도 BG에서 처리 — 사용자 응답 지연 회피
            if market == "KR":
                try:
                    results = _override_kr_day_chg(results)
                except Exception as ne:
                    logging.warning("background naver DayChg override failed: %s", ne)
            try:
                results = _annotate_one_liners(results, force=True)
            except Exception as oe:
                logging.warning("background one_liner annotate failed: %s", oe)
            _apply_moat_bonus(results)
            # Phase-3: moat 주입 후 투기주 졸업 재평가
            from speculative_themes import apply_speculative_correction as _spec_reeval_batch
            _spec_reeval_batch(results)
            # GreedZone batch enrichment
            try:
                results = _enrich_greedzone_batch(results)
            except Exception as _e:
                logging.warning("background GreedZone enrichment failed: %s", _e)
            results = _attach_scan_deltas(
                results,
                market,
                adapter=adapter,
                sector=sector,
                save_snapshot=not bool(sector),
            )
            # 스캔 결과 전체 캐시 갱신 (Breakdown 제거 + 사전 압축)
            if results:
                _cached_results = _strip_heavy(results)
                _store_scan_cache((market, strategy, sector), int(time.time()), _cached_results)
        except Exception as e:
            logging.warning("background scan refresh failed: %s", e)
        finally:
            with _scan_refresh_lock:
                _scan_refresh_inflight.discard(key)

    try:
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as te:
        # 스레드 생성 자체 실패 — inflight 키를 풀어줘야 다음 요청이 영구 차단되지 않는다.
        logging.warning("background scan thread start failed: %s", te)
        with _scan_refresh_lock:
            _scan_refresh_inflight.discard(key)


# ── 캐시 메타데이터 (stale-data UX 헤더용) ─────────────────────────────────
_scan_cache_meta_result: dict[str, tuple] = {}
_scan_cache_meta_ts: dict[str, float] = {}
_SCAN_CACHE_META_TTL = 120  # 2분 캐시 — 6,773개 파일 stat을 매 요청 반복하지 않도록


def _scan_cache_meta(market: str) -> tuple[int | None, str | None]:
    """cache_v19/ 디렉토리에서 해당 market 캐시 파일들의 최고령(가장 오래된) 분 + 가장 최신 mtime ISO 반환.
    실패/없음 시 (None, None). 결과는 2분 캐시 (6,773개 파일 stat 매 요청 방지).
    """
    _now = time.time()
    if market in _scan_cache_meta_result and (_now - _scan_cache_meta_ts.get(market, 0)) < _SCAN_CACHE_META_TTL:
        return _scan_cache_meta_result[market]
    try:
        from datetime import datetime, timezone
        cache_dir = os.path.join(_BASE, "cache_v19")
        if not os.path.isdir(cache_dir):
            return (None, None)
        # KR 캐시 파일은 `005930_KS__...pkl` 같은 형태 — `_KS`/`_KQ` 키워드 필터
        if market == "KR":
            patterns = ("_KS__", "_KQ__")
        elif market == "US":
            patterns = ("__",)  # KR suffix 제외
        else:
            patterns = ("__",)
        now = time.time()
        oldest_age_sec = 0.0
        newest_mtime = 0.0
        count = 0
        for entry in os.scandir(cache_dir):
            fn = entry.name
            if not fn.endswith(".pkl"):
                continue
            if market == "KR":
                if not any(p in fn for p in patterns):
                    continue
            elif market == "US":
                if any(p in fn for p in ("_KS__", "_KQ__")):
                    continue
            try:
                mt = entry.stat().st_mtime
            except OSError:
                continue
            age = now - mt
            if age > oldest_age_sec:
                oldest_age_sec = age
            if mt > newest_mtime:
                newest_mtime = mt
            count += 1
        if count == 0:
            return (None, None)
        cache_age_min = int(oldest_age_sec // 60)
        as_of_iso = datetime.fromtimestamp(newest_mtime, tz=timezone.utc).isoformat()
        _scan_cache_meta_result[market] = (cache_age_min, as_of_iso)
        _scan_cache_meta_ts[market] = _now
        return (cache_age_min, as_of_iso)
    except Exception:
        return (None, None)


# ── KR 캐시 워밍 (서버 기동 시 + 30분 주기, multi-process safe) ────────────
_KR_WARMUP_LOCK_PATH = os.path.join(_BASE, "cache_v19", ".warmer.lock")
_kr_warmup_started = False
_kr_warmup_lock = threading.Lock()


def _acquire_warmer_file_lock(lock_path=None):
    """non-blocking 파일 잠금 획득. 성공 시 (file_handle, 'win'|'posix'), 실패 시 None.
    반환된 핸들은 워밍 종료까지 open 상태 유지 필요 (finally에서 release+close)."""
    lock_path = lock_path or _KR_WARMUP_LOCK_PATH
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fh = open(lock_path, "a+b")
    except OSError as e:
        logging.warning("warmer lock open failed: %s", e)
        return None
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return (fh, "win")
            except OSError:
                fh.close()
                return None
        else:
            import fcntl
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return (fh, "posix")
            except OSError:
                fh.close()
                return None
    except Exception as e:
        logging.warning("warmer lock acquire failed: %s", e)
        try:
            fh.close()
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
        return None


def _release_warmer_file_lock(handle) -> None:
    if not handle:
        return
    fh, kind = handle
    try:
        if kind == "win":
            import msvcrt
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            fh.close()
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)


def _populate_sector_caches(market: str, strategy: str, results: list, ts: int) -> None:
    """full scan 결과를 섹터별로 분할해 sector cache도 함께 채운다."""
    from collections import defaultdict
    by_sector: dict[str, list] = defaultdict(list)
    for r in results:
        s = r.get("Sector") or ""
        if s:
            by_sector[s].append(r)
    with _scan_results_cache_lock:
        for sector, rows in by_sector.items():
            _scan_results_cache[(market, strategy, sector)] = {"_ts": ts, "data": rows}
    logging.info("%s sector-cache populated: %d sectors", market, len(by_sector))


def _enrich_greedzone_batch(results: list) -> list:
    """GreedZone 필드가 없는 결과에 batch yf.download()로 GreedZone을 주입한다."""
    from greedzone import calc_greedzone
    import yfinance as yf

    # 기존 캐시에 GreedZone은 있지만 Score가 없는 경우 → days 기반 간이 점수 보조
    for r in results:
        if r.get("GreedZone") and "GreedZoneScore" not in r:
            r["GreedZoneScore"] = max(1, min(99, r.get("GreedZoneDays", 1) * 5 + 10))

    missing = [r for r in results if "GreedZone" not in r]
    if not missing:
        return results

    tickers = [r["Ticker"] for r in missing]
    logging.info("GreedZone batch enrichment: %d tickers", len(tickers))

    # 200개씩 배치로 다운로드 (rate-limit 완화)
    BATCH = 200
    ticker_gz: dict[str, dict] = {}
    for i in range(0, len(tickers), BATCH):
        batch = tickers[i:i + BATCH]
        try:
            data = yf.download(batch, period="1y", group_by="ticker",
                               progress=False, threads=True, auto_adjust=True)
            for t in batch:
                try:
                    hist = data[t].dropna() if len(batch) > 1 else data.dropna()
                    if len(hist) >= 182:
                        gz = calc_greedzone(hist, low_period=112, stdev_period=50)
                        ticker_gz[t] = gz
                except Exception:
                    pass
        except Exception as e:
            logging.warning("GreedZone batch download failed (batch %d): %s", i, e)

    # 결과에 주입
    for r in results:
        t = r.get("Ticker", "")
        if "GreedZone" not in r:
            gz = ticker_gz.get(t)
            if gz:
                r["GreedZone"] = gz["in_zone"]
                r["GreedZoneEntry"] = gz["new_entry"]
                r["GreedZoneDays"] = gz["days_in_zone"]
                r["GreedZoneScore"] = gz.get("greed_score", 0)
            else:
                r["GreedZone"] = False
                r["GreedZoneEntry"] = False
                r["GreedZoneDays"] = 0
                r["GreedZoneScore"] = 0

    logging.info("GreedZone batch done: %d enriched, %d in zone",
                 len(ticker_gz), sum(1 for g in ticker_gz.values() if g.get("in_zone")))
    return results


def _warmup_fill_cache(market: str) -> None:
    """prefer_cache=True로 pickle에서 in-memory cache를 빠르게 채운다 (quick-warm pass)."""
    try:
        # moat 메모리 캐시가 비어 있으면 먼저 채운다 — annotate_one_liners 디스크 I/O 제거
        try:
            import moat as _moat
            if not _moat._mem_cache:
                _moat.preload_cache()
        except Exception:
            pass
        adapter_cls = _get_scan_adapter_cls()
        adapter = adapter_cls(market=market, strategy="BALANCED")
        results = adapter.scan_all(prefer_cache=True, cache_only=True, max_workers=20)
        if results:
            try:
                results = _annotate_one_liners(results)
            except Exception as _e:
                logging.debug("silent except (app.py): %s", _e)
            # 캐시 즉시 저장 — 네이버 오버레이/GreedZone 없이도 첫 API 응답 즉시 가능
            _apply_moat_bonus(results)
            results = _attach_scan_deltas(results, market, adapter=adapter, save_snapshot=True)
            results = _strip_heavy(results)
            ts = int(time.time())
            _store_scan_cache((market, "BALANCED", ""), ts, results)
            _populate_sector_caches(market, "BALANCED", results, ts)
            logging.info("%s quick-warm done: %d tickers (from pickle)", market, len(results))
            _save_scan_snapshot()
            # 네이버 실시간 + GreedZone — BG에서 순차 실행 후 캐시 갱신
            def _bg_enrich(_res=list(results), _mkt=market):
                try:
                    # KR 장중이면 네이버 실시간 시세로 Price/DayChg 교체
                    if _mkt == "KR" and _is_kr_market_open_window():
                        try:
                            _override_kr_day_chg(_res)
                            logging.info("KR quick-warm: naver realtime overlay applied (bg)")
                        except Exception as _e:
                            logging.warning("KR quick-warm naver overlay failed: %s", _e)
                    # GreedZone batch enrichment
                    try:
                        _res = _enrich_greedzone_batch(_res)
                    except Exception as _e:
                        logging.warning("%s GreedZone bg failed: %s", _mkt, _e)
                    if _res:
                        _apply_moat_bonus(_res)
                        _res = _attach_scan_deltas(_res, _mkt, adapter=adapter, save_snapshot=True)
                        _s = _strip_heavy(_res)
                        _t = int(time.time())
                        _store_scan_cache((_mkt, "BALANCED", ""), _t, _s)
                        _populate_sector_caches(_mkt, "BALANCED", _s, _t)
                        logging.info("%s bg enrich done", _mkt)
                except Exception as _e:
                    logging.warning("%s bg enrich failed: %s", _mkt, _e)
            threading.Thread(target=_bg_enrich, daemon=True, name=f"enrich-{market}").start()
            # 상위 20개 4축 차트 선제 생성 — 첫 클릭 즉시 표시 (matplotlib 직렬화로 단일 스레드)
            _top20 = sorted(results, key=lambda r: r.get("TotalScore") or 0, reverse=True)[:20]
            def _bg_4ax_warm(_tickers=_top20, _mkt=market):
                for _r in _tickers:
                    _tk = _r.get("Ticker", "")
                    if _tk:
                        try:
                            _warm_four_axis(_tk, _mkt)
                        except Exception as _e:
                            logging.debug("4ax-warm %s: %s", _tk, _e)
            threading.Thread(target=_bg_4ax_warm, daemon=True, name=f"4ax-warm-{market}").start()
    except Exception as e:
        logging.warning("%s quick-warm failed: %s", market, e)


def _kr_warmup_loop(interval_sec: int = 1800, initial_delay: float = 0.0) -> None:
    """KR 전체 스캔을 주기적으로 BG 실행. 파일잠금으로 multi-process duplication 방지.

    KRX 휴장 시간엔 호출 자체를 건너뛴다(yfinance/KRX 호출 0). 첫 실행은
    캐시 채우기 위해 항상 1회 수행.

    initial_delay: quick-warm 즉시 실행 후, slow-refresh(yfinance API) 전 대기 시간.
    US warmup과 동시 yfinance 429 회피용.
    """
    first_run = True
    while True:
        if not first_run and not _is_kr_market_open_window():
            time.sleep(interval_sec)
            continue
        handle = _acquire_warmer_file_lock()
        if handle is None:
            logging.info("KR warm-up skipped: another worker holds lock")
        else:
            try:
                # 첫 실행 시 quick-warm(pickle만)은 즉시, slow-refresh 전 지연
                if first_run:
                    _warmup_fill_cache("KR")
                    first_run = False
                    if initial_delay > 0:
                        logging.info("KR slow-refresh delayed %.0fs (429 avoidance)", initial_delay)
                        time.sleep(initial_delay)
                logging.info("KR warm-up started (slow-refresh)")
                try:
                    adapter_cls = _get_scan_adapter_cls()
                    adapter = adapter_cls(market="KR", strategy="BALANCED")
                    _wm_workers = _get_config_int("WARMUP_WORKERS", 8, minimum=1, maximum=16)
                    results = adapter.scan_all(max_workers=_wm_workers)
                    logging.info("KR warm-up done: %d tickers", len(results) if results else 0)
                    if results:
                        try:
                            results = _annotate_one_liners(results)
                        except Exception as _e:
                            logging.debug("silent except (app.py): %s", _e)
                        # yfinance KR은 장중 전일 종가로 고착 → 네이버 실시간으로 교정
                        if _is_kr_market_open_window():
                            try:
                                results = _override_kr_day_chg(results)
                            except Exception as _e:
                                logging.warning("KR slow-refresh naver overlay failed: %s", _e)
                        try:
                            results = _enrich_greedzone_batch(results)
                        except Exception as _e:
                            logging.warning("KR slow-refresh GreedZone enrichment failed: %s", _e)
                        _apply_moat_bonus(results)
                        results = _attach_scan_deltas(results, "KR", adapter=adapter, save_snapshot=True)
                        results = _strip_heavy(results)
                        ts = int(time.time())
                        _store_scan_cache(("KR", "BALANCED", ""), ts, results)
                        _populate_sector_caches("KR", "BALANCED", results, ts)
                except Exception as e:
                    logging.warning("KR warm-up failed: %s", e)
            finally:
                _release_warmer_file_lock(handle)
        time.sleep(interval_sec)


def _start_kr_warmup_once() -> None:
    global _kr_warmup_started
    with _kr_warmup_lock:
        if _kr_warmup_started:
            return
        _kr_warmup_started = True
    if os.environ.get("DISABLE_KR_WARMUP", "").strip() in ("1", "true", "yes"):
        logging.info("KR warm-up disabled by env DISABLE_KR_WARMUP")
        return
    # KR quick-warm(pickle)은 즉시 시작, slow-refresh(yfinance)만 10초 지연
    # — US와 동시에 yfinance를 두드려 429를 유발하던 문제 회피하면서
    #   quick-warm 60초 공백을 제거.
    def _fast_kr():
        _kr_warmup_loop(initial_delay=10.0)
    threading.Thread(target=_fast_kr, daemon=True, name="kr-warmup").start()


# KR 공휴일(KRX 휴장일) — 시장 시간 가드용. 매년 1월 1주차 갱신 권장.
# 출처: KRX 매년 12월 발표. 주말 외 KRX 휴장만 포함(임시휴장 X).
_KR_HOLIDAYS_2026: frozenset = frozenset({
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18",  # 신정, 설
    "2026-03-01", "2026-03-02",                              # 삼일절(대체)
    "2026-05-05", "2026-05-25",                              # 어린이날, 석가탄신일
    "2026-06-03", "2026-06-06",                              # 6.3 대선, 현충일
    "2026-08-15", "2026-08-17",                              # 광복절(대체)
    "2026-09-24", "2026-09-25", "2026-09-26",                # 추석
    "2026-10-03", "2026-10-05", "2026-10-09",                # 개천절·대체, 한글날
    "2026-12-25", "2026-12-31",                              # 성탄, 연말 휴장
})


def _is_kr_market_open_window() -> bool:
    """KRX 정규장(09:00~15:30 KST) ± 30분 마진 + 주말·KRX 휴일 휴장."""
    from datetime import datetime, timezone, timedelta
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    if now_kst.weekday() in (5, 6):
        return False
    if now_kst.strftime("%Y-%m-%d") in _KR_HOLIDAYS_2026:
        return False
    minutes = now_kst.hour * 60 + now_kst.minute
    # 08:30 ~ 16:00 (정규장 ± 30분)
    return 8 * 60 + 30 <= minutes <= 16 * 60


def _get_config_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip()) if raw is not None and str(raw).strip() != "" else int(default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _run_with_timeout(func, timeout_sec: int, label: str):
    q: queue.Queue = queue.Queue(maxsize=1)

    def _worker():
        try:
            q.put((True, func()))
        except Exception as exc:
            q.put((False, exc))

    t = threading.Thread(target=_worker, daemon=True, name=f"{label}-worker")
    t.start()
    t.join(timeout=max(1, int(timeout_sec)))
    if t.is_alive():
        raise TimeoutError(f"{label} timed out after {timeout_sec}s")
    ok, payload = q.get()
    if ok:
        return payload
    raise payload


def _strip_kr_suffix(ticker: str) -> str:
    raw = (ticker or "").strip()
    for suf in (".KS", ".KQ", ".ks", ".kq"):
        if raw.endswith(suf):
            return raw[:-len(suf)]
    return raw


@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# ── 페이지 라우트 ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return _render_static_template("scanner.html")


@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "service": "canslim-quant-scanner",
        "render": _render_deployment(),
        "gzip": _FLASK_COMPRESS_OK,
    })


import re as _ticker_re_mod

# 화이트리스트: 1~12자 [A-Z0-9.-]. US(AAPL/BRK.B) + KR(005930/005930.KS) 모두 통과.
# path traversal / SSRF / prompt injection 1차 방어.
_TICKER_RE = _ticker_re_mod.compile(r"^[A-Za-z0-9\.\-]{1,12}$")


def _validate_ticker(ticker) -> str | None:
    """티커가 화이트리스트 통과하면 정규화된 값, 아니면 None."""
    if not ticker or not isinstance(ticker, str):
        return None
    t = ticker.strip()
    if not _TICKER_RE.match(t):
        return None
    return t


_KR_LOCAL_NAME_CACHE = None


def _lookup_kr_name_local(code6: str) -> str:
    """repo 내 테마 종목표에서 한국 종목명을 찾는 가벼운 fallback."""
    global _KR_LOCAL_NAME_CACHE
    if _KR_LOCAL_NAME_CACHE is None:
        names: dict[str, str] = {}
        path = os.path.join(_BASE, "theme_stocks.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    parts = raw.strip().split()
                    if len(parts) < 2:
                        continue
                    tk, nm = parts[0], parts[1]
                    code = tk.split(".")[0].zfill(6)
                    names.setdefault(code, nm)
                    names.setdefault(tk.upper(), nm)
        except OSError:
            pass
        _KR_LOCAL_NAME_CACHE = names
    return _KR_LOCAL_NAME_CACHE.get(code6) or ""


@app.route("/detail/<ticker>")
def detail(ticker: str):
    safe = _validate_ticker(ticker)
    if not safe:
        return "Invalid ticker", 400
    safe_ticker = html.escape(safe, quote=True)
    return _render_static_template("detail.html", {
        "{{ ticker }}": safe_ticker,
    })


@app.route("/pyramid")
def pyramid_page():
    return _render_static_template("pyramid.html")


@app.route("/api/pyramid_suggest")
def api_pyramid_suggest():
    """GET /api/pyramid_suggest?ticker=005930&market=KR
    KR yfinance 1y 일봉으로 MA20/50/200, ATR14, 52주 고저점 계산 후
    3단계 피라미딩 진입가 추천을 반환한다.
    """
    raw_ticker = _validate_ticker((request.args.get("ticker") or "").strip().upper())
    market = (request.args.get("market") or "KR").upper()

    if not raw_ticker:
        return jsonify({"error": "ticker 파라미터 필요"}), 400
    if market != "KR":
        return jsonify({"error": "US market is disabled; KR only"}), 410

    # yfinance용 심볼 변환 (KR 6자리 → 정확한 .KS/.KQ 접미사 우선)
    if raw_ticker.endswith((".KS", ".KQ")):
        yf_symbol = raw_ticker
    else:
        code6 = raw_ticker.zfill(6) if raw_ticker.isdigit() else raw_ticker
        suffix = _resolve_kr_suffix(code6) or ".KS"
        yf_symbol = f"{code6}{suffix}"

    try:
        import yfinance as yf
        import math

        hist = yf.Ticker(yf_symbol).history(period="1y", interval="1d", auto_adjust=True)
        if hist is None or len(hist) < 20:
            return jsonify({"error": f"{raw_ticker} 데이터 부족 (최소 20거래일 필요)"}), 404

        close = hist["Close"]
        high  = hist["High"]
        low   = hist["Low"]
        n     = len(close)

        current = float(close.iloc[-1])

        def ma(period):
            if n < period:
                return None
            return float(close.rolling(period).mean().iloc[-1])

        ma20  = ma(20)
        ma50  = ma(50)
        ma200 = ma(200)

        # ATR14: True Range = max(H-L, |H-PC|, |L-PC|)
        tr_list = []
        for i in range(1, n):
            h  = float(high.iloc[i])
            lo = float(low.iloc[i])
            pc = float(close.iloc[i - 1])
            tr_list.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        atr14 = sum(tr_list[-14:]) / min(14, len(tr_list)) if tr_list else current * 0.02

        wk52_high = float(high.max())
        wk52_low  = float(low.min())

        # 추세 판정
        above_ma50  = ma50  is not None and current > ma50
        above_ma200 = ma200 is not None and current > ma200

        if above_ma50 and above_ma200:
            regime = "uptrend"
        elif above_ma200:
            regime = "recovery"
        elif above_ma50:
            regime = "mixed"
        else:
            regime = "downtrend"

        def r2(v):
            if v is None:
                return None
            # 소수점 자리수: 현재가 기준으로 결정
            if current >= 1000:
                return round(v, 0)
            elif current >= 10:
                return round(v, 2)
            else:
                return round(v, 4)

        # ── 진입가 추천 로직 ────────────────────────────────────────────
        entries = []

        if regime == "uptrend":
            # 강한 상승 추세: 눌림목 → ATR 돌파 → 신고가 돌파
            e1_raw = ma20 if ma20 and ma20 < current else current
            e1 = r2(e1_raw)
            e2 = r2(e1_raw + atr14)
            # 52주 신고가 직상이 합리적이면 사용, 아니면 +2ATR
            e3_hi = r2(wk52_high * 1.003)
            e3 = e3_hi if e3_hi > e2 else r2(e1_raw + 2 * atr14)

            strategy = "모멘텀 피라미딩"
            strategy_desc = "MA50·MA200 위의 강한 상승세. 눌림목에서 시작해 모멘텀 확인 시 비중 확대."
            entries = [
                {"stage": 1, "price": e1,
                 "reason": f"MA20({r2(ma20)}) 눌림목 — 초기 진입",
                 "tag": "눌림목", "color": "#3b82f6"},
                {"stage": 2, "price": e2,
                 "reason": f"1차 진입가 + ATR14({r2(atr14)}) — 돌파 확인",
                 "tag": "ATR 돌파", "color": "#8b5cf6"},
                {"stage": 3, "price": e3,
                 "reason": f"52주 신고가({r2(wk52_high)}) 돌파 — 추세 가속",
                 "tag": "신고가", "color": "#f59e0b"},
            ]

        elif regime == "recovery":
            # MA200 위, MA50 아래: 회복 중 — MA50 돌파 확인 피라미딩
            e1 = r2(current)
            e2 = r2(ma50) if ma50 else r2(current + atr14)
            e3 = r2((ma50 or current) * 1.03)

            strategy = "회복 피라미딩"
            strategy_desc = "MA200 위 회복 중이나 MA50 아래. MA50 돌파를 확인하며 비중 단계적 확대."
            entries = [
                {"stage": 1, "price": e1,
                 "reason": f"현재가({r2(current)}) — MA200({r2(ma200)}) 위 회복 확인",
                 "tag": "현재가", "color": "#3b82f6"},
                {"stage": 2, "price": e2,
                 "reason": f"MA50({r2(ma50)}) 돌파 — 중기 추세 전환",
                 "tag": "MA50 돌파", "color": "#8b5cf6"},
                {"stage": 3, "price": e3,
                 "reason": f"MA50 안착 +3% — 추세 재확인",
                 "tag": "추세 확인", "color": "#f59e0b"},
            ]

        elif regime == "mixed":
            # MA50 위, MA200 아래: 중간 — 조심스러운 피라미딩
            e1 = r2(current)
            e2 = r2(current + atr14)
            e3 = r2(ma200) if ma200 and ma200 > e2 else r2(current + 2 * atr14)

            strategy = "중립 피라미딩"
            strategy_desc = "MA50 위지만 MA200 아래. 소량부터 시작해 MA200 돌파 확인 후 확대."
            entries = [
                {"stage": 1, "price": e1,
                 "reason": f"현재가({r2(current)}) — MA50({r2(ma50)}) 위 소량 진입",
                 "tag": "소량 진입", "color": "#3b82f6"},
                {"stage": 2, "price": e2,
                 "reason": f"1차 + ATR14({r2(atr14)}) — 모멘텀 확인",
                 "tag": "ATR 돌파", "color": "#8b5cf6"},
                {"stage": 3, "price": e3,
                 "reason": f"MA200({r2(ma200)}) 돌파 — 장기 추세 전환",
                 "tag": "MA200 돌파", "color": "#f59e0b"},
            ]

        else:
            # 하락 추세: 매우 보수적
            e1 = r2(current)
            e2 = r2(ma20) if ma20 and ma20 > current else r2(current + atr14)
            e3 = r2(ma50) if ma50 and ma50 > e2 else r2(current + 2 * atr14)

            strategy = "역추세 피라미딩 (주의)"
            strategy_desc = "하락 추세 중. 이동평균선 돌파를 반드시 확인한 후에만 비중 확대 권장."
            entries = [
                {"stage": 1, "price": e1,
                 "reason": f"현재가({r2(current)}) — 소량 탐색 진입",
                 "tag": "탐색 진입", "color": "#6b7280"},
                {"stage": 2, "price": e2,
                 "reason": f"MA20({r2(ma20)}) 돌파 — 단기 반등 확인",
                 "tag": "MA20 돌파", "color": "#3b82f6"},
                {"stage": 3, "price": e3,
                 "reason": f"MA50({r2(ma50)}) 돌파 — 중기 추세 전환",
                 "tag": "MA50 돌파", "color": "#8b5cf6"},
            ]

        # 손절 · 목표가
        stop_suggest   = r2(entries[0]["price"] - 1.5 * atr14)
        target_suggest = r2(entries[-1]["price"] * 1.07)

        # 52주 위치 (0~100%)
        wk52_range = wk52_high - wk52_low
        wk52_pos = round((current - wk52_low) / wk52_range * 100, 1) if wk52_range > 0 else 50.0

        return jsonify({
            "ticker":        yf_symbol,
            "market":        "KR",
            "current":       r2(current),
            "ma20":          r2(ma20),
            "ma50":          r2(ma50),
            "ma200":         r2(ma200),
            "atr14":         r2(atr14),
            "wk52_high":     r2(wk52_high),
            "wk52_low":      r2(wk52_low),
            "wk52_pos":      wk52_pos,
            "regime":        regime,
            "strategy":      strategy,
            "strategy_desc": strategy_desc,
            "entries":       entries,
            "stop_suggest":  stop_suggest,
            "target_suggest": target_suggest,
        })

    except Exception as exc:
        logging.exception("api_pyramid_suggest %s", raw_ticker)
        return jsonify({"error": str(exc)}), 500


@app.route("/compare")
def compare_page():
    raw = request.args.get("tickers", "")
    tickers = [tk.strip() for tk in raw.split(",") if tk.strip()][:4]
    market = "KR"
    return _render_static_template("compare.html", {
        "{{ tickers|tojson }}": json.dumps(tickers, ensure_ascii=False),
        "{{ market|tojson }}": json.dumps(market, ensure_ascii=False),
    })


# ?? JSON API ?????????????????????????????????????????????????????????????

@app.route("/api/sectors")
def api_sectors():
    """GET /api/sectors?market=KR → {"sectors": {...}}"""
    try:
        market = (request.args.get("market") or "KR").upper()
        if market not in _SUPPORTED_MARKETS:
            return jsonify({"error": "US market is disabled; KR only"}), 410
        adapter = _make_adapter()
        return jsonify({
            "sectors": adapter.get_sectors(),
            "groups":  adapter.get_sector_groups(),
        })
    except Exception as e:
        logging.exception("api_sectors")
        return jsonify({"error": str(e)}), 500


def _apply_aq_fusion(results: list, market: str, top_n: int = 30) -> list:
    """상위 TotalScore N개에 대해 AgentQuant 점수 융합 (순차 호출, 32-bit Python 안정성).
    동시 호출 시 yfinance+pandas 메모리 충돌로 힙 손상(0xC0000374) 발생 가능 → 직렬 처리.
    """
    if not results:
        return results
    try:
        from agentquant_signal import get_regime_signal
    except Exception as exc:
        logging.warning("agentquant import skipped: %s", exc)
        return results

    ranked = sorted(
        [r for r in results if isinstance(r, dict)],
        key=lambda r: (r.get("TotalScore") or 0),
        reverse=True,
    )[:top_n]

    aq_map: dict[str, dict] = {}
    for row in ranked:
        tk = row.get("Ticker") or row.get("ticker")
        if not tk:
            continue
        try:
            sig = get_regime_signal(tk, market=market)
            if sig:
                aq_map[tk] = sig
        except Exception as exc:
            logging.debug("aq fetch failed %s: %s", tk, exc)
            continue

    for r in results:
        tk = r.get("Ticker") or r.get("ticker")
        aq = aq_map.get(tk)
        if not aq or not aq.get("stock"):
            continue
        try:
            aq_score = float(aq["stock"].get("score") or 0)
            base = r.get("EntryScore")
            base_f = float(base) if base is not None else None
            fused = round(0.6 * base_f + 0.4 * aq_score, 1) if base_f is not None else round(aq_score, 1)
            r["EntryScore_engine"] = base_f
            r["EntryScore_aq"] = aq_score
            r["EntryScore"] = fused
            r["AQ_Verdict"] = aq["stock"].get("verdict_kr")
            r["AQ_VerdictCode"] = aq["stock"].get("verdict")
            r["AQ_Regime"] = aq.get("market", {}).get("label")
        except Exception:
            continue
    return results


@app.route("/api/scan")
def api_scan():
    """GET /api/scan?market=KR&strategy=BALANCED&sector=반도체 → [{...}, ...]"""
    try:
        sector  = request.args.get("sector", "")
        market  = (request.args.get("market") or "KR").upper()
        if market not in _SUPPORTED_MARKETS:
            return jsonify({"error": "US market is disabled; KR only"}), 410
        strategy = (request.args.get("strategy") or "BALANCED").upper()
        # 기본 0 — 32-bit Python 환경 안정성 우선. ?aq_top=10 또는 env AQ_SCAN_TOP 로 활성화.
        try:
            aq_top = int(request.args.get("aq_top", os.environ.get("AQ_SCAN_TOP", "0")))
        except (TypeError, ValueError):
            aq_top = 0

        # ── 스캔 결과 전체 캐시 조회 (stale-while-revalidate) ──
        # 캐시가 있으면 나이 무관 즉시 반환 + BG 갱신. 없을 때만 동기 스캔.
        _sr_key = (market, strategy, sector)
        _sr_now = int(time.time())
        with _scan_results_cache_lock:
            _sr_cached = _scan_results_cache.get(_sr_key)
        if _sr_cached:
            _age_sec = _sr_now - _sr_cached.get("_ts", 0)
            if _age_sec > _SCAN_RESULTS_TTL_SEC:
                _refresh_scan_background(market, strategy, sector)
            _cached_dirty = False
            if not _scan_rows_have_deltas(_sr_cached.get("data") or []):
                _sr_cached["data"] = _attach_scan_deltas(
                    _sr_cached.get("data") or [],
                    market,
                    adapter=None,
                    sector=sector,
                    save_snapshot=False,
                )
                _cached_dirty = True
            if _cached_dirty:
                _store_scan_cache(_sr_key, _sr_cached.get("_ts", _sr_now), _sr_cached["data"])
            # 사전 압축 캐시 히트 — flask_compress 재압축 완전 우회
            with _scan_gz_cache_lock:
                _gz_bytes = _scan_gz_cache.get(_sr_key)
            if _gz_bytes:
                resp = Response(_gz_bytes, mimetype="application/json")
                resp.headers["Content-Encoding"] = "gzip"
            else:
                resp = jsonify(_sr_cached["data"])
            try:
                cache_age_min, as_of_iso = _scan_cache_meta(market)
                if cache_age_min is not None:
                    resp.headers["X-Cache-Age-Min"] = str(cache_age_min)
                if as_of_iso:
                    resp.headers["X-As-Of"] = as_of_iso
                resp.headers["X-Warming-In-Progress"] = "true" if _age_sec > _SCAN_RESULTS_TTL_SEC else "false"
            except Exception as _e:
                logging.debug("silent except (app.py): %s", _e)
            return _mark_json_no_store(resp)

        adapter = _make_adapter()
        results = []
        warming_in_progress = False
        if market == "KR":
            # pickle 캐시에서 빠르게 읽기 (동기 yfinance 풀스캔 없음)
            results = adapter.scan_sector(sector, prefer_cache=True, cache_only=True) if sector else adapter.scan_all(prefer_cache=True, cache_only=True)
            if results:
                _refresh_scan_background(market, strategy, sector)
            else:
                # KR 섹터: in-memory 전체 캐시에서 필터링 시도
                if market == "KR" and sector:
                    with _scan_results_cache_lock:
                        _full = _scan_results_cache.get((market, strategy, ""))
                    if _full:
                        results = [r for r in _full["data"] if r.get("Sector") == sector]
                        if results:
                            with _scan_results_cache_lock:
                                _scan_results_cache[(market, strategy, sector)] = {
                                    "_ts": _full["_ts"], "data": results,
                                }
                # 캐시 없으면 BG 갱신만 트리거, 즉시 빈 결과 반환
                _refresh_scan_background(market, strategy, sector)
                if not results:
                    warming_in_progress = True
        else:
            results = adapter.scan_sector(sector) if sector else adapter.scan_all()
        # KR 종목은 네이버 실시간 등락률로 즉시 오버라이드 (yfinance 장중 고착 회피).
        # 8-worker 병렬 호출이라 50종목 기준 ~1~2초 추가. 사용자가 fallback을 원치 않음.
        if market == "KR":
            try:
                results = _override_kr_day_chg(results)
            except Exception as ne:
                logging.warning("naver DayChg override failed: %s", ne)
        # 촌철살인 한줄평 추가 (이미 채워진 경우 스킵)
        try:
            results = _annotate_one_liners(results)
        except Exception as oe:
            logging.warning("one_liner annotate failed: %s", oe)
        _apply_moat_bonus(results)
        # Phase-3: moat 주입 후 투기주 졸업 재평가
        from speculative_themes import apply_speculative_correction as _spec_reeval_sync
        _spec_reeval_sync(results)
        # AgentQuant 융합 (상위 N개)
        if aq_top > 0:
            try:
                results = _apply_aq_fusion(results, market, top_n=aq_top)
            except Exception as ae:
                logging.warning("aq fusion failed: %s", ae)
        results = _attach_scan_deltas(
            results,
            market,
            adapter=adapter,
            sector=sector,
            save_snapshot=not bool(sector),
            async_snapshot=True,
        )
        # ── 스캔 결과 전체 캐시 저장 (Breakdown 제거 + 사전 압축) ──
        results = _strip_heavy(results)
        _gz_bytes = _store_scan_cache(_sr_key, _sr_now, results) if results else b""
        # 사전 압축 bytes 직접 서빙 — flask_compress 재압축 우회 (gzip 지원 클라이언트 한정)
        if _gz_bytes and "gzip" in request.headers.get("Accept-Encoding", ""):
            resp = Response(_gz_bytes, mimetype="application/json")
            resp.headers["Content-Encoding"] = "gzip"
        else:
            resp = jsonify(results)
        try:
            cache_age_min, as_of_iso = _scan_cache_meta(market)
            if cache_age_min is not None:
                resp.headers["X-Cache-Age-Min"] = str(cache_age_min)
            if as_of_iso:
                resp.headers["X-As-Of"] = as_of_iso
            resp.headers["X-Warming-In-Progress"] = "true" if warming_in_progress else "false"
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
        return _mark_json_no_store(resp)
    except Exception as e:
        logging.exception("api_scan")
        return jsonify({"error": str(e)}), 500


@app.route("/api/macro")
def api_macro():
    """GET /api/macro → 상단 신호등 띠용 거시 지표. /api/scan 과 완전 분리."""
    try:
        import macro
        force = request.args.get("force") in ("1", "true", "yes")
        return jsonify(macro.get_macro(force=force))
    except Exception as e:
        logging.warning("api_macro failed: %s", e)
        return jsonify({
            "signal": {"level": "unknown", "emoji": "⚪", "label": "정보없음"},
            "indicators": {k: None for k in
                           ("vix", "sp500", "kospi", "usdkrw", "kr_rate")},
            "ts": None, "stale": True,
        })


@app.route("/api/index-meta")
def api_index_meta():
    """GET /api/index-meta → 지수 명단 기준일·신선도. UI '명단 기준일' 표시용."""
    try:
        import engine_adapter
        return jsonify(engine_adapter.index_membership_meta())
    except Exception as e:
        logging.warning("api_index_meta failed: %s", e)
        return jsonify({"generated": None, "stale_days": None, "is_stale": False})


@app.route("/api/etf")
def api_etf():
    """GET /api/etf → 한국 인기 ETF 현황. /api/scan 과 완전 분리."""
    try:
        import etf
        force = request.args.get("force") in ("1", "true", "yes")
        data = etf.get_etfs(force=force)
        data["us"] = []
        return jsonify(data)
    except Exception as e:
        logging.warning("api_etf failed: %s", e)
        return jsonify({"us": [], "kr": [], "ts": None, "stale": True})


@app.route("/api/etf-sectors/<path:ticker>")
def api_etf_sectors(ticker: str):
    """GET /api/etf-sectors/SPY → ETF 섹터 비중(지연 로딩). /api/etf 와 분리."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"ticker": "", "sectors": [], "holdings": [], "stale": True}), 400
    try:
        import etf
        return jsonify(etf.get_etf_sectors(ticker))
    except Exception as e:
        logging.warning("api_etf_sectors failed: %s", e)
        return jsonify({"ticker": ticker, "sectors": [], "holdings": [], "stale": True})


@app.route("/api/etf-rotation")
def api_etf_rotation():
    """GET /api/etf-rotation → 한국 ETF M1/M3 수익률 히트맵. /api/scan 과 분리."""
    try:
        import etf
        force = request.args.get("force") in ("1", "true", "yes")
        data = etf.get_etf_rotation(force=force)
        data["us"] = []
        return jsonify(data)
    except Exception as e:
        logging.warning("api_etf_rotation failed: %s", e)
        return jsonify({"us": [], "kr": [], "ts": None, "stale": True})


@app.route("/api/score-eval")
def api_score_eval():
    """GET /api/score-eval?market=KR → 점수 신호 표본외 검증 캐시(배지용).

    실제 IC 계산은 무거우므로(yfinance) score_eval.py 가 주기적으로 생성한
    web_app/score_eval_{MARKET}.json 캐시만 읽어 가볍게 서빙. 없으면 no_data.
    """
    market = (request.args.get("market") or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    _webapp_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(_webapp_dir, f"score_eval_{market}.json")
    try:
        if not os.path.exists(path):
            return jsonify({"market": market, "status": "no_data",
                            "badge": {"level": "none", "label": "검증 데이터 없음"}})
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        logging.warning("api_score_eval failed: %s", e)
        return jsonify({"market": market, "status": "error",
                        "badge": {"level": "none", "label": "검증 데이터 없음"}})


# ── 워치리스트 영속화 ─────────────────────────────────────────────────────
# 브라우저 localStorage 단독 저장은 캐시 삭제/기기 변경 시 손실되므로
# 서버 측 SQLite(watchlist.db)에 영속화한다.
_WL_DB_PATH = os.path.join(_BASE, "watchlist.db")
_wl_lock = threading.Lock()


def _wl_is_kr(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith(".KS") or t.endswith(".KQ")


def _wl_db():
    from watchlist import WatchlistDB
    return WatchlistDB(_WL_DB_PATH)


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_list():
    """GET /api/watchlist?market=KR|US → ["TICKER", ...]"""
    market = (request.args.get("market") or "KR").upper()
    with _wl_lock:
        db = _wl_db()
        try:
            tickers = db.list()
        finally:
            db.close()
    out = [t for t in tickers if _wl_is_kr(t)]
    return jsonify(out)


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    """POST /api/watchlist {ticker, note?} → {ok, added}"""
    data = request.get_json(silent=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    note = (data.get("note") or "").strip()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400
    with _wl_lock:
        db = _wl_db()
        try:
            added = db.add(ticker, note)
        finally:
            db.close()
    return jsonify({"ok": True, "added": added, "ticker": ticker})


@app.route("/api/watchlist/<path:ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker: str):
    """DELETE /api/watchlist/<ticker> → {ok, removed}"""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required"}), 400
    with _wl_lock:
        db = _wl_db()
        try:
            removed = db.remove(ticker)
        finally:
            db.close()
    return jsonify({"ok": True, "removed": removed, "ticker": ticker})


@app.route("/api/watchlist/bulk", methods=["POST"])
def api_watchlist_bulk():
    """POST /api/watchlist/bulk {tickers: [...]} → 일괄 추가 (localStorage 마이그레이션용)"""
    data = request.get_json(silent=True) or {}
    tickers = data.get("tickers") or []
    if not isinstance(tickers, list):
        return jsonify({"ok": False, "error": "tickers must be list"}), 400
    added = 0
    with _wl_lock:
        db = _wl_db()
        try:
            for t in tickers:
                t = str(t or "").strip().upper()
                if t and db.add(t):
                    added += 1
        finally:
            db.close()
    return jsonify({"ok": True, "added": added, "total": len(tickers)})


@app.route("/api/search")
def api_search():
    """GET /api/search?q=rf&market=KR → [{ticker, name}, ...] 이름/티커 부분매칭."""
    q = (request.args.get("q") or "").strip().lower()
    market = (request.args.get("market") or "KR").upper()
    if not q:
        return jsonify([])
    if market not in _SUPPORTED_MARKETS:
        return jsonify([])
    hits = []
    try:
        for tk, nm, blob in _get_search_idx(market):
            if q in blob:
                hits.append({"ticker": tk, "name": nm})
    except Exception as e:
        logging.warning("api_search failed: %s", e)
    # 정렬: (1) 이름이 q로 시작 (2) 티커가 q로 시작 (3) 이름 길이 짧은 순 (4) 알파벳
    hits.sort(key=lambda h: (
        not h["name"].lower().startswith(q),
        not h["ticker"].lower().startswith(q),
        len(h["name"]),
        h["name"],
    ))
    return jsonify(hits[:25])


@app.route("/api/ticker/<ticker>")
def api_ticker(ticker: str):
    """GET /api/ticker/005930.KS?market=KR&strategy=BALANCED → {Ticker, TotalScore, ...}"""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market_arg = (request.args.get("market") or "KR").upper()
    if market_arg not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    strategy_arg = request.args.get("strategy", "BALANCED")
    force_refresh = request.args.get("refresh") in ("1", "true", "yes")
    # ── 응답 캐시 조회 (동일 종목 재오픈 시 즉시 반환) ──
    _td_key = f"{ticker}:{market_arg}:{strategy_arg}"
    _td_now = int(time.time())
    with _ticker_detail_cache_lock:
        if force_refresh:
            _ticker_detail_cache.pop(_td_key, None)
        _td_cached = None if force_refresh else _ticker_detail_cache.get(_td_key)
        if _td_cached and (_td_now - _td_cached.get("_ts", 0)) < _TICKER_DETAIL_TTL_SEC:
            if market_arg == "KR" and (
                not _has_bf_breakdown(_td_cached.get("data"))
                or not _has_current_kr_detail_schema(_td_cached.get("data"))
                or not (_td_cached.get("data") or {}).get("BrokerTarget")
            ):
                _ticker_detail_cache.pop(_td_key, None)
            else:
                # 한줄평은 최신 로직으로 재생성하되, moat(disk I/O)는 재계산하지 않음
                fresh = dict(_td_cached["data"])
                if market_arg == "KR":
                    _apply_kr_toss_stock_names([fresh])
                    _apply_curated_detail_sector(fresh, market_arg)
                    _apply_kr_broker_target_fallback([fresh], limit=None)
                    _overlay_kr_realtime_quote(fresh, sync_new_high=True)
                    _sync_detail_score_from_scan_cache(fresh, market_arg, strategy_arg)
                try:
                    from one_liner import annotate as _ol_annotate
                    _ol_annotate([fresh])
                except Exception:
                    pass
                return jsonify(fresh)
    try:
        adapter = _make_adapter()
        result  = adapter.analyze_ticker(ticker, prefer_cache=True, force_refresh=force_refresh)
        market = market_arg
        if result is None:
            return jsonify({"error": "해당 티커의 데이터를 찾을 수 없습니다."}), 404
        if market == "KR":
            _apply_kr_toss_stock_names([result])
            _apply_curated_detail_sector(result, market)
            _apply_kr_broker_target_fallback([result], limit=None)
            code6 = _strip_kr_suffix(ticker).zfill(6)
            name_now = str(result.get("Name") or "").strip()
            if not name_now or name_now in {ticker, code6, f"{code6}.KS", f"{code6}.KQ"}:
                try:
                    from quant_nexus_v20 import QuantNexusApp
                    fixed = (
                        QuantNexusApp.KR_NAMES.get(f"{code6}.KS")
                        or QuantNexusApp.KR_NAMES.get(f"{code6}.KQ")
                        or ""
                    )
                    if fixed:
                        result["Name"] = fixed
                except Exception as _e:
                    logging.debug("silent except (app.py): %s", _e)
            _overlay_kr_realtime_quote(result, sync_new_high=True)
            # 네이버 투자자 동향은 /api/investor_flow/<ticker>로 분리 (lazy-load)
            result["_Investor_Available"] = False
            # 캐시가 BrokerTarget=0으로 저장된 경우 실시간 재조회
            if not result.get("BrokerTarget"):
                try:
                    bt = adapter._fetch_naver_target(ticker)
                    if bt and bt > 0:
                        result["BrokerTarget"] = float(bt)
                        code6_ = _strip_kr_suffix(ticker).zfill(6)
                        result["BrokerTargetSource"] = (
                            getattr(adapter, "_naver_target_meta", {}).get(code6_, "")
                            or "네이버 증권 컨센서스 (국내 증권사 평균)"
                        )
                except Exception as _bte:
                    logging.debug("BrokerTarget live fetch failed: %s", _bte)
        else:
            # US 종목: US_NAMES 한글명 우선 적용
            try:
                from quant_nexus_v20 import QuantNexusApp
                _us_nm = getattr(QuantNexusApp, "US_NAMES", {}).get(ticker)
                if _us_nm:
                    result["Name"] = _us_nm
            except Exception as _e:
                logging.debug("silent except (app.py): %s", _e)
            # US 종목: yfinance + Finnhub 센티먼트는 /api/sentiment/<ticker>로 분리 (lazy-load).
            # 종목 상세 패널 첫 paint 지연(5~10s yfinance .info hang)을 제거하기 위함.
            # 프론트는 sentiment 응답 도착 시 _YF_*/_FH_* 키를 머지.
            result["_YF_Available"] = False
            result["_FH_Available"] = False
        try:
            result = _annotate_one_liners([result])[0]
        except Exception as oe:
            logging.warning("one_liner annotate (ticker) failed: %s", oe)
        _apply_moat_bonus([result])
        # Phase-3: moat 주입 후 투기주 졸업 재평가
        # (engine_adapter에서 moat 없이 1차 평가 → moat 주입 후 2차 재평가)
        from speculative_themes import apply_to_row as _spec_reeval
        _spec_reeval(result)

        # ── MECE 분석 프레임워크 부착 (Phase 1/2/3) ──
        try:
            from web_app.valuation_context import attach_valuation_context as _mece_val
            # 캐시된 스캔 결과에서 동일 섹터 종목 조회
            _mece_peers = []
            _mece_sector = (result.get("Sector") or "").strip()
            if _mece_sector:
                with _scan_results_cache_lock:
                    for _mk in ("BALANCED", "AGGRESSIVE", "CONSERVATIVE"):
                        _mc = _scan_results_cache.get((market_arg, _mk, ""))
                        if _mc and _mc.get("data"):
                            _mece_peers = [r for r in _mc["data"]
                                           if (r.get("Sector") or "").strip() == _mece_sector]
                            break
            _mece_val(result, _mece_peers)
        except Exception as _mece_e:
            logging.debug("MECE valuation context failed: %s", _mece_e)
            result.setdefault("ValPctile", None)
            result.setdefault("SectorRelPE", None)
            result.setdefault("PriceInLevel", None)

        try:
            from web_app.scenario_engine import build_scenario_table as _mece_scenario
            result["Scenarios"] = _mece_scenario(result)
        except Exception as _mece_e2:
            logging.debug("MECE scenario failed: %s", _mece_e2)
            result.setdefault("Scenarios", None)

        # ATR top-level 필드 추출 (EntryPlan.atr_pct → ATRPercent 순으로 폴백)
        result['ATR'] = (result.get('volatility') or {}).get('details', {}).get('atr', 0) or 0
        result['ATR_pct'] = (
            (result.get('EntryPlan') or {}).get('atr_pct', 0)
            or result.get('ATRPercent', 0)
            or (result.get('volatility') or {}).get('details', {}).get('atr_pct', 0)
            or 0
        )

        try:
            from web_app.price_levels import build_price_strategy as _mece_price
            _mece_scenarios = result.get("Scenarios", {}).get("scores") if result.get("Scenarios") else None
            try:
                import macro_gate
                _vol = macro_gate.get_vol_index(market)  # KR=VKOSPI, US=VIX (캐시)
            except Exception as _vol_e:
                logging.debug("vol index fetch failed: %s", _vol_e)
                _vol = None
            result["PriceLevels"] = _mece_price(result, _mece_scenarios, vol=_vol)
        except Exception as _mece_e3:
            logging.warning("MECE price levels failed: %s", _mece_e3)
            result.setdefault("PriceLevels", None)

        if market == "KR":
            _sync_detail_score_from_scan_cache(result, market_arg, strategy_arg)

        # ── 응답 캐시 저장 ──
        with _ticker_detail_cache_lock:
            if len(_ticker_detail_cache) >= _TICKER_DETAIL_MAX:
                _ticker_detail_cache.pop(next(iter(_ticker_detail_cache)), None)
            _ticker_detail_cache[_td_key] = {"_ts": int(time.time()), "data": result}
        return jsonify(result)
    except Exception as e:
        logging.exception("api_ticker")
        return jsonify({"error": str(e)}), 500


@app.route("/api/market_context")
def api_market_context():
    """US market context is disabled in KR-only mode."""
    return jsonify({"error": "US market is disabled; KR only"}), 410


_sentiment_cache: dict[str, dict] = {}
_sentiment_cache_lock = threading.Lock()
_SENTIMENT_TTL_SEC = 300  # 5분


@app.route("/api/sentiment/<ticker>")
def api_sentiment(ticker: str):
    """GET /api/sentiment/AAPL → yfinance + Finnhub 센티먼트 (lazy-load).

    /api/ticker 응답 후 프론트가 별도로 호출해서 _YF_*/_FH_* 키를 머지한다.
    yfinance .info 호출이 5~10초 hang하는 케이스를 격리해 첫 paint 지연을 막는다.
    """
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    return jsonify({"ok": False, "reason": "us_market_disabled"}), 410

    # ── 5분 TTL 메모리 캐시: 같은 종목 반복 호출시 yfinance/Finnhub 재호출 회피 ──
    _now = time.time()
    with _sentiment_cache_lock:
        _cached = _sentiment_cache.get(ticker)
        if _cached and (_now - _cached.get("_ts", 0)) < _SENTIMENT_TTL_SEC:
            return jsonify(_cached["data"])

    out: dict = {"ok": True, "ticker": ticker}
    # yfinance 수급/센티먼트
    try:
        import yfinance as yf
        yf_info = _run_with_timeout(
            lambda: yf.Ticker(ticker).info, 5, f"yf_info {ticker}"
        ) or {}
        short_pct = yf_info.get("shortPercentOfFloat")
        inst_pct = yf_info.get("heldPercentInstitutions")
        rec_mean = yf_info.get("recommendationMean")
        target_mean = yf_info.get("targetMeanPrice")
        cur_price = yf_info.get("currentPrice")
        n_analysts = yf_info.get("numberOfAnalystOpinions")
        if short_pct is not None:
            out["_YF_ShortPctFloat"] = round(short_pct * 100, 2)
        if inst_pct is not None:
            out["_YF_InstPct"] = round(inst_pct * 100, 1)
        if rec_mean is not None and n_analysts:
            out["_YF_RecMean"] = round(rec_mean, 2)
            out["_YF_RecKey"] = yf_info.get("recommendationKey", "")
            out["_YF_NumAnalysts"] = int(n_analysts)
        if target_mean and cur_price and cur_price > 0:
            gap_pct = (target_mean - cur_price) / cur_price * 100
            out["_YF_TargetGapPct"] = round(gap_pct, 1)
        out["_YF_Available"] = True
    except Exception as ye:
        logging.debug("yfinance sentiment failed for %s: %s", ticker, ye)
        out["_YF_Available"] = False

    # Finnhub 데이터 (내부자/추천 변화/실적 서프라이즈/뉴스/실시간 시세)
    try:
        from finnhub_api import get_sentiment_data, is_available as fh_ok
        if fh_ok():
            fh = get_sentiment_data(ticker)
            if fh.get("available"):
                out["_FH_InsiderNet"] = fh["insider_net_shares"]
                out["_FH_InsiderCount"] = fh["insider_tx_count"]
                out["_FH_RecBuy"] = fh["rec_strong_buy"] + fh["rec_buy"]
                out["_FH_RecSell"] = fh["rec_sell"]
                out["_FH_RecChange"] = fh["rec_change"]
                out["_FH_EarnSurprise"] = fh["earnings_surprise_pct"]
                out["_FH_EarnStreak"] = fh["earnings_beat_streak"]
                out["_FH_DaysToEarnings"] = fh.get("days_to_earnings_est", -1)
                out["_FH_NextEarnings"] = fh.get("next_earnings_estimate", "")
                out["_FH_News7d"] = fh.get("news_count_7d", 0)
                out["_FH_Headlines"] = fh.get("news_headlines", [])
                out["_FH_DayChangePct"] = fh.get("day_change_pct", 0)
                out["_FH_CurrentPrice"] = fh.get("current_price", 0)
                out["_FH_Logo"] = fh.get("logo", "")
                out["_FH_IpoDate"] = fh.get("ipo_date", "")
                out["_FH_ShareOut"] = fh.get("share_outstanding")
                out["_FH_Industry"] = fh.get("industry", "")
                out["_FH_Exchange"] = fh.get("exchange", "")
                out["_FH_MSPR"] = fh.get("mspr")
                out["_FH_MSPRTrend"] = fh.get("mspr_trend", [])
                out["_FH_MSPRChange"] = fh.get("mspr_change", 0.0)
                out["_FH_Available"] = True
            else:
                out["_FH_Available"] = False
        else:
            out["_FH_Available"] = False
    except Exception as fe:
        logging.debug("Finnhub sentiment failed for %s: %s", ticker, fe)
        out["_FH_Available"] = False

    # 캐시 저장
    with _sentiment_cache_lock:
        if len(_sentiment_cache) > 500:
            _sentiment_cache.pop(next(iter(_sentiment_cache)), None)
        _sentiment_cache[ticker] = {"_ts": int(time.time()), "data": out}
    return jsonify(out)


@app.route("/api/investor_flow/<ticker>")
def api_investor_flow(ticker: str):
    """GET /api/investor_flow/005930?market=KR → 네이버 외인/기관 순매수 (lazy-load).

    /api/ticker의 KR blocking 병목 제거를 위해 분리.
    """
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    if market != "KR":
        return jsonify({"ok": False, "reason": "us_not_supported"})
    try:
        from naver_finance import get_investor_flow
        code6 = _strip_kr_suffix(ticker).zfill(6)
        inv = get_investor_flow(code6)
        if inv.get("rows"):
            return jsonify({
                "ok": True,
                "ticker": ticker,
                "_Investor_Foreign": int(inv.get("foreign_net_latest") or 0),
                "_Investor_Institution": int(inv.get("inst_net_latest") or 0),
                "_Investor_Available": True,
            })
        return jsonify({"ok": False, "ticker": ticker, "_Investor_Available": False})
    except Exception as e:
        logging.debug("investor_flow failed for %s: %s", ticker, e)
        return jsonify({"ok": False, "_Investor_Available": False})






def _peers_from_finnhub(ticker: str, limit: int) -> dict | None:
    """US 종목 전용: Finnhub company_peers + basic_financials로 라이브 데이터 생성.

    스캔 캐시 의존성을 우회해 stale 시총/PE 문제를 해결한다.
    """
    try:
        import finnhub_api as fh
        if not fh.is_available():
            return None
        peer_tickers = fh.get_peers(ticker)
        if not peer_tickers:
            return None

        def _build_row(tk: str, name_fallback: str = "") -> dict:
            try:
                import yfinance as yf
                yi = _run_with_timeout(lambda: yf.Ticker(tk).info or {}, 5, f"peer_info_{tk}") or {}
            except Exception:
                yi = {}
            fin = fh.get_basic_financials(tk)
            try:
                q = fh._client().quote(tk)
                price = q.get("c") if q else None
            except Exception:
                price = yi.get("currentPrice") or yi.get("regularMarketPrice")
            _us_nm = getattr(QuantNexusApp, "US_NAMES", {}).get(tk)
            return {
                "Ticker":          tk,
                "Name":            _us_nm or yi.get("shortName") or yi.get("longName") or name_fallback or tk,
                "Sector":          yi.get("sector") or "",
                "Industry":        yi.get("industry") or "",
                "Price":           price,
                "TotalScore":      None,
                "MarketCap":       fin.get("marketCap") or yi.get("marketCap"),
                "PER":             fin.get("trailingPE") or yi.get("trailingPE"),
                "PBR":             fin.get("priceToBook") or yi.get("priceToBook"),
                "ROE":             fin.get("returnOnEquity") or yi.get("returnOnEquity"),
                "OperatingMargin": fin.get("operatingMargins") or yi.get("operatingMargins"),
                "Mom12M":          None,
                "DivYield":        fin.get("dividendYield") or yi.get("dividendYield"),
            }

        me_row = _build_row(ticker)
        peer_rows = []
        for tk in peer_tickers[:limit]:
            try:
                peer_rows.append(_build_row(tk))
            except Exception as e:
                logging.warning("peer row build failed for %s: %s", tk, e)
        # 시총 큰 순
        peer_rows.sort(key=lambda r: -float(r.get("MarketCap") or 0))
        return {
            "ok": True,
            "self":  me_row,
            "peers": peer_rows,
            "sector":   me_row.get("Sector") or "",
            "industry": me_row.get("Industry") or "",
            "count":    len(peer_rows),
            "source":   "finnhub",
        }
    except Exception as e:
        logging.warning("_peers_from_finnhub(%s) failed: %s", ticker, e)
        return None


@app.route("/api/peers/<ticker>")
def api_peers(ticker: str):
    """GET /api/peers/AAPL?market=US&limit=5 → 같은 섹터 동종업체 비교 카드 데이터."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    try:
        limit = max(2, min(8, int(request.args.get("limit", "5"))))
    except (TypeError, ValueError):
        limit = 5

    is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ") or ticker.isdigit()
    if market not in _SUPPORTED_MARKETS or not is_kr:
        return jsonify({"error": "US market is disabled; KR only"}), 410

    # KR 기존 스캔 캐시 폴백
    rows = []
    with _scan_results_cache_lock:
        for strat in ("BALANCED", "AGGRESSIVE", "CONSERVATIVE"):
            cached = _scan_results_cache.get((market, strat, ""))
            if cached and cached.get("data"):
                rows = cached["data"]
                break
    if not rows:
        return jsonify({"ok": False, "reason": "no_scan_cache",
                        "message": "스캔 캐시가 비어 있습니다. 스캔을 먼저 실행해 주세요."})

    # 본인 종목 찾기
    me = next((r for r in rows if (r.get("Ticker") or "") == ticker), None)
    if not me:
        return jsonify({"ok": False, "reason": "ticker_not_in_cache",
                        "message": "이 종목이 스캔 캐시에 없습니다."})
    sector = (me.get("Sector") or "").strip()
    industry = (me.get("Industry") or "").strip()
    if not sector and not industry:
        return jsonify({"ok": False, "reason": "no_sector",
                        "message": "섹터 정보가 없어 비교할 수 없습니다."})

    def _same_bucket(r: dict) -> bool:
        if r.get("Ticker") == ticker:
            return False
        rs = (r.get("Sector") or "").strip()
        ri = (r.get("Industry") or "").strip()
        # Sector가 있으면 반드시 일치해야 함 — Industry 라벨이 섹터를
        # 가로질러 겹치는 경우(예: yfinance 분류 흔들림) 다른 섹터 종목이
        # peers에 끼는 것을 막는다.
        if sector:
            return rs == sector
        # me 자체에 Sector가 없을 때만 Industry 폴백.
        return bool(industry and ri and ri == industry)

    candidates = [r for r in rows if _same_bucket(r)]
    # 같은 Industry를 우선, 그 다음 시총 큰 순.
    candidates.sort(key=lambda r: (
        0 if (industry and (r.get("Industry") or "").strip() == industry) else 1,
        -float(r.get("_MarketCap") or 0),
    ))
    peers = candidates[:limit]

    try:
        from quant_nexus_v20 import QuantNexusApp as _QNA
        _us_names_peer = getattr(_QNA, "US_NAMES", {}) if market == "US" else {}
    except Exception:
        _us_names_peer = {}

    def _row(r: dict) -> dict:
        _tk = r.get("Ticker") or ""
        # 0 은 '데이터 없음' — None 으로 변환해 프론트에서 '—' 표시
        def _nz(v):
            return v if v else None
        return {
            "Ticker":          _tk,
            "Name":            _us_names_peer.get(_tk) or r.get("Name") or "",
            "Sector":          r.get("Sector") or "",
            "Industry":        r.get("Industry") or "",
            "Price":           r.get("Price"),
            "TotalScore":      r.get("TotalScore"),
            "MarketCap":       _nz(r.get("_MarketCap")),
            "PER":             _nz(r.get("_PER")),
            "PBR":             _nz(r.get("_PBR")),
            "ROE":             _nz(r.get("_ROE")),
            "OperatingMargin": _nz(r.get("_OperatingMargin")),
            "Mom12M":          r.get("Mom12M"),
            "DivYield":        r.get("_DivYield"),
        }

    return jsonify({
        "ok": True,
        "self":  _row(me),
        "peers": [_row(p) for p in peers],
        "sector":   sector,
        "industry": industry,
        "count":    len(peers),
    })




# ── 매출 세그먼트 파이 ─────────────────────────────────────────────────────
_segment_data_cache: dict = {}
_segment_data_mtime: float = 0.0
_segment_data_lock = threading.Lock()


def _load_segment_data() -> dict:
    """segment_data.json 을 mtime 기반으로 캐시 로드."""
    global _segment_data_cache, _segment_data_mtime
    path = os.path.join(os.path.dirname(__file__), "segment_data.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    with _segment_data_lock:
        if mt != _segment_data_mtime or not _segment_data_cache:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _segment_data_cache = json.load(f)
                _segment_data_mtime = mt
            except Exception as e:
                logging.warning("segment_data load failed: %s", e)
                return _segment_data_cache or {}
        return _segment_data_cache


@app.route("/api/segments/<ticker>")
def api_segments(ticker: str):
    """GET /api/segments/AAPL → 사업부문 매출 비중(큐레이션 사전)."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    data = _load_segment_data()
    if not data:
        return jsonify({"ok": False, "reason": "no_dict",
                        "message": "세그먼트 사전이 없습니다."})
    rec = data.get(ticker)
    if not rec:
        return jsonify({"ok": False, "reason": "not_found",
                        "message": "이 종목은 아직 세그먼트 사전에 없습니다."})
    segs = rec.get("segments") or []
    if not isinstance(segs, list) or not segs:
        return jsonify({"ok": False, "reason": "empty"})
    # 음수(상계) 분리: 파이엔 표시 안 함, 표에는 노출
    pie = [s for s in segs if isinstance(s.get("pct"), (int, float)) and s["pct"] > 0]
    return jsonify({
        "ok":         True,
        "ticker":     ticker,
        "fy":         rec.get("fy") or "",
        "source":     rec.get("source") or "",
        "confidence": rec.get("confidence") or "estimated",
        "segments":   segs,
        "pie":        pie,
        "count":      len(segs),
    })


# ── 이벤트 캘린더 ──────────────────────────────────────────────────────────
_macro_events_cache: list = []
_macro_events_mtime: float = 0.0
_macro_events_lock = threading.Lock()
_ticker_events_cache: dict = {}   # ticker -> (ts, payload)
_ticker_events_lock = threading.Lock()
_TICKER_EVENTS_TTL = 4 * 3600     # 4시간
_TICKER_EVENTS_MAX = 500          # 무제한 증가 차단(FIFO eviction)


def _load_macro_events() -> list:
    """매크로 이벤트 로드 — NFP/CPI 자동 생성 + JSON 교정 병합."""
    global _macro_events_cache, _macro_events_mtime
    try:
        from macro_calendar import get_macro_events
        with _macro_events_lock:
            # macro_calendar 내부 24h 캐시 사용
            _macro_events_cache = get_macro_events()
            return _macro_events_cache
    except Exception as e:
        logging.warning("macro_calendar failed, falling back to JSON: %s", e)
    # fallback: 기존 JSON 직접 로드
    path = os.path.join(os.path.dirname(__file__), "macro_events.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    with _macro_events_lock:
        if mt != _macro_events_mtime or not _macro_events_cache:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    j = json.load(f)
                _macro_events_cache = j.get("events", []) if isinstance(j, dict) else []
                _macro_events_mtime = mt
            except Exception as e:
                logging.warning("macro_events load failed: %s", e)
                return _macro_events_cache or []
        return _macro_events_cache


def _fetch_ticker_events(ticker: str) -> list:
    """yfinance에서 다음 실적일·배당락일 추출."""
    import datetime as _dt
    events: list = []
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # 다음 실적일
        try:
            cal = _run_with_timeout(lambda: t.calendar, 5, "ticker_events_cal")
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    if isinstance(ed, list) and ed:
                        ed_val = ed[0]
                    else:
                        ed_val = ed
                    ds = str(ed_val)[:10]
                    events.append({"date": ds, "name": "실적 발표", "kind": "earnings"})
                xd = cal.get("Ex-Dividend Date")
                if xd:
                    events.append({"date": str(xd)[:10], "name": "배당락일", "kind": "dividend"})
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
        # info에서 보조 필드
        try:
            info = _run_with_timeout(lambda: t.info or {}, 5, "ticker_events_info") or {}
            ts_earn = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
            if ts_earn and not any(e["kind"] == "earnings" for e in events):
                d = _dt.datetime.fromtimestamp(int(ts_earn), tz=_dt.timezone.utc).date().isoformat()
                events.append({"date": d, "name": "실적 발표", "kind": "earnings"})
            ts_div = info.get("exDividendDate")
            if ts_div and not any(e["kind"] == "dividend" for e in events):
                d = _dt.datetime.fromtimestamp(int(ts_div), tz=_dt.timezone.utc).date().isoformat()
                events.append({"date": d, "name": "배당락일", "kind": "dividend"})
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
    except Exception as e:
        logging.debug("ticker events fetch failed %s: %s", ticker, e)
    return events


@app.route("/api/macro-events")
def api_macro_events():
    """GET /api/macro-events?region=US|KR → 다가올 매크로 이벤트(60일)."""
    import datetime as _dt
    region = (request.args.get("region") or "US").upper()
    today = _dt.date.today()
    horizon = today + _dt.timedelta(days=60)
    macro_all = _load_macro_events()
    out = []
    for e in macro_all:
        if e.get("region") not in (region, "GLOBAL"):
            continue
        ds = e.get("date")
        if not ds:
            continue
        try:
            d = _dt.date.fromisoformat(ds[:10])
        except Exception:
            continue
        if d < today or d > horizon:
            continue
        out.append({
            "date": d.isoformat(),
            "dday": (d - today).days,
            "name": e.get("name") or "—",
            "kind": e.get("kind") or "other",
        })
    out.sort(key=lambda x: x["dday"])
    return jsonify({"ok": True, "region": region, "events": out, "count": len(out)})


@app.route("/api/events/<ticker>")
def api_events(ticker: str):
    """GET /api/events/AAPL → 다가올 이벤트 (실적·배당락·매크로) D-day 리스트."""
    import datetime as _dt
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400

    today = _dt.date.today()
    horizon = today + _dt.timedelta(days=120)

    # 종목 이벤트 (캐시)
    now = int(time.time())
    with _ticker_events_lock:
        cached = _ticker_events_cache.get(ticker)
    if cached and (now - cached[0]) < _TICKER_EVENTS_TTL:
        ticker_evs = cached[1]
    else:
        ticker_evs = _fetch_ticker_events(ticker)
        with _ticker_events_lock:
            if len(_ticker_events_cache) >= _TICKER_EVENTS_MAX:
                _ticker_events_cache.pop(next(iter(_ticker_events_cache)), None)
            _ticker_events_cache[ticker] = (now, ticker_evs)

    # 종목 이벤트만 (매크로는 별도 엔드포인트에서)
    is_kr = ticker.replace(".KS", "").replace(".KQ", "").isdigit()
    region = "KR" if is_kr else "US"

    merged = []
    for e in ticker_evs:
        ds = e.get("date")
        if not ds:
            continue
        try:
            d = _dt.date.fromisoformat(ds[:10])
        except Exception:
            continue
        if d < today or d > horizon:
            continue
        dday = (d - today).days
        merged.append({
            "date":  d.isoformat(),
            "dday":  dday,
            "name":  e.get("name") or "—",
            "kind":  e.get("kind") or "other",
        })
    merged.sort(key=lambda x: x["dday"])

    return jsonify({
        "ok":     bool(merged),
        "ticker": ticker,
        "region": region,
        "events": merged,
        "count":  len(merged),
    })


# ── 인사이더 거래 타임라인 ────────────────────────────────────────────────
_insider_cache: dict = {}   # ticker -> (ts, payload)
_insider_lock = threading.Lock()
_INSIDER_TTL = 12 * 3600    # 12시간
_INSIDER_MAX = 500          # 무제한 증가 차단


def _fetch_insider_transactions(ticker: str) -> dict:
    """yfinance insider_transactions → 6개월 이내 거래 추출."""
    import datetime as _dt
    out = {"transactions": [], "summary": {"buy": 0.0, "sell": 0.0, "net": 0.0}}
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = None
        try:
            df = _run_with_timeout(lambda: t.insider_transactions, 8, "insider_transactions")
        except Exception:
            df = None
        if df is None or df.empty:
            return out
        cutoff = _dt.date.today() - _dt.timedelta(days=180)
        # 컬럼은 yfinance 버전에 따라 다양: Insider, Position, Shares, Value, Transaction, Start Date, Text
        cols = {c.lower().strip(): c for c in df.columns}
        def col(*aliases):
            for a in aliases:
                if a in cols:
                    return cols[a]
            return None
        c_name  = col("insider")
        c_role  = col("position")
        c_date  = col("start date", "date")
        c_shr   = col("shares")
        c_val   = col("value")
        c_txn   = col("text", "transaction", "action")  # Text가 실제 매매 설명을 담는다 (yfinance)

        rows = []
        for _, r in df.iterrows():
            try:
                ds = r[c_date] if c_date else None
                if ds is None:
                    continue
                d = ds.date() if hasattr(ds, 'date') else _dt.date.fromisoformat(str(ds)[:10])
                if d < cutoff:
                    continue
                txn_raw = ''
                # Text 우선, 비면 Transaction 시도
                for cand in (c_txn, cols.get("transaction")):
                    if cand and r[cand] is not None and not _is_nan(r[cand]):
                        s = str(r[cand]).strip()
                        if s:
                            txn_raw = s
                            break
                txn_low = txn_raw.lower()
                if 'gift' in txn_low or 'grant' in txn_low or 'award' in txn_low:
                    side = 'grant'
                elif 'option' in txn_low or 'exercise' in txn_low:
                    side = 'option'
                elif any(k in txn_low for k in ('sale', 'sell', '매도', 'dispos')):
                    side = 'sell'
                elif any(k in txn_low for k in ('buy', 'purchase', '매수', 'acquir', 'bought')):
                    side = 'buy'
                else:
                    side = 'other'
                shares = float(r[c_shr]) if c_shr and r[c_shr] is not None and not _is_nan(r[c_shr]) else None
                value  = float(r[c_val]) if c_val and r[c_val] is not None and not _is_nan(r[c_val]) else None
                rows.append({
                    "date":   d.isoformat(),
                    "name":   str(r[c_name]) if c_name else '—',
                    "role":   str(r[c_role]) if c_role else '',
                    "side":   side,
                    "txn":    txn_raw,
                    "shares": shares,
                    "value":  value,
                })
            except Exception:
                continue
        rows.sort(key=lambda x: x["date"], reverse=True)

        buy = sum((r["value"] or 0) for r in rows if r["side"] == "buy")
        sell = sum((r["value"] or 0) for r in rows if r["side"] == "sell")
        out["transactions"] = rows[:30]
        out["summary"] = {"buy": buy, "sell": sell, "net": buy - sell, "count": len(rows)}
    except Exception as e:
        logging.debug("insider fetch failed %s: %s", ticker, e)
    return out


def _is_nan(v):
    try:
        import math
        if isinstance(v, float):
            return math.isnan(v)
        return v != v
    except Exception:
        return False


@app.route("/api/insider/<ticker>")
def api_insider(ticker: str):
    """GET /api/insider/AAPL → 최근 6개월 임원/내부자 거래."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400

    is_kr = ticker.replace(".KS", "").replace(".KQ", "").isdigit()
    if is_kr:
        return jsonify({"ok": False, "reason": "kr_unsupported",
                        "message": "한국 종목은 임원공시 데이터를 아직 지원하지 않습니다."})

    now = int(time.time())
    with _insider_lock:
        cached = _insider_cache.get(ticker)
    if cached and (now - cached[0]) < _INSIDER_TTL:
        payload = cached[1]
    else:
        payload = _fetch_insider_transactions(ticker)
        with _insider_lock:
            if len(_insider_cache) >= _INSIDER_MAX:
                _insider_cache.pop(next(iter(_insider_cache)), None)
            _insider_cache[ticker] = (now, payload)

    if not payload.get("transactions"):
        return jsonify({"ok": False, "reason": "no_data",
                        "message": "최근 6개월 내 인사이더 거래가 없습니다."})

    return jsonify({
        "ok":           True,
        "ticker":       ticker,
        "transactions": payload["transactions"],
        "summary":      payload["summary"],
    })


# ── 오너십 지도 ────────────────────────────────────────────────────────────
_ownership_data_cache: dict = {}
_ownership_data_mtime: float = 0.0
_ownership_data_lock = threading.Lock()


def _load_ownership_data() -> dict:
    """ownership_data.json 을 mtime 기반으로 캐시 로드."""
    global _ownership_data_cache, _ownership_data_mtime
    path = os.path.join(os.path.dirname(__file__), "ownership_data.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    with _ownership_data_lock:
        if mt != _ownership_data_mtime or not _ownership_data_cache:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _ownership_data_cache = json.load(f)
                _ownership_data_mtime = mt
            except Exception as e:
                logging.warning("ownership_data load failed: %s", e)
                return _ownership_data_cache or {}
        return _ownership_data_cache


@app.route("/api/ownership/<ticker>")
def api_ownership(ticker: str):
    """GET /api/ownership/AAPL → 지분 구조(큐레이션 사전)."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    data = _load_ownership_data()
    if not data:
        return jsonify({"ok": False, "reason": "no_dict",
                        "message": "오너십 사전이 없습니다."})
    rec = data.get(ticker)
    if not rec:
        return jsonify({"ok": False, "reason": "not_found",
                        "message": "이 종목은 아직 오너십 사전에 없습니다."})
    breakdown = rec.get("breakdown") or []
    if not isinstance(breakdown, list) or not breakdown:
        return jsonify({"ok": False, "reason": "empty"})
    top = rec.get("top") or []
    return jsonify({
        "ok":         True,
        "ticker":     ticker,
        "asof":       rec.get("asof") or "",
        "source":     rec.get("source") or "",
        "confidence": rec.get("confidence") or "estimated",
        "breakdown":  breakdown,
        "top":        top if isinstance(top, list) else [],
    })


@app.route("/api/consensus/<ticker>")
def api_consensus(ticker: str):
    """??? ???? ??: ??? ??, ??/??, ?? ???."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    import urllib.request
    import json as _json

    market = (request.args.get("market") or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    cons_cache_key = f"{ticker}:{market}"
    now = int(time.time())
    with _consensus_cache_lock:
        cached = _consensus_cache.get(cons_cache_key)
        if cached and (now - cached.get("_ts", 0)) < _CONSENSUS_TTL_SEC:
            return jsonify(cached["data"])

    result = {"summary": {}, "reports": []}

    if market == "KR":
        code = ticker.split('.')[0].zfill(6)
        _ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        def _int(v):
            try: return int(str(v).replace(',', ''))
            except: return 0

        # 1) integration API ? ???? ?? (??/??/??/??)
        try:
            url = f"https://m.stock.naver.com/api/stock/{code}/integration"
            req = urllib.request.Request(url, headers=_ua)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode('utf-8'))
            ci = data.get('consensusInfo') or {}
            result["summary"] = {
                "mean":    _int(ci.get('priceTargetMean', '')),
                "high":    _int(ci.get('priceTargetHigh', '')),
                "low":     _int(ci.get('priceTargetLow', '')),
                "count":   _int(ci.get('targetPriceCount', '') or ci.get('consensusCount', '') or ci.get('stockFirmCount', '')),
                "opinion": ci.get('investmentOpinion', ''),
            }
        except Exception as e:
            logging.warning("consensus integration: %s", e)

        # 2) research API ? ?? ??? ???
        for ep in [
            f"https://m.stock.naver.com/api/stock/{code}/finance/research?pageSize=5",
            f"https://m.stock.naver.com/api/stock/{code}/research?pageSize=5",
        ]:
            try:
                req2 = urllib.request.Request(ep, headers=_ua)
                with urllib.request.urlopen(req2, timeout=5) as resp2:
                    rd = _json.loads(resp2.read().decode('utf-8'))
                items = rd if isinstance(rd, list) else (rd.get('list') or rd.get('reports') or rd.get('items') or [])
                for it in items[:5]:
                    firm = it.get('stockFirmName','') or it.get('brokerName','') or it.get('provider','')
                    tp   = it.get('priceTarget','') or it.get('targetPrice','')
                    if firm:
                        result["reports"].append({
                            'firm':    firm,
                            'target':  _int(tp),
                            'date':    it.get('date','') or it.get('writeDate',''),
                            'opinion': it.get('investmentOpinion','') or it.get('opinion',''),
                        })
                if result["reports"]:
                    break
            except Exception:
                continue

    else:  # US ? yfinance ??? (?? broker ???? ??)
        try:
            import yfinance as yf
            info = _run_with_timeout(
                lambda: yf.Ticker(ticker).info or {}, 8, f"consensus yf {ticker}"
            ) or {}
            def _flt(v):
                try: return float(v)
                except: return 0
            result["summary"] = {
                "mean":    _flt(info.get("targetMeanPrice", 0)),
                "high":    _flt(info.get("targetHighPrice", 0)),
                "low":     _flt(info.get("targetLowPrice", 0)),
                "count":   int(info.get("numberOfAnalystOpinions", 0) or 0),
                "opinion": info.get("recommendationKey", ""),
            }
        except Exception as e:
            logging.warning("yfinance consensus: %s", e)

    with _consensus_cache_lock:
        if len(_consensus_cache) >= _CONSENSUS_MAX:
            _consensus_cache.pop(next(iter(_consensus_cache)), None)
        _consensus_cache[cons_cache_key] = {"data": result, "_ts": int(time.time())}
    return jsonify(result)

@app.route("/api/regime/<ticker>")
def api_regime(ticker: str):
    """AgentQuant 기반 시장 레짐 + 진입 타이밍 시그널."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    try:
        from agentquant_signal import get_regime_signal
        payload = get_regime_signal(ticker, market=market)
        if not payload:
            return jsonify({"error": "신호를 계산할 수 없습니다."}), 404
        return jsonify(payload)
    except Exception as e:
        logging.exception("api_regime")
        return jsonify({"error": str(e)}), 500


def _downsample_closes(closes, max_points: int = 24):
    """스파크라인용 종가 배열을 max_points 이하로 균등 다운샘플. 최신가는 항상 보존."""
    vals = [float(c) for c in closes if c is not None]
    n = len(vals)
    if n == 0:
        return []
    if n <= max_points:
        return vals
    step = n / max_points
    out = [vals[int(i * step)] for i in range(max_points)]
    out[-1] = vals[-1]  # 마지막(최신) 값 보존
    return out


def _wk52_high_low(closes):
    """종가 배열의 (고가, 저가). 비면 (None, None)."""
    vals = [float(c) for c in closes if c is not None]
    if not vals:
        return (None, None)
    return (max(vals), min(vals))


def _compute_four_axis_payload(ticker: str, market: str, want_chart: bool = True) -> tuple:
    """yfinance + FourAxisAnalyzer + HandDrawnChartRenderer → (payload_dict|None, err_str|None).

    want_chart=False 이면 핸드드로잉 차트 렌더(+US 종목명 info 조회)를 생략한다 —
    드로어는 차트를 쓰지 않으므로 헛렌더링/네트워크를 피해 응답을 빠르게 한다.
    """
    try:
        import yfinance as yf
        _configure_yf_cache()
        from four_axis_analyzer import FourAxisAnalyzer
        from handdrawn_renderer import HandDrawnChartRenderer

        candidates = _build_yf_candidates(ticker, market)
        fetch_timeout_sec = _get_config_int("FOUR_AXIS_FETCH_TIMEOUT_SEC", 20, minimum=5, maximum=120)
        info_timeout_sec = _get_config_int("FOUR_AXIS_INFO_TIMEOUT_SEC", 8, minimum=3, maximum=60)
        min_rows = _get_config_int("FOUR_AXIS_MIN_ROWS", 20, minimum=10, maximum=252)

        hist = None
        tried = []
        periods = ("2y", "1y", "6mo", "3mo")
        for yt in candidates:
            rate_limited_break = False
            for period in periods:
                tried.append(f"{yt}({period})")
                try:
                    h = _run_with_timeout(
                        lambda yt=yt, period=period: yf.Ticker(yt).history(
                            period=period,
                            auto_adjust=True,
                            timeout=fetch_timeout_sec,
                        ),
                        fetch_timeout_sec,
                        f"four_axis history {yt} {period}",
                    )
                    if h is not None and not h.empty and len(h) >= min_rows:
                        hist = h
                        break
                except Exception as exc:
                    msg = str(exc).lower()
                    logging.warning("four_axis history fetch failed: %s", exc)
                    if "too many requests" in msg or "rate" in msg and "limit" in msg:
                        try:
                            time.sleep(1.0)
                            h = _run_with_timeout(
                                lambda yt=yt, period=period: yf.Ticker(yt).history(
                                    period=period,
                                    auto_adjust=True,
                                    timeout=fetch_timeout_sec,
                                ),
                                fetch_timeout_sec,
                                f"four_axis history {yt} {period} retry",
                            )
                            if h is not None and not h.empty and len(h) >= min_rows:
                                hist = h
                                break
                        except Exception as _e:
                            logging.debug("silent except (app.py): %s", _e)
                        rate_limited_break = True
                        break
                    continue
            if hist is not None:
                break

        if (hist is None or hist.empty or len(hist) < min_rows) and market == "KR":
            try:
                import FinanceDataReader as fdr
                from datetime import datetime, timedelta
                code6 = _strip_kr_suffix(ticker).zfill(6)
                start = (datetime.now() - timedelta(days=750)).strftime("%Y-%m-%d")
                tried.append(f"FDR:{code6}")
                fdr_df = _run_with_timeout(
                    lambda: fdr.DataReader(code6, start),
                    fetch_timeout_sec,
                    f"four_axis FDR {code6}",
                )
                if fdr_df is not None and not fdr_df.empty and len(fdr_df) >= min_rows:
                    keep = ["Open", "High", "Low", "Close", "Volume"]
                    hist = fdr_df[keep].copy()
            except Exception as exc:
                logging.warning("four_axis FDR fallback failed: %s", exc)

        if hist is None or hist.empty or len(hist) < min_rows:
            _rows_n = 0 if hist is None or hist.empty else len(hist)
            return None, f"데이터 부족 (시도: {', '.join(tried[:6])} / 확보: {_rows_n}일, 필요: {min_rows}일)"

        analyzer = FourAxisAnalyzer(hist, ticker)
        result = analyzer.analyze()

        chart_title = ""
        try:
            if market == "KR":
                code6 = _strip_kr_suffix(ticker).zfill(6)
                try:
                    from quant_nexus_v20 import QuantNexusApp
                    chart_title = (
                        QuantNexusApp.KR_NAMES.get(f"{code6}.KS")
                        or QuantNexusApp.KR_NAMES.get(f"{code6}.KQ")
                        or ""
                    )
                except Exception as _e:
                    logging.debug("silent except (app.py): %s", _e)
                try:
                    from swing_scan.config import stock_names as _sn
                    nm = _sn.get_name(code6)
                    if nm and nm != code6:
                        chart_title = str(nm)
                except Exception as _e:
                    logging.debug("silent except (app.py): %s", _e)
            if want_chart and not chart_title and market == "US":
                _us_chart_nm = getattr(QuantNexusApp, "US_NAMES", {}).get(ticker)
                if _us_chart_nm:
                    chart_title = _us_chart_nm
                else:
                    try:
                        yt0 = candidates[0] if candidates else ticker
                        yinfo = _run_with_timeout(
                            lambda yt0=yt0: yf.Ticker(yt0).info or {},
                            info_timeout_sec,
                            f"four_axis info {yt0}",
                        )
                        chart_title = (yinfo.get("longName") or yinfo.get("shortName") or "")
                    except Exception as _e:
                        logging.debug("silent except (app.py): %s", _e)
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
        chart_title = chart_title or ticker

        if want_chart:
            renderer = HandDrawnChartRenderer(
                hist, result, ticker=chart_title,
                width_px=1200, height_px=560, dpi=100,
            )
            img = renderer.render()

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            chart_b64 = base64.b64encode(buf.read()).decode("ascii")
        else:
            chart_b64 = None  # 드로어는 차트 미사용 — 핸드드로잉 렌더 생략(성능)

        import numpy as np

        def _sanitize_np(obj):
            if isinstance(obj, dict):
                return {k: _sanitize_np(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_sanitize_np(v) for v in obj]
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj

        rd = _sanitize_np(result.to_dict())
        payload = {
            "chart": chart_b64,
            "phase": rd["phase"],
            "signal_stars": rd["signal_stars"],
            "haiku": rd["haiku"],
            "trend": rd["trend"],
            "momentum": rd["momentum"],
            "volatility": rd["volatility"],
            "volume": rd["volume"],
            "key_observation": rd.get("key_observation", ""),
            "structured_analysis": rd.get("structured_analysis", ""),
        }

        # ── Hero 범위 차트용 경량 데이터 + 1일/52주 고저 ──
        try:
            import pandas as pd

            _closes_full = [float(x) for x in hist["Close"].dropna().tolist()]
            _high_src = hist["High"] if "High" in hist else hist["Close"]
            _low_src = hist["Low"] if "Low" in hist else hist["Close"]
            _vol_src = hist["Volume"] if "Volume" in hist else None
            _highs_full = [float(x) for x in _high_src.dropna().tolist()]
            _lows_full = [float(x) for x in _low_src.dropna().tolist()]
            _vols_full = [float(x) for x in _vol_src.dropna().tolist()] if _vol_src is not None else []
            _recent = _closes_full[-60:] if len(_closes_full) > 60 else _closes_full
            _current = _closes_full[-1] if _closes_full else None
            _day_high = _highs_full[-1] if _highs_full else _current
            _day_low = _lows_full[-1] if _lows_full else _current
            _day_volume = _vols_full[-1] if _vols_full else None
            _chart_hist = hist[["Open", "High", "Low", "Close", "Volume"]].copy()

            if market == "KR":
                try:
                    import toss_invest
                    if toss_invest.is_available():
                        _q = toss_invest.get_quote(ticker) or {}
                        _qp = _as_float(_q.get("price"))
                        if _qp and _qp > 0:
                            _current = _qp
                        _tc = toss_invest.get_daily_candles(ticker, count=260)
                        if _tc:
                            _last_c = _tc[-1]
                            _day_high = _as_float(_last_c.get("high")) or _day_high
                            _day_low = _as_float(_last_c.get("low")) or _day_low
                            _day_volume = _as_float(_last_c.get("volume")) or _day_volume
                        if len(_tc) >= 30:
                            _toss_rows = []
                            for _c in _tc:
                                _ts = pd.to_datetime(_c.get("timestamp"), errors="coerce")
                                if pd.isna(_ts):
                                    continue
                                _toss_rows.append({
                                    "Date": _ts,
                                    "Open": _as_float(_c.get("open")),
                                    "High": _as_float(_c.get("high")),
                                    "Low": _as_float(_c.get("low")),
                                    "Close": _as_float(_c.get("close")),
                                    "Volume": _as_float(_c.get("volume")) or 0,
                                })
                            if _toss_rows:
                                _chart_hist = pd.DataFrame(_toss_rows).set_index("Date").sort_index()
                                _closes_full = [float(x) for x in _chart_hist["Close"].dropna().tolist()]
                                _highs_full = [float(x) for x in _chart_hist["High"].dropna().tolist()]
                                _lows_full = [float(x) for x in _chart_hist["Low"].dropna().tolist()]
                                _vols_full = [float(x) for x in _chart_hist["Volume"].dropna().tolist()]
                                _recent = _closes_full[-60:] if len(_closes_full) > 60 else _closes_full
                except Exception as _e:
                    logging.debug("hero range Toss payload: %s", _e)

            if _current and _day_high:
                _day_high = max(float(_day_high), float(_current))
            if _current and _day_low:
                _day_low = min(float(_day_low), float(_current))

            # Toss 실시간가를 오늘 캔들에 반영해 상세 차트와 현재가를 맞춘다.
            if _current and _chart_hist is not None and not _chart_hist.empty:
                _last_idx = _chart_hist.index[-1]
                _chart_hist.loc[_last_idx, "Close"] = float(_current)
                _chart_hist.loc[_last_idx, "High"] = max(
                    float(_chart_hist.loc[_last_idx, "High"]), float(_current)
                )
                _chart_hist.loc[_last_idx, "Low"] = min(
                    float(_chart_hist.loc[_last_idx, "Low"]), float(_current)
                )
                _closes_full[-1:] = [float(_current)] if _closes_full else [float(_current)]
                if _highs_full:
                    _highs_full[-1] = max(float(_highs_full[-1]), float(_current))
                if _lows_full:
                    _lows_full[-1] = min(float(_lows_full[-1]), float(_current))
                _recent = _closes_full[-60:] if len(_closes_full) > 60 else _closes_full

            _chart_hist = _chart_hist.apply(pd.to_numeric, errors="coerce")
            try:
                from regime_classifier import get_market_regime
                from stock_judge import build_judgment
                _regime_state = get_market_regime("KR").state if market == "KR" else None
                payload["entry_judge"] = build_judgment(_chart_hist, _regime_state)
            except Exception as _e:
                logging.debug("entry judge payload: %s", _e)
            _chart_close = _chart_hist["Close"]
            for _period in (5, 20, 60, 120):
                _chart_hist[f"MA{_period}"] = _chart_close.rolling(_period).mean()
            def _chart_num(value):
                try:
                    value = float(value)
                    return round(value, 4) if np.isfinite(value) else None
                except (TypeError, ValueError):
                    return None

            _chart_rows = []
            for _idx, _row in _chart_hist.tail(90).iterrows():
                _dt = pd.to_datetime(_idx, errors="coerce")
                if pd.isna(_dt):
                    continue
                _chart_rows.append({
                    "time": _dt.strftime("%Y-%m-%d"),
                    "open": _chart_num(_row.get("Open")),
                    "high": _chart_num(_row.get("High")),
                    "low": _chart_num(_row.get("Low")),
                    "close": _chart_num(_row.get("Close")),
                    "volume": _chart_num(_row.get("Volume")) or 0,
                    "ma5": _chart_num(_row.get("MA5")),
                    "ma20": _chart_num(_row.get("MA20")),
                    "ma60": _chart_num(_row.get("MA60")),
                    "ma120": _chart_num(_row.get("MA120")),
                })

            payload["closes"] = _downsample_closes(_recent, max_points=24)
            payload["market_chart"] = _chart_rows
            _hi = max(_highs_full[-252:]) if _highs_full else None
            _lo = min(_lows_full[-252:]) if _lows_full else None
            payload["wk52_high"] = _hi
            payload["wk52_low"] = _lo
            payload["day_high"] = _day_high
            payload["day_low"] = _day_low
            payload["day_volume"] = _day_volume
            payload["range_current"] = _current
            payload["spark_change_pct"] = (
                round((_recent[-1] / _recent[0] - 1) * 100, 1)
                if len(_recent) >= 2 and _recent[0] else None
            )
        except Exception as _e:
            logging.debug("hero spark payload: %s", _e)

        # ── 과열/바닥 신호 ────────────────────────────────────────────
        try:
            from heat_signal import compute_heat_signal as _chs
            payload["heat_signal"] = _chs(hist, market)
        except Exception as _e:
            logging.debug("heat_signal: %s", _e)

        # ── 분할매수 가이드 + 포지션 리스크 데이터 ───────────────────
        try:
            _atr  = float(result.volatility.details.get("atr") or 0)
            _atp  = float(result.volatility.details.get("atr_pct") or 0)
            _curr = float((result.key_levels or {}).get("current") or 0)
            _sl   = float((result.risk or {}).get("stop_loss") or 0)
            _sp   = float((result.risk or {}).get("stop_pct") or 0)

            _lvls = []
            if _curr > 0 and _atr > 0:
                for _m, _r, _l in [(0, 30, "1차"), (1, 30, "2차"), (2, 25, "3차"), (3, 15, "4차")]:
                    _lvls.append({
                        "step": _m + 1, "label": _l,
                        "price": round(_curr - _m * _atr, 2),
                        "ratio": _r,
                        "from_pct": round(-_m * _atp, 1),
                    })
            payload["dca_plan"] = {
                "levels": _lvls, "atr_pct": round(_atp, 2), "current": _curr,
            }
            payload["position_data"] = {
                "current_price": _curr, "stop_loss": round(_sl, 2),
                "stop_pct": round(_sp, 2), "atr_pct": round(_atp, 2),
            }
        except Exception as _e:
            logging.debug("dca_plan/position_data: %s", _e)

        # ── RS Rating (개별 계산 + 스캔 캐시 퍼센타일 오버라이드) ─────────────
        try:
            import numpy as _np
            _c = hist["Close"]
            _n = len(_c)
            _r1  = (float(_c.iloc[-1]) / float(_c.iloc[-21])  - 1) if _n >= 21  else 0.0
            _r3  = (float(_c.iloc[-1]) / float(_c.iloc[-63])  - 1) if _n >= 63  else _r1
            _r6  = (float(_c.iloc[-1]) / float(_c.iloc[-126]) - 1) if _n >= 126 else _r3
            _r12 = (float(_c.iloc[-1]) / float(_c.iloc[-252]) - 1) if _n >= 252 else _r6
            _wret = _r1 * 0.25 + _r3 * 0.40 + _r6 * 0.20 + _r12 * 0.15
            if   _wret > 0.90: _rsr_calc = 99
            elif _wret > 0.60: _rsr_calc = 97
            elif _wret > 0.38: _rsr_calc = 93
            elif _wret > 0.25: _rsr_calc = 88
            elif _wret > 0.15: _rsr_calc = 82
            elif _wret > 0.09: _rsr_calc = 74
            elif _wret > 0.03: _rsr_calc = 62
            elif _wret > 0.00: _rsr_calc = 52
            elif _wret > -0.07: _rsr_calc = 38
            elif _wret > -0.18: _rsr_calc = 25
            else:               _rsr_calc = 12
            # 스캔 캐시에 퍼센타일 기반 RSRating이 있으면 우선 사용
            with _scan_results_cache_lock:
                _all_cached = list(_scan_results_cache.values())
            for _entry in _all_cached:
                for _row in (_entry.get("data") or []):
                    if str(_row.get("Ticker", "")).upper() == ticker.upper():
                        _v = _row.get("RSRating")
                        if isinstance(_v, (int, float)) and 1 <= _v <= 99:
                            _rsr_calc = int(_v)
                        break
            _rsr = _rsr_calc
            payload["rs_rating_data"] = {
                "rating": _rsr,
                "is_leader": _rsr >= 80,
                "label": (
                    "주도주" if _rsr >= 80 else
                    "준주도주" if _rsr >= 70 else
                    "중립" if _rsr >= 50 else
                    "약세" if _rsr >= 30 else
                    "하위권"
                ),
                "ret_pct": round(_wret * 100, 1),
                "r1_pct":  round(_r1  * 100, 1),
                "r3_pct":  round(_r3  * 100, 1),
                "r6_pct":  round(_r6  * 100, 1),
                "r12_pct": round(_r12 * 100, 1),
            }
        except Exception as _e:
            logging.debug("rs_rating_data: %s", _e)

        return payload, None
    except Exception as e:
        logging.debug("_compute_four_axis_payload %s/%s: %s", market, ticker, e)
        return None, str(e)


_four_axis_render_lock = threading.Lock()  # BG warm만 직렬화 — 유저 요청은 락 없이 즉시 실행


def _warm_four_axis(ticker: str, market: str, timeframe: str = "default") -> None:
    """BG 선제 4축 캐시 채우기 — 클릭(드로어) 시 차트+분석 즉시 표시.

    드로어가 핸드드로잉 차트를 표시하므로 c1(차트 포함) 페이로드를 워밍한다.
    """
    cache_key = f"{ticker}:{market}:{timeframe}:c1"
    with _four_axis_cache_lock:
        if cache_key in _four_axis_cache:
            return  # BG warm hit — move_to_end 호출 안 함 (cold entry가 hot으로 위장하는 것 방지)
    # 비블로킹 — 다른 BG warm이 실행 중이면 스킵 (유저 요청을 막지 않기 위해)
    if not _four_axis_render_lock.acquire(blocking=False):
        return
    try:
        with _four_axis_cache_lock:
            if cache_key in _four_axis_cache:
                return
        payload, err = _compute_four_axis_payload(ticker, market, want_chart=True)
        if payload:
            with _four_axis_cache_lock:
                if len(_four_axis_cache) >= _FOUR_AXIS_MAX:
                    _four_axis_cache.popitem(last=False)  # LRU eviction
                _four_axis_cache[cache_key] = {"data": payload, "_ts": int(time.time())}
            logging.info("4axis pre-warm: %s/%s OK", market, ticker)
        elif err:
            logging.debug("4axis pre-warm: %s/%s failed: %s", market, ticker, err)
    finally:
        _four_axis_render_lock.release()


_regime_cache: dict = {}
_regime_cache_lock = threading.Lock()


@app.route("/api/regime")
def api_market_regime():
    """현재 KOSPI 시장 레짐과 다음 상태 확률을 반환한다."""
    now = int(time.time())
    with _regime_cache_lock:
        if _regime_cache.get("_ts") and now - _regime_cache["_ts"] < 900:
            return jsonify(_regime_cache["data"])

    try:
        from regime_classifier import get_market_regime, R_BULL, R_BEAR, R_CHOP

        result = get_market_regime("KR")
        emojis = {R_BULL: "🟢", R_BEAR: "🔴", R_CHOP: "🟡"}
        labels = {R_BULL: "Bull", R_BEAR: "Bear", R_CHOP: "Chop"}
        descriptions = {R_BULL: "상승 추세", R_BEAR: "하락 추세", R_CHOP: "횡보"}
        state_codes = {R_BULL: "BULL", R_BEAR: "BEAR", R_CHOP: "CHOP"}
        signal = result.transition_signal or {}
        data = {
            "state": state_codes.get(result.state, "CHOP"),
            "raw_state": result.state,
            "emoji": emojis.get(result.state, "⚪"),
            "label": labels.get(result.state, result.state),
            "desc": descriptions.get(result.state, ""),
            "confidence": round(result.probs.get(result.state, 0.0), 3),
            "model": result.model_status,
            "p_next": {
                "bull": round(result.p_next.get(R_BULL, 0.0), 3),
                "bear": round(result.p_next.get(R_BEAR, 0.0), 3),
                "chop": round(result.p_next.get(R_CHOP, 0.0), 3),
            },
            "early_exit": bool(signal.get("early_exit", False)),
            "early_long": bool(signal.get("early_long", False)),
            "strength": round(float(signal.get("strength", 0.0)), 3),
        }
        with _regime_cache_lock:
            _regime_cache["data"] = data
            _regime_cache["_ts"] = now
        return jsonify(data)
    except Exception as exc:
        logging.warning("api_regime: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/four_axis/<ticker>")
def api_four_axis(ticker: str):
    """4축 핸드드로윙 차트 + 분석 데이터 반환 (base64 PNG)."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    # 상폐 블록리스트 — US 한정. yfinance 호출 자체 차단으로 로그 노이즈 제거.
    if market == "US":
        try:
            from symbol_alias import is_delisted as _is_delisted
            if _is_delisted(ticker):
                return jsonify({"error": f"상폐/리네임 티커: {ticker}"}), 404
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
        try:
            import quant_nexus_v20 as _qn_mod
            if getattr(_qn_mod, "_is_delisted_us", lambda _t: False)(ticker):
                return jsonify({"error": f"상폐/리네임 티커: {ticker}"}), 404
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
    timeframe = (request.args.get("timeframe") or "default").strip() or "default"
    want_chart = (request.args.get("chart", "1") != "0")  # 드로어는 chart=0 → 핸드드로잉 렌더 생략
    cache_key = f"{ticker}:{market}:{timeframe}:{'c1' if want_chart else 'c0'}"
    now = int(time.time())
    with _four_axis_cache_lock:
        cached = _four_axis_cache.get(cache_key)
        if cached and (now - cached.get("_ts", 0)) < _FOUR_AXIS_TTL_SEC:
            _four_axis_cache.move_to_end(cache_key)  # LRU bump — 유저 요청 hit만 hot으로 보존
            return jsonify(cached["data"])
    # 유저 요청은 락 없이 즉시 실행 — BG warm과 경쟁해도 Agg 백엔드에서 안전
    payload, err = _compute_four_axis_payload(ticker, market, want_chart=want_chart)
    if payload is None:
        return jsonify({"error": err or "생성 실패"}), (404 if "데이터 부족" in (err or "") else 500)
    with _four_axis_cache_lock:
        if len(_four_axis_cache) >= _FOUR_AXIS_MAX:
            _four_axis_cache.popitem(last=False)  # LRU eviction
        _four_axis_cache[cache_key] = {"data": payload, "_ts": int(time.time())}
    return jsonify(payload)


# ── 공시·뉴스 API (DART + Naver News) ─────────────────────────────────

@app.route("/api/dart-news/<ticker>")
def api_dart_news(ticker: str):
    """KR 종목 공시 목록 + 뉴스 감성분석 결합 반환."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    if market != "KR":
        return jsonify({"error": "KR 종목만 지원"}), 400

    code = ticker.split(".")[0].zfill(6)
    result = {"filings": [], "news": {}, "dart_available": False, "news_available": False}

    # 1) DART 공시
    try:
        import dart_api
        if dart_api.is_available():
            result["dart_available"] = True
            result["filings"] = dart_api.get_filings(code, count=10)
    except Exception as e:
        logging.warning("dart-news filings: %s", e)

    # 1-b) 실적 서프라이즈 (yfinance 경유)
    try:
        import event_calendar
        history = event_calendar.earnings_history(ticker, limit=4)
        if history:
            result["earnings_history"] = history
    except Exception as e:
        logging.warning("dart-news earnings_history: %s", e)

    # 2) Naver News 감성분석 — 종목명으로 검색
    try:
        import naver_news
        if naver_news.is_available():
            result["news_available"] = True
            # 종목명 가져오기
            stock_name = ""
            try:
                from swing_scan.config import stock_names as _sn
                stock_name = _sn.get_name(code)
                if stock_name == code:
                    stock_name = ""
            except Exception as _e:
                logging.debug("silent except (app.py): %s", _e)
            if not stock_name:
                stock_name = _lookup_kr_name_local(code)
            if not stock_name:
                try:
                    from quant_nexus_v20 import QuantNexusApp
                    stock_name = (
                        QuantNexusApp.KR_NAMES.get(f"{code}.KS")
                        or QuantNexusApp.KR_NAMES.get(f"{code}.KQ")
                        or ""
                    )
                except Exception as _e:
                    logging.debug("silent except (app.py): %s", _e)
            if not stock_name:
                try:
                    import dart_api as _da
                    s = _da.get_summary(code)
                    if s.get("available"):
                        data = s.get("data") or {}
                        stock_name = data.get("stock_name") or data.get("corp_name", "")
                except Exception as _e:
                    logging.debug("silent except (app.py): %s", _e)
            if stock_name:
                result["news"] = naver_news.summarize(stock_name, limit=15)
    except Exception as e:
        logging.warning("dart-news sentiment: %s", e)

    return jsonify(result)


# ── US 인사이트 API (yfinance + SEC EDGAR) ────────────────────────────

_sec_cik_map: dict = {}  # ticker → CIK 캐시


def _load_sec_cik_map() -> dict:
    """SEC company_tickers.json에서 ticker→CIK 매핑을 다운로드(첫 호출 시 1회)."""
    global _sec_cik_map
    if _sec_cik_map:
        return _sec_cik_map
    import json as _json
    import gzip as _gzip
    url = "https://www.sec.gov/files/company_tickers.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "StockScanner admin@example.com",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
            if raw[:2] == b'\x1f\x8b':
                raw = _gzip.decompress(raw)
            data = _json.loads(raw.decode("utf-8"))
        for entry in data.values():
            tk = str(entry.get("ticker", "")).upper()
            cik = entry.get("cik_str", "")
            if tk and cik:
                _sec_cik_map[tk] = str(cik).zfill(10)
    except Exception as e:
        logging.warning("SEC CIK map load failed: %s", e)
    return _sec_cik_map


def _get_sec_filings(ticker: str, count: int = 10) -> list:
    """SEC EDGAR에서 최근 공시(10-K/10-Q/8-K) 조회."""
    import json as _json
    cik_map = _load_sec_cik_map()
    cik = cik_map.get(ticker.upper())
    if not cik:
        return []
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    req = urllib.request.Request(url, headers={
        "User-Agent": "StockScanner admin@example.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read().decode("utf-8"))
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        names = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])
        results = []
        target_forms = {"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A"}
        for i in range(len(forms)):
            if forms[i] not in target_forms:
                continue
            acc = accessions[i].replace("-", "") if i < len(accessions) else ""
            doc = names[i] if i < len(names) else ""
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{acc}/{doc}" if acc and doc else ""
            results.append({
                "form": forms[i],
                "date": dates[i] if i < len(dates) else "",
                "description": descriptions[i] if i < len(descriptions) else forms[i],
                "url": filing_url,
            })
            if len(results) >= count:
                break
        return results
    except Exception as e:
        logging.warning("SEC filings fetch: %s", e)
        return []


@app.route("/api/us-insight/<ticker>")
def api_us_insight(ticker: str):
    """US 종목 인사이트: 뉴스 감성 + 기관보유/공매도 + 어닝캘린더 + SEC 공시."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    return jsonify({"error": "US market is disabled; KR only"}), 410

    result = {
        "news": {}, "holders": {}, "earnings": {},
        "recommendations": [], "sec_filings": [],
        "news_available": False, "holders_available": False,
        "earnings_available": False, "sec_available": False,
    }

    # 1) Yahoo Finance 뉴스 감성분석
    try:
        import news_summarizer
        news_data = news_summarizer.summarize(ticker, limit=10)
        if news_data.get("count", 0) > 0:
            result["news_available"] = True
        result["news"] = news_data
    except Exception as e:
        logging.warning("us-insight news: %s", e)

    # 2) 기관보유 + 공매도
    try:
        import yfinance as yf
        _t_insight = yf.Ticker(ticker)
        info = _run_with_timeout(lambda: _t_insight.info or {}, 5, "us_insight_info") or {}
        holders = {
            "institutional_pct": info.get("heldPercentInstitutions"),
            "insider_pct": info.get("heldPercentInsiders"),
            "short_pct": info.get("shortPercentOfFloat"),
            "short_ratio": info.get("shortRatio"),
        }
        if any(v is not None for v in holders.values()):
            result["holders_available"] = True
            # float 변환
            for k, v in holders.items():
                if v is not None:
                    try:
                        holders[k] = round(float(v), 4)
                    except (TypeError, ValueError):
                        holders[k] = None
        result["holders"] = holders

        # 3) 애널리스트 추천 이력 — 증권사 목표가와 동일 출처(단일 진실원천).
        #    analyst_consensus 가 증권사당 1건·최근성 정렬한 집합을 돌려주므로
        #    여기 리스트와 헤드라인 '증권사 목표가 평균'이 항상 일치한다.
        try:
            import analyst_consensus
            _upgrades = _run_with_timeout(lambda: _t_insight.upgrades_downgrades, 5, "us_insight_upgrades")
            _cons = analyst_consensus.summarize_upgrades_downgrades(_upgrades)
            if _cons["rows"]:
                result["recommendations"] = _cons["rows"]
        except Exception as _e:
            logging.debug("silent except (app.py): %s", _e)
    except Exception as e:
        logging.warning("us-insight holders: %s", e)

    # 4) 어닝 캘린더
    try:
        import event_calendar
        dday, date_str = event_calendar.earnings_dday(ticker)
        if dday is not None:
            result["earnings_available"] = True
            chip = event_calendar.build_dday_chip(dday)
            result["earnings"] = {
                "dday": dday, "date": date_str,
                "chip_text": chip["text"], "chip_fg": chip["fg"],
                "chip_bg": chip["bg"], "chip_show": chip["show"],
            }
        # 과거 실적 서프라이즈 히스토리
        history = event_calendar.earnings_history(ticker, limit=4)
        if history:
            result["earnings_available"] = True
            result["earnings_history"] = history
    except Exception as e:
        logging.warning("us-insight earnings: %s", e)

    # 5) SEC EDGAR 공시
    try:
        filings = _get_sec_filings(ticker, count=10)
        if filings:
            result["sec_available"] = True
            result["sec_filings"] = filings
    except Exception as e:
        logging.warning("us-insight sec: %s", e)

    return jsonify(result)


@app.route("/api/score-history/<ticker>")
def api_score_history(ticker: str):
    """최근 N일간 TotalScore + 순위 히스토리 (snapshots/ JSON 파일 기반)."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    market = (request.args.get("market") or "KR").upper()
    if market not in _SUPPORTED_MARKETS:
        return jsonify({"error": "US market is disabled; KR only"}), 410
    days = min(int(request.args.get("days") or 30), 90)
    import history as hist_mod
    from datetime import date, timedelta
    today = date.today()
    points = []
    for back in range(days, -1, -1):
        d = today - timedelta(days=back)
        snap = hist_mod._load(market, d)  # noqa: SLF001
        if snap and ticker in snap:
            entry = snap[ticker]
            points.append({
                "date": d.isoformat(),
                "score": entry.get("score"),
                "rank": entry.get("rank"),
            })
    return jsonify({"ticker": ticker, "market": market, "points": points})





# SocketIO 초기화 (gunicorn / 직접 실행 모두 대응)
socketio.init_app(app)

# KR 캐시 워밍 시작 (gunicorn import 시점에도 트리거; file-lock으로 중복 방지)
try:
    _start_kr_warmup_once()
except Exception as _e:
    logging.warning("KR warm-up bootstrap failed: %s", _e)

# 검색 인덱스 사전 빌드 (백그라운드) — 첫 검색 요청 시 지연 제거
def _warmup_search_index():
    try:
        _get_search_idx("KR")
        logging.info("[search-idx] pre-built KR=%d", len(_SEARCH_IDX.get("KR", [])))
    except Exception as _e:
        logging.warning("search index warmup failed: %s", _e)



def _warmup_moat_cache():
    try:
        import moat
        n = moat.preload_cache()
        logging.info("[moat-cache] preloaded %d entries into memory", n)
    except Exception as _e:
        logging.warning("moat cache preload failed: %s", _e)
    # speculative_themes도 미리 로드해 첫 스캔 import 지연 제거
    try:
        import speculative_themes  # noqa: F401
    except Exception:
        pass

# search-idx + moat-cache는 _cold_start_fill 완료 후 단계적으로 실행됨


def _cold_start_live_scan(market: str) -> None:
    """pickle이 없어 quick-warm으로도 캐시를 못 채울 때 BG에서 live scan_all 1회 수행.
    UI는 그 사이 빈 화면을 보지만 30분 warm 루프를 기다리진 않게 된다."""
    try:
        adapter_cls = _get_scan_adapter_cls()
        adapter = adapter_cls(market=market, strategy="BALANCED")
        results = adapter.scan_all(prefer_cache=False, cache_only=False, max_workers=16)
        if not results:
            return
        try:
            results = _annotate_one_liners(results)
        except Exception:
            pass
        _apply_moat_bonus(results)
        results = _attach_scan_deltas(results, market, adapter=adapter, save_snapshot=True)
        results = _strip_heavy(results)
        ts = int(time.time())
        _store_scan_cache((market, "BALANCED", ""), ts, results)
        _populate_sector_caches(market, "BALANCED", results, ts)
        logging.info("cold-start live scan done: %s %d tickers", market, len(results))
    except Exception as _e:
        logging.warning("cold-start live scan %s failed: %s", market, _e)


def _cold_start_fill():
    """서버 기동 직후 파일 잠금 없이 KR 캐시를 즉시 채운다.
    파일 잠금 실패로 quick-warm이 건너뛰어질 때도 첫 요청이 캐시 히트하도록 보장.

    Phase 1: 단일 스냅샷 파일(_scan_snapshot.pkl)에서 즉시 복원 → 수 초 이내 API 응답 가능.
    Phase 2: 개별 pickle에서 최신 데이터로 BG 갱신 (스냅샷 히트 시 join 없이 비동기).
    Phase 3: pickle도 없으면 라이브 scan_all BG 트리거.
    """
    time.sleep(0.1)  # Flask/SocketIO 초기화 완료 대기

    # Phase 1: 스냅샷 즉시 복원 (단일 파일 → 수 초 이내)
    _snapshot_hit = _load_scan_snapshot()
    if _snapshot_hit:
        _warmup_search_index()
        _warmup_moat_cache()

    def _fill_market(market: str) -> None:
        try:
            with _scan_results_cache_lock:
                _already = _scan_results_cache.get((market, "BALANCED", ""))
            if not _already:
                _warmup_fill_cache(market)
            with _scan_results_cache_lock:
                _filled = _scan_results_cache.get((market, "BALANCED", ""))
            if not _filled:
                logging.info("cold-start: %s pickle empty → live scan kicked", market)
                threading.Thread(
                    target=_cold_start_live_scan,
                    args=(market,),
                    daemon=True,
                    name=f"cold-live-{market}",
                ).start()
        except Exception as _e:
            logging.warning("cold-start-fill %s failed: %s", market, _e)

    threads = [
        threading.Thread(target=_fill_market, args=(m,), daemon=True, name=f"cold-fill-{m}")
        for m in ("KR",)
    ]
    for t in threads:
        t.start()

    if _snapshot_hit:
        # 스냅샷으로 이미 캐시 채워짐 → BG 갱신은 join 없이 비동기 진행
        return

    # 스냅샷 없음 → 기존 방식 (pickle 읽기 완료까지 대기 + 스냅샷 저장)
    for t in threads:
        t.join()
    _save_scan_snapshot()
    _warmup_search_index()
    _warmup_moat_cache()


@app.route("/api/judge/<ticker>")
def api_judge(ticker: str):
    """GET /api/judge/005930.KS  → 진입 여건 시나리오 분류 (stock_judge)"""
    safe = _validate_ticker(ticker)
    if not safe:
        return jsonify({"error": "invalid ticker"}), 400

    _cache_key = f"judge:{safe}"
    _now = int(time.time())
    with _ticker_detail_cache_lock:
        _hit = _ticker_detail_cache.get(_cache_key)
        if _hit and (_now - _hit.get("_ts", 0)) < 300:   # 5분 TTL
            return jsonify(_hit["data"])

    try:
        import sys as _sys
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from stock_judge import compute_technicals, classify, apply_regime, SCENARIOS

        tc = compute_technicals(safe)
        scenario, reasons = classify(tc)

        regime_state = None
        try:
            from regime_classifier import get_market_regime
            regime_state = get_market_regime("KR").state
        except Exception:
            pass

        scenario, _ = apply_regime(scenario, regime_state, tc)
        info = SCENARIOS[scenario]

        result = {
            "ticker":   safe,
            "scenario": scenario,
            "label":    info["label"],
            "color":    info["color"],
            "timing":   info["timing"],
            "premark":  info["premark"],
            "risk":     info["risk"],
            "reasons":  reasons,
            "technicals": {
                "ma20_dev":  round(tc["ma20_dev"], 4),
                "ma60_dev":  round(tc["ma60_dev"], 4) if tc["ma60_dev"] and not (tc["ma60_dev"] != tc["ma60_dev"]) else None,
                "rsi":       round(tc["rsi"], 1),
                "rvol":      round(tc["rvol"], 3),
                "vol_trend": round(tc["vol_trend"], 3),
                "chg5d":     round(tc["chg5d"], 4) if tc["chg5d"] and not (tc["chg5d"] != tc["chg5d"]) else None,
                "dd60":      round(tc["dd60"], 4),
                "streak_up": tc["streak_up"],
            },
        }
        with _ticker_detail_cache_lock:
            _ticker_detail_cache[_cache_key] = {"data": result, "_ts": _now}
        return jsonify(result)

    except Exception as e:
        logging.warning("api_judge [%s]: %s", safe, e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/serenity/<ticker>")
def api_serenity(ticker: str):
    """Serenity (@aleabitoreddit) 인사이트 조회."""
    ticker = _validate_ticker(ticker)
    if not ticker:
        return jsonify({"error": "invalid ticker"}), 400
    try:
        from web_app.serenity import get_serenity_insight
        result = get_serenity_insight(ticker)
        if result:
            return jsonify(result)
        return jsonify({"error": "not_covered"}), 404
    except Exception as e:
        logging.warning("api_serenity: %s", e)
        return jsonify({"error": str(e)}), 500


threading.Thread(target=_cold_start_fill, daemon=True, name="cold-start-fill").start()

if __name__ == "__main__":
    debug = (os.environ.get("FLASK_DEBUG") or "0").strip().lower() in ("1", "true", "yes")
    host = (os.environ.get("FLASK_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port_raw = (os.environ.get("PORT") or os.environ.get("FLASK_PORT") or "5000").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 5000
    # allow_unsafe_werkzeug: dev runner(werkzeug)에서 SocketIO 스레드 호환 필요.
    # PRODUCTION=1 환경에서는 gunicorn이 기동하므로 이 분기 자체가 실행되지 않음.
    is_production = os.environ.get("PRODUCTION", "").strip() in ("1", "true", "yes")
    try:
        socketio.run(app, debug=debug, port=port, host=host, allow_unsafe_werkzeug=not is_production)
    except OSError as e:
        # 포트 충돌(WinError 10048 / EADDRINUSE)을 트레이스백 대신 친절 메시지로 처리.
        # launcher가 사전 체크하지만 race condition / 직접 실행 경로에서 발생할 수 있다.
        win_err = getattr(e, "winerror", None)
        if win_err == 10048 or e.errno in (98, 48, 10048):
            print(f"[app] 포트 {port}이 이미 사용 중입니다. 기존 인스턴스가 실행 중이거나",
                  "다른 프로그램이 점유 중입니다.", file=sys.stderr)
            print(f"[app] 다른 포트로 띄우려면: set PORT=5001 && python web_app/app.py",
                  file=sys.stderr)
            sys.exit(2)
        raise
