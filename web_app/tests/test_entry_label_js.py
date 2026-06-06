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
        (0, "근접 구간"),       # 정확히 entry — '근접 구간'
        (0.8, "근접 구간"),     # 1.5% 미만 — '근접 구간'
        (3, "이격 구간"),       # 1.5~5% — '이격 구간'
        (7, "풀백대기"),       # 5% 이상 — '풀백대기'
    ],
)
def test_entry_label_disc_branch(disc, expected):
    assert _run_in_node(disc) == expected


def test_entry_label_null_fallback():
    # disc 가 null/undefined → '데이터 부족' (MNAR 결측 → 긍정 라벨 대치 방지)
    assert _run_in_node(None) == "데이터 부족"


# EG-003: ATR-정규화 임계값 — disc/atrPct 비율로 라벨 결정
@pytest.mark.parametrize(
    "disc,atr_pct,expected",
    [
        # ATR 4% 인 종목: 갭 1% (=0.25 ATR) → 근접 구간
        (1.0, 4.0, "근접 구간"),
        # ATR 4% 인 종목: 갭 2% (=0.5 ATR) → 이격 구간
        (2.0, 4.0, "이격 구간"),
        # ATR 4% 인 종목: 갭 4% (=1.0 ATR) → 풀백대기
        (4.0, 4.0, "풀백대기"),
        # 저변동성 ATR 0.5% 종목: 갭 1% (=2 ATR) → 풀백대기
        (1.0, 0.5, "풀백대기"),
        # 저변동성 ATR 0.5% 종목: 갭 0.2% (=0.4 ATR) → 근접 구간
        (0.2, 0.5, "근접 구간"),
        # 고변동성 ATR 8% 종목: 갭 3% (=0.375 ATR) → 근접 구간
        (3.0, 8.0, "근접 구간"),
        # 고변동성 ATR 8% 종목: 갭 6% (=0.75 ATR) → 이격 구간
        (6.0, 8.0, "이격 구간"),
    ],
)
def test_entry_label_atr_normalized(disc, atr_pct, expected):
    assert _run_in_node(disc, atr_pct) == expected


def test_atr_pct_zero_falls_back_to_absolute():
    # atrPct=0 → 절대값 fallback (disc 0.8 → '근접 구간')
    assert _run_in_node(0.8, 0) == "근접 구간"


def test_atr_pct_null_falls_back_to_absolute():
    # atrPct=null → 절대값 fallback (disc 3 → '이격 구간')
    assert _run_in_node(3.0, None) == "이격 구간"


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


# EG-006: 복합 드로다운 경고 — 52주 고점 거리 + MDD 중 더 나쁜 쪽 사용
def _run_with_dd(disc, ddPct, mddCurrent=None, mddRisk=None, volRegime=None) -> str:
    src = _extract_entry_label_src()
    script = (
        src
        + f"\nconst out = _entryLabel('STRONG', {_to_js(disc)}, null, null, "
        + f"{_to_js(ddPct)}, null, {_to_js(mddCurrent)}, {_to_js(mddRisk)}, {_to_js(volRegime)});\n"
        + "process.stdout.write(Buffer.from(out, 'utf8'));\n"
    )
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not available")
    res = subprocess.run([node_exe, "-e", script], capture_output=True, check=True)
    return res.stdout.decode("utf-8")


@pytest.mark.parametrize(
    "disc,ddPct,mddCurrent,expected_suffix",
    [
        # 52주 고점 -10%, MDD -25% → MDD가 더 나쁨 → [경고] (NORMAL 레짐)
        (0.5, -10, -25, "[경고]"),
        # 52주 고점 -35%, MDD -10% → 52주가 더 나쁨 → [고위험]
        (0.5, -35, -10, "[고위험]"),
        # 둘 다 -18% → [주의] (NORMAL: -15 임계값)
        (0.5, -18, -18, "[주의]"),
        # 둘 다 양호 → 경고 없음
        (0.5, -5, -5, None),
        # MDD만 나쁨, 52주 null → MDD 기준
        (0.5, None, -22, "[경고]"),
        # 52주만 나쁨, MDD null → 52주 기준
        (0.5, -31, None, "[고위험]"),
    ],
)
def test_composite_drawdown_warning(disc, ddPct, mddCurrent, expected_suffix):
    out = _run_with_dd(disc, ddPct, mddCurrent)
    if expected_suffix:
        assert out.endswith(expected_suffix), f"expected '{expected_suffix}' in '{out}'"
    else:
        assert "[" not in out, f"unexpected warning in '{out}'"


# EG-008: vol_regime 적응형 임계값 — LOW=0.6x(민감), HIGH=1.6x(관대)
@pytest.mark.parametrize(
    "ddPct,volRegime,expected_suffix",
    [
        # LOW 레짐: 임계값 -9%/-12%/-18% (0.6x)
        (-10, "LOW", "[주의]"),         # -10 <= -9 → [주의]
        (-13, "LOW", "[경고]"),         # -13 <= -12 → [경고]
        (-19, "LOW", "[고위험]"),       # -19 <= -18 → [고위험]
        (-8, "LOW", None),              # -8 > -9 → 경고 없음
        # HIGH 레짐: 임계값 -24%/-32%/-48% (1.6x)
        (-20, "HIGH", None),            # -20 > -24 → 경고 없음
        (-25, "HIGH", "[주의]"),        # -25 <= -24 → [주의]
        (-33, "HIGH", "[경고]"),        # -33 <= -32 → [경고]
        (-49, "HIGH", "[고위험]"),      # -49 <= -48 → [고위험]
        # NORMAL 레짐: 기존 -15/-20/-30
        (-16, "NORMAL", "[주의]"),
        (-21, "NORMAL", "[경고]"),
    ],
)
def test_vol_regime_adaptive_thresholds(ddPct, volRegime, expected_suffix):
    out = _run_with_dd(0.5, ddPct, None, None, volRegime)
    if expected_suffix:
        assert out.endswith(expected_suffix), f"expected '{expected_suffix}' in '{out}'"
    else:
        assert "[" not in out, f"unexpected warning in '{out}'"


# EG-007: NEUTRAL/AVOID에 MDD 극단적 위험 표시
def _run_neutral_mdd(mddRisk) -> str:
    src = _extract_entry_label_src()
    script = (
        src
        + f"\nconst out = _entryLabel('NEUTRAL', null, null, null, null, "
        + f"'추세 확인 필요', null, {_to_js(mddRisk)});\n"
        + "process.stdout.write(Buffer.from(out, 'utf8'));\n"
    )
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not available")
    res = subprocess.run([node_exe, "-e", script], capture_output=True, check=True)
    return res.stdout.decode("utf-8")


def test_neutral_mdd_extreme_shows_warning():
    assert _run_neutral_mdd("EXTREME").endswith("[고위험]")


def test_neutral_mdd_high_shows_warning():
    assert _run_neutral_mdd("HIGH").endswith("[경고]")


def test_neutral_mdd_normal_no_warning():
    out = _run_neutral_mdd("NORMAL")
    assert "[" not in out


# EG-009: P4 급락 속도 경보 — ddVelocity5d < -5 이면 [급락] 배지
def _run_with_velocity(disc, vel5d, ddPct=None) -> str:
    src = _extract_entry_label_src()
    script = (
        src
        + f"\nconst out = _entryLabel('STRONG', {_to_js(disc)}, null, null, "
        + f"{_to_js(ddPct)}, null, null, null, null, {_to_js(vel5d)});\n"
        + "process.stdout.write(Buffer.from(out, 'utf8'));\n"
    )
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("node not available")
    res = subprocess.run([node_exe, "-e", script], capture_output=True, check=True)
    return res.stdout.decode("utf-8")


@pytest.mark.parametrize(
    "vel5d,expected_suffix",
    [
        (-8, "[급락]"),       # 5일간 -8%p → 급락
        (-5.1, "[급락]"),     # 경계값 초과 → 급락
        (-4.9, None),         # 경계값 미만 → 급락 아님
        (0, None),            # 변동 없음
    ],
)
def test_velocity_badge(vel5d, expected_suffix):
    out = _run_with_velocity(0.5, vel5d)
    if expected_suffix:
        assert out.endswith(expected_suffix), f"expected '{expected_suffix}' in '{out}'"
    else:
        assert "[급락]" not in out, f"unexpected [급락] in '{out}'"


def test_velocity_overrides_drawdown_badge():
    # P13: 급락이 고위험보다 우선 — vel=-8 + dd=-35% → [급락]만 표시
    out = _run_with_velocity(0.5, -8, ddPct=-35)
    assert out.endswith("[급락]"), f"velocity should override drawdown badge: '{out}'"
    assert "[고위험]" not in out
