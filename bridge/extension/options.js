const DEFAULTS = { endpoint: 'http://127.0.0.1:8787/webhook', secret: '', notify: true };
const $ = (id) => document.getElementById(id);

chrome.storage.local.get(DEFAULTS, (cfg) => {
  $('endpoint').value = cfg.endpoint;
  $('secret').value = cfg.secret;
  $('notify').checked = cfg.notify;
});

$('save').onclick = () => {
  chrome.storage.local.set({
    endpoint: $('endpoint').value.trim(),
    secret: $('secret').value.trim(),
    notify: $('notify').checked
  }, () => { $('status').textContent = 'Saved.'; });
};

/* A test button, because the failure mode this needs to rule out is the
 * silent one: everything looks configured, and alerts quietly go nowhere. */
$('test').onclick = async () => {
  $('status').textContent = 'Sending...';
  try {
    const res = await fetch($('endpoint').value.trim(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        secret: $('secret').value.trim(),
        id: 'ext-test-' + Date.now(),
        action: 'open', symbol: 'EURNZD', side: 'buy', type: 'limit',
        price: 1.96, sl: 1.95, tp: 1.97, lots: 0.01, comment: 'exttest'
      })
    });
    $('status').textContent = `${res.status}: ${(await res.text()).trim()}`;
  } catch (e) {
    $('status').textContent = 'Failed — is webhook_server.py running?';
  }
};
