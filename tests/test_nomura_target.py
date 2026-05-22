"""Regression tests for Nomura-style target price routing.

Validates:
- SK하이닉스 케이스: 시클리컬-PB 방식, BPS × P/B
- 섹터 보정 계수 (2026 톤): 반도체 > 자동차 > 조선
- 라우팅: 은행 → Gordon-PB, 통신 → Forward-PE, 바이오 → DCF
- SOTP 우선순위
"""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from valuation_engine import (  # noqa: E402
    _forward_bps,
    _gordon_target_pb,
    _nomura_bias,
    _route_sector,
    nomura_target_price,
)


class TestNomuraTargetPrice(unittest.TestCase):

    def test_sk_hynix_cyclical_pb(self) -> None:
        """SK하이닉스: 시클리컬-PB로 분류되고, BPS × P/B 공식 일관."""
        r = nomura_target_price(
            "반도체",
            {"bps": 668_186, "roe": 0.35},
            coe=0.11, terminal_growth=0.025,
            payout_ratio=0.10, forward_months=0,
        )
        self.assertEqual(r["method"], "Cyclical-PB")
        c = r["components"]
        # 정당화 P/B = (0.35-0.025)/(0.11-0.025) × bias(1.20) ≈ 4.59
        expected_pb_pre_bias = (0.35 - 0.025) / (0.11 - 0.025)
        self.assertAlmostEqual(
            c["target_pb"], expected_pb_pre_bias * 1.20, places=2,
        )
        # target_price = forward_bps × target_pb
        self.assertAlmostEqual(
            r["target_price"], c["forward_bps"] * c["target_pb"], places=0,
        )

    def test_2026_sector_ranking(self) -> None:
        """2026 톤: 반도체 > 자동차 > 조선 (동일 입력 기준)."""
        inp = {"bps": 100_000, "roe": 0.15}
        kw = dict(coe=0.10, terminal_growth=0.025, payout_ratio=0.30, forward_months=0)
        semi = nomura_target_price("반도체", inp, **kw)["target_price"]
        auto = nomura_target_price("자동차", inp, **kw)["target_price"]
        ship = nomura_target_price("조선", inp, **kw)["target_price"]
        self.assertGreater(semi, auto)
        self.assertGreater(auto, ship)

    def test_bias_lookup(self) -> None:
        """알려진 섹터는 정확한 보정 계수, 미지 섹터는 1.0."""
        self.assertEqual(_nomura_bias("반도체"), 1.20)
        self.assertEqual(_nomura_bias("조선"), 0.70)
        self.assertEqual(_nomura_bias("자동차"), 0.85)
        self.assertEqual(_nomura_bias("미지섹터"), 1.0)
        self.assertEqual(_nomura_bias(""), 1.0)

    def test_bank_uses_gordon_pb(self) -> None:
        """은행은 Gordon-PB 방식, forward_months ≥ 24."""
        r = nomura_target_price(
            "은행",
            {"bps": 100_000, "roe": 0.10},
            coe=0.10, terminal_growth=0.025, payout_ratio=0.30,
        )
        self.assertEqual(r["method"], "Gordon-PB")
        self.assertGreaterEqual(r["components"]["forward_months"], 24)

    def test_telecom_uses_forward_pe(self) -> None:
        """통신은 Forward-PE 방식."""
        r = nomura_target_price("통신", {"eps": 5000, "peer_pe": 12.0})
        self.assertEqual(r["method"], "Forward-PE")
        # bias 1.0 → 5000 × 12 = 60,000
        self.assertAlmostEqual(r["target_price"], 60_000.0, places=1)

    def test_sotp_overrides_sector(self) -> None:
        """SOTP가 지정되면 섹터 라우팅보다 우선."""
        r = nomura_target_price(
            "반도체",
            {"shares_outstanding": 1000},
            sotp_segments=[{"value": 5_000_000}, {"value": 3_000_000}],
        )
        self.assertEqual(r["method"], "SOTP")
        # 8,000,000 / 1000 × bias(1.20) = 9,600
        self.assertAlmostEqual(r["target_price"], 9_600.0, places=1)

    def test_gordon_helper_invalid_inputs(self) -> None:
        """COE <= g 면 0 반환."""
        self.assertEqual(_gordon_target_pb(0.10, 0.02, 0.025), 0.0)
        self.assertEqual(_gordon_target_pb(0.20, 0.025, 0.025), 0.0)

    def test_forward_bps_rolls_correctly(self) -> None:
        """ROE 10%, 배당성향 30% → 1년 후 BPS = BPS × (1 + 0.10×0.70) = ×1.07."""
        fwd = _forward_bps(100_000, 0.10, 0.30, months=12)
        self.assertAlmostEqual(fwd, 107_000.0, places=0)

    def test_partial_match_sector_names(self) -> None:
        """이모지·구분자 포함된 실제 섹터명도 부분일치로 매핑."""
        self.assertEqual(_nomura_bias("🔧 반도체 / 메모리"), 1.20)
        self.assertEqual(_nomura_bias("한화오션 (조선)"), 0.70)
        self.assertEqual(_route_sector("은행/지방은행"), "은행")
        self.assertEqual(_route_sector("🚗 자동차 OEM"), "자동차")
        self.assertEqual(_route_sector("기타"), "")

    def test_forward_bps_zero_months_noop(self) -> None:
        """months=0 이면 현재 BPS 반환."""
        self.assertEqual(_forward_bps(100_000, 0.20, 0.30, months=0), 100_000)


    def test_english_sector_aliases_route(self) -> None:
        """English sector labels should still route to Nomura buckets."""
        kw = dict(coe=0.11, terminal_growth=0.025, payout_ratio=0.10, forward_months=0)
        semi = nomura_target_price(
            "Technology / Semiconductors",
            {"bps": 100_000, "roe": 0.35},
            **kw,
        )
        bank = nomura_target_price(
            "Financials / Banks",
            {"bps": 100_000, "roe": 0.10},
            **kw,
        )
        growth = nomura_target_price(
            "Software / Cloud",
            {"fcf": 500_000, "cash": 0, "debt": 0},
            **kw,
        )
        self.assertEqual(semi["method"], "Cyclical-PB")
        self.assertEqual(bank["method"], "Gordon-PB")
        self.assertEqual(growth["method"], "DCF")

    def test_semicon_equipment_uses_blend(self) -> None:
        """Semicon equipment should avoid a pure P/B haircut."""
        r = nomura_target_price(
            "반도체 장비",
            {"bps": 100_000, "roe": 0.35, "eps": 10_000, "peer_pe": 25.0},
            coe=0.11, terminal_growth=0.025, payout_ratio=0.10, forward_months=0,
        )
        self.assertEqual(r["method"], "Semicon-Blend")
        self.assertGreater(r["target_price"], 100_000)

    def test_korean_equipment_sector_routes_to_blend(self) -> None:
        """실제 KR 섹터명인 '반도체장비·소재'도 장비 블렌드로 들어가야 한다."""
        r = nomura_target_price(
            "반도체장비·소재",
            {"bps": 100_000, "roe": 0.35, "eps": 10_000, "peer_pe": 25.0},
            coe=0.11, terminal_growth=0.025, payout_ratio=0.10, forward_months=0,
        )
        self.assertEqual(r["method"], "Semicon-Blend")
        self.assertGreater(r["target_price"], 100_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
