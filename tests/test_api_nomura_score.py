"""tests/test_api_nomura_score.py — /api/nomura-score/<ticker> 엔드포인트 테스트.

실행:
    python -m pytest tests/test_api_nomura_score.py -v
"""
from __future__ import annotations

import sys
import os
import types

# ── 경로 설정 ─────────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_WEB = os.path.join(_ROOT, "web_app")
for _p in (_ROOT, _WEB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 무거운 사이드이펙트 모듈 스텁 — app import 전에 등록 ──────────────────
def _stub(name: str, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m


# flask_compress — Compress class 필요
class _FakeCompress:
    def __init__(self, app=None): pass
    def init_app(self, app): pass

_fc = _stub("flask_compress")
_fc.Compress = _FakeCompress

# chat.socketio — init_app/run 필요
class _FakeSocketIO:
    def __init__(self, *a, **kw): pass
    def init_app(self, app, **kw): pass
    def run(self, *a, **kw): pass
    def on(self, *a, **kw): return lambda f: f

_chat = _stub("chat")
_chat.socketio = _FakeSocketIO()

# config_manager
_stub("config_manager", apply_to_environ=lambda: None)

# web_app.serenity
_stub("web_app.serenity", get_serenity_insight=lambda t: None)

# quant_nexus_v20 — prevents tkinter/C-ext crash
_qn = _stub("quant_nexus_v20")
_qn.QuantNexusApp = types.SimpleNamespace(
    KR_NAMES={}, US_NAMES={}, US_DESC={}
)

# other heavy modules
for _name in (
    "engine_adapter", "moat", "one_liner", "speculative_themes",
    "greedzone", "agentquant_signal", "naver_finance", "history",
    "social_buzz", "us_company_info",
):
    _stub(_name)

# ── now safe to import Flask app ──────────────────────────────────────────────
import pytest
from unittest.mock import patch

MOCK_NOMURA = {
    "quantitative_score": 78,
    "grade": "A",
    "piotroski": 7,
    "altman_z": 3.2,
    "beneish_m": -2.1,
    "beneish_warning": False,
    "nomura_rating": "우량",
    "nomura_target": 315.0,
    "nomura_upside": 7.5,
}


@pytest.fixture(scope="module")
def flask_app():
    import web_app.app as _mod
    _mod.app.config["TESTING"] = True
    _mod.app.config["DEBUG"] = False
    return _mod.app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as c:
        yield c


# ── 성공 케이스 ──────────────────────────────────────────────────────────────

def test_nomura_score_success_shape(client):
    """GET /api/nomura-score/AAPL → 200, status=ok, 9-key data dict."""
    import nomura_score as _ns
    with patch.object(_ns, "get_nomura_score", return_value=MOCK_NOMURA):
        resp = client.get("/api/nomura-score/AAPL")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    d = body["data"]
    assert d["nomura_rating"] == "우량"
    assert d["quantitative_score"] == 78
    for key in ("piotroski", "altman_z", "beneish_m", "beneish_warning",
                "grade", "nomura_target", "nomura_upside"):
        assert key in d, f"missing key: {key}"


# ── KR 티커 지원 ─────────────────────────────────────────────────────────

def test_nomura_score_kr_ticker_success(client):
    """005930.KS (KR 종목)도 노무라式 보조 점수를 반환한다."""
    import nomura_score as _ns
    with patch.object(_ns, "get_nomura_score", return_value=MOCK_NOMURA):
        resp = client.get("/api/nomura-score/005930.KS")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["data"]["quantitative_score"] == 78


# ── 알 수 없는 티커(데이터 없음) → 404 ────────────────────────────────────

def test_nomura_score_unknown_ticker_404(client):
    """존재하지 않는 티커 → 404."""
    import nomura_score as _ns
    with patch.object(_ns, "get_nomura_score", return_value=None):
        resp = client.get("/api/nomura-score/ZZZZZZ")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["status"] == "error"
    assert "message" in body


# ── 내부 예외 → 500 ──────────────────────────────────────────────────────────

def test_nomura_score_exception_500(client):
    """get_nomura_score 예외 발생 시 500 반환."""
    import nomura_score as _ns
    with patch.object(_ns, "get_nomura_score", side_effect=RuntimeError("boom")):
        resp = client.get("/api/nomura-score/AAPL")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["message"] == "internal error"
