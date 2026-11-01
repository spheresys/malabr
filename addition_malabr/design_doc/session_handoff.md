# MALABR Session Handoff — paste the prompt below into a new session

This file is the durable record. The block in the fenced code section below is
a summary to re-establish context when resuming
seamlessly.

---

## The handoff prompt (paste this)

```
I'm an MTP student at IIT Kharagpur, working under Prof. Mainack Mondal
(secure-systems focus) on the MALABR project — pivoting an existing Chromium
browser-extension ML server (v3.0, originally built by a previous student,
Bivas Biswas) into new research: "MALABR: Browser-Managed Shared Inference
with Session Isolation and Lifecycle-Aware Scheduling." Full research plan is
in addition_malabr/design_doc/research_problem_statement.md — read it first.

READ THESE FILES FIRST, IN THIS ORDER, before anything else:
1. addition_malabr/design_doc/research_problem_statement.md — the actual thesis
   plan: 4 RQs, architecture, 6 implementation phases, related-work positioning,
   and §6.7 (a DoS/availability threat model I added for a supervisor review —
   keep this section's reasoning intact if editing nearby).
2. addition_malabr/design_doc/high_level_overview.md — my working mental model
   of the CURRENT (v3.0) codebase: two timelines (browser startup/shutdown vs.
   per-request), four roles (JS asks / C++ relays / Python does the work /
   MalabrManager is the on-off switch), and how N:M parallelism works.
3. addition_malabr/design_doc/code_structure_map.md — full file-by-file map of
   the v3.0 codebase, both C++ and Python halves.
4. addition_malabr/design_doc/open_design_questions.md — three concrete gaps
   found while reading the current code that don't map cleanly onto the pivot
   (streaming/connection-lifecycle, training-worker cancellation granularity,
   header/payload schema needing tab_id + a chat-shaped payload). Read before
   starting Phase 1 implementation.

WHERE I ACTUALLY AM:
- The ENTIRE C++ layer of the current (v3.0) codebase is fully understood by
  me, in real depth, verified against actual source (not just described):
  extensions/browser/api/malabr/{msocket_uds,mserver_uds,malabr_api}.{h,cc},
  chrome/browser/malabr_manager.{h,cc}, the whole .idl → generated-code →
  DECLARE_EXTENSION_FUNCTION → ExtensionFunctionRegistry dispatch chain
  (verified against extensions/browser/extension_function.h and
  extension_function_registry.h directly), and the feature-flag chain
  (chrome_features.*, flag_descriptions.*, about_flags.cc).
- Known real quirks in the current code (not my misunderstanding — verified):
  `ml_server_` member in all 4 malabr_api classes is declared but never
  assigned (dead code); several unused #includes throughout; mserver_uds.h's
  include guard still says READ_SERVER_UDS (copy-paste leftover from the
  legacy v2.x API); the constructor parameter is named `label` in the .h but
  `route` in the .cc definition, stored as `route_`.
- The Python side (addition_malabr/mserver/malabr_service/) is NOT yet deeply
  studied. supervisor.py and training.py are the priority — they become my
  session registry/scheduler and my llama-cpp-python integration point,
  respectively. protocol.py/runtime.py are lower-priority (mirror C++
  concepts I already know); config.py and storage.py are lowest (storage.py's
  SQLite schema likely won't survive the pivot to ephemeral, tab-scoped
  sessions).
- I already have a FULL, WORKING Chromium build: out/Default/chrome exists
  and runs (built successfully with `autoninja -j4 -C out/Default chrome`,
  logged with `screen`, took ~8.5 hours). I do NOT need to sync to a newer
  Chromium checkout — this pinned version has everything the pivot needs
  (WebContents::GetVisibility(), TabStripModelObserver are both long-stable
  APIs), and re-syncing would risk unrelated build breakage for zero benefit.
  Machine: 14GB RAM, 4GB swap, genuinely tight — clangd was disabled in
  .vscode/settings.json after repeated OOM crashes (clangd alone hit 4.5GB);
  prefer plain-text grep/search over IDE "Find References" for that reason.

HOW I LEARN — PLEASE FOLLOW THIS, IT TOOK A WHILE TO ESTABLISH:
- Explain things ONE PIECE AT A TIME. Do not dump a whole file's explanation
  in one giant response unless I explicitly ask for a full pass. Stop after
  each chunk and let me ask for the next one.
- When there's genuine uncertainty about how something works (an internal
  Chromium mechanism, a generated file's real content, git history), GO
  VERIFY IT WITH REAL TOOLS (grep, Read, git log) rather than describing it
  from general knowledge. This has consistently been more valuable to me
  than plausible-sounding explanations — I will often push back and ask "did
  you actually check that" if an answer feels asserted rather than verified.
- I'm new to JavaScript (treat it as light/read-only literacy, not something
  to go deep on) but by the end of this session had built solid C++
  fundamentals (references, RAII, namespaces, include mechanics, weak_ptr
  lifetime safety, ThreadPool/PostTask/BindOnce, socket syscalls down to
  errno) — you can build on that without re-deriving it, but I do sometimes
  need concepts re-anchored after a gap (I once came back after two weeks and
  needed a full recap — that recap is preserved in the chat history/this
  file's context, don't assume I remember details without checking).
- I like concrete grounding over abstraction: real fd numbers, real line
  numbers, real generated file contents, tracing exact call chains — not
  "trust me, this is how it works."
- When I ask a "why does X exist" or "what would go wrong without X" question,
  give the real concrete failure mode (a crash, a memory leak, an actual
  exploit class) rather than a generic "it's good practice" answer.

CURRENT UNFINISHED THREAD (pick this up if I ask to continue it, otherwise
treat it as closed background context):
I was investigating whether Chrome's built-in Prompt API (Gemini Nano) has
any actual, real scheduling/priority logic, to properly ground my thesis's
related-work claim that "the scheduling policy is unpublished and cannot be
studied." Findings so far:
- In THIS repo's local checkout (files dated ~2023, likely a somewhat old
  Chromium snapshot): grep for Queue/Priority/Schedule/foreground/visib in
  chrome/browser/ai/ai_manager_impl.{h,cc} and
  services/on_device_model/on_device_model_service.{h,cc} — ZERO matches, in
  both headers and implementations.
- Fetching the LIVE upstream main branch (chromium.googlesource.com) of
  on_device_model_service.cc found ONE new thing: a method
  `SetForceQueueingForTesting(bool force_queueing)` — name suggests a
  testing/determinism hook, not a published scheduling POLICY, but this is
  NOT YET CONFIRMED — I had not yet read its .cc implementation or found any
  queue_/pending_ member before the session ran low on context.
- The equivalent live fetch for chrome/browser/ai/ai_manager_impl.cc 404'd —
  the file path may have moved in current upstream vs. this local checkout;
  needs a fresh path lookup (try source.chromium.org's code search UI result,
  or list the current chrome/browser/ai/ directory contents via a fetch).
- NEXT STEP if resuming this: find and read the actual implementation behind
  SetForceQueueingForTesting to determine whether it's a real fairness/order
  mechanism or purely a test-determinism knob, then locate ai_manager_impl.cc's
  current path upstream and repeat the same check there. Do not overstate
  either direction in the thesis text until this is actually confirmed either
  way — right now it's a genuine open question, not evidence for or against.

Please read the four docs listed above now, confirm you've absorbed this
context, and then ask me what I want to work on next.
```

---

## Why each piece is in there (for my own reference, not to paste)

- The four-doc reading list front-loads everything durable so the new session
  doesn't need me to re-explain architecture from scratch.
- The "known quirks" list exists so a new session doesn't waste time
  re-discovering `ml_server_` dead code, etc., or worse, "fixing" it without
  asking (my use case diverges from the current code, so I explicitly said no
  changes needed there).
- The "how I learn" section is the single highest-value part of this
  handoff — it took this entire session to establish and is exactly the kind
  of thing that's invisible from just reading the codebase docs.
- The unfinished thread is preserved precisely so it isn't silently dropped
  or, worse, silently "resolved" with an unverified guess in a future session.
