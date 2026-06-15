"""CAN SLIM C earnings growth source selection.

This module is intentionally dependency-light so the priority rules can be
tested without importing the full quant engine.
"""

from __future__ import annotations

from math import isnan
from typing import Any


EPS_BASIS_LABELS = {
    "quarterly_eps": "최근 분기 EPS/순이익 YoY",
    "annual_eps": "연간/TTM EPS 성장률",
    "forward_vs_trailing_eps": "Forward EPS 대비 TTM EPS",
    "revenue_proxy": "매출 성장률 프록시",
}


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if isnan(out):
        return default
    return out


def select_canslim_c_growth(info: dict[str, Any]) -> dict[str, Any]:
    """Select the growth input for CAN SLIM C.

    C is Current Quarterly Earnings, so quarterly YoY growth must win over
    annual/TTM growth when both are available.
    """
    rg = _num(info.get("revenueGrowth"), 0.0) or 0.0

    eg = _num(info.get("earningsQuarterlyGrowth"), None)
    src = "quarterly_eps"
    if eg is None:
        eg = _num(info.get("earningsGrowth"), None)
        src = "annual_eps"
    if eg is None:
        fe = _num(info.get("forwardEps"), None)
        te = _num(info.get("trailingEps"), None)
        if fe is not None and te is not None and abs(te) > 1e-9:
            eg = (fe - te) / abs(te)
            src = "forward_vs_trailing_eps"
    if eg is None and rg not in (None, 0.0):
        eg = rg * 0.6
        src = "revenue_proxy"

    if eg is None:
        return {
            "eps_growth": None,
            "eps_src": "",
            "eps_basis": "",
            "rev_growth": rg,
            "data_missing": True,
        }

    return {
        "eps_growth": eg,
        "eps_src": src,
        "eps_basis": EPS_BASIS_LABELS.get(src, "EPS 성장률"),
        "rev_growth": rg,
        "data_missing": False,
    }
