# 株価チャートビューア 技術仕様書

## 1. 概要

日本株・米国株のOHLCV（四本値+出来高）チャートをブラウザで表示するWebアプリケーション。Yahoo Finance からデータを取得し、DuckDB にキャッシュしつつ、テクニカル指標（移動平均・ボリンジャーバンド・MACD・RSI）を付加してフロントエンドに提供する。フロントエンドは Plotly.js を用いてローソク足チャートを描画し、マウス操作による予測ローソク足の手描き・直線描画などの独自インタラクションを備える。

本書はシステムの内部構成・API・データモデル・計算式・機能仕様・UI/UXデザイン（配色・レイアウト・操作仕様）を網羅する技術仕様書であり、本書のみから同一のアプリケーションを再構築できることを目標とする。

## 2. システム構成

```
Browser (frontend/app.js + Plotly.js CDN)
   │  fetch /api/stock/{ticker}?interval=&period=|start=&end=
   │  fetch /api/securities/search?q=
   ▼
FastAPI (backend/main.py, prefix=/api)
   ├─ routers/stock.py       … GET /api/stock/{ticker}
   └─ routers/securities.py  … GET /api/securities/search
   ▼
services/market.py  … Yahoo Finance取得 + テクニカル指標計算 + 鮮度判定
services/db.py      … DuckDB永続化（ohlcv / indicators / securities）
services/securities.py … JPX・NASDAQ銘柄マスタ取得
   ▼
DuckDB (backend/stock.duckdb)
```

## 3. 技術スタック

| 層 | 技術 |
|---|---|
| バックエンド言語 | Python 3.11 |
| Webフレームワーク | FastAPI 0.138.1 / uvicorn[standard] 0.49.0 |
| データ処理 | pandas 2.2.1 |
| DB | DuckDB 1.5.4（組み込み分析DB、ファイル: `backend/stock.duckdb`） |
| HTTPクライアント | httpx 0.28.1 |
| Excel読込 | xlrd（JPX銘柄マスタの`.xls`形式読込用） |
| テスト | pytest 9.1.1 / freezegun 1.5.5 |
| フロントエンド | Vanilla JavaScript（フレームワーク・ビルドツールなし、ES2017相当・`<script>`直読み込み） |
| チャート描画 | Plotly.js 2.35.2（CDN読込、SRI検証あり） |

`backend/requirements.txt` の内容:
```
# Runtime
fastapi==0.138.1
uvicorn[standard]==0.49.0
pandas==2.2.1
duckdb==1.5.4
httpx==0.28.1
xlrd

# Dev / test
pytest==9.1.1
freezegun==1.5.5
```

Plotly.js の読み込みタグ（`frontend/index.html`）:
```html
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"
        integrity="sha384-cCVCZkAjYNxaYKbM8lsArLznDF/SvMFr1jcZrvOpSTCa0W40ZAdLzHCEulnUa5i7"
        crossorigin="anonymous"></script>
```

## 4. ディレクトリ構成

```
stock-chart/
├── backend/
│   ├── main.py               # FastAPIエントリーポイント
│   ├── requirements.txt
│   ├── routers/
│   │   ├── stock.py          # GET /api/stock/{ticker}
│   │   └── securities.py     # GET /api/securities/search
│   ├── services/
│   │   ├── db.py             # DuckDB永続化・クエリ
│   │   ├── market.py         # Yahoo Finance取得・指標計算
│   │   └── securities.py     # 銘柄マスタ取得（JPX/NASDAQ）
│   └── tests/                # pytest（backend/tests/配下）
└── frontend/
    ├── index.html
    ├── style.css
    └── app.js
```

## 5. バックエンド仕様

### 5.1 起動処理（`backend/main.py`）

- `lifespan`コンテキストマネージャで起動時に`db.init_db("stock.duckdb")`を実行しテーブルを作成（`CREATE TABLE IF NOT EXISTS`、冪等）。
- 起動をブロックしないよう、銘柄マスタ取得（`securities.ensure_securities`）はデーモンスレッドで非同期実行。
- 終了時に`db.shutdown()`でDB接続をクローズ。
- ロギングは`logging.basicConfig(level=logging.INFO)`。
- CORS: `allow_origins=["*"]`、`allow_methods=["GET"]`のみ、`allow_headers=["*"]`（開発用設定。本番デプロイ時は要変更）。
- ルーターは両方とも`prefix="/api"`でマウント。認証・レート制限は未実装。

### 5.2 API仕様

#### `GET /api/stock/{ticker}`

**パスパラメータ**
- `ticker`: 正規表現 `^[A-Za-z0-9.\-^]{1,20}$` に一致しない場合 `400 {"detail": "Invalid ticker format"}`。

**クエリパラメータ**

| 名前 | 型 | デフォルト | 説明 |
|---|---|---|---|
| `interval` | string | `"1d"` | `1d` / `1wk` / `1mo` のいずれか。不正値は `400 {"detail": "Invalid interval: {interval}"}` |
| `period` | string | なし | `1mo`(30日) / `3mo`(90日) / `6mo`(180日) / `1y`(365日) / `5y`(1825日) / `10y`(3650日)。単純日数換算で、暦月・暦年ではない。不正値は `400 {"detail": "Invalid period: {period}"}` |
| `start` / `end` | date (ISO 8601, `YYYY-MM-DD`) | なし | 明示的な期間指定。`start > end` は `400 {"detail": "start must be before end"}` |

`PERIOD_DAYS`定義（`routers/stock.py`）:
```python
PERIOD_DAYS = {
    "1mo":  30,
    "3mo":  90,
    "6mo":  180,
    "1y":   365,
    "5y":   365 * 5,
    "10y":  365 * 10,
}
VALID_INTERVALS = {"1d", "1wk", "1mo"}
```

**日付範囲の解決優先順位**
1. `start`/`end` が両方指定されていればそれを使用（`period`は無視）
2. `period`のみ指定 → `today - period日数` 〜 `today`
3. どちらも未指定 → 直近90日間（デフォルト）

**処理フロー**:
1. `ticker`を大文字化（`ticker.upper()`）した上で`market.get_ohlcv(ticker, interval, start, end)`を呼び、DuckDBキャッシュの鮮度判定・Yahoo Finance差分取得・指標再計算を行った上で要求期間のOHLCVを取得する。
2. 取得結果が空の`DataFrame`であれば`404 {"detail": "Ticker not found: {ticker}"}`。
3. `market.get_indicators(ticker, interval, start, end)`で同期間の指標をDBから取得。
4. `db.get_security_name(ticker)`で銘柄名を取得（未登録なら`None`）。
5. レスポンスを構築して返す。

**レスポンス例**
```json
{
  "ticker": "7203.T",
  "name": "トヨタ自動車",
  "interval": "1d",
  "ohlcv": [
    { "date": "2024-01-04", "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000000 }
  ],
  "indicators": {
    "ma": { "5": [null, null, null, null, 101.2], "25": [...], "75": [...] },
    "bollinger": { "upper": [...], "mid": [...], "lower": [...] },
    "macd": { "macd": [...], "signal": [...], "hist": [...] },
    "rsi": [...]
  }
}
```
- `ticker`はレスポンス側も大文字化済みの値。
- `name`はDB上の銘柄マスタに登録がなければ`ticker`自体を返す（`name or ticker.upper()`）。
- `ohlcv[].date`は`YYYY-MM-DD`のISO文字列。`volume`は`int`、値が`None`の場合は`null`。
- 指標値はAPIレスポンスへ変換する際に`round(value, 4)`で丸め、NaN/Noneは`null`にする（配列の長さは常に`ohlcv`と同じ）。

#### `GET /api/securities/search`

| クエリ | 型 | 制約 |
|---|---|---|
| `q` | string | 最大50文字、デフォルト`""` |
| `limit` | int | 1〜50、デフォルト10 |

`q`（前後空白を除去したもの）が空文字なら空配列`[]`を返す（バリデーションエラーにはしない）。レスポンス:
```json
[{ "ticker": "7203.T", "code": "7203", "name": "トヨタ自動車", "market": "JP" }]
```
検索ロジック（`services/db.py: search_securities`）:
- `q`を大文字化し、DuckDBの`LIKE`用エスケープ処理（`\`→`\\`、`%`→`\%`、`_`→`\_`）を行う。
- `WHERE UPPER(code) LIKE '%{q}%' OR UPPER(name) LIKE '%{q}%'`で部分一致検索。
- ソート順は`CASE`式による優先順位: ①`UPPER(code) = q`（完全一致）→0、②`UPPER(code) LIKE '{q}%'`（前方一致）→1、③その他（部分一致）→2。同順位内は`market, code`昇順。
- `LIMIT limit`で件数制限。

### 5.3 データ取得・キャッシュ戦略（`services/market.py`）

**鮮度判定（`is_fresh(latest, interval)`）**: DB内の最新日付が以下の基準日以降であれば「新鮮」とみなし、Yahoo Financeへの再取得をスキップする。`latest`が`None`（未取得）なら常に`False`。

| interval | 基準日 |
|---|---|
| `1d` | 直近平日（`_last_weekday`: 土曜→前の金曜、日曜→前の金曜まで1日ずつ遡る） |
| `1wk` | 今週の月曜（`_this_monday`: `today - today.weekday()日`） |
| `1mo` | 今月1日（`today.replace(day=1)`） |
| その他 | 常に古い扱い（毎回再取得） |

**Yahoo Finance取得（`_yahoo_fetch`）**: `https://query2.finance.yahoo.com/v8/finance/chart/{ticker}` を`httpx.get`で直接呼び出し（`yfinance`ライブラリは不使用）。
- リクエストヘッダ: `User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36`。
- 初回取得（DBに既存データなし、`start=None`）: クエリパラメータ`{"range": "10y", "interval": interval, "includePrePost": "false"}`で全期間取得。
- 差分取得（`start`指定あり）: DB内最新日の翌日を`period1`、現在時刻(UTC)を`period2`としてUnixタイムスタンプで指定。クエリパラメータ`{"period1": p1, "period2": p2, "interval": interval, "includePrePost": "false"}`。
- タイムアウト20秒。レスポンスJSONの`chart.error`が存在すれば`RuntimeError`。取得中に例外が発生した場合は警告ログ（`logger.warning(..., exc_info=True)`）を出し、`fetch_missing`は`None`を返す（「更新なし」として扱い、例外を上位に伝播させない）。
- レスポンスJSONの解析（`_parse_yahoo`）: `chart.result[0]`から`timestamp`配列と`indicators.quote[0]`の`open/high/low/close/volume`を取り出し、UTCの日付インデックスを持つ`DataFrame`（列名`Open/High/Low/Close/Volume`）に変換。`Close`が`NaN`の行は除外（`dropna(subset=["Close"])`）。

**更新フロー（`get_ohlcv(ticker, interval, start, end)`）**:
1. `db.get_latest_date(ticker, interval)`でDB内最新日を取得し`is_fresh`で鮮度判定。
2. 古ければ`fetch_missing`でYahooから差分（または初回は全期間）取得。
3. 新規データがある場合（`ohlcv_updated=True`）:
   - DB内の全期間OHLCV（`db.query_all_ohlcv`）と新規取得分を結合し、`date`列で重複除去（新規側が優先されるわけではなく`drop_duplicates(subset=["date"])`のデフォルト挙動＝先勝ち順だが、通常は新規分の日付はDBに存在しないため実質的に和集合）→ `sort_values("date")`で日付ソート。
   - 結合結果全体から指標を再計算（`_calc_indicators_df`）。
   - 新規OHLCV分と再計算済み指標全期間分を`db.upsert_ohlcv_and_indicators`で1トランザクション（`begin`/`commit`、失敗時`rollback`）として書き込み（原子性を担保）。
4. 新規データがなく（鮮度OKまたは取得失敗）、かつ指標が未計算（`db.indicators_up_to_date`が`False`、すなわちohlcv最新日とindicators最新日が不一致）の場合: DB全期間OHLCVを取得し指標を再計算・`db.upsert_indicators`で保存のみ行う。
5. 上記いずれでもない場合（鮮度OK かつ指標も最新）は再計算をスキップ。
6. 最後に`db.query(ticker, interval, start, end)`で要求された期間のOHLCVをDBから返す（`ORDER BY date ASC`）。

### 5.4 テクニカル指標の計算式（`_calc_indicators_df`）

| 指標 | 計算方法 |
|---|---|
| MA5 / MA25 / MA75 | 単純移動平均（`close.rolling(n).mean()`）。データ不足区間は`NaN`。 |
| ボリンジャーバンド | 期間25、σ=2.0固定。中心線=SMA25（`bb_mid`）、標準偏差=`close.rolling(25).std()`（標本標準偏差、`ddof=1`＝pandasのデフォルト）。上バンド`bb_upper = bb_mid + 2.0×σ`、下バンド`bb_lower = bb_mid − 2.0×σ`。 |
| MACD(12,26,9) | `ema_fast = close.ewm(span=12, adjust=False).mean()`、`ema_slow = close.ewm(span=26, adjust=False).mean()`。`macd = ema_fast − ema_slow`（MACD線）、`macd_signal = macd.ewm(span=9, adjust=False).mean()`（シグナル線）、`macd_hist = macd − macd_signal`（ヒストグラム）。 |
| RSI(14) | Wilder方式のEMA平滑化。`delta = close.diff()`、`gain = delta.clip(lower=0)`、`loss = (-delta).clip(lower=0)`。`avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()`、`avg_loss`同様。`rs = avg_gain / avg_loss`（`avg_loss==0`は`NaN`に置換して0除算を回避）。`rsi = 100 − 100/(1+rs)`。 |

指標値はAPIレスポンスに変換する際（`_indicators_to_api`）、`round(value, 4)`で丸め、`NaN`/`None`は`null`にする。すべての指標配列長は`ohlcv`と同じ（インデックス整合）。

### 5.5 DuckDBスキーマ（`services/db.py`）

**`ohlcv`**
```sql
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker   VARCHAR NOT NULL,
    date     DATE    NOT NULL,
    interval VARCHAR NOT NULL,
    open     DOUBLE,
    high     DOUBLE,
    low      DOUBLE,
    close    DOUBLE NOT NULL,
    volume   BIGINT,
    PRIMARY KEY (ticker, date, interval))
```

**`indicators`**
```sql
CREATE TABLE IF NOT EXISTS indicators (
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
)
```

**`securities`**
```sql
CREATE TABLE IF NOT EXISTS securities (
    ticker  VARCHAR PRIMARY KEY,
    code    VARCHAR NOT NULL,
    name    VARCHAR NOT NULL,
    market  VARCHAR NOT NULL
)
```

- グローバル接続（モジュールレベル単一`DuckDBPyConnection`）を`threading.Lock`（`_lock`）で保護し、複数リクエストからの同時アクセスに対応。すべてのDB操作は`with _db():`（ロック取得→接続返却）を経由する。
- 書き込みは一時テーブルをDataFrameから`conn.register`し、`INSERT OR REPLACE INTO ... SELECT * FROM _tmp_xxx`でUpsert（主キー競合時は上書き）。使用後は`conn.unregister`で解除（例外時も`finally`/`except`で解除）。
- OHLCVと指標の同時書き込み（`upsert_ohlcv_and_indicators`）は単一トランザクション（`conn.begin()`/`commit()`、例外時`rollback()`）で原子性を担保する。

### 5.6 銘柄マスタ取得（`services/securities.py`）

- **日本株（`download_jp`）**: JPX公式サイトの`data_j.xls`（URL: `https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls`）を`httpx`（`follow_redirects=True`、タイムアウト30秒）でダウンロードし、`pandas.read_excel`（`dtype=str`）でパース。列「コード」が4桁の数字かつ列「銘柄名」が非空・`"nan"`でない行のみ、`{code}.T`ティッカーとして`market="JP"`で登録。ダウンロード失敗時は警告ログを出し`0`件として処理を継続（例外を伝播させない）。
- **米国株（`download_us`）**: NASDAQ Traderの`nasdaqlisted.txt`（シンボル列0・名称列1・テスト銘柄列3）・`otherlisted.txt`（シンボル列0・名称列1・テスト銘柄列6）をパイプ区切りでダウンロード・パース。各行を`|`で分割し、列数不足の行・空行・`"File Creation"`で始まるフッター行はスキップ。テスト銘柄列が`"Y"`、シンボルが空、または同一ファイル内で既出のシンボルは除外。シンボルはアルファベット・`^`・`.`のみで構成されるもの（数字を含むものは除外）のみ登録。`market="US"`で登録。
- いずれもDB内に該当市場（`market`列）のデータが0件のときのみ取得を実行し（`ensure_securities`）、起動のたびに再取得はしない。

## 6. フロントエンド仕様

### 6.1 画面構成（`frontend/index.html`）

DOM構造は上から下に以下の要素が並ぶ縦方向フレックスレイアウト（`body { display:flex; flex-direction:column; height:100vh }`）。表示/非表示はJSによる`style.display`切替であり、要素自体は常にDOM上に存在する。

1. `#toolbar` — 操作バー（常時表示）
2. `#loading-bar` — データ取得中のプログレスバー（取得中のみ`block`）
3. `#loading-overlay` — スピナー付きローディング表示（取得中のみ`flex`）
4. `#error-msg` — エラーメッセージ（エラー時のみ`block`）
5. `#hover-info` — ホバー中のOHLC等の詳細情報バー（チャート初回描画後は常時`flex`、それまでは`none`）
6. `#chart` — Plotly.jsチャート本体（初期は`display:none`、初回データ取得成功時に`block`）
7. `#empty-state` — 「証券コードを入力してください」の案内（初期表示、チャート表示後は`none`）

`#toolbar`内の要素は左から右へ以下の順で配置（`gap: 8px`、折り返し可、`flex-wrap: wrap`）:

1. `.ticker-wrap`（`#ticker-input` + `#ticker-dropdown`） — 証券コード入力欄
2. `.sep`（区切り線）
3. `#interval-select` — 足種セレクトボックス（日足/週足/月足）
4. `.sep`
5. `.candle-btn`（`data-count="50/100/150/200/250/300"`、`150`が初期`active`） — 表示本数切替ボタン×6
6. `.sep`
7. `#draw-line-btn`（`✏`） — 直線描画モードトグル
8. `#y-fit-btn`（`↕`） — Y軸表示モードトグル
9. `.sep`
10. `#latest-date` — 最新データ日付表示（空文字初期値）
11. `#update-btn`（「更新」） — 手動更新ボタン（`margin-left: auto`でツールバー右端に配置）

`#ticker-input`の`placeholder="7203"`、初期`value="7203"`、`autocomplete="off"`、`spellcheck="false"`。

### 6.2 デザインシステム（配色・タイポグラフィ）

#### 6.2.1 CSSカラートークン（`:root`, `style.css`）

| トークン | 値 | 用途 |
|---|---|---|
| `--bg` | `#F5F7FA` | ページ全体の背景色 |
| `--surface` | `#FFFFFF` | ツールバー・ドロップダウン・ホバー情報バーの背景 |
| `--border` | `#D4DAE8` | 枠線・区切り線・入力欄の境界色 |
| `--text` | `#3A445E` | 基本文字色 |
| `--muted` | `#7A86A8` | 補助文字色（ラベル・プレースホルダ的テキスト） |
| `--accent` | `#3B6EE8` | アクセント色（アクティブ状態・ティッカー文字・フォーカス枠） |
| `--green` | `#1A9960` | 上昇（陽線・プラス値）を表す色 |
| `--red` | `#D63B3B` | 下落（陰線・マイナス値）を表す色 |

その他、直接指定される色（変数化されていない）:

| 値 | 用途 |
|---|---|
| `#EEF2FF` | ドロップダウン項目のhover/active背景 |
| `#E84040` | 銘柄バッジ「JP」の背景色 |
| `#2A5BD6` | 銘柄バッジ「US」の背景色 |
| `#EEF1F7` | ローディングバー背景／チャートのグリッド線色 |
| `#f8f9fc` | ローディングオーバーレイの背景 |
| `#fff5f5` | エラーメッセージの背景 |
| `#fdc` | エラーメッセージの下枠線色 |

#### 6.2.2 タイポグラフィ

- ベースフォント: `system-ui, -apple-system, 'Segoe UI', sans-serif`、`font-size: 14px`（`body`）。
- 等幅フォント（ティッカー入力・ドロップダウン証券コード・最新日付・ホバー情報バー）: `ui-monospace, monospace`。
- ページ全体でウェブフォントは使用せず、OS標準フォントに依存する。

#### 6.2.3 チャート配色（Plotly、`renderChart`内で定義）

| 要素 | 色 | 備考 |
|---|---|---|
| ローソク足 陽線（上昇） | `#1A9960`（`GREEN`） | 線・塗りとも同色 |
| ローソク足 陰線（下落） | `#D63B3B`（`RED`） | 線・塗りとも同色 |
| MA5 | `#E0AF68`（ゴールド系） | 線幅1.2 |
| MA25 | `#BB9AF7`（紫系） | 線幅1.2 |
| MA75 | `#7AA2F7`（青系） | 線幅1.2 |
| ボリンジャーバンド 上下線 | `#3B6EE8`（`BLUE`） | 線幅1、`dash: 'dot'` |
| ボリンジャーバンド 帯塗り | `rgba(59,110,232,0.07)` | 上線→下線間を`fill: 'tonexty'` |
| 出来高バー | 陽線相当`#1A9960` / 陰線相当`#D63B3B`（先頭日は常に緑扱い） | `opacity: 0.7` |
| MACDヒストグラム | `hist >= 0`は`#1A9960`、`hist < 0`は`#D63B3B` | `opacity: 0.7` |
| MACD線 | `#3B6EE8` | 線幅1.5 |
| シグナル線 | `#E0AF68` | 線幅1.2 |
| 直近25本背景ゾーン | `rgba(59,110,232,0.05)`（薄い青） | `layer: 'below'`、`n > 25`のとき`dates[n-26]`〜`dates[n-1]` |
| 26〜75本前背景ゾーン | `rgba(255,165,0,0.06)`（薄い橙） | `layer: 'below'`、`n > 75`のとき`dates[n-76]`〜`dates[n-26]` |
| 年境界の縦線 | `#B8C2D8` | 線幅1、`layer: 'below'` |
| 年ラベル・サブチャート名ラベル | 文字色`#7A86A8`、サイズ10 | 「出来高」「MACD (12,26,9)」および年数字 |
| チャート紙面背景 | `paper_bgcolor: '#FFFFFF'` | |
| プロット領域背景 | `plot_bgcolor: '#FAFBFD'` | |
| 基本フォント | 色`#3A445E`、サイズ11 | `layout.font` |
| グリッド線（メイン・MACD） | `#EEF1F7` | |
| MACDゼロライン | `#D4DAE8` | |
| タイトル: 証券コード | 色`#1B2240`、サイズ15 | |
| タイトル: 会社名 | 色`#3A445E`、サイズ13 | |
| タイトル: 足種ラベル | 色`#7A86A8`、サイズ11 | |
| 予測ローソク足 | 陽線`#1A9960` / 陰線`#D63B3B` | 確定済みローソク足と同色 |
| 予測MA5/25/75の延長線 | `#E0AF68` / `#BB9AF7` / `#7AA2F7` | 線幅1.2、実データMAと同色 |
| 予測BB延長線 | `#3B6EE8` | 線幅1、`dash: 'dot'` |
| 直線描画（確定） | `#3B6EE8` | 線幅2、`solid` |
| 直線描画（プレビュー中） | `rgba(59,110,232,0.6)` | 線幅1.5、`dash: 'dash'` |

### 6.3 グローバル状態（`app.js`）

| 変数 | 役割 |
|---|---|
| `selectedTicker` | ドロップダウンから選択された完全ティッカー（`null`なら未選択＝手入力フォールバック） |
| `ddActiveIndex` | ドロップダウンのキーボード選択インデックス（`-1`は未選択） |
| `currentDates/Opens/Highs/Lows/Closes/Vols` | 表示中の実データ配列 |
| `currentInd` | 現在の指標データ（ma/bollinger/macd/rsi） |
| `currentTicker` / `currentInterval` | 表示中の銘柄ティッカー・足種（値幅制限の市場判定に使用） |
| `candleCount` | 表示本数（初期値150） |
| `dragState` | チャートパン中のドラッグ状態（`{startX, range0, plotWidth}`または`null`） |
| `predCandles` / `predDraw` | 確定済み／描画中の予測ローソク足 `{slot, open, close, high, low}` |
| `currentBaseShapes` | `renderChart`で生成した背景ゾーン・年境界線シェイプ（予測描画更新時に再利用） |
| `yScale` | ホイールによるY軸スケール係数（パンでリセットされ`1`に戻る） |
| `lineDrawMode` | 直線描画モードON/OFF |
| `drawnLines` / `lineDraw` | 確定済み直線／描画中の直線プレビュー `{x0,y0,x1,y1}` |
| `yFitMode` | Y軸表示モード（`false`=3倍比率モード、`true`=高値安値フィットモード） |
| `debounceTimer` | オートコンプリートのデバウンスタイマーID |
| `_predRafId` | 予測シェイプ再描画の`requestAnimationFrame`ID（フレーム間引き用） |

### 6.4 API呼び出しとURL構築

- `API_BASE = 'http://localhost:8000/api'`にハードコード。
- `buildUrl()`: 入力欄の値を`trim()`。空なら`null`を返す。ティッカーは`selectedTicker`（ドロップダウン選択済み）優先、なければ`normalizeTicker(raw)`（4桁数字なら`.T`付与、それ以外は大文字化のみ）。入力欄の表示は`displayCode(ticker)`（`.T`除去）に正規化される。
- クエリパラメータは常に`{ interval: intervalSel.value, period: '10y' }`（フロントエンドは常に10年分を要求し、表示本数はクライアント側の`candleCount`とX軸範囲操作で制御する）。
- `fetchChart()`: ドロップダウンを閉じ、URLを構築、ローディング表示ON→`fetch`→成功時`renderChart(data)`＋`empty-state`を隠して`#chart`を表示、失敗時（非2xx、または例外）は`エラー: {message}`を`#error-msg`に表示（レスポンスJSONの`detail`優先、無ければ`HTTP {status}`）。最後に必ずローディング表示OFF。
- トリガー: `#update-btn`クリック、`#interval-select`の`change`（入力欄に値があるときのみ）、入力欄で候補未選択のままEnter、ドロップダウン項目選択時。

### 6.5 オートコンプリート（銘柄検索）

- `#ticker-input`の`input`イベントで`selectedTicker`と`ddActiveIndex`をリセットし、180msデバウンスで`fetchSuggestions(q)`を呼ぶ（`q`が空ならデバウンスせず即座にドロップダウンを閉じる）。
- `fetchSuggestions`: `GET /api/securities/search?q={q}&limit=8`（**フロントエンド側で`limit=8`に固定**、バックエンドのデフォルト`10`とは異なる）。エラー時（サーバー未起動等）は無視（`catch`で握り潰す）。
- `renderDropdown(items)`: 0件なら閉じる。各項目は`<li data-ticker data-code data-i>`で、内部に`.dd-code`（証券コード、幅52px固定・太字・等幅・`--accent`色）、`.dd-name`（銘柄名、はみ出しは`ellipsis`）、`.dd-market`（`JP`/`US`バッジ、背景色は`.jp`→`#E84040`、`.us`→`#2A5BD6`、文字は白・サイズ10・太字・`letter-spacing:0.05em`）を表示。
- ドロップダウンの見た目: `position:absolute`で入力欄の下`4px`、幅`320px`、背景`--surface`、`1px solid --border`、`border-radius:6px`、`box-shadow: 0 4px 16px rgba(0,0,0,0.10)`、最大高さ`280px`（縦スクロール）、`z-index:200`。項目は`padding:7px 12px`、hover/active背景`#EEF2FF`（`transition: background .08s`）。
- キーボード操作: `ArrowDown`/`ArrowUp`で`ddActiveIndex`を`-1`〜`items.length-1`の範囲で移動し`.active`クラスを更新。`Enter`は選択中項目があれば確定（`selectItem`）、なければ`fetchChart()`を即実行。`Escape`はドロップダウンを閉じる。印字可能な1文字キーは`[A-Za-z0-9^.]`以外を`preventDefault`で入力拒否（矢印・BackSpace等の制御キーは通す）。
- `blur`時は150ms後にドロップダウンを閉じる（マウスクリックによる選択を先に処理させるための遅延）。ドロップダウン項目は`mousedown`（`click`ではない）で`preventDefault`しつつ選択処理する。
- `selectItem(li)`: `selectedTicker`にフルティッカーをセットし、入力欄表示は証券コードのみに置換、ドロップダウンを閉じて即`fetchChart()`。

### 6.6 チャート描画（`renderChart`）

Plotly.jsの複数y軸に以下を割り当てて1チャートに合成する（`Plotly.react`で描画、`{responsive: true, displayModeBar: false}`）。

| y軸 | 内容 | domain |
|---|---|---|
| `yaxis` | ローソク足 + MA5/25/75 + ボリンジャーバンド | `[0.26, 1]` |
| `yaxis2` | 出来高（棒グラフ） | `[0.14, 0.25]` |
| `yaxis3` | MACD（ヒストグラム＋MACD線＋シグナル線） | `[0, 0.13]` |
| `yaxis5` | メイン価格軸の右側ラベル表示専用（`overlaying:'y'`、`side:'right'`）。ダミートレース（`opacity:0`のmarker、`hoverinfo:'skip'`）を1点だけ描画して軸を有効化する | `yaxis`と常に同一range |

レイアウト共通設定:
- `margin: { l: 60, r: 60, t: 44, b: 44 }`
- `xaxis`: `type: 'category'`（日付を離散カテゴリとして扱う＝営業日のギャップが空かない）、`rangeslider.visible: false`、`showgrid: false`、`domain: [0, 1]`、`showticklabels: false`（日付軸ラベルは非表示、日付情報はホバー情報バーと年境界ラベルで補う）。
- `xaxis2`/`xaxis3`は`matches: 'x'`（メインX軸と同期）、`showticklabels: false`。
- `dragmode: false`（Plotly標準のドラッグ・ズームは完全に無効化し、独自のマウスイベントで代替する）。
- `yaxis`/`yaxis2`/`yaxis3`/`yaxis5`はすべて`fixedrange: true`（Plotly側でのY軸操作を禁止）、`automargin: true`。
- `showlegend: false`、`hovermode: false`（Plotly標準のホバーツールチップは使わず、独自の`#hover-info`バーで表示する）。
- タイトル: `x: 0.01, xanchor: 'left'`。テキストは`{証券コード}  {会社名(あれば)}  {足種}`のHTML文字列（`<span style="...">`で色分け、6.2.3節参照）。会社名はDB未登録時（`name === ticker`）は省略。
- 追加のアノテーション: `出来高`ラベル（`yaxis2 domain`基準、左上）、`MACD (12,26,9)`ラベル（`yaxis3 domain`基準、左上）、年境界ラベル群。

初期表示範囲: `initX0 = max(-0.5, n - candleCount - 0.5)`、`initX1 = n - 0.5`（`n`=データ件数）。Y軸初期範囲は`calcYRange(initX0, n-0.5)`（6.9節）。

チャート読込完了時、`#latest-date`に最新日付を`最新: {月}/{日}`形式（先頭ゼロなし、例: `最新: 6/30`）で表示。また`clearHoverInfo()`を呼び、ホバー情報バーをプレースホルダ（`–`）表示で常時可視化する。

`renderChart`実行時、`predCandles`/`predDraw`/`drawnLines`/`lineDraw`/`yScale`はすべて初期状態にリセットされる（銘柄・足種を変更すると予測描画・直線はクリアされる）。

### 6.7 予測ローソク足描画機能

- チャート右端の余白（実データ直後のスロット、`nextSlot = n + predCandles.length`）でマウスをドラッグ開始（`mousedown`、`xData`が`nextSlot ± 0.5`の範囲内）すると、ドラッグ開始位置=始値・現在位置=終値の予測ローソク足を描画できる（`predDraw = {slot, open, close, high, low}`、全て開始時の価格で初期化）。
- ドラッグ中（`mousemove`）、マウスが次のスロット（`predDraw.slot + 0.5`以上）へ移動すると、`while`ループで現在の足を`predCandles`に確定（push）し、次の足の描画へ移行する（チェーン描画）。次の足の始値・終値・高値・安値はすべて前の足の終値で初期化される。1回のマウス移動で複数スロットを跨いだ場合も全スロットを順に確定する。
- 終値は現在のマウスY座標（`clampToPriceLimit`適用後）に更新し、高値は`max(現在高値, 新終値)`、安値は`min(現在安値, 新終値)`で追従更新（ドラッグ中に往復させれば上下にヒゲが伸びる）。
- `mouseup`時、描画中の足を`predCandles`に確定してリセット（`predDraw = null`）。
- 予測足の描画色は実ローソク足と同じ（陽線`#1A9960`、陰線`#D63B3B`）。ボディ幅は`x ± 0.38`（`type: 'rect'`）、ウィックは`type: 'line'`で幅1.5、両方`layer: 'above'`。ボディ高さが0（始値=終値）の場合は`bodyHi × 1.001`で最小限の高さを確保して視認性を保つ。
- 予測足のMA5/25/75・ボリンジャーバンドは実データ末尾からの延長として都度クライアント側で再計算し（`calcPredIndicators`）、リアルタイム表示する（6.8節）。
- 描画確定後もカーソルを次スロット上に置けば継続してチェーン描画できる。
- カーソル形状: 予測描画中でなくても、マウスが次スロット位置（描画開始可能な位置）にあるときは`#chart`に`.pred-cursor`クラスが付き、要素とその子要素すべて`cursor: crosshair !important`になる（`mousemove`ハンドラで判定）。予測描画中は`chartDiv.style.cursor = 'crosshair'`を直接設定。

#### 値幅制限（ストップ高・ストップ安）クランプ

- 東証の制限値幅テーブル（基準値段の価格帯ごとに定められた制限値幅）をフロントエンドに実装（`JP_PRICE_LIMIT_TABLE`、`[基準値段の上限（未満）, 制限値幅]`の配列）:

  ```
  [100, 30], [200, 50], [500, 80], [700, 100], [1000, 150],
  [1500, 300], [2000, 400], [3000, 500], [5000, 700], [7000, 1000],
  [10000, 1500], [15000, 3000], [20000, 4000], [30000, 5000], [50000, 7000],
  [70000, 10000], [100000, 15000], [150000, 30000], [200000, 40000], [300000, 50000],
  [500000, 70000], [700000, 100000], [1000000, 150000], [1500000, 300000], [2000000, 400000],
  [3000000, 500000], [5000000, 700000], [7000000, 1000000], [10000000, 1500000], [15000000, 3000000],
  [20000000, 4000000], [30000000, 5000000], [50000000, 7000000]
  ```
  基準値段がテーブル最終行（`50000000`）以上の場合は制限値幅`10000000`を使用（`getPriceLimitWidth`）。
- 適用条件（`clampToPriceLimit`）: 銘柄ティッカーが`.T`で終わる（日本株、`isJpTicker`）かつ`currentInterval === '1d'`（日足）の場合のみ。米国株・週足・月足では制限を適用せず、価格はそのまま返す。
- 基準値段（前日終値相当、`getPredBasePrice(slot)`）: 予測対象スロットが実データ直後（`slot <= n`）なら実データの最終終値。2本目以降（`slot > n`）はチェーンで確定した直前の予測足（`slot - 1`）の終値。該当する確定足が見つからない場合は実データ最終終値にフォールバック。
- クランプ処理: `lo = max(0, base - width)`、`hi = base + width`とし、価格を`[lo, hi]`の範囲に収める（`Math.min(hi, Math.max(lo, price))`）。ドラッグ中の価格は範囲を超えて動かすことはできない。

### 6.8 予測インジケーター計算（`calcPredIndicators`）

- 対象は確定済み予測足`predCandles`＋描画中の`predDraw`（存在する場合）。
- MA75計算に必要な直近最大75本の実終値（`currentCloses`の末尾75件）を`base`とし、予測終値配列と連結した`combined`配列上で、実データ終端からの連続位置として単純移動平均（SMA5/25/75）を計算する。
- ボリンジャーバンドは同じ`combined`配列上でSMA25と標本標準偏差（`ddof=1`相当、25要素の平均との差の二乗和を24で除して平方根）を計算し、`mean ± 2×std`で上下バンドを算出。25本分のデータが確保できない位置（`pos < 24`）では`null`。
- 各指標はSMA計算に必要な本数（5/25/75）に満たない位置では`null`を返す。
- `makePredIndicatorShapes`: 実データ最終点（`x = n-1`）の指標値を起点とし、予測点群（`x = n, n+1, ...`）へ線分（`type:'line'`のPlotlyシェイプ）を繋いでいく。各線分は前後どちらかの値が`null`ならスキップ（描画しない）。MA5/25/75は幅1.2の実線、BB上下線は幅1・`dash:'dot'`（色は6.2.3節参照）。

### 6.9 Y軸範囲計算（`calcYRange`）とパン・ズーム

- `calcYRange(x0, x1)`: 表示範囲内（カテゴリインデックス`round(x0+0.5)`〜`round(x1-0.5)`）の実データ高値・安値の最大・最小を求め、さらに表示範囲に含まれる予測ローソク足（`predCandles`・`predDraw`）の高値・安値も加味する。有効な最大高値が得られない場合は`null`を返す（Y軸は`autorange`にフォールバック）。
  - **3倍比率モード（`yFitMode = false`、既定）**: `[maxHigh / 3, maxHigh × 1.01]`。上限を最高値の約1%上、下限を最高値の1/3に固定する独自の表示比率。
  - **高値安値フィットモード（`yFitMode = true`）**: 有効な最小安値がなければ`maxHigh/3`を仮の下限とし、`pad = (maxHigh - minLow) × 0.05`（表示範囲の5%）を上下に加えた`[minLow - pad, maxHigh + pad]`。
- **Xパン（ドラッグ）**: `#chart`上で予測描画スロット・直線描画モードのいずれでもない位置で`mousedown`するとパン開始（`dragState`記録）。`mousemove`でドラッグ量に応じて`xaxis.range`を平行移動し、`clampRange`で範囲を制限した上で`relayoutXY`により X・Y軸を同時更新。パン確定時は`yScale`を`1`にリセットし、Y軸はその時点の表示範囲に基づく`calcYRange`の結果（3倍比率 or フィット）に戻る。
  - `clampRange(x0, x1)`: 右方向は最低10本が表示範囲に残る位置まで（`x0 <= n - 10 - 0.5`、右側の余白表示は許容＝予測スロット側に伸ばせる）。左方向は先頭データ（`x0 >= -0.5`）より左には移動不可。
- **Yホイールズーム**: `#chart`上の`wheel`イベント（`passive:false`、`preventDefault`）で、現在のX軸表示範囲に対する基準Y範囲（`calcYRange`）を基準に`yScale`を上下スクロールに応じて`×1.08`／`÷1.08`し、`[0.05, 20]`にクランプ。`yaxis.range`/`yaxis5.range`を`[base[0]×yScale, base[1]×yScale]`に設定（Plotly標準ズームは使用しない独自実装で、上下限が常に比例して動く）。
- **Y軸表示モード切替**: `#y-fit-btn`クリックで`yFitMode`をトグルし、ボタンに`.active`クラス付与/除去。切替時`yScale`を`1`にリセットし、現在のX軸範囲に対する新モードのY範囲を即時反映。
- **候補本数ボタン（`.candle-btn`）**: クリックで`candleCount`を変更し、他ボタンの`.active`を外して自身に付与。現在のX軸右端（`fl.xaxis.range[1]`、未取得時は`n-0.5`）を保持したまま、`x1 - candleCount`を新たな左端としてクランプ後に`relayoutXY`を適用（右端固定で表示本数のみ変える）。

### 6.10 直線描画機能

- `#draw-line-btn`クリックで`lineDrawMode`をトグル。ONのとき、ボタンに`.active`、`#chart`に`.line-cursor`クラス（子要素含め`cursor: crosshair !important`）を付与。OFFに切り替えた際、描画中のプレビュー（`lineDraw`）が残っていればクリアして再描画。
- ON時、`mousedown`はパン・予測描画より優先して横取りされ、開始点を`{x0, y0, x1, y1}`（すべて開始点で初期化）として`lineDraw`にセットする。
- `mousemove`で`lineDraw`が存在する間は`x1, y1`をカーソル位置に更新し続け、`refreshPredShapes`（`requestAnimationFrame`で1フレーム1回に間引き）で再描画。プレビュー線は`rgba(59,110,232,0.6)`・幅1.5・`dash:'dash'`。
- `mouseup`時、ドラッグ量（`dx = |x1-x0|`（カテゴリインデックス差）、`dy = |y1-y0|`（価格差））が`dx > 0.2`または`dy > 1`（誤クリック防止のしきい値）を満たせば`drawnLines`に確定追加（色`#3B6EE8`・幅2・`solid`）。満たさなければ破棄。いずれの場合も`lineDraw`は`null`に戻り、保留中の`requestAnimationFrame`をキャンセルして即座に再描画する。
- 確定済みの直線は銘柄・足種を変更するかページをリロードするまで保持され続ける（`renderChart`実行時にのみクリアされる）。

### 6.11 ホバー情報バー（`#hover-info`）

- チャート初回描画後は常時表示（`display: flex`）、データ未読込時は`display: none`。
- レイアウト: 等幅フォント・サイズ11px、`padding: 4px 16px`、背景`--surface`、下枠線`--border`、横スクロール可（`overflow-x: auto`、`white-space: nowrap`）。項目は`.hi-group`（`padding: 0 10px`、先頭グループのみ`padding-left:0`）ごとに区切り線`.hi-sep`（幅1px・高さ14px・`--border`色）で分割。ラベル`.hi-lbl`は`--muted`色、値`.hi-val`は`font-weight:500`（上昇`.up`は`--green`、下落`.dn`は`--red`）。
- **実データ上でホバー中**（`showHoverInfo(idx)`）: `日付 │ O H L C(色分け) Vol │ MA5 MA25 MA75 │ BB(下–上) │ MACD Sig`の順で表示。終値の色は`close >= open`なら`up`（緑）、それ以外`dn`（赤）。
- **予測スロット上でホバー中**（`showPredHoverInfo(predIdx)`、`predDraw`は対象外・確定済み`predCandles`のみ）: `予測 +{番号} │ O H L C(色分け) │ MA5 MA25 MA75 │ BB(下–上)`（VolとMACD/Sigは無し）。番号は1始まり。
- **ホバー対象外**（`clearHoverInfo`）: 全項目を`–`のプレースホルダで表示（データ未読込時のみ非表示）。
- 数値フォーマット:
  - 価格（`fmtP`）: 整数値は`toLocaleString()`（3桁区切り）、非整数は`toLocaleString(undefined, {minimumFractionDigits:1, maximumFractionDigits:2})`。`null`は`–`。
  - 出来高（`fmtVol`）: `1e8`以上は`(v/1e8).toFixed(1) + '億'`、`1e4`以上は`Math.round(v/1e4) + '万'`、それ未満は`toLocaleString()`。`null`は`–`。
  - 指標（`fmtI`、MACD/Signal用）: `v.toFixed(2)`。`null`は`–`。
- ホバー判定は`#chart`の`mousemove`で行い、ドラッグ・予測描画・直線描画のいずれかが進行中は判定をスキップ（既存の表示を維持）。カーソルのカテゴリインデックス（`Math.round(xData)`）が実データ件数`n`未満なら実データ、以上なら予測スロット（`idx - n`）として処理を振り分ける。`mouseleave`でプレースホルダに戻し、予測描画中でなければ`.pred-cursor`クラスを除去する。

### 6.12 ローディング・エラー・空状態のUI

- **ローディング中**（`setLoading(true, ticker)`）: `#update-btn`を`disabled`、`#loading-bar`を`display:block`（内部の`#loading-bar-inner`が幅40%のグラデーションバーとして`sweep`アニメーション、1.1秒周期で左端`-40%`→右端`100%`へ往復）、`#loading-overlay`を`display:flex`（`#spinner`は14px円形、`--border`枠に`--accent`のトップ色、`spin`アニメーションで0.7秒/回転、`#loading-text`に`「{証券コード} のデータを取得中...」`）。
- **ローディング終了**（`setLoading(false)`）: 上記をすべて非表示・有効化に戻す。
- **エラー時**（`showError(msg)`）: `#error-msg`に`エラー: {message}`を表示（背景`#fff5f5`、下枠線`#fdc`、文字色`--red`、`padding:14px 20px`）。次回`fetchChart`開始時に`clearError()`で消える。
- **空状態**（`#empty-state`）: 初期表示時、チャートが一度も表示されていない間、`flex:1`で中央に「証券コードを入力してください」（`--muted`色、14px）を表示。初回チャート表示成功時に非表示化され、以後は再表示されない。
- **チャート領域**（`#chart`）: 固定高さ`760px`（`flex:none`）。デフォルトカーソルは`grab`、マウス押下中（`:active`）は`grabbing`。予測描画スロット上または直線描画モードON時はクロスヘア（`.pred-cursor`/`.line-cursor`、子要素含めて`!important`で上書き）。

## 7. テスト仕様（`backend/tests/`）

| ファイル | 対象 | テストID / 概要 |
|---|---|---|
| `test_api.py` | `/api/stock`エンドポイント | 正常系7件: `test_ap01`period指定200 / `test_ap02`start-endがget_ohlcvに伝播 / `test_ap03`start-endがperiodを上書き / `test_ap04`デフォルト90日期間 / `test_ap05`小文字ティッカーの大文字化 / `test_ap06`interval=1wkの伝播 / `test_ap07`レスポンス構造。異常系4件: `test_ap08`不正interval→400 / `test_ap09`不正period→400 / `test_ap10`start>end→400 / `test_ap11`空DataFrame→404 |
| `test_db.py` | DuckDB層 | `test_db01`テーブル作成 / `test_db02`初期化の冪等性 / `test_db03`未初期化時の例外 / `test_db04`データなし時`None` / `test_db05`最新日取得 / `test_db06`銘柄・足種ごとの分離 / `test_db07`insert & query / `test_db08`重複キーの上書き / `test_db09`日付範囲フィルタ / `test_db10`同日クエリで1行 / `test_db11`ティッカー分離 |
| `test_market.py` | `is_fresh`・指標計算 | `test_mk01`〜`test_mk09`: 日足/週足/月足それぞれの鮮度判定境界値（`freezegun`で日付固定、土曜は前週金曜扱いになることの検証を含む）。`test_mk10`〜`test_mk13`: MA5の非NULL件数、データ不足時のMA25/75が全`None`、RSIが0〜100範囲内、ボリンジャーバンドが`upper >= mid >= lower`であることの数学的検証。 |

## 8. 既知の制約・注意点

- `frontend/app.js`の`API_BASE`は`http://localhost:8000/api`にハードコードされている。本番デプロイ時は変更が必要。
- バックエンドのCORS設定（`allow_origins=["*"]`）は開発用。本番では許可オリジンを明示的に指定する必要がある。
- 認証・レート制限は未実装。
- 値幅制限クランプは東証の公表テーブルに基づく近似実装であり、実際の呼値単位（呼値の刻み幅）の丸めまでは行っていない。
- `period`パラメータは暦月・暦年ではなく単純日数換算（例: `1mo`=30日固定）。
- フロントエンドは常にAPIへ`period=10y`を要求し、表示件数はクライアント側（`candleCount`とX軸範囲）でのみ制御する。銘柄検索のサジェスト件数上限（`limit=8`）はフロントエンド側のハードコードであり、バックエンドのデフォルト上限（10）とは独立している。
- 予測ローソク足・直線描画はブラウザメモリ上のみに保持され、リロードや銘柄・足種の変更で消去される（サーバー側への保存機能はない）。
