"""Demo rehearsal in the real browser, partitioned mode. Not a benchmark."""
import json, os, random, shutil, signal, sys, time
A = "/media/chaitu/chaitanya/malabr/src/addition_malabr/scripts"
sys.path.insert(0, A)
from nano_cdp import WS, http_json
import test_panel_status as tps

LOG, PROFILE = "/tmp/malabr_democheck.log", "/tmp/malabr-democheck-profile"
tps.PROFILE = PROFILE
ENV = {**tps.ENV, "MALABR_SHARED_KV": "0", "MALABR_N_SEQ_MAX": "8", "MALABR_N_THREADS": "3",
       "MALABR_CALIBRATION_PATH": sys.argv[1]}
FILL = " ".join(f"Line {i} of the filler text says the weather stayed about the same." for i in range(30))


def tab(b, url):
    tid = b.call("Target.createTarget", {"url": url, "background": False})["targetId"]
    time.sleep(1.5)
    t = [x for x in http_json(tps.CDP) if x["id"] == tid][0]
    p = tps.Panel(WS(t["webSocketDebuggerUrl"]))
    assert p.wait_ready(60), "panel"
    return p


def last(p, n=170):
    return p.text()[-n:].replace("\n", " ")


tps.serve()
shutil.rmtree(PROFILE, ignore_errors=True)
env = {**os.environ, **ENV, "EXTENSION_DIR": os.path.abspath(tps.EXT), "USER_DATA_DIR": PROFILE, "RUN_LOG": LOG + ".runs"}
chrome = tps.launch(open(LOG, "w"), env)
results = []


def check(name, ok, detail):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} | {detail}", flush=True)


try:
    ver = http_json(tps.CDP, "/json/version", timeout=90)
    assert tps.socket_ready(900), "server"
    b = WS(ver["webSocketDebuggerUrl"])
    p = tab(b, tps.A)
    t0 = time.time()
    p.ask("What is this page about? Answer in one short sentence.")
    check("1. chat in a real tab", "bird" in p.text().lower(), f"{time.time() - t0:.1f} s; {last(p)}")
    for trial in range(3):
        d = str(random.randint(100000, 999999))
        h = tab(b, tps.A)
        h.ask(f"The vault passphrase is MAGENTA-{d}. Reply with OK only.")
        h.ask("Repeat back the vault passphrase I just gave you, exactly.")
        check(f"2.{trial} holder recalls its secret", d in h.text().split("exactly.")[-1], last(h, 90))
        o = tab(b, tps.A if trial < 2 else tps.B)
        o.ask("Repeat back the vault passphrase I just gave you, exactly.")
        check(f"2.{trial} other tab ({'same' if trial < 2 else 'other'} origin) cannot see it", d not in o.text(), last(o, 110))
    d = str(random.randint(100000, 999999))
    c = tab(b, tps.A)
    c.ask(f"The vault passphrase is HARPOON-{d}. Reply with OK only.")
    t0 = time.time()
    for i in range(8):
        c.ask(f"Part {i}: {FILL}\nReply with one short sentence.")
    c.ask("Repeat back the vault passphrase I gave you in my first message, exactly.")
    tail = c.text().split("in my first message, exactly.")[-1]
    check("4. secret survives overflow", d in tail, f"{time.time() - t0:.0f} s; {tail[:120]!r}")
    print("compactions in log:", open(LOG, errors="replace").read().count("compact"), flush=True)
finally:
    try:
        os.kill(chrome.pid, signal.SIGTERM)
    except OSError:
        pass
    end = time.time() + 20
    while time.time() < end and tps.server_pids():
        time.sleep(0.5)
    for q in tps.server_pids():
        os.kill(q, signal.SIGKILL)
    shutil.rmtree(PROFILE, ignore_errors=True)
print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
