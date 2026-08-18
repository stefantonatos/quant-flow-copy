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
