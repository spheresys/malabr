# MALABR — session handoff v6 (2026-09-22)

Start here in a fresh session. Supersedes v5 for anything that conflicts.
v5 remains accurate about the two audits and the module split.

## State

Branch `chaitanya/v0`. Working tree has **uncommitted** work from this session
(below). §12 suite: **62 executed, 62 passed, 20 skipped (browser)**, green in
both partitioned and `MALABR_TEST_SHARED_KV=1` modes. Two tests are new (66, 67).

## The headline results (new — these are the evidence the thesis lacked)

### RQ3 — visibility-aware scheduling, measured at last

`tests/baseline_bench.py`. Three arms, SAME model, machine, prompts and engine:

- **fifo** — a lock around one shared llama.cpp context (what most local-LLM
  wrappers do).
- **parallel** — continuous batching, equal shares, no visibility signal. This
  is llama.cpp `--parallel` and VTC-style fairness and every serving engine:
  none of them can see a tab. Implemented as our own engine with the foreground
  disabled, so the ONLY variable versus the next row is the visibility signal.
- **malabr** — the same engine with that signal switched on.

8 tabs, foreground arrives last (the realistic case):

| arm | fg TTFT | median TTFT | p95 TTFT |
|---|---|---|---|
| fifo | 12.10 s | 7.01 s | 12.10 s |
| parallel (llama.cpp `--parallel`) | 4.19 s | 3.51 s | 4.19 s |
| **malabr** | **0.32 s** | **3.15 s** | **3.63 s** |

**37.6x better than FIFO, and 13.0x better than equal-share continuous
batching.** The second number is the one that matters: equal-share batching
already fixes most of FIFO's problem, so beating FIFO proves little. Beating
`--parallel` by 13x with everything else held constant isolates the visibility
signal as the cause, which is precisely RQ3's claim.

Control (foreground arrives FIRST): fifo 0.08 s, parallel 0.32 s, malabr 0.41 s.
MALABR loses, by the cost of its own machinery, and that is reported on purpose
— FIFO serves a first-arriving request immediately for reasons unrelated to
scheduling.

Makespan lands within ~1.2x across all arms and no claim rests on it: batching
the same model on the same cores does not create throughput. What changes is
who waits.

### RQ4 — what cancel-on-tab-close actually recovers

`tests/cancellation_bench.py`. 8 tabs generating, 4 closed 1.5 s in. The
measurement counts tokens that entered `llama_decode` after the close, not
frames delivered — any system can stop *showing* tokens to a closed tab; that
costs nothing and saves nothing.

| arm | decode spent on closed tabs | watched tabs finish |
|---|---|---|
| delivery-only (no lifecycle signal) | 453 tokens | 22.19 s |
| **reclaim (MALABR)** | **0 tokens** | **11.87 s** |

100% of the wasted decode recovered, and the tabs the user is actually looking
at finish **1.87x sooner**.

### RQ2 — §8 output fidelity, run for the first time

`tests/fidelity_check.py`. Two identical conversations; one compacted through
the shipping `Engine.compact`; next-token distributions compared at four probes.

| | context dropped | top-1 agreement | mean KL |
|---|---|---|---|
| compacted | **58-64%** | 4/4 | 0.0017-0.0032 nats |
| noise floor | — | 4/4 | 0.0000-0.0002 nats |

Ranges are across runs, not error bars: how much a conversation has grown when
compaction fires varies, so the fraction dropped varies with it. The conclusion
is stable across runs — top-1 agreement never broke, and the divergence stayed
within an order of magnitude of a noise floor that is itself ~0.

The noise floor is measured between two *uncompacted* sessions, because batched
decode is not bit-exact across sequences. Read the compacted row against it, not
against zero. Dropping well over half the conversation cost ~8x the noise floor
and left the chosen token unchanged everywhere.

## Chrome's built-in LLM as a baseline — CLOSED: not obtainable here

This was chased to the end. The short version: **there is no combination of
binary and model available on this machine that runs Chrome's Prompt API**, and
the reason is now understood exactly rather than guessed. Do not spend another
session on it without new information.

What was verified, in order:

1. **The perf-class gate is not the blocker.** v5 said it was. It is a
   field-trial param, `compatible_on_device_performance_classes`, and `"*"`
   opens it (`optimization_guide_features.cc:700`). chrome://flags calls this
   BypassPerfRequirement.
2. **Our own Chromium build can never run it.**
   `build_with_internal_optimization_guide` defaults to `is_chrome_branded`
   (`features.gni:38`), so a public checkout compiles the STUB
   `services/on_device_model/on_device_model.cc` — `GetEstimatedPerformanceClass()`
   hardcoded to `kFailedToLoadLibrary`, and "inference" implemented as an echo
   of the prompt. Our profile duly recorded `performance_class: 9`.
   The real implementation lives in `liboptimization_guide_internal.so`, which
   is present in `/opt/google/chrome/` and **absent from Chrome for Testing at
   every version** (checked directly). No flag substitutes for it.
3. **Google's server will not serve the model to Chrome 142.** Querying
   `update.googleapis.com` directly for component
   `fklghjjljmnfjoepjmlobpekiapffcja`, varying only the declared version:

   | declared Chrome | updatecheck status |
   |---|---|
   | 127 | noupdate |
   | **128 / 131 / 136** | **ok** |
   | 142 / 145 / 999 | noupdate |

   Not region, not hardware, not language, not disk — those were all varied and
   changed nothing. The component is retired for 142+, which now obtains its
   model some other way. chrome://components shows it registered at
   `0.0.0.0` with status "Update error", and the netlog confirms HTTP 200 with
   a `noupdate` body.
4. **The weights were obtained anyway**, by asking as Chrome 136: v2Nano
   `2024.9.25.2033`, 2,555,207,866 bytes, SHA-256 verified against the
   manifest. Kept at `/media/chaitu/chaitanya/nano-model/` (`model/weights.bin`,
   3.17 GB).
5. **Chrome 142 loads them.** With
   `--optimization-guide-ondevice-model-execution-override=<dir>`,
   chrome://on-device-internals reports **"Foundational Model: Ready
   (Overridden)"**, 3,020 MiB, VRAM 11,444 MiB against 3,000 required, device
   capable true.
6. **And still cannot create a session**, because the per-feature adaptation is
   missing: the internals page shows `PromptApi — Version 0`, and the
   `on_device_model_execution_config.pb` inside the 2024 package is zero bytes.
   Chrome refuses a session without a feature config
   (`kConfigNotAvailableForFeature`). This is a base-model/adaptation version
   mismatch: 2024 weights against a 2026 browser.
7. **Chrome 136 + the copied library does not work either.** Copying
   `liboptimization_guide_internal.so` from 142 into Chrome for Testing 136
   leaves `performance_class` unset and
   `model_validation_result: {component_version: "invalid"}` — an ABI mismatch
   six versions wide, which is the expected outcome.

**The bind, stated plainly:** the only binary here with the internal library
(Chrome 142) is refused the model, and the only versions the server will serve
(128–136) have no binary with the library. Both halves would have to change.

**This does not block the thesis, and arguably improves it.** A MALABR-vs-Nano
latency number would have confounded *scheduling* with *model choice* — a
different model, different size, different quantization. The comparison that
isolates the contribution is the one now in place: same model, same machine,
same engine, with only the scheduling policy varied. See the `parallel` arm
above. If a Nano comparison is ever wanted, it should be a POLICY comparison
measured within Chrome (foreground vs background latency as N tabs contend),
not a latency race against MALABR.

## KV-cache quantization — measured, and the answer is q8_0

`tests/kv_quant_check.py`, same method as the §8 fidelity check with the KV
cache type as the only variable:

| KV type | top-1 agreement | mean KL vs f16 |
|---|---|---|
| q8_0 | **4/4** | 0.0040 nats |
| q4_0 | 2/4 | 0.6278 nats |

Read against the ~0.0002 nat noise floor: **q8_0 costs about the same as one
compaction** (0.0017) and never changed the model's chosen token, in exchange
for roughly **2x the resident context per byte** — which is the scarce resource
all of §8 exists to ration. **q4_0 changes the answer half the time and must be
rejected.**

Not yet wired into `config.py`; the measurement is the decision, the change is
a two-line `cp.type_k`/`cp.type_v` edit plus re-running calibration (the bytes
per token it measures will change).

## New files

    scripts/nano_cdp.py              minimal stdlib CDP client (no new deps --
                                     the test venv has no websocket library)
    scripts/nano_provision_chrome.py drives real Chrome to download Gemini Nano
    scripts/nano_provision.py        same for our build; documents WHY it cannot
                                     work, so nobody retries it
    scripts/test_page_context.py     page-extraction tests in a real browser
    scripts/nano_pages/              fixtures (article.html has nav/sidebar/
                                     cookie banner/footer on purpose)
    mserver/tests/fidelity_check.py  §8 output fidelity
    mserver/tests/baseline_bench.py  MALABR vs FIFO
    mserver/NOTES.md                 the code rationale, now TRACKED
    design_doc/research_questions_v3.md   RQs reframed against Chrome's shipped
                                     built-in LLM

## Changes to existing code

- **Engine idle loop**: was `time.sleep(0.001)` on an empty round, i.e. ~1000
  wakeups/s on an idle server. Now blocks on a `threading.Event`; every
  cross-thread producer calls the new public `Engine.wake()`.
  `IDLE_WAIT_SECONDS` (25 ms) is a *backstop*, not the mechanism. Measured: 400
  idle rounds -> 16, with submit->round still 1.1 ms (§12 test 66).
- **Solo round budget**: `SOLO_ROUND_BUDGET_MS = 250`. The 50 ms round budget
  exists to stop one session delaying another; with one contender it buys
  nothing and costs prefill chunking. Not unbounded, because the budget also
  bounds how long the engine can ignore the control channel — a long round is a
  stop button that does nothing. Measured 5.3x larger solo prefill chunks
  (§12 test 67).
- **Panel repaint**: was a full `splitThink` + markdown re-parse + `innerHTML`
  rebuild *per token*, i.e. quadratic in reply length. Now coalesced into one
  `requestAnimationFrame`. A background tab gets no frames and so stops
  rendering entirely; `onComplete` flushes synchronously so nothing is lost.
- **Page context**: was `body.innerText.slice(0, 4000)`. Now two stages —
  Readability-style extraction (question-independent, so the once-per-session
  page cache still holds), then relevance selection against the question only if
  the article still overflows. Verified in a real browser: all boilerplate
  dropped, and a question about eviction now reaches a paragraph a prefix cut
  never would (`scripts/test_page_context.py`, 9/9).
  - **Bug found on the way, present in the original code too**: `innerText` on a
    detached `cloneNode` has no layout, so it silently degrades to `textContent`
    and keeps the source file's line wrapping. Text is now read from the live
    tree, block by block.
- **`mserver/NOTES.md` is tracked**; `design_doc/implementation_notes.md` keeps
  only the personal build log and points at it. Every `Rationale:` line in the
  source was updated. This closes v5's open decision: the pointers now resolve
  in a fresh checkout instead of only on this machine.

## Open work, roughly in priority order

1. **Wire q8_0 KV in `config.py`** and re-run calibration — the fidelity
   question is answered (above), only the plumbing is left.
2. ~20 §12 tests still need a real browser. `scripts/test_page_context.py` is
   the pattern: a stdlib CDP client driving headless Chrome against a fixture
   page. Several of the declared SKIPs are convertible this way.
3. Calibration refilling from zero per measured position.
4. Summarisation for page context, if extraction + relevance selection still
   is not enough. Encoding/"compression" tricks do **not** help — a BPE
   tokenizer punishes anything unlike natural text.
5. Chrome Prompt API: closed as not obtainable (above). Revisit only if a
   branded Chrome in the 128–136 range becomes available, or Google starts
   serving a component that 142+ accepts.

## Conventions

- No co-author trailers or tool attribution in commits, ever.
- Commit messages: one line, imperative, `MALABR:` or `docs:` prefix. No test
  counts, no long bodies.
- Explain config/settings changes in text before applying them.
- Test venv: `/home/chaitu/Desktop/vscode/malabr/bin/python` is the only
  interpreter with `llama_cpp`.
