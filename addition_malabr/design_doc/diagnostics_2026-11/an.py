import glob, json, os, statistics as st, sys
OUT = sys.argv[1]


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


for f in sorted(glob.glob(OUT + "/*.jsonl")):
    rows = [json.loads(x) for x in open(f)]
    name = os.path.basename(f)[:-6]
    rounds = [r for r in rows if r["k"] == "round" and "obs" in r]
    turns = [r for r in rows if r["k"] == "turn"]
    adm = [r for r in rows if r["k"] == "admit"]
    print("=====", name, "rounds", len(rounds))
    # rounds by composition, during the small-tab phase
    small_t0 = min((t["t0"] for t in turns if t["tab"] in (1, 2, 3)), default=None)
    ph = [r for r in rounds if small_t0 and r["t"] >= small_t0]
    if ph:
        dur = ph[-1]["t"] - ph[0]["t"]
        print(" small phase %.0f s, rounds %d, decode time %.0f s (%.0f%% busy)" % (dur, len(ph), sum(r["obs"] for r in ph) / 1000, 100 * sum(r["obs"] for r in ph) / 1000 / max(dur, 1)))
        print(" obs ms median %.0f p95 %.0f; est ms median %.0f; ratio median %.2f; correction first %.2f median %.2f last %.2f" % (
            st.median(r["obs"] for r in ph), q([r["obs"] for r in ph], .95), st.median(r["est"] for r in ph),
            st.median(r["obs"] / max(r["est"], 1e-6) for r in ph), ph[0]["corr"], st.median(r["corr"] for r in ph), ph[-1]["corr"]))
        g = [c for r in ph for c in r["chunk"] if c[0] >= 10]
        sm = [c for r in ph for c in r["chunk"] if c[0] < 10]
        print(" greedy chunks %d, tokens %d, median size %s, rate %.1f tok/s; small chunks %d, tokens %d, sizes %s" % (
            len(g), sum(c[2] for c in g), st.median(c[2] for c in g) if g else None, sum(c[2] for c in g) / max(dur, 1), len(sm), sum(c[2] for c in sm),
            sorted({c[2] for c in sm})[:8]))
        print(" greedy depth at phase start/end:", ([c[1] for r in ph for c in r["chunk"] if c[0] >= 10] or [None])[0], ([c[1] for r in ph for c in r["chunk"] if c[0] >= 10] or [None])[-1])
    # per small-tab turn breakdown
    rowsT = []
    for t in sorted((x for x in turns if x["tab"] in (1, 2, 3)), key=lambda x: x["t0"]):
        tab = t["tab"]; end = t["first"] or t["end"]
        rs = [r for r in rounds if t["t0"] <= r["t"] <= end]
        ch = [r for r in rs if any(c[0] == tab for c in r["chunk"])]
        if not t["first"]:
            rowsT.append((None, None, None, None, None, None, t["error"])); continue
        first = ch[0]["t"] if ch else end
        last = ch[-1]["t"] + ch[-1]["obs"] / 1000 if ch else end
        toks = sum(c[2] for r in ch for c in r["chunk"] if c[0] == tab)
        rowsT.append((end - t["t0"], first - t["t0"], last - first, len(ch), toks,
                      st.median(r["obs"] for r in rs) if rs else 0, sum(1 for r in rs) ))
    ok = [x for x in rowsT if x[0] is not None]
    if ok:
        print(" small TTFT median %.2f; wait before first chunk %.2f; first-to-last chunk %.2f; chunks per prompt %.0f; prompt tokens %.0f; round ms during wait %.0f; rounds %.0f" % tuple(
            st.median(x[i] for x in ok) for i in range(7)))
        print(" first turns:", [tuple(round(v, 2) for v in x[:6]) for x in ok[:3]], " later:", [tuple(round(v, 2) for v in x[:6]) for x in ok[-2:]])
    bad = [x[6] for x in rowsT if x[0] is None]
    if bad:
        print(" small turns with no token:", len(bad), bad[0][:70] if bad[0] else bad[0])
    # sample rounds in small phase containing a small chunk
    samp = [r for r in ph if any(c[0] < 10 for c in r["chunk"])][:3] + ph[len(ph) // 2:len(ph) // 2 + 2]
    for r in samp:
        print("  round pick", r["pick"], "chunk", r["chunk"], "wait", r["wait"], "est %.0f obs %.0f corr %.2f" % (r["est"], r["obs"], r["corr"]))
    # admission
    from collections import Counter
    c = Counter((a["tab"] >= 10, a["verdict"]) for a in adm)
    print(" admission (greedy?, verdict):", dict(c))
    for a in [x for x in adm if x["tab"] >= 10][:4]:
        print("  greedy admit t=%.0f pos %d need %d %s others %s" % (a["t"] - rows[0]["t"], a["pos"], a["need"], a["verdict"], a["others"][:3]))
    for a in [x for x in adm if x["tab"] < 10 and x["verdict"]][:2]:
        print("  small admit pos %d need %d %s others %s" % (a["pos"], a["need"], a["verdict"], a["others"]))
    gt = [t for t in turns if t["tab"] >= 10]
    print(" greedy turns:", [(t["tab"], round(t["end"] - t["t0"], 1), t["tokens"], (t["error"] or "")[:25]) for t in gt][:6])
