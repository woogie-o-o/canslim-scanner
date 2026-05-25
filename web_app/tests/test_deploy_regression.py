"""
test_deploy_regression.py — 커밋 55962c4 배포 회귀 방지 테스트.

방지 대상 버그:
  1) flask-compress 미설치로 11MB+ JSON이 비압축 전송되어 모바일 타임아웃
  2) _populate_sector_caches 분류/캐시 저장 오류
  3) app.py import 실패 (어떤 형태로든 ImportError가 import 단계에서 새어나오면 안 됨)
  4) /api/scan, /healthz 엔드포인트 사라짐 / 응답 형식 변경

실행:
  pytest web_app/tests/test_deploy_regression.py -v
"""
from __future__ import annotations

import os
import sys
import pathlib

import pytest

# 워밍 스레드는 테스트 잡음 — 모두 비활성화
os.environ.setdefault("DISABLE_KR_WARMUP", "1")
os.environ.setdefault("DISABLE_US_WARMUP", "1")

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# 1) requirements.txt 회귀 — flask-compress 누락 감지
# ─────────────────────────────────────────────────────────────────────────────
def test_requirements_lists_flask_compress():
    """requirements.txt에서 flask-compress가 빠지면 모바일 사용자가 죽는다."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "flask-compress" in req.lower(), (
        "flask-compress 가 requirements.txt 에서 사라졌습니다 — "
        "비압축 JSON(11MB+)이 모바일에서 타임아웃됩니다."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2) app.py import 무결성
# ─────────────────────────────────────────────────────────────────────────────
def test_app_imports_without_errors():
    """flask-compress 미설치 등 외부 환경 차이로 app import가 실패해서는 안 됨."""
    from web_app import app as app_module  # noqa: F401
    assert hasattr(app_module, "app")
    assert hasattr(app_module, "_FLASK_COMPRESS_OK")
    assert hasattr(app_module, "_populate_sector_caches")


# ─────────────────────────────────────────────────────────────────────────────
# 3) /healthz는 gzip 상태를 노출 — 모니터링에서 silent fail 감지
# ─────────────────────────────────────────────────────────────────────────────
def test_healthz_exposes_gzip_status():
    from web_app.app import app
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "gzip" in body, "/healthz 응답에 gzip 키가 없으면 flask-compress silent fail을 감지할 수 없다"
    assert isinstance(body["gzip"], bool)


def test_flask_compress_actually_installed_and_active():
    """gzip 키 존재만 검증하면 silent fail 감지 못 함 — 값이 True 여야 한다.

    회귀 사례: flask-compress 가 requirements.txt 에는 있지만 운영 venv 에 미설치 →
    _FLASK_COMPRESS_OK=False → /api/scan 14MB raw 전송 → 모바일/일부 회선 타임아웃.
    """
    import importlib
    # 1) 모듈 import 자체가 되어야 한다 (pip install 누락 즉시 감지).
    flask_compress = importlib.import_module("flask_compress")
    assert hasattr(flask_compress, "Compress")
    # 2) app.py 부트 시 Compress(app) 가 실제로 실행됐어야 한다.
    from web_app.app import _FLASK_COMPRESS_OK
    assert _FLASK_COMPRESS_OK is True, (
        "flask_compress 가 미설치되거나 Compress(app) wiring 이 깨졌습니다 — "
        "/api/scan 응답이 비압축으로 나가 모바일 사용자가 타임아웃됩니다."
    )
    # 3) /healthz 도 같은 진실을 노출해야 한다.
    from web_app.app import app
    body = app.test_client().get("/healthz").get_json()
    assert body.get("gzip") is True


def test_api_scan_response_carries_content_encoding():
    """End-to-end: Accept-Encoding 보낸 클라이언트가 압축 응답을 받는지.

    flask-compress 가 올라가 있어도 라우트/미들웨어 순서가 깨지면 압축이 빠질 수 있다.
    실제 응답 헤더로 검증해 silent regression 차단.
    """
    from web_app.app import app
    client = app.test_client()
    resp = client.get(
        "/api/scan?market=US&strategy=BALANCED",
        headers={"Accept-Encoding": "gzip, br"},
    )
    assert resp.status_code == 200
    enc = resp.headers.get("Content-Encoding", "")
    # 빈 응답(워밍 중)은 압축 안 될 수 있으므로 본문 크기로 가드.
    body_len = int(resp.headers.get("Content-Length") or len(resp.data) or 0)
    if body_len < 5000:
        pytest.skip(f"response too small to compress ({body_len}B) — warming cache")
    assert enc in ("gzip", "br", "deflate", "zstd"), (
        f"flask-compress 가 응답에 Content-Encoding 을 못 붙임 — got {enc!r}, size={body_len}B"
    )


def test_flask_compress_excludes_zstd_for_browser_compatibility():
    """zstd 는 Chrome 123+ 가 Accept-Encoding 에 광고하지만 Safari/구버전/프록시/확장에서
    디코딩 실패 → fetch network error → 브라우저는 무한 재시도 → 사용자는 '연결 안됨' 로 본다.

    회귀 사례: COMPRESS_ALGORITHM 설정 없이 flask-compress 가 Accept-Encoding 의 zstd 를
    1순위로 골라 응답함. 사용자가 '전체 스캔하면 연결이 안돼' 신고.
    """
    from web_app.app import app
    client = app.test_client()
    # Chrome/Edge 가 보내는 기본 Accept-Encoding 그대로.
    resp = client.get(
        "/api/scan?market=US&strategy=BALANCED",
        headers={"Accept-Encoding": "gzip, deflate, br, zstd"},
    )
    assert resp.status_code == 200
    enc = resp.headers.get("Content-Encoding", "")
    body_len = int(resp.headers.get("Content-Length") or len(resp.data) or 0)
    if body_len < 5000:
        pytest.skip(f"response too small to compress ({body_len}B) — warming cache")
    assert enc != "zstd", (
        "flask-compress 가 zstd 를 선택했음 — 브라우저 호환성 깨짐. "
        "app.config['COMPRESS_ALGORITHM'] 에서 zstd 제외 필요."
    )
    assert enc in ("br", "gzip", "deflate"), (
        f"안전한 인코딩만 허용 — got {enc!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3b) NaN/Infinity 회귀 — Python json 은 'NaN','Infinity' 토큰을 그대로 출력하지만
#     브라우저 JSON.parse() 는 SyntaxError 를 던진다 → runScan 무한 재시도.
# ─────────────────────────────────────────────────────────────────────────────
def test_jsonify_replaces_nan_and_infinity_with_null():
    """Flask jsonify 가 NaN/±Inf 를 null 로 치환해야 한다.

    회귀 사례: pandas/계산 결과의 float('inf'), float('nan') 이 응답에 섞임 →
    Python json.dumps(allow_nan=True) 가 'NaN','Infinity' 토큰을 그대로 직렬화 →
    브라우저 fetch.json() 이 SyntaxError → runScan catch → "서버 준비 중…" 무한 재시도.
    """
    import math
    import json as _json
    from web_app.app import app
    client = app.test_client()
    # 임시 엔드포인트 추가는 번거로우니, jsonify 를 직접 호출해 결과 검증.
    with app.test_request_context():
        from flask import jsonify
        resp = jsonify({
            "good": 1.5,
            "nan": float("nan"),
            "pos_inf": float("inf"),
            "neg_inf": -float("inf"),
            "nested": [{"x": float("nan")}, {"y": 2.0}],
        })
    body = resp.get_data(as_text=True)
    # JSON 표준 — 'NaN','Infinity' 토큰 금지.
    assert "NaN" not in body, f"NaN 토큰이 응답에 남아있음 — 브라우저 JSON.parse 실패: {body[:200]}"
    assert "Infinity" not in body, f"Infinity 토큰이 응답에 남아있음: {body[:200]}"
    # 표준 JSON 으로 파싱돼야 한다 (stdlib json 은 allow_nan 기본 True 라
    # 'NaN' 도 통과하므로 simplejson 로 strict 검증).
    parsed = _json.loads(body, parse_constant=lambda _v: (_ for _ in ()).throw(
        ValueError(f"non-standard JSON constant: {_v}")
    ))
    assert parsed["good"] == 1.5
    assert parsed["nan"] is None
    assert parsed["pos_inf"] is None
    assert parsed["neg_inf"] is None
    assert parsed["nested"][0]["x"] is None
    assert parsed["nested"][1]["y"] == 2.0


# ─────────────────────────────────────────────────────────────────────────────
# 4) /api/scan 엔드포인트 존재 + 200
# ─────────────────────────────────────────────────────────────────────────────
def test_api_scan_endpoint_exists():
    from web_app.app import app
    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/scan" in rules


# ─────────────────────────────────────────────────────────────────────────────
# 5) _populate_sector_caches 단위 테스트
# ─────────────────────────────────────────────────────────────────────────────
def test_populate_sector_caches_groups_correctly():
    from web_app.app import (
        _populate_sector_caches,
        _scan_results_cache,
        _scan_results_cache_lock,
    )

    # 테스트 격리: 사용할 키들 초기화
    with _scan_results_cache_lock:
        for k in [("US", "TEST", "Tech"), ("US", "TEST", "Finance"), ("US", "TEST", "")]:
            _scan_results_cache.pop(k, None)

    sample = [
        {"Ticker": "AAPL", "Sector": "Tech"},
        {"Ticker": "MSFT", "Sector": "Tech"},
        {"Ticker": "JPM",  "Sector": "Finance"},
        {"Ticker": "NOSEC", "Sector": ""},        # 빈 섹터는 제외돼야 함
        {"Ticker": "NULLSEC", "Sector": None},    # None 섹터도 제외
    ]
    _populate_sector_caches("US", "TEST", sample, ts=99999)

    with _scan_results_cache_lock:
        tech = _scan_results_cache.get(("US", "TEST", "Tech"))
        fin = _scan_results_cache.get(("US", "TEST", "Finance"))
        empty = _scan_results_cache.get(("US", "TEST", ""))

    assert tech is not None and len(tech["data"]) == 2
    assert {r["Ticker"] for r in tech["data"]} == {"AAPL", "MSFT"}
    assert fin is not None and len(fin["data"]) == 1
    assert fin["data"][0]["Ticker"] == "JPM"
    # 빈 섹터 키는 이 함수가 생성하면 안 됨
    assert empty is None or empty.get("_ts") != 99999, (
        "_populate_sector_caches가 빈 섹터('')에 데이터를 저장하면 "
        "전체 캐시를 덮어써서 /api/scan 전체 응답이 깨질 수 있다"
    )
    # 타임스탬프 정확히 전달
    assert tech["_ts"] == 99999
    assert fin["_ts"] == 99999


def test_populate_sector_caches_handles_empty_results():
    """빈 리스트가 와도 크래시 없이 통과 — 워밍 실패 시 시나리오."""
    from web_app.app import _populate_sector_caches
    # 예외 발생하지 않으면 통과
    _populate_sector_caches("US", "BALANCED", [], ts=0)


# ─────────────────────────────────────────────────────────────────────────────
# 6) _validate_ticker — 보안 회귀 방지
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("good", ["AAPL", "BRK.B", "005930", "005930.KS", "TSM"])
def test_validate_ticker_accepts_legitimate(good):
    from web_app.app import _validate_ticker
    assert _validate_ticker(good) == good


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "AAPL; rm -rf", "A" * 50, None, 123, "<script>"])
def test_validate_ticker_rejects_malicious(bad):
    from web_app.app import _validate_ticker
    assert _validate_ticker(bad) is None


# ─────────────────────────────────────────────────────────────────────────────
# 7) STORY_STOCK veto — 월가 패널 P0 회귀 방지
#    "컨셉 회사지 사업 회사가 아닌 거 언제 깨달을 거임?" 같은 모욕 톤이
#    해자 보유 우량주(AAPL/NVDA형) 또는 per>=25+eps_g>=10 일반 우량주에
#    부착되던 회귀 차단. 4개 퀀트 에이전트 컨센서스 기반.
# ─────────────────────────────────────────────────────────────────────────────
import sys as _sys
import pathlib as _pathlib
_WA = _pathlib.Path(__file__).resolve().parents[1]
if str(_WA) not in _sys.path:
    _sys.path.insert(0, str(_WA))


_VETO_CASES = [
    # (이름, 입력 dict, "STORY_STOCK 이면 안 된다" / "STORY_STOCK 이어야 한다")
    ("해자+고PE+중간성장 (AAPL형)",
     {"Ticker": "AAPL", "MoatCategory": "SWITCHING", "_PER": 30, "_ROE": 0.45,
      "_EPSGrowth": 0.12, "_OperatingMargin": 0.30, "TotalScore": 75, "Mom12M": 0.25},
     "not_story"),
    ("해자+극단모멘텀 (NVDA형)",
     {"Ticker": "NVDA", "MoatCategory": "INTANGIBLE", "_PER": 80, "_ROE": 0.55,
      "_EPSGrowth": 0.80, "_OperatingMargin": 0.55, "TotalScore": 85,
      "Mom12M": 1.50, "EntryStatus": "GREEN"},
     "not_story"),
    ("우량주 per>=25 (라인 4295 함정)",
     {"Ticker": "MID", "MoatCategory": "NONE", "_PER": 28, "_ROE": 0.18,
      "_EPSGrowth": 0.15, "_OperatingMargin": 0.20, "TotalScore": 55, "Mom12M": 0.10},
     "not_story"),
    ("진짜 컨셉주 (해자X 적자)",
     {"Ticker": "FAKE", "MoatCategory": "NONE", "_PER": 50, "_ROE": -0.05,
      "_EPSGrowth": 0.12, "_OperatingMargin": -0.10, "TotalScore": 35, "Mom12M": 0.40},
     "story"),
    ("해자 결측 + 적자 + 테마 (BUBBLE 우회)",
     {"Ticker": "KORSTORY", "MoatCategory": "", "_PER": 35, "_ROE": 0.03,
      "_EPSGrowth": 0.12, "_OperatingMargin": 0.02, "TotalScore": 40,
      "Mom12M": 0.30, "_Mom3M": 0.05},
     "story"),
]


@pytest.mark.parametrize("name,row,expect", _VETO_CASES)
def test_story_stock_veto_routing(name, row, expect):
    """월가 패널 P0: STORY_STOCK 오분류 회귀 차단."""
    import one_liner
    bucket = one_liner._raw_bucket(row)
    if expect == "not_story":
        assert bucket != "STORY_STOCK", (
            f"{name}: 해자/우량주가 STORY_STOCK 으로 분류됐다 (bucket={bucket}). "
            f"_veto_story_stock={one_liner._veto_story_stock(row)}"
        )
    else:
        assert bucket == "STORY_STOCK", (
            f"{name}: 진짜 컨셉주가 STORY_STOCK 으로 라우팅되지 않았다 (bucket={bucket})"
        )


def test_veto_helper_exists_and_signature():
    """_veto_story_stock 헬퍼가 사라지면 veto 게이트 전체가 무력화 — 존재 보장."""
    import one_liner
    assert hasattr(one_liner, "_veto_story_stock")
    assert one_liner._veto_story_stock({"MoatCategory": "SWITCHING"}) is True
    assert one_liner._veto_story_stock({"_ROE": 0.20}) is True  # ROE 20%
    assert one_liner._veto_story_stock({}) is False  # 빈 dict → veto 안 함
