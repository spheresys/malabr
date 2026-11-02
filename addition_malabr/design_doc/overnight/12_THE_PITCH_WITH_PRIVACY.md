# The pitch, with privacy as a first-class thread

Supersedes `11_THE_PITCH.md`. Same story, but privacy is where it starts and
where the honest tensions live.

---

## 1. The scene

An ordinary afternoon. A dozen tabs.

In one, you asked the browser's AI to summarise a long report — still working.
Three others you asked something earlier and wandered off; still generating,
into tabs you can't see. One is your bank. One is a site you'd never typed a
password into.

You switch to another tab, type a question, press enter.

Two questions follow, and the thesis is about both:

1. **Who should the machine serve next?**
2. **What can the tab next door learn about you?**

---

## 2. Why the model is on your machine at all: privacy

This is the part to say first, because it is the reason the setting exists.

On-device inference is sold on privacy. Your prompt never leaves the laptop;
there is no operator to trust, no retention policy to read, no jurisdiction to
worry about. Chrome's own framing is exactly this — nothing goes to a server.

That is a real privacy win, and it is why browsers are shipping it.

## 3. But it inverts the threat model, and nobody designed for the new one

Moving inference on-device removes the cloud operator from the picture — and
introduces an adversary that was never there before: **the other tab**.

In the cloud, tenants are kept apart by the provider: separate processes,
separate machines, an operator whose business depends on isolation. On your
laptop, mutually distrusting web origins share **one model process, one
context, and the same batched forward pass**. Your bank and that other site are
co-tenants of a single piece of memory.

So privacy stops being *"don't send the data anywhere"* and becomes a **systems
property**: *don't let the page next door learn anything.* That is a different
problem, with a different solution, and it is the one this thesis takes up.

## 4. Four things are scarce, not three

**Compute.** One CPU, five pages wanting tokens.

**Memory that cannot grow.** The model's working memory is allocated once and
cannot expand. Every token the report summary keeps is one your chat cannot
have.

**Attention.** Exactly one tab has a human in front of it — a signal no server
can ever have, because a server has no idea what a tab is.

**Confidentiality.** Twelve origins, one memory. Nothing about sharing a model
makes them safe from each other by default.

And the line that explains why the browser has to solve this rather than the
OS: **Linux can kill a process but cannot shrink one** — it has no idea what
the memory *means*. It also cannot tell your bank tab from an advert, because
to the kernel they are the same renderer. The browser knows both. It is the
only layer that does.

---

## 5. What the shipped system actually does

Chrome ships this to web pages today, so I measured it rather than speculating.

**Where it is good — and I say so plainly.** Chrome *does* isolate. I planted a
secret in one origin's session and tried to read it from a different origin,
from a second tab, and from a fresh session after destroying the first. All
three blocked. **Confidentiality of content is not where Chrome is weak.**

**Where governance is absent — three measured gaps.**

- **Every page gets a full memory allowance, never divided.** 9216 tokens per
  session; still 9216 with a second session, and a sixth. The cap protects a
  page from itself and does nothing about the total.
- **Close a tab and the work keeps running.** I closed two of four generating
  tabs; survivors sped up 1.04× against a 1.11× control. Halving the sessions
  should have roughly doubled their rate. It didn't move. *Your machine keeps
  working on a page you have dismissed.*
- **At the memory limit it forgets why you started talking.** I filled a session
  past its limit, having planted a fact at the beginning. Chrome evicted from
  the front, then answered with filler from the padding — `'lazy'`, `'dog.'` —
  instead of the fact.

**So: sharing works, confidentiality of content holds, and governance is
missing.**

---

## 6. What I built

A patched Chromium plus an inference server. The governance mechanisms:

- **Divides the pool** — memory allocated once and split, with a guard that
  reclaims from the *greediest* session.
- **Prefers the tab you're looking at** — re-deciding every token, not once per
  request.
- **Reclaims on close** — a closed tab's work stops within one scheduling round.
- **Degrades by choosing what to lose** — drops the middle of a conversation,
  keeps the opening and the most recent exchange.

And the privacy mechanisms, which are design decisions rather than features:

- **The web origin is the identity.** A session is keyed
  `(extension, tab, origin)`, derived entirely browser-side from an
  authenticated connection. A page cannot claim another identity in its request
  — the claim isn't in the request. The design doc calls origin *"a privacy
  boundary rather than a label"*, which is the whole point.
- **Memory slots are wiped when handed out, not when returned.** A return can be
  skipped by a crash or a bug; a handout cannot. And the wipe is *proved* — the
  code clears the slot and then independently confirms it reads back empty,
  raising an error if not.
- **Nothing is durable.** Conversations live only in RAM. No database, no file,
  no disk write on either side, by design.
- **"New chat" is a real teardown.** This was a bug I found and fixed: the panel
  cleared the display while the server's session and its memory lived on, so the
  next message silently continued the old conversation. Clearing the screen is
  not forgetting.
- **The transcript dies with the tab**, held in session-scoped storage rather
  than anything persistent.
- **Page text is sent once per session**, hashed, and re-sent only if the page
  actually changed — rather than streaming your page content to the model on
  every keystroke.

About 3,200 lines of server, 900 of panel, C++ in the browser for identity and
visibility, 4,900 lines of tests and harnesses. 68 tests pass in both memory
modes.

---

## 7. The four research questions

Each is the scene in §1, asked precisely.

**RQ1 — When several pages want the model at once, who goes next, and does it
matter?**

**RQ2 — When memory runs out, whose conversation is shrunk, by how much, and
what does it cost in answer quality?**

**RQ3 — Can pages sharing one model learn anything about each other?**
*This is the privacy question, and it is the one the setting makes urgent.*

**RQ4 — When you close a tab, does the work actually stop?**

---

## 8. Where each stands

### RQ1 — Scheduling: answered, with the price measured
Foreground first word, 8 tabs, visible tab asking last: **16.55 s** one at a
time → **4.22 s** batched evenly → **0.44 s** mine. The cost, measured rather
than waited for: visible tab **14.9 tokens/sec**, hidden tabs **~3.6** —
fairness **0.63 against 0.997**. Throughput unchanged at ~32 tok/s.

*I did not make the machine faster. I changed who waits.*

Unexpected: **Chrome prioritises the visible tab too, but decides once, at
admission.** A foreground request issued four seconds *after* three others still
gets its first token in ~1.8 s. After that Chrome serves everything
near-equally. I re-decide every token — which is why Chrome stays fair and I
don't.

### RQ2 — Memory: answered, and the strongest result
*The pool:* **"Chrome bounds a session. I bound the machine."**

*The degradation:* I discard **58–64%** of a conversation and still recall the
fact planted at the start, with the model's next-token choice unchanged on every
probe (shift of 0.0017–0.0032 against a 0.0002 noise floor).

**"Chrome keeps the recent and loses the premise. I keep the premise and lose
the middle."**

### RQ3 — Confidentiality: answered for content, open for timing
Cross-origin, cross-tab, and post-destroy-slot-reuse: all blocked. **Chrome
blocks all three too**, so this is not where I beat it.

Two things make the result worth more than "both pass":

**I demonstrated the leak before fixing it.** Slot reuse across origins did leak
until wipe-on-acquire was introduced. The property is real, not asserted.

**The method nearly fooled me.** My first cross-origin test "passed" — but the
model was answering "UNKNOWN" to *everything*, including its own planted secret.
A positive control caught it. Without that control I would have published three
meaningless passes. *A privacy test that cannot distinguish "isolated" from
"the model simply didn't answer" proves nothing.*

**The honest open problem.** I tested whether *content* leaks. The security
literature on shared models shows the realistic attack is **timing** — you learn
about your neighbour from how long your own requests take. My architecture
creates exactly those conditions, and so does Chrome's. I have not measured it.
That is the clearest limitation and the best thing left to do.

### RQ4 — Lifecycle: answered, the cleanest win
Counted as tokens actually entering the model after the close, not frames
delivered: **453 → 0** wasted tokens; tabs you're still watching finish
**1.94× sooner**. Chrome recovers none.

There is a privacy reading too: compute spent on a page you dismissed is a page
still being processed after you walked away.

---

## 9. Two tensions I own rather than hide

**Tension 1 — my memory policy is better for utility and arguably worse for
privacy.**

My compaction keeps the *opening* turn by construction. Chrome evicts from the
front. So the sensitive thing you mentioned at the start of a conversation is
precisely the thing my system refuses to forget, and the first thing Chrome
does. Better answers, longer retention of your earliest words. I do not think
this sinks the design — nothing is durable, so it dies with the tab — but it is
a real trade and an examiner may well raise it before I do.

**Tension 2 — being responsive to you requires observing them.**

My scheduler decides admission by reading *other sessions'* state: how deep
their contexts are, how much pressure the pool is under, which tab is visible.
That is exactly what makes it responsive — and exactly what makes it
observable. A policy that ignored its neighbours would leak nothing through
admission and would also be unable to prefer your tab.

**So responsiveness and confidentiality are the same dial turned opposite ways.**
That is the sharpest thing in the project, it is testable, and it is the natural
next chapter.

---

## 10. One methodological result worth volunteering

*My first isolation test was nearly blind: its question let the model say ‘I don’t know’, and it took that escape even when it knew the answer, so the test would have missed about 19 of 20 real leaks. I only found this by building a deliberately broken engine and checking the test caught it. The corrected method caught 120 of 120 leaks with no false alarms.*

---

## 11. What I can claim, and what I cannot

**Can claim:** Chrome's memory pool is never divided and mine is; Chrome keeps
computing for closed tabs and I recover all of it; under pressure the two
systems lose categorically different things; attention-aware scheduling buys an
order of magnitude on foreground latency at a measured cost in fairness; and
origin-keyed sessions with proved wipe-on-acquire hold against content leakage —
demonstrated, not assumed.

**Cannot claim:** that I am faster than Chrome (0.092 ± 0.009 vs 0.086 ± 0.012 —
indistinguishable); that I isolate better than Chrome (equal on the channel I
tested); that I have addressed timing channels (I have not measured them); that
the mechanisms are mine (Orca, vLLM, Sarathi-Serve, StreamingLLM); or that it
generalises (one machine, CPU only, two small models, ≤16 tabs, workloads where
every session costs the same).

---

## 12. The 60-second version

> Browsers now run AI models on your own machine, and the reason is privacy —
> your prompts never leave the laptop. But it quietly inverts the threat model.
> The cloud operator is gone; in their place, every page you have open now
> shares one model, one memory, and the same forward pass. Your bank tab and an
> untrusted site are co-tenants.
>
> I built the governance that sharing needs. Origin is the identity, derived by
> the browser rather than claimed by the page. Memory slots are wiped when
> handed out and the wipe is proved. Nothing is written to disk. Closing a tab
> actually stops its work — Chrome's keeps running. The memory pool is divided
> between pages instead of handed out whole to each. And the tab you're looking
> at gets its first word in 0.44 seconds instead of 4.2.
>
> What I can show: content does not leak between origins, and I showed that by
> first building it wrong and watching it leak. What I cannot yet show: that
> *timing* does not leak — pages sharing a model can in principle learn about
> each other from how long their own requests take, and neither my system nor
> Chrome's has been measured for it.
>
> So: on-device AI solved the privacy problem of sending your data away, and
> created a new one of pages sharing a machine. This thesis governs that
> sharing, and is honest about the half still open.
