// Minimal service worker. Its ONLY job: keep each tab's chat transcript, so the
// panel can redraw it after a reload.  [notes: bg.js]
//
// Content scripts never touch chrome.storage.session themselves. They ask here,
// and the key is built from `sender` -- the tab id and origin the BROWSER
// attributes to the message -- never from anything the message says. That is
// the same rule malabr_api.cc applies to the KV session key, and the key has
// the same shape: (tab, origin), within this one extension.

const MAX_TURNS = 40;   // [notes: content.js restore]

function keyFor(sender) {
  const tab = sender.tab && sender.tab.id;
  if (typeof tab !== "number" || tab < 0) return null;
  if (!sender.origin || sender.origin === "null") return null;  // opaque
  return `malabr:${tab}:${sender.origin}`;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.malabr !== "string") return false;
  const key = keyFor(sender);
  if (!key) { sendResponse(null); return false; }

  if (msg.malabr === "save") {
    const turns = Array.isArray(msg.transcript) ? msg.transcript.slice(-MAX_TURNS) : [];
    chrome.storage.session.set({ [key]: turns })
      .then(() => sendResponse(true), () => sendResponse(false));
    return true;                          // async sendResponse
  }
  if (msg.malabr === "restore") {
    chrome.storage.session.get(key)
      .then((o) => sendResponse(o[key] || null), () => sendResponse(null));
    return true;
  }
  sendResponse(null);
  return false;
});

// A closed tab's transcript goes with it, as its KV session does on the server
// (TAB_CLOSED). The tab may have held several origins, hence the prefix scan.
chrome.tabs.onRemoved.addListener((tabId) => {
  const prefix = `malabr:${tabId}:`;
  chrome.storage.session.get(null).then((all) => {
    const dead = Object.keys(all).filter((k) => k.startsWith(prefix));
    if (dead.length) return chrome.storage.session.remove(dead);
  }).catch((e) => console.warn("MALABR: transcript cleanup failed", e));
});
