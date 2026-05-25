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
