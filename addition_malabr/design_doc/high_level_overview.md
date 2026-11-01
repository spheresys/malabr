# MALABR — High-Level Mental Model (start here)

This is the **10,000-ft picture only** — no file internals, no line numbers, no C++/Python
syntax. The goal is to hold the whole system in your head in one glance before going one
level down.

For file-by-file detail (with exact line numbers, every internal API call named, byte-level
wire protocol) see **[code_structure_map.md](code_structure_map.md)** — that is the next
level down, once this page feels obvious.

---

## 1. Two separate timelines — don't mix them

Everything in the system belongs to exactly one of these two timelines. Most confusion
comes from putting something from Timeline 1 into the Timeline 2 picture, or vice versa.

### Timeline 1 — "Turn the system on" (happens ONCE, when the browser opens/closes)

```
browser starts
     │
     ▼
malabr_manager.cc  ──spawns──▶  mserver/app.py   (as a background process)

     ...browser runs for hours, Timeline 2 happens many times...

browser closes
     │
     ▼
malabr_manager.cc  ──kills────▶  mserver/app.py process
```

`malabr_manager.cc` never sees a request. It doesn't know `fit()` or `predict()` exist. Its
only job: make sure the Python server process **exists and is running** before any request
can happen, and clean it up when the browser closes. Like unlocking/locking a kitchen —
never takes an order, never cooks.

### Timeline 2 — "One request" (happens EVERY time a user clicks something, many times per session)

```
popup.js  (user clicks "Run Demo")
   │  "hey, train this model" — chrome.malabr.fit(...)
   ▼
malabr_api.cc            "I got the request, let me relay it"
   │
   ▼
mserver_uds.cc /          "I'm just the courier — move bytes over a socket,
msocket_uds.cc             don't understand them"
   │
   ▼   (Unix Domain Socket, one connection per request)
mserver/  (Python)        "I actually do the work — train, predict, save to DB"
   │
   ▼   (answer flows back up the same arrows, in reverse)
popup.js shows the result on screen
```

---

## 2. The four roles, one word each

| Piece | Role | Direction |
|---|---|---|
| `popup.js` (JS, in the extension) | **Asks** — where a request is born | starts the chain |
| `api/malabr/` (`malabr_api.cc` + `mserver_uds.cc` + `msocket_uds.cc`) | **Relays** — receives the ask, carries it to Python, carries the answer back | middle |
| `mserver/` (Python) | **Does the work** — the only place any actual computation happens | end of the chain, does the job |
| `malabr_manager.cc` | **Switch** — starts/stops the whole `mserver/` process | not part of the chain at all — Timeline 1 only |

**Correction worth remembering:** `api/malabr/` does not live "inside" or get "used by"
`mserver/`. It's the reverse — `api/malabr/` is the **client**, it reaches out and calls
`mserver/`. `mserver/` never calls back into `api/malabr/`; it just listens and responds to
whoever connects. (Concrete tell: `msocket_uds.cc` calls `connect()` — that's what a client
does. `mserver/runtime.py` calls `bind()`+`listen()`+`accept()` — that's what a server does.)

---

## 3. Where N:M parallelism actually lives — four separate pools, not one

Not one mechanism in one file. Four independent worker pools, split across both processes:

```
BROWSER PROCESS (C++)                        PYTHON PROCESS

malabr_api.cc
  PostTask() → one NEW thread                one socket
  per request, each opens    ──────────────  per request  ──┐
  its own socket connection        (N)                       │
                                                    runtime.py
                                              ThreadPoolExecutor accepts
                                              each connection on its own
                                              thread                (M) ──┐
                                                                            │
                                                    supervisor.py
                                              inference_pool — separate
                                              pool just for predict/score ──┐
                                                                              │
                                                    supervisor.py + training.py
                                              mp.Process trainer_workers —
                                              separate pool just for fit()/training
```

- **N** = browser side. Literally the line `base::ThreadPool::PostTask(...)` inside
  `malabr_api.cc`'s `Run()` methods — every click gets its own thread and its own fresh
  socket connection.
- **M** = server side, connection acceptance. `runtime.py`'s `ThreadPoolExecutor`, one
  thread per accepted connection.
- **+2 more pools**, both inside `supervisor.py`/`training.py`, downstream of M: one
  `ThreadPoolExecutor` just for running predict/score, one separate `multiprocessing.Process`
  pool just for training jobs — so training can't block inference and vice versa.

---

## 4. Reading order from here

1. This page — hold the 4-role picture + 2-timeline split in your head.
2. [code_structure_map.md](code_structure_map.md) §4 — the same Timeline-2 request, now with
   every file, every line number, every internal API call named.
3. Pick one file at a time and use the grep-outward method (header first, then `.cc`, then
   `grep -rn "ClassName" extensions/browser/api/malabr/` to find callers) rather than reading
   top to bottom — that's what actually built this picture, one hop at a time.
