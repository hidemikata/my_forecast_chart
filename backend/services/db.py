import logging
import threading
from contextlib import contextmanager
from datetime import date

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def init_db(path: str = "stock.duckdb") -> None:
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = duckdb.connect(path)
    _conn.execute("""CREATE TABLE IF NOT EXISTS ohlcv (
        ticker   VARCHAR NOT NULL,
        date     DATE    NOT NULL,
        interval VARCHAR NOT NULL,
        open     DOUBLE,
        high     DOUBLE,
        low      DOUBLE,
        close    DOUBLE NOT NULL,
        volume   BIGINT,
        PRIMARY KEY (ticker, date, interval))""")
    _conn.execute("""CREATE TABLE IF NOT EXISTS securities (
        ticker  VARCHAR PRIMARY KEY,
        code    VARCHAR NOT NULL,
        name    VARCHAR NOT NULL,
        market  VARCHAR NOT NULL
    )""")
    _conn.execute("""CREATE TABLE IF NOT EXISTS indicators (
        ticker       VARCHAR NOT NULL,
        date         DATE    NOT NULL,
        interval     VARCHAR NOT NULL,
        ma5          DOUBLE,
        ma25         DOUBLE,
        ma75         DOUBLE,
        bb_upper     DOUBLE,
        bb_mid       DOUBLE,
        bb_lower     DOUBLE,
        macd         DOUBLE,
        macd_signal  DOUBLE,
        macd_hist    DOUBLE,
        rsi          DOUBLE,
        PRIMARY KEY (ticker, date, interval)
    )""")


def shutdown() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _c() -> duckdb.DuckDBPyConnection:
    if _conn is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    return _conn


@contextmanager
def _db():
    """ロックを取得してコネクションを返すコンテキストマネージャ"""
    with _lock:
        yield _c()


# ---- OHLCV ----

def get_latest_date(ticker: str, interval: str) -> date | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM ohlcv WHERE ticker = ? AND interval = ?",
            [ticker, interval],
        ).fetchone()
    return row[0] if row and row[0] else None


def _prepare_ohlcv(df: pd.DataFrame, ticker: str, interval: str) -> pd.DataFrame:
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"OHLCV DataFrame に必須カラムがありません: {missing}")
    tmp = df.copy()
    tmp["ticker"]   = ticker
    tmp["interval"] = interval
    tmp = tmp.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    tmp["date"] = pd.to_datetime(tmp["date"]).dt.date
    return tmp[["ticker", "date", "interval", "open", "high", "low", "close", "volume"]]


def upsert(df: pd.DataFrame, ticker: str, interval: str) -> None:
    tmp = _prepare_ohlcv(df, ticker, interval)
    with _db() as conn:
        conn.register("_tmp_ohlcv", tmp)
        try:
            conn.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM _tmp_ohlcv")
        finally:
            try:
                conn.unregister("_tmp_ohlcv")
            except Exception:
                pass


def query(ticker: str, interval: str, start: date, end: date) -> pd.DataFrame:
    with _db() as conn:
        df = conn.execute(
            """SELECT date, open, high, low, close, volume
               FROM ohlcv
               WHERE ticker = ? AND interval = ? AND date BETWEEN ? AND ?
               ORDER BY date ASC""",
            [ticker, interval, start, end],
        ).df()
    if not df.empty:
        df["date"] = df["date"].dt.date
    return df


def query_all_ohlcv(ticker: str, interval: str) -> pd.DataFrame:
    """指標計算用に全期間の ohlcv を返す"""
    with _db() as conn:
        df = conn.execute(
            "SELECT date, open, high, low, close, volume FROM ohlcv "
            "WHERE ticker=? AND interval=? ORDER BY date ASC",
            [ticker, interval],
        ).df()
    if not df.empty:
        df["date"] = df["date"].dt.date
    return df


# ---- 指標 ----

def indicators_up_to_date(ticker: str, interval: str) -> bool:
    """ohlcv の最新日と indicators の最新日が一致していれば True"""
    with _db() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT MAX(date) FROM ohlcv       WHERE ticker=? AND interval=?) AS ohlcv_max,
                 (SELECT MAX(date) FROM indicators   WHERE ticker=? AND interval=?) AS ind_max""",
            [ticker, interval, ticker, interval],
        ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return False
    return row[0] == row[1]


def _prepare_indicators(df: pd.DataFrame, ticker: str, interval: str) -> pd.DataFrame:
    tmp = df.copy()
    tmp["ticker"]   = ticker
    tmp["interval"] = interval
    return tmp[["ticker", "date", "interval", "ma5", "ma25", "ma75",
                "bb_upper", "bb_mid", "bb_lower", "macd", "macd_signal", "macd_hist", "rsi"]]


def upsert_indicators(df: pd.DataFrame, ticker: str, interval: str) -> None:
    tmp = _prepare_indicators(df, ticker, interval)
    with _db() as conn:
        conn.register("_tmp_ind", tmp)
        try:
            conn.execute("INSERT OR REPLACE INTO indicators SELECT * FROM _tmp_ind")
        finally:
            try:
                conn.unregister("_tmp_ind")
            except Exception:
                pass


def upsert_ohlcv_and_indicators(
    ohlcv_df: pd.DataFrame, ind_df: pd.DataFrame, ticker: str, interval: str
) -> None:
    """OHLCV と指標をひとつのトランザクションで原子的に書き込む"""
    ohlcv_tmp = _prepare_ohlcv(ohlcv_df, ticker, interval)
    ind_tmp   = _prepare_indicators(ind_df, ticker, interval)
    with _db() as conn:
        conn.begin()
        try:
            conn.register("_tmp_ohlcv", ohlcv_tmp)
            conn.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM _tmp_ohlcv")
            conn.unregister("_tmp_ohlcv")
            conn.register("_tmp_ind", ind_tmp)
            conn.execute("INSERT OR REPLACE INTO indicators SELECT * FROM _tmp_ind")
            conn.unregister("_tmp_ind")
            conn.commit()
        except Exception:
            conn.rollback()
            for name in ("_tmp_ohlcv", "_tmp_ind"):
                try:
                    conn.unregister(name)
                except Exception:
                    pass
            raise


def query_indicators(ticker: str, interval: str, start: date, end: date) -> pd.DataFrame:
    with _db() as conn:
        df = conn.execute(
            """SELECT date, ma5, ma25, ma75, bb_upper, bb_mid, bb_lower,
                      macd, macd_signal, macd_hist, rsi
               FROM indicators
               WHERE ticker=? AND interval=? AND date BETWEEN ? AND ?
               ORDER BY date ASC""",
            [ticker, interval, start, end],
        ).df()
    if not df.empty:
        df["date"] = df["date"].dt.date
    return df


# ---- 銘柄マスタ ----

def securities_empty() -> bool:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 0


def securities_count_by_market(market: str) -> int:
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM securities WHERE market = ?", [market]
        ).fetchone()
    return row[0] if row else 0


def upsert_securities(df: pd.DataFrame) -> None:
    tmp = df[["ticker", "code", "name", "market"]].copy()
    with _db() as conn:
        conn.register("_tmp_sec", tmp)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO securities (ticker, code, name, market) "
                "SELECT ticker, code, name, market FROM _tmp_sec"
            )
        finally:
            try:
                conn.unregister("_tmp_sec")
            except Exception:
                pass


def get_security_name(ticker: str) -> str | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT name FROM securities WHERE ticker = ?", [ticker]
        ).fetchone()
    return row[0] if row else None


def search_securities(q: str, limit: int = 10) -> list[dict]:
    q_up = q.upper()
    # LIKE の特殊文字 (%, _) をエスケープしてパターン注入を防ぐ
    q_esc = q_up.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with _db() as conn:
        rows = conn.execute(
            r"""SELECT ticker, code, name, market
               FROM securities
               WHERE UPPER(code) LIKE ? ESCAPE '\'
                  OR UPPER(name) LIKE ? ESCAPE '\'
               ORDER BY
                 CASE WHEN UPPER(code) = ? THEN 0
                      WHEN UPPER(code) LIKE ? ESCAPE '\' THEN 1
                      ELSE 2 END,
                 market, code
               LIMIT ?""",
            [f"%{q_esc}%", f"%{q_esc}%", q_up, f"{q_esc}%", limit],
        ).fetchall()
    return [{"ticker": r[0], "code": r[1], "name": r[2], "market": r[3]} for r in rows]
