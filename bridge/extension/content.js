/*
 * content.js - watches the TradingView page for alerts firing and hands the
 * message text to the background worker, which forwards it to the local
 * bridge server.
 *
 * WHY THIS EXISTS AT ALL
 * TradingView's webhook feature is a PAID-PLAN feature. On a free account the
 * checkbox is not there. Reading the alert off the page works on any plan,
 * which is exactly why the commercial bridges ship a browser extension.
 *
 * WHAT IT COSTS YOU, AND THIS IS NOT A SMALL LIST:
 *   - The browser tab has to stay open, on the chart, awake. Close the laptop
 *     and the alert fires into nothing. A webhook is server-side and does not
 *     care whether you are at your desk.
 *   - It reads TradingView's HTML. TradingView redesigns their UI whenever
 *     they like, and when they do this stops working -- probably silently.
 *     That is the honest trade for not paying for a plan.
 *
 * If you have a paid plan, use the webhook instead. It is strictly better and
 * the server already accepts it.
 */

const SELECTORS = [
  // TradingView has moved its notification markup around over the years.
  // Several candidates are tried rather than one, so a redesign has to break
  // all of them at once before the extension goes deaf.
  '[data-name="alerts-log-item"]',
  '[class*="alertLogItem"]',
  '[class*="toast"] [class*="message"]',
  '[data-dialog-name="alert-fired"]',
  '[class*="notificationText"]'
];

const seen = new Set();

/* Our Pine scripts emit their alert message as JSON. Anything that is not a
 * JSON object is some other alert the user set up by hand, and forwarding it
 * would be guessing at intent -- so it is ignored rather than acted on. */
function extractCommand(text) {
  if (!text) return null;
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return null;
  try {
    const obj = JSON.parse(text.slice(start, end + 1));
    return (obj && typeof obj === 'object' && obj.symbol) ? obj : null;
  } catch (e) {
    return null;
  }
}

function handleText(text) {
  const cmd = extractCommand(text);
  if (!cmd) return;

  /* De-duplicate in the page too, not only on the server. A MutationObserver
   * fires for re-renders as well as new content, so the same alert element can
   * be observed several times within a second. */
  const key = JSON.stringify(cmd);
  if (seen.has(key)) return;
  seen.add(key);
  setTimeout(() => seen.delete(key), 60000);

  chrome.runtime.sendMessage({ type: 'ALERT', payload: cmd });
}

function scan(root) {
  for (const sel of SELECTORS) {
    let nodes;
    try {
      nodes = root.querySelectorAll ? root.querySelectorAll(sel) : [];
    } catch (e) {
      continue;
    }
    for (const n of nodes) handleText(n.textContent);
  }
}

const observer = new MutationObserver((mutations) => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (node.nodeType !== 1) continue;
      handleText(node.textContent);
      scan(node);
    }
  }
});

observer.observe(document.body, { childList: true, subtree: true });
console.log('[QuantFlow Bridge] watching TradingView for alerts');


/* ---------------------------------------------------------------------------
 * READ_LEVELS - pull entry/sl/tp off the chart for the popup.
 *
 * TradingView prints an indicator's plot values in its status line, so a Pine
 * script that plots values titled "Entry" / "Stop" / "Target" puts those
 * numbers on the page as text. pine/bridge_levels.pine does exactly that.
 *
 * This is scraping. It will break when TradingView redesigns their UI, and it
 * may return nothing on a layout it does not recognise. That is why the popup
 * has manual fields and treats this as a convenience: getting a number wrong
 * here costs real money, so whatever it reads is presented for you to CHECK,
 * never sent automatically.
 * ------------------------------------------------------------------------ */

function parseNum(text) {
  if (!text) return null;
  const m = String(text).replace(/[\u2212\u2013]/g, '-').match(/-?\d+(?:[.,]\d+)?/);
  if (!m) return null;
  const v = parseFloat(m[0].replace(',', '.'));
  return isFinite(v) ? v : null;
}

function readLevels() {
  const out = { symbol: null, entry: null, sl: null, tp: null, side: null };

  const symEl = document.querySelector('[class*="symbolNameText"], [data-name="legend-source-title"]');
  if (symEl) out.symbol = symEl.textContent.trim().toUpperCase();
  if (!out.symbol) {
    const m = document.title.match(/^([A-Z0-9._!]+)/);
    if (m) out.symbol = m[1];
  }

  /* Walk every legend/status-line row and match on the plot's TITLE. Matching
   * by title rather than by position means an extra plot in the indicator
   * cannot silently shift which number lands in which field. */
  const rows = document.querySelectorAll(
    '[class*="valuesWrapper"], [class*="valuesAdditionalWrapper"], [data-name="legend-source-item"]'
  );
  for (const row of rows) {
    const text = row.textContent || '';
    for (const [key, re] of [
      ['entry', /entry/i],
      ['sl', /\b(stop|sl)\b/i],
      ['tp', /\b(target|tp|take\s*profit)\b/i]
    ]) {
      if (out[key] == null && re.test(text)) {
        const after = text.split(re)[1] || text;
        const v = parseNum(after);
        if (v != null) out[key] = v;
      }
    }
    if (out.side == null) {
      if (/\blong\b/i.test(text)) out.side = 'buy';
      else if (/\bshort\b/i.test(text)) out.side = 'sell';
    }
  }

  if (out.side == null && out.entry != null && out.sl != null)
    out.side = out.sl < out.entry ? 'buy' : 'sell';   // stop below entry = a long

  return out;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'READ_LEVELS') {
    try { sendResponse(readLevels()); }
    catch (e) { sendResponse(null); }
  }
  return true;
});
