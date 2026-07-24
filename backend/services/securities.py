import io
import logging

import httpx
import pandas as pd

from . import db

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def _fetch(url: str, binary: bool = False) -> bytes | str:
    resp = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.content if binary else resp.text


def download_jp() -> int:
    url = ("https://www.jpx.co.jp/markets/statistics-equities/misc/"
           "tvdivq0000001vg2-att/data_j.xls")
    try:
        raw = _fetch(url, binary=True)
    except Exception:
        logger.warning("JPX 銘柄ダウンロード失敗", exc_info=True)
        return 0
    df_raw = pd.read_excel(io.BytesIO(raw), dtype=str)
    rows = []
    for _, row in df_raw.iterrows():
        code = str(row.get("コード", "")).strip()
        name = str(row.get("銘柄名", "")).strip()
        if code and code.isdigit() and len(code) == 4 and name and name != "nan":
            rows.append({"ticker": f"{code}.T", "code": code, "name": name, "market": "JP"})
    if rows:
        db.upsert_securities(pd.DataFrame(rows))
    return len(rows)


def download_us() -> int:
    rows = []
    for url, sym_col, name_col, test_col in [
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",  0, 1, 3),
        ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",   0, 1, 6),
    ]:
        try:
            text = _fetch(url)
        except Exception:
            logger.warning("NASDAQ 銘柄ダウンロード失敗: %s", url, exc_info=True)
            continue
        seen = set()
        for line in text.splitlines()[1:]:
            if not line.strip() or line.startswith("File Creation"):
                continue
            parts = line.split("|")
            if len(parts) <= max(sym_col, name_col, test_col):
                continue
            symbol = parts[sym_col].strip()
            name   = parts[name_col].strip()
            test   = parts[test_col].strip()
            if test == "Y" or not symbol or symbol in seen:
                continue
            if not all(c.isalpha() or c in ("^", ".") for c in symbol):
                continue
            seen.add(symbol)
            rows.append({"ticker": symbol, "code": symbol, "name": name, "market": "US"})
    if rows:
        db.upsert_securities(pd.DataFrame(rows))
    return len(rows)


def ensure_securities() -> None:
    """市場ごとにデータが未取得のときだけダウンロードする（バックグラウンドスレッドから呼ぶ）"""
    if db.securities_count_by_market("JP") == 0:
        download_jp()
    if db.securities_count_by_market("US") == 0:
        download_us()
