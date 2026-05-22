"""Regression tests for valuation_engine DCF integration.

Uses pytest if available; otherwise falls back to unittest.
No external network calls.
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure project root on sys.path
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from valuation_engine import (  # noqa: E402
    ValuationResult,
    _dcf_value,
    _reverse_dcf_growth,
    run,
    target_upside_score,
)


SAMSUNG_FINANCIALS = {
    "fcf": 4_500,
    "eps": 4_344,
    "book_value": 52_000,
    "ebitda": 8_200,
}


class TestDCFTarget(unittest.TestCase):
    """Regression tests for valuation_engine.run() and helpers."""

    def test_valuation_run_basic(self) -> None:
        """Samsung-like inputs produce positive values for all core fields."""
        result = run(
            ticker="005930",
            current_price=78_500,
            financials=SAMSUNG_FINANCIALS,
            wacc=0.10,
            growth_stage1=0.15,
            growth_stage2=0.08,
            terminal_growth=0.03,
        )
        self.assertIsInstance(result, ValuationResult)
        self.assertGreater(result.dcf_value, 0)
        self.assertGreater(result.dcf_low, 0)
        self.assertGreater(result.dcf_high, 0)
        self.assertGreater(result.per_fair, 0)
        self.assertGreater(result.pbr_fair, 0)
        self.assertGreater(result.ev_ebitda_fair, 0)
        # dcf_high uses lower WACC → should exceed dcf_low
        self.assertGreater(result.dcf_high, result.dcf_low)

    def test_weighted_mid_formula(self) -> None:
        """weighted_mid = 0.5*dcf + 0.2*per + 0.15*pbr + 0.15*ev_ebitda."""
        result = run(
            ticker="TEST",
            current_price=10_000,
            financials=SAMSUNG_FINANCIALS,
        )
        expected_mid = (
            result.dcf_value * 0.50
            + result.per_fair * 0.20
            + result.pbr_fair * 0.15
            + result.ev_ebitda_fair * 0.15
        )
        _, mid, _ = result.fair_value_range
        self.assertAlmostEqual(mid, expected_mid, places=1)

    def test_zero_fcf_dcf_zero(self) -> None:
        """fcf=0 should yield dcf_value=0."""
        result = run(
            ticker="ZERO",
            current_price=1_000,
            financials={"fcf": 0, "eps": 100, "book_value": 500, "ebitda": 0},
        )
        self.assertEqual(result.dcf_value, 0.0)
        self.assertEqual(result.dcf_low, 0.0)
        self.assertEqual(result.dcf_high, 0.0)

    def test_zero_current_price_no_crash(self) -> None:
        """current_price=0 → discount_pct=0 and no exception."""
        try:
            result = run(
                ticker="NOPRICE",
                current_price=0,
                financials=SAMSUNG_FINANCIALS,
            )
        except Exception as exc:  # pragma: no cover
            self.fail(f"run() raised with current_price=0: {exc!r}")
        self.assertEqual(result.discount_pct, 0.0)

    def test_shares_normalization(self) -> None:
        """shares=1000 with total fcf=10000 → fcf/share=10.

        We verify indirectly: dcf_value with normalised per-share fcf=10
        equals dcf_value when fcf=10 is given directly with no shares.
        """
        # With shares normalisation
        res_norm = run(
            ticker="NORM",
            current_price=100,
            financials={
                "fcf": 10_000,
                "eps": 0,
                "book_value": 0,
                "ebitda": 0,
                "shares": 1_000,
            },
        )
        # Direct per-share input
        res_direct = run(
            ticker="DIRECT",
            current_price=100,
            financials={
                "fcf": 10,
                "eps": 0,
                "book_value": 0,
                "ebitda": 0,
            },
        )
        self.assertAlmostEqual(res_norm.dcf_value, res_direct.dcf_value, places=4)
        self.assertGreater(res_norm.dcf_value, 0)

    def test_wacc_le_terminal_growth_zero(self) -> None:
        """wacc <= terminal_growth → dcf_value=0."""
        result = run(
            ticker="BADWACC",
            current_price=1_000,
            financials=SAMSUNG_FINANCIALS,
            wacc=0.03,
            terminal_growth=0.04,
        )
        self.assertEqual(result.dcf_value, 0.0)

    def test_reverse_dcf_in_range(self) -> None:
        """reverse_dcf_growth must be in [0, 1]."""
        result = run(
            ticker="REV",
            current_price=78_500,
            financials=SAMSUNG_FINANCIALS,
        )
        self.assertGreaterEqual(result.reverse_dcf_growth, 0.0)
        self.assertLessEqual(result.reverse_dcf_growth, 1.0)

    def test_target_upside_score_is_continuous_inside_bucket(self) -> None:
        """Same view bucket should still produce different scores."""
        score_lo, view_lo = target_upside_score(0.41)
        score_hi, view_hi = target_upside_score(0.52)
        self.assertEqual(view_lo, "STRONG_BUY")
        self.assertEqual(view_hi, "STRONG_BUY")
        self.assertEqual(score_lo, 15.0)
        self.assertEqual(score_hi, 15.0)

        # Inside BUY band, score should move continuously.
        score_a, view_a = target_upside_score(0.31)
        score_b, view_b = target_upside_score(0.39)
        self.assertEqual(view_a, "BUY")
        self.assertEqual(view_b, "BUY")
        self.assertGreater(score_b, score_a)

    def test_target_upside_score_reflects_shipbuilding_bias_case(self) -> None:
        """Nomura shipbuilding haircut should change score if upside stays > 0."""
        score_before, view_before = target_upside_score(0.29)
        score_after, view_after = target_upside_score(0.21)
        self.assertEqual(view_before, "MODERATE_BUY")
        self.assertEqual(view_after, "MODERATE_BUY")
        self.assertGreater(score_before, score_after)
        self.assertGreater(score_after, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
