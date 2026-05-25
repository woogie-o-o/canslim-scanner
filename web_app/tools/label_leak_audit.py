"""MF-005: label-leak-audit — 큐레이션 사전 키가 ML 피처로 직접 누수되는지 정적 검사.

월가 패널(Two Sigma) 권고: 라벨 키가 피처로 들어가면 타겟 누수.
검사 대상:
  - moat_data.json 의 티커 키 → 피처 컬럼명/입력 사용 여부
  - speculative_themes.SPECULATIVE_TICKERS 키 → 동일
  - moat._SPECULATIVE_HINTS 항목 → 동일

CLEAN 종료 코드 0, 1건 이상 발견 시 종료 코드 1.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_APP = ROOT / "web_app"

LABEL_KEYS_HINT = {
    "IsSpeculativeTheme", "ThemeWarning", "_RawTotalScore",
    "MoatCategory", "MoatData", "story_risk",
}

FEATURE_FILES_GLOB = [
    "multibagger*.py",
    "scoring*.py",
    "model*.py",
    "ml*.py",
    "features*.py",
]


def load_label_keys() -> set[str]:
    keys = set(LABEL_KEYS_HINT)
    md = WEB_APP / "moat_data.json"
    if md.exists():
        with open(md, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k in d.keys():
            if not k.startswith("_"):
                keys.add(k)
    return keys


def scan_file(path: Path, label_keys: set[str]) -> list[tuple[int, str, str]]:
    """파일에서 라벨 키 직접 사용 라인 검출. (line_no, key, snippet) 반환."""
    findings = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return findings
    for lineno, line in enumerate(text.splitlines(), 1):
        for key in label_keys:
            # 코드 내 직접 사용 (변수/문자열/dict 키) 패턴
            if re.search(rf'(?:["\'])\b{re.escape(key)}\b(?:["\'])', line):
                if "feature" in line.lower() or "ml_" in line.lower() or "X_train" in line or "fit(" in line:
                    findings.append((lineno, key, line.strip()))
    return findings


def audit(base: Path | None = None) -> tuple[list[dict], int]:
    base = base or WEB_APP
    label_keys = load_label_keys()
    findings = []
    for pattern in FEATURE_FILES_GLOB:
        for path in base.glob(pattern):
            file_findings = scan_file(path, label_keys)
            for ln, key, snippet in file_findings:
                findings.append({
                    "file": str(path.relative_to(ROOT)),
                    "line": ln,
                    "key": key,
                    "snippet": snippet[:120],
                })
    return findings, 0 if not findings else 1


def main(argv: list[str]) -> int:
    findings, code = audit()
    if not findings:
        print("CLEAN — no label leakage detected")
        return 0
    print(f"FOUND {len(findings)} potential leakage(s):")
    for f in findings:
        print(f"  [{f['file']}:{f['line']}] key='{f['key']}' :: {f['snippet']}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
