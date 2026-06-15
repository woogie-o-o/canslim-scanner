import importlib
app_mod = importlib.import_module("app")

def test_downsample_closes_caps_point_count():
    closes = list(range(100))  # 100 포인트
    out = app_mod._downsample_closes(closes, max_points=24)
    assert len(out) <= 24
    assert out[-1] == 99.0          # 마지막 값(최신가)은 항상 보존
    assert all(isinstance(x, float) for x in out)

def test_downsample_closes_short_input_passthrough():
    closes = [10.0, 11.0, 12.0]
    out = app_mod._downsample_closes(closes, max_points=24)
    assert out == [10.0, 11.0, 12.0]

def test_downsample_closes_empty():
    assert app_mod._downsample_closes([], max_points=24) == []

def test_wk52_high_low():
    closes = [5.0, 1.0, 9.0, 4.0]
    hi, lo = app_mod._wk52_high_low(closes)
    assert hi == 9.0 and lo == 1.0

def test_wk52_high_low_empty_returns_none():
    assert app_mod._wk52_high_low([]) == (None, None)
