#!/usr/bin/env python3
"""Page extraction under pathological DOMs -- audit 3.

`test_page_context.py` covers the page extraction was DESIGNED for: an article
surrounded by furniture. This covers the pages it was not, because the failure
mode that matters is not "picks slightly the wrong div", it is "returns nothing,
returns the whole DOM, or takes so long the panel janks".

Each case asserts three things: it returns SOMETHING when there is text to
return, it never exceeds the budget, and it completes fast enough to run on a
keystroke path.
"""
import os, re, subprocess, sys, time, http.server, socketserver, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
EXT = os.path.join(HERE, "..", "mserver", "malabr_chat_extension", "content.js")
CHROME = os.environ.get("CHROME_BIN",
                        os.path.join(HERE, "..", "..", "out", "Default", "chrome"))
CDP, HTTP = 9350, 8801
PROFILE = "/tmp/malabr-patho-profile"
BUDGET_SLACK = 64          # the "[...]" joiners may push a little past the cap

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def functions():
    src = open(EXT).read()
    body = src[src.index("const DROP_SEL"):src.index("// ---- streaming")]
    cap = re.search(r"MAX_PAGE_CHARS\s*=\s*(\d+)", src).group(1)
    return f"const MAX_PAGE_CHARS = {cap};\n" + body, int(cap)


CASES = [
    ("patho_noblocks.html", "no block elements at all", True),
    ("patho_onep.html",     "one enormous paragraph", True),
    ("patho_tables.html",   "deeply nested tables", True),
    ("patho_big.html",      "1500 paragraphs + 200 nav links", True),
    ("patho_empty.html",    "empty body", False),   # nothing to return is correct
]


def main():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", HTTP), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    args = [CHROME, "--no-sandbox", f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={CDP}", "--no-first-run",
            "--no-default-browser-check", "--headless=new", "about:blank"]
    proc = subprocess.Popen(args, stdout=open("/tmp/patho_chrome.log", "wb"),
                            stderr=subprocess.STDOUT)
    try:
        page = next(t for t in http_json(CDP, timeout=90) if t["type"] == "page")
        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Runtime.enable"); ws.call("Page.enable")
        js, cap = functions()
        print(f"=== page extraction, pathological DOMs (budget {cap}) ===\n")

        for fn, label, expect_text in CASES:
            url = f"http://localhost:{HTTP}/{fn}"
            ws.call("Page.navigate", {"url": url})
            for _ in range(60):
                if ws.evaluate("location.href", await_promise=False) == url:
                    break
                time.sleep(0.25)
            time.sleep(0.4)
            ws.evaluate(js, await_promise=False)

            t0 = time.time()
            try:
                out = ws.evaluate(
                    "pageText('How does eviction and preemption work?')",
                    await_promise=False, timeout=60)
            except Exception as e:                       # noqa: BLE001
                check(f"{label}: extraction completes", False, str(e)[:80])
                continue
            ms = (time.time() - t0) * 1000
            out = out or ""

            if expect_text:
                check(f"{label}: returns text", len(out) > 0,
                      f"{len(out)} chars in {ms:.0f}ms")
            else:
                check(f"{label}: returns nothing rather than junk", out.strip() == "",
                      f"{len(out)} chars")
            check(f"{label}: inside budget", len(out) <= cap + BUDGET_SLACK,
                  f"{len(out)} / {cap}")
            # This runs synchronously on the send path, so it must not jank.
            check(f"{label}: fast enough for the send path", ms < 250,
                  f"{ms:.0f}ms")

        failed = [n for n, ok in RESULTS if not ok]
        print(f"\n{len(RESULTS)} checks, {len(RESULTS)-len(failed)} passed")
        if failed:
            print("FAILED:")
            for n in failed:
                print(f"  - {n}")
        return 1 if failed else 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
