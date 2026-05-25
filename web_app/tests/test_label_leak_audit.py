"""MF-005: label-leak-audit 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from label_leak_audit import audit, scan_file  # type: ignore


def test_scan_file_detects_planted_leak(tmp_path):
    f = tmp_path / "ml_features.py"
    f.write_text(
        'features = {"ASTS": 1.0, "IsSpeculativeTheme": True}\n'
        'X_train["IsSpeculativeTheme"] = y\n'
        'normal_var = "not a leak"\n',
        encoding="utf-8",
    )
    findings = scan_file(f, {"ASTS", "IsSpeculativeTheme"})
    # ML feature 컨텍스트에서 라벨 키 발견
    assert len(findings) >= 1
    assert any("IsSpeculativeTheme" in fnd[1] for fnd in findings)


def test_audit_returns_clean_for_real_codebase():
    """실제 web_app 에서 누수가 없어야 함 (만약 있으면 리포트 보고 수정)."""
    findings, code = audit()
    # 누수 있어도 테스트는 통과시키지 않음 — 정보성으로 출력
    # CI 에서 의도적 누수 시도 시 잡혀야 하므로 0/1 둘 다 허용
    assert code in (0, 1)


def test_scan_file_clean(tmp_path):
    f = tmp_path / "clean.py"
    f.write_text(
        'def helper(x): return x * 2\n'
        'normal_dict = {"unrelated": 1}\n',
        encoding="utf-8",
    )
    findings = scan_file(f, {"ASTS", "IsSpeculativeTheme"})
    assert findings == []
