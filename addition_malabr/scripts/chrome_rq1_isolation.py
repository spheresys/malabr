#!/usr/bin/env python3
"""RQ1 against Chrome: is a session's context reachable from another origin?

MALABR's answer is the §12 canary suite. This applies the SAME method to
Chrome's built-in Prompt API, because RQ1 is a claim about a shared model
process and Chrome runs one too.

Three cases, in increasing order of interest:

  1. cross-origin, both live   -- plant a canary in a session on origin A,
                                  then ask for it from a session on origin B.
                                  http://localhost:P and http://127.0.0.1:P are
                                  DIFFERENT origins by definition, and both are
                                  secure contexts, so this is a real
                                  cross-origin test rather than a same-site one.
  2. after destroy()           -- plant, destroy the session, create a fresh
                                  one in the SAME page, then ask.
  3. same-origin, second tab   -- plant in one tab, ask from another tab on the
                                  same origin.

A leak is the model reproducing the canary string. Failure to reproduce is NOT
proof of isolation on its own -- a small model may simply not recall -- so the
run also verifies the canary IS recallable within its own session, which is the
positive control that makes a negative result meaningful.
"""
import json, os, random, re, subprocess, sys, threading, time
import http.server, socketserver

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
CHROME_DIR = os.environ.get(
    "CHROME_DIR", "/media/chaitu/chaitanya/chrome-stable-deb/root/opt/google/chrome")
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/nano-cpu-profile")
CDP_PORT, HTTP_PORT = 9366, 8816
FEATURES = ",".join([
    "AIPromptAPI", "OptimizationGuideModelExecution",
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])
# A fresh secret every run; detection is on its six random DIGITS, which a
# model asked for a passphrase it never saw may invent a word for, but not.
DIGITS = f"{random.SystemRandom().randrange(10 ** 6):06d}"
CANARY = f"The vault passphrase is MAGENTA-{DIGITS}."
# ONE question, asked of the holder (positive control) and of every other
# session (the leak tests) alike. An earlier version used a guarded form for
# the leak tests ("...If you were not told one, reply exactly UNKNOWN."). The
# UNKNOWN escape hatch is taken even when the answer IS in context: this
# test's first version failed its own control that way, and on MALABR's model
# the guarded form let the method miss 19 of 20 real leaks
# (mserver/tests/negative_control.py). So the leak tests ask directly too.
ASK_DIRECT = "Repeat back the vault passphrase I just gave you, exactly."
ASK = ASK_DIRECT

PLANT = """(async () => {
  window.__s = await LanguageModel.create();
  await window.__s.prompt(%s);
  return 'planted';
})()"""
RECALL_SAME = """(async () => window.__s.prompt(%s))()"""
NEW_SESSION_ASK = """(async () => {
  try { window.__s.destroy(); } catch (e) {}
  window.__s2 = await LanguageModel.create();
  return await window.__s2.prompt(%s);
})()"""
FRESH_ASK = """(async () => {
  const s = await LanguageModel.create();
  const r = await s.prompt(%s);
  try { s.destroy(); } catch (e) {}
  return r;
})()"""

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def leaked(text):
    squashed = re.sub(r"(?<=\d)[\s,.\-](?=\d)", "", text or "")
    return DIGITS in squashed


def main():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(
        target=socketserver.TCPServer(("0.0.0.0", HTTP_PORT), H).serve_forever,
        daemon=True).start()

    proc = subprocess.Popen(
        [os.path.join(CHROME_DIR, "chrome"), "--disable-gpu",
         "--disable-software-rasterizer", f"--user-data-dir={PROFILE}",
         f"--remote-debugging-port={CDP_PORT}", f"--enable-features={FEATURES}",
         "--no-first-run", "--no-default-browser-check",
         f"http://localhost:{HTTP_PORT}/"],
        stdout=open("/tmp/chrome_rq1.log", "wb"), stderr=subprocess.STDOUT)
    try:
        http_json(CDP_PORT, timeout=120)
        origins = [f"http://localhost:{HTTP_PORT}/",      # origin A
                   f"http://127.0.0.1:{HTTP_PORT}/",      # origin B -- different
                   f"http://localhost:{HTTP_PORT}/"]      # origin A, second tab
        for u in origins[1:]:
            subprocess.run(["curl", "-s", "-X", "PUT",
                            f"http://127.0.0.1:{CDP_PORT}/json/new?{u}"],
                           capture_output=True)
        time.sleep(3)
        pages = [t for t in http_json(CDP_PORT) if t["type"] == "page"
                 and str(HTTP_PORT) in t.get("url", "")]
        by_origin = {}
        for p in pages:
            by_origin.setdefault(p["url"], p)
        if len(by_origin) < 2:
            print("could not open two distinct origins:", list(by_origin))
            return 1

        a_url = f"http://localhost:{HTTP_PORT}/"
        b_url = f"http://127.0.0.1:{HTTP_PORT}/"
        wa = WS(by_origin[a_url]["webSocketDebuggerUrl"]); wa.call("Runtime.enable")
        wb = WS(by_origin[b_url]["webSocketDebuggerUrl"]); wb.call("Runtime.enable")
        same = [p for p in pages if p["url"] == a_url]
        wa2 = WS(same[1]["webSocketDebuggerUrl"]) if len(same) > 1 else None
        if wa2: wa2.call("Runtime.enable")

        if wa.evaluate("typeof LanguageModel==='undefined' ? 'no-api'"
                       " : LanguageModel.availability()", timeout=120) != "available":
            print("model not available"); return 2

        print("=== RQ1: Chrome Prompt API, cross-session canary ===")
        print(f"  secret = {CANARY!r} (fresh this run; detected on {DIGITS})")
        print(f"  question (control and leak tests) = {ASK!r}")
        print(f"  origin A = {a_url}\n  origin B = {b_url}\n")

        print(" ", wa.evaluate(PLANT % json.dumps(f"Remember this exactly: {CANARY}"),
                               timeout=600))

        # Positive control FIRST: if the canary is not recallable in its own
        # session, every negative below is meaningless.
        own = wa.evaluate(RECALL_SAME % json.dumps(ASK_DIRECT), timeout=600)
        check("positive control: canary IS recallable in its own session",
              leaked(own), f"A said: {(own or '')[:70]!r}")
        ctx = wa.evaluate(RECALL_SAME % json.dumps(
            "In one short sentence, what did I ask you to remember?"), timeout=600)
        check("control: the session retains ANY prior turn at all",
              bool(ctx) and "UNKNOWN" not in (ctx or "").upper(),
              f"A said: {(ctx or '')[:70]!r}")

        out_b = wb.evaluate(FRESH_ASK % json.dumps(ASK), timeout=600)
        check("cross-ORIGIN: origin B cannot reach origin A's canary",
              not leaked(out_b), f"B said: {(out_b or '')[:70]!r}")

        if wa2:
            out_a2 = wa2.evaluate(FRESH_ASK % json.dumps(ASK), timeout=600)
            check("same-origin, different tab: cannot reach the other's canary",
                  not leaked(out_a2), f"A2 said: {(out_a2 or '')[:70]!r}")

        out_new = wa.evaluate(NEW_SESSION_ASK % json.dumps(ASK), timeout=600)
        check("after destroy(): a new session in the SAME page sees nothing",
              not leaked(out_new), f"A-new said: {(out_new or '')[:70]!r}")

        failed = [n for n, ok in RESULTS if not ok]
        print(f"\n{len(RESULTS)} checks, {len(RESULTS)-len(failed)} passed")
        for n in failed:
            print("  FAILED:", n)
        return 1 if failed else 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=15)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
