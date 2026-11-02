# Thesis defence — the story, in plain terms

No idea-numbers, no plan. Just: what you built, what you proved, what you
concede, and what you say when pushed.

---

## 1. The one-sentence thesis

> **Browsers now share one local LLM across web pages. Sharing works.
> Governing it does not — and I built and measured the governance.**

If you only get one sentence in the room, that is it.

## 2. The four-move opening (about two minutes)

**Move 1 — the setting is real, not hypothetical.**
Chrome ships an on-device model to web pages. One model process, many origins,
one CPU, and exactly one tab with a human in front of it. This is a
multi-tenant system that nobody designed as one.

**Move 2 — three things are scarce, and only one is managed.**
Compute (one CPU, N sessions), resident context (the KV cache is allocated once
and cannot grow), and attention (one tab is visible; the rest are not). Chrome
manages *eligibility* — can this device run a model at all. It does not manage
*contention*.

**Move 3 — the gap, in one line each, measured on the shipped system.**
- Chrome gives **every** session a full context window — 9216 tokens, and it
  stays 9216 whether one session exists or six. The pool is capped per session
  and **never divided**, so N sessions can claim 9216·N.
- When a tab closes, Chrome **keeps computing for it**. Survivors speed up
  1.04× against a 1.11× control — no reclamation.
- At the context limit, Chrome **evicts from the front**: it loses the first
  thing the user said.

**Move 4 — what I did.** A patched Chromium plus an inference server that
divides the pool, reclaims on tab close, prefers the visible tab, and degrades
by choosing what to lose. ~3,200 lines of server, ~900 of panel, C++ in the
browser for identity and visibility, and ~4,900 lines of tests and measurement
harnesses. 68 tests passing in two memory modes.

---

## 3. The three things you defend

Lead with these two, **not** with speed — on speed you tie with Chrome.

### Defence 1 — I bound the machine; Chrome bounds a session
*The strongest and simplest contribution.*

Chrome's per-session cap protects one page from itself. It does nothing about
the aggregate: six sessions, six full windows. MALABR allocates the pool once
at startup and divides it, with a guard that reclaims from the **greediest**
session when the total approaches the pool.

**The line to use:** *"Chrome bounds a session. I bound the machine."*

### Defence 2 — Degradation is a choice, and the two systems choose oppositely
*The most interesting result, and it is reproducible.*

Both systems degrade rather than fail. But:
- **Chrome** slides the window from the front. Fill a session past its limit and
  it answers with filler from the padding — `'lazy'`, `'dog.'` — instead of the
  fact planted at the start. **The premise is the first thing lost**, which for
  a chat is usually the instruction or the topic.
- **MALABR** keeps the opening turn and the most recent one by construction, and
  drops the middle. After discarding **58–64%** of the conversation it still
  recalls the planted fact, with the model's next-token choice unchanged on
  every probe (KL 0.0017–0.0032 against a measured noise floor of 0.0002).

**The line:** *"Chrome keeps the recent and loses the premise. I keep the
premise and lose the middle. Same aggregate fidelity, opposite choice — and
I have not found that contrast reported as a system
property elsewhere — though I say 'have not found', not 'does not exist'."*

### Defence 3 — Attention-aware scheduling, with its price named
Foreground time-to-first-token, 8 tabs, the visible tab asking last:
**16.55 s** (one-at-a-time) → **4.22 s** (equal-share batching) → **0.44 s**
(MALABR).

And the cost, volunteered before anyone asks: fairness falls from **0.997 to
0.631** (Jain index). The visible tab gets **14.9 tokens/sec while every other
tab gets ~3.6**. Aggregate throughput is unchanged at ~32 tok/s — so this is
**redistribution, not creation**, and I say so.

**The line:** *"I did not make the machine faster. I changed who waits. Here is
the exchange rate."*

---

## 4. What you concede, early and without being asked

Conceding these *first* is what makes the rest credible.

1. **I am not faster than Chrome on foreground responsiveness.** Measured as a
   ratio within each system (foreground ÷ median, so different models don't
   confound it): MALABR **0.092 ± 0.009**, Chrome **0.086 ± 0.012**, five and
   four runs. **Statistically indistinguishable** — the gap is smaller than
   either spread and the ordering flips depending on which runs you include.
2. **Isolation is not my differentiator.** I tested cross-origin, cross-tab and
   post-destroy canary leakage against Chrome. Chrome passes all three. My
   contribution there is the *method* (including a positive control that caught
   my own first test being meaningless), not a defect in Chrome.
3. **The mechanisms are borrowed.** Iteration-level scheduling is Orca; paged KV
   is vLLM; chunked prefill is Sarathi-Serve; budgeted eviction is
   StreamingLLM/H2O; per-client token fairness is VTC. I compose them; I did not
   invent them.
4. **My evaluation is synthetic and narrow.** Homogeneous workloads, two tab
   counts, one machine, CPU-only, two small models.

## 5. The architectural finding that ties it together

Worth its own slide, because it reframes the Chrome tie from a weakness into a
result.

Chrome and MALABR both favour the visible tab — but **at different points in the
pipeline**:

- **Chrome prioritises at admission.** A foreground request issued **four
  seconds after** three background tabs still gets its first token in ~1.8 s —
  it jumps the queue. But once everything is streaming it serves them
  near-equally (fairness 0.99).
- **MALABR prioritises at every decode round.** The foreground keeps 14.9 tok/s
  against 3.6 for the whole generation — hence 0.63.

**The line:** *"We both prioritise the visible tab. Chrome decides once, at the
door. I re-decide every token. That is why Chrome stays fair and I do not — and
it predicts that Chrome's advantage decays on long replies while mine does
not."*

That last clause is a falsifiable prediction you can offer the panel.

---

## 6. The five questions you will be asked

**Q: Isn't this just Orca / vLLM / VTC?**
"Those schedule anonymous clients. None of them knows what a tab is, because
none of them runs inside a browser. I use their mechanisms with a signal they
cannot obtain — which tab a human is looking at. And I govern a *pool divided
across origins*, where they cap totals."

**Q: Your fairness index is 0.631. That is bad.**
"It is deliberate, and it is the point. That literature maximises fairness
because its clients have equal claim. Mine do not — one has a human watching.
I am optimising a different objective and I measured the exchange rate rather
than hiding it. If you want fairness back, the honest answer is that the current
policy has no knob for it, and that is the first thing I would fix: borrow the
priority and repay it, rather than granting it permanently."

**Q: Chrome is as fast as you. So what did you add?**
"Two things Chrome measurably does not do. It never divides its context pool —
9216 tokens per session, constant from one session to six. And it keeps
computing for tabs you have closed. I recover 100% of that wasted work and the
tabs you are still watching finish 1.94× sooner."

**Q: Why does any of this matter?**
"Because an operating system can kill a process but cannot shrink one — it has
no idea what the memory means. I do. A conversation can lose its middle and
stay useful. That is a governance decision only something inside the
application can make, and the browser is now that application for every page
on the web."

**Q: Your workloads are synthetic — would this hold in reality?**
"Partly, and I will say where it is weakest. Every session in my evaluation
costs the same and is worth the same. Real tab populations are not like that —
one long page summarisation beside five short chats. That is where governance
should matter *most*, and I have not run it. It is the first experiment I would
add, and I would expect the gap to widen rather than narrow."

---

## 7. One methodological result worth volunteering

It shows judgement, and panels reward that.

**The line:** *"My first isolation test was nearly blind: its question let the model say ‘I don’t know’, and it took that escape even when it knew the answer, so the test would have missed about 19 of 20 real leaks. I only found this by building a deliberately broken engine and checking the test caught it. The corrected method caught 120 of 120 leaks with no false alarms."*

---

## 8. Closing, thirty seconds

> "Sharing a local model across web pages already ships. What ships with it is
> a per-session cap that is never divided, compute that keeps running for tabs
> you have closed, and a context window that forgets why you started talking.
> I built the governance that is missing, measured it against the shipped
> system, and named what it costs: the visible tab gets an order of magnitude
> better latency, and the hidden ones pay for it. That trade is now a number
> instead of an assumption."
