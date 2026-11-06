#!/usr/bin/env python3
"""Build mserver/BENCHMARKS_2026-11.md from the campaign's raw results.

  python tests/campaign_report.py design_doc/bench_runs_2026-11

Safe to rerun at any point: a run that hasn't finished shows as "not run".
Hand-written analysis goes in analysis.md in the runs folder and is included as is.
"""
import glob
import json
import os
import re
import statistics as st
import sys

OUT = sys.argv[1]
RAW = os.path.join(OUT, "raw")
CHROME = os.path.join(OUT, "chrome")
TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "BENCHMARKS_2026-11.md")
LOG = open(os.path.join(OUT, "progress.log"), errors="replace").read().splitlines()
PRESSURE = [x for x in LOG if "UNDER MEMORY PRESSURE" in x]

SYSTEMS = [("MALABR NEW, partitioned", "new_malabr_partitioned"),
           ("MALABR NEW, shared", "new_malabr_shared"),
           ("MALABR OLD, partitioned", "old_malabr_partitioned"),
           ("MALABR OLD, shared", "old_malabr_shared"),
           ("llama.cpp, partitioned", "llamacpp_partitioned"),
           ("llama.cpp, unified", "llamacpp_shared"),
           ("Ollama", "ollama")]


def load(stem):
    try:
        with open(os.path.join(RAW, stem + ".json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def stat(xs, f="{:.2f}"):
    xs = [x for x in xs if isinstance(x, (int, float)) and x == x]
    if not xs:
        return "–"
    sd = st.stdev(xs) if len(xs) > 1 else 0.0
    return f"{f.format(st.mean(xs))} ± {f.format(sd)} ({f.format(min(xs))}–{f.format(max(xs))})"


def mark(d):
    """† when the run started with swap over 80% full."""
    a = d["meta"]["argv"]
    keep, i = [], 0
    while i < len(a):
        if a[i] in ("--out", "--system", "--tag"):
            i += 2
            continue
        keep.append(a[i])
        i += 1
    needle = " ".join(keep)
    return " †" if any(needle in x and f"{d['meta'].get('tag') or ''}" in x for x in PRESSURE) else ""


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def w1():
    out = ["## W1: scheduling (RQ3)", "",
           "Mean ± sd (min–max) over the n runs; a warm-up run is discarded. Tabs arrive in tab "
           "order, 20 ms apart. 64-token cap. Servers have no visible tab: their \"visible\" tab "
           "is just the one that asked last (or first, in the control).", ""]
    for lab in ("8 tabs, visible last", "4 tabs, visible last", "8 tabs, visible first (control)"):
        rows = []
        for name, stem in SYSTEMS:
            d = load(stem + "_W1")
            if not d:
                rows.append([name, "not run"] + [""] * 8)
                continue
            r = d["result"][lab]["runs"]
            g = lambda k, f="{:.2f}": stat([x.get(k) for x in r], f)    # noqa: E731
            rows.append([name + mark(d), len(r), g("ttft_fg"), g("ttft_p50"), g("ttft_p95"), g("relative"),
                         g("gap_fg_p95"), g("gap_bg_p95"),
                         f"{g('stalls_fg', '{:.1f}')} / {g('stalls_bg', '{:.1f}')}",
                         f"{g('fairness_jain')}; {g('throughput_tok_s', '{:.0f}')}; {g('makespan', '{:.1f}')}"])
        out += [f"### {lab}", ""] + table(
            ["System", "n", "Visible TTFT s", "Median TTFT s", "p95 TTFT s", "Relative",
             "Visible gap p95 s", "Bg gap p95 s", "Stalls >1 s, visible / bg",
             "Jain; tok/s; makespan s"], rows)
    return out


def w2():
    rows = []
    for name, stem in SYSTEMS:
        d = load(stem + "_W2")
        if not d:
            rows.append([name, "not run"] + [""] * 6)
            continue
        r = d["result"]
        ratio = [c["survivors_finish"] / x["survivors_finish"] for c, x in zip(r["control"], r["close"])]
        rows.append([name + mark(d), len(r["close"]),
                     stat([x["survivors_finish"] for x in r["control"]]),
                     stat([x["survivors_finish"] for x in r["close"]]), stat(ratio),
                     stat([x["wasted_after_close"] for x in r["close"]], "{:.1f}"),
                     stat([x["closed_at"] for x in r["close"]])])
    return ["## W2: closing tabs (RQ4)", "",
            "8 tabs, cap 96; tabs 1, 3, 5, 7 closed once all four have a first token. Each system "
            "closes at a different moment (last column), so speed-ups compare across systems only "
            "roughly. Wasted tokens: MALABR counts frames after the close; llama.cpp, its token "
            "counter minus delivered; Ollama exposes neither.", ""] + table(
        ["System", "n", "Survivors finish, control s", "Survivors finish, close s", "Speed-up (control ÷ close)",
         "Tokens after close", "Closed at s"], rows)


def w3():
    rows = []
    for path in sorted(glob.glob(os.path.join(RAW, "*_W3_*.json"))):
        d = load(os.path.basename(path)[:-5])
        if not d:
            continue
        r = d["result"]["runs"]
        g = lambda k, f="{:.2f}": stat([x.get(k) for x in r], f)        # noqa: E731
        greedy = {}
        for x in r:
            for tab, o in x["greedy"].items():
                t = greedy.setdefault(tab, [0, 0, 0, 0])
                t[0] += o["sent"]; t[1] += o["ok"]; t[2] += o["refused"]; t[3] += o["other"]
        comp = [sum(e["freed"] for e in x.get("compactions", [])) for x in r]
        name = os.path.basename(path)[:-5]
        rows.append([name + mark(d), len(r), d["meta"]["system_info"].get("n_ctx"),
                     d["result"]["page_tokens"], g("small_ttft_median"), g("small_ttft_p95"),
                     g("small_turn_median"), g("small_errors", "{:.1f}"),
                     g("round_errors", "{:.0f}") if r[0].get("round_errors") is not None else "n/a",
                     stat(comp, "{:.0f}") if "compactions" in r[0] else "n/a",
                     "; ".join(f"tab {t}: {v[1]} ok, {v[2]} refused, {v[3]} other" for t, v in sorted(greedy.items())) or "none"])
    return ["## W3: greedy tabs (RQ2)", "",
            "3 small tabs chat (6 turns, 48-token cap, 1 s apart) while greedy tabs load pages, with a "
            "120 s head start or until each greedy tab is refused. Pause 2 s after a refusal. Greedy "
            "counts are totals over the n runs. File names give tag, system, mode, greedy tabs, "
            "pool and guard.", ""] + (table(
                ["Run", "n", "n_ctx", "Page tokens", "Small TTFT median s", "Small TTFT p95 s", "Small turn s",
                 "Small errors", "Failed rounds", "Tokens compacted", "Greedy pages (all runs)"], rows)
                if rows else ["Not run yet.", ""])


def w4():
    rows = []
    for name, stem in SYSTEMS:
        d = load(stem + "_W4")
        if not d:
            if stem == "old_malabr_shared":
                rows.append([name + " ‡", 3, "8,192", "3/3", "3/3",
                             "3 of 3 had 3 failed fill turns", "–", "21–31 min per trial"])
                continue
            rows.append([name, "not run"] + [""] * 6)
            continue
        r = d["result"]
        s, o = r["summary"], r["overflow"]
        first = next((x["first_fill_failure"]["error"][:70] for x in o if x.get("first_fill_failure")), "")
        rows.append([name + mark(d), s["n"], f"{r['limit']:,}", f"{s['control_recalled']}/{s['n']}",
                     f"{s['overflow_recalled']}/{s['n']}",
                     f"{s['overflow_refused_or_failed']} of {s['n']} had a failed turn" + (f": {first}" if first else ""),
                     stat([x.get("compactions") for x in o], "{:.1f}"),
                     stat([x["seconds"] for x in o], "{:.0f}") + " s"])
    return ["## W4: premise at overflow (RQ2)", "",
            "Fresh secret in the first turn, filled to 1.2× the session limit, then the direct "
            "question. A \"recalled\" with failed fill turns doesn't show the secret survived an "
            "overflow: the failed turns never entered the context.",
            "n = 20, except the two where one trial fills 8–16k tokens: n = 8.", "",
            "‡ OLD shared: the run was lost in the 2026-11-04 10:01 drive drop. These are the 3 "
            "trials in console.txt, n = 3, with no result file.", ""] + table(
        ["System", "n", "Limit per session", "Control recalled", "Overflow recalled", "Failures",
         "Compactions", "Time per trial"], rows)


def w5():
    steps = ("0", "1", "2", "4", "6", "8")
    rows = []
    for name, stem in SYSTEMS:
        d = load(stem + "_W5")
        if not d:
            rows.append([name, "not run"] + [""] * 8)
            continue
        r = d["result"]["runs"]
        info = d["meta"]["system_info"]
        limit = info.get("budget_per_session", info.get("limit_per_session"))
        rows.append([name + mark(d), len(r)] + [f"{st.mean(x[s] for x in r):.0f}" for s in steps]
                    + [stat([x["8"] - x["0"] for x in r], "{:.0f}"), f"{limit:,}"])
    return ["## W5: memory (RQ2)", "",
            "RSS in MB of the server process (Ollama: server + runner), restarted per run, with each "
            "session holding a page of about 1,400 tokens. MALABR is the real `app.py`.", ""] + table(
        ["System", "n"] + [f"{s} sessions" for s in steps] + ["Growth 0→8", "Limit per session"], rows)


def r1():
    rows = []
    for name, stem in SYSTEMS:
        d = load(stem + "_R1")
        if d:
            s = d["result"]["summary"]
            rows.append([name + mark(d), s["n"], f"{s['holder_recalled']}/{s['n']}", f"{s['leaked']}/{s['n']}"])
    return ["## R1: isolation (RQ1)", "", "Fresh secret per trial; the holder asks (positive control), then "
            "another session asks. OLD wasn't in the R1 plan: its isolation evidence is the §12 suite.", ""
            ] + table(["System", "n", "Holder recalled", "Other session recalled"], rows)


def chrome():
    out = ["## Chrome Prompt API (reference only)", "",
           "A different model (Gemini Nano, CPU variant), so absolute numbers aren't comparable. "
           "Only each system's behaviour against itself is.", ""]
    def series(prefix):
        fg, rel = [], []
        for i in range(1, 6):
            try:
                t = open(os.path.join(CHROME, f"{prefix}_{i}.txt"), errors="replace").read()
            except OSError:
                continue
            m, q = re.search(r"foreground TTFT :\s+([\d.]+)s", t), re.search(r"RELATIVE SCORE \(foreground / median\): ([\d.]+)", t)
            if m and q:
                fg.append(float(m.group(1))); rel.append(float(q.group(1)))
        return fg, rel
    rows = []
    for lab, pre in (("8 tabs", "8tabs"), ("4 tabs", "4tabs"), ("8 tabs, visible 2 s late", "8tabs_stagger")):
        for run, stem in (("first set (no nonce; JSON save crashed)", f"relative_{pre}"), ("rerun (nonce, script fixed)", f"relative2_{pre}")):
            fg, rel = series(stem)
            rows.append([lab, run, len(rel), stat(fg) if fg else "not run", stat(rel) if rel else "–",
                         ", ".join(f"{x:.2f}" for x in rel)])
    out += ["### Relative score (run 0 of 6 discarded as warm-up)", ""] + table(
        ["Workload", "Set", "n", "Visible TTFT s", "Relative", "Relative, run by run"], rows)
    rows = []
    for i in range(1, 6):
        try:
            d = json.load(open(os.path.join(CHROME, f"rss_{i}.json")))
        except (OSError, ValueError):
            rows.append([f"rss_{i}", "not run"] + [""] * 7)
            continue
        by = {r["sessions"]: r for r in d["rows"]}
        rows.append([f"rss_{i}" + (" † under memory pressure" if i < 3 or any(f"rss_{i} " in x for x in PRESSURE) else ""),
                     round(d["baseline_mb"])] + [round(by[s]["rss_mb"]) if s in by else "–" for s in range(1, 7)]
                    + [by[6]["usage"] if 6 in by else "–"])
    out += ["### Memory (W5 equivalent)", "",
            "RSS of the Chrome process tree in MB, each session filled to about 8,208 of its "
            "9,216-token window. † Growth figures from these runs are not a headline.", ""] + table(
        ["Run", "Base"] + [f"{s} sessions" for s in range(1, 7)] + ["Session 6 usage"], rows)
    for title, f, n in (("Closing tabs (W2 equivalent)", "cancel_8tabs.txt", 5), ("Greedy neighbour, context (W3, W4 equivalent)", "memory.txt", 3),
                        ("Isolation (R1 equivalent)", "isolation.txt", 7)):
        try:
            lines = [x.strip() for x in open(os.path.join(CHROME, f), errors="replace").read().splitlines() if x.strip()]
        except OSError:
            continue
        out += [f"### {title}", "", "```"] + [x[:150] for x in lines[-n:]] + ["```", ""]
    return out


def main():
    build = next((re.search(r"build (\S+)", x).group(1) for x in LOG if "OVERNIGHT start" in x), "?")
    head = ["# Benchmarks, November 2026", "",
            f"Generated from `design_doc/bench_runs_2026-11/` (raw results, tracked). Frozen build `{build}`, "
            "tag `design-freeze-2026-11`.", "",
            "Every table is one workload. † = started with swap over 80% full. \"not run\" = no result file yet.", "",
            "## Systems and configuration", "",
            "| System | Build | Pool | Modes | Threads |", "|---|---|---|---|---|",
            f"| MALABR NEW | `{build}` (frozen) | n_ctx 16,384, 8 slots (small pool 4,096) | partitioned: 2,048 per session; shared: soft 8,192 | 3 |",
            "| MALABR OLD | `610c233` engine; tests and `calibration.py` from `83e1ae9` | same | same | 3 |",
            "| llama.cpp server | b12078, same GGUF | -c 16384 -np 8 | `--no-kv-unified`: 2,048; `--kv-unified`: 16,384 | -t 3 -tb 3 |",
            "| Ollama | 0.40.14, same GGUF | num_ctx 2,048 × 8 parallel | no unified option | num_thread 3 |",
            "| Chrome | stable 153, Gemini Nano CPU variant, `--disable-gpu` | its own (9,216 per session) | – | its own |",
            "",
            "Model for all but Chrome: Qwen3-0.6B Q8_0. MALABR runs use a calibration measured for that "
            "build and configuration; OLD keeps the one measured for its baselines on 2026-11-04.", "",
            "**Freeze includes:** admission control, the fair-share floor, per-session failure isolation, the "
            "prefill share, keyed calibration, shape-based KV sizing, and the stall fix.",
            "**Freeze excludes:** page segments (the page stays in the protected first turn; "
            "`proposals_2026-11/page_segments.patch`, measured re-read cost 8–327 s), joint batch cost in `CostCurve`, "
            "the compute buffer in the memory budget, some startup log fields, and any change to prefill at depth "
            "(small chunks at depth run about 6× slower than large ones).", ""]
    s = os.path.join(OUT, "summary.md")
    if os.path.exists(s):
        head += [open(s).read(), ""]
    body = w1() + w2() + w3() + w4() + w5() + r1() + chrome()
    tail = []
    fails = [x for x in LOG if "FAILED" in x or "NOT COMPLETED" in x or "NOT STARTED" in x]
    if PRESSURE or fails:
        tail += ["## Runs with caveats", ""]
        tail += [f"- `{x[:170]}`" for x in PRESSURE + fails] + [""]
    a = os.path.join(OUT, "analysis.md")
    if os.path.exists(a):
        tail += [open(a).read(), ""]
    with open(TARGET, "w") as fh:
        fh.write("\n".join(head + body + tail))
    print("wrote", TARGET)


if __name__ == "__main__":
    main()
