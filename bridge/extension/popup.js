/*
 * popup.js - compose a trade and send it to the local bridge as a PENDING
 * LIMIT ORDER.
 *
 * WHY A LIMIT ORDER RATHER THAN WATCHING PRICE IN THE BROWSER
 * The paid bridges watch the chart and fire a market order the moment price
 * touches your entry. That needs the tab open, the machine awake, and it adds
 * browser latency to a fill. A limit order asks the BROKER to do the same job:
 * it sits on their server, fills at your price or better, and does not care
 * whether your laptop is shut. It is the same outcome with fewer moving parts
 * and no dependency on this extension still running at the moment it matters.
 */

const $ = (id) => document.getElementById(id);

function num(el) {
  const v = parseFloat(String(el.value).replace(/[^0-9.\-]/g, ''));
  return isFinite(v) ? v : null;
}

function refreshRR() {
  const e = num($('entry')), s = num($('sl')), t = num($('tp'));
  const box = $('rr');
  if (e === null || s === null || t === null) { box.textContent = 'Risk:reward —'; return; }
  const risk = Math.abs(e - s), reward = Math.abs(t - e);
  if (risk <= 0) { box.textContent = 'Risk:reward — (stop equals entry)'; return; }

  const side = $('side').value;
  // Catch the wrong-side stop here as well as on the server. It is the
  // single most expensive typo available in this form, and seeing it before
  // you press the button is better than reading it in a rejection log.
  const bad = (side === 'buy' && s >= e) || (side === 'sell' && s <= e);
  box.textContent = `Risk:reward ${(reward / risk).toFixed(2)}R` +
    (bad ? '   ⚠ stop is on the wrong side of entry' : '');
  box.style.background = bad ? '#ffe9e9' : '#f2f5f8';
}

['entry', 'sl', 'tp'].forEach((id) => $(id).addEventListener('input', refreshRR));
$('side').addEventListener('change', refreshRR);

/* Best-effort auto-fill from the chart. TradingView shows an indicator's plot
 * values in its status line, so a script that plots entry/sl/tp exposes them
 * as readable text. This is scraping and it will break when TradingView
 * redesigns -- hence the manual fields, which always work. */
/* Read the levels by INJECTING the reader into the page on demand, rather
 * than messaging a content script that may not be loaded.
 *
 * The previous version used chrome.tabs.sendMessage, which fails with
 * "Could not establish connection" whenever the content script is not
 * already running in that tab -- which happens after every extension
 * reload, on tabs opened before the extension, and on some navigations.
 * That error is what "Could not read the page" actually was. Injecting on
 * click cannot hit that class of failure at all: the code goes in at the
 * moment it is needed.
 */
function scrapeLevels() {
  // Runs INSIDE the page. Must be entirely self-contained -- it cannot see
  // anything from popup.js's scope.
  const out = { symbol: null, entry: null, sl: null, tp: null, side: null,
                raw: null, candidates: 0 };

  /* Symbol from the URL, not the DOM. The chart URL carries
   * ?symbol=OANDA%3AEURNZD, which is unambiguous; scraping the page found
   * the first watchlist entry instead and confidently returned the wrong
   * instrument -- the kind of error that places a real order on the wrong
   * market. */
  try {
    const q = new URL(location.href).searchParams.get('symbol');
    if (q) out.symbol = decodeURIComponent(q).split(':').pop().toUpperCase();
  } catch (e) { /* fall through */ }
  if (!out.symbol) {
    const m = document.title.match(/^([A-Z0-9._!]+)/);
    if (m) out.symbol = m[1];
  }

  const numsIn = (t) => {
    const cleaned = String(t).replace(/[\u2212\u2013]/g, '-');
    return (cleaned.match(/-?\d+(?:[.,]\d+)?/g) || [])
      .map((x) => parseFloat(x.replace(',', '.')))
      .filter((v) => isFinite(v));
  };

  /* Gather every element mentioning the indicator, plus each one's parent --
   * TradingView splits the title and the values into separate siblings, so
   * the text we want may only exist on an ancestor. */
  const texts = [];
  for (const el of document.querySelectorAll('*')) {
    const t = el.textContent || '';
    if (!t || t.length > 1200) continue;
    if (!/bridge/i.test(t)) continue;
    texts.push(t);
    if (el.parentElement) {
      const pt = el.parentElement.textContent || '';
      if (pt && pt.length <= 1200) texts.push(pt);
    }
  }
  out.candidates = texts.length;
  if (!texts.length) return out;

  /* PRIMARY: match the indicator's own signature directly.
   *
   * pine/bridge_levels.pine renders as "Bridge <mode> <side> <entry> <stop>
   * <target> ..." -- the three numbers straight after the direction word are
   * the levels. Anchoring on that shape is far steadier than counting from
   * the end of a row, because it does not care what TradingView appends
   * afterwards or how the row is split across elements. */
  const sig = /bridge\s+\w+\s+(long|short)\s+(-?\d+(?:[.,]\d+)?)\s+(-?\d+(?:[.,]\d+)?)\s+(-?\d+(?:[.,]\d+)?)/i;
  let matched = null;
  for (const t of texts) {
    const m = t.match(sig);
    if (m) { matched = m; out.raw = t; break; }
  }
  if (matched) {
    const f = (x) => parseFloat(String(x).replace(',', '.'));
    out.side  = matched[1].toLowerCase() === 'long' ? 'buy' : 'sell';
    out.entry = f(matched[2]);
    out.sl    = f(matched[3]);
    out.tp    = f(matched[4]);
    return out;
  }

  /* FALLBACK: no signature match, so work from the shortest candidate row
   * that carries enough numbers, taking the plotted tail. */
  let best = null;
  for (const t of texts) {
    if (numsIn(t).length < 3) continue;
    if (best === null || t.length < best.length) best = t;
  }
  if (best === null) return out;
  out.raw = best;
  const nums = numsIn(best);
  if (nums.length >= 5) {
    out.entry = nums[nums.length - 5];
    out.sl    = nums[nums.length - 4];
    out.tp    = nums[nums.length - 3];
  } else {
    out.entry = nums[nums.length - 3];
    out.sl    = nums[nums.length - 2];
    out.tp    = nums[nums.length - 1];
  }

  if (/\blong\b/i.test(best)) out.side = 'buy';
  else if (/\bshort\b/i.test(best)) out.side = 'sell';
  else if (out.entry != null && out.sl != null)
    out.side = out.sl < out.entry ? 'buy' : 'sell';

  return out;
}

$('fill').onclick = async () => {
  $('status').textContent = 'Reading chart...';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !/tradingview\.com/.test(tab.url || '')) {
      $('status').textContent = 'Open a TradingView chart tab first.';
      return;
    }
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: scrapeLevels
    });
    const res = results && results[0] && results[0].result;
    if (!res) { $('status').textContent = 'Nothing came back from the page.'; return; }

    if (res.symbol) $('symbol').value = res.symbol;
    for (const k of ['entry', 'sl', 'tp']) if (res[k] != null) $(k).value = res[k];
    if (res.side) $('side').value = res.side;
    refreshRR();

    const got = ['entry', 'sl', 'tp'].filter((k) => res[k] != null);
    if (got.length === 3) {
      $('status').textContent = 'Read entry, stop and target. Check them against the chart.';
      $('status').style.color = '#0a0';
    } else if (res.raw) {
      $('status').textContent = 'Found the indicator but could not read 3 levels from it.';
      $('status').style.color = '#c00';
      console.log('[QuantFlow Bridge] best legend row was:', res.raw);
    } else {
      $('status').textContent =
        `Could not read levels (checked ${res.candidates || 0} page elements). ` +
        `Open the console for what it saw.`;
      $('status').style.color = '#c00';
      console.log('[QuantFlow Bridge] candidates seen:', res.candidates,
                  'best text:', res.raw);
    }
  } catch (e) {
    $('status').textContent = 'Read failed: ' + (e && e.message ? e.message : e);
    $('status').style.color = '#c00';
  }
};

$('send').onclick = async () => {
  const entry = num($('entry')), sl = num($('sl')), tp = num($('tp'));
  const risk = num($('risk')), lots = num($('lots'));
  const symbol = $('symbol').value.trim().toUpperCase();
  const side = $('side').value;

  if (!symbol) { $('status').textContent = 'Symbol is required.'; return; }
  if (entry === null) { $('status').textContent = 'Entry is required.'; return; }
  if (risk === null && lots === null) {
    $('status').textContent = 'Set a risk amount (or a lot size).'; return;
  }
  if (risk !== null && sl === null) {
    $('status').textContent = 'Risk-based sizing needs a stop loss to size against.';
    return;
  }

  const cfg = await new Promise((r) =>
    chrome.storage.local.get({ endpoint: 'http://127.0.0.1:8787/webhook', secret: '' }, r));
  if (!cfg.secret) { $('status').textContent = 'Set the shared secret in Settings first.'; return; }

  $('status').textContent = 'Sending...';
  try {
    const res = await fetch(cfg.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        secret: cfg.secret,
        id: `popup-${symbol}-${side}-${entry}-${Date.now()}`,
        action: 'open', symbol, side, type: 'limit',
        price: entry, sl: sl ?? 0, tp: tp ?? 0,
        lots: lots ?? 0, risk: risk ?? 0,
        comment: 'manual'
      })
    });
    const text = (await res.text()).trim();
    $('status').textContent = res.ok ? `✓ ${text}` : `✗ rejected: ${text}`;
    $('status').style.color = res.ok ? '#0a0' : '#c00';
  } catch (e) {
    $('status').textContent = '✗ not sent — is webhook_server.py running?';
    $('status').style.color = '#c00';
  }
};

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const m = tab && tab.title && tab.title.match(/^([A-Z0-9._!]+)/);
  if (m) $('symbol').value = m[1];
})();


/* Risk is a standing preference, not a per-trade decision, so it persists. */
chrome.storage.local.get({ risk: '100' }, (cfg) => { $('risk').value = cfg.risk; });
$('risk').addEventListener('change', () => {
  chrome.storage.local.set({ risk: $('risk').value.trim() });
});
