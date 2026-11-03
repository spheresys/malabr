"""RQ2: does the server's memory stay flat as sessions are added?

The claim to check: server RSS stays flat (~1,139 MB) from 1 to 8 sessions,
because the KV cache is one fixed n_ctx allocation made at startup.

Starts the real server (app.py) on a private socket, then reads VmRSS:
  phase 1  after each of 8 sessions has had one short chat turn
  phase 2  after each session has also been sent a large page (~1,500
           tokens) -- a fixed allocation is only flat in RSS if the pages
           behind it are touched up front, so the claim needs this too

The server is started by this script, not a browser, so it runs with the
peer-check opt-out (it prints its warning banner).

  python tests/rss_check.py --mode shared|partitioned [--json out.json]
"""

import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "/home/chaitu/Desktop/vscode/malabr/bin/python"
PAGE = " ".join(f"{i}. The committee reviewed the quarterly figures and noted a steady "
                f"rise in demand across the regional offices." for i in range(1, 76))


def rss_mb(pid):
    with open(f"/proc/{pid}/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return float("nan")


def generate(sock, tab, prompt, timeout=600):
    body = prompt.encode()
    hdr = (f"ROUTE_MALABR_GENERATE_API,{'a' * 32},{tab},https://t{tab}.test,"
           f"background,{len(body)}").encode()
    c = socket.socket(socket.AF_UNIX)
    c.settimeout(timeout)
    c.connect(sock)
    c.sendall(struct.pack(">I", len(hdr)) + hdr + body)
    buf, err = b"", None
    while True:
        chunk = c.recv(65536)
        if not chunk:
            break
        buf += chunk
        done = False
        off = 0
        while off + 5 <= len(buf):
            ft, ln = struct.unpack(">BI", buf[off:off + 5])
            if off + 5 + ln > len(buf):
                break
            if ft in (1, 2):
                done = True
                if ft == 2:
                    err = buf[off + 5:off + 5 + ln].decode(errors="replace")
            off += 5 + ln
        if done:
            break
    c.close()
    return err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("partitioned", "shared"), required=True)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    d = tempfile.mkdtemp()
    sock = os.path.join(d, "m.sck")
    log_path = os.path.join(d, "server.log")
    env = {**os.environ, "MALABR_SOCKET_PATH": sock, "MALABR_TRUST_ANY_PEER": "1",
           "MALABR_SHARED_KV": "1" if a.mode == "shared" else "0"}
    log = open(log_path, "w")
    srv = subprocess.Popen([PY, "-u", "app.py"], cwd=HERE, env=env, stdout=log,
                           stderr=subprocess.STDOUT)
    rows = []
    try:
        for _ in range(300):
            if "listening on" in open(log_path, errors="replace").read():
                break
            if srv.poll() is not None:
                sys.exit("server exited: " + open(log_path).read()[-500:])
            time.sleep(1)
        time.sleep(1.0)
        cfg = re.search(r"malabr: model=.*", open(log_path, errors="replace").read())
        print(cfg.group(0) if cfg else "(no config line)")
        rows.append({"phase": "idle", "sessions": 0, "rss_mb": rss_mb(srv.pid)})
        for n in range(1, 9):
            err = generate(sock, n, "Say hello in one short sentence.")
            rows.append({"phase": "short chat", "sessions": n, "rss_mb": rss_mb(srv.pid),
                         "error": err})
        for n in range(1, 9):
            err = generate(sock, n, f"Here is a page:\n\n{PAGE}\n\nSummarise it in one sentence.")
            rows.append({"phase": "page filled", "sessions": n, "rss_mb": rss_mb(srv.pid),
                         "error": err})
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=30)
        except subprocess.TimeoutExpired:
            srv.kill()
    print(f"=== server RSS, {a.mode} KV ===")
    for r in rows:
        print(f"  {r['phase']:<12} sessions={r['sessions']}  RSS {r['rss_mb']:8.1f} MB"
              + (f"  error: {r['error'][:50]}" if r.get("error") else ""))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"mode": a.mode, "config": cfg.group(0) if cfg else None,
                       "rows": rows}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
