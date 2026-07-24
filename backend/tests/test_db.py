import pytest
import pandas as pd
from datetime import date
from services import db


@pytest.fixture(autouse=True)
def reset_db():
    """各テスト前後にDBをインメモリで初期化・クリーンアップ"""
    if db._conn:
        db._conn.close()
        db._conn = None
    db.init_db(":memory:")
    yield
    if db._conn:
        db._conn.close()
        db._conn = None


def make_df(dates, closes=None):
    """yfinance と同形式のテスト用 DataFrame を生成"""
    n = len(dates)
    closes = closes or [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open":   [c * 0.99 for c in closes],
            "High":   [c * 1.01 for c in closes],
            "Low":    [c * 0.98 for c in closes],
            "Close":  closes,
            "Volume": [1_000_000] * n,
        },
        index=pd.DatetimeIndex(pd.to_datetime(dates), name="Date"),
    )


# ── init_db ──────────────────────────────────────────────────────────────

class TestInitDb:
    def test_db01_creates_table(self):
        result = db._conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name='ohlcv'"
        ).fetchall()
        assert len(result) == 1, "ohlcv テーブルが存在しない"

    def test_db02_idempotent(self):
        db.init_db(":memory:")  # 2回目の呼び出し
        result = db._conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()
        assert result[0] == 0

    def test_db03_raises_before_init(self):
        db._conn.close()
        db._conn = None
        with pytest.raises(RuntimeError, match="DB not initialized"):
            db.query("AAPL", "1d", date(2024, 1, 1), date(2024, 12, 31))
        db.init_db(":memory:")  # teardown のために再初期化


# ── get_latest_date ───────────────────────────────────────────────────────

class TestGetLatestDate:
    def test_db04_none_when_empty(self):
        assert db.get_latest_date("AAPL", "1d") is None

    def test_db05_returns_latest_date(self):
        db.upsert(make_df(["2024-01-01", "2024-02-15", "2024-03-31"]), "AAPL", "1d")
        assert db.get_latest_date("AAPL", "1d") == date(2024, 3, 31)

    def test_db06_isolated_by_ticker_and_interval(self):
        db.upsert(make_df(["2024-06-01"]), "AAPL",   "1d")
        db.upsert(make_df(["2024-05-01"]), "AAPL",   "1wk")
        db.upsert(make_df(["2024-04-01"]), "7203.T", "1d")
        assert db.get_latest_date("AAPL",   "1d")  == date(2024, 6, 1)
        assert db.get_latest_date("AAPL",   "1wk") == date(2024, 5, 1)
        assert db.get_latest_date("7203.T", "1d")  == date(2024, 4, 1)


# ── upsert / query ────────────────────────────────────────────────────────

class TestUpsertAndQuery:
    def test_db07_insert_and_query(self):
        db.upsert(make_df(["2024-01-01", "2024-01-02", "2024-01-03"]), "AAPL", "1d")
        df = db.query("AAPL", "1d", date(2024, 1, 1), date(2024, 1, 3))
        assert len(df) == 3

    def test_db08_duplicate_key_overwrites(self):
        db.upsert(make_df(["2024-01-01"], closes=[100.0]), "AAPL", "1d")
        db.upsert(make_df(["2024-01-01"], closes=[200.0]), "AAPL", "1d")
        df = db.query("AAPL", "1d", date(2024, 1, 1), date(2024, 1, 1))
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(200.0)

    def test_db09_date_range_filter(self):
        dates = ["2024-01-01", "2024-02-01", "2024-03-01",
                 "2024-04-01", "2024-05-01", "2024-06-01"]
        db.upsert(make_df(dates), "AAPL", "1d")
        df = db.query("AAPL", "1d", date(2024, 3, 1), date(2024, 3, 31))
        assert len(df) == 1
        assert df.iloc[0]["date"] == date(2024, 3, 1)

    def test_db10_same_day_query_returns_one_row(self):
        db.upsert(make_df(["2024-03-15"]), "AAPL", "1d")
        df = db.query("AAPL", "1d", date(2024, 3, 15), date(2024, 3, 15))
        assert len(df) == 1

    def test_db11_ticker_isolation(self):
        db.upsert(make_df(["2024-01-01"]), "AAPL",   "1d")
        db.upsert(make_df(["2024-01-01"]), "7203.T", "1d")
        df = db.query("AAPL", "1d", date(2024, 1, 1), date(2024, 12, 31))
        assert len(df) == 1
