import logging
import math
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd

from . import db

logger = logging.getLogger(__name__)

_YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def _last_weekday(d: date) -> date:
    """直近の平日（土→金、日→金）"""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def is_fresh(latest: date | None, interval: str) -> bool:
    if latest is None:
        return False
    today = date.today()
    if interval == "1d":
        return latest >= _last_weekday(today)
    if interval == "1wk":
        return latest >= _this_monday()
    if interval == "1mo":
        return latest >= today.replace(day=1)
    return False


def _yahoo_fetch(ticker: str, interval: str, start: date | None = None) -> dict:
    """Yahoo Finance chart API を httpx で取得する"""
    base = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
    if start is None:
        params = {"range": "10y", "interval": interval, "includePrePost": "false"}
    else:
        p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        p2 = int(datetime.now(timezone.utc).timestamp())
        params = {"period1": p1, "period2": p2,
                  "interval": interval, "includePrePost": "false"}
    resp = httpx.get(base, params=params, headers=_YAHOO_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if err := data.get("chart", {}).get("error"):
        raise RuntimeError(f"Yahoo Finance API error: {err}")
    return data


def _parse_yahoo(data: dict) -> pd.DataFrame:
    """Yahoo Finance JSON → DataFrame (DatetimeIndex 'Date', 大文字列名)"""
    results = data.get("chart", {}).get("result")
    if not results:
        return pd.DataFrame()
    r = results[0]
    timestamps = r.get("timestamp", [])
    if not timestamps:
        return pd.DataFrame()
    q = r["indicators"]["quote"][0]
    dates = [datetime.fromtimestamp(ts, tz=timezone.utc).date() for ts in timestamps]
    df = pd.DataFrame(
        {
            "Open":   q.get("open",   [None] * len(dates)),
            "High":   q.get("high",   [None] * len(dates)),
            "Low":    q.get("low",    [None] * len(dates)),
            "Close":  q.get("close",  [None] * len(dates)),
            "Volume": q.get("volume", [None] * len(dates)),
        },
        index=pd.DatetimeIndex(pd.to_datetime(dates), name="Date"),
    )
    return df.dropna(subset=["Close"])


def fetch_missing(ticker: str, interval: str, latest: date | None) -> pd.DataFrame | None:
    start = None if latest is None else latest + timedelta(days=1)
    try:
        data = _yahoo_fetch(ticker, interval, start)
        df = _parse_yahoo(data)
    except Exception:
        logger.warning("fetch_missing failed for %s/%s", ticker, interval, exc_info=True)
        return None
    return df if not df.empty else None


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _calc_indicators_df(df: pd.DataFrame) -> pd.DataFrame:
    """全期間 OHLCV から指標を計算し DataFrame で返す（DB 保存用）"""
    close = df["close"]

    ma5  = _sma(close, 5)
    ma25 = _sma(close, 25)
    ma75 = _sma(close, 75)

    bb_mid   = _sma(close, 25)
    bb_sigma = close.rolling(25).std()
    bb_upper = bb_mid + 2.0 * bb_sigma
    bb_lower = bb_mid - 2.0 * bb_sigma

    ema_fast    = _ema(close, 12)
    ema_slow    = _ema(close, 26)
    macd_line   = ema_fast - ema_slow
    macd_signal = _ema(macd_line, 9)
    macd_hist   = macd_line - macd_signal

    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)

    return pd.DataFrame({
        "date":        df["date"].values,
        "ma5":         ma5.values,
        "ma25":        ma25.values,
        "ma75":        ma75.values,
        "bb_upper":    bb_upper.values,
        "bb_mid":      bb_mid.values,
        "bb_lower":    bb_lower.values,
        "macd":        macd_line.values,
        "macd_signal": macd_signal.values,
        "macd_hist":   macd_hist.values,
        "rsi":         rsi.values,
    })


def _indicators_to_api(df: pd.DataFrame) -> dict:
    def col(name):
        return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 4)
                for v in df[name]]
    return {
        "ma": {"5": col("ma5"), "25": col("ma25"), "75": col("ma75")},
        "bollinger": {"upper": col("bb_upper"), "mid": col("bb_mid"), "lower": col("bb_lower")},
        "macd": {"macd": col("macd"), "signal": col("macd_signal"), "hist": col("macd_hist")},
        "rsi": col("rsi"),
    }


def calc_indicators(df: pd.DataFrame) -> dict:
    """OHLCV DataFrame から指標 dict を計算して返す（テスト・外部利用向け）"""
    return _indicators_to_api(_calc_indicators_df(df))


def get_ohlcv(ticker: str, interval: str, start: date, end: date) -> pd.DataFrame:
    latest = db.get_latest_date(ticker, interval)
    ohlcv_updated = False
    if not is_fresh(latest, interval):
        new_df = fetch_missing(ticker, interval, latest)
        if new_df is not None:
            ohlcv_updated = True

    # OHLCV が更新されたか indicators が未計算なら全期間で再計算してキャッシュ
    if ohlcv_updated or not db.indicators_up_to_date(ticker, interval):
        if ohlcv_updated and new_df is not None:
            # 新規 OHLCV と指標を 1 トランザクションで書き込む
            full_df_for_ind = db.query_all_ohlcv(ticker, interval)
            # new_df をローカルで結合して全期間指標を計算
            new_prepared = new_df.reset_index().rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            new_prepared["date"] = pd.to_datetime(new_prepared["date"]).dt.date
            if full_df_for_ind.empty:
                combined = new_prepared[["date", "open", "high", "low", "close", "volume"]]
            else:
                combined = pd.concat(
                    [full_df_for_ind, new_prepared[["date", "open", "high", "low", "close", "volume"]]],
                    ignore_index=True,
                ).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            ind_df = _calc_indicators_df(combined)
            db.upsert_ohlcv_and_indicators(new_df, ind_df, ticker, interval)
        else:
            full_df = db.query_all_ohlcv(ticker, interval)
            if not full_df.empty:
                ind_df = _calc_indicators_df(full_df)
                db.upsert_indicators(ind_df, ticker, interval)

    return db.query(ticker, interval, start, end)


def get_indicators(ticker: str, interval: str, start: date, end: date) -> dict:
    ind_df = db.query_indicators(ticker, interval, start, end)
    if ind_df.empty:
        return {
            "ma": {"5": [], "25": [], "75": []},
            "bollinger": {"upper": [], "mid": [], "lower": []},
            "macd": {"macd": [], "signal": [], "hist": []},
            "rsi": [],
        }
    return _indicators_to_api(ind_df)
