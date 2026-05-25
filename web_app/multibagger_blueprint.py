# -*- coding: utf-8 -*-
"""멀티배거 파인더 Blueprint — app.py 모놀리스 분할.

상태(캐시·락·경로)는 호환성을 위해 app.py 모듈 속성으로 유지.
라우트 핸들러는 lazy import 로 app 모듈을 읽어 monkeypatch 호환성 보장
(tests/test_multibagger_api.py 가 flask_app._MULTIBAGGER_BAGGERS_PATH 등을 직접 교체).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, render_template

multibagger_bp = Blueprint("multibagger", __name__)


# ── 페이지 ─────────────────────────────────────────────
@multibagger_bp.route("/multibagger")
def multibagger_page():
    import app as flask_app  # lazy: 순환 import 회피
    static_dir = os.path.join(flask_app.app.root_path, "static")

    def _v(name: str) -> int:
        p = os.path.join(static_dir, name)
        try:
            return int(os.path.getmtime(p))
        except OSError:
            return 0

    return render_template(
        "multibagger.html",
        v_theme_css=_v("theme.css"),
        v_multibagger_css=_v("multibagger.css"),
        v_multibagger_js=_v("multibagger.js"),
    )


# ── API ───────────────────────────────────────────────
@multibagger_bp.route("/api/multibagger")
def api_multibagger():
    import app as flask_app
    cached = flask_app._multibagger_results_cache
    if cached and (time.time() - cached.get("_ts", 0)) < flask_app._MULTIBAGGER_TTL_SEC:
        resp = jsonify(cached["data"])
        resp.headers["X-Warming-In-Progress"] = "false"
        return resp
    _maybe_trigger_multibagger_build()
    body = cached.get("data") if cached else {
        "pass": [], "watch": [], "meta": {"warming": True}
    }
    resp = jsonify(body)
    resp.headers["X-Warming-In-Progress"] = "true"
    return resp


@multibagger_bp.route("/api/multibagger/thresholds")
def api_multibagger_thresholds():
    import multibagger as mb
    return jsonify(mb.DEFAULTS)


@multibagger_bp.route("/api/multibagger/ticker/<sym>")
def api_multibagger_ticker(sym: str):
    import app as flask_app
    cached = flask_app._multibagger_results_cache
    if not cached:
        return jsonify({"error": "cache empty"}), 404
    sym_up = sym.upper()
    for row in cached.get("data", {}).get("pass", []) + cached.get("data", {}).get("watch", []):
        if row["ticker"].upper() == sym_up:
            return jsonify(row)
    return jsonify({"error": "not found"}), 404


@multibagger_bp.route("/api/multibagger/diff")
def api_multibagger_diff():
    import app as flask_app
    import multibagger as mb
    path = flask_app._MULTIBAGGER_BAGGERS_PATH
    if not os.path.exists(path):
        return jsonify({"baggers": [], "stats": {"available": False}})
    try:
        # pickle.load RCE 회피 — JSON 화이트리스트.
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning("baggers cache load failed: %s", e)
        return jsonify({"baggers": [], "stats": {"available": False}})

    items = blob.get("baggers", [])
    out = []
    pass_n = watch_n = miss_n = 0
    fail_counter: dict[str, int] = {}
    for b in items:
        snap = b.get("snapshot_at_start") or {}
        if not snap:
            out.append({**b, "classify": "UNKNOWN", "fail_gates": []})
            continue
        f = mb.Fundamentals(**{k: snap.get(k) for k in mb.Fundamentals.__dataclass_fields__})
        cls = mb.classify(f, mb.DEFAULTS)
        if cls.layer == "PASS":
            pass_n += 1
        elif cls.layer == "WATCH":
            watch_n += 1
        else:
            miss_n += 1
        for g in cls.gates_failed | cls.gates_missing:
            fail_counter[g] = fail_counter.get(g, 0) + 1
        out.append({**b, "classify": cls.layer,
                    "fail_gates": sorted(cls.gates_failed | cls.gates_missing)})

    return jsonify({
        "baggers": out,
        "stats": {
            "available": True,
            "pass_n": pass_n, "watch_n": watch_n, "miss_n": miss_n,
            "top_fail_gates": sorted(fail_counter.items(), key=lambda x: -x[1])[:5],
        },
    })


# ── 백그라운드 빌더 ─────────────────────────────────────────
def _multibagger_warmup_loop(interval_sec: int = 3600) -> None:
    import app as flask_app
    while True:
        try:
            cached = flask_app._multibagger_results_cache
            stale = (not cached) or (time.time() - cached.get("_ts", 0)) >= flask_app._MULTIBAGGER_TTL_SEC
            if stale and flask_app._multibagger_build_lock.acquire(blocking=False):
                try:
                    _rebuild_multibagger_us()
                finally:
                    flask_app._multibagger_build_lock.release()
        except Exception as e:
            logging.warning("multibagger warmup loop error: %s", e)
        time.sleep(interval_sec)


_multibagger_warmup_started = False
_multibagger_warmup_lock = threading.Lock()


def start_multibagger_warmup_once() -> None:
    global _multibagger_warmup_started
    with _multibagger_warmup_lock:
        if _multibagger_warmup_started:
            return
        _multibagger_warmup_started = True
    threading.Thread(target=_multibagger_warmup_loop, daemon=True).start()
    logging.info("multibagger warmup loop started")


def _maybe_trigger_multibagger_build() -> None:
    import app as flask_app
    if not flask_app._multibagger_build_lock.acquire(blocking=False):
        return

    def _worker():
        try:
            _rebuild_multibagger_us()
        finally:
            flask_app._multibagger_build_lock.release()

    threading.Thread(target=_worker, daemon=True).start()


def _rebuild_multibagger_us() -> None:
    import app as flask_app
    import multibagger as mb
    import multibagger_enrich as me
    import multibagger_rates as mr

    base = None
    with flask_app._scan_results_cache_lock:
        cached_base = flask_app._scan_results_cache.get(("US", "BALANCED", ""))
        if cached_base:
            base = cached_base.get("data")
    if not base:
        logging.info("multibagger: base US scan cache empty, aborting build")
        return

    dgs10 = mr.get_dgs10()
    result = mb.build_results(
        base, dgs10_pct=dgs10, enrich_fn=me.enrich_one, max_workers=8,
        hist_prefetch_fn=me.prefetch_history,
    )
    flask_app._multibagger_results_cache.clear()
    flask_app._multibagger_results_cache.update({"_ts": time.time(), "data": result})
    logging.info("multibagger: built %d PASS / %d WATCH (universe %d, candidates %d)",
                 result["meta"]["pass_n"], result["meta"]["watch_n"],
                 result["meta"]["universe_n"], result["meta"]["candidates_n"])
