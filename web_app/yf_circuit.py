"""yf_circuit — yfinance rate-limit circuit breaker.

연속 RATE_LIMIT 실패가 임계치(_THRESH)를 넘으면 _COOLDOWN_SEC 동안
should_skip() 이 True 를 반환해 호출측이 사전 차단할 수 있게 한다.
rate-limit 이외의 실패(network/parse 등)는 카운터에 영향 없음.

사이드 이펙트 없음 (네트워크/IO 호출 금지). 스레드 안전.
"""

from __future__ import annotations

import threading
import time

_THRESH: int = 5         # 연속 429 5회
_COOLDOWN_SEC: float = 60.0

_LOCK = threading.Lock()
_state: dict = {"fails": 0, "open_until": 0.0}


def should_skip() -> bool:
    """차단 중이면 True. 쿨다운 종료 시 자동 해제."""
    now = time.time()
    with _LOCK:
        if _state["open_until"] > now:
            return True
        if _state["open_until"] and _state["open_until"] <= now:
            _state["open_until"] = 0.0
            _state["fails"] = 0
        return False


def record_failure(reason: str = "") -> None:
    """실패 기록. reason 에 'rate'/'429'/'Too Many' 포함 시에만 카운트."""
    msg = (reason or "").lower()
    is_rate = ("rate" in msg) or ("429" in msg) or ("too many" in msg)
    if not is_rate:
        return
    with _LOCK:
        _state["fails"] += 1
        if _state["fails"] >= _THRESH:
            _state["open_until"] = time.time() + _COOLDOWN_SEC


def record_success() -> None:
    """성공 시 카운터 리셋."""
    with _LOCK:
        _state["fails"] = 0
        _state["open_until"] = 0.0


def status() -> dict:
    """현재 상태 스냅샷 (테스트/모니터링용)."""
    with _LOCK:
        return {
            "fails": _state["fails"],
            "open": _state["open_until"] > time.time(),
            "open_until": _state["open_until"],
        }
