import re
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from services import db, market

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^]{1,20}$")

PERIOD_DAYS = {
    "1mo":  30,
    "3mo":  90,
    "6mo":  180,
    "1y":   365,
    "5y":   365 * 5,
    "10y":  365 * 10,
}

VALID_INTERVALS = {"1d", "1wk", "1mo"}


@router.get("/stock/{ticker}")
def get_stock(
    ticker: str,
    period: Optional[str]  = Query(None),
    start:  Optional[date] = Query(None),
    end:    Optional[date] = Query(None),
    interval: str          = Query("1d"),
):
    if not _TICKER_RE.match(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker format")
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Invalid interval: {interval}")

    today = date.today()

    if start and end:
        if start > end:
            raise HTTPException(status_code=400, detail="start must be before end")
        q_start, q_end = start, end
    elif period:
        if period not in PERIOD_DAYS:
            raise HTTPException(status_code=400, detail=f"Invalid period: {period}")
        q_start = today - timedelta(days=PERIOD_DAYS[period])
        q_end   = today
    else:
        q_start = today - timedelta(days=90)
        q_end   = today

    df = market.get_ohlcv(ticker.upper(), interval, q_start, q_end)

    if df.empty:
        raise HTTPException(status_code=404, detail=f"Ticker not found: {ticker}")

    indicators = market.get_indicators(ticker.upper(), interval, q_start, q_end)

    ohlcv = [
        {
            "date":   row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
            "open":   row["open"],
            "high":   row["high"],
            "low":    row["low"],
            "close":  row["close"],
            "volume": int(row["volume"]) if row["volume"] is not None else None,
        }
        for _, row in df.iterrows()
    ]

    name = db.get_security_name(ticker.upper())

    return {
        "ticker":     ticker.upper(),
        "name":       name or ticker.upper(),
        "interval":   interval,
        "ohlcv":      ohlcv,
        "indicators": indicators,
    }
