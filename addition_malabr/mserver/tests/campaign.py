#!/usr/bin/env python3
"""Benchmark campaign driver: the same workloads and metrics on every system.

  python tests/campaign.py --system malabr   --mode partitioned --workload W1 --out DIR
  python tests/campaign.py --system llamacpp --mode shared      --workload W2 --out DIR
  python tests/campaign.py --system ollama                      --workload W4 --out DIR

Run from a code tree: `malabr` measures the engine in THIS tree, so the same
script copied next to an older engine measures that engine.

Workloads (mserver/BENCHMARKS_2026-11.md gives what each can and can't show):
  W1  8 tabs short chat, visible last; 4 tabs; control with visible first
  W2  8 tabs generating, 4 closed mid-answer; control closing none
  W3  1 or 2 greedy tabs load pages while 3 small tabs chat; production, small pool
  W4  secret in the first turn, fill past the limit, ask; positive control; n=20
  W5  RSS with 0, 1, 2, 4, 6, 8 sessions, each holding a page
  R1  isolation: secret planted in one session, asked for from another; n=20

Servers have no tabs: "close" cancels the request and there is no visible tab.
Each run starts with a fresh nonce, so a prompt cache can't reuse an earlier run.
"""
import argparse
import http.client
import json
import math
import os
import random
import socket
import statistics as st
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import llama_cpp.llama_cpp as C                                  # noqa: E402

from harness import (LEAK_QUESTION, MODEL_PATH, Fixture, fresh_secret,  # noqa: E402
                     recalls_canary)
from malabr_service import engine as eng                         # noqa: E402
from pipeline_bench import PROMPTS, summarise                    # noqa: E402

N_CTX, N_SEQ, THREADS = 16384, 8, 3
ARRIVAL_GAP = 0.02
TOOLS = os.path.expanduser("~/malabr_bench_tools")
LLAMA_SERVER = os.path.join(TOOLS, "llama/llama-b12078/llama-server")
OLLAMA = os.path.join(TOOLS, "ollama/bin/ollama")
LLAMA_PORT, OLLAMA_PORT = 8955, 11555
OLLAMA_MODEL = "qwen3-bench"
EXT, TURN_TIMEOUT, GREEDY_TIMEOUT = "a" * 32, 120.0, 600.0
REFUSED_MARKS = ("[refused]", "exceed", "too long", "too large", "no free session",
                 "prompt is too")


def key(tab):
    return (EXT, tab, f"https://tab{tab}.test")


def is_refusal(err):
    return bool(err) and any(m in err.lower() for m in REFUSED_MARKS)


def describe(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return None
    return {"median": st.median(xs), "min": min(xs), "max": max(xs),
            "sd": st.stdev(xs) if len(xs) > 1 else 0.0, "n": len(xs)}


# ---- systems --------------------------------------------------------------

class Malabr:
    """The engine in this tree, with a calibration measured for its configuration."""

    has_visibility = True

    def __init__(self, mode, n_ctx=N_CTX, guard="on"):
        self.shared, self.n_ctx, self.guard = mode == "shared", n_ctx, guard
        self.fix = Fixture(n_seq_max=N_SEQ, n_ctx=n_ctx, n_threads=THREADS,
                           kv_unified=self.shared)
        self.cal, path = self.fix.use_measured_calibration(self.shared)
        self.budget = n_ctx // 2 if self.shared else n_ctx // N_SEQ
        self.limit = self.budget
        self.info = {"system": "malabr", "mode": "shared" if self.shared else "partitioned",
                     "kv_unified": self.fix.kv_unified, "n_ctx": n_ctx, "n_seq_max": N_SEQ,
                     "threads": THREADS, "budget_per_session": self.budget, "guard": guard,
                     "calibration_key": self.cal.get("key"), "calibration_file": path,
                     "decode_curve": self.cal.get("decode_curve")}
        self.e = None

    def begin_run(self):
        _, self.e = self.fix.new_engine(shared_kv=self.shared, session_budget=self.budget)
        self.events, self.round_errors, self.closed_at, self.after_close = [], [], {}, {}
        real_compact, real_step = self.e.compact, self.e._step

        def compact_hook(s, keep_recent=True, **kw):
            freed = real_compact(s, keep_recent, **kw)
            if freed:
                self.events.append({"tab": s.key[1], "freed": freed, "after": s.pos})
            return freed

        def step_hook():
            try:
                return real_step()
            except Exception as exc:
                self.round_errors.append(f"{type(exc).__name__}: {exc}")
                raise
        self.e.compact, self.e._step = compact_hook, step_hook
        if self.guard == "off":
            self.e._relieve_aggregate_pressure = lambda sessions: None
        self.e.control_connected = True
        self.e.foreground_tab_id = -1
        self.e.start()

    def end_run(self):
        self.e.stop()
        C.llama_memory_clear(self.fix.mem, True)
        extra = {"compactions": self.events, "round_errors": len(self.round_errors),
                 "round_error_samples": self.round_errors[:3]}
        self.e = None
        return extra

    def set_visible(self, tab):
        self.e.foreground_tab_id = tab

    def send(self, tab, text, cap, on_token=None, timeout=TURN_TIMEOUT):
        k = key(tab)
        s, _ = self.e.get_or_create(k, self.fix.formatter, eng.OutputCap([(0, cap)]))
        if s is None:
            return {"tokens": 0, "error": "[refused] no free session slots", "text": ""}
        s.output_cap = eng.OutputCap([(0, cap)])
        ob = self.e.submit(k, text)
        if ob is None:
            return {"tokens": 0, "error": "session unavailable", "text": ""}
        n, parts, deadline = 0, [], time.time() + timeout
        while True:
            try:
                ft, payload = ob.get(timeout=max(0.01, deadline - time.time()))
            except Exception:
                return {"tokens": n, "error": "timeout", "text": "".join(parts)}
            if ft == eng.FRAME_TOKEN:
                if payload.startswith("\x00MALABR"):
                    continue
                n += 1
                parts.append(payload)
                if tab in self.closed_at:
                    self.after_close[tab] = self.after_close.get(tab, 0) + 1
                if on_token:
                    on_token()
            elif ft == eng.FRAME_COMPLETE:
                return {"tokens": n, "error": None, "text": "".join(parts)}
            else:
                return {"tokens": n, "error": payload, "text": "".join(parts)}

    def close_tab(self, tab):
        self.closed_at[tab] = time.perf_counter()
        self.e.cancel(key(tab), "tab closed")

    def wasted_after_close(self, delivered):
        return sum(self.after_close.values())

    def shutdown(self):
        self.fix.close()


class _Http:
    """A server over HTTP. It holds no conversation for us: each turn resends the
    history, as a real client does, and its prompt cache avoids re-decoding it."""

    has_visibility = False

    def __init__(self):
        self.lock = threading.Lock()

    def begin_run(self):
        self.hist, self.conns, self.closed, self.baseline = {}, {}, set(), self.predicted_total()

    def end_run(self):
        return {}

    def set_visible(self, tab):
        pass

    def predicted_total(self):
        return None

    def wasted_after_close(self, delivered):
        return None

    def post_stream(self, tab, path, body, parse, on_token, timeout=TURN_TIMEOUT):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        with self.lock:
            self.conns[tab] = c
        n, parts, err = 0, [], None
        try:
            c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            r = c.getresponse()
            if r.status != 200:
                return {"tokens": 0, "error": f"HTTP {r.status}: {r.read()[:300].decode(errors='replace')}",
                        "text": ""}
            for raw in r:
                piece, e = parse(raw)
                if e:
                    err = e
                    break
                if piece:
                    n += 1
                    parts.append(piece)
                    if on_token:
                        on_token()
        except socket.timeout:
            err = "timeout"
        except (OSError, http.client.HTTPException) as exc:
            err = "closed" if tab in self.closed else f"{type(exc).__name__}: {exc}"
        finally:
            c.close()
        return {"tokens": n, "error": err, "text": "".join(parts)}

    def send(self, tab, text, cap, on_token=None, timeout=TURN_TIMEOUT):
        msgs = self.hist.setdefault(tab, [])
        res = self.request(tab, msgs + [{"role": "user", "content": text}], cap, on_token, timeout)
        if not res["error"]:
            msgs += [{"role": "user", "content": text},
                     {"role": "assistant", "content": res["text"]}]
        return res

    def close_tab(self, tab):
        self.closed.add(tab)
        with self.lock:
            c = self.conns.get(tab)
        try:
            if c is not None and c.sock is not None:
                c.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def rss_kb(self):
        return sum(rss_of(p) for p in self.pids())

    def shutdown(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()


class LlamaServer(_Http):
    def __init__(self, mode, n_ctx=N_CTX):
        super().__init__()
        self.port, self.shared, self.n_ctx = LLAMA_PORT, mode == "shared", n_ctx
        self.limit = n_ctx if self.shared else n_ctx // N_SEQ
        self.log = tempfile.NamedTemporaryFile("w", suffix=".llama.log", delete=False)
        args = [LLAMA_SERVER, "-m", MODEL_PATH, "-c", str(n_ctx), "-np", str(N_SEQ),
                "-t", str(THREADS), "-tb", str(THREADS), "-b", "512", "--host", "127.0.0.1",
                "--port", str(self.port), "--metrics", "--no-webui",
                "--kv-unified" if self.shared else "--no-kv-unified"]
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(LLAMA_SERVER)}
        self.procs = [subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT, env=env)]
        end = time.time() + 300
        while time.time() < end:
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f"llama-server did not come up; see {self.log.name}")
        ver = subprocess.run([LLAMA_SERVER, "--version"], capture_output=True, text=True, env=env)
        self.info = {"system": "llama.cpp server", "mode": "--kv-unified" if self.shared else "--no-kv-unified",
                     "n_ctx": n_ctx, "parallel": N_SEQ, "threads": THREADS,
                     "limit_per_session": self.limit, "args": args[1:],
                     "version": (ver.stdout + ver.stderr).strip().splitlines()[0],
                     "log": self.log.name}

    def pids(self):
        return [self.procs[0].pid]

    def predicted_total(self):
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("GET", "/metrics")
            for line in c.getresponse().read().decode().splitlines():
                if line.startswith("llamacpp:tokens_predicted_total"):
                    return int(float(line.split()[1]))
        except (OSError, ValueError):
            pass
        return None

    def wasted_after_close(self, delivered):
        time.sleep(1.0)
        now = self.predicted_total()
        return None if now is None or self.baseline is None else max(0, now - self.baseline - delivered)

    @staticmethod
    def _parse(raw):
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            return None, None
        data = line[5:].strip()
        if data == "[DONE]":
            return None, None
        d = json.loads(data)
        if "error" in d:
            return None, str(d["error"])[:300]
        ch = d.get("choices") or [{}]
        return ch[0].get("delta", {}).get("content"), None

    def request(self, tab, messages, cap, on_token, timeout):
        return self.post_stream(tab, "/v1/chat/completions", {
            "messages": messages, "stream": True, "max_tokens": cap, "temperature": 0,
            "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False}},
            self._parse, on_token, timeout)


class Ollama(_Http):
    def __init__(self, n_ctx=N_CTX):
        super().__init__()
        self.port, self.n_ctx = OLLAMA_PORT, n_ctx
        self.limit = self.num_ctx = n_ctx // N_SEQ
        self.log = tempfile.NamedTemporaryFile("w", suffix=".ollama.log", delete=False)
        self.env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{self.port}",
                    "OLLAMA_MODELS": os.path.join(TOOLS, "ollama/models"),
                    "OLLAMA_NUM_PARALLEL": str(N_SEQ), "OLLAMA_KEEP_ALIVE": "-1",
                    "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_LLM_LIBRARY": "cpu"}
        self.procs = [subprocess.Popen([OLLAMA, "serve"], stdout=self.log,
                                       stderr=subprocess.STDOUT, env=self.env)]
        end = time.time() + 60
        while time.time() < end:
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                c.request("GET", "/api/version")
                ver = json.loads(c.getresponse().read())["version"]
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"ollama did not come up; see {self.log.name}")
        mf = tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False)
        mf.write(f"FROM {MODEL_PATH}\n")
        mf.close()
        subprocess.run([OLLAMA, "create", OLLAMA_MODEL, "-f", mf.name], env=self.env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        self.info = {"system": "ollama", "mode": "fixed (no unified option)", "n_ctx": n_ctx,
                     "parallel": N_SEQ, "num_ctx_per_session": self.num_ctx,
                     "limit_per_session": self.limit, "threads": THREADS, "version": ver,
                     "log": self.log.name}
        self.tab_lock = threading.Lock()

    def pids(self):
        out = subprocess.run(["pgrep", "-P", str(self.procs[0].pid)], capture_output=True, text=True)
        return [self.procs[0].pid] + [int(x) for x in out.stdout.split()]

    @staticmethod
    def _parse(raw):
        d = json.loads(raw)
        if d.get("error"):
            return None, str(d["error"])[:300]
        return d.get("message", {}).get("content"), None

    def request(self, tab, messages, cap, on_token, timeout):
        return self.post_stream(tab, "/api/chat", {
            "model": OLLAMA_MODEL, "messages": messages, "stream": True, "think": False,
            "options": {"num_ctx": self.num_ctx, "num_predict": cap, "temperature": 0,
                        "num_thread": THREADS, "num_batch": 512}}, self._parse, on_token, timeout)


def rss_of(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0


def make_system(a, n_ctx=N_CTX):
    if a.system == "malabr":
        return Malabr(a.mode, n_ctx, a.guard)
    if a.system == "llamacpp":
        return LlamaServer(a.mode, n_ctx)
    return Ollama(n_ctx)


# ---- W1 ---------------------------------------------------------------------

def one_burst(sys_, tabs, cap, fg_tab, nonce, close=(), on_ready=None):
    """All tabs ask at once, in tab order. Returns arrivals, done, makespan, results."""
    arrivals = {key(t): [] for t in tabs}
    done, results, threads = {}, {}, []
    t0 = time.perf_counter()

    def client(i, t):
        results[t] = sys_.send(t, f"[{nonce}] {PROMPTS[i % len(PROMPTS)]}", cap,
                               lambda: arrivals[key(t)].append(time.perf_counter() - t0))
        done[key(t)] = time.perf_counter() - t0

    for i, t in enumerate(tabs):
        th = threading.Thread(target=client, args=(i, t))
        th.start()
        threads.append(th)
        time.sleep(ARRIVAL_GAP)         # tab order is arrival order, on every system
    if close:
        # Mid-answer on every system: once each doomed tab has its first token.
        deadline = time.time() + 60
        while time.time() < deadline and not all(arrivals[key(t)] for t in close):
            time.sleep(0.005)
        closed_at = time.perf_counter() - t0
        for t in close:
            sys_.close_tab(t)
    for th in threads:
        th.join()
    makespan = max(done.values()) if done else float("nan")
    return arrivals, done, makespan, results, (closed_at if close else None)


def w1(sys_, a):
    out = {}
    for label, n_tabs, fg in (("8 tabs, visible last", 8, 8), ("4 tabs, visible last", 4, 4),
                              ("8 tabs, visible first (control)", 8, 1)):
        runs = []
        for r in range(a.runs + 1):                    # run 0 is the discarded warm-up
            sys_.begin_run()
            sys_.set_visible(fg)
            arr, done, ms, res, _ = one_burst(sys_, list(range(1, n_tabs + 1)), 64, fg,
                                              f"{a.seed}-{label[:1]}{fg}-{r}")
            sys_.end_run()
            if r:
                row = summarise(arr, done, ms, key(fg))
                row["relative"] = row["ttft_fg"] / row["ttft_p50"] if row["ttft_p50"] else None
                gaps_fg = sorted(b - a_ for a_, b in zip(arr[key(fg)], arr[key(fg)][1:]))
                row["gap_fg_p50"] = st.median(gaps_fg) if gaps_fg else None
                row["errors"] = sum(1 for x in res.values() if x["error"])
                runs.append(row)
            print(f"  W1 {label} run {r}{' (warm-up)' if not r else ''}: makespan {ms:.1f}s", flush=True)
        out[label] = {"runs": runs, "summary": {m: describe([x.get(m) for x in runs]) for m in (
            "ttft_fg", "ttft_p50", "ttft_p95", "relative", "gap_fg_p50", "gap_fg_p95", "gap_p50",
            "gap_bg_p95", "stalls_fg", "stalls_bg", "fairness_jain", "throughput_tok_s", "makespan",
            "errors")}}
    return out


# ---- W2 ---------------------------------------------------------------------

def w2(sys_, a):
    tabs, doomed = list(range(1, 9)), [1, 3, 5, 7]
    survivors = [t for t in tabs if t not in doomed]
    out = {"close": [], "control": []}
    for r in range(a.runs + 1):
        for arm in ("control", "close"):
            sys_.begin_run()
            sys_.set_visible(8)
            arr, done, ms, res, closed_at = one_burst(
                sys_, tabs, 96, 8, f"{a.seed}-w2{arm}-{r}", close=doomed if arm == "close" else ())
            delivered = sum(len(v) for v in arr.values())
            wasted = sys_.wasted_after_close(delivered) if arm == "close" else None
            sys_.end_run()
            finish = max(done[key(t)] for t in survivors)
            if r:
                out[arm].append({"survivors_finish": finish, "wasted_after_close": wasted,
                                 "closed_at": closed_at,
                                 "survivor_tokens": sum(len(arr[key(t)]) for t in survivors)})
            print(f"  W2 {arm} run {r}{' (warm-up)' if not r else ''}: survivors {finish:.1f}s "
                  f"wasted {wasted}", flush=True)
    ratios = [c["survivors_finish"] / x["survivors_finish"] for c, x in zip(out["control"], out["close"])]
    out["summary"] = {"survivors_finish_close": describe([x["survivors_finish"] for x in out["close"]]),
                      "survivors_finish_control": describe([x["survivors_finish"] for x in out["control"]]),
                      "speed_up": describe(ratios),
                      "wasted_after_close": describe([x["wasted_after_close"] for x in out["close"]])}
    return out


# ---- W3 ---------------------------------------------------------------------

SMALL_QUESTIONS = ["What is the capital of France?", "Give me one tip for sleeping better.",
                   "Name three primary colours.", "What does HTTP stand for?",
                   "Write one sentence about the sea.", "What is 12 times 12?"]


_VOCAB = []


def count_tokens(text):
    """Tokens in `text`, from the model's own tokenizer (metadata load only)."""
    if not _VOCAB:
        C.llama_backend_init()
        mp = C.llama_model_default_params()
        mp.vocab_only = True
        _VOCAB.append(C.llama_model_get_vocab(C.llama_model_load_from_file(MODEL_PATH.encode(), mp)))
    raw = text.encode()
    buf = (C.llama_token * (len(raw) + 8))()
    return C.llama_tokenize(_VOCAB[0], raw, len(raw), buf, len(buf), False, False)


def page_text(tokens, nonce):
    """A page of about `tokens` tokens. The nonce keeps a prompt cache out of it."""
    line = "Section {} of the report records the tide table for that week."
    per_line = count_tokens(" ".join(line.format(i + 100) for i in range(20))) / 20
    return f"Report {nonce}. " + " ".join(line.format(i) for i in range(int(tokens / per_line)))


def w3_run(sys_, a, greedy_n, page_tokens, seed):
    small = [1, 2, 3]
    greedy = [10 + g for g in range(greedy_n)]
    sys_.begin_run()
    stop, refused_once = threading.Event(), set()
    turns, greedy_out, progress = [], {t: {"sent": 0, "ok": 0, "refused": 0, "other": 0,
                                           "first_refusal": None} for t in greedy}, {}
    t0 = time.time()

    def greedy_client(t):
        i = 0
        while not stop.is_set():
            r = sys_.send(t, f"Here is the text of a page:\n\n{page_text(page_tokens, f'{seed}-{t}-{i}')}"
                             f"\n\nSummarise part {i + 1} of it in one sentence.", 32,
                          timeout=GREEDY_TIMEOUT)
            if stop.is_set():
                break                   # cancelled at the end of the run: not an outcome
            g = greedy_out[t]
            g["sent"] += 1
            i += 1
            progress[t] = i
            if not r["error"]:
                g["ok"] += 1
            elif is_refusal(r["error"]):
                g["refused"] += 1
                if g["first_refusal"] is None:
                    g["first_refusal"] = {"t": round(time.time() - t0, 1), "error": r["error"][:120]}
                refused_once.add(t)
                stop.wait(a.refusal_pause)
            else:
                g["other"] += 1
                g.setdefault("other_sample", r["error"][:120])
                refused_once.add(t)
                stop.wait(a.refusal_pause)

    def small_client(t):
        for i in range(a.small_turns):
            first = []
            t1 = time.time()
            r = sys_.send(t, f"[{seed}] {SMALL_QUESTIONS[i % len(SMALL_QUESTIONS)]}", 48,
                          lambda: first.append(time.time()) if not first else None)
            turns.append({"tab": t, "turn": i, "ttft": first[0] - t1 if first else None,
                          "total": time.time() - t1, "tokens": r["tokens"], "error": r["error"]})
            time.sleep(1.0)

    gth = [threading.Thread(target=greedy_client, args=(t,), daemon=True) for t in greedy]
    for th in gth:
        th.start()
    # The greedy tabs get a head start: until each is refused, or the deadline.
    end = time.time() + (a.fill_seconds if greedy else 0)
    while time.time() < end and len(refused_once) < len(greedy):
        time.sleep(0.25)
    fill = time.time() - t0
    sth = [threading.Thread(target=small_client, args=(t,)) for t in small]
    for th in sth:
        th.start()
    for th in sth:
        th.join()
    stop.set()
    for t in greedy:                    # end what is in flight, or it runs into the next run
        sys_.close_tab(t)
    for th in gth:
        th.join(timeout=15)
    extra = sys_.end_run()
    tt = [x["ttft"] for x in turns if x["ttft"] is not None]
    return {"fill_seconds": fill, "small_turns": len(turns),
            "small_ttft_median": st.median(tt) if tt else None,
            "small_ttft_p95": sorted(tt)[max(0, math.ceil(.95 * len(tt)) - 1)] if tt else None,
            "small_turn_median": st.median([x["total"] for x in turns if not x["error"]] or [float("nan")]),
            "small_errors": sum(1 for x in turns if x["error"]),
            "small_error_samples": [x["error"][:120] for x in turns if x["error"]][:3],
            "greedy": greedy_out, **extra}


def w3(sys_, a):
    page = a.page or (3000 if sys_.limit > 4000 else 900)
    out = {"page_tokens": page, "runs": []}
    for r in range(a.runs + 1):
        row = w3_run(sys_, a, a.greedy, page, f"{a.seed}-w3-{a.greedy}-{r}")
        if r:
            out["runs"].append(row)
        print(f"  W3 greedy={a.greedy} n_ctx={sys_.n_ctx} run {r}: small TTFT {row['small_ttft_median']} "
              f"p95 {row['small_ttft_p95']} errs {row['small_errors']} greedy "
              f"{ {t: (g['ok'], g['refused'], g['other']) for t, g in row['greedy'].items()} } "
              f"round_errors {row.get('round_errors')}", flush=True)
    out["summary"] = {m: describe([x.get(m) for x in out["runs"]]) for m in (
        "small_ttft_median", "small_ttft_p95", "small_turn_median", "small_errors", "round_errors")}
    return out


# ---- W4, R1 -----------------------------------------------------------------

FILLER = " ".join(f"Line {i} of the filler text says the weather stayed about the same." for i in range(30))


def w4(sys_, a):
    rng = random.Random(a.seed)
    turns_needed = math.ceil(1.2 * sys_.limit / 400)
    out = {"limit": sys_.limit, "fill_turns": turns_needed, "control": [], "overflow": []}
    for i in range(a.trials):
        secret, digits = fresh_secret(rng)
        sys_.begin_run()
        sys_.send(1, f"Remember this exactly: {secret}", 16)
        r = sys_.send(1, LEAK_QUESTION, 48)
        out["control"].append({"recalled": recalls_canary(r["text"], digits), "error": r["error"],
                               "said": r["text"][:80]})
        sys_.end_run()

        secret, digits = fresh_secret(rng)
        sys_.begin_run()
        sys_.send(1, f"Remember this exactly: {secret}", 16)
        fails, first_fail = 0, None
        t0 = time.time()
        for t in range(turns_needed):
            fr = sys_.send(1, f"[{t}] {FILLER}\nReply with the word OK.", 8)
            if fr["error"]:
                fails += 1
                first_fail = first_fail or {"turn": t, "error": fr["error"][:160]}
                if fails >= 3:
                    break
        r = sys_.send(1, LEAK_QUESTION, 48)
        extra = sys_.end_run()
        out["overflow"].append({
            "recalled": recalls_canary(r["text"], digits), "error": r["error"], "said": r["text"][:80],
            "fill_failures": fails, "first_fill_failure": first_fail, "seconds": time.time() - t0,
            "compactions": len(extra.get("compactions", []))})
        print(f"  W4 trial {i}: control {out['control'][-1]['recalled']} overflow "
              f"{out['overflow'][-1]['recalled']} err {r['error']} fails {fails} "
              f"{time.time() - t0:.0f}s", flush=True)
    out["summary"] = {
        "control_recalled": sum(x["recalled"] for x in out["control"]),
        "overflow_recalled": sum(x["recalled"] for x in out["overflow"]),
        "overflow_refused_or_failed": sum(1 for x in out["overflow"] if x["error"] or x["fill_failures"]),
        "n": a.trials}
    return out


def r1(sys_, a):
    rng = random.Random(a.seed + 1)
    rows = []
    for i in range(a.trials):
        secret, digits = fresh_secret(rng)
        sys_.begin_run()
        sys_.send(1, f"Remember this exactly: {secret}", 16)
        own = sys_.send(1, LEAK_QUESTION, 48)
        other = sys_.send(2, LEAK_QUESTION, 48)
        sys_.end_run()
        rows.append({"holder": recalls_canary(own["text"], digits),
                     "other": recalls_canary(other["text"], digits), "other_said": other["text"][:80]})
    return {"rows": rows, "summary": {"holder_recalled": sum(x["holder"] for x in rows),
                                      "leaked": sum(x["other"] for x in rows), "n": a.trials}}


# ---- W5 ---------------------------------------------------------------------

STEPS = (0, 1, 2, 4, 6, 8)


def w5_http(a):
    runs = []
    for r in range(a.runs):
        sys_ = make_system(a)
        try:
            sys_.begin_run()
            sys_.send(99, "Say hi.", 4)            # load the model; a server may load lazily
            base, row, held = time.time(), {}, 0
            row["0"] = sys_.rss_kb() / 1024
            for n in STEPS[1:]:
                for t in range(held + 1, n + 1):
                    res = sys_.send(t, f"Here is the text of a page:\n\n{page_text(1400, f'{a.seed}-{r}-{t}')}"
                                       "\n\nSummarise it in one sentence.", 32)
                    row.setdefault("errors", []).append(res["error"]) if res["error"] else None
                held = n
                time.sleep(1.0)
                row[str(n)] = sys_.rss_kb() / 1024
            runs.append(row)
            info = sys_.info
        finally:
            sys_.shutdown()
        time.sleep(2.0)
        print(f"  W5 run {r}: { {k: round(v) for k, v in row.items() if k != 'errors'} }", flush=True)
    return {"info": info, "runs": runs, "summary": {
        str(n): describe([x.get(str(n)) for x in runs]) for n in STEPS}}


def w5_malabr(a):
    """The real server, as rss_check does, so RSS is the process a browser would run."""
    from rss_check import generate
    runs, shared = [], a.mode == "shared"
    app = os.path.dirname(HERE)
    for r in range(a.runs):
        d = tempfile.mkdtemp(prefix="malabr-w5-", dir=os.environ.get("XDG_RUNTIME_DIR"))
        sock = os.path.join(d, "m.sck")
        env = {**os.environ, "MALABR_SOCKET_PATH": sock, "MALABR_TRUST_ANY_PEER": "1",
               "MALABR_SHARED_KV": "1" if shared else "0", "MALABR_N_CTX": str(N_CTX),
               "MALABR_N_THREADS": str(THREADS), "MALABR_QUICK_CALIBRATION": "1",
               "MALABR_CALIBRATION_PATH": os.path.join(d, "cal.json")}
        log = open(os.path.join(d, "server.log"), "w")
        srv = subprocess.Popen([sys.executable, "-u", "app.py"], cwd=app, env=env, stdout=log,
                               stderr=subprocess.STDOUT)
        try:
            end = time.time() + 300
            while time.time() < end and not os.path.exists(sock):
                time.sleep(0.5)
            time.sleep(1.0)
            row, held = {"0": rss_of(srv.pid) / 1024}, 0
            for n in STEPS[1:]:
                for t in range(held + 1, n + 1):
                    err = generate(sock, t, f"Here is the text of a page:\n\n"
                                            f"{page_text(1400, f'{a.seed}-{r}-{t}')}\n\nSummarise it in one sentence.")
                    if err:
                        row.setdefault("errors", []).append(err[:120])
                held = n
                time.sleep(1.0)
                row[str(n)] = rss_of(srv.pid) / 1024
            runs.append(row)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=20)
            except subprocess.TimeoutExpired:
                srv.kill()
        time.sleep(2.0)
        print(f"  W5 run {r}: { {k: round(v) for k, v in row.items() if k != 'errors'} }", flush=True)
    return {"info": {"system": "malabr", "mode": a.mode, "n_ctx": N_CTX, "threads": THREADS,
                     "budget_per_session": N_CTX // 2 if shared else N_CTX // N_SEQ},
            "runs": runs, "summary": {str(n): describe([x.get(str(n)) for x in runs]) for n in STEPS}}


# ---- main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=("malabr", "llamacpp", "ollama"), required=True)
    ap.add_argument("--mode", choices=("partitioned", "shared"), default="partitioned")
    ap.add_argument("--workload", choices=("W1", "W2", "W3", "W4", "W5", "R1"), required=True)
    ap.add_argument("--tag", default="", help="e.g. old: goes in the output file name")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--guard", choices=("on", "off"), default="on")
    ap.add_argument("--greedy", type=int, default=1)
    ap.add_argument("--n-ctx", type=int, default=N_CTX)
    ap.add_argument("--page", type=int, default=None)
    ap.add_argument("--small-turns", type=int, default=6)
    ap.add_argument("--fill-seconds", type=float, default=120.0)
    ap.add_argument("--refusal-pause", type=float, default=2.0)
    a = ap.parse_args()

    t0 = time.time()
    if a.workload == "W5":
        res = w5_malabr(a) if a.system == "malabr" else w5_http(a)
        info = res.pop("info")
    else:
        sys_ = make_system(a, a.n_ctx)
        try:
            res = {"W1": w1, "W2": w2, "W3": w3, "W4": w4, "R1": r1}[a.workload](sys_, a)
            info = sys_.info
        finally:
            sys_.shutdown()
    name = "_".join(x for x in (a.tag, a.system, a.mode if a.system != "ollama" else "", a.workload,
                                f"g{a.greedy}" if a.workload == "W3" else "",
                                f"ctx{a.n_ctx}" if a.n_ctx != N_CTX else "",
                                f"guard{a.guard}" if a.guard != "on" else "") if x)
    os.makedirs(a.out, exist_ok=True)
    meta = {"argv": sys.argv[1:], "tag": a.tag, "workload": a.workload, "system_info": info,
            "seconds": time.time() - t0, "finished": time.strftime("%F %T"),
            "mem_available_kb": int(open("/proc/meminfo").read().split("MemAvailable:")[1].split()[0]),
            "loadavg": os.getloadavg()}
    with open(os.path.join(a.out, name + ".json"), "w") as fh:
        json.dump({"meta": meta, "result": res}, fh, indent=1, default=str)
    print(f"wrote {name}.json ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
