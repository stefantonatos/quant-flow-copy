/*
 * background.js - forwards an alert to the local bridge server.
 *
 * The server (bridge/webhook_server.py) does the real validation: secret,
 * symbol allowlist, lot caps, stop-direction, duplicate suppression. Nothing
 * here is trusted, and nothing here should be relied on as a safety layer --
 * this file only carries the message across.
 */

const DEFAULTS = {
  endpoint: 'http://127.0.0.1:8787/webhook',
  secret: '',
  notify: true
};

async function settings() {
  return new Promise((r) => chrome.storage.local.get(DEFAULTS, r));
}

function toast(title, message) {
  try {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icon.png',
      title,
      message: String(message).slice(0, 300)
    });
  } catch (e) { /* notifications are a nicety, never a failure path */ }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== 'ALERT') return;

  (async () => {
    const cfg = await settings();
    if (!cfg.secret) {
      console.warn('[QuantFlow Bridge] no secret set; open the extension options');
      if (cfg.notify) toast('Bridge not configured', 'Set the shared secret in the extension options.');
      return;
    }

    const body = Object.assign({}, msg.payload, { secret: cfg.secret });
    try {
      const res = await fetch(cfg.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const text = await res.text();
      console.log('[QuantFlow Bridge]', res.status, text.trim());
      if (cfg.notify) {
        /* Surface rejections. A silently-dropped alert is the worst outcome
         * here: you would believe a trade was placed when it was not. */
        if (res.ok) toast('Bridge: sent', `${body.side || body.action} ${body.symbol}`);
        else toast('Bridge: REJECTED', `${res.status} ${text.trim()}`);
      }
    } catch (e) {
      console.error('[QuantFlow Bridge] send failed', e);
      if (cfg.notify) toast('Bridge: NOT SENT', 'Is webhook_server.py running?');
    }
  })();

  return true;
});
