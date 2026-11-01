# The pitch — the whole story, plainly

For understanding the project yourself first. One running example, four
questions, honest status on each.

---

## 1. The scene

It's an ordinary afternoon. You have a dozen tabs open.

In one, you asked the browser's built-in AI to summarise a long report. It's
still working on it. In three others you asked something earlier and wandered
off; they're still generating, into tabs you can't see. Now you switch to a
fourth tab, type a question, and press enter.

**Who should the machine serve next?**

There is one model. It is loaded once, into one process, and shared by every
page. There is one CPU. And there is one of you — looking at exactly one tab.

Nobody decided the answer to that question. That is what this thesis is about.

---

## 2. Why it is genuinely hard

Three things are scarce, and they interact.

**Compute.** One CPU, five pages wanting tokens. Serve them in turn and the tab
you're staring at waits behind the report summariser. Serve them all at once and
everyone goes slowly.

**Memory that cannot grow.** The model's working memory — the KV cache, where
the conversation lives — is allocated once when the model loads and cannot
expand. Every token your report summary keeps is a token your chat cannot have.
This is the part people miss: the pages are not just competing for *time*, they
are competing for *memory that is already full*.

**Attention.** Exactly one tab has a human in front of it. This is the signal
that makes the problem different from every server version of it — and it's a
signal no server can ever have, because a server has no idea what a tab is.

And here's the thing an operating system cannot help with: **Linux can kill a
process, but it cannot shrink one.** It has no idea what the memory means. A
conversation, though, can lose its middle and still be useful. That judgement
can only be made by something that understands what the memory *is* — and the
browser is now that something, for every page on the web.

---

## 3. What exists today

This is not hypothetical. Chrome ships an on-device model to web pages. Sharing
one model across origins already works, in production, for very large numbers of
people.

So the interesting question is not *"can it be shared?"* It's **"is the sharing
governed?"**

I measured the shipped system to find out. Three answers, all reproducible:

**Chrome gives every page a full memory allowance, and never divides it.**
Each session gets 9216 tokens of context. Open a second session: still 9216.
A sixth: still 9216. The cap protects a page from itself; it does nothing about
the total. Six pages can claim 9216 × 6 between them, and the machine — not any
policy — decides what happens next.

**Close a tab and the work keeps running.** I started four tabs generating,
closed two, and measured whether the survivors sped up. They didn't: 1.04×
against a 1.11× control where nothing was closed. If the compute had been
returned, halving the sessions should have roughly *doubled* the survivors'
rate. It didn't move.

**When memory runs out, it forgets why you started talking.** I filled a session
past its limit, having planted a fact at the very beginning. Chrome evicted from
the front and then answered with filler words from the padding — `'lazy'`,
`'dog.'` — instead of the fact. The *premise* is the first thing lost, which in
a conversation is usually the instruction or the topic.

**So: sharing is solved. Governance is absent.** That is the gap.

---

## 4. What I built

A patched Chromium plus an inference server that governs the sharing:

- **It divides the pool.** The memory is allocated once and split between
  sessions, with a guard that reclaims from the *greediest* session when the
  total runs short.
- **It prefers the tab you're looking at**, re-deciding every single token
  rather than once per request.
- **It reclaims on close.** When a tab goes away, its work stops within one
  scheduling round.
- **It degrades by choosing what to lose.** When a conversation outgrows its
  share, it drops the middle and keeps the opening and the most recent
  exchange.
- **It keeps origins apart.** A session's identity is the page's origin, bound
  to an authenticated connection, and memory slots are wiped *when handed out*
  rather than when returned — because a return can be skipped by a crash, and a
  handout cannot.

About 3,200 lines of server, 900 of chat panel, C++ in the browser for identity
and visibility, and 4,900 lines of tests and measurement harnesses. 68 tests
pass in both memory modes.

---

## 5. The four research questions

Each one is just the scene in §1, asked precisely.

### RQ1 — When several pages want the model at once, who goes next, and does it matter?
*In the example:* you just pressed enter in the fourth tab, behind a report
summariser and three background chats.

### RQ2 — When memory runs out, whose conversation gets shrunk, by how much, and what does that cost in answer quality?
*In the example:* the report summary is enormous. Something has to give.

### RQ3 — Can pages that share one model learn anything about each other?
*In the example:* one of those twelve tabs is a site you don't trust. It is
sharing a model process with your banking tab.

### RQ4 — When you close a tab, does the work actually stop?
*In the example:* you close the report summariser halfway through. Does that
compute come back to you, or is it spent on nobody?

---

## 6. Where each one stands

### RQ1 — Scheduling: **answered, with the price measured**

Eight tabs, the visible one asking last (the realistic case — the others are
already busy). Time until the first word appears in the tab you're watching:

| | foreground first token |
|---|---|
| one request at a time | **16.55 s** |
| all equally, batched | **4.22 s** |
| **mine** | **0.44 s** |

And the cost, which I measured rather than waited to be asked about: the visible
tab gets **14.9 tokens/sec while every hidden tab gets about 3.6**. On the
standard fairness measure that is **0.63, against 0.997** for the even-handed
policies. Total throughput is unchanged at ~32 tokens/sec.

**So I did not make the machine faster. I changed who waits.** That is the
honest claim, and the exchange rate is now a number instead of an assumption.

There is also a finding here I did not expect. **Chrome prioritises the visible
tab too** — but at a different point. A foreground request issued *four seconds
after* three background tabs still gets its first token in ~1.8 seconds: it
jumps the queue. But after that, Chrome serves everything near-equally (0.99
fairness). So Chrome **decides once, at the door**. I **re-decide every token**.
That is exactly why Chrome stays fair and I don't — and it predicts that
Chrome's advantage fades on long replies while mine persists.

### RQ2 — Memory: **answered, and this is the strongest result**

Two separate findings.

*The pool.* Chrome caps each session at 9216 tokens and never divides;
I allocate once and split. **"Chrome bounds a session. I bound the machine."**

*The degradation.* Both systems degrade rather than fail — but they lose
opposite things. Chrome loses the premise (above). I discard **58–64%** of a
conversation and still recall the fact planted at the start, with the model's
next-token choice unchanged on every probe. The distributional shift is
0.0017–0.0032 against a measured noise floor of 0.0002 — real but tiny.

**"Chrome keeps the recent and loses the premise. I keep the premise and lose
the middle."** Same aggregate fidelity, opposite choice. I have not found this
contrast reported as a system property elsewhere, though an absence of that kind
cannot be proved by searching.

### RQ3 — Isolation: **answered, and it is NOT my differentiator**

I planted a secret in one origin's session and tried to reach it from another
origin, from a second tab, and from a fresh session after destroying the first.
All blocked — *and* Chrome blocks all three too.

So the honest position: Chrome is safe here, and my contribution is the
*method*, not a defect found. (The method mattered: my first version of the test
was meaningless because the model answered "UNKNOWN" to everything including
its own secret. A positive control caught it.)

There is a real gap, though, and it is where the project could go next.
I tested whether the *content* leaks. The current security literature shows that
on shared models the realistic attack is **timing** — you learn about your
neighbour from how long your own requests take. My architecture creates exactly
those conditions, and so does Chrome's. **That is a known limitation, honestly
stated, and the most promising thing left to do.**

### RQ4 — Lifecycle: **answered, and it is the cleanest win**

Closing a tab should return its compute. Measured by counting tokens that
actually entered the model after the close, not frames delivered to the screen:

| | compute spent on closed tabs | watched tabs finish |
|---|---|---|
| delivery stops only | 453 tokens | 22.19 s |
| **mine** | **0 tokens** | **11.87 s** |

All of it recovered, and the tabs you're still watching finish **1.94× sooner**.
Chrome, as above, recovers none.

---

## 7. What I can claim, and what I cannot

**Can claim:**
- Chrome's context pool is never divided; mine is.
- Chrome keeps computing for closed tabs; I recover 100% of it.
- Under pressure, the two systems lose categorically different things.
- Attention-aware scheduling buys an order of magnitude on foreground latency,
  and costs a measured amount of fairness.

**Cannot claim:**
- That I am faster than Chrome. On foreground responsiveness we are
  **statistically indistinguishable** (0.092 ± 0.009 vs 0.086 ± 0.012) — the
  gap is smaller than the spread.
- That I isolate better than Chrome. Equal on the channel I tested.
- That the mechanisms are mine. Iteration-level scheduling, paged memory,
  chunked prefill and budgeted eviction are all established work. I compose
  them for a setting they were not built for.
- That it generalises. One machine, CPU only, two small models, up to 16 tabs,
  and workloads where every session is equally expensive — which real tab
  populations are not.

---

## 8. The 60-second version

> Browsers now ship a local AI model and share it across every page you have
> open. The sharing works. The *governing* doesn't: every page is handed a full
> memory allowance that is never divided between them, work keeps running for
> tabs you have closed, and when memory runs out the model forgets why you
> started talking rather than forgetting the middle.
>
> I built the governance that's missing — a divided memory pool, reclamation on
> tab close, and a scheduler that prefers the tab you're actually looking at —
> inside a patched Chromium, and measured it against the shipped system.
>
> The visible tab gets its first word in 0.44 seconds instead of 4.2. The tabs
> you can't see pay for that, and I measured exactly how much. Closing a tab now
> returns all of its compute instead of none. And when memory runs short, the
> conversation loses its middle instead of its premise.
>
> I am not faster than Chrome at answering the tab you're looking at — we're
> indistinguishable there. I am *governed* where Chrome is not, and I can tell
> you what that costs.
