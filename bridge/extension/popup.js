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
$('fill').onclick = async () => {
  $('status').textContent = 'Reading chart...';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !/tradingview\.com/.test(tab.url || '')) {
      $('status').textContent = 'Open a TradingView chart tab first.';
      return;
    }
    const res = await chrome.tabs.sendMessage(tab.id, { type: 'READ_LEVELS' });
    if (!res) { $('status').textContent = 'No response from the page — reload the chart.'; return; }
    if (res.symbol) $('symbol').value = res.symbol;
    for (const k of ['entry', 'sl', 'tp']) if (res[k] != null) $(k).value = res[k];
    if (res.side) $('side').value = res.side;
    refreshRR();
    const got = ['entry', 'sl', 'tp'].filter((k) => res[k] != null);
    $('status').textContent = got.length
      ? `Read ${got.join(', ')} from the chart. Check them before sending.`
      : 'Could not find levels — type them in manually.';
  } catch (e) {
    $('status').textContent = 'Could not read the page — type the levels in manually.';
  }
};

$('send').onclick = async () => {
  const entry = num($('entry')), sl = num($('sl')), tp = num($('tp')), lots = num($('lots'));
  const symbol = $('symbol').value.trim().toUpperCase();
  const side = $('side').value;

  if (!symbol) { $('status').textContent = 'Symbol is required.'; return; }
  if (entry === null || lots === null) { $('status').textContent = 'Entry and lots are required.'; return; }

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
        price: entry, sl: sl ?? 0, tp: tp ?? 0, lots,
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
