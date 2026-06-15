"""Tests for bottleneck_screen — 공급망 병목(scarce-layer) 키워드 근접도 스크리너.

'초벌 패스' 프록시: 사업설명·섹터를 희소층 키워드와 매칭해 병목 근접도(0~100)와
매칭 레이어를 산출. 증거기반 심층판단이 아니라 후보 발굴용.
검증 포인트:
- 상류 희소층(장비·소재·후공정·HBM)은 고점
- 다운스트림/스토리(플랫폼·완성품)는 저점
- 한/영 키워드 모두 매칭
- 무매칭 0, 범위 [0,100]
"""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bottleneck_screen import (  # noqa: E402
    SCARCE_LAYERS,
    SCORECARD_FACTORS,
    SCORECARD_PENALTIES,
    bottleneck_entry_signal,
    bottleneck_proximity,
    build_bottleneck_brief,
)


class TestTaxonomy(unittest.TestCase):
    def test_layers_have_weight_and_keywords(self):
        self.assertTrue(SCARCE_LAYERS)
        for name, spec in SCARCE_LAYERS.items():
            self.assertIn("weight", spec)
            self.assertIn("keywords", spec)
            self.assertGreaterEqual(spec["weight"], 1)
            self.assertLessEqual(spec["weight"], 5)
            self.assertTrue(spec["keywords"])


class TestProximity(unittest.TestCase):
    def test_equipment_material_scores_high(self):
        r = bottleneck_proximity("반도체 식각 장비 및 CMP 슬러리 소재 제조", sector="반도체")
        self.assertGreaterEqual(r["score"], 60)
        self.assertTrue(r["layers"])

    def test_hbm_packaging_scores_high(self):
        r = bottleneck_proximity("HBM 고대역폭메모리 advanced packaging 후공정", sector="반도체")
        self.assertGreaterEqual(r["score"], 60)

    def test_english_keywords_match(self):
        r = bottleneck_proximity("silicon photonics and CPO optical interconnect supplier")
        self.assertGreaterEqual(r["score"], 50)
        self.assertTrue(r["layers"])

    def test_story_or_downstream_scores_low(self):
        r = bottleneck_proximity("AI 플랫폼 서비스 및 광고 매출 중심 인터넷 기업", sector="인터넷")
        self.assertLess(r["score"], 40)

    def test_no_match_is_zero(self):
        r = bottleneck_proximity("일반 소비재 유통 및 식음료 프랜차이즈", sector="유통")
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["layers"], [])

    def test_score_bounded(self):
        r = bottleneck_proximity(
            "HBM 후공정 advanced packaging CMP 식각 etch 포토레지스트 소재 장비 substrate CPO 전력반도체",
            sector="반도체",
        )
        self.assertGreaterEqual(r["score"], 0)
        self.assertLessEqual(r["score"], 100)

    def test_top_layer_reported(self):
        r = bottleneck_proximity("반도체 검사 장비 테스트 핸들러", sector="반도체")
        self.assertIn("top_layer", r)
        self.assertIsNotNone(r["top_layer"])

    def test_empty_text_safe(self):
        r = bottleneck_proximity("", sector="")
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["layers"], [])


class TestBrief(unittest.TestCase):
    """종목별 심층 브리프 — 외부 병목 스킬 스코어카드와 호환되는 prefilled 출력."""

    def _result(self):
        return {
            "Ticker": "000660.KS", "Name": "SK하이닉스", "Sector": "반도체",
            "Desc": "HBM 고대역폭메모리 advanced packaging 후공정",
            "_PER": 12.3, "_PBR": 3.4, "_ROE": 0.61,
            "BottleneckScore": 90,
            "BottleneckLayers": ["메모리/HBM·인터커넥트", "후공정/어드밴스드 패키징"],
            "BottleneckTop": "메모리/HBM·인터커넥트",
        }

    def test_skeleton_has_all_factor_and_penalty_keys(self):
        brief = build_bottleneck_brief(self._result())
        sk = brief["scorecard_skeleton"]
        for k in SCORECARD_FACTORS:
            self.assertIn(k, sk["factors"])
        for k in SCORECARD_PENALTIES:
            self.assertIn(k, sk["penalties"])
        # 팩터는 리서치로 채울 값이라 0으로 시작
        self.assertTrue(all(v == 0 for v in sk["factors"].values()))

    def test_skeleton_identifies_company_and_market(self):
        brief = build_bottleneck_brief(self._result())
        sk = brief["scorecard_skeleton"]
        self.assertEqual(sk["ticker"], "000660.KS")
        self.assertEqual(sk["company"], "SK하이닉스")
        self.assertIn("Korea", sk["market"])  # .KS → 한국

    def test_research_prompt_contains_context(self):
        brief = build_bottleneck_brief(self._result())
        p = brief["research_prompt"]
        self.assertIn("000660.KS", p)
        self.assertIn("반도체", p)
        # 감지된 희소층이 프롬프트에 시드되어야
        self.assertIn("HBM", p)

    def test_us_ticker_market(self):
        brief = build_bottleneck_brief({"Ticker": "NVDA", "Name": "NVIDIA", "Sector": "Semis"})
        self.assertIn("US", brief["scorecard_skeleton"]["market"])

    def test_missing_fields_safe(self):
        brief = build_bottleneck_brief({"Ticker": "X"})
        self.assertEqual(brief["scorecard_skeleton"]["ticker"], "X")
        self.assertIsInstance(brief["research_prompt"], str)
        self.assertTrue(brief["research_prompt"])


class TestKeywordBoundary(unittest.TestCase):
    """짧은 영문 키워드(gan/sic/inp 등)가 단어 내부에 박혀 생기는 오탐 방지."""

    def test_morgan_not_gan(self):
        # "Morgan Stanley"의 gan 이 GaN 으로 오탐되면 안 됨
        r = bottleneck_proximity("Morgan Stanley investment bank", sector="금융")
        self.assertEqual(r["score"], 0)

    def test_asic_not_sic(self):
        # "ASIC"의 sic 이 SiC(전력반도체)로 오탐되면 안 됨
        r = bottleneck_proximity("AI 네트워크 칩 ASIC 설계", sector="반도체")
        self.assertNotIn("전력/냉각 인프라", r["layers"])

    def test_organic_input_not_matched(self):
        r = bottleneck_proximity("organic food company input logistics", sector="")
        self.assertEqual(r["score"], 0)

    def test_real_sic_still_matches(self):
        # 진짜 SiC 는 여전히 매칭돼야
        r = bottleneck_proximity("SiC 전력반도체 웨이퍼", sector="반도체")
        self.assertGreater(r["score"], 0)

    def test_real_gan_still_matches(self):
        r = bottleneck_proximity("GaN 트랜지스터 전력 반도체", sector="반도체")
        self.assertGreater(r["score"], 0)

    def test_real_inp_still_matches(self):
        r = bottleneck_proximity("InP 화합물반도체 기판", sector="반도체")
        self.assertGreater(r["score"], 0)


class TestEntryGate(unittest.TestCase):
    """병목 ∩ 진입타이밍 게이트 — 폭등 꼭대기/과매수는 빼고, 병목+진입자리만 통과."""

    def test_low_bottleneck_not_applicable(self):
        r = bottleneck_entry_signal(bottleneck_score=30, entry_score=70,
                                    rsi=50, rs_rating=80, mom_3m=10)
        self.assertFalse(r["applicable"])
        self.assertFalse(r["pass_gate"])

    def test_parabola_is_wait_not_buy(self):
        # AXTI류: 병목 만점이나 3M +175% 폭등 → 조정대기(게이트 불통과)
        r = bottleneck_entry_signal(bottleneck_score=100, entry_score=60,
                                    rsi=60, rs_rating=90, mom_3m=175)
        self.assertTrue(r["applicable"])
        self.assertFalse(r["pass_gate"])
        self.assertIn("조정", r["label"])

    def test_overbought_is_wait(self):
        r = bottleneck_entry_signal(bottleneck_score=90, entry_score=70,
                                    rsi=78, rs_rating=85, mom_3m=20)
        self.assertFalse(r["pass_gate"])

    def test_good_timing_passes(self):
        # 병목 높음 + 모멘텀 과하지 않음 + 과매수 아님 + 진입점수 양호 → 통과
        r = bottleneck_entry_signal(bottleneck_score=85, entry_score=65,
                                    rsi=55, rs_rating=70, mom_3m=18)
        self.assertTrue(r["pass_gate"])
        self.assertIn("진입", r["label"])

    def test_laggard_does_not_pass(self):
        r = bottleneck_entry_signal(bottleneck_score=85, entry_score=30,
                                    rsi=45, rs_rating=25, mom_3m=5)
        self.assertFalse(r["pass_gate"])

    def test_missing_fields_safe(self):
        r = bottleneck_entry_signal(bottleneck_score=80, entry_score=None,
                                    rsi=None, rs_rating=None, mom_3m=None)
        self.assertTrue(r["applicable"])
        self.assertIn("label", r)
        self.assertIsInstance(r["pass_gate"], bool)


if __name__ == "__main__":
    unittest.main()
