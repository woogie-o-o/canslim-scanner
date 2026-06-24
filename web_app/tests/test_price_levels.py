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
    select_profile,
    compute_allocation,
    average_cost,
    compute_risk_scenarios,
)


class TestComputePriceLevels(unittest.TestCase):
    """Tests for compute_price_levels."""

    def test_compute_price_levels_basic(self):
        """단독 호출(폴백) -> σ 밴드(shallow) 진입가. σ_daily = ATR_pct = 5%."""
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
        self.assertIn('vol_band', result)
        self.assertEqual(result['vol_source'], 'ATR')
        self.assertEqual(result['profile'], 'shallow')
        # σ_daily = ATR/price*100 = 5.0, shallow ks = [0, 0.3, 0.6, 0.9]
        self.assertAlmostEqual(result['sigma_daily'], 5.0, places=3)
        self.assertAlmostEqual(result['entry_1'], 100.0, places=2)  # k=0   (시장가)
        self.assertAlmostEqual(result['entry_2'], 98.5, places=2)   # k=0.3: 100*(1-0.015)
        self.assertAlmostEqual(result['entry_3'], 97.0, places=2)   # k=0.6: 100*(1-0.030)
        # stop_k = 0.9 + max(0.3,0.5) = 1.4 -> 100*(1-0.07) = 93.0
        self.assertAlmostEqual(result['stop_loss'], 93.0, places=2)

    def test_compute_price_levels_zero_price(self):
        """Price 0 -> None returned."""
        stock = {'Price': 0, 'ATR': 5.0}
        result = compute_price_levels(stock)
        self.assertIsNone(result)

    def test_compute_price_levels_no_atr(self):
        """ATR·ATR_pct 모두 없음 -> σ_daily 2.0 폴백, atr 표시값은 2% of price."""
        stock = {
            'Price': 200.0,
            'high_52w': 220.0,
        }
        result = compute_price_levels(stock)
        self.assertIsNotNone(result)
        # 표시용 atr = 200 * 0.02 = 4.0 (유지)
        self.assertAlmostEqual(result['atr'], 4.0, places=2)
        # σ_daily 폴백 = 2.0, shallow
        self.assertAlmostEqual(result['sigma_daily'], 2.0, places=3)
        self.assertAlmostEqual(result['entry_1'], 200.0, places=2)   # k=0
        self.assertAlmostEqual(result['entry_2'], 198.8, places=2)   # k=0.3: 200*(1-0.006)
        self.assertAlmostEqual(result['entry_3'], 197.6, places=2)   # k=0.6: 200*(1-0.012)

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

    def test_build_price_strategy_vol_kr_deep(self):
        """VKOSPI 고변동성 주입 -> deep 프로파일 1~2σ 6밴드."""
        stock = {'Price': 100.0, 'high_52w': 130, 'low_52w': 70,
                 'ATR': 2.5, 'ATR_pct': 2.5, 'AnalystTargetPrice': 120}
        result = build_price_strategy(stock, vol={'source': 'VKOSPI', 'level': 87.6})
        vb = result['vol_band']
        self.assertEqual(vb['source'], 'VKOSPI')
        self.assertEqual(vb['profile'], 'deep')
        self.assertEqual(len(vb['entries']), 6)
        self.assertAlmostEqual(vb['sigma_daily'], 87.6 / (252 ** 0.5), places=2)
        # 1σ 진입가 = 100*(1 - sigma_daily/100)
        self.assertAlmostEqual(vb['entries'][0]['price'],
                               100 * (1 - vb['sigma_daily'] / 100), places=2)
        self.assertIn('risk_scenarios', result)
        self.assertEqual(len(result['risk_scenarios']), 4)

    def test_build_price_strategy_us_shallow(self):
        """VIX 저변동성 주입 -> shallow 프로파일 0~0.9σ 4밴드, 시장가 시작."""
        stock = {'Price': 100.0, 'high_52w': 130, 'low_52w': 70, 'ATR_pct': 1.5}
        result = build_price_strategy(stock, vol={'source': 'VIX', 'level': 16.4})
        vb = result['vol_band']
        self.assertEqual(vb['profile'], 'shallow')
        self.assertEqual(len(vb['entries']), 4)
        self.assertAlmostEqual(vb['entries'][0]['k'], 0.0)
        self.assertAlmostEqual(vb['entries'][0]['price'], 100.0, places=2)


class TestSelectProfile(unittest.TestCase):
    def test_vkospi_high_deep(self):
        self.assertEqual(select_profile(40.0, 'VKOSPI')[0], 'deep')
        self.assertEqual(select_profile(87.6, 'VKOSPI')[0], 'deep')

    def test_vkospi_low_shallow(self):
        self.assertEqual(select_profile(25.0, 'VKOSPI')[0], 'shallow')

    def test_vix_threshold(self):
        self.assertEqual(select_profile(30.0, 'VIX')[0], 'deep')
        self.assertEqual(select_profile(16.4, 'VIX')[0], 'shallow')

    def test_none_and_atr_shallow(self):
        self.assertEqual(select_profile(None, 'ATR')[0], 'shallow')
        self.assertEqual(select_profile(99.0, 'ATR')[0], 'shallow')  # ATR은 연율 아님


class TestAllocation(unittest.TestCase):
    def test_equal_sums_to_one(self):
        w = compute_allocation(4, 'equal')
        self.assertEqual(len(w), 4)
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertTrue(all(abs(x - 0.25) < 1e-9 for x in w))

    def test_geo_back_weighted(self):
        w = compute_allocation(6, 'geo', geo_ratio=1.3)
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertGreater(w[-1], w[0])  # 후반 가중

    def test_step_back_weighted(self):
        w = compute_allocation(6, 'step', step_mult=2.5)
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertGreater(w[-1], w[0])

    def test_custom_normalized(self):
        w = compute_allocation(3, 'custom', custom=[10, 20, 70])
        self.assertAlmostEqual(sum(w), 1.0, places=6)
        self.assertAlmostEqual(w[2], 0.7, places=6)


class TestAverageCost(unittest.TestCase):
    def test_average_cost_known(self):
        # 두 회차 동일 비중, 진입가 100/90 -> 조화평균
        entries = [{'price': 100.0}, {'price': 90.0}]
        w = [0.5, 0.5]
        ac = average_cost(entries, w)
        # 1 / (0.5/100 + 0.5/90) = 1 / (0.005 + 0.005556) = 94.7368
        self.assertAlmostEqual(ac, 94.7368, places=3)


class TestRiskScenarios(unittest.TestCase):
    def test_structure_and_over_limit(self):
        entries = [{'k': 1.0, 'price': 95.0}, {'k': 2.0, 'price': 90.0}]
        w = [0.5, 0.5]
        scn = compute_risk_scenarios(entries, w, base=100.0,
                                     loss_limit_pct=1.0, sigma_daily=5.0)
        self.assertEqual(len(scn), 4)
        self.assertEqual(scn[0]['type'], 'idle')
        self.assertEqual(scn[3]['type'], 'down')
        # 작은 손실한도(1%)면 하방 스트레스가 초과
        self.assertTrue(scn[3]['over_limit'])


if __name__ == '__main__':
    unittest.main()
