#!/usr/bin/env python3
"""RQ2 against Chrome: is resident context GOVERNED, or just capped per session?

Chrome 153 does expose per-session accounting -- `contextWindow`,
`contextUsage`, `measureContextUsage()` and an `oncontextoverflow` event. So
the naive claim "Chrome has no per-session budget" is wrong and is not what
this tests.

The question RQ2 actually asks is about the AGGREGATE. A per-session cap
protects nobody if every session is handed the same cap independently: N
sessions then claim N x window between them, and the machine, not the policy,
decides what happens. Governance means the pool is finite and divided.

So:

  A. Does `contextWindow` SHRINK as more sessions are created?
     constant  -> each session claims a full window; aggregate is unbounded,
                  and one greedy page can crowd the others out.
     shrinking -> Chrome is dividing a fixed pool, i.e. governing it.

  B. What happens at overflow? MALABR compacts, keeping an anchor and the
     recent turns (§8) and degrading gracefully. Chrome fires
     `oncontextoverflow`; this records what the session does next -- whether it
     still answers, and whether the earliest content survives.

  C. Does a greedy session degrade its NEIGHBOURS? One tab fills its context
     while others run normal prompts, against a control where every tab is
     normal. This is the question that matters to a user with many tabs open.
"""
import argparse, os, statistics as st, subprocess, sys, threading, time
import http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME_DIR = os.environ.get(
    "CHROME_DIR", "/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome")
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-cpu-profile")
CDP_PORT, HTTP_PORT = 9368, 8821
FEATURES = ",".join([
    "AIPromptAPI", "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])


def serve():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(
        target=socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H).serve_forever,
        daemon=True).start()


def launch():
    return subprocess.Popen(
        [os.path.join(CHROME_DIR, "chrome"), "--disable-gpu",
         "--disable-software-rasterizer", f"--user-data-dir={PROFILE}",
         f"--remote-debugging-port={CDP_PORT}", f"--enable-features={FEATURES}",
         "--no-first-run", "--no-default-browser-check",
         f"http://localhost:{HTTP_PORT}/"],
        stdout=open("/tmp/chrome_rq2.log", "wb"), stderr=subprocess.STDOUT)


def page(ws_url):
    ws = WS(ws_url, timeout=2400)   # must outlast the longest evaluate()
    ws.call("Runtime.enable"); ws.call("Page.enable")
    return ws


def wait_origin(ws):
    ws.call("Page.navigate", {"url": f"http://localhost:{HTTP_PORT}/"})
    for _ in range(80):
        if ws.evaluate("location.origin", await_promise=False) == \
                f"http://localhost:{HTTP_PORT}":
            return True
        time.sleep(0.5)
    return False


def open_tabs(n):
    for _ in range(n):
        subprocess.run(["curl", "-s", "-X", "PUT",
                        f"http://127.0.0.1:{CDP_PORT}/json/new?"
                        f"http://localhost:{HTTP_PORT}/"], capture_output=True)
    time.sleep(3)
    return [t for t in http_json(CDP_PORT) if t["type"] == "page"
            and f"localhost:{HTTP_PORT}" in t.get("url", "")]


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


# ---- A: does the window shrink as sessions multiply? ---------------------
HOLD_JS = """(async () => {
  window.__held = window.__held || [];
  const s = await LanguageModel.create();
  window.__held.push(s);
  return {window: s.contextWindow, usage: s.contextUsage};
})()"""


def experiment_a(conns):
    print("\n--- A. contextWindow as more sessions are held open ---")
    rows = []
    for i, ws in enumerate(conns, 1):
        r = ws.evaluate(HOLD_JS, timeout=600)
        rows.append((i, r["window"], r["usage"]))
        print(f"  sessions alive: {i:<3} contextWindow={r['window']:<8} "
              f"usage={r['usage']}")
    wins = [w for _, w, _ in rows]
    shrank = len(set(wins)) > 1 and wins[-1] < wins[0]
    verdict = ("divided (governed)" if shrank else
               "NOT divided: each session claims a full window independently")
    print(f"\n  window {'SHRINKS' if shrank else 'is CONSTANT'} as sessions "
          f"multiply -> aggregate is {verdict}")
    if wins:
        print(f"  {len(wins)} sessions x {wins[0]} tokens = "
              f"{len(wins)*wins[0]:,} tokens claimable in total")
    return rows


# ---- B: what happens at overflow? ---------------------------------------
OVERFLOW_JS = """(async (chunkChars, maxRounds) => {
  const s = await LanguageModel.create();
  const out = {window: s.contextWindow, overflow: 0, rounds: 0,
               usage: [], error: null, answered_after: null, anchor_kept: null};
  s.addEventListener('contextoverflow', () => { out.overflow++; });
  try {
    await s.prompt('Remember this exactly: the anchor word is OBSIDIAN.');
    const filler = 'The quick brown fox jumps over the lazy dog. '
                   .repeat(Math.ceil(chunkChars / 44));
    for (let i = 0; i < maxRounds; i++) {
      await s.append([{role: 'user', content: filler}]);
      out.rounds++;
      out.usage.push(s.contextUsage);
      if (out.overflow > 0) break;
    }
    const a = await s.prompt('What is the anchor word? Reply with one word.');
    out.answered_after = a;
    out.anchor_kept = /OBSIDIAN/i.test(a || '');
  } catch (e) { out.error = String(e).slice(0, 200); }
  try { s.destroy(); } catch (e) {}
  return out;
})(%d, %d)"""


def experiment_b(ws, chunk=12000, rounds=25):
    print("\n--- B. behaviour at context overflow ---")
    r = ws.evaluate(OVERFLOW_JS % (chunk, rounds), timeout=1800)
    print(f"  contextWindow      : {r['window']}")
    print(f"  append rounds       : {r['rounds']} x ~{chunk} chars")
    print(f"  contextoverflow ev  : {r['overflow']}")
    print(f"  final usage         : {r['usage'][-1] if r['usage'] else 'n/a'}")
    print(f"  error               : {r['error']}")
    print(f"  still answered      : {(r['answered_after'] or '')[:60]!r}")
    print(f"  earliest content kept: {r['anchor_kept']}")
    return r


# ---- C: does a greedy session hurt its neighbours? -----------------------
NORMAL_JS = """(async () => {
  const t0 = performance.now();
  const s = await LanguageModel.create();
  const st = s.promptStreaming('Explain in detail how virtual memory works.');
  let first = null;
  for await (const c of st) { if (c && c.length) { first = performance.now()-t0; break; } }
  try { s.destroy(); } catch(e) {}
  return first;
})()"""

GREEDY_JS = """(async (chunkChars, rounds) => {
  const s = await LanguageModel.create();
  const filler = 'The quick brown fox jumps over the lazy dog. '
                 .repeat(Math.ceil(chunkChars / 44));
  for (let i = 0; i < rounds; i++) {
    try { await s.append([{role:'user', content: filler}]); } catch(e) { break; }
  }
  window.__greedy = s;                 // hold it open
  return s.contextUsage;
})(%d, %d)"""


def experiment_c(conns, runs):
    print("\n--- C. does one greedy session degrade its neighbours? ---")
    out = {}
    for arm in ("control (all normal)", "one greedy neighbour"):
        vals = []
        for r in range(runs):
            if arm.startswith("one greedy"):
                try:
                    conns[-1].evaluate(GREEDY_JS % (4000, 13), timeout=1800)
                except Exception as e:                    # noqa: BLE001
                    print(f"    greedy setup failed: {str(e)[:80]}")
            res = {}
            def go(i, ws):
                try: res[i] = ws.evaluate(NORMAL_JS, timeout=900)
                except Exception: res[i] = None
            ts = [threading.Thread(target=go, args=(i, c), daemon=True)
                  for i, c in enumerate(conns[:-1])]
            for t in ts: t.start()
            for t in ts: t.join(timeout=900)
            got = [v/1000.0 for v in res.values() if v]
            if got:
                vals.append(st.median(got))
                print(f"  {arm:<22} run {r+1}: median TTFT "
                      f"{st.median(got):.2f}s over {len(got)} tabs", flush=True)
        out[arm] = vals
    for arm, vals in out.items():
        if vals:
            print(f"  {arm:<22} median {st.median(vals):.2f}s (n={len(vals)})")
    a, b = out.get("control (all normal)"), out.get("one greedy neighbour")
    if a and b:
        print(f"\n  A greedy neighbour changes everyone else's TTFT by "
              f"{st.median(b)/st.median(a):.2f}x")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", type=int, default=6)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", choices=["a", "b", "c"], default=None,
                    help="run one experiment. Chrome with Nano resident is "
                         "~4.2 GB, so on a 14 GB machine running these one at "
                         "a time is the difference between finishing and "
                         "thrashing.")
    a = ap.parse_args()
    serve(); kill_stale()
    proc = launch()
    try:
        http_json(CDP_PORT, timeout=120)
        pages = open_tabs(a.tabs - 1)
        conns = [page(p["webSocketDebuggerUrl"]) for p in pages[:a.tabs]]
        ready = []
        for i, ws in enumerate(conns):
            ok = wait_origin(ws)
            try:
                av = ws.evaluate("typeof LanguageModel==='undefined' ? 'no-api'"
                                 " : LanguageModel.availability()", timeout=120)
            except Exception as e:                        # noqa: BLE001
                av = f"err:{str(e)[:40]}"
            print(f"  tab {i}: origin_ok={ok} availability={av}")
            if av == "available":
                ready.append(ws)
        if not ready:
            print("no tab reached an available model"); return 2
        conns = ready
        print(f"  using {len(conns)} usable tab(s)")
        print(f"=== RQ2 vs Chrome: {len(conns)} tabs ===")
        if a.only in (None, "a"):
            experiment_a(conns)
        if a.only in (None, "b"):
            experiment_b(conns[0])
        if a.only in (None, "c"):
            if len(conns) < 2:
                print("\n--- C skipped: needs at least 2 usable tabs ---")
            else:
                experiment_c(conns, a.runs)
        return 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
