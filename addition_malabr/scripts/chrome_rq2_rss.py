#!/usr/bin/env python3
"""RQ2, the part that actually bites: what does an undivided pool COST in RAM?

Experiment A showed Chrome hands every session a full 9216-token context
window regardless of how many already exist. That is only a problem if those
windows are really resident -- so this measures it directly.

Sessions are created one at a time and each is FILLED to its window, with the
resident memory of the whole Chrome process tree sampled after each. A
per-session KV cache that is genuinely allocated shows up as a step per
session; a shared or lazily-allocated one does not.

The comparison this is built for is MALABR's §8 measurement: its KV pool is
allocated ONCE at startup and RSS is flat from 1 to 8 sessions (~1139 MB
regardless of occupancy), because the pool is divided rather than handed out.

    python3 chrome_rq2_rss.py --sessions 6
"""
import argparse, json, os, subprocess, sys, threading, time
import http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME_DIR = os.environ.get(
    "CHROME_DIR", "/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome")
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-cpu-profile")
CDP_PORT, HTTP_PORT = 9372, 8825
FEATURES = ",".join([
    "AIPromptAPI", "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])

# Create a session, fill it to its window, keep it alive, report its usage.
FILL_JS = """(async (chunkChars, maxRounds) => {
  window.__held = window.__held || [];
  const s = await LanguageModel.create();
  window.__held.push(s);                    // never destroyed: stays resident
  const filler = 'The quick brown fox jumps over the lazy dog. '
                 .repeat(Math.ceil(chunkChars / 44));
  let overflowed = false;
  s.addEventListener('contextoverflow', () => { overflowed = true; });
  for (let i = 0; i < maxRounds; i++) {
    try { await s.append([{role:'user', content: filler}]); } catch (e) { break; }
    if (overflowed) break;
  }
  return {window: s.contextWindow, usage: s.contextUsage, held: window.__held.length};
})(%d, %d)"""


def tree_rss_mb():
    """RSS of every process attached to this profile, in MB."""
    total = 0
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            cl = open(f"/proc/{p}/cmdline", "rb").read().decode("latin-1")
            if "nano-cpu-profile" not in cl or "chrome" not in cl:
                continue
            for line in open(f"/proc/{p}/status"):
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
        except (OSError, ValueError):
            continue
    return total / 1024.0


def kill_stale():
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            cl = open(f"/proc/{p}/cmdline", "rb").read().decode("latin-1")
        except OSError:
            continue
        if "nano-cpu-profile" in cl and "chrome" in cl and "python" not in cl:
            try: os.kill(int(p), 9)
            except OSError: pass
    time.sleep(4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=12000)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *x, **k): super().__init__(*x, directory=DOCROOT, **k)
        def log_message(self, *x): pass
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(
        target=socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H).serve_forever,
        daemon=True).start()

    kill_stale()
    proc = subprocess.Popen(
        [os.path.join(CHROME_DIR, "chrome"), "--disable-gpu",
         "--disable-software-rasterizer", f"--user-data-dir={PROFILE}",
         f"--remote-debugging-port={CDP_PORT}", f"--enable-features={FEATURES}",
         "--no-first-run", "--no-default-browser-check",
         f"http://localhost:{HTTP_PORT}/"],
        stdout=open("/tmp/chrome_rq2rss.log", "wb"), stderr=subprocess.STDOUT)
    try:
        http_json(CDP_PORT, timeout=120)
        ws = WS(next(t for t in http_json(CDP_PORT) if t["type"] == "page")
                ["webSocketDebuggerUrl"], timeout=2400)
        ws.call("Runtime.enable"); ws.call("Page.enable")
        ws.call("Page.navigate", {"url": f"http://localhost:{HTTP_PORT}/"})
        for _ in range(80):
            if ws.evaluate("location.origin", await_promise=False) == \
                    f"http://localhost:{HTTP_PORT}":
                break
            time.sleep(0.5)
        if ws.evaluate("typeof LanguageModel==='undefined' ? 'no-api'"
                       " : LanguageModel.availability()", timeout=120) != "available":
            print("model not available"); return 2

        # Baseline: model loaded, no sessions. One prompt forces the service to
        # fully materialise so the baseline is not flattered by lazy loading.
        ws.evaluate("""(async()=>{const s=await LanguageModel.create();
            await s.prompt('hi'); s.destroy(); return 1;})()""", timeout=900)
        time.sleep(3)
        base = tree_rss_mb()
        print(f"=== RQ2: resident cost of an undivided context pool ===")
        print(f"  baseline (model loaded, 0 sessions held): {base:8.0f} MB\n")
        print(f"  {'sessions':>8} {'usage tok':>10} {'RSS MB':>9} {'delta':>8}")

        rows, prev = [], base
        for i in range(1, a.sessions + 1):
            r = ws.evaluate(FILL_JS % (a.chunk, a.rounds), timeout=2400)
            time.sleep(2)
            rss = tree_rss_mb()
            rows.append({"sessions": i, "usage": r["usage"],
                         "window": r["window"], "rss_mb": round(rss, 1)})
            print(f"  {i:>8} {r['usage']:>10} {rss:>9.0f} {rss-prev:>+8.0f}",
                  flush=True)
            prev = rss

        if rows:
            grew = rows[-1]["rss_mb"] - base
            per = grew / len(rows)
            print(f"\n  {len(rows)} filled sessions added {grew:.0f} MB "
                  f"({per:.0f} MB per session) on top of a {base:.0f} MB baseline.")
            print("  MALABR for contrast: its KV pool is allocated once at")
            print("  startup and RSS is FLAT from 1 to 8 sessions (~1139 MB),")
            print("  because the pool is divided rather than handed out per")
            print("  session. Growth here is the cost of not dividing it.")
        if a.json:
            json.dump({"baseline_mb": base, "rows": rows}, open(a.json, "w"),
                      indent=2)
            print(f"\n  wrote {a.json}")
        return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
