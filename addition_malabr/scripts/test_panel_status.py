#!/usr/bin/env python3
"""§12 test 72, run by hand: the panel shows a conversation only while the
model still holds it.

Needs the whole stack -- the patched Chromium with MalabrFeature on, the real
model server, the real panel -- so it is not part of the §12 suite. It launches
through run_malabr.sh (a scratch profile and a private socket), drives the
panel's closed shadow DOM over CDP the way a user would (type, Enter), and
restarts the server by hand where a case needs one. Takes a few minutes.

Cases:
  1. a reload redraws the conversation: same tab, same origin, same server
  2. back/forward cache: chat on A, leave for B and chat there (rule 1 drops
     A on the server), go Back -- the cached panel clears itself
  3. restart mid-page: chat, restart the server, chat again -- the panel keeps
     only what the new server holds, and a reload redraws exactly that
  4. restart, then reload without chatting -- nothing is redrawn
Plus the server's own boundaries, which only the real browser can show:
  - the browser is the server's only allowed peer: a program that is not the
    browser (this script) is refused
  - the hand-restarted server needs the opt-out, and warns loudly about it
  - killing the browser (a crash) takes the server down with it
"""
import http.server, json, os, shutil, signal, socket, socketserver, struct, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
EXT = os.path.join(HERE, "..", "mserver", "malabr_chat_extension")
CDP, HTTP = 9351, 8793
PROFILE = "/tmp/malabr-panelstatus-profile"
DOCROOT = "/tmp/malabr-panelstatus-pages"
LOG = "/tmp/malabr_panelstatus.log"
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), "malabr-paneltest.sck")
SERVER = "addition_malabr/mserver/app.py"
ENV = {"MALABR_SOCKET_PATH": SOCK, "MALABR_SHARED_KV": "1", "MALABR_CPU_DUTY": "1.0",
       "MALABR_PYTHON": "/home/chaitu/Desktop/vscode/malabr/bin/python"}
A, B = f"http://localhost:{HTTP}/", f"http://127.0.0.1:{HTTP}/"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def serve():
    os.makedirs(DOCROOT, exist_ok=True)
    with open(os.path.join(DOCROOT, "index.html"), "w") as f:
        f.write("<!doctype html><title>t</title><p>A short test page about birds.</p>")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a):
            pass
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", HTTP), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def server_pids():
    out = subprocess.run(["pgrep", "-f", SERVER], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def socket_ready(timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.socket(socket.AF_UNIX); s.connect(SOCK); s.close()
            return True
        except OSError:
            time.sleep(1.0)
    return False


def foreign_status():
    """Ask the server for a status AS THIS SCRIPT, not as the browser. A server
    started by the browser must close the connection without answering."""
    body = b"\x00MALABR::status"
    hdr = f"ROUTE_MALABR_GENERATE_API,{'a' * 32},1,{A.rstrip('/')},foreground,{len(body)}".encode()
    c = socket.socket(socket.AF_UNIX); c.settimeout(5)
    buf = b""
    try:
        c.connect(SOCK)
        c.sendall(struct.pack(">I", len(hdr)) + hdr + body)
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError:
        pass
    finally:
        c.close()
    return buf


def launch(log, env):
    return subprocess.Popen([os.path.join(HERE, "run_malabr.sh"),
                             f"--remote-debugging-port={CDP}", "--no-first-run",
                             "--no-default-browser-check", "about:blank"],
                            env=env, stdout=log, stderr=subprocess.STDOUT)


def crash_check(log, env):
    """Kill the browser outright and expect its server to follow it."""
    shutil.rmtree(PROFILE, ignore_errors=True)
    chrome = launch(log, env)              # run_malabr.sh execs, so this pid IS chrome
    try:
        http_json(CDP, "/json/version", timeout=60)
        if not socket_ready(240) or not server_pids():
            return False, "server never came up"
        server = server_pids()[0]
        os.kill(chrome.pid, signal.SIGKILL)
        for _ in range(100):
            if server not in server_pids():
                return True, f"server pid {server} exited after the browser was killed"
            time.sleep(0.1)
        return False, f"server pid {server} still running 10 s after the browser died"
    finally:
        try:
            chrome.kill()
        except OSError:
            pass
        subprocess.run(["pkill", "-f", PROFILE], capture_output=True)


def restart_server(log):
    """Kill the running server and start a fresh one on the same socket. The
    browser does not respawn it, so this is a stand-in for a crash + restart."""
    for pid in server_pids():
        os.kill(pid, signal.SIGTERM)
    end = time.time() + 20
    while server_pids() and time.time() < end:
        time.sleep(0.5)
    for pid in server_pids():
        os.kill(pid, signal.SIGKILL)
    # Its parent is now this script, not the browser, so the browser would be
    # refused without the opt-out (which the server must announce loudly).
    subprocess.Popen([ENV["MALABR_PYTHON"], "-u", SERVER], cwd=REPO_ROOT,
                     env={**os.environ, **ENV, "MALABR_TRUST_ANY_PEER": "1"},
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(2.0)                       # let the old socket file be replaced
    return socket_ready(180)


class Panel:
    """The panel in one tab, reached through its CLOSED shadow root: CDP's
    pierce sees it; page script cannot."""

    def __init__(self, ws):
        self.ws = ws

    def _shadow(self):
        doc = self.ws.call("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        stack = [doc]
        while stack:
            n = stack.pop()
            attrs = n.get("attributes", [])
            if n.get("localName") == "div" and "malabr-root" in attrs[1::2]:
                return n["shadowRoots"][0]["nodeId"]
            stack += n.get("children", []) + n.get("shadowRoots", [])
        return None

    def _node(self, selector):
        root = self._shadow()
        if root is None:
            return None
        nid = self.ws.call("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
        return nid or None

    def _get(self, selector, fn):
        nid = self._node(selector)
        if nid is None:
            return None
        obj = self.ws.call("DOM.resolveNode", {"nodeId": nid})["object"]["objectId"]
        return self.ws.call("Runtime.callFunctionOn",
                            {"objectId": obj, "functionDeclaration": fn,
                             "returnByValue": True})["result"].get("value")

    def wait_ready(self, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if self._shadow() is not None:
                time.sleep(2.0)           # let restore() and its status check land
                return True
            time.sleep(0.5)
        return False

    def text(self):
        return self._get(".log", "function () { return this.innerText; }") or ""

    def busy(self):
        return self._get(".act", "function () { return this.classList.contains('stop'); }")

    def ask(self, prompt, timeout=240):
        self.ws.call("DOM.focus", {"nodeId": self._node("textarea")})
        self.ws.call("Input.insertText", {"text": prompt})
        for t in ("rawKeyDown", "keyUp"):
            self.ws.call("Input.dispatchKeyEvent",
                         {"type": t, "key": "Enter", "code": "Enter",
                          "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        time.sleep(1.0)
        end = time.time() + timeout
        while time.time() < end and self.busy():
            time.sleep(1.0)
        time.sleep(3.0)                   # the post-reply status check
        return not self.busy()


def main():
    if server_pids():
        sys.exit(f"a MALABR server is already running ({server_pids()}); stop it first")
    shutil.rmtree(PROFILE, ignore_errors=True)
    serve()
    log = open(LOG, "w")
    env = {**os.environ, **ENV, "EXTENSION_DIR": os.path.abspath(EXT),
           "USER_DATA_DIR": PROFILE, "RUN_LOG": LOG + ".runs"}
    chrome = launch(log, env)
    try:
        ver = http_json(CDP, "/json/version", timeout=60)
        print("browser:", ver["Browser"])
        if not socket_ready(240):
            check("model server came up", False, f"see {LOG}")
            return 1
        check("a program that is not the browser is refused", foreign_status() == b"")
        b = WS(ver["webSocketDebuggerUrl"])
        tid = b.call("Target.createTarget", {"url": A})["targetId"]
        time.sleep(2.0)
        t = [x for x in http_json(CDP) if x["id"] == tid][0]
        ws = WS(t["webSocketDebuggerUrl"])
        p = Panel(ws)
        ws.call("Page.enable")

        def go(url):
            ws.call("Page.navigate", {"url": url})
            time.sleep(1.5)
            return p.wait_ready()

        def reload():
            ws.call("Page.reload")
            time.sleep(1.5)
            return p.wait_ready()

        if not p.wait_ready(60):
            check("panel injected", False)
            return 1

        # 1. reload keeps the conversation
        p.ask("Remember the codeword PELICAN. Reply with OK only.")
        reload()
        txt = p.text()
        check("1. reload: conversation redrawn",
              "PELICAN" in txt and "restored after reload" in txt, repr(txt[-120:]))

        # 2. back/forward cache, after rule 1 dropped A on the server
        ws.call("Runtime.evaluate", {"expression": "window.__malabr_marker = 1"})
        go(B)
        p.ask("Reply with OK only.")
        ws.call("Runtime.evaluate", {"expression": "history.back()"})
        time.sleep(4.0)
        cached = ws.call("Runtime.evaluate",
                         {"expression": "window.__malabr_marker === 1",
                          "returnByValue": True})["result"].get("value")
        txt = p.text()
        if cached:
            check("2. back/forward cache: stale panel cleared",
                  "PELICAN" not in txt and "ended on the server while you were away" in txt,
                  repr(txt[-120:]))
        else:
            # Not cached: a fresh load ran restore(), and bg.js had already
            # dropped A's copy on B's chat turn. Still correct, different path.
            check("2. Back (page NOT served from cache): nothing stale drawn",
                  "PELICAN" not in txt, repr(txt[-120:]))
            print("     note: the page was not restored from the back/forward cache,"
                  " so the pageshow path was not exercised", flush=True)

        # 3. restart mid-page
        go(A)
        p.ask("Remember the codeword HERON. Reply with OK only.")
        before = p.text()
        up = restart_server(log)
        log.flush()
        warned = "peer check is OFF" in open(LOG, errors="replace").read()
        check("server restarted (opt-out on, and it warned)", up and warned, f"see {LOG}")
        p.ask("Reply with the single word DONE.")
        txt = p.text()
        check("3a. restart mid-page: earlier turns dropped, the new turn kept",
              "HERON" in before and "HERON" not in txt and "DONE" in txt
              and "no longer has the earlier messages" in txt, repr(txt[-160:]))
        reload()
        txt = p.text()
        check("3b. ...and a reload redraws exactly what the new server holds",
              "DONE" in txt and "HERON" not in txt and "restored after reload" in txt,
              repr(txt[-120:]))

        # 4. restart, then reload without chatting
        up = restart_server(log)
        reload()
        txt = p.text()
        check("4. restart then reload: nothing redrawn",
              up and "DONE" not in txt and "ended on the server" in txt, repr(txt[-120:]))

    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        # The hand-restarted server's parent is this script, not the browser.
        for pid in server_pids():
            os.kill(pid, signal.SIGTERM)
        end = time.time() + 20
        while server_pids() and time.time() < end:
            time.sleep(0.5)
        shutil.rmtree(PROFILE, ignore_errors=True)

    # 5. crash: a fresh browser + server, then SIGKILL the browser
    ok, detail = crash_check(log, env)
    check("5. browser killed: its server exits too", ok, detail)
    for pid in server_pids():
        os.kill(pid, signal.SIGTERM)
    shutil.rmtree(PROFILE, ignore_errors=True)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed   (log: {LOG})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
