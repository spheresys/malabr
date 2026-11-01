# MALABR: Browser-Managed Shared Inference with Session Isolation and Lifecycle-Aware Scheduling

## Research Problem Statement — MTP Document

---

## 1. Title

**MALABR: A Browser-Managed Shared Local Inference Runtime with Session Isolation and Lifecycle-Aware Scheduling**

---

## 2. Abstract

Local AI inference in browsers is growing rapidly. Current browser AI systems — Chrome's and Edge's Prompt APIs, Firefox's AI Runtime, WebLLM — expose inference either as a per-application runtime or as a closed product capability, and operating systems are converging on the same shared-model shape at the OS level (Apple Foundation Models, Windows Phi Silica). Yet no existing *open* system studies how a shared inference runtime, owned and managed by the browser process itself, should coordinate multiple isolated sessions across concurrent clients using browser-context signals such as tab visibility, tab lifecycle events, and extension identity — the vendors that have built this shape publish neither their policies nor a way to vary them.

MALABR is an open Chromium-based research prototype that investigates this problem. One language model is loaded once and shared across all authorized extension clients. Each client tab receives an isolated session — its own conversation context, scoped to its (extension_id, tab_id) identity — that is created when a tab opens and destroyed when a tab closes. A browser-aware scheduler dispatches inference requests using tab visibility signals from Chromium, prioritizing foreground sessions over background ones. Lifecycle cancellation removes queued requests for closed tabs before they consume compute.

We implement and compare three scheduling policies against an application-owned baseline and measure shared model residency savings, foreground latency improvement, wasted compute reduction, and isolation correctness. MALABR is the first open research prototype to make these orchestration policies explicit, implementable, and measurable in the Chromium browser process.

---

## 3. Motivation

### 3.1 The Problem With How AI Works in Browsers Today

Consider a realistic scenario: a user has three browser tabs open.

**Tab A** — an AI coding assistant extension is actively helping the user write code. The user is typing, asking questions, receiving suggestions in real time. This is a foreground, latency-sensitive workload.

**Tab B** — a PDF summarizer extension is running in the background, summarizing a research paper the user opened 5 minutes ago and forgot about.

**Tab C** — a translation extension is translating a page the user navigated away from 2 minutes ago. The request is stale but still queued.

Under the current model — where each extension manages its own AI independently — three things go wrong:

**Problem 1: Three separate models in memory.**
Each extension has downloaded and loaded its own copy of a language model. If
all three use the same 2B-class 4-bit model, the weight residency cost scales
roughly with the number of independent runtimes, plus KV cache and allocator
overhead. The exact RSS depends on the model artifact, backend, context window,
and allocator, so MALABR treats this as a measured quantity rather than a fixed
constant. The architectural problem is still clear: no extension knows the
others exist, and there is no deduplication because there is no shared owner.

**Problem 2: No intelligent scheduling.**
Tab A's request — the one the user is actively waiting for — sits in the same queue as Tab B's background request and Tab C's stale request. The coding assistant stutters while the summarizer runs. The browser knows Tab A is foreground and Tab C's tab is already closed, but nothing acts on that knowledge. The models inside each extension have no access to browser context.

**Problem 3: Wasted compute on stale requests.**
The user closed Tab C's source page. The translation request is in the extension's queue. The extension does not know the tab closed — or even if it does, it has no mechanism to propagate that cancellation into its inference pipeline. The model runs the full generation. The response is discarded. That compute is wasted.

### 3.2 Why Existing Solutions Do Not Solve This

**WebLLM + SharedWorker** — the closest existing *open* approach — partially solves Problem 1 for same-origin applications. If Tab A and Tab B are from the same web application, a SharedWorker (or ServiceWorker) can host one shared engine. But:

- A SharedWorker is owned by the application and scoped to a single origin. It cannot coordinate across unrelated extensions or origins — the three-extension scenario above is structurally out of its reach.
- A SharedWorker has no trusted access to browser context. Each client can self-report its own `document.visibilityState`, but the worker cannot verify it, cannot rank all tabs in the browser, and cannot see extension identity.
- A SharedWorker does not receive a reliable signal when a client tab closes. The `beforeunload` event is not guaranteed to fire, so lifecycle cancellation cannot be built on it.

**Chrome Prompt API (Gemini Nano)** — browser-native and close to the shape MALABR studies, but not fully inspectable. Chrome's public Prompt API documentation states that the API uses Gemini Nano, that the model is downloaded separately the first time an origin uses the API, and that extensions are supported from Chrome 138 while web access is documented for Chrome 148. Sessions are supported: `LanguageModel.create()` with `initialPrompts` carries multi-turn context, `session.contextWindow` exposes the current context budget, and `AbortSignal` / `destroy()` can stop session use. Chromium source also exposes the relevant Optimization Guide / On-Device Model Service plumbing. What is missing is everything MALABR wants to study:

- The scheduling policy across concurrent clients (multiple tabs and extensions sharing one model) is unpublished and cannot be varied or instrumented.
- Session lifecycle follows JavaScript object lifetime, not browser tab policy — there is no published notion of visibility-driven priority, cancel-on-tab-close, or queue-wait accounting.
- The model is fixed and vendor-controlled. Current Chrome docs intentionally make model size and context budget version-dependent; observed builds may report values such as 9,216 tokens, but that number should be treated as implementation-specific unless measured in the evaluated browser build. Researchers cannot swap models, measure contention, or test alternative policies.

**Edge Prompt API (Phi-4-mini, with Aion prerelease)** — Microsoft's documentation states the browser-provided model is "downloaded the very first time the API is called and **shared across all websites that run in the browser**." Edge Canary/Dev documentation ties the Prompt API developer preview to version 138.0.3309.2 with Phi-4-mini, and documents a prerelease Aion-1.0-Instruct path starting in Edge 150.0.4070. This is direct product confirmation that browser vendors are converging on browser-managed shared residency — while exposing nothing about how contention between those websites is resolved.

**Firefox AI Runtime** — the most open of the browser-native runtimes, but not a Prompt-API-style shared default LLM. Firefox runs Transformers.js on an in-tree native ONNX runtime (2–10× faster than the WASM path, per Mozilla), exposes an experimental `browser.trial.ml` extension API from Firefox 134 Nightly, caches downloaded models, restricts extension models to blessed hubs/organizations, and currently disallows several extension engines running in parallel to avoid resource conflicts. Its documentation therefore validates browser-managed inference and resource-conflict concerns, but does not publish a multi-client scheduling policy, visibility-aware prioritization, or tab-close cancellation semantics.

**The pattern across browser and OS systems:** production platforms have accepted the broad architectural direction MALABR assumes — local model capability mediated by the browser or operating system rather than by each application alone. Edge explicitly documents a cross-site shared browser model; Chrome exposes a browser-provided Gemini Nano API with session objects and On-Device Model Service plumbing; Firefox exposes browser-managed inference with explicit resource-conflict limits; Apple Foundation Models and Windows Phi Silica expose OS-level local model APIs. None provides an open, instrumentable implementation where the *policies* — scheduling, session lifecycle, cancellation, isolation, and fairness — can be varied and measured. The architecture exists in products; the policy science does not exist in the open.

**The fundamental gap:** No existing open system studies what happens when the browser process itself owns inference orchestration — specifically, how browser context signals should govern scheduling, session lifecycle, and isolation. That is what MALABR investigates.

---

## 4. Research Problem

### 4.1 Formal Statement

> **How should a browser-managed shared inference runtime use browser-context signals — tab visibility, tab lifecycle events, and extension identity — to schedule inference requests, manage isolated per-tab sessions, and enforce cross-session security, and what effect do these policies have on memory utilization, user-perceived latency, wasted compute, and isolation correctness?**

### 4.2 What Makes This a Research Problem

This is not purely an engineering question for two reasons:

**First**, the policies are not obvious. Should a foreground tab always preempt a background tab mid-inference? Or should in-progress requests complete before rescheduling? What is the fairness tradeoff when two foreground tabs compete? When exactly should a queued request be cancelled — immediately on tab close, or only if it has not started yet? These are policy choices with measurable consequences. The right answer is not known without implementation and measurement.

**Second**, the architecture requires browser process integration that no existing open prototype provides. Implementing this inside Chromium — where the process boundary, IPC model, and lifecycle events actually exist — is a non-trivial systems contribution distinct from a userspace simulation.

---

## 5. Research Questions

### RQ1 — Shared Model Residency

**Question:** Does one browser-managed model instance serving N concurrent extension sessions use measurably less memory than N independently loaded application-owned model instances?

**Example:**
- Baseline: 3 extensions, each loads the same Gemma-2B GGUF artifact. Total RSS is measured, not assumed, because it includes weights, KV cache, backend buffers, and allocator overhead.
- MALABR: 1 model loaded by MalabrManager. Sessions A, B, C each hold only their conversation history and per-session metadata in RAM.
- The hypothesis: memory footprint scales with sessions (small), not with model copies (large).

**Why it matters:** This is the core justification for browser-managed shared inference. If the memory saving is negligible, the architecture needs a different justification.

**Measurement:** Peak RSS of MALABR process vs. N independent Python inference processes under equivalent concurrent load. Measured at 1, 3, 5, 8 concurrent sessions.

---

### RQ2 — Visibility-Aware Scheduling

**Question:** Does a foreground-first scheduling policy reduce P95 inference latency for the active foreground tab compared to global FIFO, under concurrent background workload?

**Example:**
```
Timeline:

t=0s   Tab A (foreground) sends: "complete this function..."
t=0s   Tab B (background) sends: "summarize this 5000-word paper..."
t=0s   Tab C (background) sends: "translate this article..."

Global FIFO (arrival order): B arrives 5ms before A → B runs first
Foreground-first:             A is foreground → A runs first

Result (illustrative):
FIFO:    Tab A response arrives at t=8.2s  (waited behind B)
VIS:     Tab A response arrives at t=1.1s  (went first)
```

**Why it matters:** The user is actively waiting for Tab A. Tabs B and C are unattended. The browser knows this. The question is whether acting on it produces a meaningful improvement, and how much throughput Tab B and C sacrifice.

**Measurement:** P50/P95 latency per session tier (foreground / background / hidden) under three policies:
- Policy A: Global FIFO
- Policy B: Visibility-first (foreground queue drains before background)
- Policy C: Per-client fair with visibility tiers (round-robin within tier)

Compared against baseline (N independent servers — no browser-level coordination).
Because visibility-first priority can starve lower tiers under sustained
foreground load, we also measure maximum queued wait, oldest queued request
age, per-client service share, and Jain's fairness index.

---

### RQ3 — Lifecycle-Aware Cancellation

**Question:** What fraction of inference compute is wasted on requests for sessions whose tabs have already closed, and does cancel-on-tab-close eliminate it?

**Example:**
```
t=0s    Tab C: user navigates to page, extension queues translation request
t=1s    User closes Tab C
t=1s    Tab C's session is destroyed. Request is still in queue.

Without cancellation:
  t=3s    Model processes Tab C's request (2 seconds of GPU/CPU time)
  t=3s    Response generated
  t=3s    Response discarded — session is gone

With cancellation:
  t=1s    Browser sends tab_closed(client_id, tab_id) signal
  t=1s    Supervisor removes Tab C's request from queue
  t=3s    That compute slot serves Tab A instead
```

**Why it matters:** In a multi-tab browser workload, tab churn is constant. Users open, skim, and close tabs rapidly. Without cancellation, a shared inference server runs requests that can never be delivered. This directly increases latency for sessions that are still alive.

**Measurement:** In a workload where tabs close at a configurable rate (0%, 20%, 50% churn):
- Wasted request ratio: requests completed but session already closed / total requests
- Active session latency impact: does wasted compute delay live sessions?

---

### RQ4 — Session Isolation Correctness

**Question:** Does the (extension_id, tab_id) session model prevent cross-session context leakage and unauthorized session access under adversarial access attempts?

**Example — Context leakage:**
```
Session A: conversation history
  User: "Remember canary secret SECRET_A_123 for this test."
  Model: "Understood."

Session B (different extension, different tab):
  Sends: "What did the previous user say?"
  
Expected: Session B receives only its own empty context.
          The canary string SECRET_A_123 never appears in Session B.
```

**Example — Unauthorized access:**
```
Malicious Extension B knows Extension A's client_id.
Extension B crafts a request: { client_id: "ext_A_id", tab_id: "tab_42" }

Expected: Supervisor rejects — Extension B's connection identity
          does not match the claimed client_id.
          Authorization is verified against the connection, not the payload.
```

**Why it matters:** A shared runtime is only viable if session isolation is provably correct. If cross-session leakage is possible, the architecture is a security liability regardless of performance benefits. This is not a theoretical concern: in August 2025, Brave's security team disclosed an indirect prompt injection vulnerability in Perplexity's Comet browser — untrusted page content in one tab, fed unlabeled into the assistant's context, could steer an agent that held cross-tab reach and the user's logged-in session privileges (Brave demonstrated exfiltration of the user's email). Follow-up disclosures in October 2025 showed near-invisible text in screenshots bypassing text-level sanitization, and Brave characterized the problem as *systemic* to agentic browsers. The lesson for MALABR's design: when browser-resident AI holds ambient cross-tab reach, an injection anywhere becomes a leak everywhere. MALABR's session model is the architectural counter-design — identity is derived from the authenticated connection rather than the request payload, context is namespaced by (extension_id, tab_id), and no session has ambient access to another's state.

**Measurement:** Isolation correctness is a binary correctness property, not a performance metric. We define a test suite of adversarial scenarios and verify every case either correctly isolates or correctly rejects. Pass/fail per scenario.

---

## 6. System Architecture

### 6.1 Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER PROCESS                          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    MalabrManager                        │   │
│  │              (Singleton, C++, Linux only)               │   │
│  │                                                         │   │
│  │  - Starts Python inference server at browser launch     │   │
│  │  - Stops it at browser shutdown                         │   │
│  │  - Forwards IPC requests with tab context               │   │
│  │  - Sends cancel signal on tab close                     │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                          │ Unix Domain Socket                   │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│              PYTHON INFERENCE SERVER                            │
│                          │                                      │
│  ┌───────────────────────▼─────────────────────────────────┐   │
│  │                    Supervisor                           │   │
│  │                                                         │   │
│  │  ┌─────────────────────────────────────────────────┐   │   │
│  │  │  Visibility-Aware Scheduler                     │   │   │
│  │  │                                                 │   │   │
│  │  │  FOREGROUND queue  ←── Tab A (active user)     │   │   │
│  │  │  BACKGROUND queue  ←── Tab B (summarizer)      │   │   │
│  │  │  HIDDEN queue      ←── Tab C (translation)     │   │   │
│  │  └────────────────────────┬────────────────────────┘   │   │
│  │                           │                             │   │
│  │  ┌────────────────────────▼────────────────────────┐   │   │
│  │  │  Session Registry                               │   │   │
│  │  │                                                 │   │   │
│  │  │  (ext_id_A, tab_1) → Session A                 │   │   │
│  │  │     history: ["user: ...", "model: ..."]       │   │   │
│  │  │                                                 │   │   │
│  │  │  (ext_id_B, tab_2) → Session B                 │   │   │
│  │  │     history: ["user: ...", "model: ..."]       │   │   │
│  │  │                                                 │   │   │
│  │  │  (ext_id_A, tab_3) → Session C                 │   │   │
│  │  │     history: []  ← closed, being destroyed     │   │   │
│  │  └────────────────────────┬────────────────────────┘   │   │
│  │                           │                             │   │
│  │  ┌────────────────────────▼────────────────────────┐   │   │
│  │  │  Shared LLM (Gemma-2B, loaded once)             │   │   │
│  │  │  One model instance serves all sessions         │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Request Flow

**Normal request (Tab A, foreground):**
```
1. Extension JS calls: chrome.malabr.generate(session_id, "explain this code")
2. Renderer → Chromium IPC → browser process
3. MalabrManager reads: tab_id and trusted browser lifecycle state.
   Foreground means the active visible tab in the focused browser window.
   Background means visible-but-not-active or occluded.
   Hidden means hidden, minimized, discarded, or closing.
4. Sends over UDS: { client_id, tab_id, visibility: "foreground", prompt }
5. Supervisor authorizes client_id
6. Looks up Session A — appends prompt to history
7. Puts request in FOREGROUND queue
8. Scheduler dispatches: FOREGROUND queue drains before BACKGROUND
9. LLM runs: format_prompt(Session A history) → generate tokens → stream
10. Response sent back over UDS → IPC → extension JS
11. Session A history updated with model response
```

**Cancellation flow (Tab C closes):**
```
1. User closes Tab C
2. Chromium notifies our TabStripModelObserver via OnTabStripModelChanged()
   (a kRemoved change with will_be_deleted = true)
3. MalabrManager sends over UDS: { event: "cancel", client_id, tab_id }
4. Supervisor:
   a. Removes Tab C's pending requests from all queues
   b. If Tab C's request is inflight: marks it cancelled, response discarded
   c. Destroys Session C — history cleared from memory
5. Freed queue slot is now available for Tab A or Tab B
```

### 6.3 UDS Protocol (Extension to Server Header)

```
Current header fields:  { route, client_id, payload_size }
New header fields:      { route, client_id, tab_id, tab_visibility, payload_size }

New event type:
{ event: "tab_closed", client_id, tab_id }
```

This is the only message-schema change required. The security design also
requires that `client_id` be bound to the authenticated browser-side connection
rather than accepted from the request payload, and that the UDS path be created
with restrictive filesystem permissions. Those mechanisms are separate from the
wire-format extension above.

### 6.4 Scheduler Design (Three Variants)

**Policy A — Global FIFO (baseline within MALABR)**
```python
self.queue = deque()  # single global queue, FIFO
# Every request appended to tail, regardless of visibility
```

**Policy B — Visibility-First**
```python
self.queues = {
    "foreground": deque(),
    "background": deque(),
    "hidden":     deque(),
}
# Dispatcher: drain foreground entirely, then background, then hidden
def next_request(self):
    for tier in ["foreground", "background", "hidden"]:
        if self.queues[tier]:
            return self.queues[tier].popleft()
```

**Policy C — Per-Client Fair with Visibility Tiers**
```python
# Round-robin across clients within each visibility tier
# Prevents one extension from monopolizing the foreground queue
def next_request(self):
    for tier in ["foreground", "background", "hidden"]:
        client = self.round_robin_next(tier)
        if client:
            return self.client_queues[tier][client].popleft()
```

### 6.5 Scheduling Granularity — What Priority Can and Cannot Do

An honest boundary must be stated up front, because it shapes how results are
interpreted. The runtime has a single execution slot, and dispatch decisions are
made at request boundaries. Two consequences:

**Priority manifests as queue reordering, not execution speedup.** A foreground
request that arrives while a background request is mid-generation still waits for
the in-flight request to finish (head-of-line blocking). Scheduling policy
determines *who goes next*, not how fast anyone runs. We therefore decompose
latency into **dispatch wait** (queued → inference start) and **service time**
(inference start → completion) and report them separately: policy can move only
the first component, and the evaluation must not let service-time noise mask or
inflate the scheduling effect.

**Token-granularity intervention is available, and bounds the blocking.**
`llama-cpp-python` exposes generation as a Python iterator, so the supervisor
regains control between every token. This is what makes in-flight cancellation
cheap (RQ3): a cancel flag is checked at each token boundary, so cancellation
latency is bounded by one token time, not one request time. The same mechanism
would support mid-generation *preemption* (pause a background request when a
foreground one arrives). Preemption is implemented as an optional fourth policy
variant only if time permits — it introduces a KV-cache residency question
(suspended state must be retained or recomputed) that is measurable but not
required for the core claims. The maximum foreground blocking time under
non-preemptive policies (= P95 background service time) is itself reported, since
it quantifies exactly what preemption would buy.

### 6.6 Security Design Principles (Derived from Agentic-Browser Defenses)

The defense architectures published by Google, Brave, and OpenAI after the 2025
Comet disclosures (§9.7) converge on a small set of deterministic principles.
MALABR adopts each one as a concrete mechanism:

| Industry principle | Source | MALABR mechanism |
|---|---|---|
| Trusted metadata and untrusted content must travel in separate channels | Brave; Google's metadata-only Critic | Browser-attached context (client_id, tab_id, visibility) lives in the protocol *header*, written by MalabrManager in the browser process. The prompt is *payload*. The supervisor's trust decisions (authorization, scheduling, session routing) read only the header; payload never influences them. |
| Identity must come from a trusted channel, not from claims in content | Brave; Trail of Bits (CSRF analogy) | client_id is bound to the authenticated UDS connection at connect time. A request claiming another client's identity in its payload fails RQ4's adversarial tests by construction. |
| Same-Origin-Policy analog for AI state | Trail of Bits recommendation | Session state is namespaced by (extension_id, tab_id); no API exists for one session to reference another's context. Cross-session access is not filtered — it is unrepresentable. |
| Least-privilege scoping of what the AI can reach | Google's Agent Origin Sets | The inference server has no browser capabilities at all: it cannot read tabs, DOM, history, or cookies. It receives exactly the prompt the extension sent and the session history that same (extension_id, tab_id) produced. |
| Powerful modes must be isolated and explicit | Brave's separate-profile stance; Atlas logged-out sessions | Within the browser-mediated threat model, only extensions granted the malabr permission can connect; there is no ambient web-page access to the socket. Session state dies with its tab (lifecycle destruction is a *security* property, not only a memory one). |

Two boundaries stated honestly: MALABR does not defend against prompt injection
*within* a single session — a malicious page whose text an extension chooses to
summarize can still influence that session's output. That is the probabilistic
layer (classifiers, alignment critics), explicitly out of scope (§10). And
MALABR's current clients do not take actions (no navigation, clicking, or
sending), so confirmation-gate design — the human-in-the-loop layer every
vendor requires for consequential actions — becomes relevant only in the
future browser-agent extension of this work. What MALABR contributes is the
deterministic substrate both of those layers assume: authenticated identity,
namespaced sessions, and lifecycle-enforced state destruction, implemented and
adversarially tested in the browser process.

### 6.7 Availability and Denial-of-Service Threat Model

§6.6 addresses the *trust-boundary* dimension of security — who can influence
a decision the runtime makes. This section addresses the orthogonal
*availability* dimension — who can exhaust the runtime — since a shared
resource is a natural DoS target that the vendor defenses surveyed in §9.7 do
not directly discuss.

**Threat actors, by reachability.**

1. *Arbitrary web page.* Cannot reach `chrome.malabr.*` at all — the API is
   scoped to `privileged_extension` contexts only, so a page has no direct
   path to the runtime.
2. *A legitimate extension, manipulated by an untrusted page (confused
   deputy).* If an extension holding the `malabr` permission also exposes a
   content-script or page-message surface a malicious page can drive, that
   page can induce the extension to submit inference requests under the
   extension's authority. This threat is better grounded in browser-extension
   security than in prompt-injection alone: the web page controls untrusted
   content, a content script or DOM bridge forwards it, and privileged
   extension code invokes `chrome.malabr.*`. The Comet disclosures illustrate
   the same high-level shape — untrusted content steering a privileged
   browser-resident AI component — but MALABR's actor-2 DoS case is an
   extension confused-deputy problem.
3. *A malicious or compromised installed extension*, deliberately or
   otherwise, submitting requests as fast as possible.
4. *A co-resident local process*, connecting directly to the Unix domain
   socket. UDS access control is filesystem-permission-based, not
   extension-permission-based, so any process running as the same OS user can
   bypass the entire Chromium authorization layer and reach the socket
   directly. This requires local code execution already, but it means the
   extension-permission boundary (§6.6) does not, by itself, bound resource
   consumption.

**What already bounds this, and what does not.** A bounded training queue
converts unbounded queue growth into a fail-fast rejection once full, and
per-worker resource limits (memory and CPU time) bound any single job's
footprint. Neither mechanism is *per-client*: the queue is a shared, global
resource, so one client submitting requests fast enough can consume the
entire budget and starve every other legitimate session — an availability
failure the current design does not prevent by itself. §6.8 introduces the
mechanism that closes this gap.

**The scheduling contribution is also a DoS mitigation, not only a latency
one.** Policy C (§6.4) — per-client fair, round-robin scheduling within a
visibility tier — directly addresses threat actor 3 at dispatch time: no
single client can monopolize a tier's execution order regardless of how fast it
submits requests, because dispatch alternates across clients rather than
draining one client's backlog first. This does not replace admission control:
without per-client queue limits, a noisy client can still fill memory before
fair dispatch helps. Workload 3b's fairness measurement (§8.2) is therefore
evidence for or against resilience to actor-3-style monopolization, not only a
performance number.

**Stated honestly:** threat actor 4 (local-process bypass) is not addressed
by anything in this design — the trust model assumes the local machine's OS
user account itself is not already compromised, consistent with how
browser-adjacent local tooling is typically scoped. This assumption is made
explicit in §10.

### 6.8 Session Resource Governor (Userspace cgroup Analog)

**Why real cgroups do not apply here.** Linux cgroups enforce resource limits
at OS process/thread granularity — the kernel schedules the group's CPU time,
tracks its page allocations, and can freeze or kill it because a process
boundary exists for it to attach to. MALABR has no such boundary at session
granularity by design: RQ1's shared-residency claim depends on *one* process
hosting *one* loaded model, with sessions as in-memory dictionary entries
(`(extension_id, tab_id) → history`), not separate OS processes. The browser
tabs that originate requests do run as separate OS processes (Chromium's
renderer-per-tab model), but those belong to Chromium, not to the inference
server — the Supervisor never sees them, only a UDS connection carrying
`client_id`/`tab_id` metadata. cgroups have nothing to attach to on either
side of this system. Closing §6.7's per-client gap therefore means
reimplementing cgroups' *policy* — quota accounting, throttling, and forced
teardown — in userspace, inside the Supervisor, at session/client
granularity instead of process granularity.

**The mapping.**

| cgroup controller | Kernel-level enforcement | MALABR's userspace analog |
|---|---|---|
| `cpu.max` | X µs of CPU time per Y-ms period | A token-bucket per `client_id`, since MALABR serializes generation onto one execution slot (§6.5): track tokens/service-time consumed in a rolling window, throttle or deprioritize further dispatch once the client's share is exceeded. |
| `memory.max` | Hard RSS cap, OOM-kill on breach | A cap on stored history size/tokens per session; truncate or evict rather than let one session's context grow unbounded. |
| `pids.max` | Cap on process/thread count | A bounded *per-client* queue depth (not only the current global bound, §6.7): reject new submissions from a client once it holds its share of the queue, fail-fast rather than starving others. |
| freezer / kill | Pause or terminate the group | Already covered by RQ3's cancel-on-tab-close — tear down a session's queued/in-flight work on lifecycle destruction. |

Three concrete, additive mechanisms follow from this table: a per-client
token-bucket for dispatch throttling, a per-session history-size cap, and a
per-client queue-depth bound. None require kernel privileges or process
restructuring; all live inside the Supervisor alongside the existing
scheduler and session registry.

**An architectural fork worth stating explicitly, not defaulting into.**
There are two ways to get real per-session resource isolation, and MALABR
deliberately picks the first:
1. **Soft/userspace governance** (above) — preserves the one-process,
   one-shared-model architecture and with it RQ1's memory-residency claim.
   Enforcement is application-level, not kernel-guaranteed.
2. **Real cgroups via process-per-tenant** — spawn a worker process per
   active client and place each in its own cgroup for kernel-enforced limits.
   This buys real enforcement guarantees but reintroduces N processes instead
   of one, undermining RQ1 unless model weights are shared read-only across
   workers (e.g. via `mmap`), which adds its own complexity.

MALABR adopts (1) as the default because the shared-residency claim is its
central contribution; (2) is recorded here as a considered and rejected
alternative rather than left unstated.

**Relationship to RQ4.** This mechanism is orthogonal to RQ4, not a
restatement of it. RQ4 and the `SessionRegistry`/connection-bound-identity
design (§6.2, §6.6) answer *"can session B read or access session A's
data?"* — a correctness property, tested adversarially. §6.8 answers *"can
session B exhaust the resources session A needs?"* — an availability
property, measured under load (Workload 3b, §8.2) rather than pass/fail
tested. Building session isolation should start with the RQ4 mechanism,
since it requires no throttling/quota machinery; §6.8 is the natural
follow-on once basic isolation is in place.

---

## 7. Proof of Concept (POC)

### 7.1 What the POC Demonstrates

The POC is a runnable demonstration of the complete system. It shows:

1. One LLM loaded once, serving three concurrent extension sessions simultaneously
2. Foreground session getting visibly lower latency than background sessions
3. A closing tab's queued request being cancelled before execution
4. Extension B's attempt to access Extension A's session being rejected

### 7.2 POC Setup

**Model:** Gemma-2B-IT (instruction-tuned) via `llama-cpp-python`, 4-bit quantized GGUF format.
- RAM: measured from the selected GGUF artifact and runtime RSS during experiments
- CPU inference: ~3–8 tokens/sec on a modern 8-core CPU
- Download: artifact-dependent, approximately the GGUF file size one time
- The architecture is model-agnostic: Gemma 3 1B-IT (QAT GGUF, ~0.8GB) or
  Qwen3-1.7B are drop-in alternatives if faster CPU decoding is needed for the
  contention experiments; all numbers in this document use Gemma-2B.

**Three test extensions (simulated by scripts):**
```
Extension A (ext_id: "aaa..."):
  Tab 1 — foreground, coding assistant
  Sends:  "Write a Python function to sort a list by second element"

Extension B (ext_id: "bbb..."):
  Tab 2 — background, PDF summarizer
  Sends:  "Summarize: [500-word text block]"

Extension A (ext_id: "aaa..."):
  Tab 3 — background, then closes mid-request
  Sends:  "Translate: [200-word paragraph]"
  Closes after 1 second
```

### 7.3 POC Expected Output

```
[MALABR Server] Model loaded: gemma-2b-it.Q4_K_M.gguf (1.47 GB RAM)
[MALABR Server] Sessions active: 0

[t=0.0s] Session created: (aaa, tab_1) visibility=FOREGROUND
[t=0.0s] Session created: (bbb, tab_2) visibility=BACKGROUND
[t=0.0s] Session created: (aaa, tab_3) visibility=BACKGROUND
[t=0.0s] Sessions active: 3

[t=0.1s] Request queued: (aaa, tab_1) [FOREGROUND] "Write a Python function..."
[t=0.1s] Request queued: (bbb, tab_2) [BACKGROUND] "Summarize: Lorem ipsum..."
[t=0.1s] Request queued: (aaa, tab_3) [BACKGROUND] "Translate: ..."

[t=0.1s] Scheduler: dispatching (aaa, tab_1) [FOREGROUND priority]
[t=0.1s] Inference start: Session (aaa, tab_1)

[t=1.0s] tab_closed received: (aaa, tab_3)
[t=1.0s] Cancelled 1 queued request for (aaa, tab_3)
[t=1.0s] Session destroyed: (aaa, tab_3) — history cleared

[t=3.2s] Inference complete: (aaa, tab_1) — 2.8s latency
[t=3.2s] Streaming response to Tab 1: "def sort_by_second(lst): ..."

[t=3.2s] Scheduler: dispatching (bbb, tab_2) [BACKGROUND]
[t=3.2s] Inference start: Session (bbb, tab_2)

[t=8.9s] Inference complete: (bbb, tab_2) — 5.7s latency (background, acceptable)

[MALABR Server] Stats:
  Requests completed:  2
  Requests cancelled:  1  (33% wasted compute avoided)
  Foreground P95:      3.2s
  Background P95:      8.9s
  Model RAM:           1.47 GB (vs. 4.41 GB for 3 independent instances)
```

### 7.4 POC Isolation Demonstration

```
[t=5.0s] Unauthorized access attempt:
  Extension B tries to query Session (aaa, tab_1) using A's session_id.
  Request header: { client_id: "bbb", tab_id: "tab_1_of_aaa" }

[MALABR Server] Authorization check:
  client_id "bbb" does not own session (aaa, tab_1)
  → REJECTED: 403 unauthorized

[t=5.0s] Session (aaa, tab_1) history: unchanged, not exposed to bbb
```

### 7.5 Baseline for Comparison

The baseline is three independent Python inference servers, each loaded with their own Gemma-2B model, connected directly via separate UDS sockets. No shared scheduling, no cancellation, no session registry. This represents the application-owned local-AI baseline for residency and lifecycle comparison.

One caveat matters for latency interpretation: independent servers may execute
in parallel on different CPU cores, while the core MALABR design intentionally
uses one shared execution slot. The baseline is therefore primarily a residency
and coordination baseline, not a claim that a single-slot shared runtime always
dominates independent per-extension runtimes on raw latency. Latency results
are reported with CPU utilization and service time separated from dispatch
wait.

```
Baseline:
  3 × llama-cpp-python process, each loading Gemma-2B
  RAM: 3 × 1.47 GB = 4.41 GB
  Tab 3's request: executes fully even after tab closes (wasted)
  Tab 1 latency: competes with Tab 2 and Tab 3 equally
```

---

## 8. Evaluation Plan

### 8.1 Metrics

| Metric | Description | Unit |
|---|---|---|
| Peak RSS | RAM used by inference runtime | MB |
| Foreground P50/P95 latency | Request latency for foreground sessions | seconds |
| Background P50/P95 latency | Request latency for background sessions | seconds |
| Dispatch wait (per tier) | Enqueue → inference start; the component scheduling can move (§6.5) | seconds |
| Service time (per tier) | Inference start → completion; policy-independent control | seconds |
| Wasted compute ratio | Requests executed for dead sessions / total | % |
| Wasted service time | CPU/GPU seconds spent on dead-session requests, including mid-generation cancellation | seconds |
| Wasted output tokens | Generated tokens for requests whose session was already dead | tokens |
| Cancelled request ratio | Requests cancelled before execution / dead session requests | % |
| Max queued wait | Longest enqueue → inference-start wait per tier/client | seconds |
| Jain's fairness index | Fairness of service share across clients under saturation | 0–1 |
| Isolation pass rate | Adversarial isolation test cases passed | pass/fail count |

### 8.2 Workloads

**Workload 1 — Steady state (3 concurrent sessions)**
1 foreground + 2 background. All sending requests at 1 request every 5 seconds.
Duration: 5 minutes. Measures: latency distribution per tier, throughput.

**Workload 2 — Tab churn (sessions opening and closing)**
Sessions open and close at 20-second intervals. New sessions connect, old ones disconnect.
Measures: wasted compute ratio with/without cancellation, session cleanup correctness.

**Workload 3a — Burst (all foreground simultaneously)**
5 sessions all marked foreground, all sending requests simultaneously.
Measures: per-client fairness and maximum latency under saturation.

**Workload 3b — Noisy client monopolization**
5 sessions all marked foreground. One session sends at a much higher request
rate than the other four. Measures: per-client service share, maximum queued
wait, Jain's fairness index, and whether Policy C prevents actor-3-style
monopolization (§6.7).

**Workload 4 — Memory scaling**
Increase concurrent sessions from 1 to 10. Measure peak RSS vs. 1–10 independent instances.
Measures: memory savings from shared residency as a function of session count.

### 8.3 Comparison Matrix

| Policy | Description | Expected strength |
|---|---|---|
| Baseline (independent) | N separate servers, no coordination | Reference |
| MALABR-FIFO | Shared server, global FIFO | Shows shared residency benefit; no scheduling benefit |
| MALABR-VIS | Shared server, foreground-first | Shows scheduling benefit on foreground latency |
| MALABR-VIS+FAIR | Shared server, visibility tiers + per-client fair scheduling | Shows starvation resistance within a tier |
| MALABR-VIS+CANCEL | Shared server, foreground-first + cancellation | Shows combined benefit: latency + wasted compute |

---

## 9. Related Work Positioning

### 9.1 Browser-Native and OS-Native Runtimes: Product Evidence, Policy Opacity

The platform evidence for MALABR's premise is now strong, but the exact product
claims are versioned and should not be treated as stable constants. Chrome's
Prompt API documentation states that the API uses Gemini Nano, that the model
is downloaded separately the first time an origin uses the API, that extension
support is documented from Chrome 138, and that web support is documented from
Chrome 148. It exposes sessions, `initialPrompts`, `session.contextWindow`,
`clone()`, `destroy()`, and `AbortSignal` cancellation. Chromium source places
the underlying implementation in the Optimization Guide / On-Device Model
Service area. What is not publicly specified is a cross-client scheduling
policy, queue accounting, or a stable context-window number: observed builds
may report values such as 9,216 tokens, but Chrome's public docs make the
model and its budget version-dependent.

Edge is the cleanest browser-native citation for shared residency. Microsoft's
Prompt API documentation says the browser-provided model is downloaded on first
use and "shared across all websites that run in the browser." Edge Canary/Dev
documentation ties the developer preview to version 138.0.3309.2 with
Phi-4-mini, and documents a prerelease Aion-1.0-Instruct path starting in Edge
150.0.4070. This supports MALABR's architectural claim directly while also
showing how quickly product details change.

Firefox AI Runtime is more open but not the same architecture. Firefox source
docs describe `browser.trial.ml` for extensions from Firefox 134 Nightly, model
downloads cached in OPFS, blessed model organizations, and a current limitation
that extensions cannot run several engines in parallel to avoid resource
conflicts. Mozilla's AI Runtime work therefore validates browser-managed local
inference and resource-conflict concerns, but not a Prompt-API-style single
default LLM shared across all clients.

Operating systems show the same migration at a different layer. Apple
Foundation Models exposes `LanguageModelSession`, `SystemLanguageModel`,
`contextSize`, `tokenCount(for:)`, and OS-versioned model updates; Apple's 2026
model report describes AFM 3 Core as a 3B on-device dense model. Windows Phi
Silica exposes local text generation through Windows AI APIs, is optimized for
Copilot+ NPUs, supports speculative decoding and content filtering, and can be
unloaded under memory pressure. Microsoft also documents a 2026 transition plan
from Phi Silica toward Aion Instruct. These OS systems validate local model
services with per-app/session APIs, but do not publish contention policies.

**Gap statement:** Chrome, Edge, Firefox, Apple, and Windows validate the
direction: local model capability is increasingly mediated by the browser or
OS rather than independently by each application. What none of them provides is
an open, instrumentable policy surface for cross-client scheduling, lifecycle
cancellation, fairness, or session isolation.

### 9.2 In-Browser Inference Engines: Open Execution, Not Browser Orchestration

WebLLM (arXiv:2412.15803) established performant WebGPU LLM inference, with
SharedWorker sharing limited to one origin. WeInfer (WWW '25) improved WebGPU
buffer lifecycle management and asynchronous pipelining for up to 3.76x faster
decoding over WebLLM. LlamaWeb ("Llamas on the Web," arXiv:2605.20706) brings
llama.cpp to WebGPU with static memory planning (29-33% less memory, 45-69%
higher decode throughput). A 2026 characterization study (arXiv:2604.02344)
quantifies WebGPU dispatch overhead across vendors, backends, and browsers.

This line of work makes single-client execution fast. It is orthogonal and
complementary to MALABR, which studies multi-client orchestration. Any of these
engines could serve as a future MALABR backend; none decides which tab goes
first, which extension receives fairness protection, or which queued request
dies when its tab closes.

### 9.3 OS-Level Model Services and Mobile LLM-as-a-Service

Apple Foundation Models and Windows Phi Silica are the strongest product
analogies because the platform, not the application, owns model access. Both
expose session-like APIs and local execution benefits, but both are closed with
respect to scheduling and contention. The academic counterpart is ELMS
(MobiCom '25, arXiv:2409.09071), which studies mobile "LLM-as-a-Service" with
per-app latency SLOs through elastic sub-models. ELMS is valuable because it
frames on-device inference as a shared service, but it does not focus on
browser-style tab/extension concurrency, trusted visibility signals, or
tab-close lifecycle cancellation.

The browser is precisely the environment where the mobile assumption weakens:
many tabs and extensions can be active simultaneously, and lifecycle churn
(open, close, hide, restore, discard) is constant. MALABR studies the regime
that OS/mobile systems either hide behind closed policies or leave outside the
core scheduling problem.

### 9.4 Datacenter LLM Serving: Fairness Exists, But Browser Signals Do Not

The current datacenter literature is deeper than simple FIFO. vLLM (SOSP '23)
made PagedAttention and continuous batching practical; Orca introduced
iteration-level scheduling for generative models; SGLang (NeurIPS '24) added a
programmable serving runtime with efficient cache reuse. FastServe studies
token-level preemptive scheduling with a multi-level feedback queue and KV
state movement, making it directly relevant to MALABR's optional preemption
boundary.

Recent work studies fairness and starvation explicitly. FairServe ("Ensuring
Fair LLM Serving Amid Diverse Applications," arXiv:2411.15997) analyzes
millions of Microsoft Copilot requests and treats excessive users as a
multi-tenant fairness and availability problem; it combines
application-characteristic-aware throttling with weighted service-counter
scheduling. "Is the GPU Half-Empty or Half-Full?" (arXiv:2410.17840) surveys
practical LLM scheduling in vLLM, TensorRT-LLM, and SGLang and proposes LARRY
and SAL as lightweight scheduling and load-balancing policies; it explicitly
notes that naive preference for short work can starve long requests unless
wait time and load are included. CoLoRA (ASP-DAC 2026) targets multi-tenant
LoRA serving with adaptive priority scheduling, adapter-aware cache management,
load-aware batching, and tenant-level fairness. H-MAS (ACL Findings 2026)
adapts multi-tenant LLM scheduling under non-stationary MaaS workloads.
Continuum (arXiv:2511.02230) studies multi-turn agent scheduling and KV-cache
time-to-live for workloads that pause between turns.

**Gap statement:** MALABR does not claim to invent LLM-serving fairness in
general. Its novelty is browser-contextual fairness: trusted visibility tiers,
extension identity, per-tab session state, and tab death are scheduling inputs
that server-side LLM schedulers do not receive.

### 9.5 Resource Exhaustion and Denial-of-Service in Shared Inference

Shared inference is also an availability target. OWASP LLM10:2025 names
"Unbounded Consumption" as a risk class covering excessive inference,
denial-of-service, cost exhaustion, and service degradation. Industry systems
operationalize the same concern with throttling and quota isolation: OCI Model
Inference documents 429 throttling to protect model serving from overload and
DoS; AWS guidance for Bedrock gateways recommends per-tenant quotas; and
DigitalOcean's serverless inference architecture describes per-account and
per-model rate limits to prevent single-tenant resource exhaustion.

Academic work is emerging but mostly cloud-serving-centric. FairServe treats
excessive users in a multi-tenant LLM platform as a fairness and availability
problem. "Rethinking Latency Denial-of-Service: Attacking the LLM Serving
Framework, Not the Model" (arXiv:2602.07878) studies scheduler/KV-cache-level
latency DoS against modern serving frameworks. Prompt-induced over-generation
work (arXiv:2512.23779) studies black-box prompts that force long continuations
and inflate latency/cost. A 2026 vLLM advisory (CVE-2026-34756) illustrates a
concrete serving-layer resource-exhaustion class caused by an unbounded
OpenAI-compatible `n` parameter.

**Gap statement:** There appears to be little direct academic work on DoS
against browser-managed shared local inference runtimes or extension-only AI
APIs. The best-supported bridge is: shared inference endpoints need per-tenant
quotas and fair scheduling; LLM-specific systems add token, output-length, and
KV-cache exhaustion; browser extensions add a confused deputy path from
untrusted pages to privileged extension APIs.

### 9.6 Browser Extension Security and Confused Deputies

The extension-security literature strongly supports MALABR's threat actor 2.
Chrome's extension architecture separates lower-privilege content scripts from
higher-privilege background/service-worker code, but message passing bridges
that boundary. Carlini, Felt, and Wagner's USENIX Security 2012 evaluation of
Chrome extensions found 70 vulnerabilities across 40 extensions and concluded
that isolation and permissions helped but did not eliminate developer-created
vulnerabilities. Kim and Lee's USENIX Security 2023 "Extending a Hand to
Attackers" found 59 vulnerabilities in 40 extensions and demonstrated
webpage-to-extension privilege escalation, including UXSS and theft of
passwords or cryptocurrency; their FistBump proposal strengthens process
isolation between webpages and content scripts.

Platform guidance says the same thing operationally. MDN warns that extensions
are privileged code and hostile web pages can trick them into exercising those
capabilities. OWASP's Browser Extension Vulnerabilities Cheat Sheet calls out
insecure message passing: if a service worker fails to validate sender origin
or URL, a compromised webpage can trick the extension into privileged actions.

**Gap statement:** MALABR's confused-deputy DoS story is grounded in extension
messaging literature, not only agentic-browser prompt injection. The chain is:
webpage controls input; content script or DOM bridge forwards it; privileged
extension background code invokes `chrome.malabr.*`; the shared runtime
consumes resources under the extension's identity. MALABR's defenses are
trusted identity derivation, per-client scheduling fairness, lifecycle
cancellation, and eventually per-client admission limits.

### 9.7 Agentic Browser Defenses and Session/Context Isolation

The Comet disclosures and subsequent defenses remain important motivation, but
they should be read as evidence for deterministic browser-enforced boundaries,
not as direct evidence for inference DoS. Google, Brave, and OpenAI converge on
the same root diagnosis: LLMs cannot reliably distinguish data from
instructions, so security decisions must be enforced around the model. Google's
published Gemini-in-Chrome architecture separates untrusted web content from a
metadata-only critic, restricts agents with origin sets, requires user
confirmation for sensitive actions, and uses prompt-injection classifiers.
Brave recommends strict separation of user instructions from page content,
alignment checking isolated from untrusted input, confirmation gates, and
separate browsing profiles. OpenAI's Atlas hardening combines adversarially
trained models with structural containment: logged-out agent sessions,
per-tab isolation, sensitive-site pauses, and restrictions on code execution,
downloads, and app access.

Research generalizes this lesson. CaMeL (arXiv:2503.18813) separates a
privileged planner from a quarantined reader and tracks capabilities outside
the LLM. IsolateGPT (arXiv:2403.04960) frames LLM app ecosystems as repeating
earlier platform-isolation failures. Agent Security Bench (ICLR 2025)
formalizes attacks on agents across tool use, memory retrieval, and prompt
handling. Capability-oriented systems such as "Securing Agents With Tracked
Capabilities" (ACM AI and Agentic Systems 2026) use object-capability and
type-system reasoning to prevent classified information from crossing trust
boundaries, with an explicit requirement that the LLM service itself not retain
inputs as context for later queries. AWS and Azure multi-tenant agent/RAG
guidance similarly treats tenant isolation as spanning identity, tool
credentials, vector stores, orchestration context, and noisy-neighbor controls.

**Gap statement:** These systems generalize MALABR's Same-Origin-Policy-for-
sessions intuition: isolation must be enforced around the model because the
model cannot reliably enforce it inside generated text. MALABR contributes a
concrete browser-process instance: context is keyed by `(extension_id, tab_id)`,
identity is browser-derived, and lifecycle events destroy state.

### 9.8 Standards and the Agentic Trajectory

WebNN reached an updated W3C Candidate Recommendation in January 2026, but it
remains an execution standard for graph computation on CPU/GPU/NPU, not an
orchestration layer. Standardized execution makes MALABR's problem more urgent
because it increases the number of plausible in-browser AI clients; it does not
answer who schedules them, who owns shared residency, or how browser lifecycle
events affect inference queues.

Browser-mediated agents and built-in AI APIs are becoming product priorities
across vendors, but those product systems are volatile and closed. MALABR
therefore avoids depending on any one vendor roadmap. The stable research claim
is narrower: as browsers and operating systems expose local model APIs,
orchestration, session isolation, fairness, and lifecycle cleanup become
browser/OS systems problems rather than application-only problems.

### 9.9 Comparison Matrix

| System | Shared Residency | System Level | Scheduling Studied | Session / Context Isolation | Lifecycle Signals | Open |
|---|---|---|---|---|---|---|
| WebLLM + SharedWorker | Same-origin only | Page/origin | No | Origin/application-managed | Worker/client lifetime | Yes |
| WeInfer / LlamaWeb | No (single-client engine) | Page | No (execution optimization) | Application-managed | Page-local | Yes |
| Chrome Prompt API | Browser-provided Gemini Nano; exact sharing policy not fully public | Browser | Not published | Session object; API availability scoped by document/permission policy | JS abort/destroy; no published tab-close policy | No |
| Edge Prompt API | Yes, explicitly documented as shared across websites | Browser | Not published | Session object | JS abort/destroy; no published tab-close policy | No |
| Firefox AI Runtime | Runtime/cache with no-parallel-engine limit for extensions | Browser | Not published | Extension/API-managed engine state | Engine deletion under memory pressure; no published tab-close policy | Partial |
| Apple FM / Phi Silica | OS-provided local model APIs | OS | Not published | Per-app/session API surface | App/OS lifecycle; policy closed | No |
| ELMS (MobiCom '25) | Shared mobile service | Mobile OS | SLO elasticity | App-level service abstraction | Not browser lifecycle | Yes |
| vLLM / SGLang / Orca | Shared server instance | Datacenter | Yes | Request/tenant level | No browser lifecycle | Yes |
| FairServe / CoLoRA / H-MAS | Shared serving platform | Datacenter | Yes, fairness/QoS | Tenant/application level | No browser lifecycle | Partial |
| Agentic browser defenses | Product-specific | Browser | No | Profile/tab/origin containment | Product-specific | No |
| **MALABR** | **Yes, one local model loaded once** | **Browser process + local server** | **Yes, FIFO / VIS / VIS+FAIR / cancellation** | **(extension_id, tab_id), adversarially tested** | **Visibility + tab close** | **Yes** |

**Gap statement:** Closed products have the architecture without the science;
open in-browser inference research has execution without browser orchestration;
datacenter serving has fairness scheduling without trusted browser lifecycle
signals; agentic-browser security work has isolation principles without a
measurable shared local inference runtime. No open research prototype combines
browser-process ownership, shared local model residency, explicit scheduling-
policy comparison, lifecycle cancellation, and adversarial session-isolation
tests in one system. MALABR targets that gap.

---

## 10. Scope Boundaries

### In Scope
- One model (Gemma-2B or TinyLlama) loaded and shared across sessions
- Three scheduling policies implemented and measured
- (extension_id, tab_id) session isolation with lifecycle cleanup
- Streaming token generation per session
- Cancel-on-tab-close signal from Chromium
- Linux only (UDS requires POSIX)
- Measurement harness and evaluation

### Explicitly Out of Scope
- PagedAttention / KV-cache paging — cloud-scale complexity, different device constraints
- Multi-model serving — one model is the contribution
- GPU scheduling / WebGPU integration — MALABR does not study WebGPU-backed
  in-page inference or cross-origin WebGPU model sharing
- Continuous batching — valid optimization, not the research question
- Windows / macOS — scope to Linux explicitly for the prototype
- Prompt injection / adversarial inputs — separate threat model
- Timing side-channel attacks — requires formal security analysis beyond this work
- Local-process / same-machine trust (§6.7) — MALABR assumes the OS user account itself is not already compromised; defending against a co-resident malicious process with direct UDS access is out of scope

### 10.1 Future Extension (Post-MTP, Not in Current Scope): Visibility-Driven Edge-Cloud Split Inference

Explicitly deferred to future work, not part of this MTP's implementation plan or
timeline (§12): extending MALABR's browser-context signals to drive *placement*,
not only *scheduling order*.

**The idea.** Edge-cloud collaborative inference (as posed in the ICPC Online
Challenge powered by Huawei, Codeforces blog entry 155646, Aug 2026) splits a
transformer in U-shape — the first and last layers run on-device, the
computationally heavy middle layers run in the cloud — so that raw inputs and
outputs never leave the user's machine while the cloud absorbs the expensive
compute. This is directly relevant to MALABR because the placement decision
(local-only vs. split-to-cloud) is exactly the kind of policy MALABR's
architecture already exists to make: `tab_visibility` and session lifecycle
could decide *where* a request executes, not just when it is dispatched.
Concretely — foreground sessions would stay fully local (lowest latency, full
privacy, consistent with the current design); background/hidden sessions,
already deprioritized by Policy B/C, would become candidates for split
execution, trading some privacy exposure and network latency for freed local
compute capacity. This reframes visibility as an elastic-capacity signal in
the spirit of ELMS (§9.3), with MALABR contributing the browser-trust layer
ELMS lacks.

**Why it is out of scope now, not merely lower priority.** This is a second
implementation surface, not an add-on phase:
- The current runtime (`llama-cpp-python` over a GGUF-quantized model, §7.2)
  executes `generate()` as one opaque call. Splitting at a layer boundary
  requires a runtime that can pause the forward pass, serialize the
  intermediate hidden state, and resume from a cloud-returned tensor —
  realistically a different inference stack (e.g. HF `transformers` with
  forward hooks), which gives up the CPU-quantization performance story this
  MTP is built on.
- A cloud-side counterpart process must run the middle layers, with its own
  scheduling and multi-tenant isolation — a second server, not a client
  change.
- Per-token round trips during decode reintroduce exactly the "bubble" problem
  the Huawei challenge names, and would fold into MALABR's service-time model
  (§6.5) rather than its dispatch-wait model — a materially different
  measurement design.
- Cancellation (RQ3) would need to tear down cloud-side state on tab close,
  not just local queue entries; isolation (RQ4) would need to extend its
  adversarial test suite across a network boundary instead of a single
  browser-process trust boundary.

**Why the privacy claim needs qualification if this is ever built — and where
that claim actually comes from.** "Only the first and last layers run
on-device, so data never leaves the device" is true only at the raw-token
level, and it is worth being precise about its source: it is motivation
prose from the challenge *announcement*, not a property the graded problem
itself tests. The problem statement (Codeforces contest 2251, Problem A,
"Edge–Cloud Collaborative Scheduling") is explicit that "No AI background is
needed" and treats the split boundary purely as a scheduling abstraction —
`activations` is just a byte-size field feeding a generic transfer-time
formula; the scorer never reasons about what those bytes reveal. So this
citation supports the *scheduling* half of a future MALABR extension, not the
privacy half. If MALABR ever builds real layer-split inference, the privacy
property still needs to be argued independently: split-inference/split-
learning literature has repeatedly shown that intermediate activations can be
partially inverted toward the original input, particularly at shallow split
depths (model inversion / feature-leakage attacks on split learning). Any
future version of this idea must state the privacy property as *reduced
exposure relative to sending raw text*, not as a leakage-proof guarantee.

**Reusable formalisms from the ICPC problem, independent of the cloud-split
question.** Even without committing to layer splitting, this problem's
scheduling formalism is worth borrowing for MALABR's own local-only
measurement design (§8):
- **TDR** (time-to-decode-ready: arrival → first-token-eligible) and **TPOT**
  (time-per-output-token: mean gap between produced tokens) are cleaner named
  metrics than MALABR currently defines, and map closely onto the
  dispatch-wait / service-time split in §6.5 — worth adopting as terminology
  in §8.1's metrics table.
- Its transfer-time model, `latency_in_ms + 8·bytes/(bandwidth_gbps·10^6)`, is
  a ready-made cost function for §10.1's cloud-offload extension if it is
  ever built — no need to invent one from scratch.
- Its piecewise-linear task-time-by-batch-size lookup is a reusable way to
  model how group/batch size affects service time — applicable to MALABR's
  own Policy C batching even without any cloud component.
- Its clamp-based composite score,
  `score = w_tp·clamp(throughput; base, UB) + w_c·clamp(SLO_distance; base, 0)`,
  is a good template for §8.3: a single weighted, normalized number to rank
  Policies A–D, exposing the throughput-vs-fairness tradeoff as one tunable
  weight instead of only a table of separate metrics.
- Its reactive event-frame loop (`ARR/TDN/XDN/FIN` → update state → respond)
  is a clean pattern for the Phase 5 measurement harness (§12) regardless of
  whether cloud offload is ever implemented.

---

## 11. Contributions

1. **MALABR system** — first open Chromium-based prototype where the browser process manages a shared inference runtime across multiple concurrent extension clients.

2. **Scheduling policy study** — empirical comparison of browser-context scheduling policies — global FIFO, visibility-aware, and per-client fair scheduling — for browser-managed local inference under realistic multi-session workloads.

3. **Lifecycle-aware cancellation** — design and measurement of cancel-on-tab-close for inference requests, quantifying wasted compute reduction in tab-churn workloads.

4. **Session isolation model** — implementation and adversarial correctness testing of (extension_id, tab_id)-keyed session isolation in a shared inference server, motivated by real-world failures in deployed agentic browsers.

5. **Measurement baseline** — reproducible comparison methodology between browser-managed shared inference and application-owned independent inference, usable by future work in this space.

---

## 12. Implementation Plan

### What is already built
- `MalabrManager` singleton in browser process (C++) — starts/stops server
- `chrome.malabr.*` extension API surface — IPC from renderer to browser process
- Python Supervisor with queue, inference pool, authorization
- Unix Domain Socket protocol with FlatBuffers framing
- Extension-ID authorization

### What needs to be built

**Phase 1 — Model swap (1 week)**
Replace sklearn with llama-cpp-python. Load Gemma-2B once at startup. Implement `generate(session_id, prompt)` → streamed text output. Remove `fit/predict/score` from the API.

**Phase 2 — Session model (1 week)**
Implement `SessionRegistry` keyed on (client_id, tab_id). Each session holds conversation history list. Generate call formats history into prompt before inference. Session destroyed on tab_closed event.

**Phase 3 — Chromium protocol changes (3–4 days)**
Add `tab_id` and `tab_visibility` to UDS protocol header. Read `WebContents::GetVisibility()` in browser process. Register `TabStripModelObserver` to send tab_closed signal.

**Phase 4 — Scheduler variants (1 week)**
Implement FIFO, visibility-first, and per-client fair schedulers. Config flag to switch between them at startup.

**Phase 5 — Measurement harness (1 week)**
Script that simulates N concurrent extension clients with configurable visibility states, request rates, and tab churn. Logs per-request timestamps for latency computation.

**Phase 6 — Security and fairness tests (3–4 days)**
Add adversarial tests for connection-bound identity, payload-forged identity,
cross-session canary leakage, and noisy-client monopolization.

**Phase 7 — Experiments and data collection (1 week)**
Run all workloads under all policy variants. Collect latency, memory, wasted
compute, fairness, and isolation test results.

**Total: 6–7 weeks implementation + writing.**

---

## 13. Key References

**Browser-native runtimes (products)**
- Chrome Prompt API / built-in AI: https://developer.chrome.com/docs/ai/prompt-api
- Chromium Optimization Guide / On-Device Model Service source: https://chromium.googlesource.com/chromium/src.git/+/main/components/optimization_guide/
- Chromium on-device model service build flags: https://chromium.googlesource.com/experimental/chromium/src/+/HEAD/services/on_device_model/on_device_model.gni
- Edge Prompt API (Phi-4-mini, Aion prerelease, shared across websites): https://learn.microsoft.com/en-us/microsoft-edge/web-platform/prompt-api
- Edge on-device AI expansion (June 2026): https://blogs.windows.com/msedgedev/2026/06/02/expanding-on-device-ai-in-microsoft-edge-new-models-and-apis-for-the-web/
- Firefox AI Runtime docs: https://firefox-source-docs.mozilla.org/toolkit/components/ml/index.html
- Firefox WebExtensions AI API: https://firefox-source-docs.mozilla.org/toolkit/components/ml/extensions.html
- Firefox native ONNX speedup: https://blog.mozilla.org/en/firefox/firefox-ai/speeding-up-firefox-local-ai-runtime/
- Firefox inference in web extensions: https://blog.mozilla.org/en/firefox/firefox-ai/running-inference-in-web-extensions/

**In-browser inference engines (research)**
- WebLLM: arXiv:2412.15803
- WeInfer (WWW '25): https://dl.acm.org/doi/10.1145/3696410.3714553
- LlamaWeb, "Llamas on the Web" (2026): arXiv:2605.20706
- WebGPU dispatch overhead characterization (2026): arXiv:2604.02344

**OS-level model services**
- Apple Foundation Models documentation: https://developer.apple.com/documentation/FoundationModels
- Apple Foundation Models updates: https://developer.apple.com/documentation/Updates/FoundationModels
- Apple Foundation Models research report (AFM 3): https://machinelearning.apple.com/research/introducing-third-generation-of-apple-foundation-models
- Windows Phi Silica API docs: https://learn.microsoft.com/en-us/windows/ai/apis/phi-silica
- Windows Phi Silica platform card: https://learn.microsoft.com/en-us/windows/ai/cards/phi-silica-platform-card
- ELMS, "Elastic On-Device LLM Service" (MobiCom '25): arXiv:2409.09071

**Datacenter serving, scheduling, and fairness**
- vLLM, "Efficient Memory Management for Large Language Model Serving with PagedAttention" (SOSP '23)
- SGLang (NeurIPS '24)
- Orca, "A Distributed Serving System for Transformer-Based Generative Models" (OSDI '22)
- FastServe, "Iteration-Level Preemptive Scheduling for Large Language Model Inference" (2026)
- FairServe, "Ensuring Fair LLM Serving Amid Diverse Applications": arXiv:2411.15997
- "Is the GPU Half-Empty or Half-Full? Practical Scheduling Techniques for LLMs": arXiv:2410.17840
- CoLoRA, "A Collaborative Scheduling Framework for Multi-Tenant LoRA LLM Inference" (ASP-DAC '26): https://doi.org/10.1109/ASP-DAC66049.2026.11420717
- H-MAS, "Hierarchical Multi-Agent Scheduling for Multi-Tenant LLM Serving" (ACL Findings '26): https://aclanthology.org/2026.findings-acl.1946/
- Continuum, "Efficient and Robust Multi-Turn LLM Agent Scheduling with KV Cache Time-to-Live": arXiv:2511.02230

**Resource exhaustion / inference DoS**
- OWASP LLM10:2025 Unbounded Consumption: https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/
- OCI Model Inference endpoint throttling: https://docs.oracle.com/en-us/iaas/Content/data-science/using/model-dep-invoke-throttling.htm
- AWS Bedrock / LLM gateway quota isolation: https://aws.amazon.com/blogs/machine-learning/implementing-resilience-patterns-with-amazon-bedrock-and-llm-gateway/
- DigitalOcean serverless inference architecture: https://www.digitalocean.com/blog/serverless-inference-deep-dive
- "Rethinking Latency Denial-of-Service: Attacking the LLM Serving Framework, Not the Model": arXiv:2602.07878
- "Prompt-Induced Over-Generation as Denial-of-Service": arXiv:2512.23779
- vLLM CVE-2026-34756 advisory summary: https://justappsec.com/news/2026-04-vllm-unauth-oom-dos

**Browser extension security**
- Carlini, Felt, and Wagner, "An Evaluation of the Google Chrome Extension Security Architecture" (USENIX Security '12): https://www.usenix.org/conference/usenixsecurity12/technical-sessions/presentation/carlini
- Kim and Lee, "Extending a Hand to Attackers: Browser Privilege Escalation Attacks via Extensions" (USENIX Security '23): https://www.usenix.org/conference/usenixsecurity23/presentation/kim-young-min
- MDN WebExtensions content scripts: https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Content_scripts
- OWASP Browser Extension Vulnerabilities Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Browser_Extension_Vulnerabilities_Cheat_Sheet.html

**Security motivation and defense architectures**
- Brave: indirect prompt injection in Perplexity Comet (Aug 2025): https://brave.com/blog/comet-prompt-injection/
- Brave: unseeable prompt injections in screenshots (Oct 2025): https://brave.com/blog/unseeable-prompt-injections/
- Brave: Security & Privacy in Agentic Browsing series: https://brave.com/series/security-privacy-in-agentic-browsing/
- Google: Architecting Security for Agentic Capabilities in Chrome (Dec 2025): https://blog.google/security/architecting-security-for-agentic/
- OpenAI: Hardening ChatGPT Atlas against prompt injection: https://openai.com/index/hardening-atlas-against-prompt-injection/
- Trail of Bits: Lack of isolation in agentic browsers (Jan 2026): https://blog.trailofbits.com/2026/01/13/lack-of-isolation-in-agentic-browsers-resurfaces-old-vulnerabilities/
- CaMeL, "Defeating Prompt Injections by Design" (DeepMind): arXiv:2503.18813
- IsolateGPT, "An Execution Isolation Architecture for LLM-Based Agentic Systems": arXiv:2403.04960
- Agent Security Bench (ICLR '25): https://mlanthology.org/iclr/2025/zhang2025iclr-agent/
- "Securing Agents With Tracked Capabilities" (ACM AI and Agentic Systems '26): https://doi.org/10.1145/3786335.3813127
- AWS Prescriptive Guidance, Enforcing tenant isolation for agentic AI: https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-multitenant/enforcing-tenant-isolation.html
- Azure Architecture Center, Secure multitenant RAG: https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/secure-multitenant-rag

**Standards**
- WebNN, W3C Candidate Recommendation (updated Jan 2026): https://www.w3.org/TR/webnn/
