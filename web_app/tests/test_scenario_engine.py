"""Unit tests for web_app.scenario_engine module.

Tests cover:
- compute_scenario_scores: signal-to-scenario scoring logic
- generate_active_triggers: condition-based trigger extraction
- build_scenario_table: full structure assembly
"""
import unittest

from web_app.scenario_engine import (
    compute_scenario_scores,
    generate_active_triggers,
    build_scenario_table,
)


class TestComputeScenarioScores(unittest.TestCase):
    """Tests for compute_scenario_scores signal contributions."""

    def test_high_quality_bull(self):
        """TotalScore >= 70 (S/A grade) -> +25 to bull_score raw contribution."""
        stock = {'TotalScore': 80, 'EntryScore': 50, 'RSI': 50, 'Regime': 'SIDEWAYS'}
        result = compute_scenario_scores(stock)
        # TotalScore 80 >= 70 triggers +25 bull contribution
        bull_contribution = [c for c in result['contributions']
                            if c['impact'] == 25 and '퀄리티' in c['name']]
        self.assertEqual(len(bull_contribution), 1)

    def test_entry_signal_bull(self):
        """EntryScore >= 50 -> +20 to bull_score raw contribution."""
        stock = {'TotalScore': 50, 'EntryScore': 60, 'RSI': 50, 'Regime': 'SIDEWAYS'}
        result = compute_scenario_scores(stock)
        entry_contribution = [c for c in result['contributions']
                             if c['impact'] == 20 and '진입' in c['name']]
        self.assertEqual(len(entry_contribution), 1)

    def test_overbought_bear(self):
        """RSI > 75 (overbought) -> +15 to bear_score raw contribution."""
        stock = {'TotalScore': 50, 'EntryScore': 50, 'RSI': 80, 'Regime': 'SIDEWAYS'}
        result = compute_scenario_scores(stock)
        rsi_contribution = [c for c in result['contributions']
                           if c['impact'] == -15 and '과매수' in c['name']]
        self.assertEqual(len(rsi_contribution), 1)

    def test_oversold_bull(self):
        """RSI < 30 (oversold) -> +15 to bull_score raw contribution."""
        stock = {'TotalScore': 50, 'EntryScore': 50, 'RSI': 25, 'Regime': 'SIDEWAYS'}
        result = compute_scenario_scores(stock)
        rsi_contribution = [c for c in result['contributions']
                           if c['impact'] == 15 and '과매도' in c['name']]
        self.assertEqual(len(rsi_contribution), 1)

    def test_strong_bull_regime(self):
        """Regime STRONG_BULL -> +15 to bull_score."""
        stock = {'TotalScore': 50, 'EntryScore': 50, 'RSI': 50, 'Regime': 'STRONG_BULL'}
        result = compute_scenario_scores(stock)
        regime_contribution = [c for c in result['contributions']
                              if c['impact'] == 15 and 'STRONG_BULL' in c['name']]
        self.assertEqual(len(regime_contribution), 1)

    def test_strong_bear_regime(self):
        """Regime STRONG_BEAR -> +20 to bear_score."""
        stock = {'TotalScore': 50, 'EntryScore': 50, 'RSI': 50, 'Regime': 'STRONG_BEAR'}
        result = compute_scenario_scores(stock)
        regime_contribution = [c for c in result['contributions']
                              if c['impact'] == -20 and 'STRONG_BEAR' in c['name']]
        self.assertEqual(len(regime_contribution), 1)

    def test_scenario_sum_100(self):
        """Bull + neutral + bear must always sum to exactly 100%."""
        test_cases = [
            {'TotalScore': 90, 'EntryScore': 80, 'RSI': 20, 'Regime': 'STRONG_BULL'},
            {'TotalScore': 30, 'EntryScore': 10, 'RSI': 85, 'Regime': 'STRONG_BEAR'},
            {'TotalScore': 55, 'EntryScore': 50, 'RSI': 50, 'Regime': 'SIDEWAYS'},
            {'TotalScore': 50, 'EntryScore': 50, 'RSI': 50, 'Regime': 'SIDEWAYS'},
            {},  # All defaults
        ]
        for stock in test_cases:
            result = compute_scenario_scores(stock)
            total = result['bull'] + result['neutral'] + result['bear']
            self.assertEqual(total, 100,
                            f"Sum {total} != 100 for stock {stock}")

    def test_clamping_prevents_overconfidence(self):
        """Even with extreme inputs, each scenario stays within 10~70%."""
        # Extremely bullish
        stock_bull = {
            'TotalScore': 95, 'EntryScore': 90, 'RSI': 20,
            'Regime': 'STRONG_BULL', 'PriceInLevel': 10, 'SectorRelPE': -50,
        }
        result = compute_scenario_scores(stock_bull)
        for key in ('bull', 'neutral', 'bear'):
            self.assertGreaterEqual(result[key], 10,
                                   f"{key}={result[key]} below 10%")
            self.assertLessEqual(result[key], 70,
                                f"{key}={result[key]} above 70%")

        # Extremely bearish
        stock_bear = {
            'TotalScore': 20, 'EntryScore': 10, 'RSI': 90,
            'Regime': 'STRONG_BEAR', 'PriceInLevel': 90, 'SectorRelPE': 50,
        }
        result = compute_scenario_scores(stock_bear)
        for key in ('bull', 'neutral', 'bear'):
            self.assertGreaterEqual(result[key], 10,
                                   f"{key}={result[key]} below 10%")
            self.assertLessEqual(result[key], 70,
                                f"{key}={result[key]} above 70%")

    def test_key_variables_top3(self):
        """key_variables should contain at most 3 items."""
        stock = {
            'TotalScore': 80, 'EntryScore': 60, 'RSI': 25,
            'Regime': 'STRONG_BULL', 'PriceInLevel': 20, 'SectorRelPE': -30,
        }
        result = compute_scenario_scores(stock)
        self.assertLessEqual(len(result['key_variables']), 3)

    def test_phase1_fallback(self):
        """Without PriceInLevel/SectorRelPE, still works with 4 core signals."""
        stock = {'TotalScore': 70, 'EntryScore': 55, 'RSI': 45, 'Regime': 'BULL'}
        result = compute_scenario_scores(stock)
        self.assertIn('bull', result)
        self.assertIn('neutral', result)
        self.assertIn('bear', result)
        self.assertEqual(result['bull'] + result['neutral'] + result['bear'], 100)


class TestGenerateActiveTriggers(unittest.TestCase):
    """Tests for generate_active_triggers."""

    def test_generate_active_triggers_bull(self):
        """Bullish conditions met -> bull triggers non-empty."""
        stock = {
            'TotalScore': 80,
            'EntryScore': 60,
            'RSI': 50,
            'Regime': 'STRONG_BULL',
            'PriceInLevel': 20,
        }
        triggers = generate_active_triggers(stock)
        self.assertIsInstance(triggers['bull'], list)
        self.assertGreater(len(triggers['bull']), 0)

    def test_generate_active_triggers_bear(self):
        """Bearish conditions met -> bear triggers non-empty."""
        stock = {
            'TotalScore': 30,
            'EntryScore': 20,
            'RSI': 80,
            'Regime': 'STRONG_BEAR',
            'PriceInLevel': 80,
            'SectorRelPE': 40,
        }
        triggers = generate_active_triggers(stock)
        self.assertIsInstance(triggers['bear'], list)
        self.assertGreater(len(triggers['bear']), 0)


class TestBuildScenarioTable(unittest.TestCase):
    """Tests for build_scenario_table full structure."""

    def test_build_scenario_table_structure(self):
        """Return value must contain scores, triggers, responses keys."""
        stock = {
            'TotalScore': 65,
            'EntryScore': 50,
            'RSI': 50,
            'Regime': 'SIDEWAYS',
        }
        result = build_scenario_table(stock)
        self.assertIn('scores', result)
        self.assertIn('triggers', result)
        self.assertIn('responses', result)
        # scores sub-keys
        self.assertIn('bull', result['scores'])
        self.assertIn('neutral', result['scores'])
        self.assertIn('bear', result['scores'])
        # triggers sub-keys
        self.assertIn('bull', result['triggers'])
        self.assertIn('neutral', result['triggers'])
        self.assertIn('bear', result['triggers'])


if __name__ == '__main__':
    unittest.main()
