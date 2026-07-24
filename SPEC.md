# 株価チャートビューア 技術仕様書

## 1. 概要

日本株・米国株のOHLCV（四本値+出来高）チャートをブラウザで表示するWebアプリケーション。Yahoo Finance からデータを取得し、DuckDB にキャッシュしつつ、テクニカル指標（移動平均・ボリンジャーバンド・MACD・RSI）を付加してフロントエンドに提供する。フロントエンドは Plotly.js を用いてローソク足チャートを描画し、マウス操作による予測ローソク足の手描き・直線描画などの独自インタラクションを備える。

利用者向けの操作マニュアルは `manual.html` を参照。本書はシステムの内部構成・API・データモデル・計算式・機能仕様を対象とする技術仕様書。

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
| フロントエンド | Vanilla JavaScript（フレームワーク・ビルドツールなし） |
| チャート描画 | Plotly.js 2.35.2（CDN読込、SRI検証あり） |

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
- CORS: `allow_origins=["*"]`、`allow_methods=["GET"]`のみ、`allow_headers=["*"]`（開発用設定。本番デプロイ時は要変更）。
- ルーターは両方とも`prefix="/api"`でマウント。認証・レート制限は未実装。

### 5.2 API仕様

#### `GET /api/stock/{ticker}`

**パスパラメータ**
- `ticker`: 正規表現 `^[A-Za-z0-9.\-^]{1,20}$` に一致しない場合 `400 Invalid ticker format`。

**クエリパラメータ**

| 名前 | 型 | デフォルト | 説明 |
|---|---|---|---|
| `interval` | string | `"1d"` | `1d` / `1wk` / `1mo` のいずれか。不正値は `400 Invalid interval` |
| `period` | string | なし | `1mo`(30日) / `3mo`(90日) / `6mo`(180日) / `1y`(365日) / `5y`(1825日) / `10y`(3650日)。単純日数換算で、暦月・暦年ではない。不正値は `400 Invalid period: {period}` |
| `start` / `end` | date (ISO) | なし | 明示的な期間指定。`start > end` は `400 start must be before end` |

**日付範囲の解決優先順位**
1. `start`/`end` が両方指定されていればそれを使用（`period`は無視）
2. `period`のみ指定 → `today - period日数` 〜 `today`
3. どちらも未指定 → 直近90日間（デフォルト）

**処理フロー**: `market.get_ohlcv(ticker, interval, start, end)` でデータ取得。空であれば `404 Ticker not found: {ticker}`。続けて `market.get_indicators(...)` で指標を取得し結合。

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
    "ma": { "5": [...], "25": [...], "75": [...] },
    "bollinger": { "upper": [...], "mid": [...], "lower": [...] },
    "macd": { "macd": [...], "signal": [...], "hist": [...] },
    "rsi": [...]
  }
}
```
`name`はDB上の銘柄マスタに登録がなければ`ticker`自体を返す。指標値は小数第4位に丸め、NaNは`null`。

#### `GET /api/securities/search`

| クエリ | 型 | 制約 |
|---|---|---|
| `q` | string | 最大50文字、デフォルト`""` |
| `limit` | int | 1〜50、デフォルト10 |

`q`が空文字なら空配列を返す（バリデーションエラーにはしない）。レスポンス:
```json
[{ "ticker": "7203.T", "code": "7203", "name": "トヨタ自動車", "market": "JP" }]
```
検索は完全一致 > 前方一致 > 部分一致の優先順で`market, code`昇順にソート。

### 5.3 データ取得・キャッシュ戦略（`services/market.py`）

**鮮度判定（`is_fresh`）**: DB内の最新日付が以下の基準日以降であれば「新鮮」とみなし、Yahoo Financeへの再取得をスキップする。

| interval | 基準日 |
|---|---|
| `1d` | 直近平日（土日ならその週の金曜まで遡る） |
| `1wk` | 今週の月曜 |
| `1mo` | 今月1日 |
| その他 | 常に古い扱い（毎回再取得） |

**Yahoo Finance取得**: `https://query2.finance.yahoo.com/v8/finance/chart/{ticker}` を`httpx`で直接呼び出し（`yfinance`ライブラリは不使用）。
- 初回取得: `range=10y` で全期間取得。
- 差分取得: DB内最新日の翌日を`period1`、現在時刻を`period2`としてUnixタイムスタンプで指定。
- タイムアウト20秒。取得失敗時は警告ログを出し「更新なし」として扱う（例外を上位に伝播させない）。

**更新フロー（`get_ohlcv`）**:
1. DB内最新日を取得し鮮度判定。
2. 古ければYahooから差分取得。
3. 新規データがある場合: 全期間OHLCVと結合（重複除去・日付ソート）→ 指標を全期間で再計算 → OHLCVと指標を1トランザクションで書き込み（`upsert_ohlcv_and_indicators`）。
4. 新規データがなく指標だけ未計算の場合は指標のみ再計算・保存。
5. 要求された期間のOHLCVをDBから返す。

### 5.4 テクニカル指標の計算式

| 指標 | 計算方法 |
|---|---|
| MA5 / MA25 / MA75 | 単純移動平均（`rolling(n).mean()`） |
| ボリンジャーバンド | 期間25、σ=2.0固定。中心線=SMA25、標準偏差=`rolling(25).std()`（標本標準偏差、ddof=1）。上下バンド = 中心線 ± 2×標準偏差 |
| MACD(12,26,9) | EMA12 − EMA26 = MACD線、MACD線のEMA9 = シグナル線、MACD線−シグナル線 = ヒストグラム（EMAは`ewm(span=n, adjust=False).mean()`） |
| RSI(14) | Wilder方式のEMA平滑化（`alpha=1/14`）。値上がり幅・値下がり幅それぞれのEMAを取り `RSI = 100 − 100/(1+平均上げ幅/平均下げ幅)` |

指標値はAPIレスポンスに変換する際、`round(value, 4)`で丸め、NaN/Noneは`null`にする。

### 5.5 DuckDBスキーマ（`services/db.py`）

**`ohlcv`**
```
ticker VARCHAR, date DATE, interval VARCHAR,
open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE NOT NULL, volume BIGINT,
PRIMARY KEY (ticker, date, interval)
```

**`indicators`**
```
ticker VARCHAR, date DATE, interval VARCHAR,
ma5 DOUBLE, ma25 DOUBLE, ma75 DOUBLE,
bb_upper DOUBLE, bb_mid DOUBLE, bb_lower DOUBLE,
macd DOUBLE, macd_signal DOUBLE, macd_hist DOUBLE, rsi DOUBLE,
PRIMARY KEY (ticker, date, interval)
```

**`securities`**
```
ticker VARCHAR PRIMARY KEY, code VARCHAR NOT NULL, name VARCHAR NOT NULL, market VARCHAR NOT NULL
```

グローバル接続を`threading.Lock`で保護し、複数リクエストからの同時アクセスに対応。OHLCVと指標の同時書き込みは単一トランザクション（`begin`/`commit`/`rollback`）で原子性を担保する。

### 5.6 銘柄マスタ取得（`services/securities.py`）

- **日本株**: JPX公式サイトの`data_j.xls`をダウンロードし、4桁の証券コードを`{code}.T`ティッカーとして登録（`market=JP`）。
- **米国株**: NASDAQ Traderの`nasdaqlisted.txt`・`otherlisted.txt`（パイプ区切り）をダウンロードし、テスト銘柄・不正シンボルを除外して登録（`market=US`）。
- いずれもDB内に該当市場のデータが0件のときのみ取得を実行し、起動のたびに再取得はしない。

## 6. フロントエンド仕様（`frontend/app.js`）

### 6.1 グローバル状態

| 変数 | 役割 |
|---|---|
| `currentDates/Opens/Highs/Lows/Closes/Vols` | 表示中の実データ配列 |
| `currentInd` | 現在の指標データ（ma/bollinger/macd/rsi） |
| `currentTicker` / `currentInterval` | 表示中の銘柄ティッカー・足種（値幅制限の市場判定に使用） |
| `predCandles` / `predDraw` | 確定済み／描画中の予測ローソク足 `{slot, open, close, high, low}` |
| `drawnLines` / `lineDraw` | 確定済み／描画中の直線 `{x0,y0,x1,y1}` |
| `dragState` | チャートパン中のドラッグ状態 |
| `yScale` | ホイールによるY軸スケール係数 |
| `yFitMode` | Y軸表示モード（3倍比率 / 高値安値フィット） |
| `candleCount` | 表示本数（デフォルト150） |

### 6.2 チャート描画（`renderChart`）

Plotly.jsの複数y軸に以下を割り当てて1チャートに合成する。

| y軸 | 内容 | domain |
|---|---|---|
| `yaxis` | ローソク足 + MA5/25/75 + ボリンジャーバンド | `[0.26, 1]` |
| `yaxis2` | 出来高（棒グラフ） | `[0.14, 0.25]` |
| `yaxis3` | MACD（ヒストグラム＋MACD線＋シグナル線） | `[0, 0.13]` |
| `yaxis5` | メイン価格軸の右側ラベル表示専用（`overlaying:'y'`） | `yaxis`と連動 |

追加要素: 直近25本・26〜75本前の背景色分けゾーン、年境界の縦線とラベル。Plotly標準のドラッグ・ズームは無効化(`dragmode:false`)し、独自のマウスイベントで代替する。

### 6.3 予測ローソク足描画機能

- チャート右端の余白（実データ直後のスロット）でマウスドラッグすると、ドラッグ開始位置=始値・現在位置=終値の予測ローソク足を描画できる。
- マウスが次のスロットへ移動すると自動的に現在の足を確定し、次の足の描画へ移行する（チェーン描画。次の足の始値=前の足の終値）。
- 予測足のMA5/25/75・ボリンジャーバンドは実データ末尾からの延長として都度クライアント側で再計算し、リアルタイム表示する。

**値幅制限（ストップ高・ストップ安）クランプ**
- 東証の制限値幅テーブル（基準値段の価格帯ごとに定められた制限値幅）をフロントエンドに実装。
- 適用条件: 銘柄ティッカーが`.T`で終わる（日本株）かつ`currentInterval === '1d'`（日足）の場合のみ。米国株・週足・月足では制限を適用しない。
- 基準値段（前日終値相当）は、最初の予測足では実データの最終終値、2本目以降はチェーンで確定した直前の予測足の終値を用いる。
- ドラッグ中の価格は `[基準値段 − 制限値幅, 基準値段 + 制限値幅]` の範囲にクランプされ、範囲を超えて動かすことはできない。

### 6.4 直線描画機能

- ツールバーのボタンで直線描画モードのON/OFFを切替。ON時はドラッグ操作がパン・予測描画より優先され、ドラッグ開始点から終了点までの直線プレビューを表示する。
- マウスアップ時、ドラッグ量が一定以上（誤クリック防止のしきい値）であれば直線を確定して保持する。

### 6.5 パン・ズーム

- **Xパン**: 先頭データより左には移動不可。右方向は最低10本が表示範囲に残るよう制限（右側の余白表示は許容）。
- **Yホイールズーム**: マウスホイールでY軸の表示スケールを`0.05〜20`倍の範囲で変更（Plotly標準ズームは使用しない独自実装）。
- **Y軸表示モード切替**: 「3倍比率モード」（表示範囲内の最高値の1/3を下限とする独自表示）と「高値安値フィットモード」（表示範囲内の高安に対して上下5%の余白を付ける表示）をボタンでトグル。

### 6.6 オートコンプリート

- ティッカー入力欄に180msデバウンスで`/api/securities/search`を呼び出し、候補をドロップダウン表示（日本株/米国株を色分け）。
- 矢印キーでの候補選択、Enter確定、Escapeで閉じる操作に対応。4桁数字の入力はドロップダウン未選択時に自動で`.T`を付与して日本株ティッカーとして扱う。

## 7. テスト仕様（`backend/tests/`）

| ファイル | 対象 | 概要 |
|---|---|---|
| `test_api.py` | `/api/stock`エンドポイント | 正常系7件（period/start-end優先/デフォルト期間/大文字化/interval伝播/レスポンス構造）、異常系4件（不正interval/period/start>end/データなし404） |
| `test_db.py` | DuckDB層 | テーブル作成・冪等性、最新日取得、銘柄・足種ごとの分離、upsertの重複上書き、範囲クエリ |
| `test_market.py` | `is_fresh`・指標計算 | 日足/週足/月足それぞれの鮮度判定境界値（`freezegun`で日付固定）、MA/BB/RSIの数学的検証 |

## 8. 既知の制約・注意点

- `frontend/app.js`の`API_BASE`は`http://localhost:8000/api`にハードコードされている。本番デプロイ時は変更が必要。
- バックエンドのCORS設定（`allow_origins=["*"]`）は開発用。本番では許可オリジンを明示的に指定する必要がある。
- 認証・レート制限は未実装。
- 値幅制限クランプは東証の公表テーブルに基づく近似実装であり、実際の呼値単位（呼値の刻み幅）の丸めまでは行っていない。
- `period`パラメータは暦月・暦年ではなく単純日数換算（例: `1mo`=30日固定）。
