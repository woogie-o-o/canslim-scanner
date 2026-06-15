"""Tests for bottleneck_consensus — 유니버스 병목 후보 추출 + 6렌즈 합의 패널 브리프.

결정적(코드) 부분만 검증: 후보 추출/정렬/필터, 패널 스펙(6렌즈·후보블록·의장 프롬프트).
실제 LLM 토론은 on-demand(Workflow)라 단위테스트 대상 아님.
"""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bottleneck_consensus import (  # noqa: E402
    PANEL_LENSES,
    build_consensus_panel,
    top_bottleneck_candidates,
)


class TestLenses(unittest.TestCase):
    def test_six_lenses_with_required_fields(self):
        self.assertEqual(len(PANEL_LENSES), 6)
        for L in PANEL_LENSES:
            self.assertTrue(L["label"])
            self.assertTrue(L["dir"])
            self.assertTrue(L["focus"])


class TestCandidates(unittest.TestCase):
    def _universe(self):
        return {
            "AXTI": "InP·GaAs 화합물반도체 기판 substrate · CPO",
            "SOLB": "CMP 슬러리 식각액 소재",
            "PLT": "AI 플랫폼 광고 서비스",          # 스토리 → 0점
            "CAN": "알루미늄 식품 캔 패키징",          # 거짓양성이지만 '패키징' 매칭됨
            "FOOD": "식음료 유통 프랜차이즈",           # 0점
        }

    def test_filters_min_score_and_sorts_desc(self):
        cands = top_bottleneck_candidates(self._universe(), n=20, min_score=60)
        scores = [c["score"] for c in cands]
        self.assertEqual(scores, sorted(scores, reverse=True))  # 내림차순
        for c in cands:
            self.assertGreaterEqual(c["score"], 60)
        tickers = {c["ticker"] for c in cands}
        self.assertIn("AXTI", tickers)
        self.assertNotIn("FOOD", tickers)   # 0점 제외
        self.assertNotIn("PLT", tickers)    # 스토리 제외

    def test_n_limit(self):
        cands = top_bottleneck_candidates(self._universe(), n=1, min_score=1)
        self.assertEqual(len(cands), 1)

    def test_candidate_has_layer_info(self):
        cands = top_bottleneck_candidates({"AXTI": "InP 화합물반도체 기판 substrate"}, min_score=1)
        self.assertTrue(cands)
        self.assertIn("top_layer", cands[0])
        self.assertIn("desc", cands[0])


class TestPanelBrief(unittest.TestCase):
    def _cands(self):
        return [
            {"ticker": "AXTI", "desc": "InP 기판", "score": 100, "top_layer": "화합물반도체", "layers": ["화합물반도체"]},
            {"ticker": "POET", "desc": "실리콘 포토닉스", "score": 90, "top_layer": "광통신", "layers": ["광통신"]},
        ]

    def test_panel_has_six_lens_prompts(self):
        panel = build_consensus_panel(self._cands(), pack_dir="/tmp/pack")
        self.assertEqual(len(panel["lenses"]), 6)
        for L in panel["lenses"]:
            self.assertIn("skill_path", L)
            self.assertIn("round1_prompt", L)
            self.assertIn("/tmp/pack", L["skill_path"])

    def test_candidate_block_contains_tickers(self):
        panel = build_consensus_panel(self._cands(), pack_dir="/tmp/pack")
        self.assertIn("AXTI", panel["candidate_block"])
        self.assertIn("POET", panel["candidate_block"])

    def test_round1_prompt_embeds_path_and_candidates(self):
        panel = build_consensus_panel(self._cands(), pack_dir="/tmp/pack")
        p = panel["lenses"][0]["round1_prompt"]
        self.assertIn("AXTI", p)
        self.assertIn(panel["lenses"][0]["skill_path"], p)

    def test_chair_prompt_nonempty(self):
        panel = build_consensus_panel(self._cands(), pack_dir="/tmp/pack")
        self.assertIsInstance(panel["chair_prompt"], str)
        self.assertTrue(panel["chair_prompt"].strip())


if __name__ == "__main__":
    unittest.main()
