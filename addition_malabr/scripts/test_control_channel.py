#!/usr/bin/env python3
"""§12 test 80, run by hand: the browser's control channel reaches the server.

Every lifecycle and priority result that depends on a browser event (tab
close, foreground, LIVE_TABS resync, extension unload) travels over this one
connection from malabr_manager.cc. Until 2026-11-03 the server refused its
opening header (5 fields; the parser wanted 6), so none of those events ever
arrived -- and nothing noticed, because the §12 suite injects control messages
on the server side. This test drives the real browser instead.

Needs the whole stack, like test_panel_status.py (whose Panel and launch
helpers it reuses). The server runs with TWO session slots, so a freed slot is
visible from outside:

  0. the control connection is established (no "connection error" in the log)
  1. foreground: activating tab A, then tab B, makes the server log
     "control FOREGROUND -> tab <id>" with each tab's own id (the ids come
     from the server's "generate ... tab=<id>" lines)
  2. tab close: A and B hold both slots, so C is refused ("no free session
     slots"); after A's tab is closed, C is served

Expected today: 0, 1 and 2a pass; 2b FAILS until rebuild item 9, because the
browser's TAB_CLOSED names the closed tab's "recently closed" history entry,
not the tab (seen: closing tab 1061986978 sent TAB_CLOSED,1061986981).
"""
import os, re, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402
import test_panel_status as tps  # noqa: E402

LOG = "/tmp/malabr_controlchannel.log"
PROFILE = "/tmp/malabr-controlchannel-profile"
tps.PROFILE = PROFILE
ENV = {**tps.ENV, "MALABR_N_SEQ_MAX": "2"}
URL = tps.A
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def log_text():
    with open(LOG, errors="replace") as f:
        return f.read()


def new_tab(b):
    tid = b.call("Target.createTarget", {"url": URL})["targetId"]
    time.sleep(2.0)
    t = [x for x in http_json(tps.CDP) if x["id"] == tid][0]
    p = tps.Panel(WS(t["webSocketDebuggerUrl"]))
    return tid, p


def chat(p, prompt):
    """Ask in one tab; return the server-side tab id it was keyed under, and
    the server log written during the request."""
    mark = len(log_text())
    p.ask(prompt)
    new = log_text()[mark:]
    ids = re.findall(r"malabr: generate ext=\w+ tab=(-?\d+)", new)
    return (int(ids[-1]) if ids else None), new


def foreground_after(b, tid, mark, timeout=10):
    b.call("Target.activateTarget", {"targetId": tid})
    end = time.time() + timeout
    while time.time() < end:
        seen = re.findall(r"control FOREGROUND -> tab (-?\d+)", log_text()[mark:])
        if seen:
            return [int(x) for x in seen]
        time.sleep(0.5)
    return []


def main():
    if tps.server_pids():
        sys.exit(f"a MALABR server is already running ({tps.server_pids()}); stop it first")
    shutil.rmtree(PROFILE, ignore_errors=True)
    tps.serve()
    log = open(LOG, "w")
    env = {**os.environ, **ENV, "EXTENSION_DIR": os.path.abspath(tps.EXT),
           "USER_DATA_DIR": PROFILE, "RUN_LOG": LOG + ".runs"}
    chrome = tps.launch(log, env)
    try:
        ver = http_json(tps.CDP, "/json/version", timeout=60)
        print("browser:", ver["Browser"])
        if not tps.socket_ready(240):
            check("model server came up", False, f"see {LOG}")
            return 1
        b = WS(ver["webSocketDebuggerUrl"])
        tid_a, pa = new_tab(b)
        if not pa.wait_ready(60):
            check("panel injected", False)
            return 1
        id_a, _ = chat(pa, "Reply with the single word ALPHA.")
        tid_b, pb = new_tab(b)
        pb.wait_ready(60)
        id_b, _ = chat(pb, "Reply with the single word BRAVO.")

        # 0. The control connection exists. The browser opens it lazily, on
        # its first control event, so by now (two tabs created) it has tried.
        text = log_text()
        refused = re.findall(r"malabr: connection error: (.*)", text)
        check("0. control connection established",
              "malabr: control connection established" in text and not refused,
              f"refused: {refused[:2]}" if refused else "")

        # 1. Foreground follows the active tab.
        mark = len(log_text())
        fg_a = foreground_after(b, tid_a, mark)
        mark = len(log_text())
        fg_b = foreground_after(b, tid_b, mark)
        check("1. activating a tab moves the server's foreground to it",
              id_a is not None and id_b is not None and id_a != id_b
              and fg_a[-1:] == [id_a] and fg_b[-1:] == [id_b],
              f"tab ids A={id_a} B={id_b}; FOREGROUND after A: {fg_a}, after B: {fg_b}")

        # 2. Closing a tab frees its slot.
        tid_c, pc = new_tab(b)
        pc.wait_ready(60)
        # A served request ends with the engine's "turn done"; a request
        # refused for lack of a slot never reaches the engine.
        _, new = chat(pc, "Reply with the single word CHARLIE.")
        full = "turn done" not in new and "no free session slots" in pc.text()
        b.call("Target.closeTarget", {"targetId": tid_a})
        time.sleep(3.0)
        id_c, new = chat(pc, "Reply with the single word DELTA.")
        served = "-> session CREATED" in new and "turn done" in new
        check("2a. with both slots held, a third tab is refused", full,
              repr(pc.text()[-100:]))
        check("2b. closing a tab frees its slot: the third tab is served",
              full and served, f"tab C id={id_c}; server: {new.strip()[-160:]!r}")
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        end = time.time() + 20
        while tps.server_pids() and time.time() < end:
            time.sleep(0.5)
        shutil.rmtree(PROFILE, ignore_errors=True)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed   (log: {LOG})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
