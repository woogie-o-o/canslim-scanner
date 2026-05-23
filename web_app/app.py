"""
app.py — (.)(.)분석기 Flask 웹 서버
engine_adapter.ScanAdapter를 JSON API로 서빙하고 HTML 템플릿을 렌더링한다.

실행: python web_app/app.py
접속: http://localhost:5000
"""
import sys
import os
import io
import base64
import json
import html
import logging
import subprocess
import threading
import queue
import time
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))

# 프로젝트 루트 경로 (four_axis_analyzer, handdrawn_renderer 접근용)
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from flask import Flask, request, jsonify, render_template, Response
from chat import socketio
from config_manager import (
    SETTINGS_SCHEMA, load_config, save_config,
    apply_to_environ, get_masked, get_connection_status,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 앱 시작 시 저장된 설정을 환경변수에 반영
apply_to_environ()

# (섹터 히트/핫 섹터 기능 제거됨 — yfinance rate-limit 부담 ↓, UI 정리)

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)


def _render_deployment() -> bool:
    return bool((os.environ.get("RENDER") or "").strip())



# ── 4축 차트 / 컨센서스 캐시 (성능 최적화) ──
_FOUR_AXIS_TTL_SEC = 1800  # 30분
_FOUR_AXIS_MAX = 200
_four_axis_cache: dict[str, dict] = {}
_four_axis_cache_lock = threading.Lock()

_CONSENSUS_TTL_SEC = 900  # 15분
_CONSENSUS_MAX = 200
_consensus_cache: dict[str, dict] = {}
_consensus_cache_lock = threading.Lock()

# ── 티커 상세 응답 캐시 (드로어 재오픈 시 즉시 응답) ──
_TICKER_DETAIL_TTL_SEC = 300  # 5분
_TICKER_DETAIL_MAX = 200
_ticker_detail_cache: dict[str, dict] = {}
_ticker_detail_cache_lock = threading.Lock()

_scan_refresh_lock = threading.Lock()
_scan_refresh_inflight: set[tuple[str, str, str]] = set()

# ── 스캔 결과 전체 캐시 (API 레벨, pickle 재읽기 방지) ──
_SCAN_RESULTS_TTL_SEC = 300  # 5분
_scan_results_cache: dict[tuple[str, str, str], dict] = {}
_scan_results_cache_lock = threading.Lock()


def _configure_yf_cache() -> None:
    try:
        import yfinance as yf
        cache_dir = os.path.join(_BASE, ".yfinance-cache")
        os.makedirs(cache_dir, exist_ok=True)
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location(cache_dir)
    except Exception as e:
        logging.warning("yfinance cache init failed: %s", e)


def _build_yf_candidates(ticker: str, market: str) -> list[str]:
    raw = (ticker or "").strip()
    market = (market or "US").upper()

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
        if kept_suf in (".KS", ".KQ"):
            other = ".KQ" if kept_suf == ".KS" else ".KS"
            candidates = [f"{t6}{kept_suf}", f"{t6}{other}"]
        else:
            candidates = [f"{t6}.KS", f"{t6}.KQ"]

    seen = set()
    return [c for c in candidates if c and not (c in seen or seen.add(c))]


def _get_scan_adapter_cls():
    from engine_adapter import ScanAdapter
    return ScanAdapter


def _annotate_one_liners(results: list):
    from one_liner import annotate
    return annotate(results)


def _render_static_template(name: str, replacements: dict[str, str] | None = None) -> Response:
    path = os.path.join(os.path.dirname(__file__), "templates", name)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for src, dst in (replacements or {}).items():
        content = content.replace(src, dst)
    return Response(content, mimetype="text/html; charset=utf-8")


_adapter_pool: dict[tuple[str, str], object] = {}
_adapter_pool_lock = threading.Lock()
_ADAPTER_POOL_MAX = 4


def _make_adapter():
    market   = request.args.get("market",   "US")
    strategy = request.args.get("strategy", "BALANCED")
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
            results = adapter.scan_sector(sector) if sector else adapter.scan_all()
            try:
                import history
                results = history.annotate_deltas(results, market)
                if not sector:
                    history.save_snapshot(results, market)
            except Exception as he:
                logging.warning("background history annotate/save failed: %s", he)
            try:
                results = _annotate_one_liners(results)
            except Exception as oe:
                logging.warning("background one_liner annotate failed: %s", oe)
            # 스캔 결과 전체 캐시 갱신
            if results:
                with _scan_results_cache_lock:
                    _scan_results_cache[(market, strategy, sector)] = {
                        "_ts": int(time.time()), "data": results,
                    }
        except Exception as e:
            logging.warning("background scan refresh failed: %s", e)
        finally:
            with _scan_refresh_lock:
                _scan_refresh_inflight.discard(key)

    threading.Thread(target=_worker, daemon=True).start()


# ── 캐시 메타데이터 (stale-data UX 헤더용) ─────────────────────────────────
def _scan_cache_meta(market: str) -> tuple[int | None, str | None]:
    """cache_v19/ 디렉토리에서 해당 market 캐시 파일들의 최고령(가장 오래된) 분 + 가장 최신 mtime ISO 반환.
    실패/없음 시 (None, None).
    """
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
        for fn in os.listdir(cache_dir):
            if not fn.endswith(".pkl"):
                continue
            if market == "KR":
                if not any(p in fn for p in patterns):
                    continue
            elif market == "US":
                if any(p in fn for p in ("_KS__", "_KQ__")):
                    continue
            try:
                mt = os.path.getmtime(os.path.join(cache_dir, fn))
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
        return (cache_age_min, as_of_iso)
    except Exception:
        return (None, None)


# ── KR 캐시 워밍 (서버 기동 시 + 30분 주기, multi-process safe) ────────────
_KR_WARMUP_LOCK_PATH = os.path.join(_BASE, "cache_v19", ".warmer.lock")
_kr_warmup_started = False
_kr_warmup_lock = threading.Lock()


def _acquire_warmer_file_lock():
    """non-blocking 파일 잠금 획득. 성공 시 (file_handle, 'win'|'posix'), 실패 시 None.
    반환된 핸들은 워밍 종료까지 open 상태 유지 필요 (finally에서 release+close)."""
    try:
        os.makedirs(os.path.dirname(_KR_WARMUP_LOCK_PATH), exist_ok=True)
        fh = open(_KR_WARMUP_LOCK_PATH, "a+b")
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
        except Exception:
            pass
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
        except Exception:
            pass


def _kr_warmup_loop(interval_sec: int = 1800) -> None:
    """KR 전체 스캔을 주기적으로 BG 실행. 파일잠금으로 multi-process duplication 방지."""
    while True:
        handle = _acquire_warmer_file_lock()
        if handle is None:
            logging.info("KR warm-up skipped: another worker holds lock")
        else:
            try:
                logging.info("KR warm-up started")
                try:
                    adapter_cls = _get_scan_adapter_cls()
                    adapter = adapter_cls(market="KR", strategy="BALANCED")
                    # 워머는 Semaphore(4)로 throttle: scan_all max_workers=4 로 호출
                    results = adapter.scan_all(max_workers=4)
                    logging.info("KR warm-up done: %d tickers", len(results) if results else 0)
                    # 스캔 결과 전체 캐시 갱신
                    if results:
                        try:
                            results = _annotate_one_liners(results)
                        except Exception:
                            pass
                        with _scan_results_cache_lock:
                            _scan_results_cache[("KR", "BALANCED", "")] = {
                                "_ts": int(time.time()), "data": results,
                            }
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
    threading.Thread(target=_kr_warmup_loop, daemon=True, name="kr-warmup").start()


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
    })


@app.route("/detail/<ticker>")
def detail(ticker: str):
    import re as _re
    ticker = _re.sub(r"[^A-Za-z0-9.\-]", "", ticker)
    if not ticker:
        return "Invalid ticker", 400
    safe_ticker = html.escape(ticker, quote=True)
    return _render_static_template("detail.html", {
        "{{ ticker }}": safe_ticker,
    })


@app.route("/compare")
def compare_page():
    raw = request.args.get("tickers", "")
    tickers = [tk.strip() for tk in raw.split(",") if tk.strip()][:4]
    market = (request.args.get("market") or "US").upper()
    return _render_static_template("compare.html", {
        "{{ tickers|tojson }}": json.dumps(tickers, ensure_ascii=False),
        "{{ market|tojson }}": json.dumps(market, ensure_ascii=False),
    })


@app.route("/settings")
def settings_page():
    return _render_static_template("settings.html", {
        "{{ schema | tojson }}": json.dumps(SETTINGS_SCHEMA, ensure_ascii=False),
    })


# ── 설정 API ─────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """현재 저장된 설정 조회 (민감값 마스킹)."""
    data = load_config()
    return jsonify({
        "values": data,
        "values_masked": get_masked(data),
        "status": get_connection_status(data),
        "schema": SETTINGS_SCHEMA,
    })


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    """?? ?? ? ????? ?? ??."""
    try:
        incoming = request.get_json(force=True) or {}
        existing = load_config()
        for key, value in incoming.items():
            if value is not None and value != "":
                existing[key] = value
        save_config(existing)
        apply_to_environ(existing)
        return jsonify({"ok": True})
    except Exception as e:
        logging.exception("api_settings_post")
        return jsonify({"ok": False, "error": str(e)}), 500



@app.route("/api/settings", methods=["DELETE"])
def api_settings_delete():
    """?? ???."""
    try:
        save_config({})
        apply_to_environ({})
        return jsonify({"ok": True})
    except Exception as e:
        logging.exception("api_settings_delete")
        return jsonify({"ok": False, "error": str(e)}), 500


# ?? JSON API ?????????????????????????????????????????????????????????????

@app.route("/api/sectors")
def api_sectors():
    """GET /api/sectors?market=US ? {"sectors": {???: [ticker, ...]}}"""
    try:
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
    """GET /api/scan?market=US&strategy=BALANCED&sector=SaaS → [{...}, ...]"""
    try:
        sector  = request.args.get("sector", "")
        market  = (request.args.get("market") or "US").upper()
        strategy = (request.args.get("strategy") or "BALANCED").upper()
        # 기본 0 — 32-bit Python 환경 안정성 우선. ?aq_top=10 또는 env AQ_SCAN_TOP 로 활성화.
        try:
            aq_top = int(request.args.get("aq_top", os.environ.get("AQ_SCAN_TOP", "0")))
        except (TypeError, ValueError):
            aq_top = 0

        # ── 스캔 결과 전체 캐시 조회 (pickle 재읽기 완전 회피) ──
        _sr_key = (market, strategy, sector)
        _sr_now = int(time.time())
        with _scan_results_cache_lock:
            _sr_cached = _scan_results_cache.get(_sr_key)
        if _sr_cached and (_sr_now - _sr_cached.get("_ts", 0)) < _SCAN_RESULTS_TTL_SEC:
            _refresh_scan_background(market, strategy, sector)
            resp = jsonify(_sr_cached["data"])
            try:
                cache_age_min, as_of_iso = _scan_cache_meta(market)
                if cache_age_min is not None:
                    resp.headers["X-Cache-Age-Min"] = str(cache_age_min)
                if as_of_iso:
                    resp.headers["X-As-Of"] = as_of_iso
                resp.headers["X-Warming-In-Progress"] = "false"
            except Exception:
                pass
            return resp

        adapter = _make_adapter()
        results = []
        warming_in_progress = False
        if market in ("US", "KR"):
            results = adapter.scan_sector(sector, prefer_cache=True, cache_only=True) if sector else adapter.scan_all(prefer_cache=True, cache_only=True)
            if results:
                _refresh_scan_background(market, strategy, sector)
            elif market == "US":
                # US는 캐시 미스 시 동기 풀 스캔 fallback
                results = adapter.scan_sector(sector) if sector else adapter.scan_all()
            else:
                # KR: 첫 배포·캐시 전체 미스 → BG 워밍만 트리거하고 빈 응답 반환
                _refresh_scan_background(market, strategy, sector)
                warming_in_progress = True
        else:
            results = adapter.scan_sector(sector) if sector else adapter.scan_all()
        # 히스토리 델타 주석/스냅샷 저장
        try:
            import history
            results = history.annotate_deltas(results, market)
            # 섹터 스캔이 아닐 때만 스냅샷 저장 (전체 스캔만 저장)
            if not sector:
                history.save_snapshot(results, market)
        except Exception as he:
            logging.warning("history annotate/save failed: %s", he)
        # 촌철살인 한줄평 추가
        try:
            results = _annotate_one_liners(results)
        except Exception as oe:
            logging.warning("one_liner annotate failed: %s", oe)
        # AgentQuant 융합 (상위 N개)
        if aq_top > 0:
            try:
                results = _apply_aq_fusion(results, market, top_n=aq_top)
            except Exception as ae:
                logging.warning("aq fusion failed: %s", ae)
        # ── 스캔 결과 전체 캐시 저장 ──
        if results:
            with _scan_results_cache_lock:
                _scan_results_cache[_sr_key] = {"_ts": _sr_now, "data": results}
        # Stale-data UX 헤더 (non-breaking: 본문은 array 유지)
        resp = jsonify(results)
        try:
            cache_age_min, as_of_iso = _scan_cache_meta(market)
            if cache_age_min is not None:
                resp.headers["X-Cache-Age-Min"] = str(cache_age_min)
            if as_of_iso:
                resp.headers["X-As-Of"] = as_of_iso
            resp.headers["X-Warming-In-Progress"] = "true" if warming_in_progress else "false"
        except Exception:
            pass
        return resp
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


@app.route("/api/search")
def api_search():
    """GET /api/search?q=rf&market=KR → [{ticker, name}, ...] 이름/티커 부분매칭."""
    q = (request.args.get("q") or "").strip().lower()
    market = (request.args.get("market") or "US").upper()
    if not q or len(q) < 1:
        return jsonify([])
    hits = []
    try:
        from quant_nexus_v20 import QuantNexusApp
        if market == "KR":
            for tk, nm in QuantNexusApp.KR_NAMES.items():
                code = tk.split(".")[0]
                if q in nm.lower() or q in tk.lower() or q in code.lower():
                    hits.append({"ticker": tk, "name": nm})
        else:
            try:
                from us_company_info import US_COMPANY_INFO
            except Exception:
                US_COMPANY_INFO = {}
            for tk, desc in US_COMPANY_INFO.items():
                if q in tk.lower() or q in desc.lower():
                    hits.append({"ticker": tk, "name": desc})
    except Exception as e:
        logging.warning("api_search failed: %s", e)
    hits.sort(key=lambda h: (not h["ticker"].lower().startswith(q), not h["name"].lower().startswith(q), h["name"]))
    return jsonify(hits[:15])


@app.route("/api/ticker/<ticker>")
def api_ticker(ticker: str):
    """GET /api/ticker/AAPL?market=US&strategy=BALANCED → {Ticker, TotalScore, ...}"""
    market_arg = (request.args.get("market") or "US").upper()
    strategy_arg = request.args.get("strategy", "BALANCED")
    # ── 응답 캐시 조회 (동일 종목 재오픈 시 즉시 반환) ──
    _td_key = f"{ticker}:{market_arg}:{strategy_arg}"
    _td_now = int(time.time())
    with _ticker_detail_cache_lock:
        _td_cached = _ticker_detail_cache.get(_td_key)
        if _td_cached and (_td_now - _td_cached.get("_ts", 0)) < _TICKER_DETAIL_TTL_SEC:
            # 한줄평은 항상 최신 로직으로 재생성 (캐시는 raw 데이터만 재사용)
            try:
                fresh = _annotate_one_liners([_td_cached["data"]])[0]
            except Exception:
                fresh = _td_cached["data"]
            return jsonify(fresh)
    try:
        adapter = _make_adapter()
        result  = adapter.analyze_ticker(ticker, prefer_cache=True)
        market = market_arg
        if result is None:
            return jsonify({"error": "해당 티커의 데이터를 찾을 수 없습니다."}), 404
        if market == "KR":
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
                except Exception:
                    pass
            # 네이버 금융 투자자 매매동향 (외인/기관 순매수, 단위: 주)
            try:
                from naver_finance import get_investor_flow
                inv = get_investor_flow(code6)
                if inv.get("rows"):
                    result["_Investor_Foreign"] = int(inv.get("foreign_net_latest") or 0)
                    result["_Investor_Institution"] = int(inv.get("inst_net_latest") or 0)
                    result["_Investor_Available"] = True
            except Exception as ne:
                logging.debug("naver investor flow failed for %s: %s", ticker, ne)
        else:
            # US 종목: yfinance 수급/센티먼트 데이터
            try:
                import yfinance as yf
                yf_info = yf.Ticker(ticker).info
                short_pct = yf_info.get("shortPercentOfFloat")
                inst_pct = yf_info.get("heldPercentInstitutions")
                rec_mean = yf_info.get("recommendationMean")
                target_mean = yf_info.get("targetMeanPrice")
                cur_price = yf_info.get("currentPrice")
                n_analysts = yf_info.get("numberOfAnalystOpinions")
                if short_pct is not None:
                    result["_YF_ShortPctFloat"] = round(short_pct * 100, 2)
                if inst_pct is not None:
                    result["_YF_InstPct"] = round(inst_pct * 100, 1)
                if rec_mean is not None and n_analysts:
                    result["_YF_RecMean"] = round(rec_mean, 2)
                    result["_YF_RecKey"] = yf_info.get("recommendationKey", "")
                    result["_YF_NumAnalysts"] = int(n_analysts)
                if target_mean and cur_price and cur_price > 0:
                    gap_pct = (target_mean - cur_price) / cur_price * 100
                    result["_YF_TargetGapPct"] = round(gap_pct, 1)
                result["_YF_Available"] = True
            except Exception as ye:
                logging.debug("yfinance sentiment failed for %s: %s", ticker, ye)
            # Finnhub 데이터 (내부자 거래, 애널리스트 추천 변화, 실적 서프라이즈)
            try:
                from finnhub_api import get_sentiment_data, is_available as fh_ok
                if fh_ok():
                    fh = get_sentiment_data(ticker)
                    if fh.get("available"):
                        result["_FH_InsiderNet"] = fh["insider_net_shares"]
                        result["_FH_InsiderCount"] = fh["insider_tx_count"]
                        result["_FH_RecBuy"] = fh["rec_strong_buy"] + fh["rec_buy"]
                        result["_FH_RecSell"] = fh["rec_sell"]
                        result["_FH_RecChange"] = fh["rec_change"]
                        result["_FH_EarnSurprise"] = fh["earnings_surprise_pct"]
                        result["_FH_EarnStreak"] = fh["earnings_beat_streak"]
                        result["_FH_Available"] = True
            except Exception as fe:
                logging.debug("Finnhub sentiment failed for %s: %s", ticker, fe)
        try:
            result = _annotate_one_liners([result])[0]
        except Exception as oe:
            logging.warning("one_liner annotate (ticker) failed: %s", oe)
        # AQ 융합은 /api/aq_signal/<ticker> 로 분리 (드로어 lazy-load)
        # ── 응답 캐시 저장 ──
        with _ticker_detail_cache_lock:
            if len(_ticker_detail_cache) >= _TICKER_DETAIL_MAX:
                _ticker_detail_cache.pop(next(iter(_ticker_detail_cache)), None)
            _ticker_detail_cache[_td_key] = {"_ts": int(time.time()), "data": result}
        return jsonify(result)
    except Exception as e:
        logging.exception("api_ticker")
        return jsonify({"error": str(e)}), 500


@app.route("/api/aq_signal/<ticker>")
def api_aq_signal(ticker: str):
    """GET /api/aq_signal/AAPL?market=US → AgentQuant 진입 타이밍 (lazy-load)."""
    market = (request.args.get("market") or "US").upper()
    try:
        from agentquant_signal import get_regime_signal
        aq = get_regime_signal(ticker, market=market)
        if not aq or not aq.get("stock"):
            return jsonify({"ok": False})
        stock = aq["stock"]
        return jsonify({
            "ok": True,
            "EntryScore_aq": float(stock.get("score") or 0),
            "AQ_Verdict": stock.get("verdict_kr"),
            "AQ_VerdictCode": stock.get("verdict"),
            "AQ_Regime": aq.get("market", {}).get("label"),
            "AQ_Reasons": stock.get("reasons", [])[:4],
        })
    except Exception as e:
        logging.warning("api_aq_signal failed for %s: %s", ticker, e)
        return jsonify({"ok": False, "error": str(e)})




@app.route("/api/consensus/<ticker>")
def api_consensus(ticker: str):
    """??? ???? ??: ??? ??, ??/??, ?? ???."""
    import urllib.request
    import json as _json

    market = (request.args.get("market") or "US").upper()
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
            info = yf.Ticker(ticker).info or {}
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
    market = (request.args.get("market") or "US").upper()
    try:
        from agentquant_signal import get_regime_signal
        payload = get_regime_signal(ticker, market=market)
        if not payload:
            return jsonify({"error": "신호를 계산할 수 없습니다."}), 404
        return jsonify(payload)
    except Exception as e:
        logging.exception("api_regime")
        return jsonify({"error": str(e)}), 500


@app.route("/api/four_axis/<ticker>")
def api_four_axis(ticker: str):
    """4축 핸드드로윙 차트 + 분석 데이터 반환 (base64 PNG)."""
    market = (request.args.get("market") or "US").upper()
    cache_key = f"{ticker}:{market}"
    now = int(time.time())
    with _four_axis_cache_lock:
        cached = _four_axis_cache.get(cache_key)
        if cached and (now - cached.get("_ts", 0)) < _FOUR_AXIS_TTL_SEC:
            return jsonify(cached["data"])
    try:
        import yfinance as yf
        _configure_yf_cache()
        from four_axis_analyzer import FourAxisAnalyzer
        from handdrawn_renderer import HandDrawnChartRenderer

        candidates = _build_yf_candidates(ticker, market)
        fetch_timeout_sec = _get_config_int("FOUR_AXIS_FETCH_TIMEOUT_SEC", 20, minimum=5, maximum=120)
        info_timeout_sec = _get_config_int("FOUR_AXIS_INFO_TIMEOUT_SEC", 8, minimum=3, maximum=60)
        min_rows = _get_config_int("FOUR_AXIS_MIN_ROWS", 20, minimum=10, maximum=252)

        # 다중 기간 폴백 — 2y → 1y → 6mo → 3mo 순으로 시도
        # 일부 ETF/저유동 종목은 1y로는 비어있고 6mo 이하에서만 데이터가 나오기도 함
        hist = None
        tried = []
        periods = ("2y", "1y", "6mo", "3mo")
        for yt in candidates:
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
                    logging.warning("four_axis history fetch failed: %s", exc)
                    continue
            if hist is not None:
                break

        # yfinance가 KR 종목에 빈 데이터를 자주 반환 → FinanceDataReader 폴백
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
            rows = 0 if hist is None or hist.empty else len(hist)
            return jsonify({
                "error": f"데이터 부족 (시도: {', '.join(tried[:6])} / 확보: {rows}일, 필요: {min_rows}일)"
            }), 404

        analyzer = FourAxisAnalyzer(hist, ticker)
        result = analyzer.analyze()

        # 차트 제목에 종목명 표시 (티커 코드 대신).
        # KR 우선순위: kr_company_info → yfinance longName/shortName → 티커 폴백.
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
                except Exception:
                    pass
                try:
                    from swing_scan.config import stock_names as _sn
                    nm = _sn.get_name(code6)
                    if nm and nm != code6:
                        chart_title = str(nm)
                except Exception:
                    pass
            # yf.Ticker(...).info 호출은 매우 느리고 hang 위험이 큼.
            # KR은 stock_names 미스 시 ticker 폴백 (info 호출 안 함).
            # US만 보조 폴백으로 info 사용.
            if not chart_title and market == "US":
                try:
                    yt0 = candidates[0] if candidates else ticker
                    yinfo = _run_with_timeout(
                        lambda yt0=yt0: yf.Ticker(yt0).info or {},
                        info_timeout_sec,
                        f"four_axis info {yt0}",
                    )
                    chart_title = (yinfo.get("longName")
                                   or yinfo.get("shortName") or "")
                except Exception:
                    pass
        except Exception:
            pass
        chart_title = chart_title or ticker

        # 큰 차트 렌더링 (1200x560) — 2패널(가격+거래량) 구성
        renderer = HandDrawnChartRenderer(
            hist, result, ticker=chart_title,
            width_px=1200, height_px=560, dpi=100,
        )
        img = renderer.render()

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        chart_b64 = base64.b64encode(buf.read()).decode("ascii")

        import numpy as np

        def _sanitize(obj):
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_sanitize(v) for v in obj]
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj

        rd = _sanitize(result.to_dict())
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
        with _four_axis_cache_lock:
            if len(_four_axis_cache) >= _FOUR_AXIS_MAX:
                _four_axis_cache.pop(next(iter(_four_axis_cache)), None)
            _four_axis_cache[cache_key] = {"data": payload, "_ts": int(time.time())}
        return jsonify(payload)
    except Exception as e:
        logging.exception("api_four_axis")
        return jsonify({"error": str(e)}), 500


# ── 공시·뉴스 API (DART + Naver News) ─────────────────────────────────

@app.route("/api/dart-news/<ticker>")
def api_dart_news(ticker: str):
    """KR 종목 공시 목록 + 뉴스 감성분석 결합 반환."""
    market = (request.args.get("market") or "US").upper()
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
            except Exception:
                pass
            if not stock_name:
                try:
                    import dart_api as _da
                    s = _da.get_summary(code)
                    if s.get("available"):
                        stock_name = s["data"].get("corp_name", "")
                except Exception:
                    pass
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
        with urllib.request.urlopen(req, timeout=15) as r:
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
        with urllib.request.urlopen(req, timeout=10) as r:
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
    market = (request.args.get("market") or "US").upper()
    if market != "US":
        return jsonify({"error": "US 종목만 지원"}), 400

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
        info = yf.Ticker(ticker).info or {}
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
            _cons = analyst_consensus.summarize_upgrades_downgrades(
                yf.Ticker(ticker).upgrades_downgrades)
            if _cons["rows"]:
                result["recommendations"] = _cons["rows"]
        except Exception:
            pass
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
    market = (request.args.get("market") or "KR").upper()
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


@app.route("/api/signal-history/<ticker>")
def api_signal_history(ticker):
    market = request.args.get("market")
    if market not in ("KR", "US"):
        return jsonify({"error": "market must be KR or US"}), 400
    try:
        import history
        timeline = history.load_timeline(ticker, market)
    except Exception as e:
        logging.warning("signal-history failed (%s): %s", ticker, e)
        return jsonify({"ticker": ticker, "market": market, "timeline": []}), 500
    return jsonify({"ticker": ticker, "market": market, "timeline": timeline})


@app.route("/api/deep-analysis/<ticker>")
def api_deep_analysis(ticker: str):
    """Gemini 2.0 Flash + Google Search 그라운딩 기반 8-Phase 종목 심층 분석.

    Query: market=KR|US, mode=brief|standard|detail, force=1 (캐시 무시)
    """
    market = (request.args.get("market") or "KR").upper()
    mode = (request.args.get("mode") or "standard").lower()
    if mode not in ("brief", "standard", "detail"):
        mode = "standard"
    force = (request.args.get("force") or "").lower() in ("1", "true", "yes")
    cache_only = (request.args.get("cache_only") or "").lower() in ("1", "true", "yes")
    name = (request.args.get("name") or "").strip() or None

    try:
        import deep_analysis
    except Exception as e:
        return jsonify({"ok": False, "error": f"deep_analysis 모듈 로드 실패: {e}"}), 500

    # cache_only: 캐시 적중 시만 결과, 미적중 시 304 의미로 빈 응답
    if cache_only:
        cached = deep_analysis._load_cache(ticker, mode)  # noqa: SLF001
        if cached:
            return jsonify(cached)
        return jsonify({"ok": False, "error": "no-cache", "_cached": False}), 204

    if not deep_analysis.is_available():
        return jsonify({
            "ok": False,
            "error": "GEMINI_API_KEY가 설정되지 않았습니다. 설정 화면에서 등록해주세요.",
        }), 503

    try:
        result = deep_analysis.analyze(
            ticker=ticker, market=market, mode=mode, name=name, force=force,
        )
        return jsonify(result)
    except Exception as e:
        logging.exception("deep-analysis failed: %s", ticker)
        return jsonify({"ok": False, "error": str(e)}), 500


# SocketIO 초기화 (gunicorn / 직접 실행 모두 대응)
socketio.init_app(app)

# KR 캐시 워밍 시작 (gunicorn import 시점에도 트리거; file-lock으로 중복 방지)
try:
    _start_kr_warmup_once()
except Exception as _e:
    logging.warning("KR warm-up bootstrap failed: %s", _e)

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
    socketio.run(app, debug=debug, port=port, host=host, allow_unsafe_werkzeug=not is_production)
