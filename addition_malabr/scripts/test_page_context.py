#!/usr/bin/env python3
"""Test the chat panel's page-context extraction against a realistic page.

This cannot be unit-tested in the Python suite: extraction is DOM work, and the
whole point is what it does with nav bars, sidebars and cookie banners -- none
of which exist without a browser. So the real content.js functions are pulled
out of the extension and evaluated in a real page over CDP.

Checks, in order of what actually went wrong before:
  1. the article survives and the boilerplate does not
  2. a long page is cut to the budget by RELEVANCE, not by position
  3. extraction is deterministic, so the "send the page once" cache still holds
"""
import os, re, subprocess, sys, time, http.server, socketserver, threading, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(HERE, "nano_pages")
EXT = os.path.join(HERE, "..", "mserver", "malabr_chat_extension", "content.js")
CHROME = os.environ.get("CHROME_BIN",
                        os.path.join(HERE, "..", "..", "out", "Default", "chrome"))
CDP, HTTP = 9336, 8779
PROFILE = "/tmp/malabr-pagectx-profile"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""),
          flush=True)


def extract_functions():
    """Lift the extraction helpers out of content.js verbatim.

    Deliberately not a copy kept in this file: a copy would drift, and then the
    test would be green about code nobody ships.
    """
    src = open(EXT).read()
    start = src.index("const DROP_SEL")
    end = src.index("// ---- streaming")
    body = src[start:end]
    # MAX_PAGE_CHARS lives further up the file; take its real value.
    cap = re.search(r"MAX_PAGE_CHARS\s*=\s*(\d+)", src).group(1)
    return f"const MAX_PAGE_CHARS = {cap};\n" + body


def main():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=DOCROOT, **k)
        def log_message(self, *a): pass
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", HTTP), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    args = [CHROME, "--no-sandbox", f"--user-data-dir={PROFILE}",
            f"--remote-debugging-port={CDP}", "--no-first-run",
            "--no-default-browser-check", "--headless=new",
            f"http://localhost:{HTTP}/article.html"]
    log = open("/tmp/pagectx_chrome.log", "wb")
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    try:
        page = next(t for t in http_json(CDP, timeout=90) if t["type"] == "page")
        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Runtime.enable"); ws.call("Page.enable")
        for _ in range(40):
            if "article.html" in (ws.evaluate("location.href", await_promise=False) or ""):
                break
            ws.call("Page.navigate", {"url": f"http://localhost:{HTTP}/article.html"})
            time.sleep(0.5)

        ws.evaluate(extract_functions(), await_promise=False)
        print("=== page-context extraction ===\n")

        raw = ws.evaluate("document.body.innerText.length", await_promise=False)
        art = ws.evaluate("extractArticle()", await_promise=False)
        print(f"  body innerText : {raw} chars")
        print(f"  extracted      : {len(art)} chars\n")

        # 1. the article is kept, the furniture is dropped
        check("extraction keeps the article body",
              "Paged attention is a memory management technique" in art
              and "block table" in art)
        for junk, label in (("We use cookies", "cookie banner"),
                            ("Subscribe to our newsletter", "sidebar"),
                            ("All rights reserved", "footer"),
                            ("Hiring engineers", "related links")):
            check(f"extraction drops the {label}", junk not in art,
                  "" if junk not in art else f"still contains {junk!r}")

        # 2. selection is by relevance, not by position
        sel = ws.evaluate(
            "selectRelevant(extractArticle(), 'How does eviction and preemption "
            "work when the pool is full?', 600)", await_promise=False)
        naive = art[:600]
        got = "preempt" in sel
        check("relevance selection reaches a paragraph a prefix cut would miss",
              got and "preempt" not in naive,
              f"selected {len(sel)} chars; 'preempt' in prefix-cut: "
              f"{'preempt' in naive}")
        check("selection stays inside its budget", len(sel) <= 600 + 40,
              f"{len(sel)} chars")
        check("selection keeps document order",
              sel.index("Paged attention") < sel.index("preempt")
              if ("Paged attention" in sel and "preempt" in sel) else True)

        # 3. determinism -- the once-per-session page cache depends on it
        a = ws.evaluate("extractArticle()", await_promise=False)
        b = ws.evaluate("extractArticle()", await_promise=False)
        check("extraction is deterministic (page cache stays valid)", a == b)

        print("\n  sample of what the model would receive:\n")
        for line in sel.splitlines()[:6]:
            print(f"    {line[:78]}")

        failed = [n for n, ok in RESULTS if not ok]
        print(f"\n{len(RESULTS)} checks, {len(RESULTS)-len(failed)} passed")
        if failed:
            print("FAILED: " + ", ".join(failed))
        return 1 if failed else 0
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
