"""EG-001: _entryLabel(st, disc) — disc<0 (chase) → 풀백대기 가드 검증.

app.js 의 _entryLabel 함수를 node 로 직접 evaluate 해서 6 케이스 테스트.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"


def _extract_entry_label_src() -> str:
    """app.js 에서 _entryLabel 함수 소스만 잘라낸다. _ENTRY_LABEL 상수도 함께."""
    src = APP_JS.read_text(encoding="utf-8")
    # _ENTRY_LABEL 상수
    label_start = src.index("const _ENTRY_LABEL")
    label_end = src.index(";", label_start) + 1
    label_block = src[label_start:label_end]
    # _entryLabel 함수
    fn_start = src.index("function _entryLabel")
    # 함수 끝: 일치하는 닫는 중괄호 찾기
    depth = 0
    i = src.index("{", fn_start)
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                fn_end = i + 1
                break
        i += 1
    else:
        raise RuntimeError("could not find end of _entryLabel function")
    fn_block = src[fn_start:fn_end]
    return label_block + "\n" + fn_block


def _to_js(v):
    if v is None:
        return "null"
    if isinstance(v, float) and v != v:
        return "NaN"
    return json.dumps(v)


def _run_in_node(disc, atr_pct=None) -> str:
    """주어진 disc, atrPct 로 _entryLabel('STRONG', disc, atrPct) 실행."""
    src = _extract_entry_label_src()
    script = (
        src
        + f"\nconst out = _entryLabel('STRONG', {_to_js(disc)}, {_to_js(atr_pct)});\n"
        + "process.stdout.write(Buffer.from(out, 'utf8'));\n"
    )
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not available")
    res = subprocess.run(
        [node_exe, "-e", script], capture_output=True, check=True
    )
    return res.stdout.decode("utf-8")


@pytest.mark.parametrize(
    "disc,expected",
    [
        (-5, "풀백대기"),     # 현재가가 entry 5% 위 → 추격
        (-0.1, "풀백대기"),   # 살짝 위라도 음수면 추격으로 분류
        (0, "진입적기"),       # 정확히 entry — '진입적기'
        (0.8, "진입적기"),     # 1.5% 미만 — '진입적기'
        (3, "분할진입"),       # 1.5~5% — '분할진입'
        (7, "풀백대기"),       # 5% 이상 — '풀백대기'
    ],
)
def test_entry_label_disc_branch(disc, expected):
    assert _run_in_node(disc) == expected


def test_entry_label_null_fallback():
    # 역호환: disc 가 null/undefined 면 '진입적기' fallback
    assert _run_in_node(None) == "진입적기"


# EG-003: ATR-정규화 임계값 — disc/atrPct 비율로 라벨 결정
@pytest.mark.parametrize(
    "disc,atr_pct,expected",
    [
        # ATR 4% 인 종목: 갭 1% (=0.25 ATR) → 진입적기
        (1.0, 4.0, "진입적기"),
        # ATR 4% 인 종목: 갭 2% (=0.5 ATR) → 분할진입
        (2.0, 4.0, "분할진입"),
        # ATR 4% 인 종목: 갭 4% (=1.0 ATR) → 풀백대기
        (4.0, 4.0, "풀백대기"),
        # 저변동성 ATR 0.5% 종목: 갭 1% (=2 ATR) → 풀백대기
        (1.0, 0.5, "풀백대기"),
        # 저변동성 ATR 0.5% 종목: 갭 0.2% (=0.4 ATR) → 진입적기
        (0.2, 0.5, "진입적기"),
        # 고변동성 ATR 8% 종목: 갭 3% (=0.375 ATR) → 진입적기
        (3.0, 8.0, "진입적기"),
        # 고변동성 ATR 8% 종목: 갭 6% (=0.75 ATR) → 분할진입
        (6.0, 8.0, "분할진입"),
    ],
)
def test_entry_label_atr_normalized(disc, atr_pct, expected):
    assert _run_in_node(disc, atr_pct) == expected


def test_atr_pct_zero_falls_back_to_absolute():
    # atrPct=0 → 절대값 fallback (disc 0.8 → '진입적기')
    assert _run_in_node(0.8, 0) == "진입적기"


def test_atr_pct_null_falls_back_to_absolute():
    # atrPct=null → 절대값 fallback (disc 3 → '분할진입')
    assert _run_in_node(3.0, None) == "분할진입"


# EG-005: stale 가드 — asOfTs 가 5분 초과 시 라벨에 ' (stale)' 접미사
def _run_with_age(disc, age_sec) -> str:
    src = _extract_entry_label_src()
    script = (
        src
        + f"\nconst asOf = Math.floor(Date.now()/1000) - {age_sec};\n"
        + f"const out = _entryLabel('STRONG', {_to_js(disc)}, null, asOf);\n"
        + "process.stdout.write(Buffer.from(out, 'utf8'));\n"
    )
    import shutil
    import subprocess
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not available")
    res = subprocess.run([node_exe, "-e", script], capture_output=True, check=True)
    return res.stdout.decode("utf-8")


def test_stale_appends_suffix_after_5min():
    # 6분 경과 → ' (stale)' 접미사
    out = _run_with_age(0.5, 360)
    assert out.endswith(" (stale)")


def test_fresh_no_stale_suffix():
    # 1분 경과 → stale 아님
    out = _run_with_age(0.5, 60)
    assert "(stale)" not in out


def test_stale_boundary_just_under_5min_not_stale():
    # 4분(240초) → stale 아님
    out = _run_with_age(0.5, 240)
    assert "(stale)" not in out
