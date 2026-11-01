#!/usr/bin/env python3
"""Test that the chat panel's saved transcript is keyed like its KV session.

The server keys a conversation by (extension, tab, origin). The panel's display
copy, kept in chrome.storage.session for reload, must be keyed the same way, or
the panel shows a conversation the model never saw. bg.js builds that key from
the message `sender`, so the content script cannot choose it.

Loads the real unpacked extension into the patched Chromium with MalabrFeature
OFF (no model server is spawned, so this is cheap), and drives the real bg.js
from each tab's content-script world over CDP. Checks, in order of what was
actually wrong before (an origin-only key, opened to content scripts):
  1. a content script cannot read storage.session directly
  2. a second tab on the SAME origin does not get the first tab's transcript
  3. another origin does not get it
  4. a reload does get it back
  5. closing the tab deletes it, and leaves other tabs' transcripts alone
  6. rule 1, mirrored: passing through another origin deletes nothing; a
     chat turn on another origin deletes the tab's old transcript, and only
     that tab's (a save that is not a chat turn deletes nothing)
"""
import http.server, json, os, shutil, socketserver, subprocess, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(HERE, "..", "mserver", "malabr_chat_extension")
CHROME = os.environ.get("CHROME_BIN",
                        os.path.join(HERE, "..", "..", "out", "Default", "chrome"))
CDP, HTTP = 9341, 8791
PROFILE = "/tmp/malabr-transcript-profile"
DOCROOT = "/tmp/malabr-transcript-pages"
EXT_NAME = "MALABR Chat"            # manifest name = the isolated world's name

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def serve():
    os.makedirs(DOCROOT, exist_ok=True)
    with open(os.path.join(DOCROOT, "index.html"), "w") as f:
        f.write("<!doctype html><title>t</title><p>hello</p>")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a):
            pass
    # Threading, not plain TCPServer: Chrome holds idle preconnect sockets, and
    # a single-threaded server blocks on one of them so the next tab never loads.
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", HTTP), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def jget(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP}{path}", timeout=5) as r:
        return json.load(r)


class Tab:
    """One tab, plus the content script's isolated world inside it."""

    def __init__(self, browser, url):
        self.tid = browser.call("Target.createTarget", {"url": url})["targetId"]
        time.sleep(2.0)
        t = [x for x in jget("/json/list") if x["id"] == self.tid][0]
        self.ws = WS(t["webSocketDebuggerUrl"])
        self.ctx = self.find_ctx()

    def find_ctx(self):
        # Raw send/recv, not WS.call: call() discards events, and the
        # executionContextCreated events are exactly what is needed here.
        self.ws.send_text(json.dumps({"id": 1, "method": "Runtime.enable"}))
        end = time.time() + 20
        while time.time() < end:
            m = json.loads(self.ws.recv_text())
            c = m.get("params", {}).get("context") or {}
            if (m.get("method") == "Runtime.executionContextCreated"
                    and c.get("auxData", {}).get("type") == "isolated"
                    and c.get("name") == EXT_NAME):
                return c["id"]
        return None

    def ev(self, expr):
        self.ws._id = max(self.ws._id, 100)   # clear of the raw Runtime.enable id
        r = self.ws.call("Runtime.evaluate", {"expression": expr, "contextId": self.ctx,
                                              "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError((d.get("exception") or {}).get("description") or d.get("text"))
        return r["result"].get("value")

    def save(self, turns, chat_turn=False):
        evict = "true" if chat_turn else "false"
        return self.ev("new Promise(r => chrome.runtime.sendMessage("
                       f"{{malabr: 'save', transcript: {json.dumps(turns)}, "
                       f"evictOtherOrigins: {evict}}}, r))")

    def go(self, url):
        self.ws.call("Page.navigate", {"url": url}); time.sleep(2.0)
        self.ctx = self.find_ctx()
        return self.ctx is not None

    def restore(self):
        return self.ev("new Promise(r => chrome.runtime.sendMessage({malabr: 'restore'}, r))")


def sw_keys():
    """Every transcript key, read from inside the service worker."""
    sw = [x for x in jget("/json/list") if x["type"] == "service_worker"]
    if not sw:
        return None
    return WS(sw[0]["webSocketDebuggerUrl"]).evaluate(
        "chrome.storage.session.get(null).then(o => Object.keys(o))")


def main():
    if not os.path.exists(CHROME):
        sys.exit(f"no chrome at {CHROME}")
    shutil.rmtree(PROFILE, ignore_errors=True)
    serve()
    log = open("/tmp/malabr_transcript_chrome.log", "w")
    proc = subprocess.Popen([CHROME, "--no-sandbox", f"--user-data-dir={PROFILE}",
                             f"--remote-debugging-port={CDP}",
                             f"--load-extension={os.path.abspath(EXT)}",
                             "--disable-gpu", "--no-first-run",
                             "--no-default-browser-check", "about:blank"],
                            stdout=log, stderr=subprocess.STDOUT)
    try:
        ver = None
        for _ in range(60):
            try:
                ver = jget("/json/version"); break
            except OSError:
                time.sleep(0.5)
        if ver is None:
            sys.exit("browser never opened its CDP port")
        print("browser:", ver["Browser"])
        b = WS(ver["webSocketDebuggerUrl"])
        A, B = f"http://localhost:{HTTP}/", f"http://127.0.0.1:{HTTP}/"
        t1, t2, t3 = Tab(b, A), Tab(b, A), Tab(b, B)
        if not all(t.ctx for t in (t1, t2, t3)):
            check("content-script world found in every tab", False)
            return 1

        try:
            got = t1.ev("chrome.storage.session.get(null).then(o => 'READ')")
        except RuntimeError as e:
            got = str(e)[:70]
        check("content script cannot read storage.session directly", got != "READ", got)

        check("tab1 saves", t1.save([{"who": "you", "text": "t1 secret"}]) is True)
        check("tab1 restores its own transcript",
              (t1.restore() or [{}])[0].get("text") == "t1 secret")
        check("tab2, SAME origin, does not get tab1's transcript", t2.restore() is None)
        check("tab3, other origin, does not get it", t3.restore() is None)
        t2.save([{"who": "you", "text": "t2 own"}])

        t1.ws.call("Page.reload"); time.sleep(2.0)
        t1.ctx = t1.find_ctx()
        check("tab1 restores after reload",
              t1.ctx is not None and (t1.restore() or [{}])[0].get("text") == "t1 secret")

        # Rule 1, mirrored, in a tab of its own (t4). The server evicts lazily,
        # on the new origin's first chat turn; the transcript must too.
        def count(origin_tail):
            return len([k for k in (sw_keys() or []) if k.endswith(origin_tail)])
        a_tail, b_tail = f"localhost:{HTTP}", f"127.0.0.1:{HTTP}"
        t4 = Tab(b, A)
        t4.save([{"who": "you", "text": "t4 on A"}], chat_turn=True)
        check("chat turn with no other origin keeps the own key",
              (t4.restore() or [{}])[0].get("text") == "t4 on A")
        a_before, b_before = count(a_tail), count(b_tail)
        nav = t4.go(B) and t4.go(A)
        check("passing through another origin deletes nothing",
              nav and (t4.restore() or [{}])[0].get("text") == "t4 on A")
        t4.go(B)
        t4.save([])                                   # e.g. "New chat": not a chat turn
        check("a save that is not a chat turn evicts nothing",
              count(a_tail) == a_before, f"{count(a_tail)} vs {a_before}")
        t4.save([{"who": "you", "text": "t4 on B"}], chat_turn=True)
        check("a chat turn on B deletes this tab's A transcript, no other tab's",
              count(a_tail) == a_before - 1 and count(b_tail) == b_before + 1
              and t2.restore() == [{"who": "you", "text": "t2 own"}],
              f"A {a_before}->{count(a_tail)}, B {b_before}->{count(b_tail)}")
        t4.go(A)
        check("back on A, nothing to restore", t4.restore() is None)
        b.call("Target.closeTarget", {"targetId": t4.tid}); time.sleep(1.0)

        b.call("Target.closeTarget", {"targetId": t1.tid}); time.sleep(1.5)
        keys = sw_keys()
        if keys is None:
            check("service worker reachable to inspect keys", False)
            return 1
        # Two localhost keys existed before the close (tab1, tab2).
        local = [k for k in keys if k.endswith(f"localhost:{HTTP}")]
        check("closing tab1 deleted its transcript", len(local) == 1, repr(keys))
        check("tab2's transcript survives", t2.restore() == [{"who": "you", "text": "t2 own"}])

        failed = [n for n, ok in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        return 1 if failed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(PROFILE, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
