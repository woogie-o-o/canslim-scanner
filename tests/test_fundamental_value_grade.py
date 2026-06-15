"""Tests for the fundamental value grade scorer (Phase 1, naver-only 60%).

순수 채점 함수 — 네트워크 無. 검증 포인트:
- 정순 지표(ROE·성장): 값↑ → 등급↑
- 역순 지표(PBR·PSR): 값↓ → 등급↑ (저평가 = 고득점)
- 결측(None) 지표: 해당 종목 비중을 가용 지표로 재정규화 (0점 왜곡 금지)
- basis="sector": 섹터 내에서만 백분위
- 동률: 같은 등급
- 등급 범위 [0, 100]
- 기본 가중치 합 = 1.0
"""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fundamental_value_grade import (  # noqa: E402
    DEFAULT_WEIGHTS,
    INVERTED_METRICS,
    apply_grades,
    compute_grades,
)


def _rec(ticker, sector="A", **metrics):
    base = {"ticker": ticker, "sector": sector}
    base.update(metrics)
    return base


class TestDefaultWeights(unittest.TestCase):
    def test_default_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(DEFAULT_WEIGHTS.values()), 1.0, places=6)

    def test_inverted_metrics_are_valuation_ratios(self) -> None:
        # 낮을수록 좋은 것만 역순: PBR·PSR
        self.assertIn("pbr", INVERTED_METRICS)
        self.assertIn("psr", INVERTED_METRICS)
        self.assertNotIn("roe", INVERTED_METRICS)


class TestDirection(unittest.TestCase):
    def test_higher_roe_ranks_higher(self) -> None:
        recs = [
            _rec("LOW", roe=0.05),
            _rec("MID", roe=0.15),
            _rec("TOP", roe=0.25),
        ]
        g = compute_grades(recs, basis="universe")
        self.assertLess(g["LOW"]["grade"], g["MID"]["grade"])
        self.assertLess(g["MID"]["grade"], g["TOP"]["grade"])

    def test_lower_pbr_ranks_higher(self) -> None:
        # 역순: 싼(낮은) PBR 이 높은 등급
        recs = [
            _rec("CHEAP", pbr=0.5),
            _rec("MID", pbr=1.5),
            _rec("RICH", pbr=3.0),
        ]
        g = compute_grades(recs, basis="universe")
        self.assertGreater(g["CHEAP"]["grade"], g["RICH"]["grade"])

    def test_lower_psr_ranks_higher(self) -> None:
        recs = [
            _rec("CHEAP", psr=0.8),
            _rec("RICH", psr=12.0),
        ]
        g = compute_grades(recs, basis="universe")
        self.assertGreater(g["CHEAP"]["grade"], g["RICH"]["grade"])


class TestMissingMetric(unittest.TestCase):
    def test_none_metric_redistributes_weight(self) -> None:
        # ROE 만 있는 종목도 0점 처리되지 않고 가용 지표로 재정규화되어야.
        recs = [
            _rec("ONLY_ROE", roe=0.30, pbr=None, psr=None,
                 rev_qoq=None, op_qoq=None, ni_qoq=None),
            _rec("ONLY_ROE_LOW", roe=0.01, pbr=None, psr=None,
                 rev_qoq=None, op_qoq=None, ni_qoq=None),
        ]
        g = compute_grades(recs, basis="universe")
        # 가용 지표(roe) 단독으로 순위가 매겨져야 — 높은 ROE 가 더 높은 등급
        self.assertGreater(g["ONLY_ROE"]["grade"], g["ONLY_ROE_LOW"]["grade"])
        # 0점 왜곡 금지: 최상위는 만점에 근접
        self.assertGreater(g["ONLY_ROE"]["grade"], 90.0)

    def test_all_metrics_none_is_neutral_not_zero(self) -> None:
        recs = [
            _rec("BLANK", roe=None, pbr=None, psr=None,
                 rev_qoq=None, op_qoq=None, ni_qoq=None),
        ]
        g = compute_grades(recs, basis="universe")
        # 가용 지표가 전무하면 중립(50) 처리 — 0점으로 떨어뜨리지 않음
        self.assertAlmostEqual(g["BLANK"]["grade"], 50.0, delta=1e-6)


class TestSectorBasis(unittest.TestCase):
    def test_sector_basis_ranks_within_sector(self) -> None:
        # 섹터 A 내부에서는 0.10 이 최저 → 낮은 등급,
        # 섹터 B 내부에서는 0.10 이 최고 → 높은 등급.
        recs = [
            _rec("A_LOW", sector="A", roe=0.10),
            _rec("A_HIGH", sector="A", roe=0.40),
            _rec("B_LOW", sector="B", roe=0.02),
            _rec("B_HIGH", sector="B", roe=0.10),
        ]
        g = compute_grades(recs, basis="sector")
        # 동일 ROE(0.10)이지만 섹터 내 위치가 반대 → 등급도 반대
        self.assertLess(g["A_LOW"]["grade"], g["B_HIGH"]["grade"])


class TestTiesAndBounds(unittest.TestCase):
    def test_identical_records_get_equal_grades(self) -> None:
        recs = [
            _rec("X", roe=0.2, pbr=1.0, psr=2.0),
            _rec("Y", roe=0.2, pbr=1.0, psr=2.0),
        ]
        g = compute_grades(recs, basis="universe")
        self.assertAlmostEqual(g["X"]["grade"], g["Y"]["grade"], places=6)

    def test_grade_bounded_0_100(self) -> None:
        recs = [
            _rec("A", roe=0.5, pbr=0.3, psr=0.5, rev_qoq=2.0, op_qoq=2.0, ni_qoq=2.0),
            _rec("B", roe=-0.5, pbr=20.0, psr=40.0, rev_qoq=-0.9, op_qoq=-0.9, ni_qoq=-0.9),
            _rec("C", roe=0.1, pbr=1.0, psr=2.0, rev_qoq=0.1, op_qoq=0.1, ni_qoq=0.1),
        ]
        g = compute_grades(recs, basis="universe")
        for t in ("A", "B", "C"):
            self.assertGreaterEqual(g[t]["grade"], 0.0)
            self.assertLessEqual(g[t]["grade"], 100.0)


class TestApplyGrades(unittest.TestCase):
    """스캐너 통합용 헬퍼 — fetcher 주입으로 GUI/네트워크 없이 검증."""

    def _results(self):
        # 동일 섹터, 동일 시총/PBR → ROE 만 차등 → ROE 가 등급 견인
        return [
            {"Ticker": "A.KS", "Sector": "반도체", "_MarketCap": 1e12,
             "_PBR": 1.5, "ValueScore": 10, "QualityScore": 20},
            {"Ticker": "B.KS", "Sector": "반도체", "_MarketCap": 1e12,
             "_PBR": 1.5, "ValueScore": 50, "QualityScore": 60},
            {"Ticker": "C.KS", "Sector": "반도체", "_MarketCap": 1e12,
             "_PBR": 1.5, "ValueScore": 90, "QualityScore": 95},
        ]

    def _qfetch(self, mapping):
        def f(code):
            return mapping.get(code.split(".")[0], {"available": False})
        return f

    def test_apply_grades_assigns_finvalue(self):
        results = self._results()
        qmap = {
            "A": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 5.0, "pbr": 1.5},
            "B": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 15.0, "pbr": 1.5},
            "C": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 30.0, "pbr": 1.5},
        }
        apply_grades(results, quarter_fetch=self._qfetch(qmap),
                     ttm_fetch=lambda c: {"revenue": 1e12})
        by = {r["Ticker"]: r for r in results}
        for t in ("A.KS", "B.KS", "C.KS"):
            self.assertIsNotNone(by[t]["FinValue"])
            self.assertGreaterEqual(by[t]["FinValue"], 0.0)
            self.assertLessEqual(by[t]["FinValue"], 100.0)
            self.assertIn("FinValueSec", by[t])
        # ROE 높을수록 등급 ↑
        self.assertLess(by["A.KS"]["FinValue"], by["C.KS"]["FinValue"])

    def test_apply_grades_skips_unavailable(self):
        results = self._results()
        qmap = {
            "A": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 5.0, "pbr": 1.5},
            "B": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 15.0, "pbr": 1.5},
            # C 결측
        }
        apply_grades(results, quarter_fetch=self._qfetch(qmap),
                     ttm_fetch=lambda c: {"revenue": 1e12})
        by = {r["Ticker"]: r for r in results}
        self.assertIsNone(by["C.KS"]["FinValue"])

    def test_psr_inverted_lower_marketcap_scores_higher(self):
        # ROE/PBR/QoQ 동일, 시총만 차등 → 매출 동일이면 낮은 시총 = 낮은 PSR = 고득점
        results = [
            {"Ticker": "CHEAP.KS", "Sector": "X", "_MarketCap": 1e11, "_PBR": 1.5},
            {"Ticker": "RICH.KS", "Sector": "X", "_MarketCap": 5e12, "_PBR": 1.5},
        ]
        qmap = {
            "CHEAP": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 10.0, "pbr": 1.5},
            "RICH": {"available": True, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1, "roe": 10.0, "pbr": 1.5},
        }
        apply_grades(results, quarter_fetch=self._qfetch(qmap),
                     ttm_fetch=lambda c: {"revenue": 1e12})
        by = {r["Ticker"]: r for r in results}
        self.assertGreater(by["CHEAP.KS"]["FinValue"], by["RICH.KS"]["FinValue"])

    def test_apply_grades_returns_correlation_summary(self):
        results = self._results()
        qmap = {
            "A": {"available": True, "roe": 5.0, "pbr": 1.5, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1},
            "B": {"available": True, "roe": 15.0, "pbr": 1.5, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1},
            "C": {"available": True, "roe": 30.0, "pbr": 1.5, "rev_qoq": 0.1, "op_qoq": 0.1, "ni_qoq": 0.1},
        }
        summary = apply_grades(results, quarter_fetch=self._qfetch(qmap),
                               ttm_fetch=lambda c: {"revenue": 1e12})
        self.assertEqual(summary["n"], 3)
        self.assertIn("pearson_value", summary)
        self.assertIn("pearson_quality", summary)


if __name__ == "__main__":
    unittest.main()
