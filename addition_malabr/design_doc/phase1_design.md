# MALABR Phase 1 — Design Document

**Status:** design complete, ready to flag gaps before implementation starts.
**Scope:** isolation, memory governance, compaction, naive scheduling. No real
browser visibility (harness-supplied), no cancellation, no baselines (Chrome/VTC)
— those are Phase 2.

This document is the single source of truth for "what Phase 1 builds." Read it
end to end and flag anything missing, wrong, or underspecified — that's its job.

---

## 1. What Phase 1 proves

1. Many sessions share one model with **nothing leaking** — including across
   session end + slot reuse, and including after "new chat."
2. Memory is a **fixed, divided pool**, not unbounded growth — with an explicit
   rule for who gets shrunk when it's tight.
3. Conversations **shrink instead of dying** when their share runs out.
4. No session can monopolise the engine, even under a naive policy.

Nobody else — not Chrome, not Ollama, not llama.cpp's server — does 2 or 3 at
all. That's Phase 1's contribution, standing on its own before any scheduling
sophistication exists.

---

## 2. UI

**Surface:** content script, injected into every page (`<all_urls>` for
experiments; narrow later if needed). Not a popup, not a side panel.

**`all_frames` must be stated explicitly as `false` — a real gap, not a
non-issue.** Chrome defaults to main-frame-only when this manifest key is
omitted, but relying on an *unstated* default is exactly the kind of thing
this audit exists to catch: a page with a dozen ad/tracking iframes would
otherwise inject the content script into every one of them if someone later
"fixes" this to `true` without knowing why it was left out — each iframe
attempting its own tab-scoped session would be a real bug, not a cosmetic
one. State it in the manifest deliberately, not by omission.

*Why not popup:* destroyed the instant it loses focus — can't hold a
conversation. Also has no tab id (`SessionTabHelper::IdForTab` returns
`InvalidValue()` for it — verified, `AttachTabHelpers` skips popups).

*Why not side panel:* separate per tab, but the extension must *claim* which
tab it is (`chrome.tabs.query` + pass `tabId`) — the browser can't derive it.
That claim can lie: an extension could assert it's the visible tab to steal
foreground priority. Bounded (same extension, can't steal another's data) but
avoidable entirely by using a content script instead, where the browser derives
tab id and visibility itself with nothing asserted by the caller.

**Shape:** small floating button → expands to a chat panel (message list +
input box). Built in a **Shadow DOM** to avoid the host page's CSS
leaking in or out.

**Minimise:** hides the panel (`display:none`). Does **not** end the session —
the server has no idea it was hidden. Only two things end a session:
tab close, or the user's explicit **"new chat"** button.

**Persistence across navigation:** the panel and its message list are destroyed
when the page navigates (new document). To make the *displayed* history survive:

```js
// Stored WITH its origin. Keying by tab alone leaks across origins -- see
// below and §5g.
chrome.storage.session.set({
    ["chat_" + tabId]: { origin: location.origin, messages }
});                                                             // after each turn

// On script start: restore ONLY if the stored origin matches this document's,
// and actively delete it otherwise -- do not merely decline to display it.
const stored = (await chrome.storage.session.get("chat_" + tabId))["chat_" + tabId];
if (stored && stored.origin === location.origin) {
    restore(stored.messages);
} else if (stored) {
    await chrome.storage.session.remove("chat_" + tabId);       // cross-origin: wipe
}
```

**Two corrections here, both found while tracing what a user sees after
navigating:**

**1. The displayed history must be origin-scoped too — §5g only fixed half
the leak.** §5g added `origin` to the *server-side* session key, so the KV
cache is correctly cleared on a cross-origin navigation. But `chat_<tabId>`
was still keyed by tab alone, so the bank conversation would remain *visible
on screen* after navigating to another site — arguably the more obvious half
of the privacy problem, and the half a user would actually notice. Storing
the origin alongside the messages and wiping on mismatch closes it. Note the
`else if` branch actively **removes** the entry rather than just not
rendering it: leaving it in place would keep the text recoverable from
extension storage.

**2. `pagehide` was wrong and actively broke the feature it sat next to.**
The earlier draft cleared storage on `pagehide` as "tab-close cleanup". But
`pagehide` fires on **every cross-document navigation**, not only on tab
close — so it would delete the history on each ordinary page change,
defeating the whole point of persisting it. There is no reliable way to tell
"tab closing" from "navigating away" inside `pagehide`. Correct API:

```js
// extension service worker -- fires ONLY on real tab close, never on navigation
chrome.tabs.onRemoved.addListener((tabId) => {
    chrome.storage.session.remove("chat_" + tabId);
});
```

This mirrors the server side exactly: `chrome.tabs.onRemoved` is the JS-side
counterpart of the `TAB_CLOSED` push that `TabStripModelObserver` sends
(§6a), both firing on genuine closes only.

`chrome.storage.session` is memory-only, cleared when the browser closes — never
written to disk. Populate the panel fully *before* attaching it to the page, so
there's no visible empty-then-fill flash.

**Server-side session persists across navigation too** (keyed on tab id, not
frame id — see §4), so the conversation the model remembers matches what's
displayed.

---

## 3. "New chat" — full teardown, not a display clear

Must hit all three places conversation data exists, or it's a partial reset
that looks clean but isn't:

```
1. KV cache (Python/llama.cpp)  — the actual attention state, in the slot
2. Session object (Python)      — pos, budget, visibility, state
3. Displayed messages (JS)      — chrome.storage.session
```

**The bug this avoids:** clearing only #3. Panel looks empty, but the slot
still holds old KV state — the next message gets old context bleeding in. Same
bug class as slot-reuse leakage (§5), just user-triggered instead of
scheduler-triggered.

```python
def new_chat(ext_id, tab_id):
    session = registry.pop((ext_id, tab_id), None)
    if session:
        allocator.release(session.slot)   # llama_memory_seq_rm — wipes KV
    # next message creates a fresh session via acquire(), which wipes AGAIN
    # (belt and braces — cheap, and correct even if release is ever skipped)
```

JS side: clear that tab's `chrome.storage.session` entry, empty the panel.

**Transport (implementation note):** the page-facing API is `generate()`/`stop()`
only, and `stop()` ends the turn but keeps the session — there is no page-facing
"end session" verb, and adding one is a Chromium rebuild. So "new chat" rides the
same meta channel as the model switcher: the panel sends the sentinel prompt
`\x00MALABR::new`, and `_handle_meta("new")` runs `engine.cancel(key)` followed by
`engine.wait_for_teardown([key])` before it replies. The blocking wait is what
makes this a real teardown and not a race: the panel's next `generate()` cannot
be handed the condemned session back, because by the time the "new" reply lands
the slot is already released.

**Free test this gives us:** click "new chat," send a canary as the first
message, ask for it back. If it ever appears, teardown is broken. Cheaper to
run than closing/reopening tabs, and belongs in the canary suite (§9).

---

## 4. Storage — nowhere durable, by design

Conversations live **only in RAM**, in exactly the two places in §2/§3. No
database, no file, no disk write, on either side. `storage.py` (SQLite,
inherited from v3.0) is **not used** for the generate path — it existed to
persist trained model metadata, which doesn't apply here.

This is deliberate, not an oversight: persisting chat text would be a much
bigger privacy surface than anything in the current threat model, and reopens
"how long do we keep it, who can read it" — explicitly out of scope. Session
death is *supposed* to mean data death; that's the property RQ1 is about, not
a limitation to work around.

| Event | KV cache | Displayed messages |
|---|---|---|
| tab closes | freed | gone (renderer destroyed) |
| "new chat" clicked | freed (§3) | cleared |
| browser closes | freed (process exit) | gone (session storage is memory-only) |
| **Python server crashes/restarts mid-session** | freed | **survives**, but orphaned — see below |

**Crash recovery — resolved, not silent.** Displayed messages already survive a
server crash (they live in `chrome.storage.session`, independent of the
server), but without an explicit path the socket just breaks and the user sees
nothing. Required behaviour:

```
   C++: recv() fails / SO_RCVTIMEO fires on an active connection
       → fire a "connection_lost" event to that tab's panel (not silence)
   JS:  show "Connection lost — [Reconnect]" rather than hanging or failing
        silently
   on reconnect: generate() is called fresh; server has no session (it
        restarted), client resends its displayed history as opening context
```

**Why this must be explicit rather than "just reconnect silently":** a silent
reconnect that quietly starts a new session is indistinguishable, from the
outside, from a properly-cleared "new chat" — which would make the leak tests
in §12 unreliable, since you couldn't tell "teardown worked correctly" from
"the server forgot and nobody noticed." Surface the loss; don't paper over it.

**Future option, not Phase 1:** `llama_state_seq_save_file` gives a real
hibernate-to-disk primitive if persistence is ever wanted later — the only path
that doesn't mean writing raw conversation text to a database.

---

## 5. Session key and identity — everything browser-derived

**Key:** `(extension_id, tab_id, origin)` — origin added in §5g, a
privacy boundary rather than a label.

```cpp
// in malabr_api.cc, at request time — nothing here comes from the payload
std::string ext_id = extension_id();                              // trusted
content::RenderFrameHost* rfh = render_frame_host();
content::WebContents* wc = GetSenderWebContents();
int tab_id = sessions::SessionTabHelper::IdForTab(wc).id();       // trusted, -1 if not a tab
bool fg    = rfh->GetVisibilityState() ==
             content::PageVisibilityState::kVisible;               // trusted
content::RenderProcessHost* proc = rfh->GetProcess();              // for output routing
uint64_t req_id = NextRequestId();                                 // minted here, once per request
```

Content scripts always get a real `tab_id` (they run inside the page's own
frame, which is in a tab). `tab_id = -1` is a legitimate value meaning
"not tab-scoped" (e.g. future service-worker callers) — treat as always
background, don't special-case it as an error.

**Layering note:** use `sessions::SessionTabHelper` (in
`components/sessions/content/`, reachable from `extensions/browser/`) — **not**
`ExtensionTabUtil` (in `chrome/browser/extensions/`, which `extensions/browser/`
cannot depend on).

**`tab_id` uniqueness across profiles/incognito — VERIFIED, not assumed.**
Checked `components/sessions/core/session_id_generator.cc`:
`SessionIdGenerator` is a `base::Singleton` — one instance, one counter, for
the entire browser process, seeded from `local_state` (browser-install-wide
prefs, not per-profile). Two tabs in different profiles, including incognito,
cannot receive the same `tab_id`. The `(extension_id, tab_id)` key does **not**
need a `profile_id` added — confirmed safe on this axis, not merely assumed.

**One thing this check surfaced, worth knowing rather than acting on:** the
comment in that file notes the counter is padded and persisted across restarts
("On startup, we increment the internal counter by `kCautionaryIdPadding` to
mitigate issues during ungraceful shutdown..."). `tab_id` values only ever
climb, across browser launches on the same machine — don't assume test `tab_id`s
start near 0 or are small; irrelevant to isolation, but will look odd in logs
if unexpected.

---

## 6. Protocol — streamed, header carries browser-derived context only

**Header** (was 3 fields, now 5):
```
route, extension_id, tab_id, visibility, payload_size
```
`tab_id` and `visibility` are attached by the browser process — the content
script never sends them.

**Two visibility channels now exist — needs an explicit ordering rule, or
they can conflict.** §6a/§10 add a *push* channel (`visibility_changed`),
independent of any particular request, alongside this *per-request header*
value. Without a rule, a request carrying a stale header value could arrive
after a push already updated the session — which one wins is otherwise
ambiguous. **Rule:** header-carried `visibility` only *seeds* a session at
creation (step 2 of the lookup/creation sequence in §7); once a session
exists, the push channel is the sole source of truth, and the header field on
subsequent requests for that session is ignored.

**Wire framing** — changed from one-shot to streamed, in Phase 1 (not deferred
to Phase 2), because:
- time-to-first-token is the metric that shows scheduling responsiveness at all
- doing it now means Phase 2 changes exactly one variable (the scheduler), not
  two (scheduler + wire format) — keeps the baseline comparison clean

```
[1B type][4B length][payload]
   type 0 = token chunk
   type 1 = complete
   type 2 = error
```
`type` byte (not a zero-length terminator) because "finished" and "failed
partway" must be distinguishable once generations can hit a budget or be
cancelled mid-flight.

**C++ side:** `Send()` becomes a loop — read a frame, invoke a per-chunk
callback, repeat until type 1/2. Reuses existing `ReadExact`.

**`req_id` vs `seq_id` — different axes, batching does not remove the need for
either.** `seq_id` identifies which KV-pool slot a token was computed in (the
*session*). `req_id` identifies which `generate()` call a token's output
*event* belongs to (the *request*) — needed because one session has many
requests over its life (every chat turn), and because batching multiple
sessions into one `llama_decode()` call is an engine-internal detail that JS
never sees; it still needs to know which chat bubble a token belongs to.
Minted fresh per request, in C++, never accepted from the frontend (§5).
**Collision risk is smaller than it first appears:** `req_id` is only
meaningful for the lifetime of one open connection, and a server crash/restart
breaks every open connection — there's no live listener left to confuse after
a restart. The only real (small) risk is two requests active *at the moment of
restart* colliding on a counter that resets to 0; avoided entirely by using
`base::UnguessableToken` instead of an incrementing integer.

**Output routing (browser → renderer):** `DispatchEventToSender(proc, ...)` —
the *actual* `RenderProcessHost` captured in §5, not a claim from a listener
filter. **Do not** use `EventListener`'s renderer-supplied filter dict
(`{tabId: X}` registered via `addListener`) for authorization — verified in
`event_router.cc:737-780` that this dict is stored **verbatim, unvalidated**
from the renderer. It's fine as a client-side routing convenience (matching
`req_id` to the right chat bubble) but must never gate delivery.

**Stated residual (write into thesis, don't hide):** Chromium's event-routing
trust granularity stops at *process*, not *frame*
(`extensions/common/mojom/event_router.mojom` carries only `extension_id` /
`listener_url`, no frame token). Two tabs of the *same* extension, same site,
can share a process under site isolation; a compromised script in one could in
principle observe token events meant for its sibling. Bounded: same principal,
same site, requires an already-compromised script. Not something MALABR
introduces — matches the platform's own trust granularity everywhere else.

---

## 5g. Origin is part of the session key — closes a cross-origin data leak

**The leak, in one scenario.** A user chats on their bank's site; the
conversation (account details, balances, whatever they typed) is resident in
that session's KV cache. They then navigate **the same tab** to any other
site. With a key of `(extension_id, tab_id)` the session survives — §4
deliberately keys on tab, not frame, so sessions outlive ordinary page
navigation. So the content script now running on the *new* site inherits a
session whose context still contains the bank conversation, and the model
will happily answer questions about it. §2's `chat_<tabId>` entry in
`chrome.storage.session` leaks the same way, since it is also keyed by tab
alone.

**Why this is a real boundary violation and not merely untidy.** Origin is
*the* security boundary the entire web platform is built on — same-origin
policy, storage partitioning, cookie scoping. A conversation store keyed by
tab rather than origin runs directly against that model. And it undercuts
§1's own headline claim (*"many sessions share one model with nothing
leaking"*), which until now only covered leakage across *sessions* via slot
reuse; this is leakage across *origins* inside a single session — a
different axis, previously unaddressed.

**Honest scoping of the severity.** The extension already holds `<all_urls>`,
so it could read both sites anyway; this does not hand it new access, and the
isolated world plus shadow DOM stop the *page* from reading the panel. So it
is primarily a **privacy-scoping** failure rather than a classic
exploitable escalation. It still matters: it violates the user's reasonable
expectation that a per-tab chat is scoped to the site they are on, it leaves
sensitive text on screen after navigation, and it contradicts §4's stated
principle that *session death means data death*.

**Fix — add origin to the key, derived in the browser:**

```cpp
// malabr_api.cc, at request time
std::string origin = rfh->GetLastCommittedOrigin().Serialize();  // unspoofable
```

Wire header grows to six fields:
`route, extension_id, tab_id, origin, visibility, payload_size`.
(Safe in a comma-separated header: a serialized origin is `scheme://host:port`
or the literal `null` when opaque — none can contain a comma.)

**Server-side rule, and note it needs NO new browser signal.** On creating a
session for `(ext, tab, origin)`, tear down any existing session with the
same `(ext, tab)` but a *different* origin — same teardown path as tab close
(§6a), freeing the slot and wiping the KV. Cross-origin navigation therefore
cleans itself up on the tab's next request, with no navigation-detection
observer required:

```python
def get_or_create(ext_id, tab_id, origin):
    for key, s in list(registry.items()):
        if key.ext_id == ext_id and key.tab_id == tab_id and key.origin != origin:
            cancel_and_release(s)      # wipes KV, frees slot
            registry.pop(key)
    ...                                # then create normally
```

**Same-origin navigation still preserves the session**, which is the
behaviour §2/§4 wanted: moving between pages of one site keeps the
conversation, exactly as a user expects. Only crossing an origin resets it.

**Origin, not site, deliberately.** `https://a.example.com` and
`https://b.example.com` get separate sessions. Stricter than Chrome's own
site-based partitioning in some places, but the correct default when the
stored data is free-form user text that may contain anything.

**Interaction with §6b's navigation rollback:** these are complementary, not
duplicates. Rollback handles a generation *interrupted mid-stream* so the
model never retains an answer the user did not see. This handles the
*session's whole history* crossing an origin boundary. A cross-origin
navigation during generation triggers both.

## 5a. Visibility ≠ focus — a real gap that could undermine the whole
scheduler, found by checking Chromium's own `Visibility` enum

**Confirmed against source, not assumed.** `content::Visibility::VISIBLE`
(`content/public/browser/visibility.h`) means only *"the view is visible on
the screen"* — nothing about input focus. Two Chrome windows on two
monitors, both unoccluded: **both report `VISIBLE` for their active tab.**

Every derivation of `fg` in this document (§5, §9) uses visibility alone:

```cpp
bool fg = rfh->GetVisibilityState() == content::PageVisibilityState::kVisible;
```

**Consequence: with more than one open window, several tabs can
simultaneously claim foreground priority.** §9's 50/50 split assumes
foreground means "the one tab the user is actually looking at" — with
multiple monitors it silently degrades into an N-way split that doesn't mean
what the design says it means, undermining the scheduler's core premise
rather than a peripheral detail.

**RESOLVED — window-level check, not content-view `HasFocus()`.** Checked
both candidates against real Chromium source. `RenderWidgetHostView::HasFocus()`
was the first candidate but has exactly the edge case flagged as a risk: it
tracks input focus on the content area specifically, so a user clicking the
*intended* window's address bar would flip that tab out of foreground even
though the user hasn't switched windows. The correct signal is one level up:

```cpp
// chrome/browser/ui/browser_list_observer.h — confirmed present
virtual void OnBrowserSetLastActive(Browser* browser) {}
virtual void OnBrowserNoLongerActive(Browser* browser) {}
```

A real observer pair, firing exactly on **window** activation change — not
content-focus change within a window. `BrowserList::GetLastActive()`
(`chrome/browser/ui/browser_list.h`) gives the current value on demand.

**Foreground, correctly defined: visible AND the containing window is
active.**

```
foreground = (WebContents::GetVisibility() == VISIBLE)
             AND (this tab's Browser* == BrowserList::GetLastActive())
```

Neither condition alone is sufficient — visibility alone misses the
multi-window case (§5a's original finding); window-activity alone misses a
minimized-but-technically-"last-active" window, which `Visibility::HIDDEN`
already correctly excludes (the enum's own comment: *"is part of a window
that is minimized or hidden"*). Both conditions were needed regardless; this
just supplies the second one properly instead of approximating it.

**Implementation note:** `MalabrManager` already needs a
`TabStripModelObserver` per §10 for visibility and tab-close events; add a
`BrowserListObserver` alongside it, firing the same `visibility_changed` push
(§6a) whenever active-window changes affect which tab is foreground —  no new
wire message, just a second trigger for the one already specified. Also
resolves an adjacent gap the original write-up didn't state explicitly:
`TabStripModelObserver` is naturally scoped per browser window, so watching
*all* open windows (not just one) requires observing each `Browser` instance,
not a single global observer — worth being explicit about at implementation
time rather than assuming one observer instance covers every window.

## 5b. Chat template EOS/stop condition — checked, correct for this model,

> **CORRECTION (implementation_notes #10).** The stop condition below is
> right in spirit but wrong in the primitive: it must use
> `llama_vocab_is_eog(vocab, tok)`, NOT `tok == llama_vocab_eos(vocab)`.
> `eos()` returns ONE token; Qwen3 flags SIX tokens as end-of-generation and
> gemma-3 flags three. Comparing against `eos` alone misses the rest and the
> turn runs to the output cap instead of stopping.
one portability caveat

**Confirmed:** `<|im_end|>` tokenizes to exactly the model's own EOS token
id (151645, verified via `llama_vocab_eos` + `llama_token_to_piece`) — not a
separate multi-token stop sequence. The existing `tok == EOS` check (§7)
already catches turn-end correctly for this model; no separate stop-string
matching needed.

**Caveat, not currently true by guarantee:** this holds because Qwen3
happens to tie its EOS token to its chat template's turn marker. Not every
model does this. A future model swap needs re-verifying this assumption, or
needs explicit stop-string matching (checking generated text against the
template's turn-end marker directly, not just the EOS token id) as a
fallback that doesn't currently exist.

## 5c. Extension update — verified already covered, not a new gap

Checked whether an extension **update** (not just uninstall/disable) fires
the teardown path §10 already wires up. Confirmed in
`extension_registrar.cc:108`: `RemoveExtension(extension->id(),
UnloadedExtensionReason::UPDATE)` — an update genuinely fires
`OnExtensionUnloaded`, the exact event already used as the teardown trigger.
No new gap; existing fix already handles this correctly.

## 5d. Sampling parameters — unspecified until now, and not deferrable to
the model

**Checked whether the GGUF carries recommended sampling defaults, the way it
carried the chat template — it does not.** 25 total metadata keys scanned,
none sampling-related. This has to be an explicit design decision, not
something read from the model file the way §6c's template was.

**Two different settings needed for two different purposes — currently
conflated as one implicit global default:**

- **Canary/fidelity tests require `temperature=0` (greedy, deterministic).**
  Every test in §12 that checks "does the canary come back" (tests 1, 4, 18,
  27, 32, 40) implicitly assumes deterministic output. With any sampling
  randomness, these become probabilistic rather than reliable pass/fail —
  a real risk to the test suite's validity if left unstated.
- **Real user-facing chat should NOT use greedy sampling.** Deterministic
  argmax produces flat, repetitive text — standard chat systems use some
  temperature (and typically top-p/top-k) for usable output.

**Fix: separate sampling configs for the two contexts**, not one shared
default — test harness runs with `temperature=0` explicitly set; the
user-facing engine path uses a real chat-appropriate default (e.g.
`temperature≈0.7`, top-p≈0.9 — standard values, not model-provided here,
so chosen deliberately and stated as such rather than left implicit).

## 5e. A dedicated control connection — a real gap found by tracing the
"real-time focus" requirement, resolves a contradiction in §6a below too

**The gap.** §6's protocol is per-call sockets: opened for one `generate()`,
closed when the response ends. §6a (below) says tab-close "actively closes
that session's socket connection" — which only makes sense while a
generation is actively streaming, where the socket genuinely is open. An
**idle** session — nothing in flight — has no open socket at all. Tracing
through what "visibility updates should reach the scheduler in real time"
actually requires surfaced this: there was never a specified channel for
either `visibility_changed` or tab-close-while-idle to travel on. Both were
described as "pushed down the socket" without saying which one.

**Fix — one dedicated, persistent control connection, separate from
per-request sockets.**

```
per-request sockets (generate())   ← unchanged: open per call, close at response end
control connection                 ← ONE, opened once, kept alive for the whole
                                       browser session, carries ONLY push
                                       notifications:
                                         FOREGROUND,<tab_id>    [see §5f]
                                         TAB_CLOSED,<tab_id>    [idle case]
                                         LIVE_TABS,<id>,...     [resync, §5f]
                                         EXT_UNLOADED,<ext_id>  [sweep, §10]
```

Python side: a dedicated reader thread, separate from the
`ThreadPoolExecutor` handling `generate()` calls, permanently blocked on
`recv()` for this one connection. A message updates `registry.foreground_tab_id`
directly (or triggers teardown) — a single attribute write, safe without a
lock under the GIL, picked up by the engine on its next dispatch round.

**This is what makes "real time, updates on every token" actually true end
to end — the read side was already correct, only the delivery path was
missing:**

```
OS focus change
  → BrowserListObserver (§5a) — synchronous, no polling
  → C++ writes visibility_changed to the control connection   ← was missing
  → Python reader thread updates session.visibility            ← microseconds
  → build_batch() reads it fresh on the very next dispatch      ← already
    round, never cached (§9 — confirmed correct from the start)   correct
```

The full path from a physical focus switch to the scheduler seeing it: one
OS callback, one socket write, one field assignment — no polling, no
batching, lag bounded by the next token boundary (~23–50ms, the same bound
already established for cancellation in §6a).

## 5f. Foreground is ONE key on the registry, not a per-session boolean —

> **CORRECTION (implementation_notes #12).** `LIVE_TABS` reconciliation must
> EXEMPT `tab_id == -1`. -1 is the documented "not tab-scoped" value, so it can
> never appear in a list of live tabs; a literal "reap anything not in the
> list" therefore destroys those sessions on the very first reconciliation.
> Not mentioned below.
a structural fix, not a performance one

**The gap, found by walking a concrete scenario: user fires a request in 5
tabs, one after another.** Each request's header says `visibility=foreground`
— correctly, because the user had to be *looking at* a tab to type in it. If
per-session booleans are the storage, all five sessions can simultaneously
hold `visibility == "foreground"`, and the whole scheduler premise collapses
in exactly the way §5a's two-monitor case does, just reached differently.

**The invariant nobody had written down.** By §5a's own definition,
foreground = *visible AND its window is `BrowserList::GetLastActive()`*.
There is exactly one last-active window, containing exactly one active tab.
So **at most one tab in the entire browser is foreground, at any instant** —
a real, global invariant. A per-session boolean *cannot represent* it: "five
sessions foreground" is a perfectly well-formed state in that encoding, and
the design was relying on always receiving matched *pairs* of pushes (`tab_3
hidden` AND `tab_7 visible`) to stay consistent. Drop either half and the
inconsistency is permanent and silent.

**Fix — store the invariant, don't police it:**

**It is a TAB id, not a session key — corrected while writing the C++ side.**
An earlier draft of this section stored a session key `(ext_id, tab_id)`.
Writing `malabr_manager.cc` proved that wrong for two independent reasons:
(1) the browser genuinely does not know which extensions hold sessions in a
tab, and should not have to — it would need a registration channel that does
not and need not exist; (2) if a tab is foreground then **every** session in
it is foreground, so a single session key cannot express the state when two
extensions both have a session in the visible tab. The real invariant is *at
most one foreground **tab***, and the count of foreground *sessions* is
however many sessions that one tab holds (normally 1).

```python
class SessionRegistry:
    sessions: dict[key, Session]              # key = (ext_id, tab_id)
    foreground_tab_id: Optional[int] = None   # AT MOST ONE TAB, by construction

def on_foreground_changed(tab_id):            # ONE message per switch, not two
    registry.foreground_tab_id = tab_id       # previous holder demoted implicitly
```

`build_batch()` (§9a) then reads:

```python
fg = [s for s in runnable if s.tab_id == registry.foreground_tab_id]
bg = [s for s in runnable if s.tab_id != registry.foreground_tab_id]
```

`fg` is normally 0 or 1 elements, and is >1 only in the genuine case of two
extensions sharing the visible tab — which is correct, not a bug.

| | per-session boolean | single `foreground_tab_id` |
|---|---|---|
| Two sessions foreground at once | **representable** — the 5-tab bug | **unrepresentable** |
| Messages per tab switch | 2 (must both arrive) | **1** |
| A dropped message | permanent silent inconsistency | self-corrects on next switch |

**This is the same principle as §7's wipe-on-acquire:** put the correctness
step where it *cannot be skipped*, rather than depending on a protocol always
behaving. Release can be forgotten; acquire cannot. Likewise a "hide the old
one" message can be lost; overwriting a single key cannot.

**Measured, so the justification isn't overstated — this is NOT a
performance optimization.** Benchmarked on this machine (8 sessions):

```
attr read (foreground_tab_id)        :   28.3 ns
attr + dict lookup (the real op)  :   71.0 ns
OLD: scan 8 sessions + bool cmp   :  253.8 ns
one decode step (measured)        :  23,000,000 ns  (23 ms)
  → lookup is 0.0003% of one token's cost
```

The O(1) form is 3.6x faster and **both are utterly irrelevant** against a
23ms decode. Recorded explicitly so nobody later reads this as a speed fix
and "optimizes" it back into a scan: the entire argument is representational
correctness, at `n_seq_max=8` the speed difference does not exist in practice.

**The header `visibility` field is near-information-free, and that's now
explicit.** At `generate()` time the tab is essentially always visible — the
user had to look at it to type. As §6's creation seed it is therefore almost
always `true`. It is retained only for the one case the push channel
structurally cannot cover: a *programmatic* call from a genuinely hidden tab,
where no visibility *change* ever occurs and so no push ever fires. Outside
that edge case it carries no information, and nothing should be built on it.

**Robustness — the control connection can drop, and nothing currently
resyncs. A real gap.** If the connection breaks while both processes stay
alive, Python keeps scheduling on a frozen `foreground_tab_id` indefinitely,
silently, with no indication anything is wrong. (If the *server* dies instead,
sessions die with it — §4 already covers that path, and there is no stale
state to worry about.) Detection is reliable here precisely because this is a
Unix domain socket: peer death or close means `recv()` returns 0. There is no
half-open-connection case as with TCP over a network, so **no heartbeat is
needed**.

```
DETECT     C++: any write() error on the control socket → disconnected
           Python: control reader's recv() returns 0 / raises → disconnected,
                   record the time, return to waiting for a new connection

RECONNECT  C++: retry with backoff AND jitter — reuse §10's existing
           connect-retry policy, do not invent a second one

RESYNC     on every successful (re)connect, MalabrManager pushes CURRENT
           STATE unconditionally — two messages, not only on change:
             FOREGROUND,<tab_id>       which tab is foreground right now
             LIVE_TABS,<id>,<id>,...   every tab that currently exists
           Python reconciles: any session whose tab_id is absent from
           LIVE_TABS is dead -> cancel it and free its slot. Pushes naming
           tabs Python has no session for stay a defined no-op (§9).

DEGRADE    while disconnected beyond STALE_VISIBILITY_TIMEOUT (~2s; a local
           UDS reconnect is milliseconds): set foreground_tab_id = None, i.e.
           treat every session as background.
```

**Why LIVE_TABS is needed, and why resyncing FOREGROUND alone is not enough
— found by asking what stops FUTURE tokens after a tab closes.** `FOREGROUND`
is *state*: re-pushable at any time, latest push wins. `TAB_CLOSED` is an
*event*, and on reconnect that difference is fatal. If the control connection
is down when a tab closes, that `TAB_CLOSED` is written into a dead socket and
lost — and can never be replayed, because once the tab is gone the browser
retains no record it ever existed. Nothing would ever free that slot:

```
control connection dead
  → TAB_CLOSED(tab 7) written into dead socket → LOST PERMANENTLY
  → reconnect re-pushes FOREGROUND ... but tab 7 is already gone
  → session 7 holds its slot forever; n_seq_max such tabs exhaust the pool
```

**Fix: convert the event into state.** On reconnect the browser sends the set
of tabs that *do* exist; the server frees any session whose tab is not in it.
Idempotent and self-correcting, where event replay is neither — the same
reason `foreground_tab_id` is a pushed key rather than hide/show event pairs.
`TAB_CLOSED` stays the fast path normally (one small message, no tab walk);
`LIVE_TABS` is the reconciliation that makes a lost one recoverable.

**Consequence for the Python side:** reconciliation must run on the control
handshake, not only on explicit messages. A server that restarted with no
sessions receives `LIVE_TABS` and correctly does nothing; one that kept
running receives it and reaps.

**Why `None` rather than "keep the last known value" — this changed once §9a
existed.** Under §9a, foreground gets **unconditional admission** into every
round, bypassing the latency budget. If stale data wrongly names a hidden tab
as foreground, that tab receives guaranteed admission every round
indefinitely — not merely mis-prioritized, but the one path that can blow the
~50ms round budget. `foreground_tab_id = None` degrades to *fair-but-unprioritized*
rather than *confidently wrong*, and keeps the latency bound intact. A genuine
foreground tab loses its guarantee for a second or two; that is acceptable and
self-healing. One assignment implements it — another benefit of a single key
over eight booleans that would each need clearing.

**Not surfaced to the user, deliberately.** Unlike §4's crash recovery (which
loses actual conversation the user would notice), this is invisible,
self-healing, and brief. Log it and make it observable for tests; do not put a
banner in the chat panel.

## 6a. Tab close — must stop generation, not just stop delivery

**Added to Phase 1 scope** (was implicitly Phase 2). Two distinct problems,
requiring two distinct mechanisms — conflating them is the mistake to avoid.

**Problem A — don't touch a dead tab (a pointer-safety problem).** After a tab
closes, its `RenderFrameHost`/`WebContents` may be destroyed while a token is
still in flight toward it. **Use the same pattern Chrome's own `AIManager`
uses** — confirmed in this checkout, `chrome/browser/ai/ai_manager.h`:
`content::WeakDocumentPtr rfh_;`, not a raw `RenderFrameHost*`. Applied here:

```cpp
content::WeakDocumentPtr rfh_;   // captured at request time, not a raw pointer
// on delivery:
content::RenderFrameHost* rfh = rfh_.AsRenderFrameHostIfValid();
if (!rfh) return;                // tab is gone — drop silently, no UAF, no crash
```

Confirmed present: `content/public/browser/weak_document_ptr.h`. For the
process-level handle `DispatchEventToSender` needs, the equivalent is
`RenderProcessHost::GetSafeRef()` → `base::SafeRef<RenderProcessHost>`
(confirmed at `content/public/browser/render_process_host.h:299`) instead of a
raw `RenderProcessHost*`.

This extends a pattern already in the existing codebase:
`weak_ptr_factory_.GetWeakPtr()` in `malabr_api.cc` already makes the
UI-thread-posted callback a no-op if the `ExtensionFunction` object died first.
The frame/process reference needs the same treatment, not just the callback.

**Problem B — actually stop generation, not just stop delivering it (a
resource-ownership problem, different from A).** Dropping the *output* silently
is not enough on its own: the engine would keep spending its one execution
slot generating tokens for a session nobody is listening to. Needs an active
signal, using the tab-close observer already being added for real visibility
(§10):

**SIMPLIFIED to ONE path while implementing `malabr_manager.cc` — the two
paths below are kept for the reasoning, but Case 1's "C++ actively closes
the socket" step is NOT what gets built.** Implementing it revealed the
per-request socket is owned by `MalabrGenerateFunction` on a thread-pool
thread; `MalabrManager` has no handle on it and would need a whole
registration channel to acquire one. Unnecessary, because the control
message alone already covers both cases:

```
tab closes (streaming OR idle — same path)
  → TabStripModelObserver fires, remove_reason == kDeleted
  → C++ sends TAB_CLOSED(tab_id) on the control connection
  → Python reader thread sets s.cancelled = True
  → engine loop, next token boundary: release slot, pop registry
  → Python's in-flight request handler (if any) sees the session is gone,
    emits a terminal error frame and closes that request socket
  → C++ ReadFrame fails/receives type 2 → SendStreaming returns
    → OnComplete(error) → Release()
```

**One requirement this places on the Python side, stated so it is not
missed:** the request handler must notice its session was cancelled and
terminate the stream with a terminal frame, rather than blocking forever
waiting for tokens that will never come. That is the Python-side
counterpart of C++'s `SO_RCVTIMEO`, and without it the C++ read would sit
until the 60s frame timeout rather than ending promptly.

**A `kDeleted` check is required, not optional:** `RemoveReason` also has
`kInsertedIntoOtherTabStrip`, which fires when a tab is *dragged into
another window*. That is not a close — the session must survive the drag.
Verified against `tab_strip_model_observer.h` in this checkout.

**Original two-path reasoning, retained for the record:**

```
tab closes
  → TabStripModelObserver (kRemoved, will_be_deleted=true) fires in
    malabr_manager.cc — the SAME observer added for visibility (§10), extended

  CASE 1 — generation actively streaming (a per-request socket is genuinely open):
  → C++ actively closes that session's socket connection (don't wait for a
    read timeout — that would waste up to SO_RCVTIMEO's full duration)
  → Python: recv() on that connection fails → mark session.cancelled = True

  CASE 2 — session idle, nothing in flight (no per-request socket exists — §5e):
  → C++ sends tab_closed(tab_id) over the dedicated control connection
  → Python: reader thread marks session.cancelled = True directly

  BOTH CASES converge here — engine loop, at the next token boundary (≤1 token
  later, ~23ms unthrottled / up to ~50ms under the Phase 1 `cpu.max` quota —
  see §7's correction — same bound as the preemption guarantee in §7):
        if s.cancelled:
            allocator.release(s.slot)     # wipes KV, per §7's rule
            registry.pop(key)
            continue
```

**Session ownership — this is where `unique_ptr` fits, and it is a different
role from A's weak-pointer safety.** `SessionRegistry` should own each
`Session` by `unique_ptr<Session>`. `registry.pop()` on cancel/close/new-chat is
the single, unambiguous point a session is destroyed and its slot freed — no
shared ownership, no second place that could also think it owns cleanup.

**Cancellation bound:** ≤1 token, same guarantee as preemption (§7) — because
it's the same mechanism (return-to-scheduler-every-token) checked for one more
condition. No new latency budget needed.

## 6b. Single-flight per session — one generation in flight, always

> **CORRECTIONS (implementation_notes #3, #4, #11).**
> 1. The rollback target given below for a GENERATING session
>    (`pos_before_generation`) is **not a usable boundary**: the KV at that
>    point ends inside an OPEN assistant block, so appending a new user turn
>    produces malformed structure. Roll back to `pos_before_request` — which is
>    also what this section's own rule demands, since a turn is the user message
>    AND its response. `pos_before_generation` is still tracked, because
>    compaction must shift it.
> 2. Rollback must undo the **formatter**, not only KV positions. §6c later
>    added the rendered conversation as a second per-session state; rolling one
>    without the other desyncs them and the next turn is built on a
>    conversation the model never saw. See `ChatFormatter.checkpoint()/
>    restore()`, captured at exactly the moment `pos_before_request` is.
> 3. The outbox is per-**REQUEST**, not "the session's outbox". Under
>    single-flight replace two handlers are briefly alive, and one shared queue
>    lets whichever polls first steal the other's frames.

**Missing from earlier drafts; a real correctness gap, not a UX nicety.**
Nothing previously prevented two `generate()` calls landing concurrently for
the same session — a real race: two threads incrementing `s.pos`, two decode
calls interleaving into the same slot. Modern chat UIs enforce "one response
generating at a time, sending a new message stops the old one" for exactly
this reason underneath the UX framing. Adopted as a hard invariant here.

**Mechanism — reuses §6a's cancellation path, different trigger:**

```
new generate() arrives for a session already PENDING/GENERATING
    → mark session.pending_replace = new_prompt   (don't touch state yet)
    → engine loop, TOP of next iteration, BEFORE build_batch():
          if session.pending_replace is not None:
              roll_back_partial(session)           # see below
              session.inbox = session.pending_replace
              session.state = PENDING
              session.pending_replace = None
```

Checked at the top of the loop, before dispatch — same ≤1-token bound as
tab-close cancellation (§6a); same flag-check pattern, not new machinery.

**The subtlety this surfaces: KV pollution from a half-finished turn.** The old
generation's tokens were already decoded into the slot as they were produced —
real KV state, not just undelivered output. Stopping without rolling back
leaves an incomplete assistant turn in context (no closing token), which can
produce strange continuations when the new prompt is appended right after it.

**Fix — `llama_memory_seq_rm`, a third use of a primitive already load-bearing
elsewhere in this design:**

```python
# TWO snapshots, not one — see correction below
s.pos_before_request    = s.pos    # set when ANY new prompt starts being handled
s.pos_before_generation = s.pos    # set once prefill completes, before first output token

def roll_back_partial(s):
    if s.state == PENDING:          # interrupted during/before prefill of the NEW prompt
        target = s.pos_before_request
    else:                            # interrupted during GENERATING (response already started)
        target = s.pos_before_generation
    llama_memory_seq_rm(mem, s.slot, target, s.pos)
    s.pos = target
```

`llama_memory_seq_rm` now does three distinct jobs across this document: wipe a
slot on handout (§7), shrink old context in compaction (§8), roll back an
interrupted turn here. Worth stating as one recurring pattern, not three
separate mechanisms, when this gets written up.

**Correction — one snapshot was not precise enough.** The original design
only captured `pos_before_generation` ("after prefill completes"), which is
correct for cancelling a response already GENERATING but wrong for a replace
arriving *during* prefill of a large prompt, before generation even starts —
there was no earlier snapshot to roll back to in that case. Fixed with a
second, earlier snapshot taken the moment any new prompt begins being
processed at all, and `roll_back_partial` picks the right target based on
which phase (`PENDING` prefill vs. `GENERATING` response) was interrupted.

**Second correction, cross-referenced from §8: these snapshots can go stale
mid-flight, not just be imprecise at creation.** `s.pos_before_request` and
`s.pos_before_generation` are absolute positions — if compaction (§8) fires
on this same session while either is live (it can: §8's 95%-trigger check
runs on every `GENERATING` session every round, and a session can be both
`GENERATING` and holding a live `pos_before_generation` at the same time),
compaction shifts everything after the dropped turn down without knowing
these snapshots exist. §8's `compact()` is now the one place that owns
shifting them — see the fix there — but the dependency is recorded here too
since this is the code that would silently roll back to the wrong position
if that shift were ever skipped.

**Navigation mid-generation is a THIRD trigger for this rollback — found by
asking what happens when a user clicks a link while tokens are streaming.**
`WeakDocumentPtr` nulls when the frame *navigates to a different document*,
not only when the tab closes — verified in
`content/public/browser/weak_document_ptr.h`. Without handling it:

```
user clicks a link mid-response
  → document destroyed, weak ptr nulls, every later token silently dropped
  → BUT the server keeps generating; s.pos advances; the full response
    enters the KV cache
  → new content script restores history from chrome.storage.session, which
    never received the in-flight response
  → model's context contains an answer THE USER NEVER SAW
```

That directly contradicts §2's claim that *"the conversation the model
remembers matches what's displayed"*, and on the next turn the model
references text absent from the screen. Note this is NOT the same as tab
close: the session legitimately survives navigation (it is keyed on tab id,
not frame id, per §4), so the fix is to cancel the in-flight **request** and
roll its partial turn back — not to tear the session down.

**Mechanism (implemented in the C++ side):** `OnToken` already discovers the
null document on the very next token; it now also raises a shared atomic
`abandoned_` flag, which `SendStreaming` polls once per frame and then
abandons the read. The server sees the socket drop and takes the ordinary
cancellation path, which rolls the partial turn back under the rule below.
Abandoning matters as much as dropping: continuing would burn an engine slot
producing tokens for nobody.

**Rule, stated once so "stop" and "replaced by a new message" don't need
separate handling:** a turn only enters permanent context if it reaches EOS or
the output cap. Any other termination (explicit stop, superseded by a new
prompt) is rolled back — never partially remembered.

**Old connection needs a terminal frame, not a hang.** A `superseded` outcome
(extend §6's frame types, or reuse the error type with a specific reason) so
the old request's client-side promise resolves instead of dangling. The new
request proceeds on its own fresh connection — consistent with the existing
per-call-socket design (`open_design_questions.md` entry 1).

## 6c. Chat template application — a real gap, missing entirely until this

> **CORRECTION (implementation_notes #1).** The incremental fragment below is
> **wrong as written**. It hardcodes `<|im_start|>{role}\n{content}<|im_end|>\n`,
> which is ChatML — Qwen-specific — and is also wrong for the assistant turn,
> because turn 1 with `add_ass=True` already leaves that block OPEN, so the
> fragment duplicates the header. Render the full conversation with the model's
> OWN template and take the suffix past what is already in KV. Verified on
> gemma-3, whose delta begins `<end_of_turn>`: the hardcoded version would have
> silently malformed every turn after the first.
audit pass, verified against the actual model file

**Confirmed present and working.** `llama_chat_apply_template` (bound as
`llama_cpp.llama_cpp.llama_chat_apply_template`) applies the model's own
carried template correctly — tested directly against `Qwen3-0.6B-Q8_0.gguf`,
which carries a real Jinja-based template (4100 chars, role markers,
tool-calling support, confirmed via `llama_model_chat_template`):

```
input:  [user: "Hello, who are you?", assistant: "I am Qwen."]
output: '<|im_start|>user\nHello, who are you?<|im_end|>\n
         <|im_start|>assistant\nI am Qwen.<|im_end|>\n
         <|im_start|>assistant\n'    ← trailing generation prompt (add_ass=True)
```

Nothing in this document previously addressed building prompts this way —
without it, the engine would feed the model raw unformatted text, which a
template-tuned model handles badly (fine-tuned expecting exactly this
structure).

**The subtlety that must be gotten right, not just "call the function":**
sessions keep KV cache resident specifically so each turn only prefills the
*new* tokens (§7), not the whole history — reformatting and re-prefilling the
full conversation every turn would defeat that design entirely. So:

- **First turn of a session:** apply the FULL template (captures any
  system-prompt/tools preamble the template only emits once — Qwen3's
  template conditionally includes a tools block, confirmed from the raw
  template text).
- **Subsequent turns:** apply only the INCREMENTAL per-turn wrapping
  (`<|im_start|>{role}\n{content}<|im_end|>\n`), appended at the session's
  current `pos` — never re-run the full template.

**Unverified, and load-bearing — needs its own fidelity check before
relying on it, the same class of test compaction already got (§8):** does
incrementally-appending per-turn fragments produce logits/behavior identical
to what full-history reformatting-and-reprefill would produce at the same
position? Not yet tested. If the incremental fragment pattern doesn't
precisely match what the full template would emit for that turn position, the
model could receive subtly malformed structure on every turn after the first.

## 6d. Streaming a partial multi-byte UTF-8 sequence — a real, confirmed bug
class, not hypothetical

**Tested directly against the vocabulary, not assumed:** scanned the first
20,000 tokens' raw byte output (`llama_token_to_piece`) for validity as
standalone UTF-8.

```
checked=19990 tokens, INVALID standalone UTF-8: 282  (~1.4%)
example: token 94 -> b'\xa1'   (a single byte — one piece of a multi-byte
                                 UTF-8 character, from BPE's byte-level
                                 fallback for rare/non-ASCII characters)
```

**282 tokens produce bytes that are not valid UTF-8 on their own.** §6's
streaming design currently emits each token's bytes as they're produced. Any
non-English text, many symbols, or emoji will eventually land on one of these
tokens mid-character — the frame sent over the wire splits a UTF-8 character
across two chunks, and the browser-side decoder either shows a replacement
character or throws outright.

**Fix — standard practice, not a new invention:** buffer emitted bytes at the
streaming layer; only flush up to the last point the buffer decodes as
complete valid UTF-8; hold any trailing partial bytes for the next token
before flushing again. This is how llama.cpp's own server and every
OpenAI-compatible streaming server handle the same tokenizer property — it
needs to be explicit in this design's streaming implementation (§6's frame
emission), not assumed away.

## 7. Engine — one thread, fixed pool, many slots

> **CORRECTIONS (implementation_notes #2, #11, #14).**
> 1. The `SlotAllocator` sketch below has `acquire()` call
>    `llama_memory_seq_rm` — i.e. it touches the model from a **connection
>    thread** — while this same section names single-threadedness a load-bearing
>    invariant. Both cannot hold. `llama.h` promises thread safety exactly once,
>    scoped to Tokenization; the Memory section carries no such guarantee, so
>    this is an undocumented data race whose symptom is corrupted KV, not a
>    crash. `acquire()`/`release()` are now pure bookkeeping (any thread);
>    `prepare(slot)` does the wipe and is ENGINE THREAD ONLY; `assert_ready()`
>    guards the decode path. The release-side "belt and braces" wipe was
>    dropped — it would have to run on a connection thread, and wipe-on-acquire
>    exists precisely because release cannot be trusted.
> 2. "The session's outbox" should read "the request's outbox" — see §6b.
> 3. The `n_ctx = 16384` stated here disagrees with §11's formula by 4.6x. §11
>    is the one that was wrong; see the correction there.

**Model context:** created once at startup.

```
n_ctx = 16384   (NOT 4096 — see §11)
n_seq_max = 8   (tabs supported concurrently — a stated tradeoff, see §11)
n_threads = 3   (NOT 8, and NOT the earlier draft's flat 4 — see §11: startup-computed as
                 physical_cores * QUOTA_PCT/100, matched to the 80% cpu.max quota. An earlier
                 revision of this section hardcoded 4, measured before the quota-matching
                 correction in §11 existed; that value is superseded, not a second valid option.)
```

**SlotAllocator** — the isolation mechanism:

```python
class SlotAllocator:
    def acquire(self):
        with self.lock:                           # see concurrency note below
            if not self.free: return None          # reject — no capacity
            slot = self.free.pop()
        llama_memory_seq_rm(mem, slot, -1, -1)      # WIPE ON HANDOUT
        assert llama_memory_seq_pos_max(mem, slot) == -1
        return slot

    def release(self, slot):
        llama_memory_seq_rm(mem, slot, -1, -1)      # wipe again — belt and braces
        with self.lock:
            self.free.append(slot)
```

**The rule, stated once because it's the whole isolation design:** wipe on
*acquire*, not only on release. Release can be forgotten (a bug, a crash
between release and reuse); acquire cannot — a session cannot exist without
going through it. Putting the safety-critical step at the point that can't be
skipped means a missed release is harmless instead of a leak.

**Concurrency — a real gap, not hypothetical.** Multiple connection threads
call session-creation concurrently (one per tab, arriving close together).
`self.free.pop()` from several threads with no lock is a race: two tabs could
receive the same slot, or two sessions could be created for one
`(ext_id, tab_id)` key if both requests check-then-create before either
finishes. `SlotAllocator` needs the lock shown above; `SessionRegistry`'s
get-or-create path needs the same protection — "look up, and if absent create"
must be one atomic critical section, not two steps a second thread can
interleave with.

**Session lookup/creation decision, stated explicitly (was previously only
implied by the registry being keyed):** on every `generate()` request —
1. registry has `(ext_id, tab_id)`? → reuse (this is where the race above lives)
2. absent, slot available? → `acquire()`, create
3. absent, no slot available? → reject — see the foreground-starvation gap
   immediately below; this reject is currently NOT visibility-aware

**Known Phase 1 limitation — foreground can be starved at admission, not just
at dispatch.** `SlotAllocator.acquire()` rejects purely on availability, with
no regard for visibility. Concretely: 8 background tabs already hold all 8
slots, user opens a 9th tab (foreground) and asks something — **that new
foreground session is rejected**, which directly contradicts the thesis's own
motivating scenario (foreground should always win). §9's 50/50 dispatch rule
only governs sessions that already have a slot; it has no say over who *gets*
one. For Phase 1: keep the simple reject rather than build eviction (evicting
a background session's conversation mid-flight is a real design decision on
its own, not a small addition) — but state this as a **named limitation**,
not a silently absent case. A reviewer finding it unaddressed would undercut
the thesis's own claim more than stating it plainly up front.

**Rejected for now: hibernate-to-disk eviction (`llama_state_seq_save_file`/
`_load_file`), considered explicitly, not overlooked.** Would free a slot for
a rejected foreground request by serializing a background session's KV state
to disk and restoring it later, rather than dropping it outright. Rejected
for Phase 1 for two concrete reasons, not just "more scope": (1) it directly
reopens §4's stated design principle — session death is supposed to mean data
death — since hibernating means conversation state genuinely persists to
disk, needing its own encryption-at-rest and deletion-lifecycle decisions
that don't currently exist; (2) real disk I/O cost, sized by the same
~107KB/token constant used elsewhere (a position-2000 session is ~214MB to
save and later restore), plus a thrashing risk if a user bounces between
tabs. The message-on-reject fix (surface "slots full" to the user rather than
failing silently) is still worth doing regardless — cheap, consistent with
§4's crash-recovery principle, and independent of this decision. Revisit
hibernation only if the plain-reject limitation proves to matter in practice,
not preemptively.

**Engine thread — exactly one, owns the context:**

**Note on the two `engine_loop` sketches in this section — reconciled, not two options.** The
single-session skeleton immediately below predates real batching being pulled into Phase 1 scope
and is kept only because it's the clearest way to show `scheduler.pick()`/`ensure_room()` in
isolation, one session at a time. It is **not** the authoritative loop — the batched version later
in this section (under "Real batching"), together with §9a's round-latency-budget `build_batch()`,
is what Phase 1 actually implements. Anyone implementing this should start from the batched
version; the skeleton below is explanatory scaffolding, not a second valid engine_loop.

```python
def engine_loop(self):                    # ILLUSTRATIVE ONLY — superseded by the batched
    while running:                         # version below + §9a's build_batch(). Kept to show
        s = scheduler.pick(registry.values())   # scheduler.pick()/ensure_room() in isolation.
        if s is None: time.sleep(0.001); continue

        if s.state == PENDING:
            decode(s.slot, s.inbox_tokens, s.pos)
            s.pos += len(s.inbox_tokens)
            s.state = GENERATING
            continue

        ensure_room(s, 1)                 # §8 — compact if budget hit
        tok = sample(s.slot)
        if tok == EOS or s.produced >= s.max_output_per_request:
            s.outbox.put(DONE); s.state = IDLE; continue
        s.outbox.put(tok)
        decode(s.slot, [tok], s.pos)
        s.pos += 1; s.produced += 1
```

**Returns to the scheduler after every single token.** Measured cost: ~1%
(same-sequence). This *is* the preemption mechanism — nothing else is needed,
because nothing ever commits to more than one token at a time. A foreground
request arriving mid-background-generation starts within ~23ms **unthrottled
— CORRECTED below once running under the Phase 1 `cpu.max` quota**, not the
~3s a request-granularity design would cost.

**Correction — the ~23ms figure was measured unthrottled; it does not hold
under the `cpu.max` quota Phase 1 actually runs at.** `cpu.max` doesn't fail
or signal an error when its budget is exhausted mid-token — the kernel simply
takes every thread in the cgroup off the run queue until the next accounting
period opens, and `decode()` silently stalls partway through and resumes
later. Measured per-token latency distribution, not just average throughput:

```
UNTHROTTLED:                              p50=23.2ms  p95=26.8ms  p99=28.3ms  max=29.4ms
THROTTLED (80% quota, default 100ms period): p50=24.7ms  p95=47.8ms  p99=49.9ms  max=51.7ms
```

**Median is essentially unaffected — most tokens land cleanly within their
period. The tail roughly doubles** (max: 29.4ms → 51.7ms) from tokens that
happen to start near a period boundary and eat a partial stall. Not the
catastrophic full-period stall a naive model of the mechanism would predict,
but real, and the honest worst-case preemption bound under Phase 1's actual
CPU governor is **~50ms, not ~23ms** — still far better than Chrome's ~3s
request-granularity blocking, but this specific number needed correcting
everywhere it's cited as a guarantee, not just as a best case.

**The accounting period length is a real, measured, controllable tradeoff —
not a free win in either direction:**

```
default period (100ms):  p50=24.7ms  p95=47.8ms  p99=49.9ms  max=51.7ms
shorter period (10ms):   p50=33.4ms  p95=38.5ms  p99=42.8ms  max=44.0ms
```

A shorter accounting window lowers the worst case (max: 52ms → 44ms) by
bounding how much budget can be exhausted before the next reset, but raises
the typical case (median: 25ms → 33ms) from more frequent quota-boundary
bookkeeping overhead. Worth choosing deliberately once real workloads are
available to tune against, not defaulting to either extreme without data.

**Named invariant: cancellation and batch composition are strictly
sequential, never interleaved.** Holds *because* the engine loop is
single-threaded — a session flipped to cancelled is excluded from
`build_batch()`'s selection entirely, before any batch is composed, so
mid-batch cancellation (a session cancelled at the exact moment it would
otherwise have been included) cannot happen by construction, not merely by
careful ordering. Worth stating explicitly rather than leaving it as an
implicit consequence of the architecture — it is exactly the kind of property
that silently breaks if this loop is ever parallelized later (e.g. to drive
multiple model instances), so anyone changing that structure needs to know
this guarantee depends on it.

**`outbox` needs a bound and an explicit backpressure policy — a genuine gap,
distinct from every memory bound discussed so far.** Everything else in this
design bounds the KV pool (§7's `SlotAllocator`, §8's budgets/compaction).
Nothing yet bounds the *output* side: if the C++ connection thread reading a
session's `outbox` stalls (browser under load, IPC congested, tab backgrounded
by the OS), the engine keeps producing tokens into an unread queue — unbounded
growth outside the KV pool entirely, a leak class none of the earlier
mechanisms cover. Needs a bounded queue with one of three explicit policies,
not left implicit:
- **block the engine** on a full queue — wrong: stalls the one shared
  execution slot for every other session over one slow reader
- **drop tokens** past the bound — wrong: silent data loss, the user sees a
  corrupted response with no indication anything was dropped
- **disconnect the slow session** — correct: treat a stalled reader the same
  as a dead one, routed through the exact same teardown path as tab-close
  (§6a) rather than inventing a fourth cleanup mechanism

**The bound itself needs a concrete number, not just a chosen policy — a real gap, found in a
later audit pass.** Tie it to a constant this document already derives, rather than a new
arbitrary one:

```python
MAX_OUTBOX_TOKENS = 2 * ABSOLUTE_CEILING   # ABSOLUTE_CEILING: Phase G's flat outer bound on
                                             # max_output_per_request (§11a) — already the largest
                                             # number of tokens any single response can produce.
                                             # 2x gives headroom for one in-flight response plus a
                                             # brief stall before disconnect fires, without
                                             # inventing a second unrelated constant to tune.
```

Bytes, not tokens, would be a more precise bound (token byte-lengths aren't constant — §6d already
established this, from the UTF-8 byte-fallback finding), but tying it to `ABSOLUTE_CEILING` in
token terms keeps it anchored to a number this document already treats as authoritative, rather
than picking a fresh byte figure with no existing basis. Revisit as a byte bound if implementation
measurement shows token-count alone is too imprecise.

**Real batching — IN Phase 1, not deferred.** Corrected from an earlier draft
of this document, which put batching in Phase 2. Multiple sessions' tokens go
into the SAME `llama_decode()` call, each tagged with its own `seq_id` —
verified this session with a real test (4 sessions, one batched decode call
each round vs. 4 separate calls): **1.65× total throughput** (66.1 vs 40.1
tok/s-equivalent). Confirms the "read weights once, produce N tokens" argument
empirically rather than leaving it as reasoning from first principles.

**Batch composition — CORRECTED, see §9a. The slot-count version below does not actually bound
anything and must not be implemented as written; kept for the record only.** Checking it against
how batched `llama_decode()` actually behaves surfaced two problems, both detailed in §9a: (1)
with `BATCH_SIZE` defaulted to `n_seq_max` and admission already capped at `n_seq_max`, every
runnable session always fits in one batch — the 50/50 split can never exclude anyone, so it never
binds; (2) even with a smaller `BATCH_SIZE`, there is normally exactly one foreground session, so
`fg_slots` is almost always undersubscribed and `fill_unused_slots` hands its leftover capacity to
background — the reverse of the intended priority. §9a's round-latency-budget rule replaces this.

```python
def build_batch(sessions, batch_size):    # SUPERSEDED by §9a — kept for the record
    runnable = [s for s in sessions if s.state == GENERATING]
    fg = [s for s in runnable if s.visibility == "foreground"]
    bg = [s for s in runnable if s.visibility != "foreground"]
    if not fg and not bg: return []
    if not bg:   fg_slots, bg_slots = batch_size, 0      # no background contention
    elif not fg: fg_slots, bg_slots = 0, batch_size
    else:        fg_slots = batch_size // 2; bg_slots = batch_size - fg_slots

    picks  = round_robin_pick(fg, fg_slots)   # RR within group if slots > sessions
    picks += round_robin_pick(bg, bg_slots)
    # work-conserving AT THE SLOT LEVEL: if one group has fewer sessions than
    # its allotted slots, the leftover slots go to the other group rather than
    # sitting idle — THIS is exactly the step that defeats the priority intent,
    # see §9a
    picks += fill_unused_slots(fg, bg, picks, batch_size - len(picks))
    return picks

def engine_loop():
    while running:
        # cost_curve: §9a's build_batch(registry, cost_curve) — held on the engine
        # from calibration's Phase D output at startup (§11a), never re-derived
        # per round. build_batch now returns BOTH decode picks and prefill
        # chunks: a PENDING session contributes a chunk of its prompt sized to
        # whatever budget is left, rather than prefilling in one blocking call
        # (§9b).
        decode_picks, prefill_chunks = build_batch(registry, self.cost_curve)
        if not decode_picks and not prefill_chunks:
            time.sleep(0.001); continue

        batch = new_batch()
        for s in decode_picks:                            # 1 token each
            batch.add(token=s.next_token, pos=s.pos, seq_id=s.slot)
        for s, chunk in prefill_chunks:                   # N tokens each
            for i, tok in enumerate(chunk):
                batch.add(token=tok, pos=s.pos + i, seq_id=s.slot)

        t0 = time.perf_counter()
        llama_decode(ctx, batch)                # ONE pass: decode AND prefill
        observed_ms = (time.perf_counter() - t0) * 1000

        # §9b: closed-loop correction. The cost model is an ASSUMPTION until
        # measured; feeding the real round time back means a wrong model
        # degrades gracefully instead of silently blowing the bound.
        self.cost_curve.observe_round(estimated_ms, observed_ms)

        for s, chunk in prefill_chunks:                   # advance prefill state
            s.pos += len(chunk)
            s.prefill_offset += len(chunk)
            if s.prefill_offset >= len(s.inbox_tokens):
                s.state = GENERATING
        for s in batch_picks:
            tok = sample(s.slot)
            ...                                            # per-session bookkeeping unchanged
```

**RESOLVED by §9b's chunked prefill — this limitation is no longer accurate,
kept for the reasoning that produced the fix.** The problem was real: a
`PENDING` session's prefill ran to completion in one `llama_decode()` before
the loop reached `build_batch()` again, and because the engine is
single-threaded (§7's named invariant) *nothing else ran meanwhile* — not
even a foreground session mid-response. §9a's ~50ms budget did not cover it,
and the ≤1-token preemption bound (§6a/§7) applies to generated tokens, not
to a prefill call. So a background tab receiving a pasted article could stall
every other session for the whole prefill.

§9b splits prefill into budget-sized chunks admitted into the same batch as
decode work, which removes the unbounded stall. What remains open is not the
mechanism but its **input**: §14's item that prefill speed is entirely
unmeasured, so `PREFILL_CHUNK_TOKENS` cannot yet be derived from real data.
§9b specifies the calibration that produces it.

`BATCH_SIZE` as a single fixed constant is superseded by §9a too — round membership is now
decided by a latency budget, not a fixed slot count, so there is no longer one number to tune
here. Its interaction with memory (more sessions batched ⇒ more KV state touched per pass) and
with `n_ubatch`/`n_batch` context params remains unmeasured beyond the original 4-session
uniform-depth test and should be swept once §9a's rule is implemented.

**Thread pools around it are waiting rooms, not compute:**
- C++: `base::ThreadPool::PostTask(MayBlock())`, one per in-flight request
- Python: `ThreadPoolExecutor(max_workers)` — **must be raised well above
  `n_seq_max`** (e.g. 64), or the pool itself becomes an invisible FIFO gate in
  front of the scheduler: a 9th concurrent tab would sit unparsed in the pool's
  queue, invisible to the scheduler, even if it's the visible tab. This is a
  one-line config change but load-bearing.
- `trainer_workers` → **0**. The `mp.Process` training pool is deleted for this
  path entirely (sklearn is gone).

---

## 8. Memory governance

> **CORRECTIONS (implementation_notes #5, #6).**
> 1. Compaction must drop the **formatter's messages** too, not only KV
>    positions. Dropping KV alone leaves the rendered conversation describing
>    content the model no longer has. See `ChatFormatter.drop_messages()`,
>    called from `compact()`, plus `msg_index` on each Turn, shifted exactly
>    like the positions. Verified: render length == `s.pos` after compaction.
> 2. "Aligned to chat-template boundaries" below is under-specified. The
>    boundary is **[user-delta start, response end)**, and the choice is
>    load-bearing: every delta begins with the terminator closing the preceding
>    assistant block, so dropping a whole exchange leaves the survivor's block
>    closed by the next surviving delta — well-formed, with no repair step.

**The pool is fixed, divided, not policed.** `n_ctx` is allocated once at
startup (measured: RSS flat from 1 to 8 sessions — ~1139MB regardless of
occupancy). Nothing to enforce after that; there's nowhere to grow into.
`n_ctx_seq = n_ctx / n_seq_max` per session — currently an **equal** division,
not a per-session-configured cap (see §11 for why this may need revisiting).

**Per-request output cap — server-set, closes a real gap:**

```python
s.max_output_per_request = 512     # server constant, client cannot raise it
```

Chrome's equivalent (`max_output_tokens` in `InputOptions`) is *optional and
caller-supplied* — verified in `on_device_model.mojom`. A careless or hostile
extension omits it; there is no runtime-side ceiling. This closes that hole:
the limit is never something the client controls.

**Input cap — same principle, checked BEFORE any prefill or compaction is
attempted, resolving a gap in the compaction design:**

```python
RESERVED_FOR_RESPONSE = 256
MAX_INPUT_TOKENS = session.budget - RESERVED_FOR_RESPONSE

if len(tokenize(prompt)) > MAX_INPUT_TOKENS:
    reject("input too long for available context")
```

**Defense-in-depth — a second gate at the actual point of no return, same
constant, not a redundant restatement.** One check point is a single point of
failure: if it's ever bypassed (a bug, a race between check and use, a code
path added later that skips it), nothing else stops an oversized prefill
from reaching `decode()`. Gate 2 lives inside the engine itself, structurally
unable to be skipped by anything upstream:

```python
def prefill(session, tokens):
    assert len(tokens) <= MAX_INPUT_TOKENS, "gate 1 was bypassed — should never fire"
    decode(session.slot, tokens, session.pos)
```

If gate 2 ever fires, that is a loud signal something upstream is broken —
not silent tolerance of the oversized input it exists to prevent. Same
pattern as Phase F/G's calibration-output clamps (§11a): never trust a single
check point for anything safety-relevant.

**Why this belongs here rather than as a compaction case:** `compact()`
returns `False` when there's nothing left to drop after keeping the head
anchor and tail window (§8's compaction subsection) — which happens whenever a
*single incoming prompt* is larger than the session's entire budget (pasting a
long page to summarize into a small session share, say). Rather than have
compaction fail and then decide what to do about it, reject the oversized
input outright, before it ever reaches prefill. Simpler, and it never silently
truncates what the user actually asked — `RESERVED_FOR_RESPONSE` guarantees
room is left for the model to answer, not just to fit the question.

**Compaction — triggered when a session's share fills, not a rare edge case.**
At `n_seq_max=8` and even the revised `n_ctx=16384` (2048/session), a real
back-and-forth conversation *will* reach its share; compaction is the normal
path for any conversation that runs long, not a fallback for an anomaly.

**Trigger at 95% of budget, and check BEFORE a session enters a batch — not
after.** This is a correctness fix, not just a safety margin: with real
batching (§7/§9) now in the design, one `llama_decode()` call can advance
*several* sessions' `pos` at once. Checking room only for the session about to
overflow, after the fact, races the batch — session A might be fine, session B
one token from its ceiling, and the batch pushes B past it before anything
would have re-checked. Checking at batch-composition time, before inclusion,
means compaction always has headroom and the ceiling is never actually crossed:

```python
def build_batch(sessions, cost_curve):    # §9a's build_batch — updated from an earlier
    picks = ...                            # draft that called a since-superseded §9
    for s in picks:                        # selection rule; §9a's actual selection logic
        if s.pos >= s.budget * 0.95:       # is unchanged by this fix, only the check's
            compact(s)                    # BEFORE this session's token enters the batch
    return picks
```

The compaction check itself plugs into §9a's `build_batch()` exactly as shown there — this
snippet exists only to isolate the 95%-trigger check, not to sketch a competing selection
mechanism. An earlier draft of this snippet referenced "§9's foreground/background rule," which
was correct before §9a replaced that rule; kept here corrected rather than left stale, since
`build_batch` is exactly the function §9a redefines.

**User-triggered compaction — a "compact now" option in the chat panel**, in
addition to automatic. Calls the same `compact()` path as the 95% trigger.
Useful to the user (proactively shrink a conversation they know is getting
long) and useful to testing: it gives a *user-triggered* compaction case,
parallel to how "new chat" (§3) gives a user-triggered teardown case — both
belong in the canary suite (§12).

**CORRECTED — drop whole turns, oldest-first, aligned to chat-template
boundaries (§6c), not an arbitrary position range.** The original version
below drops a blind byte range, which can land mid-turn — leaving a
half-formed `<|im_start|>user\nWhat did you` with no closing marker, strictly
worse than losing a whole turn cleanly (the same class of problem §6b's
mid-generation rollback exists to prevent). Fix reuses `session.turn_boundaries`
— a natural byproduct of applying the chat template incrementally, not new
bookkeeping — to guarantee every drop lands exactly on a clean `<|im_end|>`:

**CORRECTED AGAIN — a second, more serious bug in the version below: shifting
`s.pos` after a drop is not enough. Everything else recorded as an absolute
position must shift too, or it silently points at the wrong content.** Found
by tracing what else in this document holds an absolute position across a
compaction event, not by re-reading `compact()` in isolation:

1. **§6b's rollback snapshots go stale.** `build_batch()` (above) runs the
   95%-trigger check on *every* runnable session every round, including ones
   currently `GENERATING` — i.e. mid-response, already past prefill, with a
   live `pos_before_generation` snapshot from §6b sitting in `s`. If
   compaction fires on that same session while that snapshot is live and a
   replacement prompt then arrives, `roll_back_partial()` calls
   `llama_memory_seq_rm(mem, slot, target, s.pos)` with a `target` that no
   longer corresponds to the same content — it was never shifted when
   `compact()` moved everything after it down by `n`. Rolls back to either the
   wrong position or a range compaction already removed.
2. **The turns `compact()` doesn't remove this pass keep their stale
   positions.** The loop below only calls `s.turn_boundaries.remove(turn)` on
   the turn it just dropped — it never updates the recorded `(start, end)` of
   every *other* turn in the list, all of which sit after the just-removed
   range and are now off by `n`. The very next iteration of the same loop
   (the next-oldest droppable turn) then calls `llama_memory_seq_rm`/`_add`
   using that stale, now-wrong range — silently acting on the wrong content.
   The two retained tail turns (`turn_boundaries[-2:]`) go stale the same way
   and stay stale until the next incremental append or compaction pass reads
   them.

**Fix — one shift step, applied to every live absolute-position reference at
once, not just `s.pos`.** Requires `turn_boundaries` entries to be mutable
(a small dataclass, not a literal tuple — the `(start_pos, end_pos, role)`
comment below describes the *shape*, not that it must be immutable; this fix
needs to write back into entries still in the list):

```python
def compact(self, s):
    # s.turn_boundaries: [Turn(start, end, role), ...], mutable entries —
    # tracked as each turn is appended via §6c's incremental template
    # application
    anchor = s.turn_boundaries[0]                    # first turn — may carry
                                                        # the system/tools preamble
    droppable = s.turn_boundaries[1:-2]                # everything except the
                                                        # anchor and the most
                                                        # recent exchange
    freed = 0
    for turn in list(droppable):                       # oldest first
        if freed >= TARGET_FREED_TOKENS: break
        n = turn.end - turn.start
        llama_memory_seq_rm (mem, s.slot, turn.start, turn.end)
        llama_memory_seq_add(mem, s.slot, turn.end, -1, -n)

        # shift EVERY absolute position >= turn.end by -n — the fix. Not just
        # s.pos: every other turn still in the list, and any live §6b
        # rollback snapshot, reference positions in the same shifted space.
        for other in s.turn_boundaries:
            if other is turn: continue
            if other.start >= turn.end:
                other.start -= n
                other.end   -= n
        if s.pos_before_request is not None and s.pos_before_request >= turn.end:
            s.pos_before_request -= n
        if s.pos_before_generation is not None and s.pos_before_generation >= turn.end:
            s.pos_before_generation -= n

        s.pos -= n
        s.turn_boundaries.remove(turn)
        freed += n
    return freed > 0
```

**Why `>= turn.end` and not something narrower:** every position that matters
here — the other turns' boundaries, both rollback snapshots — is recorded
for content that comes *after* the turn being dropped (droppable turns are
processed oldest-first, and snapshots/later turns only ever reference the
live, in-progress tail of the conversation). Nothing legitimately live should
ever fall *inside* `[turn.start, turn.end)` — droppable turns are, by
construction, fully-closed prior turns, never the in-flight one a snapshot or
the anchor could reference. If gate-2-style defensive code is ever added
here, that invariant — no live reference strictly inside a dropped range —
is the thing to assert.

Standard technique (whole-turn eviction is common production practice for
chat context management, not novel) — the improvement here is specifically
tying it to boundaries the design already tracks for another reason, so it
costs nothing extra to implement correctly, once the shift is applied
consistently everywhere a position is held, not just on `s.pos`.

**Two further options considered, kept deliberately inside "cheap" — neither
built yet, both worth stating rather than silently deciding:**

1. **Incremental vs. batch eviction — a genuine open question, not obvious
   either way.** Currently: wait until 95% of budget, evict a chunk in one
   event. Alternative: evict small increments continuously as usage climbs.
   Incremental means smaller, more frequent changes (less disruptive
   individually) but more total eviction events and bookkeeping per turn;
   batch means fewer disruptions but each a bigger jump. No measured answer
   for which produces better perceived continuity — worth a canary-recall
   comparison between the two before picking one, not an assumption.

2. **A cheap "stub" between full-drop and full-summarize — new, distinct
   from Chrome's `session-compacting` (entry 21, `open_design_questions.md`),
   which requires a real generation call.** Keep the first/last ~40 characters
   of a dropped turn, truncate the middle — pure string operation, no model
   call, microseconds not a generation pass. Preserves the shape of an
   exchange without every word, at near-zero cost. **Honest caveat:** a
   heuristic, not principled compression — naive truncation can cut
   mid-sentence and lose the actual point. Test via canary recall before
   trusting it; drop the idea if it fails that test meaningfully worse than
   full-turn eviction, rather than keep it purely for being cheap.

**Explicitly reaffirmed, not reopened:** attention-score-based selective
retention (StreamingLLM/H2O/SnapKV-style — deciding *which tokens* within a
turn matter) stays out of Phase 1 scope. Real complexity, a genuinely
different research question from "which whole turns to keep." Nothing above
should be read as drifting back toward it under a "deep research" banner.

**Mechanically verified this session, for the ORIGINAL position-range
version** — the underlying `llama_memory_seq_rm`/`_add` mechanics are
unchanged by the turn-alignment fix, only *which* range gets passed to them:
position bookkeeping is exact (`pos_max` after surgery matched the predicted
value precisely). **Numerically not identical to a fresh prefill** — same
top-1 token, but a ranking divergence from rank 4 onward (max|Δlogit|=4.56,
mean=0.68), likely from sliding-window attention on the tested model. One
trial, one model — repeat on `Qwen3-0.6B` (no SWA) before relying on the
magnitude. **The fidelity test (§12 test 4 / test 32) should be re-run
against the turn-aligned version specifically** — dropping at clean
boundaries may reduce this divergence versus the original arbitrary-range
version, but that is a prediction, not yet measured.

**Keeping the first ~32 tokens is deliberate, not arbitrary** — they act as
anchor tokens (per StreamingLLM's finding); dropping them degrades output
sharply. Costs nothing to respect.

**Explicitly not in Phase 1:** summarising (needs a generation pass, and drags
in "is the output still good" — a different research field entirely) and
disk hibernation (additive, add once the basics pass). Quality of compacted
output is measured only via **canary recall** (§12 — corrected from an
earlier draft's stale reference to §9, which doesn't discuss canary recall
at all) — never a scoring model.

---

## 9. Naive scheduler (Phase 1's scheduling — Phase 2 replaces this)

**Read this section for the *reasoning* (the fg/bg group-split idea, why token-rate throttling
was rejected, why real browser visibility matters) — §9a is what's actually implemented.**
`NaiveScheduler.pick()` below is **not** the class Phase 1 builds; §9a's `build_batch()` replaces
its selection logic entirely (round-latency budget instead of slot count). Stated explicitly here,
not just at §9a, since this is the first place an implementer would land looking for "the
scheduler."

```python
class NaiveScheduler:   # NOT IMPLEMENTED — superseded by §9a's build_batch(); kept for the
    def pick(self, sessions):   # reasoning below, not as a second valid implementation
        runnable = [s for s in sessions if s.state != IDLE]
        fg = [s for s in runnable if s.visibility == "foreground"]
        bg = [s for s in runnable if s.visibility != "foreground"]
        if not fg and not bg: return None
        if not bg: return self._rr(fg, "fg")     # work-conserving
        if not fg: return self._rr(bg, "bg")     # work-conserving
        self.fg_turn = not self.fg_turn
        return self._rr(fg,"fg") if not self.fg_turn else self._rr(bg,"bg")
```

**50/50 is a split between the foreground *group* and the background *group*,
applied once — not "each session gets 50%."** Background's 50% share is then
split again by round-robin among however many background sessions exist (1
foreground + 3 background ⇒ foreground 50%, each background 16.7%).

**Work-conserving, not a hard ceiling:** an idle group's share goes entirely to
the other. A lone foreground session with no background contention gets 100%,
not 50%. Nothing here is a `cpu.max`-style rate limit — there's exactly one
compute thread; there's nothing to throttle. What's actually controlled is
**how often that one thread's next token goes to which session** — i.e. the
*rate* a session gets picked, not a ceiling on total usage over time.

**What this naive policy does NOT do (correctly, deliberately deferred):**
- **no per-tab cumulative cap across multiple requests, over time — clarified
  distinction, not yet built.** §8's `max_output_per_request` bounds ONE
  request. Nothing bounds how much a session can generate cumulatively across
  many turns over its lifetime — that requires tracking usage over time
  (VTC-style accounting), explicitly deferred to Phase 2. Do not conflate the
  two when describing what Phase 1 covers: per-request cap exists; per-tab
  cumulative cap does not.
- becomes `MALABR-NAIVE`, kept as a baseline row later: does a dumb fixed
  split already capture most of the benefit a sophisticated policy would add?

**Tokens as a CPU-capping mechanism — TESTED and REJECTED, do not rely on
this.** Investigated whether throttling token generation rate (inserting a
delay between `decode()` calls to target a lower tokens/sec) could serve as an
application-level substitute for a kernel CPU quota. Measured, with corrected
methodology (one CPU-time sample over the full window, not rapid sampling —
same class of error as the earlier per-core-percentage mistake, caught and
fixed before trusting the result):

```
unthrottled (max rate):        43.5 tok/s → 399% CPU
throttled to 50% of max rate:  21.9 tok/s → 318% CPU   (expected ~200% if proportional)
throttled to 25% of max rate:  11.0 tok/s → 250% CPU   (expected ~100% if proportional)
```

**Not proportional, and there is a floor (~250%) that further throttling
cannot get below.** Plausible cause, flagged as unverified rather than
asserted — llama.cpp's source is not in this checkout, so this is inference
from the pattern, not confirmed from source: high-performance thread pools
commonly spin-wait between jobs rather than block, to avoid wake-latency on
the next call. `time.sleep()` between Python-issued `decode()` calls only
pauses the *Python* thread; it says nothing about llama.cpp's own worker
threads, which may keep consuming CPU regardless of whether new work is being
requested.

**Conclusion: token rate is a genuine, useful lever for scheduling FAIRNESS
(§9's 50/50 rule — deciding who gets dispatched next) but not for capping
total CPU CONSUMPTION.** These looked like they might unify into one
mechanism; measurement shows they do not. The `cpu.max`/`CPUQuota` mechanism
(§11) remains necessary and is not replaceable by token-level throttling.

**Real browser visibility — IN Phase 1, corrected from an earlier draft.**
A "naive scheduler" that never sees a real signal only proves it can alternate
between harness-applied labels, not that it responds to anything real. The
required C++ work is small and already confirmed present in this checkout:
`WebContents::GetVisibility()` for current state, plus a `TabStripModelObserver`
in `MalabrManager` that watches tabs holding active sessions and pushes a
lightweight `visibility_changed` update down the socket (not a new request —
the engine applies it to the matching session's `visibility` field, read live
by `build_batch()` on the next dispatch). See §10 for the C++ scope this adds.

**Background → foreground rate increase — already covered, no separate
mechanism needed.** `build_batch()` reads `s.visibility` fresh on *every*
dispatch round, never cached. The moment a `visibility_changed` update lands,
the very next batch composition — at most one round later — applies the new
share. Worth one explicit test in §12 (promote a background session, confirm
its token rate visibly jumps within one dispatch round) but requires no code
beyond this observer wiring.

**`visibility_changed` arriving before a session exists — must be a defined
no-op, not an unhandled lookup.** The `TabStripModelObserver` watches *tabs*,
not *sessions* — it can fire for a tab that hasn't sent a `generate()` request
yet, so `registry.get((ext_id, tab_id))` can legitimately miss. **Rule:**
silently discard the update in that case (there's no `visibility` field to set
on a session that doesn't exist). Explicitly stated so the lookup-miss path
doesn't throw or, worse, get handled by accidentally creating a malformed
registry entry with no slot behind it.

---

## 9a. Batch composition — cost-weighted round budget, not a slot-count split.

> **CORRECTION (implementation_notes #7).** The prose below says a deep
> session "simply runs alone in its own round, spending the whole budget on
> itself, which harms nobody else". The accompanying code gives that
> unconditional-first-pick escape **only to foreground**. Measured: a
> background session at pos 6000 (100 ms against a 50 ms round) was excluded
> 60/60 rounds with no foreground present — permanent starvation, not the
> bounded limit this section describes. Aged background sessions now get the
> same `or not picks` escape, which bounds the exclusion streak at exactly 5.
DESIGNED, NOT YET IMPLEMENTED OR MEASURED — see status note at the end.

**The gap this closes.** §7's original `build_batch()` split the batch 50/50 between the
foreground and background *groups* and filled unused slots work-conservingly. Two problems, found
by tracing through what a single `llama_decode()` call actually costs (§11's own cost model:
`cost ≈ shared weight-read (once per call) + Σ per-entry KV-read (∝ that entry's own position)`),
not by inspecting the scheduling code in isolation:

1. **The split can never actually exclude anyone under the stated default.** `BATCH_SIZE` defaults
   to `n_seq_max`, and admission is already capped at `n_seq_max` — so the number of sessions that
   can ever be `GENERATING` at once is always ≤ `BATCH_SIZE`. Every runnable session fits in one
   batch regardless of group, so the 50/50 split and its round-robin never engage.
2. **Even with a smaller `BATCH_SIZE`, the split still fails, for a more basic reason.** There is
   normally exactly one foreground session — one active window, one active tab. `fg_slots =
   batch_size // 2` is sized as if multiple foreground sessions might compete for it; they don't.
   Foreground fills 1 of its allotted slots, and `fill_unused_slots` — the work-conserving step —
   hands its other slots to background. Background ends up with the *majority* of most batches,
   the opposite of the intended priority.
3. **Batching does not make heterogeneous-depth sessions cheaper to share a round.** A batched
   `llama_decode()` call is one synchronous forward pass; nobody in it gets a token back until the
   whole pass returns, and the pass's cost is the *sum* of every included session's own KV-read
   cost, not the max and not something split fairly by cheapness. A shallow foreground session
   sharing a round with a deep background session inherits the deep session's cost on every token
   computed in that shared round — confirmed by the doc's own Phase C number: 4 sessions at depth
   1500, batched, gave 12.5 tok/s **aggregate**, 3.1 tok/s **each** — worse than naive division of
   a single session's solo rate at that depth (25.1 tok/s), because the KV-read terms stack rather
   than share. This directly threatens the ~23–50ms per-token bound §6a/§7 already claim and test
   (§12 tests 8, 23, 49) — those bounds were measured from single-session decode timing, not from
   a round shared with an expensive session.

**The fix — compose each round by spending a latency budget, not by filling slots.** Calibration's
Phase B curve (tok/s at a given position) already gives everything needed: invert it to a
per-session cost estimate, `cost_ms(pos) = 1000 / tps_at(pos)` (§11a's Phase D output extended
below), and admit sessions into a round until the budget is spent, cheapest first, with foreground
admitted unconditionally ahead of everything else.

**Admission uses `cost_ms_worst`, not the median-based `cost_ms` — a real gap in an earlier draft
of this section, caught in a later audit pass.** §11a already distinguishes two statistics from
the same curve for two different purposes: median for the output-cap formula (representative), and
worst-observed for Phase C/D's safety gate (because the measured trial-to-trial spread is real —
up to 19% at short context). An earlier version of this admission rule used the median-based
`cost_ms` for a *hard* 50ms cutoff — exactly the kind of safety-relevant decision §11a already
argues should use the worst-observed statistic, not the median. Using the median here means a
round estimated at 45ms could, on a noisy trial, actually run closer to 54ms — silently blowing
the budget on the case this rule exists to bound. Fixed by adding a second function to Phase D's
output, `cost_ms_worst(pos)`, derived the same way but from the worst-observed trial at each
position rather than the median — same data already collected, no new measurement:

```python
ROUND_LATENCY_BUDGET_MS = 50         # matches the bound §7 already commits to elsewhere
MAX_CONSECUTIVE_EXCLUSIONS = 5       # aging bound — see below; no fixed value measured yet

def build_batch(registry, cost_curve):
    runnable = [s for s in registry.sessions.values() if s.state == GENERATING]

    # §5f: foreground is ONE TAB id on the registry, re-read fresh every
    # round, never cached. At most one TAB can be foreground by construction.
    # None == no foreground (no active window, or the control connection is
    # down and we have deliberately degraded to uniform treatment).
    fg_tab = registry.foreground_tab_id
    fg = [s for s in runnable if s.tab_id == fg_tab]   # normally 0 or 1; >1 only
    bg = [s for s in runnable if s.tab_id != fg_tab]   # if 2 extensions share it

    picks, budget = [], ROUND_LATENCY_BUDGET_MS
    for s in fg:                                   # foreground always admitted first —
        c = cost_curve.cost_ms_worst(s.pos)         # never excluded, even if it alone
        if not picks or c <= budget:                # exceeds the budget on its own
            picks.append(s); budget -= c

    # AGING PASS — force-priority for any background session that's been excluded too
    # long, BEFORE cheapest-first gets another chance to pass over it again. Without
    # this, a moderately expensive bg session can lose to the same cheaper bg sessions
    # every single round, indefinitely — real starvation, just moved from fg-vs-bg
    # (the old bug) to bg-vs-bg.
    aged = sorted([s for s in bg if s.rounds_excluded >= MAX_CONSECUTIVE_EXCLUSIONS],
                  key=lambda s: -s.rounds_excluded)   # longest-waiting first
    for s in aged:
        c = cost_curve.cost_ms_worst(s.pos)
        if c <= budget:
            picks.append(s); budget -= c

    # NORMAL FILL — cheapest-first among whatever's left, same greedy rule as before
    remaining = sorted([s for s in bg if s not in picks], key=lambda s: cost_curve.cost_ms_worst(s.pos))
    for s in remaining:
        c = cost_curve.cost_ms_worst(s.pos)
        if c <= budget:
            picks.append(s); budget -= c

    for s in bg:                                    # bookkeeping for every bg session,
        s.rounds_excluded = 0 if s in picks else s.rounds_excluded + 1   # picked or not

    return picks
```

**Why cheapest-first, not arrival-order:** admitting by request recency (FIFO) lets whichever
session happened to ask first — deep or shallow — consume the whole budget alone, stranding
several cheap sessions that would collectively have fit. Cheapest-first is the greedy choice that
maximises how many sessions a round can serve without breaking the budget.

**The aging pass's honest limit — it raises priority, it does not guarantee admission within a
fixed number of rounds.** If foreground is present and saturating most of the budget every single
round, a very expensive aged background session can still be skipped past `MAX_CONSECUTIVE_EXCLUSIONS`
in the worst case — aging makes it *first in line* for whatever budget background gets, it doesn't
manufacture budget that isn't there. A stronger guarantee (e.g. force-admit regardless of budget
once a session ages out completely, the same unconditional treatment foreground gets) was
considered and deliberately not adopted here — it would reintroduce exactly the latency-blowout
problem this whole rule exists to prevent, just for a different session. Left as a known, stated
limit rather than a silently assumed one; worth revisiting with real contention data before Phase 2.

**What this subsumes, so it isn't read as three separate mechanisms:** it replaces the old
`fg_slots`/`bg_slots` split (foreground's "leftover slots" problem above disappears — nothing is
donated, spare budget is just spent on whatever fits), and it naturally produces "batch shallow
sessions together, run deep sessions closer to one-at-a-time" without a separate special case —
a deep session that doesn't fit alongside anything simply runs alone in its own round, spending the
whole budget on itself, which harms nobody else and is consistent with Phase C's own finding that
batching stops paying for itself once sessions are deep (batched-4-at-depth-1500 barely beat naive
serial execution).

**Status — designed, not built or measured.** This corrects a reasoning gap in the existing
design (§7 already claims the ~50ms bound; this section is what actually makes that claim hold
under concurrent, mixed-depth generation, not new scope). But nothing here has run against real
hardware yet. Before relying on it: (1) `cost_curve.cost_ms_worst(pos)` needs adding to §11a Phase
D's output alongside the existing `max_output_per_request` table — a one-line inversion of data
already collected, not a new measurement; (2) needs its own Phase-C-style joint measurement —
mixed-depth sessions (not the uniform-depth 4-session test that exists today), composed under this
rule, checked against the ~50ms target the same way Phase C caught the old design's 3x
extrapolation error; (3) needs a new §12 test (see test 52 below) checking that a foreground
session sharing rounds with a much deeper background session still gets picked every round and
that the measured round latency stays within budget, not just that token counts look proportional;
(4) needs a second test (test 55) specifically for the aging pass — several background sessions of
different costs, confirm none of them is excluded for more than `MAX_CONSECUTIVE_EXCLUSIONS`
rounds while foreground is absent or light, and confirm the stated limit (aging can still be
outrun when foreground saturates the budget every round) is what actually happens, not a worse,
unbounded starvation.


---

## 9b. Chunked prefill, and a closed loop on the latency budget —

> **CORRECTION (implementation_notes #8).** The prefill chunk-minimum rule
> below starves if taken literally: "if n < PREFILL_CHUNK_MIN and n <
> remaining: continue" holds EVERY round whenever decode picks keep leaving
> less than a minimum chunk's budget. Measured: 200 rounds, zero chunks. This
> section bounds decode starvation with aging but leaves prefill unbounded.
> After `MAX_PREFILL_STALLS`, one chunk is forced through — overrunning the
> latency bound by at most one chunk, which is finite, unlike never answering.
closes the two items §9a left open

### Part 1 — prefill joins the budget instead of bypassing it

**The problem §9a did not cover.** `build_batch()` selected only
`GENERATING` sessions, so a `PENDING` session's prefill happened *outside*
the round budget entirely: one `llama_decode()` over the whole prompt, on a
single-threaded engine, with everything else frozen until it finished. A
2000-token paste in a background tab would stall a foreground response for
the full prefill duration — and the ~50ms bound said nothing about it,
because prefill is not a token loop.

**The fix is a cited technique, not an invention:** Sarathi-Serve (OSDI 2024,
already in this project's literature survey as *"work in small chunks so
nothing gets stuck waiting"*) splits prefill into chunks and mixes them into
the same forward pass as decode work. `llama_batch` already supports this —
it holds arbitrary `(token, pos, seq_id)` tuples, so one batch can carry a
prefill chunk for session A alongside single decode tokens for B, C, D.

```python
PREFILL_CHUNK_MIN = 32          # below this the per-call overhead dominates

def build_batch(registry, cost_curve):
    # ... foreground/aging/cheapest-first decode admission exactly as §9a ...
    # `budget` now carries whatever is LEFT after decode picks.

    # PREFILL fills the remainder. Decode is admitted first on purpose: a
    # token owed to a session already mid-response is more latency-sensitive
    # than starting a new one, and this keeps §9a's foreground guarantee
    # untouched.
    prefill_chunks = []
    pending = sorted([s for s in registry.sessions.values()
                      if s.state == PENDING],
                     key=lambda s: s.tab_id != registry.foreground_tab_id)
    for s in pending:
        remaining = len(s.inbox_tokens) - s.prefill_offset
        # How many prompt tokens fit in the leftover budget? Uses the PREFILL
        # curve, NOT the decode curve -- see below, they are different costs.
        affordable = cost_curve.prefill_tokens_affordable(budget)
        n = min(remaining, affordable)
        if n < PREFILL_CHUNK_MIN and n < remaining:
            continue                       # wait for a rounder budget
        if n <= 0:
            continue
        chunk = s.inbox_tokens[s.prefill_offset : s.prefill_offset + n]
        prefill_chunks.append((s, chunk))
        budget -= cost_curve.prefill_ms(n)
    return decode_picks, prefill_chunks
```

**Prefill cost is NOT decode cost, and reusing the decode curve would be
wrong.** Decode is memory-bandwidth-bound: one token, but every prior K/V
vector must be read — which is why §11's curve collapses from 48 to 5 tok/s
with depth. Prefill is compute-bound and parallel: many tokens processed in
one pass, amortising the same weight read. Prefill throughput is typically an
order of magnitude higher and scales differently with depth. **§14 already
records that prefill speed is entirely unmeasured** — every number in this
document is decode-only — so calibration needs a new phase:

> **Phase B-prefill.** Measure prompt-processing throughput at several
> prompt sizes and several starting depths. Emit `prefill_ms(n_tokens, pos)`
> and its inverse `prefill_tokens_affordable(budget_ms)` as Phase D outputs,
> alongside the decode curve. Use **worst-observed**, not median — this feeds
> an admission cutoff, the same reasoning as `cost_ms_worst` (§11a).

**What this does and does not guarantee.** A large prompt no longer blocks
the engine for its full duration; it is spread across rounds, and foreground
decode keeps its unconditional admission throughout. The cost is that a large
prompt takes *longer in wall-clock* to become answerable, because it now
shares rounds instead of monopolising them — the correct trade, since the
alternative froze every other session.

### Part 2 — a closed loop, so a wrong cost model degrades instead of lying

**The deeper problem: §9a's budget is only as good as its cost model, and
that model is an assumption.** `build_batch` sums `cost_ms_worst(pos)` across
members and admits while the total fits 50ms. That assumes round cost ≈ sum
of member costs. Plausible from the bandwidth model — but **this project has
already been burned by exactly this class of assumption**: Phase C's naive
extrapolation predicted ~41 tok/s where the real joint measurement gave
**12.5 tok/s**, a 3× error in the optimistic direction. Nothing currently
stops the same thing happening here, and it would fail *silently*: rounds
would simply take longer than 50ms while the code believed they fit.

**Fix — measure the real round, feed it back:**

```python
class CostCurve:
    def __init__(self):
        self.correction = 1.0          # multiplies every estimate

    def observe_round(self, estimated_ms, observed_ms):
        if estimated_ms <= 0:
            return
        ratio = observed_ms / estimated_ms
        # EWMA: one slow round (a GC pause, a cpu.max throttle window) must
        # not swing admission. Sustained divergence must.
        self.correction = 0.9 * self.correction + 0.1 * ratio
        self.correction = clamp(self.correction, 1.0, MAX_CORRECTION)

    def cost_ms_worst(self, pos):
        return self._raw_worst(pos) * self.correction
```

**Clamped at a floor of 1.0 deliberately** — the loop may only make estimates
*more* conservative, never less. An optimistic correction would let a lucky
run of fast rounds widen batches until the bound breaks, which is the exact
failure being defended against.

**Why this matters beyond tuning:** it converts §9a from open-loop to
closed-loop. Open-loop, a wrong model silently violates the bound and only a
dedicated experiment would reveal it. Closed-loop, a wrong model shrinks
batches until reality fits — the bound holds even when the model is wrong,
which is the property actually wanted. It is the same principle §11a already
applies with Phase F's clamps: *trust calibration to optimise within bounds
that hold even when it is wrong.*

**Still requires measurement — this is not a substitute for testing §9a.**
The loop bounds the damage from a wrong model; it does not tell you the model
is wrong. Tests 52/55 still need to run, and test 62 below checks the loop
itself.

---

## 10. C++ changes — all in one build

> **CORRECTION (implementation_notes #13).** The ordering is the load-bearing
> part: the socket may be unlinked **only after** the pid lock has confirmed no
> live owner. Otherwise a second process deletes a live instance's socket and
> steals the path.

**STATUS: WRITTEN, NOT YET COMPILED (2026-08-22).** Every item in the table
below is implemented across 15 files (+1241/−524). Nothing has been through a
compiler — the build was stopped deliberately, so expect include/signature
errors on the first real build. Cross-file references were verified by grep
(signatures match across headers and call sites, no dangling references to the
removed v3.0 API, every used Chromium API confirmed present in this checkout),
but grep is not a typechecker.

**Six things ended up DIFFERENT from what this table originally specified.**
Each was found by implementing it, and each is documented at the section
listed — recorded here so the table is not read as still-accurate where it
was superseded:

| Original plan | What was actually built | Why |
|---|---|---|
| `SafeRef<RenderProcessHost>` held alongside the frame (§6a) | **No cached process at all** — derived from the already-validated `RenderFrameHost` at dispatch time | `SafeRef` CHECK-fails when its target dies, converting a recoverable "destination went away" into a browser crash. Deriving it cannot be stale by construction |
| Tab close = two paths, one of which actively closes the request socket (§6a Case 1) | **One path**: `TAB_CLOSED` over the control connection covers streaming *and* idle | The request socket is owned by `MalabrGenerateFunction` on a pool thread; `MalabrManager` would need a whole registration channel to reach it, for no gain (§6a) |
| Per-session `visibility` field, updated by `visibility_changed` | **One `foreground_tab_id`** on the registry, one message per switch | A per-session boolean can represent "5 sessions foreground at once" — a real bug with 5 tabs. One key makes it unrepresentable (§5f) |
| Session key `(extension_id, tab_id)` | **`(extension_id, tab_id, origin)`** | Cross-origin navigation in one tab leaked a bank conversation into the next site's session (§5g) |
| Control channel carries 2 message types | **Four**: `FOREGROUND`, `TAB_CLOSED`, `LIVE_TABS`, `EXT_UNLOADED` | `LIVE_TABS` recovers slots whose `TAB_CLOSED` was lost while disconnected; `EXT_UNLOADED` is §10's own sweep requirement (§5f) |
| `SO_RCVTIMEO` as a fixed constant | **`base::FeatureParam`** (`MalabrTunables:frame_read_timeout_seconds`) | The value is an acknowledged placeholder until prefill is measured, and a rebuild here costs 8-9 hours. Tunable without rebuilding |

**Thirteen further holes were found by auditing the written code** and are
fixed in place: control connection that might never connect, exit-time
destructor touching `BrowserList`, dropped control message on write failure,
missing extension sweep, resync payload rebuilt and discarded, duplicate
`FOREGROUND`, `EventRouter::Get(nullptr)` on profile shutdown, dangling
`ExtensionRegistry` observation, dead members, the socket path diverging
between C++ (hardcoded) and Python (env var), silent `sun_path` truncation,
unbounded outbound prompt, and opaque origins all colliding on `"null"`.


| File | Change |
|---|---|
| `_api_features.json` | add `"content_script"` to `malabr`'s contexts |
| `mserver_uds.{h,cc}` | `tab_id`, `visibility` params; header string; `Send()` → streamed-frame loop |
| `msocket_uds.cc` | **`SO_RCVTIMEO`** (currently NO timeout — `recv()` blocks forever on a wedged server, leaking a browser thread permanently); split `EAGAIN` from `ECONNRESET` in `MapErrno` (a timeout must not be misreported as a reset) |
| `mserver_uds.cc` | **bound `response_len`** before allocating (currently unbounded — `std::string response_accum(response_len, '\0')` allocates whatever the wire says, no sanity check) |
| `protocol.py` | **bound `payload_size`** before `recv_full` — confirmed in source (`protocol.py:153`) this is checked ONLY for `< 0`, no upper bound at all. Exact mirror of the `response_len` bug above, on the opposite direction of the wire — a co-resident process with direct UDS access (§6.7 threat actor 4) could claim a payload of arbitrary size and the server will attempt to receive/buffer it |
| `mserver_uds.cc` | **remove response-body logging** (`LOG(INFO) << "Response Read done! " << response` — currently logs full generated text, i.e. entire conversations, into the Chrome log) |
| `malabr_api.{h,cc}` | new `MalabrGenerateFunction`, following the existing 4-class pattern; derives identity per §5; `DispatchEventToSender` per §6 |
| `malabr.idl` | declare `generate` function + `onToken`/`onComplete` events |
| `extension_function_histogram_value.h` | one new enum value (touches 41+ files transitively — do in the same build as everything else, not separately) |
| `BUILD.gn` (extensions/browser/api/malabr) | add `components/sessions` dependency |
| `malabr_manager.{h,cc}` | **added to Phase 1 scope** (was Phase 2) — implement `TabStripModelObserver`, watch visibility changes AND tab removal (`kRemoved`, `will_be_deleted=true`) on tabs with active sessions. Visibility changes push `visibility_changed`; removal actively closes that session's socket (§6a) rather than waiting for a read timeout. One observer, two event types. |
| `malabr_api.{h,cc}` | additionally: hold caller identity as `content::WeakDocumentPtr` (frame) and `base::SafeRef<RenderProcessHost>` (process), not raw pointers — §6a |
| `malabr_manager.{h,cc}` | **additionally implement `ExtensionRegistryObserver`** (`OnExtensionUnloaded`, `OnExtensionUninstalled` — both confirmed present in `extensions/browser/extension_registry_observer.h`). Tab-close (§6a) tears down one session at a time; this tears down **every** session belonging to a disabled/uninstalled extension in one sweep, across all its tabs, rather than waiting for each tab to close individually. A real gap without this — an uninstalled extension's sessions would otherwise sit holding slots until their tabs happen to close. |
| `malabr_manager.{h,cc}` | **open and hold a dedicated control connection** (§5e) — one per browser session, separate from per-request `generate()` sockets, carrying `visibility_changed` and idle-session `tab_closed` pushes. Also add `BrowserListObserver` (§5a) alongside the existing `TabStripModelObserver`, feeding this same connection. |
| `runtime.py` | **a dedicated reader thread for the control connection**, separate from the `ThreadPoolExecutor` handling `generate()` calls — permanently blocked on `recv()`, updates `session.visibility` or triggers teardown directly on message arrival |

**Connect retry:** model load takes ~3s (measured); `MalabrManager` has no
readiness check on the spawned Python process. Add a short retry-with-backoff
inside `Send()`'s `Connect()` rather than touching `MalabrManager` — avoids a
race on browser startup.

**Backoff must be jittered, not fixed-interval — smaller risk than first
suspected, still a real refinement.** Traced through: session creation is
lazy, tied to the first actual `generate()` call, not page load — restoring
many tabs at browser startup does not itself trigger server connections
(local history restore via `chrome.storage.session` touches no network).
Real exposure is narrower: several tabs sending messages within the first
~13s of a cold start (model load + calibration, §11a). Still worth fixing —
a fixed retry interval risks several tabs retrying in lockstep, synchronizing
into a burst the moment the server becomes ready; add a small random jitter
to each retry's delay so simultaneous cold-start requests don't correlate.

**Single-instance enforcement — a real gap, and the existing "delete stale
socket" pattern makes it actively dangerous, not merely redundant.** The
inherited `runtime.py` deletes the stale socket file before binding — safe
only if the old process is genuinely dead. If `MalabrManager` ever launches a
second `app.py` while a first is still alive (retry-logic bug, or a browser
crash-and-restart that doesn't guarantee the old child process actually
died), the new instance's startup would **delete the live instance's socket
and steal the path** — orphaning the first instance, which still holds
sessions and slots nobody can reach anymore, silently. Fix: a PID lockfile
checked before binding, not stale-socket cleanup alone — refuse to start (or
verify the PID in the lockfile is actually dead first) rather than assuming
any existing socket file means a dead process.

**Build config:** current `args.gn` is empty → default debug build, full
symbols — the slowest possible configuration (this is why the original build
took 8.5h). Reconfigure (`is_debug=false`, `symbol_level=0`,
`is_component_build=true`) **before** starting C++ work; this forces one more
full rebuild but makes every subsequent incremental build fast. Do this
overnight while Phase 1 Python work continues — no dependency between them.

**Do NOT sync to a newer Chromium checkout.** Everything Phase 1 and Phase 2
need (`WebContents::GetVisibility()`, `TabStripModelObserver`,
`SessionTabHelper`) is present in the current pinned revision (127.0.6519.0).


---

## 10a. The exact contract the Python side must implement

> **CORRECTION (implementation_notes #9).** The claim that `protocol.py`
> "splits the header into 3 fields and raises otherwise" is not what it did. It
> used `split(",", 2)` — maxsplit=2 — so a 6-field header yields exactly THREE
> parts, the count check PASSES, and it fails later on `int()` of the merged
> remainder with "invalid payload size". Same outcome, different mechanism.
> Separately, `mserver_uds.cc`'s comment claiming origin may be the literal
> "null" for opaque origins is stale: `malabr_api.cc:77` rejects opaque origins
> before that point.

**Read this first when starting the Python work.** The C++ side is written
and will emit exactly what follows. Everything here is settled — if Python
disagrees with any of it, Python is wrong.

### Where Python currently breaks

`protocol.py:143-144` splits the header into **3** fields and raises if it is
not exactly 3. The browser now sends **6**. Every `generate()` and every
control connection is rejected today. That is the first thing to fix.

### Wire format — request connections

```
[4B BE length][header ASCII][payload bytes]

header = "route,extension_id,tab_id,origin,visibility,payload_size"

  route          "ROUTE_MALABR_GENERATE_API"
  extension_id   32 chars, a-p          (browser-derived, trusted)
  tab_id         int, -1 == not tab-scoped, treat as always background
  origin         "https://host:port"    (browser-derived; NEVER "null" --
                                         opaque origins are rejected in C++)
  visibility     "foreground"|"background"   SEED ONLY -- see §6's ordering
                                             rule; ignored for an existing
                                             session
  payload_size   bytes of prompt that follow. MUST be bounded before
                 recv_full (§10) -- C++ caps outbound at 1 MiB, but the
                 server cannot rely on the peer being ours
```

Responses stream back as frames until exactly one terminal frame:

```
[1B type][4B BE length][payload]
  type 0 = token chunk    (UTF-8 safe -- buffer partial sequences, §6d)
  type 1 = complete
  type 2 = error          (payload is the reason: cap hit, cancelled,
                           superseded -- must be distinguishable from
                           a clean finish, §6/§6b)
```

### Wire format — the ONE control connection

Opens with the same 6-field header, route `ROUTE_MALABR_CONTROL`, then
`payload_size=0`. The server must recognise this route and hand the socket to
a dedicated reader thread rather than treating it as a request (§5e). It is
**write-only from the browser**; Python never replies on it.

Subsequent messages are `[4B BE length][ASCII]`, exactly four kinds:

| Message | When | Python must |
|---|---|---|
| `FOREGROUND,<tab_id>` | every tab/window switch, and on every (re)connect | `registry.foreground_tab_id = tab_id`. `-1` means none. Single assignment, atomic under the GIL, no lock (§5f) |
| `TAB_CLOSED,<tab_id>` | real tab close only (never a drag between windows) | cancel any generation for that tab, free the slot, drop the session (§6a) |
| `LIVE_TABS,<id>,<id>,...` | on every (re)connect | **reconcile**: any session whose `tab_id` is absent is dead — cancel and free it (§5f) |
| `EXT_UNLOADED,<ext_id>` | extension disabled, updated, or uninstalled | sweep every session for that extension in one pass (§10) |

Messages naming a tab or extension with no session are a **defined no-op**,
not an error (§9) — the browser does not track what sessions exist.

### Three rules that need no new browser signal

1. **Cross-origin eviction.** On creating a session for `(ext, tab, origin)`,
   tear down any existing session with the same `(ext, tab)` and a *different*
   origin. This is what makes §5g work — navigation needs no observer.
2. **Cancellation must produce a terminal frame.** A request handler whose
   session was cancelled must emit a type-2 frame and close, not block waiting
   for tokens that will never come. Without it the browser sits until the 60s
   `SO_RCVTIMEO` instead of ending promptly (§6a).
3. **Reconcile on the control handshake**, not only on explicit messages. A
   server that restarted with no sessions receives `LIVE_TABS` and correctly
   does nothing; one that kept running receives it and reaps (§5f).

### Build order for the Python side

`engine.py` first — `SlotAllocator` (wipe-on-acquire), chat template (§6c),
the single-threaded loop, compaction with the position-shift fix (§8), then
§9a/§9b's scheduler. Then `protocol.py`/`runtime.py` for the 6-field header,
streamed frames, and the control reader thread. Then the §12 harness.

The scheduler (§9a/§9b) is the piece with **zero validation** behind it —
build it last within `engine.py`, and treat tests 52, 55, 62, 63 as the
things that decide whether the design survives contact with hardware.

---

## 11. Sizing — computed at startup from detected hardware, not hardcoded

> **CORRECTION (implementation_notes #14).** The formula below,
> `n_ctx = MemAvailable * 0.8 / BYTES_PER_TOKEN`, is **the wrong formula**, and
> disagrees with §7's stated 16384 by 4.6x — on the development machine it
> yields 75,340 tokens (7.7 GB of KV). It overshoots for three reasons:
> `MemAvailable` counts reclaimable page cache as free, which is true for
> transient allocations and false for a permanent one-shot reservation; it
> never subtracts the model weights, which are also resident; and it leaves
> nothing for the browser, whose protection is the entire point of the design.
> Corrected to `0.5 * (MemAvailable - browser_reserve - model_bytes)`, capped
> at §7's 16384. That lands on exactly 2048 tokens/session on reference
> hardware and degrades sensibly (2 GB -> 4096 floor, 4 GB -> 6880).

**Corrected in this revision: every number below was previously a constant
tuned to this one machine. All of them should instead be computed once, at
Python server startup, from what the running machine actually reports —
`os.cpu_count(logical=False)`, available RAM — so the design generalises to
whatever machine it runs on rather than silently assuming this one's specs.**

**RESOLVED ARCHITECTURE — two governance layers, cleanly separated, not
redundant:**

```
   cpu.max          → PROCESS boundary (engine process vs. browser)
                       kernel-enforced, continuous, holds under real contention
                             ↓ operates entirely inside whatever budget this secures
   calibration data → SESSION boundary (session vs. session, inside the engine)
                       no kernel mechanism possible here — see below — so this
                       is pure software scheduling logic instead
```

**Why this is the only structurally coherent split, not merely a workable
one:** cgroups attach to processes — a PID, an address space the kernel can
hold onto. A session is not that; it is a `seq_id`, a few fields in a Python
dict, sharing one process with every other session. There is no PID a
session-level `cpu.max` could attach to, even in principle — this was the
founding architectural constraint of this entire design (§14's cgroup-analogy
discussion). So the split is not a choice between two options: `cpu.max` can
only ever operate at the process boundary; calibration-informed scheduling is
what has to fill the session boundary, because nothing kernel-level can.

**What "session-wise calibration" concretely means — software logic, not a
second enforcement layer.** Precise on this to avoid the reading "give session
A a hard 30% ceiling, session B a hard 20% ceiling," which would imply a
second kernel mechanism that cannot exist for the reason above. It means the
calibrated cost curve (`short_ctx_tps`, `long_ctx_tps`) feeds two pieces of
scheduling logic already specified elsewhere in this document:
- the position-aware `max_output_per_request` cap (§8/§11's context-growth
  correction) — how many tokens a session may generate before yielding,
  derived from this machine's actual measured cost-per-position
- §9's time-weighted foreground/background split — dividing wall-clock time
  fairly requires knowing, from calibration, roughly what a token costs at a
  given session's current depth

Both run inside the one engine thread, entirely within whatever budget the
process-level `cpu.max` has already secured from the OS. There is no
OS-level contention at the session layer to enforce against — sessions are
not separately scheduled by the kernel; they are data one thread iterates
over (or batches, still bounded by the same process-wide `n_threads`). This
is why the split closes CPU governance completely: process-vs-browser
contention is handled by `cpu.max`; session-vs-session fairness is handled by
scheduling logic informed by calibration — different layers, no overlap, no
gap between them.

**Superseded by a stronger version: MEASURE at setup, don't formula-derive.**
`n_threads = physical_cores * 0.8` is a guess. Actual calibration — briefly
running the real model on the real machine at startup — is strictly better,
and this session already proved the formula version isn't reliable: this
machine's true optimum is `n_threads=4`, i.e. `physical_cores * 1.0`, not
`* 0.8` — a formula would have silently picked the wrong number. Tested a lean
calibration routine (thread sweep + a two-point context-cost probe):

**Reconciling this `n_threads=4` with the `n_threads=3` used as the actual Phase 1 default
later in this section — these are answers to two different questions, not a contradiction, but
worth stating explicitly since nothing below did.** This measurement (Phase A's raw sweep) finds
the *unthrottled, solo* optimum — no `cpu.max` quota running, nothing else competing. The
deployed default derived later (`n_threads = physical_cores × QUOTA_PCT/100 = 3`) answers a
different question: what's optimal *given* the engine is deliberately capped to 80% via `cpu.max`
to leave the browser headroom — and there, matching threads to the quota measurably wins over
leaving them at the solo optimum (44.4 tok/s matched-to-3 vs. 37.4 tok/s unmatched-at-4, both
under an 80% quota — the `CPUQuota` table below). **What calibration's Phase A should actually
hand to the engine is the quota-matched value, not the raw sweep result** — an implementer reading
only this paragraph could otherwise wire `n_threads=4` straight through and silently reintroduce
the nonlinear-loss regime the table below exists to warn against.

```
model load:        0.24s
thread sweep:       2.22s  → best n_threads=4 at 50.1 tok/s (matches earlier findings)
context probe:      7.31s  → short_ctx(64)=47.9 tok/s, long_ctx(1500)=25.3 tok/s
TOTAL:             10.10s, zero decode errors
```

Consistent with every other measurement this session (context=1500 → 25.1
tok/s in the earlier 5-point sweep, `n_threads=4` optimal throughout) — this
is a real, working, fast-enough calibration, not a hypothetical. **~10s on top
of the ~3s model load already happening at startup** — comparable to a
first-run "optimizing for your device" step users already tolerate elsewhere.

**What it should produce:** `n_threads` (measured optimum, not guessed),
`short_ctx_tps` and `long_ctx_tps` (feeds directly into the position-aware
`max_output_per_request` curve this session already flagged as needed —
derived from a real measured cost curve on this machine, not hand-picked
breakpoints tuned to one dev machine).

**Four real design questions this raises, not yet resolved:**

1. **Where does the result live?** Breaks §4's "nothing stored durably" rule
   — but should, deliberately: calibration data is hardware performance
   numbers, not user data, no privacy content. Needs its own small persistent
   store (a local config file), explicitly scoped as a stated exception to §4
   rather than silently contradicting it.
2. **Staleness and invalidation.** A cached calibration goes stale when the
   model file changes (a different model has a different cost curve entirely
   — this isn't just a hardware measurement) or, less predictably, when
   thermal/power conditions differ from calibration time (plugged in vs.
   battery, sustained throttling). Model-file change should force
   recalibration; other drift is accepted risk in Phase 1, not solved.
3. **Calibration measured under unrepresentative conditions.** If it happens
   to run while the machine is under heavy load, results come back
   artificially conservative and the engine under-utilizes the machine for
   the rest of that install (or the reverse, if calibration catches an
   unusually idle moment). No clean fix within a ~10s budget — a full
   statistical procedure would blow past it. Stated as a known limitation,
   not solved.
4. **This is new Phase 1 scope, with its own failure modes.** A calibration
   routine can itself crash, time out, or produce nonsense on unusual
   hardware — needs an explicit conservative fallback (e.g. `n_threads=2`,
   small `n_ctx`) if calibration errors out, not an assumption it always
   succeeds.

**Note on methodology, kept for the record rather than silently fixed:** the
first version of this test produced `short_ctx=41323 tok/s` — physically
impossible, caused by an unchecked `llama_decode()` return code failing
silently and returning near-instantly (the same class of bug as the earlier
switch-cost measurement in this session). Caught before being reported;
retested with error-checking added, `0` decode errors on the clean run. Worth
keeping as a reminder for whoever implements this: **always check
`llama_decode`'s return value in a calibration routine**, since a silent
failure there produces a plausible-looking but meaningless number.

**Scope, stated precisely so this isn't read as "calibration solves
everything" — it solves MEMORY fully, CPU partially, and context-growth cost
not at all on its own:**

| | Does calibration alone fix it? |
|---|---|
| Memory sizing | **Yes** — stock resource, self-enforcing once allocated |
| CPU *number* (what `n_threads` should be) | **Yes** — measured beats guessed |
| CPU *enforcement* (holding under real contention) | **No** — still needs the `cpu.max` quota running continuously alongside it (§11's earlier CPU subsection); calibration improves the input to that mechanism, it does not replace it |
| Context-growth cost (position-aware output caps, time-weighted scheduler split) | **No** — calibration supplies the cost curve; the caps and scheduler fix that consume it are separate mechanisms still to build |

**Phase 2 refinement, consistent with the staleness question above:** a
usage-over-time tracker (VTC-style, already Phase 2 scope per §9) is a better
trigger for *when* to recalibrate than the simple "model file changed" rule
Phase 1 uses — sustained drift in measured vs. expected throughput could
signal thermal/power conditions have shifted enough to warrant a fresh
calibration. Recorded as the natural Phase 2 answer to item 2 above, not
built now.

**Memory — a startup-computed multiplier, and this one genuinely does not
need `cgroup memory.max`.** Verified this session: `n_ctx` is allocated once,
as a single fixed block, at context creation — RSS measured flat (~1139MB)
regardless of whether 1 or 8 sessions were using the pool. Memory is a
**stock**: the instant that allocation completes, the RAM is reserved by the
OS, and there is no runtime path by which the pool can grow past what was
allocated. A `cgroup` memory ceiling would be redundant on top of that — the
allocation already cannot exceed itself.

```python
n_ctx = int(detect_available_ram_bytes() * 0.8 / BYTES_PER_TOKEN)  # ~107KB/token, measured
```

`n_ctx = 4096` (this machine, hardcoded) was too small regardless of the
above: at `n_seq_max=8` it gives 512 tokens/session ≈ 6-8 short chat turns
before compaction fires on *every* conversation — testing "does compaction
work at all" rather than "does the policy correctly decide whose context to
shrink under real contention." A startup-computed value on typical hardware
lands well above this; the failure mode to avoid is a fixed small constant,
not the mechanism itself.

**This covers the KV pool specifically, not all memory in the process.** The
`outbox` queues and per-session Python bookkeeping (§7's engine-loop section)
are ordinary heap allocations, *outside* this fixed block — they still need
their own separate bound (already flagged: a stalled reader must disconnect
the session, not let its queue grow unbounded). The startup-computed pool
solves the part of memory that behaves like a stock; it does not solve parts
that behave like a flow.

**`n_seq_max` is a real, stated tradeoff, not a free parameter** — more slots
(more concurrent tabs) directly divides less context per tab, whatever the
computed `n_ctx` turns out to be. Record it as a parameter with a known cost;
the resulting "slots vs. context-length" tradeoff is itself a small measured
finding worth stating (nobody else states this tradeoff because nobody else
bounds memory at all).

**CPU — a startup-computed multiplier too, but this one is NOT sufficient on
its own, and the reason is structural, not incidental.** `n_threads` is a
request for how many workers to *spawn* — it does not reserve wall-clock CPU
*time* the way an allocation reserves RAM. CPU is a **flow**: the OS scheduler
arbitrates every scheduling quantum, continuously, regardless of what thread
count was requested earlier. Choosing `n_threads = round(physical_cores * 0.8)`
once at startup controls how many cores the engine will *try* to use — it
provides no guarantee about what happens the instant something else (Chrome's
UI thread) genuinely wants the same physical cores at the same moment.

**Evidence for the distinction — already measured, not asserted:** a real
`cpu.max`-style quota (`CPUQuota=` via `systemd-run --user --scope`, tested
without root) produced a clean, monotonic, kernel-enforced ceiling:

```
100% quota: 50.2 tok/s
80%  quota: 37.4 (n_threads left at 4, unmatched) / 44.4 (matched to 3)
60%  quota: 19.6 (unmatched) / 37.7 (matched to 2)
50%  quota: 14.6 (unmatched) / 37.7 (matched to 2)
```

That degrades predictably because the kernel is genuinely enforcing it — this
is what "does not need anything further" looks like. A thread-count multiplier
alone does not produce this property; it happens to behave fine on an
otherwise-idle machine and offers no guarantee once real contention exists.

**Attempted to demonstrate the gap directly with `os.nice()` and got an
unreliable result — reported honestly rather than cherry-picked.** Two runs
under genuine 12-contender oversubscription gave `nice=0: 12.5 tok/s` then
`nice=19: 46.3 tok/s` — backwards from what lower priority should produce,
almost certainly a test-methodology artifact (likely process-cleanup timing
between sequential runs) rather than a real finding. **This does not weaken
the conclusion** — it stands on the `CPUQuota` evidence above, which is clean
and repeatable, independent of the failed `nice()` attempt.

**Practical conclusion:** memory needs only a startup-computed size (stock
resource, self-enforcing). CPU needs a startup-computed thread count **plus**
a matching `cpu.max` quota, both applied once at startup (§11's earlier CPU
subsection) — the multiplier is necessary but not sufficient by itself, unlike
the memory case where it is.

**`n_threads`, startup-computed:**
```python
physical_cores = os.cpu_count(logical=False)
n_threads = round(physical_cores * 0.8)
```
On this machine that resolves to 4 physical cores → `n_threads=3`. Measured
separately that requesting more threads than physical cores hurts, not helps:
8 threads (this machine's logical count) was *slower* than 4 (43.4 vs 34.1
tok/s) — hyperthread siblings contending for the same execution units, not
adding real parallelism. Detecting `logical=False` specifically (not
`os.cpu_count()`'s default, which returns logical count) matters for this
reason.

**CPU headroom — the engine must never claim 100%, and the reason is
mechanical, not stylistic.** Requesting all physical cores for inference
starves Chromium's own UI/rendering threads on the same machine — verified
directly: `n_threads=4` alone was measured at ~400% CPU (psutil convention),
i.e. all 4 physical cores saturated near-continuously for the duration of a
generation. On a 4-physical-core machine that leaves nothing for the browser
itself while a response streams.

**A naive fix — sampling `psutil.cpu_percent(percpu=True)` and reactively
lowering `n_threads` — was tried and REJECTED, with a measured reason.** Under
simulated 85% contention (3 background processes at 85% duty cycle), the
per-logical-core busy% came back scattered (`[43,11,2,5,46,77,4,85]`) rather
than cleanly showing "3 cores busy, 1 free" — Linux's scheduler moves
processes across logical cores between samples, and logical-core percentages
don't map cleanly to physical-core availability on a hyperthreaded machine
(2 logical cores per physical core). Backing off to a computed `n_threads=3`
under that reading actually measured **slower** (40.3 tok/s) than simply
leaving `n_threads=4` and letting the kernel's own scheduler arbitrate
(42.7 tok/s) — the heuristic cost throughput without a measurable benefit.
**Do not use this approach.**

**What actually works — a real `cpu.max`-style cap, kernel-enforced, not
inferred from sampling.** Tested with `systemd-run --user --scope -p
CPUQuota=N%` (no root needed — user cgroup delegation), which gives real
kernel accounting instead of a guess from noisy percentages:

| CPU quota (of 4 cores) | `n_threads` left at 4 (unmatched) | `n_threads` matched to quota | 
|---|---|---|
| 100% | 50.2 tok/s | — |
| 80% | 37.4 tok/s (74% of full) | **44.4 tok/s (88% of full)**, `n_threads=3` |
| 60% | 19.6 tok/s (39% of full — badly nonlinear) | 37.7 tok/s (75%), `n_threads=2` |
| 50% | 14.6 tok/s (29% of full — badly nonlinear) | 37.7 tok/s (75%), `n_threads=2` |

**The nonlinearity at low quotas has a clear, verified cause.** Requesting 4
threads into a budget that only allows, say, 2.4 core-equivalents of real
runtime per period forces the kernel to constantly preempt and context-switch
4 competing threads within a shrunk time slice — pure scheduling overhead, no
extra work accomplished. **`n_threads` must be derived from the same quota
value, not left at the physical core count:**

```python
QUOTA_PCT = 80                                                  # reserve 20% for the browser
n_threads = max(1, round(physical_cores * QUOTA_PCT / 100))     # 4 * 0.8 = 3.2 → 3
# apply BOTH: a real cgroup cpu.max quota on the process, AND set n_threads to match.
# setting one without the other is the nonlinear-loss regime measured above.
```

**Practical maximum for this design, stated plainly: ~44 tokens/sec** —
80% CPU quota (100% is unrealistic; it starves the browser hosting the engine
and any other application on the machine), `n_threads=3` matched to that
quota. Not the 50 tok/s idle-machine ceiling, and not the 37 tok/s figure from
capping without matching threads — the number that reflects a machine that
also has to run the browser around the inference.

**CRITICAL CORRECTION — this number, and every throughput figure in this
document up to this point, was measured at a SHORT context (~64 tokens) and
does NOT hold as a conversation grows. Measured directly:**

```
context=64:     48.3 tok/s
context=500:    40.2 tok/s   (17% slower)
context=1500:   25.1 tok/s   (48% slower)
context=3000:   12.8 tok/s   (74% slower)
context=6000:    5.2 tok/s   (89% slower — under 1/9th the short-context speed)
```

**Mechanism, and why it's structural, not incidental:** every decode step
attends to every prior token, reading that token's K/V vectors from the KV
cache. That read is proportional to context length, on top of the fixed
weight read that was the sole cost at short context:

```
bytes read per token  ≈  weights (0.61GB, fixed)  +  KV-cache-read (∝ context length)
```

At context=64 the KV term is negligible against 0.61GB — which is why every
earlier measurement in this document (the ~44 tok/s ceiling, the `CPUQuota`
table above, the 1.65× batching figure) looks the way it does. At realistic
Phase 1 conversation lengths (§11's revised `n_ctx`/`n_seq_max` sizing targets
1000s of tokens per session), this term dominates. **None of the earlier
numbers are wrong at the context they were measured; they are being corrected
here for scope — they must not be read as constants that hold as a
conversation grows.**

**Two concrete design fixes this requires, not yet in the mechanisms above:**

1. **`max_output_per_request` (§8) must become context-position-aware, not a
   flat constant.** A fixed `512` bounds output *count*, not *time or CPU
   spent* — and the gap between those two grows enormously with position. At
   context=64, 512 tokens costs ~11s; at context=6000, the *same* 512-token
   cap costs ~98s. The cap needs to shrink as `s.pos` grows (a lookup table
   or a curve derived from measurements like the one above), or it fails to
   bound what it was introduced to bound.
2. **§9's 50/50 dispatch split implicitly assumed every token costs the same
   — it does not, by up to 9×.** Splitting raw *token counts* between
   foreground and background lets one deep-context background session consume
   the majority of actual wall-clock/CPU time while still technically getting
   only "its share" of the count. The naive scheduler should divide **time**
   (or a cost-weighted token budget), not raw token count, or a background
   session with a long conversation can silently dominate real usage under a
   policy that looks fair on paper.

**This mechanism is a second, coarser layer of the same governance idea
already central to this design** — a `cpu.max`-style cap between "the browser"
and "the inference engine" as whole processes, sitting above the §9 50/50 rule
that divides whatever capacity the engine has *between foreground and
background sessions inside it*. Two layers, same principle, different
granularity — worth stating together when this gets written up rather than
presenting the engine-level cap as a separate, unrelated mechanism.

**Phase 1 default: STATIC, computed once at startup — not reactive.**

```python
n_threads = round(physical_cores * 0.8)      # one multiplication, done once, at startup
# apply a matching cgroup cpu.max quota once, at startup, same percentage — never touched again
```

Costs nothing at runtime — no sampling loop, no ongoing decision, no risk of
the misfire measured above with `psutil`. Given a bad reactive heuristic was
just shown to make things *worse* than doing nothing, a fixed conservative cap
is the more defensible choice here, not merely the cheaper one: it guarantees
the ~44 tok/s ceiling under all conditions, never degrades further under load
(the kernel enforces the quota directly), and never over-claims on an idle
machine either — it simply doesn't try to detect that case.

**A periodic (not per-token, not per-dispatch-round) re-check IS a real future
improvement — but needs a validated signal, and `psutil` isn't it.** Tested
`/proc/pressure/cpu` (Linux PSI — kernel-native stall tracking) against the
exact failure case that broke the `psutil` approach:

```
same 85%-duty-cycle load that scattered psutil's readings:
    some avg10=0.00      ← correctly quiet; that load genuinely wasn't saturating

genuine oversubscription (6 pure-spin processes, 4 physical cores):
    some avg10=0.18 → 0.59   ← correctly rises; real stall detected
```

PSI measures time spent *waiting* for CPU, not just reported busy-ness — immune
to the hyperthread-scattering problem that made per-core percentages
unreliable. This is a genuinely different, better signal, validated against
the specific failure case that sank the earlier attempt.

**Still not the Phase 1 default — one validated test case is a lower bar than
everything else in this document has been held to,** and shipping an
undertested "reasonable-sounding" CPU mechanism is exactly what went wrong
last time. Recorded as a strong Phase 2 candidate: check `avg10` every 30–60s
(already a 10s decaying average — checking faster is pointless), and only
**raise** `n_threads` above the static 80% floor when pressure stays near
zero for a sustained period — never lower it reactively below the floor,
since the static floor already guarantees the safe minimum on its own.

---

## 11a. The full calibration script — thorough spec, and why CPU/memory couple

**The coupling, confirmed and load-bearing for this whole section.** Every
decode step attends to every prior token — reading that token's K/V vectors
is what costs both the CPU time (bandwidth to fetch it) and the memory (space
to hold it). Memory cost and CPU cost per token are not two independent
things that happen to correlate; they are two different costs of the *same*
growing quantity, position:

```
memory cost  = position × ~107KB/token     (measured, linear)
CPU cost     = position → tok/s curve      (measured, non-linear — steeper at depth)
```

**One calibration curve — tok/s as a function of position — therefore
captures both.** No need for separate memory and CPU calibration passes; the
same probe informs both the `n_ctx` sizing (§11) and the position-aware
`max_output_per_request` table (§8/§11's context-growth correction).

**The full script, five phases:**

**Phase A — hardware and thread sizing (~2s).** Sweep `n_threads` over
`(1, 2, 4, physical_cores)`, pick the measured optimum. Already validated:
`~10s` total for a lean version of this plus Phase B below, `0` decode errors.

**Phase B — position-cost curve, extended from a 2-point probe to enough
points to interpolate (5–6, spanning the intended `n_ctx_seq` range).** Feeds
the position-aware output cap directly from measured data on this machine,
rather than hand-picked breakpoints.

**Rigor, corrected after real testing — a closed-form cost model was tried
and doesn't fit; measurement noise was quantified, not assumed away.**
Attempted to fit the simple "fixed weight-read + linear KV-read" bandwidth
model (which would make `1/tps` linear in position) against real multi-trial
data. It doesn't hold:

```
slope of 1/tps between consecutive measured points:
   64→500:     ≈2.03e-5
   500→1500:   ≈1.14e-5   ← drops, not monotonic
   1500→3000:  ≈2.70e-5
   3000→6000:  ≈3.86e-5
```

**Conclusion: stick with piecewise interpolation between real measured
points — do not replace this with a fitted formula.** The simpler
theoretical model was worth trying and doesn't survive contact with data.

**Real, previously-unquantified trial-to-trial noise, even after taking a
median of 3 runs per point:**

```
depth=64:   41.2, 48.9, 49.2 tok/s   (spread ≈19%)
depth=1500: 24.4, 24.6, 25.0 tok/s   (spread ≈2%)
depth=6000:  4.8,  5.1,  5.3 tok/s   (spread ≈10%)
```

Every earlier single-shot measurement in this document carried this much
uncertainty, unquantified until this check. **Fix — different statistics for
different purposes, not one number reused for both:**
- **This curve** (feeds the output-cap formula) → use the **median** of
  ≥3 trials per point. Representative, not overly conservative.
- **Phase C/D's safety gate** (§11a below) → use the **worst observed
  trial**, not the median. The gate exists specifically to catch danger —
  gating on a middling number defeats its purpose given swings this size are
  real and measured, not hypothetical.

**Phase C — aggregate worst-case validation. NEW — this was the actual gap,
not previously in the calibration design.** Phases A/B only validate
*single-session* cost. They do not confirm that `n_ctx_seq`, `n_seq_max`, and
the `cpu.max` quota percentage are *jointly* safe at the worst realistic
case: every slot full, every session at its budget ceiling, batched together,
under the same quota that will run at runtime. Each number can look
individually reasonable and still fail together.

**Not the same experiment as §12 test 52, worth stating so neither is mistaken for covering the
other.** Phase C answers *"can this configuration survive worst-case full occupancy"* — every
slot full, uniform depth, one aggregate throughput number. Test 52 answers a different question —
*"does §9a's round-latency-budget rule actually enforce the ~50ms target under **heterogeneous**
depth"*, specifically one shallow foreground session sharing rounds with a much deeper background
one. Phase C's existing uniform-depth run does not validate test 52's claim; both are needed, and
an implementer should not read a passing Phase C as evidence §9a's mixed-depth behavior is sound.

**CORRECTION — this must be a REAL measurement, not an extrapolation from
Phase B. Verified directly, and extrapolation was proven unreliable, not
just theoretically risky:**

```
measured:  4 sessions, depth=1500, batched, n_threads=3 (matched to 80% quota):
           12.5 tok/s aggregate, 3.1 tok/s per session, 0 decode errors

naive extrapolation from Phase A/B alone would have predicted:
           single-session cost at depth 1500 (~25 tok/s) × short-context
           batching multiplier (1.65x) ≈ 41 tok/s — off by MORE THAN 3x,
           in the OPTIMISTIC direction
```

**Why extrapolation fails, and it's structural, not a fluke:** batching's
benefit comes from reading the model's weights once to serve several
sessions — a fixed cost, shared across the batch. Each session's own K/V
cache read is proportional to *that session's own depth* and is **not
shared** — every session has independent history. At short context (where
the 1.65x figure was measured) the shared weight-read term dominates; at
depth 1500 the unshared per-session K/V term dominates instead, and batching
barely helps with the part that's actually expensive. A calibration built on
extrapolation would have looked safe on paper and been more than 3x slower
than expected the moment several tabs held real conversations at once —
exactly the case Phase C exists to catch, and exactly what it would have
missed under the original "extrapolate, spot-check" instruction.

**Corrected implementation — run the real joint case:**

```python
def calibrate_phase_c(n_seq_max, target_depth, quota_threads):
    fill_sessions_to_depth(n_seq_max, target_depth)   # real prefill, every slot
    return measure_batched_decode(rounds=100, threads=quota_threads)
```

Cost is real (a couple of minutes for 4 sessions at depth 1500 in this
session's test) but runs once at setup. If setup time is a hard constraint,
run at a reduced depth/session-count and state that reduction explicitly as
a known conservative approximation — never silently extrapolate from
single-session data again; this session's own numbers are the proof that
doing so is unreliable, not merely unproven.

**Phase D — output and a real validation gate, not just data collection.**
Produces `n_threads`, quota %, `n_ctx`, `n_seq_max`, the cost curve — **and**
a pass/fail check: if Phase C's worst case falls below an acceptable service
floor, calibration recommends reducing `n_seq_max` or `n_ctx` before the
server starts, rather than silently accepting a configuration that will
throttle badly under normal multi-tab use.

**Phase D must also output the FINISHED `max_output_per_request` lookup
table, not raw tok/s numbers left for some other unspecified code to
interpret later.** Concrete derivation rule:

```python
TARGET_MAX_RESPONSE_SECONDS = 15   # no single response should take longer than this to generate

def derive_output_cap(position, cost_curve):
    tps_at_position = cost_curve.interpolate(position)
    cap = TARGET_MAX_RESPONSE_SECONDS * tps_at_position
    return clamp(cap, MIN_CAP, ABSOLUTE_CEILING)   # Phase G's outer bound, always applies
```

Calibration hands back the ready-to-use table; the engine never derives caps
from raw curve data at runtime.

**Phase D must also output `cost_ms_worst(pos)` — §9a's round-latency-budget composition needs it,
and it's already implicit in this same curve.** Not a new measurement:
`cost_ms_worst(pos) = 1000 / cost_curve.interpolate_worst(pos)`, inverted from the same
worst-observed-trial statistic Phase C/D's safety gate already uses (§11a's own rule: median for
representative curves, worst-observed for anything safety-relevant) — **not** the median-based
`interpolate(pos)` used for the output cap above. §9a's admission decision is a hard cutoff against
a latency bound, exactly the class of decision that rule already says needs the worst-observed
statistic, not the median: a round estimated from the median could, on a noisy trial (up to ~19%
spread measured at short context), actually cost more than the budget allows. Handed back as a
second ready-to-use function alongside the median-based output-cap table and `cost_ms(pos)` (kept
for any future use that genuinely wants the representative estimate) — the engine composes batches
every round and must not be re-deriving either from raw curve data on the hot path.

**Phase E — fallback and storage**, as already specified in §11's earlier
calibration subsection: conservative floor if calibration itself errors,
result stored as a stated, deliberate exception to §4's no-durable-storage
rule (hardware performance data, not user data).

**Phase E must write atomically — a real gap, found by tracing the actual
shutdown path.** Verified in source: `MalabrManager::StopMLServer()` calls
`Terminate(0, false)` — traced through `process_posix.cc`, this sends
`SIGTERM` (not an immediate `SIGKILL`), and `runtime.py` genuinely installs a
handler for it that cleans up gracefully (closes and removes the socket).
But `wait=false` means the browser does not confirm that handler finished
before continuing its own shutdown — if the OS force-kills remaining
processes during a fast full-logout before the handler completes, whatever
was mid-write is left however it was left. Calibration's file write (the only
thing this document persists to disk at all, per §4's exception) is exactly
the operation this could catch mid-write. **Fix: write to a temp file, then
`os.rename()` over the real path** — atomic on the same filesystem, so the
calibration file is always either fully the old version or fully the new one,
never a corrupt hybrid. The read path (Phase E's fallback) must already treat
"unparseable" the same as "missing" — falling back to fresh calibration — not
just "file absent."

**This residual shutdown race is exactly what §10's single-instance
PID-lockfile fix already exists to catch** — cross-referenced here rather
than treated as a second problem: an interrupted graceful shutdown can leave
a stale socket behind precisely because cleanup didn't finish, which is why
"check whether the old process is genuinely dead" (not just "does a socket
file exist") is the correct defense at startup, not an assumption that
shutdown always completes cleanly.

**Phase F — sanity clamps on calibration's OWN output, distinct from
Phase E's error fallback. A real gap, added here.** Phase E covers calibration
*erroring out* (crash, timeout, bad model path). It does not cover calibration
*succeeding* but returning an absurd value from an internal bug —
`n_threads=0`, a nonsensical `n_ctx`. That is a different failure class and
needs a different defense: **every calibration output is clamped to a sane
range before use, regardless of what was measured** —
`n_threads = clamp(measured, 1, physical_cores)`, `n_ctx` clamped to a sane
floor/ceiling. This converts "trust calibration got it right" into "trust
calibration to optimize within bounds that hold even when it's wrong," which
is the property actually needed — see §11c below for why this distinction
between safety-critical and performance-critical trust matters throughout
this whole section.

**Phase G — an absolute ceiling on `max_output_per_request`, independent of
the calibrated curve.** The position-aware cap (§8) is currently derived
entirely from Phase B's calibration data. If that data were ever corrupted or
nonsensical, nothing stops the derived cap from being equally nonsensical —
there is no outer number it structurally cannot exceed. **Fix: one flat,
conservative ceiling applies always, regardless of what the calibrated curve
says; the curve refines behavior inside that ceiling, never replaces it.**

---

## 11b. What happens when `cpu.max` is actually hit, and the compaction policy this drives

**Detection — confirmed possible, tested directly.** `cpu.stat` inside our
own cgroup (path derivable from `/proc/self/cgroup`) exposes `nr_periods`,
`nr_throttled`, `throttled_usec` — readable by the process itself, no special
privileges needed, verified from inside a real `systemd-run --user --scope`
cgroup. Cheap to poll periodically (once a second is plenty — this is not a
per-token check).

**The mechanism, already established in §7's correction:** no error, no
signal. The kernel takes every thread in the cgroup off the run queue until
the next accounting period opens; a `decode()` call in flight silently stalls
and resumes later. Measured consequence: median latency roughly unaffected,
tail (p95/p99/max) roughly doubles under the Phase 1 quota.

**The "which session caused this" question — a real asymmetry with memory,
resolved by the position/cost coupling above, not by new tracking.** Unlike
memory, where every session's footprint is exactly known (`s.pos × 107KB`,
no ambiguity), CPU time consumed has no natural per-session attribution
without explicit usage-over-time tracking — that's Phase 2's VTC-style
accounting, not built now. **But the coupling above means you don't need to
know who caused the overage historically — only who is most expensive going
forward, which is already known per-session, right now, from `s.pos` alone.**
The session with the largest current position is both the most expensive to
continue and the one compaction helps the most. That is a locally justified
policy using data already tracked, not a blind guess.

**Primary defense is prevention, not reaction.** Every session already has a
position-based budget and 95%-trigger compaction (§8). That already bounds
the worst-case *per-session* CPU cost — no session can exceed its budget's
depth, so no session's per-token cost can exceed that budget's worst case.
Phase C's aggregate calibration (§11a) is what confirms this bound actually
holds jointly against the real `cpu.max` quota. **If calibration is done
right, throttling under normal multi-tab use should be rare** — the reactive
mechanism below is a safety valve, not the primary defense.

**The reactive policy — one session at a time, always with a visible
message, never silent:**

```python
def check_throttle_and_compact():
    stat = read_cpu_stat()                       # once/sec poll, cheap
    if stat.nr_throttled > last_seen.nr_throttled:
        candidates = sorted(runnable_sessions(), key=lambda s: -s.pos)
        for s in candidates:
            compact(s)                            # existing primitive (§8) — not new
            notify(s, "conversation shortened due to system load")
            break                                  # one at a time; re-check before escalating
    last_seen = stat
```

**Must run INSIDE the single engine-thread loop, never on a separate timer
thread — a real gap this audit surfaced, not just an implementation detail.**
§7 names single-threadedness as a load-bearing invariant specifically because
it guarantees cancellation and batch composition can never interleave. If
`check_throttle_and_compact` runs on a *separate* wall-clock timer thread, it
reintroduces exactly that risk — calling `compact()` on a session the main
loop might simultaneously be selecting for a batch. Correct implementation
checks elapsed time from *within* the main loop's own iteration:

```python
def engine_loop(self):
    while running:
        if time.time() - self.last_throttle_check > 1.0:
            check_throttle_and_compact()
            self.last_throttle_check = time.time()
        s = scheduler.pick(registry.values())
        ...   # unchanged
```

One thread, one control flow, no new concurrency surface — the ~1s cadence
costs nothing measurable against a ~23-50ms dispatch loop.

Reuses the existing compaction primitive from §8 — no new mechanism, just a
new trigger condition and a selection policy for *which* session, informed by
data already tracked. The visible message is deliberate, consistent with this
document's earlier principle (§4's crash recovery: surfaced explicitly, never
silent) — a session losing context because of system-wide pressure should be
just as visible to the user as a session losing context because of its own
compaction threshold.

## 11c. How much trust calibration and compaction actually require — audited, not assumed

**Direct answer: performance depends on them being right. Safety does not —
and this split is deliberate, not incidental.** Traced through every
load-bearing mechanism in this design:

| Property | Depends on calibration/compaction? | What actually provides it |
|---|---|---|
| Cross-session isolation | **No** | `SlotAllocator` wipe-on-acquire (§7) — structurally independent |
| Browser not starved | **No** | `cpu.max` quota — a separately-chosen, conservative safety boundary that holds regardless of whether calibration got `n_threads` right |
| Session count bounded | **No** | `n_seq_max` admission reject (§7) — a fixed structural limit |
| No OOM at runtime | **No** | fixed-allocation property of `n_ctx` (§11) — self-enforcing once allocated, independent of whether the *size chosen* was optimal |
| Conversation length achievable | **Yes** | calibration's `n_ctx` sizing — a bad choice means shorter conversations, not danger |
| Generation speed | **Yes** | calibration's `n_threads` — a bad choice means slower generation, not danger |
| Compacted context stays coherent | **Yes, bounded to one session** | compaction's position math — a bug loses/garbles that session's own data; cannot leak into another session's data because `llama_memory_seq_rm` is scoped to one `seq_id` at the data-structure level, verified from this session's own isolation tests |

**Why isolation and browser-protection specifically cannot depend on
calibration:** this is the same principle that has shaped the whole design
since the founding cgroup-analogy discussion (§14) — structural/kernel-
enforced mechanisms carry the safety-critical properties; software logic
(calibration, compaction, scheduling) optimizes on top of them, never
underneath them. A calibration bug degrading throughput is an inconvenience.
A calibration bug that could theoretically enable a cross-session leak or
starve the browser would be a design failure — and by the table above, no
such path exists.

**What this does NOT mean — "contained to one session" is not "harmless."**
A compaction bug can still ruin a user's actual conversation (unexpected loss
of important context, incoherent output from a subtly wrong position range),
even though it cannot become a security bug. This is exactly why §12's
canary-recall test suite exists — it is the real check on compaction
*quality*, distinct from and in addition to the structural guarantee that
compaction bugs can't cross session boundaries.

---

## 12. Test suite — this is what proves Phase 1 is done

All reuse one mechanism: plant a canary early, act, check whether it leaked
or survived.

1. **Cross-session, both alive** — session A's canary must never reach session B
2. **Post-teardown, slot reuse** — end A, start C on the same slot, check for A's residue (the exact bug demonstrated this session — `pos_max` stayed nonzero until explicitly cleared)
3. **"New chat"** — same as #2, triggered by the user button (§3) instead of natural teardown
4. **Compaction fidelity** — plant a canary, force compaction, ask for it back (yes/no — never a quality score)
5. **Repeat #1–4 at batch_size 2 and 4** if/when batching is turned on — batching is a *stronger* isolation test, since two sessions' tokens sit in the same forward pass; a tagging bug shows up immediately rather than subtly
6. **Per-request output cap** — a session that never emits EOS is still cut off at `max_output_per_request`
7. **Admission at slot exhaustion** — `n_seq_max + 1`th concurrent tab gets a clean rejection, not a hang or a silent overwrite
8. **Tab-close cancellation bound** — close a tab mid-generation, confirm the session is torn down and the slot freed within ≤1 token time, not left running to completion
9. **User-triggered compaction** — same canary-recall check as automatic compaction (#4), triggered via the chat panel's "compact now" instead of the 95% threshold
10. **Visibility promotion rate change** — background session promoted to foreground; confirm its token output rate visibly increases within one dispatch round
11. **Crash-then-reconnect** — kill the server mid-session, confirm the tab surfaces "connection lost" rather than hanging silently or silently starting over
12. **Single-flight replace, no KV pollution** — start a long generation, send a second prompt before it finishes, confirm (a) the first is superseded not queued, (b) the model's context afterward is indistinguishable from one where the interrupted turn never happened (canary planted before the interrupted turn must still be recallable — proves rollback actually removed the partial rather than leaving it)
13. **Concurrent admission race** — fire session-creation for several distinct tabs at once, confirm no two receive the same slot and no `(ext_id, tab_id)` key ends up with two sessions
14. **Stale header visibility ignored** — send a request with a header `visibility` that contradicts a value already pushed via `visibility_changed`; confirm the push value wins, not the header
15. **Oversized input rejected cleanly** — a prompt larger than `MAX_INPUT_TOKENS` is refused before prefill, not silently truncated or left to compaction to fail on
16. **Extension-wide teardown** — disable/uninstall an extension with sessions open across several tabs; confirm all of them are torn down in one sweep, not left holding slots until each tab happens to close
17. **`visibility_changed` for a nonexistent session is a no-op** — fire the push before any `generate()` call for that tab; confirm it's silently discarded, no crash, no malformed registry entry
18. **Cancel-during-prefill rollback** — send a large prompt, replace it with a new one before prefill finishes (still `PENDING`, never reached `GENERATING`); confirm context reverts to `pos_before_request`, not a stale/wrong snapshot
19. **Slow-reader disconnect** — deliberately stall a connection's reads mid-generation; confirm the session is torn down via the same path as tab-close once the `outbox` bound is hit, rather than the engine blocking or tokens silently vanishing
20. **Context-position-aware output cap** — confirm `max_output_per_request` actually shrinks as `s.pos` grows (not a flat 512 regardless of position); a deep-context session must not be allowed the same worst-case generation time as a fresh one
21. **Time-weighted, not count-weighted, foreground/background split** — with one foreground session at low context and one background session at high context, confirm the background session's *token count* share does not translate into a disproportionate *wall-clock/CPU time* share (the failure mode §11's context-cost finding predicts if §9's split stays count-based)
22. **Calibration fallback on failure** — force the calibration routine to error (bad model path, simulated crash); confirm the engine starts anyway with a conservative hardcoded floor, not a hang or an unhandled exception at startup
23. **Worst-case preemption latency under real quota, not just unthrottled** — measure p99/max single-token latency running under the actual Phase 1 `cpu.max` quota (not the idle-machine number); confirm it stays within the corrected ~50ms bound, not the originally-claimed ~23ms, so the design's own responsiveness claims are validated against the conditions Phase 1 actually runs under
24. **Calibration's aggregate worst-case gate actually gates** — construct a config where Phase C's extrapolated worst case falls below the service floor; confirm calibration recommends reducing `n_seq_max`/`n_ctx` rather than silently accepting an unsafe joint configuration
25. **Throttle-triggered compaction targets the largest session, not an arbitrary one** — saturate `cpu.max` with several sessions at different context depths; confirm the session with the largest `s.pos` is compacted first, and that the affected tab receives a visible "shortened due to system load" message rather than a silent context loss
26. **Calibration output clamping** — force calibration to internally produce an absurd value (`n_threads=0`, a nonsensical `n_ctx`) via fault injection; confirm the clamp catches it before use, distinct from test 22's error-fallback path (this is calibration succeeding with a bad number, not calibration failing outright)
27. **Absolute output ceiling holds independent of a corrupted cost curve** — feed the position-aware cap a deliberately corrupted calibration curve; confirm `max_output_per_request` never exceeds the flat outer ceiling regardless of what the curve says
28. **Compaction bug containment** — deliberately inject a wrong position range into a compaction call for one session; confirm no other session's canary is affected, verifying the structural (not just tested) claim that `llama_memory_seq_rm`'s `seq_id` scoping prevents cross-session impact even under a real bug, not only under correct usage
29. **Single-instance enforcement** — start a second server instance while the first is still alive; confirm it refuses to start (or verifies the lockfile PID is actually dead) rather than deleting the live instance's socket and orphaning its sessions
30. **Throttle-check runs in-thread, not on a separate timer** — code review / instrumentation check that `check_throttle_and_compact` is called from within `engine_loop`'s own iteration, never from a spawned timer thread — this is a structural property to verify by inspection, since a race introduced here would be intermittent and easy to miss in normal testing
31. **Output cap table is calibration's finished product, not derived at runtime** — confirm the engine reads `max_output_per_request` directly from calibration's Phase D output and never recomputes it from raw curve data during a request
32. **Incremental chat-template fidelity** — compare logits after (a) applying the full chat template to a multi-turn history and prefilling it fresh, vs. (b) applying the template turn-by-turn incrementally on top of resident KV cache as this design does; confirm they match (same class of check already applied to compaction in §8) — this is currently UNVERIFIED and load-bearing for every conversation past the first turn
33. **UTF-8 streaming boundary** — deliberately generate text containing multi-byte characters (non-English text, emoji) until a byte-fallback token (confirmed to exist, ~1.4% of vocabulary) lands mid-character; confirm the streamed output never sends an incomplete UTF-8 sequence to the client, i.e. the buffering fix in §6d is actually applied, not merely specified
34. **Incoming payload size bound** — send a header claiming an oversized `payload_size`; confirm the server rejects before attempting to receive/buffer it, mirroring test coverage already required for the response-direction bound
35. **Calibration file survives an interrupted write** — kill the server mid-write to the calibration file (simulating the traced SIGTERM/no-wait shutdown race); confirm the next startup finds either the complete old file or the complete new one, never a corrupt partial write, and falls back to fresh calibration if it somehow finds neither
36. **iframe non-injection** — load a page containing several iframes from different origins; confirm the content script runs only in the main frame, not once per iframe
37. **Retry jitter under simulated cold-start burst** — fire several `generate()` calls from different tabs within the first few seconds of server startup; confirm retries don't visibly synchronize into a single burst at the moment the server becomes ready
38. **Input cap gate 2 fires if gate 1 is bypassed** — deliberately skip gate 1 in a test build; confirm gate 2's assertion catches the oversized prefill before `decode()`, not silently
39. **Tab-close storage cleanup, and navigation does NOT clear it** — two halves, because the earlier `pagehide` approach passed the first and silently failed the second (§2): (a) close a tab naturally (not via "new chat"); confirm `chrome.tabs.onRemoved` removed its `chrome.storage.session` entry rather than leaving it orphaned until browser close; (b) navigate the same tab between pages of ONE origin and confirm the entry SURVIVES and the history still restores — a listener that fires on navigation would destroy the persistence §2 exists to provide, and only the second half catches that
40. **Compaction never produces a half-formed turn** — force compaction on a multi-turn session; confirm every remaining turn boundary is clean (`<|im_end|>`-terminated), and re-run the logit-divergence fidelity check (test 4) specifically against the turn-aligned version, not the original arbitrary-range one
41. **Phase C uses a real joint measurement, not extrapolation** — confirm the calibration script actually fills sessions to depth and measures batched decode under quota, and does not fall back to extrapolating from Phase B's single-session curve, which this session measured to be off by more than 3x at depth 1500
42. **Two visible tabs in different windows — only the active window's tab is foreground** — open two Chrome windows on separate displays, both unoccluded; confirm only the tab in the window `BrowserList::GetLastActive()` reports is scheduled foreground, per §5a's resolved `visible AND active-window` rule, not visibility alone
43. **Clicking the address bar doesn't flip the intended tab out of foreground** — with one window active and its tab foreground, click that same window's address bar; confirm the tab remains foreground (validates window-level `BrowserListObserver` was the right granularity, not content-view `HasFocus()`, which this test would fail)
44. **EOS assumption re-verified on any future model swap** — before relying on `tok == EOS` as the sole stop condition for a different model, confirm that model also ties EOS to its template's turn marker; if not, add explicit stop-string matching
45. **Canary tests run at `temperature=0`, chat path does not** — confirm the test harness explicitly sets greedy sampling and the user-facing engine path does not silently inherit it
46. **Calibration gate uses worst-observed, not median** — confirm Phase D's pass/fail check is computed from the worst of ≥3 trials, not the median used for the output-cap curve; construct a config that passes on median but fails on worst-observed and confirm the gate catches it
47. **Incremental vs. batch eviction — canary-recall comparison run, not assumed** — before picking one as default, run both against the same growing conversation and compare canary recall quality, per the open question this raised
48. **Cheap-stub compaction fidelity** — apply the truncation-stub option to a dropped turn; canary-recall test whether the model can still answer about that turn's content meaningfully better than full deletion; drop the option if it can't
49. **Focus-switch latency, end to end** — physically switch OS focus between two windows with active sessions in each; measure real elapsed time from the switch to the scheduler's next dispatch reflecting the new foreground assignment; confirm it's bounded by one token boundary (~23–50ms), not lagging behind a batched or delayed update path
50. **Idle-session tab-close reaches the server at all** — close a tab with no generation in flight; confirm `tab_closed` arrives via the control connection and the session is torn down, since this case previously had no delivery path (§5e/§6a Case 2)
51. **Control connection survives and is reused, not reopened per push** — send several `visibility_changed` events in succession; confirm they travel over the same persistent connection, not a new socket per event
52. **Round-latency-budget composition holds under mixed depth (§9a)** — one foreground session at low position and one background session at high position, both `GENERATING` concurrently; confirm (a) the foreground session is included in every round regardless of its own or the background session's cost, (b) measured round latency stays within `ROUND_LATENCY_BUDGET_MS`, not just that token counts look proportional over a window, and (c) the background session is excluded from rounds it doesn't fit in rather than dragging foreground's round cost up — this is the case §9a's fix targets and the old slot-split version would fail
53. **Rollback snapshot survives an interleaved compaction (§6b/§8)** — plant a canary, start a long generation on a session close enough to its 95% threshold that a build_batch()-triggered compaction fires mid-response (while `pos_before_generation` is live), then send a replacement prompt to trigger `roll_back_partial()`; confirm the rollback lands on the correct, shifted position — not the stale pre-compaction one — and the canary planted before the interrupted turn is still recallable afterward
54. **Untouched turn boundaries stay correct across multiple compaction passes** — force two compaction events on the same session in sequence (e.g. two separate 95%-trigger fires as the conversation keeps growing); confirm every turn boundary `compact()` did *not* remove in the first pass — both the remaining droppable turns and the retained tail — still points at the correct content in the second pass, i.e. the shift step was applied to every live position reference, not just the turn actually being dropped that round
55. **Background aging bound holds, and its stated limit is what actually happens (§9a)** — several background sessions of different calibrated costs, foreground absent or light; confirm no background session is excluded for more than `MAX_CONSECUTIVE_EXCLUSIONS` consecutive rounds. Then repeat with foreground present and saturating most of the round budget every round; confirm the aging pass still prioritizes correctly (longest-waiting first) but does *not* falsely claim a bound it can't deliver — the aged session may still wait longer here, which is the documented, accepted limit, not a regression to catch as a bug
56. **Five tabs, five requests, only one foreground (§5f)** — fire a `generate()` from five tabs in sequence, switching tabs between each (the concrete scenario that exposed this). Confirm that at every instant at most ONE session is treated as foreground, and specifically that the four earlier sessions are background by the time the fifth is created — the failure this replaces would have left all five foreground, since each was genuinely visible when it sent
57. **Control-connection resync on reconnect (§5f)** — kill and restore the control connection while several sessions are live and a non-default tab is foreground; confirm `MalabrManager` pushes the current foreground unconditionally on reconnect (not only on the next change), and that `registry.foreground_tab_id` matches reality afterward without waiting for the user to switch tabs
58. **Degraded mode does not blow the latency budget (§5f)** — drop the control connection and hold it down past `STALE_VISIBILITY_TIMEOUT` while sessions generate; confirm `foreground_tab_id` becomes `None` (all sessions treated background, no session receives §9a's unconditional admission) and that measured round latency stays within `ROUND_LATENCY_BUDGET_MS` — the specific failure being prevented is a stale key naming a hidden tab as foreground and granting it guaranteed admission every round
59. **Slot recovered for a tab closed while the control connection was down (§5f)** — with several sessions live, kill the control connection, close a tab (so its `TAB_CLOSED` is written into the dead socket and lost), then restore the connection. Confirm the reconnect handshake's `LIVE_TABS` causes that session to be cancelled and its slot freed. Without this the slot leaks permanently, because the lost event can never be replayed — the browser has no record the tab existed once it is gone. Repeat with `n_seq_max` such tabs and confirm the pool is not exhausted
60. **Navigation mid-generation rolls back, and does not desync the model (§6b)** — start a long generation, then navigate the tab to a different page while tokens are still streaming. Confirm (a) the server stops generating promptly rather than draining the whole response, (b) a canary planted before the interrupted turn is still recallable, and (c) the model does NOT behave as though it gave the truncated answer — i.e. the unseen response was rolled back out of the KV cache, so the model's context matches the restored on-screen history. Distinguish from tab close: the session itself must SURVIVE the navigation, since it is keyed on tab id
61. **Cross-origin navigation does not leak conversation (§5g)** — hold a conversation containing a distinctive canary on origin A, then navigate the same tab to origin B and ask the model to recall it. Confirm the canary is NOT recoverable, that the slot held by the origin-A session was freed, and that `chrome.storage.session`'s displayed history for that tab was cleared too. Then verify the complement: a SAME-origin navigation (page to page within one site) preserves the conversation, since that is the behaviour §2/§4 intend
62. **Chunked prefill does not stall other sessions (§9b)** — with a foreground session mid-response, submit a `MAX_INPUT_TOKENS` prompt in a background tab. Confirm the foreground session keeps receiving tokens throughout the prefill (gaps stay within `ROUND_LATENCY_BUDGET_MS`), rather than pausing for its full duration. Then confirm the large prompt does eventually complete prefill and begin generating — chunking must not starve it
63. **Closed-loop correction holds the bound when the cost model is wrong (§9b)** — inject a deliberately optimistic cost curve (e.g. report half the true per-token cost). Confirm measured round latency converges back within budget as `correction` rises, instead of silently exceeding it; and confirm `correction` never drops below 1.0 even after a run of unusually fast rounds, since an optimistic correction would widen batches until the bound breaks

---

## 13. Explicitly out of Phase 1

| | Why | When |
|---|---|---|
| Chrome / VTC baseline policies | nothing to compare against yet | Phase 2 |
| Accumulated-usage-aware fairness (VTC-style) | naive 50/50 is group-level only, no history tracking | Phase 2 |
| Semantic summarisation (compaction) | quality-evaluation scope risk | possibly never |
| Disk hibernation of idle sessions | additive; add once basics pass | later |
| Per-session unequal memory shares (visibility-weighted) | current split is flat/equal | Phase 2, ties into real scheduling |
| KV quantization per session | `type_k`/`type_v` are context-wide in this build, not per-sequence — verified, not available at this granularity | not currently possible |
| Multiple concurrent model support | prove Phase 1 correct end-to-end on ONE model (`Qwen3-0.6B`) first — calibration (§11a), chat template (§6c), EOS assumption (§5b), everything else is designed against real, verified behavior of one model; swapping models is a config change once the design is proven, not a Phase 1 feature | Phase 2+, once Phase 1 is validated |

**Incognito — assumption stated explicitly, not silently relied on.**
Extension platform behavior (confirmed: `extension_util.h`'s
`CanBeIncognitoEnabled`) already gates this — extensions are off in
incognito windows by default, opt-in only. Nothing in this design needs to
handle incognito specially if the platform default holds; if a user
explicitly enables it, the same mechanisms apply unchanged (§4's
nothing-persisted-to-disk default already matches incognito's own privacy
expectation). Stated as an assumption rather than left implicit.

---

## 14. Known open items — carried from `open_design_questions.md`

- Session-switching cost between *different* resident sequences (serial
  alternation, one `decode()` call each): three runs gave +9.9%, +8.5%, 0.0% —
  noise, needs a clean re-measurement (quiet machine, pinned governor, many
  trials, median). Superseded as the Phase 1 default by real batching (§7/§9),
  which IS measured (1.65× at 4 sessions/batch) — but the serial-alternation
  number still matters wherever `BATCH_SIZE` ends up smaller than the runnable
  set, since that's a mix of batching and turn-taking.
- Real batched decode was verified only at one configuration: 4 sessions,
  `n_ctx=8192`, `n_seq_max=4`, `n_threads=n_threads_batch=4`. The relationship
  between `BATCH_SIZE`, `n_seq_max`, `n_ubatch`/`n_batch`, and throughput is
  otherwise unswept — don't assume the 1.65x figure holds at other slot counts
  or batch sizes without re-testing.
- Compaction drift (§8) — repeat on `Qwen3-0.6B` to separate model-specific
  (SWA) effects from general ones.
- Prefill-speed parallelism unmeasured — all throughput numbers this session
  are decode-only.
