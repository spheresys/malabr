# MALABR — Updated Problem Statement (v2)

**Date:** 2026-08-20
**Status:** supersedes the research-question and related-work parts of
`research_problem_statement.md` (v1). v1 is kept — its background, threat model
(§6.6, §6.7), and reference list are still good. This document replaces what v1
says about Chrome, about the research questions, and about what to build first.

**Why v2 exists:** we read Chrome's current source code and ran benchmarks on
this machine. Several things in v1 turned out to be wrong. This document says
what is actually true, in plain language, and what to do next.

---

## 0. The three things that changed

**1. Chrome already does the main thing v1 proposed.**
v1's headline question was "should the browser prioritise the tab you're looking
at?" Chrome now does exactly that, in shipped open-source code. That question is
answered.

**2. v1 says Chrome's policy is secret. It isn't.**
v1 §3.2 says the scheduling policy "is unpublished and cannot be varied or
instrumented." That sentence is false and must be removed. The policy is in the
Chromium source and we quoted it directly. What *is* closed is the model
(Gemini Nano) and the maths engine — Chrome loads that from an external binary
called `optimization_guide_internal`, which is not in the source tree.

The honest version: **the decisions are open, the maths is closed.** Nobody has
ever measured Chrome's policy or compared it to anything. That is the real gap.

**3. Running two models is worse than one. We measured it.**
An idea we explored — one model for foreground tabs, one for background — is
dead. Numbers in §3.

---

## 1. The problem, in plain terms

Several browser extensions want to use an AI model at the same time.

Today each one loads its own copy. That wastes memory, and none of them knows
the others exist. The obvious fix is for the browser to load one model and share
it. Chrome does this now, and so does Edge.

But sharing creates a new problem, and this is what the project is about:

> **Once many sessions live inside one process, the operating system can no
> longer protect them from each other.**

Linux can limit how much CPU and memory a *program* uses. It cannot limit a
*session*, because a session is not a program — it is a few entries in a table
inside one program. There is nothing for the kernel to grab hold of.

So somebody has to do that job in software, inside the server. Nobody has.

**Two consequences:**

- **Memory.** No system today limits how much memory a session can use. Chrome
  has no session limit, no memory ceiling, no per-session budget — we checked the
  source. One long conversation can grow without anything stopping it.
- **Fairness.** One tab can take everything and the others just wait.

---

## 2. What exists today

### 2.1 Chrome — the only one using browser knowledge

Chrome reads whether a tab is visible and uses it to decide what runs next.

```
foreground  = the tab is visible
background  = it is not
```

When the model finishes something, it looks through the waiting list, takes the
first foreground item, and if there are none, takes whatever is at the front.

That's the whole scheduler. It works, and nobody else does even this much.

**But it has five problems, all read from the source:**

1. **Background work can wait forever.** The decision only looks at "is this
   visible." It never looks at how long something has been waiting. Chrome
   *records* the waiting time — but only to report a statistic, never to make a
   decision.
2. **Work for closed tabs gets priority.** The check is "is this session
   foreground **or gone**?" A dead session passes that check, so work belonging
   to a tab you closed jumps ahead of work for tabs that are still open. This is
   backwards.
3. **It cannot tell two tabs apart.** The queue entry holds no extension id and
   no tab id. Two visible tabs look identical to it, so one extension can take
   everything and Chrome cannot even notice.
4. **It never interrupts.** Once something starts, it runs to the end. If you
   click on a tab while a background summary is running, you wait for that
   summary to finish. On our machine that's **2.95 seconds** for a short
   response, and longer for a real one.
5. **Nothing has a limit.** The session list is unbounded. The waiting list is
   unbounded. There is no memory cap. A client can queue as much as it likes.

**And a sixth, which is a security problem:** Chrome does have a setting for
"maximum tokens to produce," but it is *optional* and it is set **by the caller**.
The limit on how much a client may consume is chosen by that client. A badly
written extension leaves it out; a malicious one certainly does. This is the same
bug class as OWASP's "Unbounded Consumption" and the vLLM advisory already cited
in v1 §9.5.

**One correction to something we believed earlier:** older Chrome (version 127,
which is what our local checkout is) threw away a session's conversation and
rebuilt it every time it switched sessions. **Current Chrome does not do this any
more** — sessions now keep their own state. So Chrome no longer pays that cost,
and Chrome is no longer automatically safe from leaks between sessions. Both
systems now have the same risk.

### 2.2 Everyone else — sharing, but no policy at all

| System | Shares one model? | How does it choose? | Knows about tabs? |
|---|---|---|---|
| **Chrome** | yes | visible tab first | **yes — only one** |
| Edge | yes | not published | unknown |
| Firefox | no — refuses to run engines in parallel | — | no |
| **Ollama** | yes | strict first-come-first-served | no |
| **llama.cpp server** | yes | first-come-first-served | no |
| **WebLLM** | yes, across tabs | nothing | no |
| vLLM / SGLang | yes | sophisticated | no — built for servers |

Ollama's own documentation describes our exact problem: *"a 10-token request
waits behind a 4,000-token generation with no way to skip ahead."* It is the most
widely used local AI server and it has no priorities at all.

llama.cpp's server already has the machinery — slots for separate sessions, a
queue, batching. What it does not have is any idea of *whose* request matters.

### 2.3 Research — most of the pieces already exist

This matters, so it is stated plainly rather than buried:

| Idea we discussed | Already published as |
|---|---|
| Track tokens used per client, serve whoever used least | **VTC**, OSDI 2024 (has a proof) |
| Work in small chunks so nothing gets stuck waiting | **Sarathi-Serve**, OSDI 2024 |
| Fair sharing that also reuses cached work | **DLPM**, 2025 |
| Keep the first few tokens plus a recent window | **StreamingLLM** |
| Keep only the important tokens | **H2O**, **SnapKV**, **PyramidKV** |
| Save a session to disk while it's idle | **CachedAttention**, **InferCept** |

**We are not inventing these, and should not claim to.** They are building blocks
to use.

---

## 3. What we measured on this machine

Real numbers, `Qwen3-0.6B-Q8_0`, 8 logical / 4 physical cores, 14 GB RAM.

### 3.1 Speed and threads

| threads | tokens/sec |
|---|---|
| 1 | 21.2 |
| 2 | 34.6 |
| **4** | **43.4** ← best |
| 8 | 34.1 ← *worse than 4* |

**Use 4 threads, not 8.** More threads makes it slower.

### 3.2 Two models is worse than one

| | tokens/sec |
|---|---|
| one model, 4 threads | **43.4** |
| two models, 4 threads each | 20.6 + 21.0 = **41.6** |

**Why:** producing one token means reading the whole model from memory. Both
copies read through the same memory channel, so they share one budget:

```
   one model : 43.4 × 0.61 GB = 26.5 GB/s
   two models: 41.6 × 0.61 GB = 25.4 GB/s     ← same ceiling
```

Two copies do not create more memory bandwidth. They split what exists.

**And two models caps the foreground.** With one model you can give the visible
tab *everything*:

| how you split | visible tab | background |
|---|---|---|
| one model, visible tab only | **43.4** | 0 |
| one model, 9 to 1 | **39.1** | 4.3 |
| two models | **21.0** | 20.6 |

One model makes the visible tab roughly **twice as fast** as two models do,
because two models permanently limits it to half the machine.

### 3.3 Stopping between tokens is nearly free

| | tokens/sec |
|---|---|
| generate all at once | 43.6 |
| stop after every token | 43.4 |

**About 1% cost.** This is important — it means we can make a decision after
*every token* instead of only after a whole response. That turns Chrome's
2.95-second wait into about **23 milliseconds**.

### 3.4 Memory is a fixed pool

| sessions allowed | context each | total memory |
|---|---|---|
| 1 | 4096 | 1139 MB |
| 2 | 2048 | 1139 MB |
| 4 | 1024 | 1140 MB |
| 8 | 512 | 1143 MB |

**Memory does not grow when sessions are added.** You reserve a pool once and
sessions share it. Eight sessions cost 4 MB more than one.

The catch: more sessions means a shorter conversation each. That trade is the
heart of Phase 1.

Roughly **107 KB per token** of conversation. Up to 256 sessions supported.

### 3.5 Sessions are separate — but not cleaned up automatically

```
session A writes 5 tokens   → A has 5,  B has none
session B writes 2 tokens   → A has 5,  B has 2     (they don't affect each other)

session A ends, its slot is reused:
   before clearing   → A's data is STILL THERE
   after clearing    → gone
```

**Sessions do not interfere while both are alive.** But when a session ends,
nothing wipes it. If a new session gets that slot without an explicit clear, it
inherits the old conversation.

**That is a real leak, and it is one missing line of code away.** It is also
exactly what Phase 1 must prevent.

### 3.6 Shrinking a conversation works, but drifts

We removed a chunk from the middle of a conversation and closed the gap:

```
positions after the operation: exactly as expected — the mechanism works

compared against a conversation that never had that chunk:
   next token chosen : IDENTICAL
   the ranking below : starts to differ at 4th place
```

**So shrinking works but is not perfect.** The model picks the same next word,
but its internal numbers have shifted slightly.

**This matters because the error builds up.** Shrink once and nothing changes.
Shrink twenty times over a long conversation and it might. Nobody has measured
this, and browser conversations last hours.

*Caveat: one test, one model. The model we used has an unusual attention design
that may explain all of it. Repeat on Qwen3 before relying on it.*

### 3.7 One number we do NOT have

Switching between two sessions instead of staying on one: we measured **+9.9%,
+8.5%, and 0.0%** on three runs. That is noise, not a result. It needs redoing
properly on a quiet machine. All we can say is that it is small — nowhere near
the seconds it replaces.

---

## 4. What is actually broken

Putting §2 and §3 together, four holes exist in every system today:

| Hole | Who has it | What we can do |
|---|---|---|
| Old session data is not wiped | us and current Chrome | wipe when a slot is handed out |
| No memory limit per session | everyone | fixed pool, divided by policy |
| Work for closed tabs still runs | everyone | cancel when a tab closes |
| Visible tab waits behind background work | everyone | decide every token, not every response |

---

## 5. Research questions

Split into two phases. **Phase 1 comes first because you cannot schedule
sessions that do not exist yet** — the session table, the memory pool, and the
slot allocator all have to work before any scheduling can be built on them.

### Phase 1 — sessions and memory

**RQ1. Can many sessions share one model with nothing leaking between them?**

Including the hard case: a session ends, its slot is reused, and nothing of the
old one survives. We have demonstrated this failing, so it is a real question
with a real answer, not a formality.

*How we answer it:* plant a secret in one session, then try to reach it from
another — during its life, after it ends, and after its slot is reused.

**RQ2. When memory runs out, whose conversation gets shrunk, by how much, and
what does that cost?**

Memory is one fixed pool shared by all sessions. Something has to give when it
fills. There are five choices, not two:

| option | cost to do | what is lost |
|---|---|---|
| keep everything | nothing | nothing |
| drop the middle | almost nothing | the middle of the conversation |
| summarise it | one generation | unpredictable |
| save to disk | a file write | nothing, but slow to bring back |
| delete it | nothing | everything |

**Which one you pick can depend on whether the tab is visible.** The tab you're
looking at keeps everything; a hidden one gets shrunk; a long-dead one goes to
disk.

**This is the part an operating system fundamentally cannot do.** Linux can kill
a program. It cannot compress one, because it has no idea what the memory means.
We do. That is the clearest reason this project is not just a rate limiter.

*How we measure quality without getting lost:* put a fact early in a
conversation, shrink it, then ask for that fact back. Yes or no. No scoring
model, no judgement calls. **Same test as RQ1**, asking a different question.

### Phase 2 — scheduling

**RQ3. Does using tab visibility to decide what runs next make the visible tab
faster?**

Three schedulers, same machine, same model, same work:

| | how it decides | known weakness |
|---|---|---|
| Chrome's | visible first, once per response | 2.95 s waits |
| VTC | fairest share, often | cannot see tabs at all |
| ours | visible first, every token | — |

**RQ4. Does cancelling work for closed tabs remove waste?**

**This is the strongest card.** No other system can do it. Chrome doesn't — and
actually runs dead tabs' work *early*. Ollama can't. VTC can't. None of them
knows what a tab is.

### What is deliberately not an RQ

- **Overhead** — measured and reported alongside every result, not studied
  separately.
- **Whether sharing saves memory** — Chrome and Edge already prove it. It's the
  starting assumption, not a finding.

---

## 6. The design

### 6.1 Shape

```
   browser tab (JavaScript)
        │  asks for text
        ▼
   browser process (C++)          knows which tab, and whether it's visible
        │  sends the request plus that knowledge
        ▼
   our server (Python)            one model, many sessions, decides who runs
```

Same three layers as before. The new part is that the middle layer now sends
down *what it knows about the tab*, and the bottom layer acts on it.

### 6.2 What a session is

A session is one tab's conversation with one extension: `(extension, tab)`.

Inside the model, it is a numbered slot holding that conversation. Slots are
limited, so they get reused — which is exactly why they must be wiped.

### 6.3 The rule that prevents leaks

**Wipe a slot when you hand it out, not when you give it back.**

Giving it back can be forgotten. Handing it out cannot — you have to do it to
start a session at all. Putting the safety step there means a forgotten cleanup
is harmless.

Backed up by: one piece of code owns all slots, a check that a fresh slot really
is empty, and tests that end sessions and look for leftovers.

### 6.4 Three limits, not one

| limit | stops |
|---|---|
| tokens per second, per client | one client hogging over time |
| tokens per single request | one runaway answer |
| how many requests waiting, per client | flooding the queue |

Chrome has the second one but lets the *client* set it, which means it isn't a
limit at all. Ours is set by the server, and can depend on whether the tab is
visible.

**Neat trick:** if a request that uses up its turn goes to the back of the queue
instead of continuing, that *is* the second limit. A very long answer becomes
many short turns, and it can never monopolise. This is what operating systems
do — a program that uses its slice gets paused and requeued, not killed.

---

## 7. How to evaluate

### 7.1 Compare against three things

| baseline | represents |
|---|---|
| **MALABR-CHROME** | what browsers actually ship |
| **MALABR-VTC** | the best published research method |
| **MALABR-ours** | using browser knowledge |

### 7.2 Do NOT test against real Chrome

Tempting, but it would prove nothing. Real Chrome uses a different model, a
different engine, and different settings. If we were faster, we could not say
whether it was our scheduling or just a faster engine.

**Instead, implement Chrome's algorithm inside our own system.** Same model, same
engine, same machine — only the policy differs. We can do this faithfully because
we read the source.

**This also means we do not need to update the Chromium checkout.** Version 127
has everything needed. A sync would cost 8+ hours of rebuilding, on top of a
97 GB repository, for an experiment that would not answer the question.

### 7.3 What to measure

- time until the visible tab gets its answer (typical and worst case)
- how long the longest-waiting request waited
- how evenly clients were served
- memory used, both busy and idle
- work done for tabs that had already closed
- how often the secret leaked (should be never)
- how often the fact survived shrinking

**Always report cost next to benefit.** If a policy helps the visible tab by 2
seconds and costs 15% throughput, say both.

---

## 8. Build plan

### Phase 1 — mostly Python, low risk

| step | what |
|---|---|
| 1 | Generate text with several sessions at once, using the low-level interface |
| 2 | A session table keyed on (extension, tab) |
| 3 | A slot allocator that wipes on hand-out |
| 4 | Track memory per session |
| 5 | Shrinking: drop a range, save to disk |
| 6 | Secret-leak and fact-recall tests |

**Almost none of this touches the browser.** A script pretending to be several
extensions is enough to test it. The only browser change needed is adding the
tab id to the message header.

That matters — it means the risky 8-hour build stays out of the way until later.

### Phase 2 — adds the browser

| step | what |
|---|---|
| 7 | Send tab visibility from the browser |
| 8 | Tell the server when a tab closes |
| 9 | Three schedulers: Chrome's, VTC's, ours |
| 10 | Cancel work for closed tabs |
| 11 | Measurement scripts |
| 12 | Run everything, collect numbers |

### The hardest part, flagged early

Step 1 is the risk. The easy Python interface **does not support several sessions**
— it only handles one conversation. Everything needed is available at the lower
level, but it has to be driven directly, which is fiddly work.

We confirmed the pieces exist: multiple sessions, per-token tagging, saving a
session to disk, removing part of a conversation, and interrupting generation.

---

## 9. What could go wrong

| risk | how bad | what to do |
|---|---|---|
| The low-level interface is fiddly and slow to get right | high | do it first, it blocks everything |
| Shrinking drifts more than we think | medium | re-test on Qwen3 in week 1 |
| Switching sessions costs more than 1% | medium | measure properly and early |
| We only reimplement published methods | medium | make sure results show browser knowledge *changing the answer*, not just being present |
| The external drive fails again | high | it already did once today — set up a git remote |

### On the "we didn't invent anything" worry

Most of the building blocks are published. That is fine. Systems work is
frequently about putting known pieces together for a new situation and showing
with numbers that it works better.

But it only counts if the new situation **changes the answer**. Fortunately it
does, in four ways we can support:

1. **One slot, limited by memory speed.** All the published methods assume many
   parallel slots on a graphics card. We measured that adding parallelism here
   adds nothing.
2. **The priority comes from outside the request.** Every published method guesses
   importance from the request itself. None of them can know a tab closed.
3. **Sessions last hours, not seconds.** That is why shrinking drift matters here
   and nowhere else.
4. **DLPM's method may be forbidden here.** It works by sharing cached work
   *between* clients — which is exactly what our isolation requirement rules out.
   The best published method depends on something our security model cannot allow.

**The comparison against VTC is what proves this.** If we only tie it, the browser
knowledge did not matter and we should say so.

---

## 10. Still open

- Redo the session-switching measurement properly. **Highest priority** — part of
  RQ3 depends on it.
- Repeat the shrinking test on Qwen3 to rule out the model being the cause.
- Prefill speed is unmeasured; all numbers are generation-only.
- When did Chrome's visibility code land? Decides whether we are "after" or
  "alongside" that work.
- Does the extension path use the same code as the web page path in Chrome?
- Do we allow batching several sessions into one pass? It would roughly double
  throughput but moves us closer to server research. **Currently: no**, and say
  why.

---

## 11. Fixes needed in v1

| where | problem |
|---|---|
| §3.2 | "the policy is unpublished and cannot be varied" — **false**, delete it |
| §9.9 | Chrome's row says scheduling "not published" — **false**, it is open source |
| §5 | all four research questions replaced by §5 here |
| §10 | "continuous batching out of scope" — still true, but say *why* now |
| §12 | phases reordered: sessions and memory first, scheduling second |

**Replacement wording for §3.2:**

> Chrome's scheduling policy is visible in open source but has never been
> evaluated or compared against alternatives, and the model and inference engine
> remain closed.

That is a **better** position than "it is closed." If it were closed we could only
guess. Because it is open, we can copy it exactly, measure it fairly, and show
precisely where it falls short.

---

## 12. The whole thing in one paragraph

Browsers are starting to load one AI model and share it between extensions.
Sharing saves memory, but it puts many users inside one program, where the
operating system can no longer keep them apart. Nobody limits how much memory a
session uses, nobody stops one tab taking everything, and nobody notices when a
tab closes and its work becomes pointless. Chrome does one thing about this — it
prefers the tab you are looking at — and even that has clear holes. We build the
missing part: sessions that cannot see each other, memory that is shared by an
explicit rule instead of by accident, and scheduling that uses what only a
browser knows. We measure it against what Chrome ships and against the best
published method.
