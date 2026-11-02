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

// Every key this tab holds, one per origin it has chatted on.
function keysForTab(tabId) {
  const prefix = `malabr:${tabId}:`;
  return chrome.storage.session.get(null)
    .then((all) => Object.keys(all).filter((k) => k.startsWith(prefix)));
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.malabr !== "string") return false;
  const key = keyFor(sender);
  if (!key) { sendResponse(null); return false; }

  if (msg.malabr === "save") {
    const turns = Array.isArray(msg.transcript) ? msg.transcript.slice(-MAX_TURNS) : [];
    // The server's id for this conversation  [notes: content.js restore]
    const id = typeof msg.id === "string" && msg.id.length <= 64 ? msg.id : null;
    // Rule 1, mirrored: a chat turn from this origin is the moment the server
    // tears down this tab's sessions for other origins, so their transcripts
    // go too. Only on a chat turn -- a save that sends nothing (new chat,
    // dropped stale copy) evicts nothing on the server either.  [notes: bg.js]
    const evicted = msg.evictOtherOrigins
      ? keysForTab(sender.tab.id).then((ks) => {
          const other = ks.filter((k) => k !== key);
          if (other.length) return chrome.storage.session.remove(other);
        })
      : Promise.resolve();
    evicted.then(() => chrome.storage.session.set({ [key]: { id, turns } }))
      .then(() => sendResponse(true), () => sendResponse(false));
    return true;                          // async sendResponse
  }
  if (msg.malabr === "restore") {
    chrome.storage.session.get(key)
      .then((o) => sendResponse(o[key] || null), () => sendResponse(null));   // { id, turns }
    return true;
  }
  sendResponse(null);
  return false;
});

// A closed tab's transcript goes with it, as its KV session does on the server
// (TAB_CLOSED). The tab may have held several origins, hence the prefix scan.
chrome.tabs.onRemoved.addListener((tabId) => {
  keysForTab(tabId).then((dead) => {
    if (dead.length) return chrome.storage.session.remove(dead);
  }).catch((e) => console.warn("MALABR: transcript cleanup failed", e));
});
