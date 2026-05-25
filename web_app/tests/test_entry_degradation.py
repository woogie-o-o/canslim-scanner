"""EG-004: 자동 강등 — disc>5% + STRONG → NEUTRAL + degradation_reason='gap_too_deep'."""

from __future__ import annotations

import re
from pathlib import Path


SRC = (Path(__file__).resolve().parents[2] / "quant_nexus_v20.py").read_text(encoding="utf-8")


def test_degradation_block_present():
    """엔진 코드에 EG-004 강등 블록이 존재해야 한다."""
    assert "EG-004" in SRC
    assert "degradation_reason" in SRC
    assert "gap_too_deep" in SRC


def test_degradation_triggers_only_on_strong():
    """if entry_status == 'STRONG' and entry_discount > 0.05 패턴이 있어야 한다."""
    pattern = re.compile(
        r"if\s+entry_status\s*==\s*[\"']STRONG[\"']\s+and\s+entry_discount\s*>\s*0\.05"
    )
    assert pattern.search(SRC), "STRONG + disc>0.05 조건이 없음"


def test_degradation_updates_status_to_neutral():
    """강등 시 entry_status='NEUTRAL' 으로 변경되어야 한다."""
    # 강등 블록 내부에 entry_status = "NEUTRAL" 가 있는지
    eg004_start = SRC.index("EG-004")
    block = SRC[eg004_start:eg004_start + 600]
    assert 'entry_status = "NEUTRAL"' in block
    assert 'status_label = "NEUTRAL"' in block


def test_entry_plan_exposes_degradation_reason():
    """entry_plan dict 에 degradation_reason 필드가 포함되어야 한다."""
    assert '"degradation_reason": degradation_reason' in SRC


def test_entry_plan_exposes_as_of_ts():
    """EG-005: entry_plan 에 as_of_ts 필드가 포함되어야 한다."""
    assert '"as_of_ts": int(time.time())' in SRC


def _simulate_degradation(status: str, discount: float):
    """순수 함수 시뮬레이션 — EG-004 로직을 그대로 재현."""
    degradation_reason = None
    if status == "STRONG" and discount > 0.05:
        status = "NEUTRAL"
        degradation_reason = "gap_too_deep"
    return status, degradation_reason


def test_simulation_strong_deep_gap_degrades():
    s, r = _simulate_degradation("STRONG", 0.07)
    assert s == "NEUTRAL"
    assert r == "gap_too_deep"


def test_simulation_strong_shallow_gap_keeps():
    s, r = _simulate_degradation("STRONG", 0.02)
    assert s == "STRONG"
    assert r is None


def test_simulation_neutral_deep_gap_untouched():
    # 이미 NEUTRAL/AVOID 은 강등 대상 아님
    s, r = _simulate_degradation("NEUTRAL", 0.10)
    assert s == "NEUTRAL"
    assert r is None


def test_simulation_strong_boundary_5pct_no_degrade():
    # disc == 0.05 정확히는 강등 X (> 사용)
    s, r = _simulate_degradation("STRONG", 0.05)
    assert s == "STRONG"
    assert r is None
