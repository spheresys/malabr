#!/usr/bin/env python3
"""Download Gemini Nano in real Google Chrome, so it can serve as a baseline.

Why REAL Chrome and not our own build: `build_with_internal_optimization_guide`
defaults to `is_chrome_branded` (components/optimization_guide/features.gni), so
a public Chromium checkout compiles the STUB services/on_device_model/
on_device_model.cc -- GetEstimatedPerformanceClass() hardcoded to
kFailedToLoadLibrary and "inference" implemented as an echo of the prompt. No
command-line flag can turn that into a model; the real one is a Google-internal
library plus a component served only to branded builds.

Two non-obvious requirements, both of which cost a run to discover:

  1. SECURE CONTEXT. `LanguageModel` is [SecureContext]-gated, so it is absent
     on about:blank -- which is where Chrome lands when a URL on the command
     line is swallowed by an existing profile window. We serve a page on
     http://localhost (a secure origin) and navigate to it over CDP.
  2. USER GESTURE. create() starts a ~2 GB download and wants a gesture, so
     every evaluate() here runs with userGesture set.
"""
import argparse, http.server, json, os, shutil, socketserver, subprocess, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME = os.environ.get("REAL_CHROME", "/usr/bin/google-chrome")
# Big volume on purpose: the component install check needs 20 GB free on the
# filesystem holding the profile, and this machine's root has ~2 GB.
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-chrome-profile")
CDP_PORT = int(os.environ.get("NANO_PORT", "9334"))
HTTP_PORT = int(os.environ.get("NANO_HTTP_PORT", "8777"))

FEATURES = ",".join([
    "AIPromptAPI",
    "AIPromptAPIForExtension",
    "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a):
            pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def launch():
    args = [CHROME, f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={CDP_PORT}",
            f"--enable-features={FEATURES}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows",
            f"http://localhost:{HTTP_PORT}/"]
    log = open("/tmp/nano_real_chrome.log", "ab")
    log.write(f"\n===== launch {time.strftime('%F %T')} =====\n".encode()); log.flush()
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)


def attach():
    """Attach and make sure we are on the localhost origin, not about:blank."""
    targets = http_json(CDP_PORT, timeout=90)
    pages = [t for t in targets if t["type"] == "page"]
    local = [t for t in pages if f"localhost:{HTTP_PORT}" in t["url"]]
    page = (local or pages)[0]
    ws = WS(page["webSocketDebuggerUrl"])
    ws.call("Runtime.enable"); ws.call("Page.enable")
    if not local:
        ws.call("Page.navigate", {"url": f"http://localhost:{HTTP_PORT}/"})
    # Poll rather than sleep: Page.navigate returns before the new execution
    # context exists, and evaluating too early lands back on the old
    # about:blank context -- where isSecureContext is false and LanguageModel
    # is absent, which looks exactly like the API being unavailable.
    for _ in range(40):
        try:
            if (ws.evaluate("location.origin", await_promise=False)
                    == f"http://localhost:{HTTP_PORT}"
                    and ws.evaluate("window.isSecureContext", await_promise=False)):
                return ws
        except Exception:                             # noqa: BLE001
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"never reached a secure http://localhost:{HTTP_PORT} context; "
        f"LanguageModel would be absent. Current: "
        f"{ws.evaluate('location.href', await_promise=False)!r}")


AVAIL_JS = "typeof LanguageModel!=='undefined' ? LanguageModel.availability() : 'no-api'"

# Kick the download and keep the progress on window.__dl so we can poll it.
START_JS = r"""
(async () => {
  if (typeof LanguageModel === 'undefined') return 'no-api';
  if (window.__creating) return 'already';
  window.__creating = true; window.__dl = 0; window.__err = null;
  LanguageModel.create({
    monitor(m) {
      m.addEventListener('downloadprogress', e => { window.__dl = e.loaded; });
    }
  }).then(s => { window.__session = s; window.__ready = true; })
    .catch(e => { window.__err = String(e); window.__creating = false; });
  return 'started';
})()
"""

POLL_JS = "({dl: window.__dl||0, ready: !!window.__ready, err: window.__err||null})"


def disk_mb():
    base = os.path.join(PROFILE, "OptGuideOnDeviceModel")
    n = 0
    for root, _, files in os.walk(base) if os.path.isdir(base) else []:
        for f in files:
            try: n += os.path.getsize(os.path.join(root, f))
            except OSError: pass
    return n / (1024 * 1024)


def kill(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=3600)
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(CHROME):
        sys.exit(f"no chrome at {CHROME}")
    if a.fresh and os.path.isdir(PROFILE):
        shutil.rmtree(PROFILE)
    os.makedirs(PROFILE, exist_ok=True)
    os.makedirs(DOCROOT, exist_ok=True)

    ver = subprocess.run([CHROME, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"chrome  : {ver}")
    print(f"profile : {PROFILE}  ({shutil.disk_usage(PROFILE).free/1e9:.0f} GB free)")

    serve()
    proc = launch()
    try:
        ws = attach()
        print(f"origin  : {ws.evaluate('location.origin', await_promise=False)}")
        avail = ws.evaluate(AVAIL_JS, timeout=120)
        print(f"availability: {avail}")
        if avail == "no-api":
            print("LanguageModel absent even on a secure origin -- check the "
                  "feature flags against this Chrome version.")
            return 2
        if avail != "available":
            print("starting download:", ws.evaluate(START_JS, timeout=120))

        t0, last = time.time(), None
        while time.time() - t0 < a.wait:
            st = ws.evaluate(POLL_JS, await_promise=False)
            avail = ws.evaluate(AVAIL_JS, timeout=120)
            line = (f"  t+{int(time.time()-t0):5d}s  {avail:<13} "
                    f"progress={st['dl']:.3f}  on-disk={disk_mb():7.0f} MB"
                    + (f"  ERR={st['err']}" if st["err"] else ""))
            if line != last:
                print(line, flush=True); last = line
            if st["err"]:
                return 1
            if avail == "available" or st["ready"]:
                print("\n  READY -- Gemini Nano is usable as a baseline.")
                out = ws.evaluate("""(async () => {
                    const s = window.__session || await LanguageModel.create();
                    const r = await s.prompt('Reply with one word: ready');
                    return r; })()""", timeout=300)
                print("  smoke ->", repr(out))
                print(f"\n  Reuse this profile for benchmarking: {PROFILE}")
                return 0
            time.sleep(20)
        print(f"\n  timed out at: {last}")
        return 1
    finally:
        kill(proc)


if __name__ == "__main__":
    sys.exit(main())
