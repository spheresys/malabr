#!/usr/bin/env python3
"""RQ4 against Chrome: does closing a tab actually recover its compute?

MALABR's answer is measured (tests/cancellation_bench.py): 453 -> 0 wasted
decode tokens, survivors finish 1.94x sooner. This asks the same of Chrome's
built-in Prompt API.

The measurement is WITHIN a run, which removes most of the cross-run variance
that CPU-only Nano suffers from. N tabs stream simultaneously; halfway through,
half of them are closed outright (the CDP target is closed -- a real tab close,
not a JS destroy()). For the tabs that SURVIVE we compare their chunk arrival
rate before the close against after it:

    speed-up = rate_after / rate_before

If Chrome reclaims the closed tabs' compute, the survivors should accelerate
sharply -- they are now sharing the machine with half as many sessions. If the
work for closed tabs keeps running, the survivors see little or no change.

A control arm closes nothing, so any speed-up from sessions simply finishing
can be subtracted.

    python3 chrome_rq4_cancel.py --tabs 4 --runs 3
"""
import argparse, json, os, statistics as st, subprocess, sys, threading, time
import http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME_DIR = os.environ.get(
    "CHROME_DIR", "/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome")
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-cpu-profile")
CDP_PORT, HTTP_PORT = 9365, 8815
FEATURES = ",".join([
    "AIPromptAPI", "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])
PROMPTS = [
    "Write a long, detailed explanation of how virtual memory works.",
    "Write a long, detailed explanation of how TCP congestion control works.",
    "Write a long, detailed explanation of how a compiler optimises code.",
    "Write a long, detailed explanation of how databases achieve durability.",
]

# Streams for a fixed wall-clock window and reports every chunk's arrival, so
# the rate on each side of the close can be computed.
RUN_JS = """
(async (prompt, seconds) => {
  const t0 = performance.now();
  window.__at = [];
  const s = await LanguageModel.create();
  const stream = s.promptStreaming(prompt);
  for await (const chunk of stream) {
    if (!chunk || !chunk.length) continue;
    window.__at.push(performance.now() - t0);
    if (performance.now() - t0 > seconds * 1000) break;
  }
  try { s.destroy(); } catch (e) {}
  return window.__at;
})(%s, %d)
"""


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def launch():
    args = [os.path.join(CHROME_DIR, "chrome"), "--disable-gpu",
            "--disable-software-rasterizer",
            f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
            f"--enable-features={FEATURES}", "--no-first-run",
            "--no-default-browser-check",
            f"http://localhost:{HTTP_PORT}/"]
    return subprocess.Popen(args, stdout=open("/tmp/chrome_rq4.log", "wb"),
                            stderr=subprocess.STDOUT)


def rate(arrivals, lo, hi):
    n = sum(1 for t in arrivals if lo <= t < hi)
    return n / (hi - lo) if hi > lo else float("nan")


def one_run(close_half, tabs, window, close_at):
    proc = launch()
    try:
        http_json(CDP_PORT, timeout=120)
        base = f"http://localhost:{HTTP_PORT}/"
        for _ in range(tabs):
            subprocess.run(["curl", "-s", "-X", "PUT",
                            f"http://127.0.0.1:{CDP_PORT}/json/new?{base}"],
                           capture_output=True)
        time.sleep(3)
        pages = [t for t in http_json(CDP_PORT) if t["type"] == "page"
                 and f"localhost:{HTTP_PORT}" in t.get("url", "")][:tabs]
        if len(pages) < tabs:
            return None
        conns = [WS(p["webSocketDebuggerUrl"]) for p in pages]
        for ws in conns:
            ws.call("Runtime.enable")
        if conns[0].evaluate("typeof LanguageModel==='undefined' ? 'no-api'"
                             " : LanguageModel.availability()",
                             timeout=120) != "available":
            return None

        survivors = list(range(tabs // 2))          # first half survive
        doomed = list(range(tabs // 2, tabs))
        out = {}

        def run(i, ws):
            try:
                out[i] = ws.evaluate(RUN_JS % (json.dumps(PROMPTS[i % len(PROMPTS)]),
                                               window), timeout=window + 300)
            except Exception as e:                   # noqa: BLE001
                out[i] = {"error": str(e)}

        threads = [threading.Thread(target=run, args=(i, c), daemon=True)
                   for i, c in enumerate(conns)]
        for t in threads: t.start()

        time.sleep(close_at)
        if close_half:
            for i in doomed:                        # a REAL tab close
                subprocess.run(["curl", "-s",
                                f"http://127.0.0.1:{CDP_PORT}/json/close/{pages[i]['id']}"],
                               capture_output=True)
        for t in threads: t.join(timeout=window + 240)

        # Survivors only, and only tabs that actually streamed on both sides.
        ratios = []
        for i in survivors:
            a = out.get(i)
            if not isinstance(a, list) or len(a) < 6:
                continue
            a = [x / 1000.0 for x in a]
            before = rate(a, 1.0, close_at)
            after = rate(a, close_at + 1.0, window)
            if before > 0 and after > 0:
                ratios.append(after / before)
        return ratios or None
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill()


def kill_stale():
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            cl = open(f"/proc/{p}/cmdline", "rb").read().decode("latin-1")
        except OSError:
            continue
        if "nano-cpu-profile" in cl and "chrome" in cl:
            try: os.kill(int(p), 15)
            except OSError: pass
    time.sleep(4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=4)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--window", type=int, default=40)
    ap.add_argument("--close-at", type=float, default=15.0)
    a = ap.parse_args()
    serve()

    print(f"=== RQ4 vs Chrome: {a.tabs} tabs, closing {a.tabs//2} at "
          f"t={a.close_at}s, {a.window}s window ===")
    print("  survivor speed-up = chunk rate after the close / rate before\n")

    res = {}
    for arm, close in (("control (close nothing)", False),
                       ("close half the tabs", True)):
        vals = []
        for r in range(1, a.runs + 1):
            kill_stale()
            got = one_run(close, a.tabs, a.window, a.close_at)
            if got:
                vals.extend(got)
                print(f"  {arm:<24} run {r}: "
                      + " ".join(f"{x:.2f}x" for x in got), flush=True)
            else:
                print(f"  {arm:<24} run {r}: no usable data", flush=True)
        res[arm] = vals

    print()
    for arm, vals in res.items():
        if vals:
            print(f"  {arm:<24} median speed-up {st.median(vals):.2f}x "
                  f"(n={len(vals)}, min {min(vals):.2f}, max {max(vals):.2f})")
        else:
            print(f"  {arm:<24} no data")
    c, k = res.get("control (close nothing)"), res.get("close half the tabs")
    if c and k:
        print(f"\n  Closing half the tabs changes the survivors' rate by "
              f"{st.median(k)/st.median(c):.2f}x relative to the control.")
        print("  Near 1.0 means closing a tab did NOT return its compute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
