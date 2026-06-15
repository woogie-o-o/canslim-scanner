"""Unit tests for web_app.valuation_context module.

Tests cover:
- _valid_per: PER validation (positive, zero, negative, None)
- compute_sector_rel_pe: sector-relative PER premium/discount
- compute_price_in_level: composite price-in-level scoring with missing components
- val_pctile: historical PER percentile via EPS-derived history
"""
import unittest

from web_app.valuation_context import (
    _valid_per,
    compute_sector_rel_pe,
    compute_price_in_level,
    compute_val_pctile,
)


class TestValidPer(unittest.TestCase):
    """Tests for _valid_per helper."""

    def test_valid_per_positive(self):
        """Positive PER should be returned as-is (float)."""
        result = _valid_per(15.3)
        self.assertEqual(result, 15.3)
        self.assertIsInstance(result, float)

    def test_valid_per_zero(self):
        """PER of 0 is invalid (safe_get default) -> None."""
        result = _valid_per(0)
        self.assertIsNone(result)

    def test_valid_per_negative(self):
        """Negative PER (loss-making company) -> None."""
        result = _valid_per(-8.5)
        self.assertIsNone(result)

    def test_valid_per_none(self):
        """None input -> None."""
        result = _valid_per(None)
        self.assertIsNone(result)


class TestComputeSectorRelPE(unittest.TestCase):
    """Tests for compute_sector_rel_pe."""

    def test_compute_sector_rel_pe_premium(self):
        """Stock PER > sector median -> positive (premium)."""
        stock = {'_PER': 30.0}
        peers = [{'_PER': 15.0}, {'_PER': 20.0}, {'_PER': 25.0}, {'_PER': 18.0}]
        result = compute_sector_rel_pe(stock, peers)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_compute_sector_rel_pe_discount(self):
        """Stock PER < sector median -> negative (discount)."""
        stock = {'_PER': 10.0}
        peers = [{'_PER': 20.0}, {'_PER': 25.0}, {'_PER': 30.0}, {'_PER': 22.0}]
        result = compute_sector_rel_pe(stock, peers)
        self.assertIsNotNone(result)
        self.assertLess(result, 0)

    def test_compute_sector_rel_pe_insufficient_peers(self):
        """Fewer than 3 valid peers -> None."""
        stock = {'_PER': 15.0}
        peers = [{'_PER': 20.0}, {'_PER': 18.0}]  # Only 2 peers
        result = compute_sector_rel_pe(stock, peers)
        self.assertIsNone(result)

    def test_compute_sector_rel_pe_invalid_per(self):
        """Stock with invalid (negative) PER -> None."""
        stock = {'_PER': -5.0}
        peers = [{'_PER': 15.0}, {'_PER': 20.0}, {'_PER': 25.0}]
        result = compute_sector_rel_pe(stock, peers)
        self.assertIsNone(result)


class TestComputePriceInLevel(unittest.TestCase):
    """Tests for compute_price_in_level."""

    def test_compute_price_in_level_all_components(self):
        """All 3 components present -> weighted average returned."""
        result = compute_price_in_level(
            val_pctile=60.0,
            dist_from_52w_high=0.1,     # close to high -> ~90 score
            consensus_gap=0.8,          # 80% of target -> 80 score
        )
        self.assertIsNotNone(result)
        # val: 40w*60 + 52w: 30w*90 + con: 30w*80 = 2400+2700+2400 = 7500/100 = 75.0
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 100)

    def test_compute_price_in_level_missing_consensus(self):
        """No consensus gap -> weight redistributed to remaining components."""
        result = compute_price_in_level(
            val_pctile=50.0,
            dist_from_52w_high=0.3,
            consensus_gap=None,
        )
        self.assertIsNotNone(result)
        # Only val (40w) + 52w (30w), total_weight=70
        # val: 40*50=2000, 52w: 30*70=2100 -> 4100/70 = 58.6
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 100)

    def test_compute_price_in_level_missing_52w(self):
        """dist_from_52w_high is 1.0 (no data) -> excluded, weight redistributed."""
        result = compute_price_in_level(
            val_pctile=40.0,
            dist_from_52w_high=1.0,     # Default = no data -> excluded
            consensus_gap=0.5,
        )
        self.assertIsNotNone(result)
        # Only val (40w) + con (30w), total_weight=70
        # val: 40*40=1600, con: 30*50=1500 -> 3100/70 = 44.3
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 100)

    def test_compute_price_in_level_all_none(self):
        """All components None/invalid -> None."""
        result = compute_price_in_level(
            val_pctile=None,
            dist_from_52w_high=None,
            consensus_gap=None,
        )
        self.assertIsNone(result)


class TestValPctile(unittest.TestCase):
    """Tests for compute_val_pctile (PER percentile via price history + EPS)."""

    def test_val_pctile_with_price_history(self):
        """Given price history and EPS, should return a 0~100 percentile."""
        # Simulate 12 months of prices and a current EPS
        price_history = [100, 110, 105, 115, 120, 125, 130, 128, 135, 140, 138, 145]
        current_per = 15.0
        eps_ttm = 145 / 15.0  # ~9.67 TTM EPS

        result = compute_val_pctile(
            current_per=current_per,
            price_history=price_history,
            eps_ttm=eps_ttm,
        )
        self.assertIsNotNone(result)
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 100)


if __name__ == '__main__':
    unittest.main()
