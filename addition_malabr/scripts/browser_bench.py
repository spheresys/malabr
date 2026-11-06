#!/usr/bin/env python3
"""Real-browser numbers: the patched Chromium, the real panel and real app.py.

The campaign's other MALABR numbers come from the server harness. This drives
the panel over CDP, so foreground comes from the browser's real tab state.

  B1 visible tab: N tabs ask together; TTFT from click to the first token in
     each panel. The visible tab asks last or first (control).
  B2 focus switch: while 8 tabs generate, activate a tab and time the server's
     "control FOREGROUND" log line.
  B3 close: 8 tabs generate; 4 are closed once they have a first token. Then a
     new tab asks, which shows whether the closed tabs' slots came back.

Replies aren't capped to a fixed token count, so times to finish are rougher
than the harness's. A warm-up round is discarded in B1.
"""
import argparse, json, os, re, shutil, signal, statistics as st, sys, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402
import test_panel_status as tps     # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=("partitioned", "shared"), required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--calibration", required=True, help="a copy: the app may rewrite it")
ap.add_argument("--runs", type=int, default=5)
ap.add_argument("--close-runs", type=int, default=5)
a = ap.parse_args()

LOG = f"/tmp/malabr_browserbench_{a.mode}.log"
PROFILE = f"/tmp/malabr-browserbench-{a.mode}"
tps.PROFILE = PROFILE
ENV = {**tps.ENV, "MALABR_SHARED_KV": "1" if a.mode == "shared" else "0", "MALABR_N_SEQ_MAX": "8",
       "MALABR_N_THREADS": "3", "MALABR_N_CTX": "16384", "MALABR_CALIBRATION_PATH": a.calibration}
URL = tps.A
QUESTIONS = ["What is the capital of France?", "Name a primary colour.", "How many legs does a spider have?",
             "What do bees make?", "What is two plus two?", "Name a large ocean.",
             "What gas do plants take in?", "Which planet is known as the red planet?"]

ARM = """function (prompt) {
  const root = this.getRootNode(), ta = root.querySelector('textarea'), act = root.querySelector('.act');
  const n = root.querySelectorAll('.turn.ai').length;
  const b = this.__bench = {t0: performance.now(), first: null, done: null, started: false};
  const see = () => {
    const now = performance.now(), ai = root.querySelectorAll('.turn.ai');
    if (ai.length <= n) return;
    b.started = true;
    const body = ai[ai.length - 1].querySelector('.body');
    if (b.first === null && body && body.classList.contains('caret')) b.first = now;
    if (b.done === null && !act.classList.contains('stop') && (b.first !== null)) b.done = now;
  };
  if (this.__obs) this.__obs.disconnect();
  this.__obs = new MutationObserver(see);
  this.__obs.observe(root, {subtree: true, childList: true, characterData: true, attributes: true,
                            attributeFilter: ['class']});
  ta.value = prompt; act.click();
}"""
READ = "function () { const b = this.__bench || {}; const r = this.getRootNode().querySelector('.act');" \
       " return JSON.stringify({first: b.first == null ? null : b.first - b.t0," \
       " done: b.done == null ? null : b.done - b.t0, busy: r.classList.contains('stop')," \
       " text: this.innerText.length, err: /refused|no free session|at capacity|connection/i.test(this.innerText.slice(-200))}); }"
CLEAR = "function () { this.getRootNode().querySelector('.clear').click(); }"


def log_text():
    with open(LOG, errors="replace") as f:
        return f.read()


class Tab:
    def __init__(self, b, focus=False):
        self.tid = b.call("Target.createTarget", {"url": URL, "background": not focus})["targetId"]
        time.sleep(1.0)
        t = [x for x in http_json(tps.CDP) if x["id"] == self.tid][0]
        self.ws = WS(t["webSocketDebuggerUrl"])
        self.p = tps.Panel(self.ws)
        if not self.p.wait_ready(60):
            raise RuntimeError("panel not injected")
        self.obj = None

    def log_obj(self):
        nid = self.p._node(".log")
        self.obj = self.ws.call("DOM.resolveNode", {"nodeId": nid})["object"]["objectId"]
        return self.obj

    def fn(self, f, *args):
        return self.ws.call("Runtime.callFunctionOn",
                            {"objectId": self.log_obj(), "functionDeclaration": f,
                             "arguments": [{"value": x} for x in args], "returnByValue": True})["result"].get("value")

    def ask(self, prompt):
        self.fn(ARM, prompt)

    def read(self):
        return json.loads(self.fn(READ))

    def clear(self):
        self.fn(CLEAR)


def start():
    shutil.rmtree(PROFILE, ignore_errors=True)
    log = open(LOG, "w")
    env = {**os.environ, **ENV, "EXTENSION_DIR": os.path.abspath(tps.EXT),
           "USER_DATA_DIR": PROFILE, "RUN_LOG": LOG + ".runs"}
    chrome = tps.launch(log, env)
    ver = http_json(tps.CDP, "/json/version", timeout=90)
    if not tps.socket_ready(900):
        raise RuntimeError(f"server did not come up, see {LOG}")
    time.sleep(1)
    return chrome, WS(ver["webSocketDebuggerUrl"]), ver["Browser"]


def stop(chrome):
    try:
        os.kill(chrome.pid, signal.SIGTERM)
    except OSError:
        pass
    end = time.time() + 20
    while time.time() < end and tps.server_pids():
        time.sleep(0.5)
    for p in tps.server_pids():
        os.kill(p, signal.SIGKILL)
    try:
        chrome.wait(timeout=10)
    except Exception:
        chrome.kill()
    shutil.rmtree(PROFILE, ignore_errors=True)


def wait_all(tabs, limit=240):
    end = time.time() + limit
    while time.time() < end:
        rs = [t.read() for t in tabs]
        if all(r["first"] is not None and not r["busy"] for r in rs):
            return rs, False
        if all(not r["busy"] and r["err"] for r in rs):
            return rs, False
        time.sleep(0.5)
    return [t.read() for t in tabs], True


def b1(b, tabs, order, visible, nonce):
    b.call("Target.activateTarget", {"targetId": visible.tid})
    time.sleep(1.5)
    t0 = time.time()
    for i, t in enumerate(order):
        t.ask(f"[{nonce}] {QUESTIONS[i % len(QUESTIONS)]} Answer in one short sentence.")
        time.sleep(0.02)
    rs, timed_out = wait_all(order)
    wall = time.time() - t0
    ttft = {t: r["first"] / 1000 if r["first"] is not None else None for t, r in zip(order, rs)}
    allv = [v for v in ttft.values() if v is not None]
    return {"ttft_fg": ttft[visible], "ttft_median": st.median(allv) if allv else None,
            "ttft_p95": sorted(allv)[max(0, int(.95 * len(allv) + .999) - 1)] if allv else None,
            "ttft_bg_median": st.median([v for t, v in ttft.items() if t is not visible and v is not None] or [float("nan")]),
            "wall": wall, "served": len(allv), "n": len(order), "timed_out": timed_out}


def scenario_b1(b, tabs, n, last):
    order = tabs[:n]
    visible = order[-1] if last else order[0]
    rows = []
    for r in range(a.runs + 1):
        for t in order:
            t.clear()
        time.sleep(1.5)
        row = b1(b, tabs, order, visible, uuid.uuid4().hex[:8])
        print(f"  B1 tabs={n} visible={'last' if last else 'first'} round {r}: {row}", flush=True)
        if r:
            rows.append(row)
    return rows


def b2(b, tabs, nonce):
    for t in tabs:
        t.clear()
    time.sleep(1.5)
    for i, t in enumerate(tabs):
        t.ask(f"[{nonce}] Write four sentences about {QUESTIONS[i]}")
    time.sleep(2.0)
    out = []
    for i in range(10):
        t = tabs[i % len(tabs)]
        mark = len(log_text())
        t0 = time.time()
        b.call("Target.activateTarget", {"targetId": t.tid})
        got = None
        while time.time() - t0 < 5:
            if "control FOREGROUND" in log_text()[mark:]:
                got = time.time() - t0
                break
            time.sleep(0.01)
        out.append(got)
        time.sleep(0.5)
    wait_all(tabs, 120)
    return out


def b3(close):
    chrome, b, _ = start()
    try:
        tabs = [Tab(b, i == 7) for i in range(8)]
        for i, t in enumerate(tabs):
            t.ask(f"[{uuid.uuid4().hex[:8]}] Write two sentences about {QUESTIONS[i]}")
            time.sleep(0.02)
        t0 = time.time()
        closed_at, closing = None, [tabs[i] for i in (0, 2, 4, 6)] if close else []
        survivors = [t for t in tabs if t not in closing]
        while closing and time.time() - t0 < 120:
            if all(t.read()["first"] is not None for t in closing):
                closed_at = time.time() - t0
                for t in closing:
                    b.call("Target.closeTarget", {"targetId": t.tid})
                break
            time.sleep(0.2)
        rs, timed_out = wait_all(survivors, 240)
        finish = max(r["done"] for r in rs if r["done"] is not None) / 1000
        chars = sum(r["text"] for r in rs)
        new_served = new_err = None
        if close:
            nt = Tab(b, True)
            nt.ask("What is two plus two? Answer in one short sentence.")
            r, _ = wait_all([nt], 40)
            new_served, new_err = r[0]["first"] is not None, bool(r[0]["err"])
        return {"finish": finish, "closed_at": closed_at, "survivor_chars": chars, "timed_out": timed_out,
                "new_tab_served": new_served, "new_tab_error": new_err}
    finally:
        stop(chrome)


def main():
    if tps.server_pids():
        sys.exit(f"a MALABR server is already running ({tps.server_pids()}); stop it first")
    tps.serve()
    os.makedirs(a.out, exist_ok=True)
    res = {"mode": a.mode, "env": {k: v for k, v in ENV.items() if k != "MALABR_PYTHON"}}
    t00 = time.time()
    chrome, b, res["browser"] = start()
    print("browser:", res["browser"], flush=True)
    try:
        tabs = [Tab(b) for _ in range(8)]
        res["fg_events_before"] = log_text().count("control FOREGROUND")
        res["b1_8_last"] = scenario_b1(b, tabs, 8, True)
        res["b1_4_last"] = scenario_b1(b, tabs, 4, True)
        res["b1_8_first"] = scenario_b1(b, tabs, 8, False)
        res["fg_events_after"] = log_text().count("control FOREGROUND")
        res["b2_switch_seconds"] = b2(b, tabs, uuid.uuid4().hex[:8])
        print("  B2", res["b2_switch_seconds"], flush=True)
    finally:
        stop(chrome)
    res["b3_close"], res["b3_control"] = [], []
    for r in range(a.close_runs):
        for arm, close in (("b3_close", True), ("b3_control", False)):
            row = b3(close)
            res[arm].append(row)
            print(f"  B3 {arm} {r}: {row}", flush=True)
    res["seconds"] = time.time() - t00
    with open(os.path.join(a.out, f"browser_{a.mode}.json"), "w") as f:
        json.dump(res, f, indent=1)
    print("wrote", a.mode, flush=True)


if __name__ == "__main__":
    main()
