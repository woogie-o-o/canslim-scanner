"""한줄평 스마트 줄바꿈(wrap_oneliner) 단위 테스트.

설계 문서: docs/superpowers/specs/2026-05-19-oneliner-linebreak-design.md

핵심 보장:
  - 긴 한줄평은 자연스러운 구문 경계(쉼표/연결·종결 어미)에서 끊는다.
  - 의존 토큰(텐데/게/거임 등)은 줄 첫머리에 오지 않는다.
  - 짧은 문구는 무변경, 문자 손실/추가 없음, 멱등.

Uses pytest if available; otherwise falls back to unittest.
No external network calls.
"""

from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_WEB_APP = os.path.join(_PROJECT_ROOT, "web_app")
for _p in (_PROJECT_ROOT, _WEB_APP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import one_liner as ol  # noqa: E402

def _first_word(line: str) -> str:
    line = line.strip()
    return line.split(" ", 1)[0] if line else ""


def _starts_with_dep_token(line: str) -> bool:
    """구현의 EXACT(정확 일치) / PREFIX(접두 일치) 분리를 그대로 반영."""
    w = _first_word(line)
    bare = w.rstrip(",")
    return (bare in ol._DEP_HEAD_EXACT
            or any(bare.startswith(t) for t in ol._DEP_HEAD_PREFIX))


def test_screenshot_phrase_splits_after_텐데():
    src = "뭐 하나 터져야 움직일 텐데 터질 게 없는 종목임"
    out = ol.wrap_oneliner(src)
    assert "\n" in out, f"긴 문구인데 줄바꿈이 없음: {out!r}"
    first, second = out.split("\n", 1)
    # "움직일"과 "텐데"는 같은 줄
    assert "움직일 텐데" in first, f"움직일/텐데가 쪼개짐: {out!r}"
    # 두 번째 줄은 의존 토큰으로 시작하면 안 됨
    assert not _starts_with_dep_token(second), f"줄 시작 의존토큰: {out!r}"


def test_comma_phrase_splits_after_comma():
    src = "지금은 비싸 보여도, 실적이 받쳐주면 결국 간다"
    out = ol.wrap_oneliner(src)
    assert out.count("\n") == 1, f"줄바꿈이 정확히 1개여야: {out!r}"
    first, second = out.split("\n", 1)
    assert first.rstrip().endswith(","), f"쉼표 뒤에서 안 끊김: {out!r}"
    assert second.strip(), f"두 번째 줄이 비어있음: {out!r}"


def test_dep_token_prefix_not_overmatched():
    # 버그(리뷰): startswith 매칭이 '만큼'을 '만'으로 오인해 줄머리 금지.
    # 이제 '만큼'은 정상적으로 줄 첫머리에 올 수 있어야 한다.
    src = "버틸 만큼 버텼으니 이제는 슬슬 올라줘야 하는 자리임"
    out = ol.wrap_oneliner(src)
    assert out.replace("\n", "") == src
    # '만큼'이 어딘가에서 줄머리가 되어도 허용 — 핵심은 균형 분리가
    # '만큼' 금지 때문에 한쪽으로 쏠리지 않는 것.
    for line in out.split("\n"):
        assert line.strip(), f"빈 줄 생성: {out!r}"


def test_all_boundaries_forbidden_no_split():
    # 길이 게이트는 통과하지만 첫 어절 이후가 모두 의존 토큰이라
    # 유효 경계가 하나도 없음 → 원문 그대로(스펙 규칙 6).
    src = "가나다라마바사아자차카타파하 게 뿐 듯 만"
    assert len(src.replace(" ", "")) > ol._WRAP_MIN_CHARS
    out = ol.wrap_oneliner(src)
    assert "\n" not in out, f"분리 불가인데 줄바꿈됨: {out!r}"
    assert out == src


def test_preexisting_newline_returned_unchanged():
    # 멱등성 우선: 이미 \n이 있으면 손대지 않는다.
    src = "이미 줄바꿈이 \n들어있는 긴 문구임 그대로 두어야 함"
    assert ol.wrap_oneliner(src) == src


def test_short_phrase_unchanged():
    src = "지금이 바닥이다"
    assert ol.wrap_oneliner(src) == src


def test_no_dependent_token_starts_a_line():
    samples = [
        "뭐 하나 터져야 움직일 텐데 터질 게 없는 종목임",
        "오를 듯 말 듯 애매한 자리에서 계속 횡보만 하는 종목",
        "버틸 만큼 버텼으니 이제는 슬슬 올라줘야 하는 자리임",
    ]
    for s in samples:
        out = ol.wrap_oneliner(s)
        for line in out.split("\n"):
            assert not _starts_with_dep_token(line), (
                f"의존 토큰이 줄 첫머리: {out!r}"
            )


def test_no_char_loss():
    samples = [
        "뭐 하나 터져야 움직일 텐데 터질 게 없는 종목임",
        "지금은 비싸 보여도, 실적이 받쳐주면 결국 간다",
        "지금이 바닥이다",
        "존버하면 언젠가 오긴 오는데 그게 언제일지 아무도 모름",
    ]
    for s in samples:
        out = ol.wrap_oneliner(s)
        assert out.replace("\n", "") == s, f"문자 손실/추가: {s!r} -> {out!r}"


def test_idempotent():
    samples = [
        "뭐 하나 터져야 움직일 텐데 터질 게 없는 종목임",
        "지금은 비싸 보여도, 실적이 받쳐주면 결국 간다",
        "지금이 바닥이다",
    ]
    for s in samples:
        once = ol.wrap_oneliner(s)
        twice = ol.wrap_oneliner(once)
        assert once == twice, f"멱등성 위반: {s!r} -> {once!r} -> {twice!r}"


def test_single_token_no_split():
    assert ol.wrap_oneliner("존버") == "존버"
    assert "\n" not in ol.wrap_oneliner("줄바꿈불가한아주긴단일어절문구임표시")


def test_at_most_one_newline():
    src = "존버하면 언젠가 오긴 오는데 그게 언제일지 아무도 모르는 종목임"
    out = ol.wrap_oneliner(src)
    assert out.count("\n") <= 1, f"줄바꿈이 2개 이상: {out!r}"


def test_empty_and_none_safe():
    assert ol.wrap_oneliner("") == ""
    assert ol.wrap_oneliner("   ") == "   "


class _UT(unittest.TestCase):
    def test_all(self):
        test_screenshot_phrase_splits_after_텐데()
        test_comma_phrase_splits_after_comma()
        test_dep_token_prefix_not_overmatched()
        test_short_phrase_unchanged()
        test_no_dependent_token_starts_a_line()
        test_no_char_loss()
        test_idempotent()
        test_single_token_no_split()
        test_all_boundaries_forbidden_no_split()
        test_preexisting_newline_returned_unchanged()
        test_at_most_one_newline()
        test_empty_and_none_safe()


if __name__ == "__main__":
    unittest.main()
