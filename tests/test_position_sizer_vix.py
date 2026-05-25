"""WS-003 — position_sizer VIX-스케일 회귀 테스트."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from position_sizer import calc_position


def test_normal_vix_no_scale():
    r = calc_position(10000, 1, 100, 97, vix=18)
    assert r["qty"] == 33
    assert r["vix_scale"] == 1.0


def test_vix_above_30_three_quarter():
    r = calc_position(10000, 1, 100, 97, vix=32)
    assert r["vix_scale"] == 0.75
    # 100 / 3 * 0.75 = 25
    assert r["qty"] == 25


def test_vix_above_40_halved():
    r = calc_position(10000, 1, 100, 97, vix=45)
    assert r["vix_scale"] == 0.5
    assert r["qty"] == 16


def test_invalid_stop_returns_zero_not_raises():
    r = calc_position(10000, 1, 100, 105)
    assert r["qty"] == 0
    assert r.get("skipped") == "stop_not_below_entry"


def test_invalid_entry_returns_zero():
    r = calc_position(10000, 1, 0, -5)
    assert r["qty"] == 0


def test_nan_vix_no_effect():
    r = calc_position(10000, 1, 100, 97, vix=float("nan"))
    assert r["qty"] == 33
    assert r["vix_scale"] == 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
    print("ALL OK")
