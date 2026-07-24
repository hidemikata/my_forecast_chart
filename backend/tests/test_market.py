import pytest
import pandas as pd
import numpy as np
from datetime import date
from freezegun import freeze_time
from services.market import is_fresh, calc_indicators


def make_ohlcv(n: int = 100) -> pd.DataFrame:
    """calc_indicators に渡す形式（db.query の戻り値と同形式）"""
    np.random.seed(42)
    close = 100.0 + np.cumsum(np.random.randn(n) * 2)
    return pd.DataFrame(
        {
            "date":   pd.date_range("2024-01-01", periods=n).date,
            "open":   close * 0.99,
            "high":   close * 1.01,
            "low":    close * 0.98,
            "close":  close,
            "volume": [1_000_000] * n,
        }
    )


# ── is_fresh ──────────────────────────────────────────────────────────────

class TestIsFresh:
    def test_mk01_none_returns_false(self):
        assert is_fresh(None, "1d") is False

    @freeze_time("2026-06-29")  # 月曜日
    def test_mk02_1d_fresh_today(self):
        assert is_fresh(date(2026, 6, 29), "1d") is True

    @freeze_time("2026-06-29")
    def test_mk03_1d_stale_previous_friday(self):
        assert is_fresh(date(2026, 6, 26), "1d") is False

    @freeze_time("2026-06-28")  # 土曜日 → _last_weekday = 6/26（金）
    def test_mk04_1d_saturday_uses_last_friday(self):
        assert is_fresh(date(2026, 6, 26), "1d") is True

    @freeze_time("2026-06-29")  # 月曜日 → _this_monday = 6/29
    def test_mk05_1wk_fresh_this_monday(self):
        assert is_fresh(date(2026, 6, 29), "1wk") is True

    @freeze_time("2026-06-29")
    def test_mk06_1wk_stale_last_week(self):
        assert is_fresh(date(2026, 6, 22), "1wk") is False

    @freeze_time("2026-06-29")  # today.replace(day=1) = 6/1
    def test_mk07_1mo_fresh_first_of_month(self):
        assert is_fresh(date(2026, 6, 1), "1mo") is True

    @freeze_time("2026-06-29")
    def test_mk08_1mo_stale_last_month(self):
        assert is_fresh(date(2026, 5, 31), "1mo") is False

    @freeze_time("2026-06-29")
    def test_mk09_unknown_interval_false(self):
        assert is_fresh(date(2026, 6, 29), "2d") is False


# ── calc_indicators ───────────────────────────────────────────────────────

class TestCalcIndicators:
    def test_mk10_ma5_non_null_count(self):
        ind = calc_indicators(make_ohlcv(100))
        non_null = [v for v in ind["ma"]["5"] if v is not None]
        # rolling(5): 先頭4つがNaN → 96個が有効
        assert len(non_null) == 96

    def test_mk11_short_data_ma25_ma75_all_none(self):
        ind = calc_indicators(make_ohlcv(10))
        assert all(v is None for v in ind["ma"]["25"]), "MA25 は全て None のはず"
        assert all(v is None for v in ind["ma"]["75"]), "MA75 は全て None のはず"
        # MA5: rolling(5) → 先頭4つがNaN、残り6つが有効
        non_null_ma5 = [v for v in ind["ma"]["5"] if v is not None]
        assert len(non_null_ma5) == 6

    def test_mk12_rsi_in_0_to_100_range(self):
        ind = calc_indicators(make_ohlcv(100))
        rsi_vals = [v for v in ind["rsi"] if v is not None]
        assert len(rsi_vals) > 0, "RSI に有効な値がない"
        assert all(0.0 <= v <= 100.0 for v in rsi_vals), "RSI が 0〜100 の範囲外"

    def test_mk13_bollinger_upper_ge_mid_ge_lower(self):
        ind = calc_indicators(make_ohlcv(100))
        for u, m, l in zip(
            ind["bollinger"]["upper"],
            ind["bollinger"]["mid"],
            ind["bollinger"]["lower"],
        ):
            if None in (u, m, l):
                continue
            assert u >= m, f"upper({u}) < mid({m})"
            assert m >= l, f"mid({m}) < lower({l})"
