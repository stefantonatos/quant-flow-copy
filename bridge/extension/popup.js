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

  /* Read each number from its OWN element.
   *
   * This is the thing three earlier attempts got wrong. TradingView renders
   * every value in a separate element, and textContent on the row glues them
   * together with no separator: entry 1.96706, stop 1.96603 and target
   * 1.96812 arrive as the single string "1.967061.966031.96812". Any regex
   * over that text produces confident garbage -- 966031.9681214 was a real
   * reading. Walking to the leaf elements keeps the values apart, because
   * the DOM already separates them. */
  const leafNumbers = (root) => {
    const found = [];
    const walk = (el) => {
      if (!el) return;
      if (el.children.length === 0) {
        const t = (el.textContent || '').trim();
        if (/^-?\d+(?:[.,]\d+)?$/.test(t)) found.push(parseFloat(t.replace(',', '.')));
        return;
      }
      for (const c of el.children) walk(c);
    };
    walk(root);
    return found;
  };

  // Every element naming the indicator, plus their ancestors.
  const hits = [];
  for (const el of document.querySelectorAll('div, span')) {
    const t = el.textContent || '';
    if (t && t.length <= 1200 && /bridge/i.test(t)) hits.push(el);
  }
  out.candidates = hits.length;

  /* Take the candidate with the MOST separate numeric leaves.
   *
   * Climbing to the FIRST ancestor with "enough" numbers was the last bug:
   * it landed on the element holding only the indicator's INPUTS and read
   * 14 / 0.5 / 1 -- the ATR length, the stop buffer and the RR setting --
   * as if they were prices. The full legend row carries the inputs AND the
   * plotted values, so it has strictly more leaves than either part alone,
   * while the whole legend (all indicators) is excluded by the length cap.
   * Maximising leaf count therefore lands on exactly the row wanted. */
  let nums = null;
  let sideText = '';
  for (const hit of hits) {
    let el = hit;
    for (let up = 0; up < 5 && el; up++, el = el.parentElement) {
      const t = el.textContent || '';
      if (t.length > 1200 || !/bridge/i.test(t)) break;
      const found = leafNumbers(el);
      if (found.length >= 5 && (nums === null || found.length > nums.length)) {
        nums = found;
        sideText = t;
      }
    }
  }

  if (!nums) {
    out.raw = hits.length ? (hits[0].textContent || '').slice(0, 300) : null;
    return out;
  }

  /* The plotted tail is Entry, Stop, Target, Long, RR. */
  const entry = nums[nums.length - 5];
  const sl    = nums[nums.length - 4];
  const tp    = nums[nums.length - 3];
  out.raw = nums.join(' , ');

  /* SANITY CHECK. Three prices for one instrument sit close together; the
   * indicator's settings do not. Both failures seen in practice -- reading
   * 966031.9681214 from concatenated text, and reading 14/0.5/1 from the
   * settings -- are caught by this, and neither was obvious on screen. A
   * scraper reading somebody else's page can always start returning
   * plausible-looking rubbish, so refusing is safer than displaying. */
  const vals = [entry, sl, tp].filter((v) => typeof v === 'number' && isFinite(v) && v > 0);
  if (vals.length < 3) { out.implausible = true; return out; }
  if (Math.max(...vals) / Math.min(...vals) > 1.5) {
    out.implausible = true;
    return out;
  }

  out.entry = entry;
  out.sl = sl;
  out.tp = tp;

  if (/\blong\b/i.test(sideText)) out.side = 'buy';
  else if (/\bshort\b/i.test(sideText)) out.side = 'sell';
  else out.side = sl < entry ? 'buy' : 'sell';

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
    } else if (res.implausible) {
      $('status').textContent =
        'Read numbers that do not look like prices — ignoring them. Type the ' +
        'levels in manually.';
      $('status').style.color = '#c00';
      console.log('[QuantFlow Bridge] rejected as implausible:', res.raw);
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


/* ---------------------------------------------------------------------------
 * Diagnostics.
 *
 * Four attempts at reading the legend failed because they were written
 * against guessed markup. This dumps the real structure so a selector can be
 * written from evidence instead: for every element naming the indicator, its
 * tag, classes, data-* attributes and immediate children with their own text.
 * Copy the result, and the reader can be pinned to a stable anchor rather
 * than to text position, which is what has been breaking.
 * ------------------------------------------------------------------------ */
function dumpBridgeDom() {
  const lines = [];
  const attrs = (el) => {
    const out = [];
    for (const a of el.attributes || []) {
      if (a.name === 'class' || a.name.startsWith('data-') || a.name === 'title')
        out.push(`${a.name}="${String(a.value).slice(0, 90)}"`);
    }
    return out.join(' ');
  };
  const describe = (el, depth) => {
    const pad = '  '.repeat(depth);
    const own = el.children.length === 0 ? ` TEXT="${(el.textContent || '').trim().slice(0, 40)}"` : '';
    return `${pad}<${el.tagName.toLowerCase()} ${attrs(el)}>${own}`;
  };

  const seen = new Set();
  let n = 0;
  for (const el of document.querySelectorAll('div, span')) {
    const t = el.textContent || '';
    if (!/bridge/i.test(t) || t.length > 400) continue;
    if (n++ > 6) break;

    // Climb to a row-level ancestor, then print that subtree.
    let root = el;
    for (let i = 0; i < 3 && root.parentElement; i++) {
      const pt = root.parentElement.textContent || '';
      if (pt.length > 900) break;
      root = root.parentElement;
    }
    if (seen.has(root)) continue;
    seen.add(root);

    lines.push(`=== CANDIDATE ${n} (root text len ${(root.textContent || '').length}) ===`);
    lines.push(describe(root, 0));
    const walk = (parent, depth) => {
      if (depth > 3) return;
      for (const c of parent.children) {
        lines.push(describe(c, depth));
        walk(c, depth + 1);
      }
    };
    walk(root, 1);
    lines.push(`FULL TEXT: ${(root.textContent || '').slice(0, 300)}`);
    lines.push('');
  }
  return lines.join('\n') || 'no elements matched "bridge"';
}

$('diag').onclick = async () => {
  $('status').textContent = 'Collecting...';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, func: dumpBridgeDom
    });
    const text = (results && results[0] && results[0].result) || 'nothing returned';
    await navigator.clipboard.writeText(text);
    $('status').textContent = 'Copied to clipboard — paste it to Claude.';
    $('status').style.color = '#0a0';
    console.log(text);
  } catch (e) {
    $('status').textContent = 'Diagnostics failed: ' + (e && e.message ? e.message : e);
    $('status').style.color = '#c00';
  }
};
