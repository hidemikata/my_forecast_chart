const API_BASE = 'http://localhost:8000/api';

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const tickerInput      = document.getElementById('ticker-input');
const tickerDropdown   = document.getElementById('ticker-dropdown');
const intervalSel      = document.getElementById('interval-select');
const updateBtn        = document.getElementById('update-btn');
const loadingBar       = document.getElementById('loading-bar');
const loadingOverlay   = document.getElementById('loading-overlay');
const loadingText      = document.getElementById('loading-text');
const errorMsg         = document.getElementById('error-msg');
const chartDiv         = document.getElementById('chart');
const emptyState       = document.getElementById('empty-state');
const latestDate       = document.getElementById('latest-date');
const hoverInfo        = document.getElementById('hover-info');
const drawLineBtn      = document.getElementById('draw-line-btn');
const yFitBtn          = document.getElementById('y-fit-btn');

let selectedTicker    = null;   // フルティッカー ("9984.T" / "AAPL")
let ddActiveIndex     = -1;     // ドロップダウンキーボード選択インデックス
let currentDates      = [];     // 描画中の日付配列（ドラッグ用）
let currentHighs      = [];     // 高値配列（Y軸範囲計算用）
let currentOpens      = [];
let currentLows       = [];
let currentCloses     = [];
let currentVols       = [];
let currentInd        = null;   // インジケーター全配列
let currentTicker     = null;   // 現在表示中のティッカー（値幅制限の市場判定用）
let currentInterval   = null;   // 現在表示中の足種
let candleCount       = 150;    // 表示本数
let dragState         = null;
let predCandles       = [];     // 確定済み予測ローソク足 { slot, open, close }
let predDraw          = null;   // 描画中の予測 { slot, open, close }
let currentBaseShapes = [];     // renderChart で生成したベースシェイプ
let yScale            = 1;      // ホイールによる Y 軸スケール係数（パンでリセット）
let lineDrawMode      = false;  // 直線描画モードON/OFF
let drawnLines        = [];     // 確定済み直線 [{x0,y0,x1,y1}, ...]
let lineDraw          = null;   // 描画中の直線プレビュー or null
let yFitMode          = false;  // false=3倍比率, true=高値・安値フィット

// ---- ティッカー表示ヘルパー ----
function displayCode(ticker) {
  return ticker.endsWith('.T') ? ticker.slice(0, -2) : ticker;
}

// ---- 正規化（ドロップダウン未選択時のフォールバック） ----
function normalizeTicker(raw) {
  const t = raw.trim().toUpperCase();
  if (/^\d{4}$/.test(t)) return t + '.T';
  return t;
}

// ---- 足種変更で自動更新 ----
intervalSel.addEventListener('change', () => {
  if (tickerInput.value.trim()) fetchChart();
});

// ---- ローディング制御 ----
function setLoading(on, ticker = '') {
  updateBtn.disabled           = on;
  loadingBar.style.display     = on ? 'block' : 'none';
  loadingOverlay.style.display = on ? 'flex' : 'none';
  loadingText.textContent      = on ? `${displayCode(ticker)} のデータを取得中...` : '';
}

function showError(msg) {
  errorMsg.textContent   = msg;
  errorMsg.style.display = 'block';
}

function clearError() {
  errorMsg.textContent   = '';
  errorMsg.style.display = 'none';
}

// ---- オートコンプリート ----
let debounceTimer = null;

tickerInput.addEventListener('input', () => {
  selectedTicker = null;
  ddActiveIndex  = -1;
  const q = tickerInput.value.trim();
  clearTimeout(debounceTimer);
  if (!q) { closeDropdown(); return; }
  debounceTimer = setTimeout(() => fetchSuggestions(q), 180);
});

tickerInput.addEventListener('keydown', e => {
  const items = tickerDropdown.querySelectorAll('li');

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    ddActiveIndex = Math.min(ddActiveIndex + 1, items.length - 1);
    updateDdActive(items);
    return;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    ddActiveIndex = Math.max(ddActiveIndex - 1, -1);
    updateDdActive(items);
    return;
  }
  if (e.key === 'Enter') {
    if (ddActiveIndex >= 0 && items[ddActiveIndex]) {
      selectItem(items[ddActiveIndex]);
    } else {
      fetchChart();
    }
    return;
  }
  if (e.key === 'Escape') {
    closeDropdown();
    return;
  }
  // 印字可能文字のみ制限（制御キーは通す）
  if (e.key.length === 1 && !/[A-Za-z0-9^.]/.test(e.key)) {
    e.preventDefault();
  }
});

tickerInput.addEventListener('blur', () => {
  setTimeout(closeDropdown, 150);
});

function updateDdActive(items) {
  items.forEach((li, i) => li.classList.toggle('active', i === ddActiveIndex));
}

async function fetchSuggestions(q) {
  try {
    const res = await fetch(`${API_BASE}/securities/search?q=${encodeURIComponent(q)}&limit=8`);
    if (!res.ok) return;
    const items = await res.json();
    renderDropdown(items);
  } catch (_) { /* サーバー未起動時は無視 */ }
}

function renderDropdown(items) {
  if (!items.length) { closeDropdown(); return; }
  tickerDropdown.innerHTML = items.map((item, i) => {
    const marketClass = item.market === 'JP' ? 'jp' : 'us';
    return `<li data-ticker="${escHtml(item.ticker)}" data-code="${escHtml(item.code)}" data-i="${i}">
      <span class="dd-code">${escHtml(item.code)}</span>
      <span class="dd-name">${escHtml(item.name)}</span>
      <span class="dd-market ${marketClass}">${escHtml(item.market)}</span>
    </li>`;
  }).join('');
  tickerDropdown.querySelectorAll('li').forEach(li => {
    li.addEventListener('mousedown', e => { e.preventDefault(); selectItem(li); });
  });
  ddActiveIndex = -1;
}

function selectItem(li) {
  selectedTicker = li.dataset.ticker;
  tickerInput.value = li.dataset.code;
  closeDropdown();
  fetchChart();
}

function closeDropdown() {
  tickerDropdown.innerHTML = '';
  ddActiveIndex = -1;
}

// ---- URL 組み立て ----
function buildUrl() {
  const raw = tickerInput.value.trim();
  if (!raw) return null;

  const ticker = selectedTicker || normalizeTicker(raw);
  tickerInput.value = displayCode(ticker);

  const interval = intervalSel.value;
  const params   = new URLSearchParams({ interval, period: '10y' });

  return { url: `${API_BASE}/stock/${encodeURIComponent(ticker)}?${params}`, ticker };
}

// ---- データ取得 ----
async function fetchChart() {
  closeDropdown();
  const built = buildUrl();
  if (!built) { showError('証券コードを入力してください'); return; }
  const { url, ticker } = built;

  clearError();
  setLoading(true, ticker);

  try {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    renderChart(data);
    emptyState.style.display = 'none';
    chartDiv.style.display   = 'block';
  } catch (e) {
    showError(`エラー: ${e.message}`);
  } finally {
    setLoading(false);
  }
}

updateBtn.addEventListener('click', fetchChart);

// ---- Y軸範囲計算 ----
function calcYRange(x0, x1) {
  const start = Math.max(0, Math.round(x0 + 0.5));
  const end   = Math.min(currentHighs.length - 1, Math.round(x1 - 0.5));
  let maxHigh = -Infinity;
  let minLow  =  Infinity;
  for (let i = start; i <= end; i++) {
    if (currentHighs[i] != null && currentHighs[i] > maxHigh) maxHigh = currentHighs[i];
    if (currentLows[i]  != null && currentLows[i]  < minLow)  minLow  = currentLows[i];
  }
  // 表示範囲内の予測ローソク足も加味
  [...predCandles, ...(predDraw ? [predDraw] : [])].forEach(p => {
    if (p.slot >= x0 - 0.5 && p.slot <= x1 + 0.5) {
      const hi = Math.max(p.open, p.close);
      const lo = Math.min(p.open, p.close);
      if (hi > maxHigh) maxHigh = hi;
      if (lo < minLow)  minLow  = lo;
    }
  });
  if (!isFinite(maxHigh) || maxHigh <= 0) return null;
  if (yFitMode) {
    if (!isFinite(minLow)) minLow = maxHigh / 3;
    const pad = (maxHigh - minLow) * 0.05;  // 上下5%の余白
    return [minLow - pad, maxHigh + pad];
  }
  return [maxHigh / 3, maxHigh * 1.01];
}

// ---- X軸+Y軸 一括更新（パン時は yScale をリセットして auto 範囲に戻す） ----
function relayoutXY(x0, x1) {
  yScale = 1;
  const yRange = calcYRange(x0, x1);
  Plotly.relayout(chartDiv, {
    'xaxis.range':      [x0, x1],
    ...(yRange
      ? { 'yaxis.range': yRange, 'yaxis5.range': yRange }
      : { 'yaxis.autorange': true }),
    'yaxis2.autorange': true,
    'yaxis3.autorange': true,
  });
}

// ---- X軸クランプ ----
// 右方向: 最低10本が見える位置まで（右側に余白を表示可能）
// 左方向: 先頭足より左には行けない
function clampRange(x0, x1) {
  const n    = currentDates.length;
  const span = x1 - x0;

  if (x0 > n - 10 - 0.5) { x0 = n - 10 - 0.5; x1 = x0 + span; }
  if (x0 < -0.5)          { x0 = -0.5;          x1 = x0 + span; }

  return [x0, x1];
}

// ---- ホバー情報表示 ----

function fmtP(v) {
  if (v == null) return '–';
  return Number.isInteger(v) ? v.toLocaleString()
    : v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 2 });
}

function fmtVol(v) {
  if (v == null) return '–';
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '億';
  if (v >= 1e4) return Math.round(v / 1e4) + '万';
  return v.toLocaleString();
}

function fmtI(v) {
  if (v == null) return '–';
  return v.toFixed(2);
}

function showHoverInfo(idx) {
  if (!currentInd || idx < 0 || idx >= currentDates.length) {
    clearHoverInfo();
    return;
  }
  const d    = currentDates[idx];
  const o    = currentOpens[idx];
  const h    = currentHighs[idx];
  const l    = currentLows[idx];
  const c    = currentCloses[idx];
  const v    = currentVols[idx];
  const ind  = currentInd;
  const cls  = c >= o ? 'up' : 'dn';

  const ma5  = ind.ma['5'][idx];
  const ma25 = ind.ma['25'][idx];
  const ma75 = ind.ma['75'][idx];
  const bbU  = ind.bollinger.upper[idx];
  const bbL  = ind.bollinger.lower[idx];
  const macd = ind.macd.macd[idx];
  const sig  = ind.macd.signal[idx];

  hoverInfo.innerHTML =
    `<span class="hi-group"><span class="hi-lbl">${d}</span></span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">O</span><span class="hi-val">${fmtP(o)}</span>` +
      `<span class="hi-lbl">H</span><span class="hi-val">${fmtP(h)}</span>` +
      `<span class="hi-lbl">L</span><span class="hi-val">${fmtP(l)}</span>` +
      `<span class="hi-lbl">C</span><span class="hi-val ${cls}">${fmtP(c)}</span>` +
      `<span class="hi-lbl">Vol</span><span class="hi-val">${fmtVol(v)}</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">MA5</span><span class="hi-val">${fmtP(ma5)}</span>` +
      `<span class="hi-lbl">MA25</span><span class="hi-val">${fmtP(ma25)}</span>` +
      `<span class="hi-lbl">MA75</span><span class="hi-val">${fmtP(ma75)}</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">BB</span><span class="hi-val">${fmtP(bbL)} – ${fmtP(bbU)}</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">MACD</span><span class="hi-val">${fmtI(macd)}</span>` +
      `<span class="hi-lbl">Sig</span><span class="hi-val">${fmtI(sig)}</span>` +
    `</span>`;

  hoverInfo.style.display = 'flex';
}

function clearHoverInfo() {
  if (!currentDates.length) { hoverInfo.style.display = 'none'; return; }
  hoverInfo.innerHTML =
    `<span class="hi-group"><span class="hi-lbl">–</span></span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">O</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">H</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">L</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">C</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">Vol</span><span class="hi-val">–</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">MA5</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">MA25</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">MA75</span><span class="hi-val">–</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group"><span class="hi-lbl">BB</span><span class="hi-val">– – –</span></span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">MACD</span><span class="hi-val">–</span>` +
      `<span class="hi-lbl">Sig</span><span class="hi-val">–</span>` +
    `</span>`;
  hoverInfo.style.display = 'flex';
}

// ---- 予測ローソク足ヘルパー ----

// スクリーンX座標 → カテゴリインデックス
function screenToXIndex(clientX, fl) {
  const rect = chartDiv.getBoundingClientRect();
  const mx   = clientX - rect.left - fl._size.l;
  return fl.xaxis.range[0] + (mx / fl._size.w) * (fl.xaxis.range[1] - fl.xaxis.range[0]);
}

// スクリーンY座標 → 株価
function screenToPrice(clientY, fl) {
  const rect   = chartDiv.getBoundingClientRect();
  const my     = clientY - rect.top;
  const domain = fl.yaxis.domain;                   // [0.37, 1]
  const axTop  = fl._size.t + (1 - domain[1]) * fl._size.h;
  const axBot  = fl._size.t + (1 - domain[0]) * fl._size.h;
  const frac   = (my - axTop) / (axBot - axTop);
  const yr     = fl.yaxis.range;
  return yr[1] - frac * (yr[1] - yr[0]);
}

// ---- 値幅制限（ストップ高・ストップ安） ----
// 東証の制限値幅テーブル（基準値段の上限（未満）→ 制限値幅）
const JP_PRICE_LIMIT_TABLE = [
  [100, 30], [200, 50], [500, 80], [700, 100], [1000, 150],
  [1500, 300], [2000, 400], [3000, 500], [5000, 700], [7000, 1000],
  [10000, 1500], [15000, 3000], [20000, 4000], [30000, 5000], [50000, 7000],
  [70000, 10000], [100000, 15000], [150000, 30000], [200000, 40000], [300000, 50000],
  [500000, 70000], [700000, 100000], [1000000, 150000], [1500000, 300000], [2000000, 400000],
  [3000000, 500000], [5000000, 700000], [7000000, 1000000], [10000000, 1500000], [15000000, 3000000],
  [20000000, 4000000], [30000000, 5000000], [50000000, 7000000],
];

function getPriceLimitWidth(basePrice) {
  for (const [upper, width] of JP_PRICE_LIMIT_TABLE) {
    if (basePrice < upper) return width;
  }
  return 10000000;
}

function isJpTicker(ticker) {
  return !!ticker && ticker.endsWith('.T');
}

// 予測スロットの基準値段（前日終値相当）: 最初の予測足は実データ最終終値、
// 2本目以降はチェーンで確定した直前の予測足の終値
function getPredBasePrice(slot) {
  const n = currentDates.length;
  if (slot <= n) return currentCloses[currentCloses.length - 1];
  const prev = predCandles.find(c => c.slot === slot - 1);
  return prev ? prev.close : currentCloses[currentCloses.length - 1];
}

// 日本株の日足のみ、基準値段に応じたストップ高・ストップ安の範囲にクランプする
function clampToPriceLimit(price, slot) {
  if (currentInterval !== '1d' || !isJpTicker(currentTicker)) return price;
  const base = getPredBasePrice(slot);
  if (base == null || !isFinite(base)) return price;
  const width = getPriceLimitWidth(base);
  const hi = base + width;
  const lo = Math.max(0, base - width);
  return Math.min(hi, Math.max(lo, price));
}

// { slot, open, high, low, close } → Plotly shapes（ボディ + ウィック）
function predToShapes(p) {
  const bodyLo = Math.min(p.open, p.close);
  const bodyHi = Math.max(p.open, p.close);
  const bodyHi2 = bodyHi > bodyLo ? bodyHi : bodyLo * 1.001;
  const wickHi  = p.high ?? bodyHi2;
  const wickLo  = p.low  ?? bodyLo;
  const color   = p.close >= p.open ? '#1A9960' : '#D63B3B';
  const shapes  = [];
  // ウィック（高値〜安値の縦線）
  shapes.push({
    type: 'line', xref: 'x', yref: 'y',
    x0: p.slot, x1: p.slot,
    y0: wickLo, y1: wickHi,
    line: { color, width: 1.5 },
    layer: 'above',
  });
  // ボディ
  shapes.push({
    type: 'rect', xref: 'x', yref: 'y',
    x0: p.slot - 0.38, x1: p.slot + 0.38,
    y0: bodyLo, y1: bodyHi2,
    fillcolor: color,
    line: { width: 0 },
    layer: 'above',
  });
  return shapes;
}

// { x0, y0, x1, y1 } → Plotly line shape
function lineToShape(l, isPreview) {
  return {
    type: 'line', xref: 'x', yref: 'y',
    x0: l.x0, y0: l.y0, x1: l.x1, y1: l.y1,
    line: {
      color: isPreview ? 'rgba(59,110,232,0.6)' : '#3B6EE8',
      width: isPreview ? 1.5 : 2,
      dash:  isPreview ? 'dash' : 'solid',
    },
    layer: 'above',
  };
}

// ---- 予測スロットの MA5/MA25/MA75/BB を計算 ----
function calcPredIndicators() {
  const allPreds = [...predCandles, ...(predDraw ? [predDraw] : [])];
  if (!allPreds.length || !currentCloses.length) return [];

  const predCloses = allPreds.map(p => p.close);
  const k = predCloses.length;
  // MA75 計算に必要な最大 75 本の実終値を確保
  const base     = currentCloses.slice(Math.max(0, currentCloses.length - 75));
  const combined = [...base, ...predCloses];
  const baseLen  = base.length;

  const results = [];
  for (let i = 0; i < k; i++) {
    const pos = baseLen + i;

    const sma = (n) => {
      if (pos < n - 1) return null;
      let s = 0;
      for (let j = pos - n + 1; j <= pos; j++) s += combined[j];
      return s / n;
    };

    const ma5  = sma(5);
    const ma25 = sma(25);
    const ma75 = sma(75);

    let bbUpper = null, bbLower = null;
    if (pos >= 24) {
      const mean = sma(25);
      let v = 0;
      for (let j = pos - 24; j <= pos; j++) v += (combined[j] - mean) ** 2;
      const std = Math.sqrt(v / 24);  // pandas rolling.std() は ddof=1
      bbUpper = mean + 2 * std;
      bbLower = mean - 2 * std;
    }

    results.push({ ma5, ma25, ma75, bbUpper, bbLower });
  }
  return results;
}

// ---- 予測インジケーターを Plotly line shapes に変換 ----
function makePredIndicatorShapes(predInds) {
  if (!predInds.length || !currentInd) return [];
  const n = currentDates.length;

  const MA_COLORS = { ma5: '#E0AF68', ma25: '#BB9AF7', ma75: '#7AA2F7' };
  const BB_COLOR  = '#3B6EE8';

  // 最終実データ点 + 予測点
  const pts = [
    {
      x:   n - 1,
      ma5:  currentInd.ma['5'][n - 1],
      ma25: currentInd.ma['25'][n - 1],
      ma75: currentInd.ma['75'][n - 1],
      bbU:  currentInd.bollinger.upper[n - 1],
      bbL:  currentInd.bollinger.lower[n - 1],
    },
    ...predInds.map((p, i) => ({
      x:   n + i,
      ma5:  p.ma5,  ma25: p.ma25,  ma75: p.ma75,
      bbU:  p.bbUpper,  bbL:  p.bbLower,
    })),
  ];

  const shapes = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    const seg = (y0, y1, color, width, dash) => {
      if (y0 == null || y1 == null) return;
      shapes.push({
        type: 'line', xref: 'x', yref: 'y',
        x0: a.x, x1: b.x, y0, y1,
        line: { color, width: width ?? 1.2, dash: dash ?? 'solid' },
        layer: 'above',
      });
    };
    seg(a.ma5,  b.ma5,  MA_COLORS.ma5,  1.2);
    seg(a.ma25, b.ma25, MA_COLORS.ma25, 1.2);
    seg(a.ma75, b.ma75, MA_COLORS.ma75, 1.2);
    seg(a.bbU,  b.bbU,  BB_COLOR, 1, 'dot');
    seg(a.bbL,  b.bbL,  BB_COLOR, 1, 'dot');
  }
  return shapes;
}

// 予測シェイプ + ベースシェイプ + Y軸を一括更新（yScale を維持）
// RAF でフレームあたり最大 1 回に絞り、mousemove の過剰呼び出しを吸収する
let _predRafId = null;

function refreshPredShapes() {
  if (_predRafId !== null) cancelAnimationFrame(_predRafId);
  _predRafId = requestAnimationFrame(_applyPredShapes);
}

function _applyPredShapes() {
  _predRafId = null;
  const fl = chartDiv._fullLayout;
  if (!fl) return;
  const predShapes = [];
  [...predCandles, ...(predDraw ? [predDraw] : [])].forEach(p => {
    predShapes.push(...predToShapes(p));
  });
  const indicatorShapes = makePredIndicatorShapes(calcPredIndicators());
  const lineShapes  = drawnLines.map(l => lineToShape(l, false));
  const previewLine = lineDraw ? [lineToShape(lineDraw, true)] : [];
  const shapes = [...currentBaseShapes, ...indicatorShapes, ...predShapes, ...lineShapes, ...previewLine];
  const xr     = fl.xaxis.range;
  const yr     = calcYRange(xr[0], xr[1]);
  const update = { shapes };
  if (yr) {
    update['yaxis.range']  = [yr[0] * yScale, yr[1] * yScale];
    update['yaxis5.range'] = [yr[0] * yScale, yr[1] * yScale];
  }
  Plotly.relayout(chartDiv, update);
}

// ---- 本数ボタン ----
// ---- Y軸表示モード切替 ----
yFitBtn.addEventListener('click', () => {
  yFitMode = !yFitMode;
  yFitBtn.classList.toggle('active', yFitMode);
  if (!currentDates.length) return;
  yScale = 1;
  const fl = chartDiv._fullLayout;
  if (!fl?.xaxis?.range) return;
  const [x0, x1] = fl.xaxis.range;
  const yRange = calcYRange(x0, x1);
  if (yRange) {
    Plotly.relayout(chartDiv, {
      'yaxis.range':  yRange,
      'yaxis5.range': yRange,
    });
  }
});

// ---- 直線描画モードON/OFF ----
drawLineBtn.addEventListener('click', () => {
  lineDrawMode = !lineDrawMode;
  drawLineBtn.classList.toggle('active', lineDrawMode);
  chartDiv.classList.toggle('line-cursor', lineDrawMode);
  if (!lineDrawMode && lineDraw) {
    lineDraw = null;
    _applyPredShapes();
  }
});

document.querySelectorAll('.candle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.candle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    candleCount = parseInt(btn.dataset.count, 10);
    if (!currentDates.length) return;
    const fl  = chartDiv._fullLayout;
    const n   = currentDates.length;
    const x1c = fl?.xaxis?.range?.[1] ?? n - 0.5;
    const [x0, x1] = clampRange(x1c - candleCount, x1c);
    relayoutXY(x0, x1);
  });
});

// ---- ドラッグパン / 予測描画 ----
chartDiv.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  const fl = chartDiv._fullLayout;
  if (!fl?.xaxis?.range || !currentDates.length) return;

  // 直線描画モードON時: パン・予測描画より先に横取り
  if (lineDrawMode) {
    const x = screenToXIndex(e.clientX, fl);
    const y = screenToPrice(e.clientY, fl);
    lineDraw = { x0: x, y0: y, x1: x, y1: y };
    e.preventDefault();
    return;
  }

  const xData    = screenToXIndex(e.clientX, fl);
  const n        = currentDates.length;
  const nextSlot = n + predCandles.length;

  // 次の予測スロット（最新足の直後）→ 予測描画開始
  if (xData >= nextSlot - 0.5 && xData < nextSlot + 0.5) {
    const price = clampToPriceLimit(screenToPrice(e.clientY, fl), nextSlot);
    predDraw = { slot: nextSlot, open: price, close: price, high: price, low: price };
    chartDiv.style.cursor = 'crosshair';
    e.preventDefault();
    return;
  }

  // それ以外 → パン
  dragState = {
    startX:    e.clientX,
    range0:    [...fl.xaxis.range],
    plotWidth: fl._size?.w || chartDiv.clientWidth,
  };
  e.preventDefault();
}, true);

document.addEventListener('mousemove', e => {
  // 直線プレビュー更新
  if (lineDraw) {
    const fl = chartDiv._fullLayout;
    if (!fl) return;
    lineDraw = {
      ...lineDraw,
      x1: screenToXIndex(e.clientX, fl),
      y1: screenToPrice(e.clientY, fl),
    };
    refreshPredShapes();
    return;
  }

  // 予測描画中
  if (predDraw) {
    const fl = chartDiv._fullLayout;
    if (!fl) return;
    const rawPrice = screenToPrice(e.clientY, fl);
    const xData    = screenToXIndex(e.clientX, fl);

    // 次スロットに入ったら前の足を確定して自動進行（始値 = 前の足の終値）
    while (xData >= predDraw.slot + 0.5) {
      predCandles.push({ ...predDraw });
      predDraw = { slot: predDraw.slot + 1, open: predDraw.close, close: predDraw.close,
                   high: predDraw.close, low: predDraw.close };
    }

    // 終値・高値・安値を更新（基準値段のストップ高・安でクランプ）
    const price = clampToPriceLimit(rawPrice, predDraw.slot);
    predDraw = {
      ...predDraw,
      close: price,
      high:  Math.max(predDraw.high, price),
      low:   Math.min(predDraw.low,  price),
    };

    refreshPredShapes();
    return;
  }

  // カーソル切替: 次の予測スロット（描画可能位置）のみクロスヘア
  if (!dragState && !lineDrawMode && currentDates.length) {
    const fl = chartDiv._fullLayout;
    if (fl?.xaxis?.range) {
      const xData    = screenToXIndex(e.clientX, fl);
      const nextSlot = currentDates.length + predCandles.length;
      chartDiv.classList.toggle(
        'pred-cursor',
        xData >= nextSlot - 0.5 && xData < nextSlot + 0.5
      );
    }
  }

  if (!dragState) return;
  const span  = dragState.range0[1] - dragState.range0[0];
  const shift = -(e.clientX - dragState.startX) / dragState.plotWidth * span;
  const [x0, x1] = clampRange(dragState.range0[0] + shift, dragState.range0[1] + shift);
  relayoutXY(x0, x1);
});

document.addEventListener('mouseup', () => {
  // 直線確定
  if (lineDraw) {
    const dx = Math.abs(lineDraw.x1 - lineDraw.x0);
    const dy = Math.abs(lineDraw.y1 - lineDraw.y0);
    if (dx > 0.2 || dy > 1) {
      drawnLines.push({ ...lineDraw });
    }
    lineDraw = null;
    if (_predRafId !== null) { cancelAnimationFrame(_predRafId); _predRafId = null; }
    _applyPredShapes();
    return;
  }

  if (predDraw) {
    predCandles.push({ ...predDraw });
    predDraw = null;
    // 未処理の RAF をキャンセルして即座に確定描画
    if (_predRafId !== null) { cancelAnimationFrame(_predRafId); _predRafId = null; }
    chartDiv.style.cursor = '';
    _applyPredShapes();
  }
  dragState = null;
});

// ---- 予測ローソク足ホバー表示 ----
function showPredHoverInfo(predIdx) {
  const allPreds = [...predCandles];  // predDraw は描画中なので対象外
  if (predIdx < 0 || predIdx >= allPreds.length) { clearHoverInfo(); return; }

  const p   = allPreds[predIdx];
  const o   = p.open;
  const c   = p.close;
  const h   = Math.max(o, c);
  const l   = Math.min(o, c);
  const cls = c >= o ? 'up' : 'dn';

  const inds = calcPredIndicators();
  const ind  = inds[predIdx];

  const ma5  = ind?.ma5     ?? null;
  const ma25 = ind?.ma25    ?? null;
  const ma75 = ind?.ma75    ?? null;
  const bbU  = ind?.bbUpper ?? null;
  const bbL  = ind?.bbLower ?? null;

  hoverInfo.innerHTML =
    `<span class="hi-group"><span class="hi-lbl">予測 +${predIdx + 1}</span></span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">O</span><span class="hi-val">${fmtP(o)}</span>` +
      `<span class="hi-lbl">H</span><span class="hi-val">${fmtP(h)}</span>` +
      `<span class="hi-lbl">L</span><span class="hi-val">${fmtP(l)}</span>` +
      `<span class="hi-lbl">C</span><span class="hi-val ${cls}">${fmtP(c)}</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">MA5</span><span class="hi-val">${fmtP(ma5)}</span>` +
      `<span class="hi-lbl">MA25</span><span class="hi-val">${fmtP(ma25)}</span>` +
      `<span class="hi-lbl">MA75</span><span class="hi-val">${fmtP(ma75)}</span>` +
    `</span>` +
    `<span class="hi-sep"></span>` +
    `<span class="hi-group">` +
      `<span class="hi-lbl">BB</span><span class="hi-val">${fmtP(bbL)} – ${fmtP(bbU)}</span>` +
    `</span>`;
  hoverInfo.style.display = 'flex';
}

// ---- チャートホバー: 枠外にOHLC等を表示 ----
chartDiv.addEventListener('mousemove', e => {
  if (dragState || predDraw || lineDraw) return;
  const fl = chartDiv._fullLayout;
  if (!fl?.xaxis?.range || !currentDates.length) return;
  const xData = screenToXIndex(e.clientX, fl);
  const idx   = Math.round(xData);
  const n     = currentDates.length;
  if (idx >= n) {
    showPredHoverInfo(idx - n);
  } else {
    showHoverInfo(idx);
  }
});

chartDiv.addEventListener('mouseleave', () => {
  clearHoverInfo();
  if (!predDraw) chartDiv.classList.remove('pred-cursor');
});

// ---- ホイールで Y 軸を比率ごと上下スケール ----
// 上スクロール: yScale を増加 → 両端が比例して上昇（下限 ≈ 上限/3 を維持）
// 下スクロール: yScale を減少 → 両端が比例して下降（上限 ≈ 下限×3 を維持）
chartDiv.addEventListener('wheel', e => {
  e.preventDefault();
  const fl = chartDiv._fullLayout;
  if (!fl?.xaxis?.range || !currentDates.length) return;
  const xr   = fl.xaxis.range;
  const base = calcYRange(xr[0], xr[1]);
  if (!base) return;
  yScale *= (e.deltaY < 0 ? 1.08 : 1 / 1.08);
  yScale  = Math.max(0.05, Math.min(20, yScale));  // 極端な値を制限
  Plotly.relayout(chartDiv, {
    'yaxis.range':  [base[0] * yScale, base[1] * yScale],
    'yaxis5.range': [base[0] * yScale, base[1] * yScale],
  });
}, { passive: false });

// ---- チャート描画 ----
function renderChart(data) {
  const dates  = data.ohlcv.map(d => d.date);
  const opens  = data.ohlcv.map(d => d.open);
  const highs  = data.ohlcv.map(d => d.high);
  const lows   = data.ohlcv.map(d => d.low);
  const closes = data.ohlcv.map(d => d.close);
  const vols   = data.ohlcv.map(d => d.volume);
  const ind    = data.indicators;
  const n      = dates.length;

  currentDates    = dates;
  currentOpens    = opens;
  currentHighs    = highs;
  currentLows     = lows;
  currentCloses   = closes;
  currentVols     = vols;
  currentInd      = ind;
  currentTicker   = data.ticker;
  currentInterval = data.interval;
  predCandles   = [];
  predDraw      = null;
  drawnLines    = [];
  lineDraw      = null;
  yScale        = 1;

  const initX0     = Math.max(-0.5, n - candleCount - 0.5);
  const initYRange = calcYRange(initX0, n - 0.5);

  // 最新ローソク足の日付を表示（例: 6/30）
  if (n > 0) {
    const [, mm, dd] = dates[n - 1].split('-');
    latestDate.textContent = `最新: ${parseInt(mm)}/${parseInt(dd)}`;
  }

  const GREEN = '#1A9960';
  const RED   = '#D63B3B';
  const BLUE  = '#3B6EE8';

  const traces = [];

  // ローソク足
  traces.push({
    type: 'candlestick', xaxis: 'x', yaxis: 'y',
    x: dates, open: opens, high: highs, low: lows, close: closes,
    name: 'OHLC',
    increasing: { line: { color: GREEN }, fillcolor: GREEN },
    decreasing: { line: { color: RED },   fillcolor: RED },
    showlegend: false,
  });

  // MA
  const maColors = { '5': '#E0AF68', '25': '#BB9AF7', '75': '#7AA2F7' };
  for (const [p, color] of Object.entries(maColors)) {
    traces.push({
      type: 'scatter', mode: 'lines', xaxis: 'x', yaxis: 'y',
      x: dates, y: ind.ma[p], name: `MA${p}`,
      line: { color, width: 1.2 },
    });
  }

  // ボリンジャーバンド
  traces.push({
    type: 'scatter', mode: 'lines', xaxis: 'x', yaxis: 'y',
    x: dates, y: ind.bollinger.upper, name: 'BB upper',
    line: { color: BLUE, width: 1, dash: 'dot' }, showlegend: false,
  });
  traces.push({
    type: 'scatter', mode: 'lines', xaxis: 'x', yaxis: 'y',
    x: dates, y: ind.bollinger.lower, name: 'BB lower',
    fill: 'tonexty', fillcolor: 'rgba(59,110,232,0.07)',
    line: { color: BLUE, width: 1, dash: 'dot' }, showlegend: false,
  });

  // 出来高
  const volColors = closes.map((c, i) => (i === 0 || c >= opens[i]) ? GREEN : RED);
  traces.push({
    type: 'bar', xaxis: 'x', yaxis: 'y2',
    x: dates, y: vols, name: '出来高',
    marker: { color: volColors, opacity: 0.7 },
    showlegend: false,
  });

  // MACD
  const histColors = (ind.macd.hist || []).map(v => (v >= 0 ? GREEN : RED));
  traces.push({
    type: 'bar', xaxis: 'x', yaxis: 'y3',
    x: dates, y: ind.macd.hist, name: 'MACD hist',
    marker: { color: histColors, opacity: 0.7 }, showlegend: false,
  });
  traces.push({
    type: 'scatter', mode: 'lines', xaxis: 'x', yaxis: 'y3',
    x: dates, y: ind.macd.macd, name: 'MACD',
    line: { color: BLUE, width: 1.5 },
  });
  traces.push({
    type: 'scatter', mode: 'lines', xaxis: 'x', yaxis: 'y3',
    x: dates, y: ind.macd.signal, name: 'Signal',
    line: { color: '#E0AF68', width: 1.2 },
  });

  // 右軸（yaxis5）用ダミートレース: 軸ラベルを表示させるために必要
  traces.push({
    type: 'scatter', mode: 'markers', xaxis: 'x', yaxis: 'y5',
    x: [dates[0]], y: [null],
    showlegend: false, hoverinfo: 'skip', marker: { opacity: 0 },
  });

  // ---- 25日前・75日前の背景ゾーン ----
  const bgShapes = [];
  if (n > 25) {
    bgShapes.push({
      type: 'rect', xref: 'x', yref: 'paper',
      x0: dates[n - 26], x1: dates[n - 1],
      y0: 0, y1: 1,
      fillcolor: 'rgba(59,110,232,0.05)',
      line: { width: 0 },
      layer: 'below',
    });
  }
  if (n > 75) {
    bgShapes.push({
      type: 'rect', xref: 'x', yref: 'paper',
      x0: dates[n - 76], x1: dates[n - 26],
      y0: 0, y1: 1,
      fillcolor: 'rgba(255,165,0,0.06)',
      line: { width: 0 },
      layer: 'below',
    });
  }

  // 年境界の縦線とラベル
  const yearShapes      = [];
  const yearAnnotations = [];
  let prevYr = '';
  dates.forEach(d => {
    const yr = d.slice(0, 4);
    if (yr !== prevYr) {
      if (prevYr !== '') {
        yearShapes.push({
          type: 'line', xref: 'x', yref: 'paper',
          x0: d, x1: d, y0: 0, y1: 1,
          line: { color: '#B8C2D8', width: 1 },
          layer: 'below',
        });
      }
      yearAnnotations.push({
        xref: 'x', yref: 'paper',
        x: d, y: -0.03,
        text: yr,
        showarrow: false,
        xanchor: 'left', yanchor: 'top',
        font: { size: 10, color: '#7A86A8' },
        cliponaxis: false,
      });
      prevYr = yr;
    }
  });

  currentBaseShapes = [...bgShapes, ...yearShapes];

  // タイトル: 証券コード + 会社名
  const code = displayCode(data.ticker);
  const name = data.name && data.name !== data.ticker ? data.name : '';
  const titleText = name
    ? `${code}  <span style="font-size:13px;color:#3A445E">${name}</span>  <span style="font-size:11px;color:#7A86A8">${data.interval}</span>`
    : `${code}  <span style="font-size:11px;color:#7A86A8">${data.interval}</span>`;

  const layout = {
    paper_bgcolor: '#FFFFFF',
    plot_bgcolor:  '#FAFBFD',
    font: { color: '#3A445E', size: 11 },
    margin: { l: 60, r: 60, t: 44, b: 44 },
    title: {
      text: titleText,
      font: { size: 15, color: '#1B2240' },
      x: 0.01, xanchor: 'left',
    },
    xaxis: {
      type: 'category',
      range: [initX0, n - 0.5],
      rangeslider: { visible: false },
      showgrid: false,
      domain: [0, 1],
      showticklabels: false,
    },
    dragmode: false,
    yaxis:  { domain: [0.26, 1],    showgrid: true,  gridcolor: '#EEF1F7', automargin: true, fixedrange: true,
              ...(initYRange ? { range: initYRange } : { autorange: true }) },
    yaxis2: { domain: [0.14, 0.25], showgrid: false, automargin: true, fixedrange: true },
    yaxis3: { domain: [0, 0.13],    showgrid: true,  gridcolor: '#EEF1F7', automargin: true, zeroline: true, zerolinecolor: '#D4DAE8', fixedrange: true },
    yaxis5: { overlaying: 'y', side: 'right', showgrid: false, zeroline: false, fixedrange: true,
              ...(initYRange ? { range: initYRange } : { autorange: true }) },
    shapes: currentBaseShapes,
    // サブチャートの指標名ラベル + 年ラベル
    annotations: [
      { xref: 'paper', yref: 'y2 domain', x: 0.005, y: 1,
        text: '出来高', showarrow: false, xanchor: 'left', yanchor: 'top',
        font: { size: 10, color: '#7A86A8' } },
      { xref: 'paper', yref: 'y3 domain', x: 0.005, y: 1,
        text: 'MACD (12,26,9)', showarrow: false, xanchor: 'left', yanchor: 'top',
        font: { size: 10, color: '#7A86A8' } },
      ...yearAnnotations,
    ],
    showlegend: false,
    hovermode:  false,
  };

  ['xaxis2', 'xaxis3'].forEach(ax => {
    layout[ax] = { matches: 'x', showticklabels: false };
  });

  Plotly.react(chartDiv, traces, layout, { responsive: true, displayModeBar: false });
  clearHoverInfo();  // チャート読み込み後にバーを常時表示
}
