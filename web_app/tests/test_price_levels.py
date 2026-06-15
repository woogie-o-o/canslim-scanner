"""Unit tests for web_app.price_levels module.

Tests cover:
- compute_price_levels: entry/stop-loss/fibonacci/target price computation
- generate_action_plan: holder/new-investor action routing
- build_price_strategy: full structure assembly
"""
import unittest

from web_app.price_levels import (
    compute_price_levels,
    generate_action_plan,
    build_price_strategy,
)


class TestComputePriceLevels(unittest.TestCase):
    """Tests for compute_price_levels."""

    def test_compute_price_levels_basic(self):
        """Basic input -> entry_1, entry_2, entry_3, stop_loss all present."""
        stock = {
            'Price': 100.0,
            'high_52w': 120.0,
            'low_52w': 80.0,
            'ATR': 5.0,
            'AnalystTargetPrice': 130.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        self.assertIn('entry_1', result)
        self.assertIn('entry_2', result)
        self.assertIn('entry_3', result)
        self.assertIn('stop_loss', result)
        # Verify ATR-based calculations
        self.assertAlmostEqual(result['entry_1'], 95.0, places=2)   # 100 - 5*1
        self.assertAlmostEqual(result['entry_2'], 90.0, places=2)   # 100 - 5*2
        self.assertAlmostEqual(result['entry_3'], 85.0, places=2)   # 100 - 5*3
        self.assertAlmostEqual(result['stop_loss'], 80.0, places=2)  # 100 - 5*4

    def test_compute_price_levels_zero_price(self):
        """Price 0 -> None returned."""
        stock = {'Price': 0, 'ATR': 5.0}
        result = compute_price_levels(stock)
        self.assertIsNone(result)

    def test_compute_price_levels_no_atr(self):
        """ATR missing -> fallback to 2% of price."""
        stock = {
            'Price': 200.0,
            'high_52w': 220.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        # Fallback ATR = 200 * 0.02 = 4.0
        self.assertAlmostEqual(result['atr'], 4.0, places=2)
        self.assertAlmostEqual(result['entry_1'], 196.0, places=2)   # 200 - 4*1
        self.assertAlmostEqual(result['entry_2'], 192.0, places=2)   # 200 - 4*2
        self.assertAlmostEqual(result['entry_3'], 188.0, places=2)   # 200 - 4*3
        self.assertAlmostEqual(result['stop_loss'], 184.0, places=2)  # 200 - 4*4

    def test_compute_price_levels_fibonacci(self):
        """52-week low present -> fib_382, fib_500, fib_618 exist."""
        stock = {
            'Price': 150.0,
            'high_52w': 200.0,
            'low_52w': 100.0,
            'ATR': 5.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        self.assertIn('fib_382', result)
        self.assertIn('fib_500', result)
        self.assertIn('fib_618', result)
        # range = 200 - 100 = 100
        # fib_382 = 200 - 100*0.382 = 161.80
        # fib_500 = 200 - 100*0.500 = 150.00
        # fib_618 = 200 - 100*0.618 = 138.20
        self.assertAlmostEqual(result['fib_382'], 161.80, places=2)
        self.assertAlmostEqual(result['fib_500'], 150.00, places=2)
        self.assertAlmostEqual(result['fib_618'], 138.20, places=2)

    def test_compute_price_levels_no_fibonacci(self):
        """52-week low absent -> no fib keys."""
        stock = {
            'Price': 150.0,
            'high_52w': 200.0,
            'ATR': 5.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        self.assertNotIn('fib_382', result)
        self.assertNotIn('fib_500', result)
        self.assertNotIn('fib_618', result)

    def test_compute_price_levels_no_analyst(self):
        """No analyst target price -> target_analyst is None."""
        stock = {
            'Price': 100.0,
            'high_52w': 120.0,
            'ATR': 3.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        self.assertIsNone(result['target_analyst'])

    def test_entry_ordering(self):
        """entry_1 > entry_2 > entry_3 > stop_loss (descending)."""
        stock = {
            'Price': 100.0,
            'high_52w': 120.0,
            'ATR': 5.0,
        }
        result = compute_price_levels(stock)
        self.assertGreater(result['entry_1'], result['entry_2'])
        self.assertGreater(result['entry_2'], result['entry_3'])
        self.assertGreater(result['entry_3'], result['stop_loss'])


class TestGenerateActionPlan(unittest.TestCase):
    """Tests for generate_action_plan."""

    def _make_price_levels(self):
        """Helper to create a standard price_levels dict."""
        return {
            'entry_1': 95.0,
            'entry_2': 90.0,
            'entry_3': 85.0,
            'stop_loss': 80.0,
            'target_analyst': 130.0,
            'target_52w_high': 120.0,
            'fib_382': 105.0,
            'price': 100.0,
            'atr': 5.0,
            'atr_pct': 5.0,
        }

    def test_generate_action_plan_bull_buy(self):
        """bull >= 40 and PriceInLevel <= 50 -> new investor gets '분할 매수 고려'."""
        stock = {'PriceInLevel': 30}
        scenarios = {'bull': 45, 'neutral': 35, 'bear': 20}
        price_levels = self._make_price_levels()

        plan = generate_action_plan(stock, price_levels, scenarios)
        self.assertIn('분할 매수 고려', plan['new_investor']['action'])

    def test_generate_action_plan_bear_reduce(self):
        """bear >= 40 -> holder gets '비중 축소 고려'."""
        stock = {'PriceInLevel': 70}
        scenarios = {'bull': 20, 'neutral': 30, 'bear': 50}
        price_levels = self._make_price_levels()

        plan = generate_action_plan(stock, price_levels, scenarios)
        self.assertIn('비중 축소 고려', plan['holder']['action'])

    def test_generate_action_plan_neutral(self):
        """bull 30~40 -> new investor gets '관망'."""
        stock = {'PriceInLevel': 50}
        scenarios = {'bull': 35, 'neutral': 40, 'bear': 25}
        price_levels = self._make_price_levels()

        plan = generate_action_plan(stock, price_levels, scenarios)
        self.assertIn('관망', plan['new_investor']['action'])

    def test_generate_action_plan_hold(self):
        """bull >= 40 and bear < 40 -> holder gets '보유 유지'."""
        stock = {'PriceInLevel': 40}
        scenarios = {'bull': 45, 'neutral': 35, 'bear': 20}
        price_levels = self._make_price_levels()

        plan = generate_action_plan(stock, price_levels, scenarios)
        self.assertIn('보유 유지', plan['holder']['action'])


class TestBuildPriceStrategy(unittest.TestCase):
    """Tests for build_price_strategy full structure."""

    def test_build_price_strategy_structure(self):
        """Return value must contain price_levels and action_plan keys."""
        stock = {
            'Price': 100.0,
            'high_52w': 120.0,
            'low_52w': 80.0,
            'ATR': 5.0,
            'AnalystTargetPrice': 130.0,
            'PriceInLevel': 40,
        }
        scenarios = {'bull': 45, 'neutral': 35, 'bear': 20}

        result = build_price_strategy(stock, scenarios)
        self.assertIn('price_levels', result)
        self.assertIn('action_plan', result)
        # price_levels sub-keys
        self.assertIn('entry_1', result['price_levels'])
        self.assertIn('stop_loss', result['price_levels'])
        # action_plan sub-keys
        self.assertIn('new_investor', result['action_plan'])
        self.assertIn('holder', result['action_plan'])


if __name__ == '__main__':
    unittest.main()
