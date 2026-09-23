#!/usr/bin/env python3
"""Chrome's built-in Prompt API under contention -- the RELATIVE baseline.

The comparison against MALABR must not be a race in seconds. Chrome runs a
different model (Gemini Nano) at a different size and quantization, so absolute
latency would measure the model, not the scheduler. What IS comparable is each
system's behaviour against ITSELF:

    foreground TTFT / median TTFT across all tabs

A system that prioritises the tab you are looking at scores well below 1.0.
A system with no notion of a foreground scores about 1.0 -- it cannot tell the
tabs apart. That ratio is dimensionless, so it survives the models differing,
and it is the number to put beside MALABR's.

Method: open N same-origin tabs, each holding its own LanguageModel session,
fire all prompts at once with the LAST tab foregrounded (the realistic case --
background tabs already busy when the user turns to a tab and types), and
record each tab's time to first streamed chunk.

Requires Gemini Nano to be downloaded. As of this writing it is NOT obtainable
on the development machine -- see design_doc/session_handoff_v6.md for the full
diagnosis -- so this script exits cleanly saying so rather than inventing
numbers. It is here ready to run wherever the model does exist.

  python3 chrome_relative_bench.py --tabs 8
"""
import argparse, json, os, statistics, subprocess, sys, time
import http.server, socketserver, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME_DIR = os.environ.get(
    "CHROME_DIR", "/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome")
# The CPU profile, deliberately. Chrome reads ~11 GB of "VRAM" from this
# machine's Intel iGPU, decides the device is GPU-capable, and selects
# nano_v3_gpu_component -- which never downloads (it sat at 0% for 13 hours).
# --disable-gpu below makes it select nano_v3_cpu_component, which does. That
# is also the RIGHT baseline: MALABR is CPU-only, so a GPU-accelerated Nano
# would confound the scheduler comparison with an accelerator difference.
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-cpu-profile")
CDP_PORT = int(os.environ.get("NANO_PORT", "9360"))
HTTP_PORT = int(os.environ.get("NANO_HTTP_PORT", "8810"))

FEATURES = ",".join([
    "AIPromptAPI", "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])

PROMPTS = [
    "Explain in three sentences what a hash table is.",
    "What is the difference between a process and a thread?",
    "Describe the TCP three-way handshake.",
    "What is a race condition? Give one example.",
    "Explain what a page fault is.",
    "What does a write-ahead log guarantee?",
    "Summarise the CAP theorem.",
    "What is a memory barrier for?",
]

# Runs in each tab. Resolves as soon as the FIRST chunk arrives and abandons
# the rest of the stream.
#
# Waiting for completion instead would not measure TTFT, and on CPU-only Nano
# it does not terminate in any useful time: an early version of this harness
# ran the stream to the end and three of four background tabs hit a ten-minute
# timeout having long since produced their first token. Time-to-first-token is
# the quantity the ratio is built on, so take it and stop.
RUN_JS = """
(async (prompt) => {
  const t0 = performance.now();
  const s = await LanguageModel.create();
  const created = performance.now() - t0;
  const stream = s.promptStreaming(prompt);
  let first = null;
  for await (const chunk of stream) {
    if (chunk && chunk.length) { first = performance.now() - t0; break; }
  }
  try { s.destroy(); } catch (e) {}          // abandons the remaining stream
  return {ttft_ms: first, create_ms: created};
})(%s)
"""


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def launch(no_throttle=False):
    extra = []
    if no_throttle:
        # A background TAB has its timers and task queues throttled by the
        # renderer, independently of anything the model scheduler does. If the
        # foreground advantage survives these flags it is attributable to
        # model scheduling; if it collapses toward 1.0, it was throttling.
        extra = ["--disable-background-timer-throttling",
                 "--disable-renderer-backgrounding",
                 "--disable-backgrounding-occluded-windows",
                 "--disable-features=IntensiveWakeUpThrottling"]
    args = [os.path.join(CHROME_DIR, "chrome"), *extra,
            f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
            f"--enable-features={FEATURES}",
            "--disable-gpu", "--disable-software-rasterizer",
            "--no-first-run", "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows",
            f"http://localhost:{HTTP_PORT}/"]
    return subprocess.Popen(args, stdout=open("/tmp/chrome_relbench.log", "wb"),
                            stderr=subprocess.STDOUT)


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=8)
    ap.add_argument("--no-throttle", action="store_true",
                    help="disable renderer/timer background throttling, to "
                         "separate model scheduling from browser throttling")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    serve()
    proc = launch(a.no_throttle)
    try:
        http_json(CDP_PORT, timeout=120)
        # One tab per client, all on the same origin.
        base = f"http://localhost:{HTTP_PORT}/"
        targets = []
        for i in range(a.tabs):
            r = subprocess.run(
                ["curl", "-s", "-X", "PUT",
                 f"http://127.0.0.1:{CDP_PORT}/json/new?{base}"],
                capture_output=True, text=True)
            try: targets.append(json.loads(r.stdout))
            except Exception: pass
        time.sleep(3)

        pages = [t for t in http_json(CDP_PORT) if t["type"] == "page"
                 and f"localhost:{HTTP_PORT}" in t.get("url", "")]
        pages = pages[:a.tabs]
        if len(pages) < a.tabs:
            print(f"only {len(pages)} of {a.tabs} tabs opened")
        conns = [WS(p["webSocketDebuggerUrl"]) for p in pages]
        for ws in conns:
            ws.call("Runtime.enable")

        avail = conns[0].evaluate(
            "typeof LanguageModel==='undefined' ? 'no-api'"
            " : LanguageModel.availability()", timeout=120)
        if avail != "available":
            print(f"\nGemini Nano is not usable here (availability: {avail!r}).")
            print("Nothing is measured rather than guessed. See")
            print("design_doc/session_handoff_v6.md for why, and re-run this")
            print("script unchanged on a machine where the model downloads.")
            return 2

        # Foreground = the LAST tab: background tabs are already busy when the
        # user turns to a tab and types. Bringing it to the front is what makes
        # Chrome consider it visible.
        fg = len(conns) - 1
        conns[fg].call("Page.bringToFront")
        time.sleep(0.5)

        results, threads = {}, []

        def run(i, ws):
            try:
                results[i] = ws.evaluate(
                    RUN_JS % json.dumps(PROMPTS[i % len(PROMPTS)]), timeout=1200)
            except Exception as e:                      # noqa: BLE001
                results[i] = {"error": str(e)}

        for i, ws in enumerate(conns):
            t = threading.Thread(target=run, args=(i, ws), daemon=True)
            threads.append(t)
        for t in threads: t.start()
        for t in threads: t.join(timeout=1500)

        # TWO measures, because the comparison against MALABR depends on it.
        #  from_create: t0 before LanguageModel.create() -- what a page feels.
        #  from_prompt: create() subtracted -- the interval MALABR measures,
        #               whose harness creates the session BEFORE starting the
        #               clock. Comparing MALABR against from_create would
        #               charge Chrome for work MALABR does not count.
        ttfts = {i: r["ttft_ms"] / 1000.0 for i, r in results.items()
                 if isinstance(r, dict) and r.get("ttft_ms") is not None}
        ttfts_prompt = {i: (r["ttft_ms"] - r.get("create_ms", 0)) / 1000.0
                        for i, r in results.items()
                        if isinstance(r, dict) and r.get("ttft_ms") is not None}
        bad = {i: r for i, r in results.items() if i not in ttfts}
        if bad:
            print(f"\n  {len(bad)} of {len(conns)} tabs produced NO token:")
            for i, r in sorted(bad.items()):
                print(f"    tab {i}: {str(r)[:150]}")
        if not ttfts:
            print("no tab produced a token")
            return 1
        # A ratio computed from a handful of survivors is not a measurement of
        # the scheduler -- it is a measurement of which tabs happened to
        # survive. Refuse rather than mislead.
        if len(ttfts) < max(4, len(conns) // 2):
            print(f"\n  ONLY {len(ttfts)}/{len(conns)} tabs reported a TTFT -- too few to")
            print("  characterise the scheduler. Not reporting a ratio.")
            return 1

        vals = list(ttfts.values())
        med = statistics.median(vals)
        fg_ttft = ttfts.get(fg, float("nan"))
        print(f"\n=== Chrome built-in Prompt API, {len(vals)} tabs"
          f"{', background throttling DISABLED' if a.no_throttle else ''} ===")
        print(f"  foreground TTFT : {fg_ttft:6.2f}s  (tab {fg}, brought to front)")
        print(f"  median TTFT     : {med:6.2f}s")
        print(f"  p95 TTFT        : {pct(vals,95):6.2f}s")
        print(f"\n  RELATIVE SCORE (foreground / median): {fg_ttft/med:.2f}")
        pv = list(ttfts_prompt.values())
        pmed = statistics.median(pv)
        pfg = ttfts_prompt.get(fg, float("nan"))
        print(f"  RELATIVE SCORE excluding create(): {pfg/pmed:.2f}"
              f"   (fg {pfg:.2f}s / median {pmed:.2f}s)")
        print("  The second line is the one comparable to MALABR, whose")
        print("  harness creates the session before starting its clock.")
        print("  Below 1.0 means the visible tab is prioritised; about 1.0")
        print("  means the scheduler cannot tell the tabs apart.")
        if a.json:
            json.dump({"ttft_s": ttfts, "ttft_from_prompt_s": ttfts_prompt,
                       "foreground_tab": fg, "median_s": med,
                       "ratio": fg_ttft / med,
                       "ratio_from_prompt": pfg / pmed},
                      open(a.json, "w"), indent=2)
            print(f"\n  wrote {a.json}")
        return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
