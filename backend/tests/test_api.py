import pytest
import pandas as pd
from datetime import date, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient

# ── モックデータ ───────────────────────────────────────────────────────────

MOCK_DF = pd.DataFrame(
    {
        "date":   [date(2024, 1, i + 1) for i in range(10)],
        "open":   [100.0 + i for i in range(10)],
        "high":   [105.0 + i for i in range(10)],
        "low":    [98.0  + i for i in range(10)],
        "close":  [102.0 + i for i in range(10)],
        "volume": [1_000_000] * 10,
    }
)

MOCK_IND = {
    "ma": {
        "5":  [None] * 4 + [101.0 + i for i in range(6)],
        "25": [None] * 10,
        "75": [None] * 10,
    },
    "bollinger": {"upper": [None]*10, "mid": [None]*10, "lower": [None]*10},
    "macd":      {"macd":  [None]*10, "signal": [None]*10, "hist": [None]*10},
    "rsi":       [None] * 10,
}

EMPTY_DF = pd.DataFrame(
    columns=["date", "open", "high", "low", "close", "volume"]
)


# ── fixture ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_client():
    """TestClient を 1 モジュールにつき 1 つ生成。lifespan の DB 初期化を無効化。"""
    from main import app
    from services import db as _db
    _db.init_db(":memory:")   # テスト用インメモリ DB を先に準備
    with patch("services.db.init_db"), \
         patch("services.securities.ensure_securities"):  # ファイル作成とネットワーク呼び出しを抑止
        with TestClient(app) as client:
            yield client
    _db.shutdown()


# ── 正常系 ────────────────────────────────────────────────────────────────

class TestNormalCases:
    def test_ap01_period_returns_200(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF), \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/AAPL?period=3mo")
        assert resp.status_code == 200
        assert resp.json()["ticker"] == "AAPL"

    def test_ap02_start_end_passed_to_get_ohlcv(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF) as mock_get, \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/AAPL?start=2024-01-01&end=2024-03-31")
        assert resp.status_code == 200
        _, _, q_start, q_end = mock_get.call_args[0]
        assert q_start == date(2024, 1, 1)
        assert q_end   == date(2024, 3, 31)

    def test_ap03_start_end_overrides_period(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF) as mock_get, \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get(
                "/api/stock/AAPL?period=1mo&start=2024-01-01&end=2024-01-31"
            )
        assert resp.status_code == 200
        _, _, q_start, q_end = mock_get.call_args[0]
        assert q_start == date(2024, 1, 1)
        assert q_end   == date(2024, 1, 31)

    def test_ap04_default_90_day_period(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF) as mock_get, \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/AAPL?interval=1d")
        assert resp.status_code == 200
        _, _, q_start, q_end = mock_get.call_args[0]
        assert (q_end - q_start).days == 90

    def test_ap05_lowercase_ticker_uppercased(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF), \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/aapl?period=3mo")
        assert resp.status_code == 200
        assert resp.json()["ticker"] == "AAPL"

    def test_ap06_interval_1wk_passed_correctly(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF) as mock_get, \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/AAPL?interval=1wk&period=1y")
        assert resp.status_code == 200
        assert resp.json()["interval"] == "1wk"
        _, interval, _, _ = mock_get.call_args[0]
        assert interval == "1wk"

    def test_ap07_response_structure(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=MOCK_DF), \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/AAPL?period=3mo")
        data = resp.json()
        assert "ticker"     in data
        assert "interval"   in data
        assert "ohlcv"      in data
        assert len(data["ohlcv"]) == 10
        ohlcv0 = data["ohlcv"][0]
        assert all(k in ohlcv0 for k in ["date", "open", "high", "low", "close", "volume"])
        ind = data["indicators"]
        assert all(k in ind for k in ["ma", "bollinger", "macd", "rsi"])
        assert all(k in ind["ma"]        for k in ["5", "25", "75"])
        assert all(k in ind["bollinger"] for k in ["upper", "mid", "lower"])
        assert all(k in ind["macd"]      for k in ["macd", "signal", "hist"])


# ── 異常系 ────────────────────────────────────────────────────────────────

class TestErrorCases:
    def test_ap08_invalid_interval_400(self, app_client):
        resp = app_client.get("/api/stock/AAPL?interval=2d")
        assert resp.status_code == 400
        assert "Invalid interval" in resp.json()["detail"]

    def test_ap09_invalid_period_400(self, app_client):
        resp = app_client.get("/api/stock/AAPL?period=2mo")
        assert resp.status_code == 400
        assert "Invalid period" in resp.json()["detail"]

    def test_ap10_start_after_end_400(self, app_client):
        resp = app_client.get(
            "/api/stock/AAPL?start=2024-06-30&end=2024-01-01"
        )
        assert resp.status_code == 400
        assert "start must be before end" in resp.json()["detail"]

    def test_ap11_empty_df_returns_404(self, app_client):
        with patch("routers.stock.market.get_ohlcv",        return_value=EMPTY_DF), \
             patch("routers.stock.market.get_indicators",   return_value=MOCK_IND):
            resp = app_client.get("/api/stock/XXXXX?period=3mo")
        assert resp.status_code == 404
        assert "Ticker not found" in resp.json()["detail"]
