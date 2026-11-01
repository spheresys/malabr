# MALABR Code Structure Map — Detailed Walkthrough (branch `chaitu-v3`)

This document explains the **entire current MALABR codebase** for someone who is new to
JavaScript, Chromium internals, or both. It covers:

- §1 The technologies used, in plain words (read this first if you are new)
- §2 The big picture — 5 layers
- §3 Layer-by-layer explanation, including the JavaScript side line-by-line
- §4 A single `fit()` call traced hop-by-hop with every internal API named
- §5 The full train→predict workflow (polling pattern)
- §6 Reference table of every internal Chromium/Python API we use and why
- §7 How the API is registered inside Chromium (the "8 metadata files")
- §8 Complete file map (both halves of the repo)
- §9 Runtime artifacts + "if I change X, what else must move" cheat sheet

> **Divergence from the thesis to keep in mind:** the thesis (Ch. 6) says the Chromium
> C++ integration is "future work". On this branch it is **already implemented** — the
> extension JS never opens the socket itself; the C++ browser process does the UDS I/O.
> Also the wire format in code is a CSV header (`"ROUTE,ext_id,size"`), not the
> `[msg_len][ext_id_len]` envelope drawn in thesis §6.4. Details in §4 step 8.

---

## 1. The technologies, in plain words

| Term | What it actually is |
|---|---|
| **Chrome Extension** | A small web app (HTML + CSS + JS files in a folder) that Chrome loads and gives special powers to, listed in its `manifest.json`. Ours asks for the `"malabr"` permission. |
| **manifest.json** | The extension's config file. Declares its name, which HTML is the popup, which JS is the background worker, and which permissions it wants. |
| **popup** | The little window that opens when you click the extension's icon in the toolbar. It is just a normal web page (`popup.html`) running its own JS (`popup.js`). |
| **JS module (`import`/`export`)** | Modern JavaScript's way of splitting code into files. `export default X` in one file + `import X from "./file.js"` in another = same idea as Python's `from file import X`. The `<script type="module">` tag in popup.html turns this on. |
| **callback** | A function you hand to an API, which the API calls later when the work is done. All classic `chrome.*` APIs work this way: `chrome.malabr.fit({payload}, (result) => {...})` — the arrow function runs when the browser has the answer. |
| **Promise / async / await** | A nicer wrapper over callbacks. `await someAsyncThing()` pauses that function (without freezing the page) until the result arrives. Our SDK wraps every chrome callback API in a Promise so `popup.js` can be written as straight-line code: `await model.fit(...)`, then `await model.predict(...)`. |
| **FlatBuffers** | A binary serialization format (like JSON, but compact bytes with a fixed schema). The schema is `ml.fbs`; a compiler (`flatc`) generates matching reader/writer code for Python (`mserver/ML/`) and JS (`malabr_js/ml_generated.js`). Both sides must be regenerated together when the schema changes. |
| **Unix Domain Socket (UDS)** | A socket like TCP, but it lives as a file on disk (`/tmp/malabr_v3.sck`) and only connects processes on the same machine. Very low latency, no network stack. This is the pipe between browser and Python server. |
| **Renderer process vs. Browser process** | Chromium runs each tab/extension page in a sandboxed *renderer* process that is not allowed to touch the OS. The one privileged *browser* process does anything OS-level (files, sockets, spawning). So the extension's JS **cannot** open our socket — it must ask the browser process via `chrome.malabr.*`, and the C++ code does the socket work. This is the core reason the architecture looks the way it does. |
| **ExtensionFunction** | Chromium's C++ base class for implementing a `chrome.something.method()` API. One subclass per method. Chromium routes the JS call to the right subclass's `Run()`. |
| **base::ThreadPool** | Chromium's worker-thread pool in the browser process. Blocking work (like our socket read) must run there, never on the UI thread. |
| **scikit-learn / joblib** | The Python ML library the server currently uses (LogisticRegression, SVC, RandomForest) and the tool that saves/loads trained models as `.joblib` files. |
| **SQLite** | A tiny database stored in one file (`malabr.db`). The server uses it to remember which clients and models exist across restarts. |
| **multiprocessing (mp.Process/Queue)** | Python's way to run work in separate OS processes. Training runs in worker processes so a heavy/broken training job cannot freeze or crash the main server. |

---

## 2. The big picture — 5 layers

```
 LAYER 1: Extension UI + JS SDK          (JavaScript, runs in renderer process)
   popup.html → popup.js → malabr_js/ SDK → builds FlatBuffer → chrome.malabr.fit()
        │
        │  Chromium internal IPC (automatic — we wrote no code for this hop)
        ▼
 LAYER 2: Extension API dispatch          (Chromium machinery, browser process)
   permission check → find MalabrFitFunction by name "malabr.fit" → call Run()
        │
        ▼
 LAYER 3: MALABR C++ handlers             (our C++, browser process)
   malabr_api.cc  →  ThreadPool worker  →  mserver_uds.cc framing  →  msocket_uds.cc POSIX socket
        │
        │  Unix Domain Socket  /tmp/malabr_v3.sck
        ▼
 LAYER 4: Python ML server                (our Python, separate process spawned by browser)
   runtime.py accept loop → protocol.py parse → supervisor.py route
      → training.py (fit, in mp.Process workers)  or  LRU cache + joblib (predict/score)
      → storage.py (SQLite)
        │
        ▼  response flows back up the exact same path
 LAYER 0 (build time, not runtime): code generators
   malabr.idl  --json_schema_compiler-->  gen/extensions/common/api/malabr.h   (C++)
   ml.fbs      --flatc-->                 mserver/ML/*.py  +  malabr_js/ml_generated.js
```

And one side-channel that makes it all start:

```
Browser startup:  chrome_browser_main.cc → MalabrManager::StartMLServerIfEnabled()
                  → base::LaunchProcess("python3 -u addition_malabr/mserver/app.py")
Browser shutdown: → MalabrManager::StopMLServer() → process.Terminate()
```

---

## 3. Layer-by-layer explanation

### 3.1 Layer 1 — The extension (JavaScript) — read this slowly if JS is new

Folder: [addition_malabr/mserver/malabr_test_extension/](../mserver/malabr_test_extension/)

**File-by-file, in the order things happen:**

1. **[manifest.json](../mserver/malabr_test_extension/manifest.json)** — Chrome reads this
   when you "Load unpacked" the extension. Key lines:
   - `"permissions": ["malabr"]` → without this, `chrome.malabr` would be `undefined`
     inside this extension (see §7 for where the permission itself is defined).
   - `"action": {"default_popup": "popup.html"}` → clicking the toolbar icon opens popup.html.
   - `"background": {"service_worker": "background.js"}` → background.js is currently just
     a one-line `console.log`; it does nothing yet.

2. **[popup.html](../mserver/malabr_test_extension/popup.html)** — the test UI: a model
   dropdown (SVC / Logistic / RandomForest), textareas pre-filled with train/test arrays,
   a "Run Demo" button, output and error panels. The last line —
   `<script type="module" src="popup.js"></script>` — loads the JS **as a module**, which
   is what allows `import` statements to work.

3. **[popup.js](../mserver/malabr_test_extension/popup.js)** — the UI controller. Top of file:

   ```js
   import LogisticRegression from "./malabr_js/logistic_regression.js";
   import RandomForestClassifier from "./malabr_js/random_forest_classifier.js";
   import SVC from "./malabr_js/svc.js";

   const MODEL_REGISTRY = { logistic: LogisticRegression, random_forest: ..., svc: SVC };
   ```
   Then it grabs the HTML elements by id (`document.getElementById("run-demo-btn")` etc.)
   and registers a **click handler**:
   `runButton.addEventListener("click", async () => { ... await runFromUi(); ... })`.

   `runFromUi()` is the whole demo in straight-line async code:
   ```js
   const model  = new ModelClass(modelConfig, { name: ... });   // e.g. new SVC({})
   const started = await model.fit(trainX, trainY);             // send FIT
   const status  = await waitForReady(model);                   // poll check_status every 500ms
   const yPred   = await model.predict(testX);                  // send PREDICT
   const score   = await model.score(testX, testY);             // send SCORE
   renderOutput(...)                                            // draw results into popup
   model.destroy();
   ```
   `waitForReady()` is the polling loop from thesis §6.11.4: up to 20 attempts,
   `await model.check_status()`, sleep 500 ms between attempts, throw on `"failed"`.

4. **[malabr_js/logistic_regression.js](../mserver/malabr_test_extension/malabr_js/logistic_regression.js)**
   (and `svc.js`, `random_forest_classifier.js`) — tiny subclasses, ~14 lines each:
   ```js
   class LogisticRegression extends BaseModel {
     constructor(params = {}, options = {}) {
       const defaults = { penalty: "l2", solver: "lbfgs", max_iter: "100" };
       super("LogisticRegression", { ...defaults, ...params }, options);
     }
   }
   ```
   `extends` = inheritance (like Python subclassing). `{ ...defaults, ...params }` merges
   two objects, caller's params winning. The string `"LogisticRegression"` must match what
   the Python server's `training.build_model()` expects — that is the real contract here.

5. **[malabr_js/base_model.js](../mserver/malabr_test_extension/malabr_js/base_model.js)** —
   **the SDK core (322 lines). Everything interesting on the JS side happens here.**

   - **Constructor**: stores `type`, generates `this.id` with `crypto.randomUUID()`
     (this becomes the server-side `model_id`), stores `params`.

   - **`_buildRequest({action, params, x, y})`** — serializes one `ML.Request` FlatBuffer:
     1. `new flatbuffers.Builder(1024)` — a growable byte buffer.
     2. Strings first: `builder.createString(this.type / this.id / this.name)`.
     3. Params → each key/value becomes a `Param` table holding a `StringVal`
        (everything is sent as a string; the Python side re-parses "100" → int, "true" → bool
        in `protocol.coerce_param_value`).
     4. Arrays → `_createTensor()`: JS number array → `Float32Array` → raw bytes →
        `ML.Tensor` with dtype FLOAT32 and shape `[length]`.
     5. Finally `ML.Request.startRequest(builder)` … `addAction/addType/addId/addParams/addX/addY`
        … `endRequest`, `builder.finish()`, and `builder.asUint8Array()` returns the bytes.
     (FlatBuffers rule: all child objects must be built *before* `startRequest` — that is
     why the code looks "inside-out".)

   - **The four API wrappers** — this is the *only* place the extension touches Chrome's API:
     ```js
     async malabrFitAsync(payload) {
       return new Promise((resolve, reject) => {
         chrome.malabr.fit({ payload }, (result) => {          // callback style →
           resolve(this.convertStringToFlatbuffers(result));   // → Promise style
         });
       });
     }
     ```
     Identical wrappers exist for `chrome.malabr.predict`, `.score`, `.checkStatus`.
     This pattern ("promisify a callback API") is standard JS; memorize its shape once
     and all four methods read the same.

   - **Response parsing**: the C++ side returns the server's response bytes as a string;
     `convertStringToFlatbuffers()` turns it back into bytes (`_toUint8Array`) and reads it
     with `ML.Response.getRootAsResponse()`. Then:
     - `fit()` → returns `resp.status() === 0` (true = "fit_started" accepted)
     - `check_status()` → `_parseStatusPayload(resp.message())` parses the JSON
       `{"status":"ready"}` / `{"status":"failed","error_msg":...}`
     - `predict()` → `_parsePredictPayload()` extracts `y_pred` array from JSON message
     - `score()` → `parseFloat(resp.message())`

   - **Lifecycle**: `destroy()` sets a flag; every method first calls
     `_assertNotDestroyed()` so a destroyed model throws immediately.

6. **[malabr_js/ml_generated.js](../mserver/malabr_test_extension/malabr_js/ml_generated.js)** —
   1247 lines, **generated by `flatc` from `ml.fbs` — never edit by hand.** Defines the
   `ML.*` namespace used above: `ML.Action` (FIT=0, PREDICT=1, SCORE=2, CHECK_STATUS=3),
   `ML.Request`, `ML.Response`, `ML.Tensor`, `ML.Param`, `ML.ParamValue`, `ML.DType`.
   The Python twin of this file is the `mserver/ML/` package.

7. **[malabr_js/flatbuffers.js](../mserver/malabr_test_extension/malabr_js/flatbuffers.js)** —
   the vendored FlatBuffers runtime library (Builder, ByteBuffer classes).

8. **[dmeo.js](../mserver/malabr_test_extension/dmeo.js)** — a scripted version of the same
   fit→poll→predict→score flow, without the UI.

### 3.2 Layer 2 — What Chromium does between JS and our C++ (we wrote none of this)

When `popup.js` calls `chrome.malabr.fit({payload}, callback)`:

1. **Bindings**: the renderer process has an auto-generated JS binding for the `malabr`
   namespace, created at build time from [malabr.idl](../../extensions/common/api/malabr.idl)
   (because the idl is listed in [schema.gni](../../extensions/common/api/schema.gni)).
   The binding validates the argument shape (`{payload: ArrayBuffer}`) against the schema.
2. **Feature/permission gate**: `_api_features.json` says the `malabr` namespace only exists
   in `privileged_extension` contexts whose manifest holds `permission:malabr`.
3. **IPC**: the renderer sends the call over Chromium's internal Mojo IPC to the browser process.
4. **Dispatch**: the browser-side `ExtensionFunctionDispatcher` looks up the function by
   its string name `"malabr.fit"` → finds `MalabrFitFunction` (that binding was made by the
   `DECLARE_EXTENSION_FUNCTION("malabr.fit", MALABR_FIT)` macro in
   [malabr_api.h](../../extensions/browser/api/malabr/malabr_api.h)) → instantiates it,
   attaches request context (including **which extension called** — this is where the
   trustworthy `extension_id` comes from), and calls `Run()`.
5. When our code later calls `Respond(...)`, the same machinery serializes the result,
   IPCs it back to the renderer, and invokes the JS callback with it.

### 3.3 Layer 3 — Our C++ in the browser process

Three classes, three files, one job each:

- **[malabr_api.cc](../../extensions/browser/api/malabr/malabr_api.cc)** — four near-identical
  `ExtensionFunction` subclasses (`MalabrFitFunction`, `MalabrPredictFunction`,
  `MalabrScoreFunction`, `MalabrCheckStatusFunction`). Per-call constants at the top:
  socket path `/tmp/malabr_v3.sck` and route strings `ROUTE_MALABR_FIT_API` etc.
- **[mserver_uds.cc](../../extensions/browser/api/malabr/mserver_uds.cc)** — `MServerUDS`,
  the **framing** layer: builds the header, writes header+payload, reads length-prefixed
  response. `WriteExact`/`ReadExact` loop because sockets may read/write partially.
- **[msocket_uds.cc](../../extensions/browser/api/malabr/msocket_uds.cc)** — `MSocketUDS`,
  the **raw socket** layer: literal POSIX calls `socket(AF_UNIX, SOCK_STREAM)`, `connect()`,
  `read()`, `write()`, plus errno→`net::Error` mapping. Blocking mode on purpose
  (that's the whole v2.1 ThreadPool design).

The exact sequence inside these files is traced in §4 steps 4–9.

Also in the browser process, but only at startup/shutdown:

- **[malabr_manager.cc](../../chrome/browser/malabr_manager.cc)** — singleton. If the
  `MalabrFeature` flag is on (`chrome://flags` → `enable-malabr`, defined in
  [chrome_features.cc](../../chrome/common/chrome_features.cc)), it resolves
  `DIR_CURRENT` (= the `src/` dir the browser was launched from) + hardcoded
  `addition_malabr/mserver/app.py`, and runs `python3 -u <that path>` via
  `base::LaunchProcess`. It keeps the `base::Process` handle and `Terminate()`s it on
  shutdown. Hooked into [chrome_browser_main.cc](../../chrome/browser/chrome_browser_main.cc)
  at ~line 1260 (start) and ~1933 (stop).
  (Legacy twins `ml_server_manager.cc` (v1 HTTP) and `ml_server_uds_manager.cc` (v2) still
  exist behind their own old flags.)

### 3.4 Layer 4 — The Python server

Folder: [addition_malabr/mserver/](../mserver/) — package `malabr_service/`, entry `app.py`.

| File | What it does, in one breath |
|---|---|
| [app.py](../mserver/app.py) | 5 lines: `from malabr_service.runtime import run_server; run_server()`. This is the file the browser launches. |
| [config.py](../mserver/malabr_service/config.py) | Frozen dataclass of all tunables, each overridable by a `MALABR_*` env var. Socket path default `/tmp/malabr_v3.sck` **must equal** the constant in malabr_api.cc. `model_dir` and `db_path` resolve relative to `mserver/`. |
| [runtime.py](../mserver/malabr_service/runtime.py) | The listener. Deletes stale socket file, starts `Supervisor`, `bind`+`listen(128)` on the UDS, installs SIGINT/SIGTERM cleanup. Each accepted connection goes to a `ThreadPoolExecutor`; `handle_connection()` does exactly one request/response then closes. |
| [protocol.py](../mserver/malabr_service/protocol.py) | Byte-level helpers: `recv_full` (loop until N bytes), `unpack_client_envelope` (split CSV header), `tensor_to_numpy` (FlatBuffer Tensor → numpy, reshape), `parse_request_params` (Param/StringVal union → dict, with "100"→100, "true"→True coercion), `build_response` (dict → ML.Response bytes). |
| [supervisor.py](../mserver/malabr_service/supervisor.py) | The coordinator (thesis §6.7). Owns train/result `mp.Queue`s, trainer `mp.Process` pool, inference `ThreadPoolExecutor`, the LRU `model_cache` (`OrderedDict` keyed `(client_id, model_id)`), the `MetadataStore`, and the `_result_loop` daemon thread. Entry point `route_request()` → authorize → dispatch by Action **and** route string (both must match — the "route verifier"). |
| [training.py](../mserver/malabr_service/training.py) | Runs **inside the worker processes**: `apply_training_limits()` (setrlimit RLIMIT_AS 2 GiB / RLIMIT_CPU 120 s), `build_model()` (type string → sklearn class + defaults + `filter_model_params` against the class signature), `train_one_job()` (fit → `joblib.dump` to `models/<model_id>.joblib`), `trainer_worker()` (the forever loop: get job → emit "started" → train → emit "completed"; `None` = shutdown sentinel). |
| [storage.py](../mserver/malabr_service/storage.py) | `MetadataStore`: all SQLite. Creates `clients` and `models` tables if missing. `ensure_client`/`authorize_client`/`touch_client_activity` (identity), `upsert_model` (INSERT..ON CONFLICT — one row per (client_id, model_id)), `get_model_row`, `touch_model_last_used`. A `threading.Lock` serializes access. |
| [ml.fbs](../mserver/ml.fbs) + [ML/](../mserver/ML/) | The schema and its **generated** Python bindings (`Request.py`, `Response.py`, `Tensor.py`, `Action.py`, …). Twin of `ml_generated.js`. |

---

## 4. One `fit()` call, traced hop-by-hop

Every step numbered; internal APIs in bold the first time they appear.

**In the renderer (JavaScript):**

1. User clicks "Run Demo" → `popup.js runFromUi()` → `model.fit(trainX, trainY)`
   ([base_model.js:251](../mserver/malabr_test_extension/malabr_js/base_model.js#L251)).
2. `_buildRequest({action: ML.Action.FIT, params, x, y})` serializes the `ML.Request`
   FlatBuffer → `Uint8Array` payload (§3.1 item 5).
3. `malabrFitAsync(payload)` calls **`chrome.malabr.fit({payload}, callback)`** and wraps
   the callback in a Promise. Renderer → Mojo IPC → browser process (§3.2).

**In the browser process (C++), UI thread:**

4. `MalabrFitFunction::Run()` ([malabr_api.cc:33](../../extensions/browser/api/malabr/malabr_api.cc#L33)):
   - **`extension()->id()`** — Chromium hands us the caller's real extension ID. The JS
     never sends it; it cannot be faked. This is the identity used for multi-tenancy.
   - **`EXTENSION_FUNCTION_VALIDATE(has_args())`** — kills the call if args are missing.
   - **`api::malabr::Fit::Params::Create(args())`** — generated from malabr.idl; type-checks
     and extracts `request.payload` as `std::vector<uint8_t>`.
   - **`AddRef()`** — manually keeps this object alive while background work runs
     (paired with `Release()` in step 11).
   - **`base::ThreadPool::PostTask(FROM_HERE, {base::MayBlock()}, base::BindOnce(&DispatchRequest, base::Unretained(this), payload, ext_id))`**
     — schedules `DispatchRequest` on a worker thread. `MayBlock` tells the pool this task
     will block (on socket I/O). `BindOnce` = "package this method + these arguments as a
     one-shot callable".
   - **`return RespondLater()`** — tells Chromium "the answer will come asynchronously".

**In the browser process, ThreadPool worker thread:**

5. `DispatchRequest()` constructs `MServerUDS(socket_path="/tmp/malabr_v3.sck",
   route="ROUTE_MALABR_FIT_API", extension_id)` and calls `Send(payload, len, response, error)`.
6. `MServerUDS::Send()` ([mserver_uds.cc:24](../../extensions/browser/api/malabr/mserver_uds.cc#L24)):
   `MSocketUDS::Connect()` → POSIX **`socket(AF_UNIX, SOCK_STREAM)`** + **`connect()`** to
   the socket file.
7. `GetHeaderPayload(payload_size)` builds the header:
   string `"ROUTE_MALABR_FIT_API,<extension_id>,<payload_size>"`, prefixed with its own
   length as 4 bytes big-endian (**`htonl`**).
8. **Wire format** (both directions):
   ```
   → [4B BE header_len]["ROUTE,ext_id,size"][FlatBuffer Request bytes]
   ← [4B BE resp_len][FlatBuffer Response bytes]
   ```
   `WriteExact` loops `socket.Write()` until all bytes are out; then `ReadExact` reads the
   4-byte response length (**`ntohl`**) and then exactly that many body bytes.

**In the Python server process:**

9. `runtime.handle_connection()`:
   `recv_full(conn, 4)` → **`struct.unpack(">I")`** (">I" = big-endian uint32, matching
   htonl) → `recv_full(header_len)` → `unpack_client_envelope()` → `(route, client_id,
   payload_size)` → `recv_full(payload_size)` → `ML.Request.GetRootAsRequest(bytes)`.
10. `Supervisor.route_request(req, client_id, route)`:
    - `_authorize_client(client_id)` → `MetadataStore.ensure_client()` (INSERT if new,
      status "active") + `authorize_client()` (status must be "active") + activity
      timestamps. Fail → `{"status":1,"message":"unauthorized"}`.
    - `req.Action()==FIT` **and** `route=="ROUTE_MALABR_FIT_API"` → `handle_fit()`:
      decode id/type/params, tensors → numpy, validate shapes (≥4 samples, x/y lengths
      match), `upsert_model(status="queued")` into SQLite, **`train_queue.put_nowait(job)`**,
      and *immediately* return `{"status":0,"message":"fit_started"}` — training has not
      happened yet. (Queue full → status "failed", message "fit_failed".)
    - Meanwhile, asynchronously: a `trainer_worker` **mp.Process** picks the job up →
      emits `"started"` on `result_queue` → `_result_loop` daemon thread flips SQLite row
      to "training" → worker runs `model.fit(x,y)` under setrlimit caps →
      `joblib.dump()` to `models/<model_id>.joblib` → emits `"completed"` →
      `_result_loop` upserts "ready" (or "failed") and evicts the model's LRU cache entry.
11. `build_response()` packs status+message into an `ML.Response` FlatBuffer;
    `runtime` sends `[4B len][bytes]` back. **C++**: `ReadExact` completes, `Send()` returns;
    `DispatchRequest` posts the result to the UI thread via
    **`content::GetUIThreadTaskRunner({})->PostTask(BindOnce(&OnSuccess, weak_ptr_factory_.GetWeakPtr(), response))`**
    (extension functions must respond on the UI thread; **`GetWeakPtr`** makes the callback
    safe if the object died meanwhile). `OnSuccess()` → **`Respond(WithArguments(base::Value(result)))`**
    → `Release()` (balances step 4's AddRef).
12. Chromium IPCs the value back to the renderer → the JS callback in `malabrFitAsync`
    fires → the Promise resolves → `await model.fit(...)` in popup.js returns `true`.

---

## 5. The full workflow (why there is a polling loop)

Because step 10 returns *before* training finishes, the client must poll:

```
popup.js                          C++ (per call: new socket)        Python server
--------                          --------------------------        -------------
await model.fit(x,y)      ──FIT──────────────────────────────▶  queue job, reply "fit_started"
                                                                  [worker trains in background]
loop every 500ms:
  await model.check_status() ──CHECK_STATUS──────────────────▶  read models row → "training"
  ...                        ──CHECK_STATUS──────────────────▶  → "ready"        (exit loop)
await model.predict(testX)   ──PREDICT───────────────────────▶  wait_for_terminal_state →
                                                                  inference_pool → LRU cache /
                                                                  joblib.load(mmap) → y_pred JSON
await model.score(testX,testY)──SCORE────────────────────────▶  same path → score as string
model.destroy()                   (no request — client-side only; server keeps the model)
```

Note: **every request is its own socket connection** (connect → one exchange → close), on
both the C++ side (`MServerUDS` per call) and the server side (`handle_connection` closes).
There is no persistent connection or streaming yet — worth knowing before the LLM pivot,
where streaming tokens will change exactly this.

---

## 6. Internal API reference (everything non-obvious we call)

### Chromium C++ side

| API | Where used | What it does / why |
|---|---|---|
| `BASE_FEATURE / base::FeatureList::IsEnabled` | chrome_features.cc, malabr_manager.cc | Runtime on/off switch surfaced in chrome://flags as `enable-malabr`. |
| `base::PathService::Get(base::DIR_CURRENT)` | malabr_manager.cc | Directory the browser was launched from (`src/`); server path is relative to it. |
| `base::CommandLine` + `base::LaunchProcess` | malabr_manager.cc | Spawn `python3 -u app.py`; returns a `base::Process` handle for later `Terminate()`. |
| `DECLARE_EXTENSION_FUNCTION("malabr.fit", MALABR_FIT)` | malabr_api.h | Binds JS name → C++ class; enum value must exist in extension_function_histogram_value.h. |
| `EXTENSION_FUNCTION_VALIDATE(cond)` | malabr_api.cc | Arg sanity check; fails the extension call cleanly if false. |
| `api::malabr::Fit::Params::Create(args())` | malabr_api.cc | Generated (from malabr.idl) type-safe argument parser. |
| `AddRef()` / `Release()` | malabr_api.cc | ExtensionFunction is ref-counted; keep it alive across the async gap manually. |
| `RespondLater()` / `Respond(WithArguments(...))` / `Respond(Error(...))` | malabr_api.cc | The async response contract: Run() promises a later answer; OnSuccess/OnError deliver it. |
| `base::ThreadPool::PostTask(..., {base::MayBlock()}, ...)` | malabr_api.cc | Run blocking socket I/O off the UI thread (the v2.1 design). |
| `base::BindOnce` / `base::Unretained(this)` | malabr_api.cc | Package method+args as a one-shot callback; `Unretained` = "I guarantee lifetime myself" (via AddRef). |
| `content::GetUIThreadTaskRunner({})->PostTask` | malabr_api.cc | Hop back to the UI thread, because Respond() must happen there. |
| `weak_ptr_factory_.GetWeakPtr()` | malabr_api.cc | If the function object is gone by callback time, the call becomes a no-op instead of a crash. |
| `htonl` / `ntohl` | mserver_uds.cc | Host↔network (big-endian) 32-bit conversion for the length prefixes; pairs with Python `">I"`. |
| `socket/connect/read/write` (POSIX) | msocket_uds.cc | The raw blocking UDS client; errno mapped to `net::Error` codes. |

### Python side

| API | Where used | What it does / why |
|---|---|---|
| `socket.socket(AF_UNIX, SOCK_STREAM)` + `bind/listen/accept` | runtime.py | The UDS server; the socket *is* the file `/tmp/malabr_v3.sck`. |
| `struct.unpack(">I", ...)` / `struct.pack(">I", ...)` | runtime.py | Big-endian uint32 length prefixes (matches C++ htonl/ntohl). |
| `concurrent.futures.ThreadPoolExecutor` | runtime.py, supervisor.py | Connection handling pool + inference pool (threads are fine here: I/O + numpy release the GIL enough). |
| `multiprocessing.Process` / `mp.Queue` | supervisor.py, training.py | Training in separate OS processes: crash/memory isolation; queues carry jobs in, events out. |
| `resource.setrlimit(RLIMIT_AS / RLIMIT_CPU)` | training.py | Per-worker caps (2 GiB / 120 s) so a runaway job dies instead of the server (thesis §6.9). |
| `joblib.dump` / `joblib.load(..., mmap_mode="r")` | training.py, supervisor.py | Persist / memory-map model artifacts; mmap avoids re-deserializing hot models (thesis §6.10). |
| `sqlite3.connect(..., check_same_thread=False)` + `threading.Lock` | storage.py | One shared connection, manually serialized. |
| `INSERT ... ON CONFLICT DO UPDATE` | storage.py | Upsert: re-fitting the same model_id replaces the row. |
| `OrderedDict` pop/re-insert + `popitem(last=False)` | supervisor.py | The LRU cache mechanics: re-insert on hit = mark recent; popitem(False) = evict oldest. |
| `flatbuffers.Builder` / generated `ML.*` | protocol.py | Build `ML.Response`; read `ML.Request`. |

---

## 7. How `chrome.malabr` gets registered inside Chromium (the 8-file chain)

Adding one extension API touches all of these — if any is missing, the API silently
doesn't exist or fails at compile:

| # | File | Contribution |
|---|---|---|
| 1 | [extensions/common/api/malabr.idl](../../extensions/common/api/malabr.idl) | The schema: 4 functions, each `{ArrayBuffer payload}` in, `DOMString` out via callback. |
| 2 | [extensions/common/api/schema.gni](../../extensions/common/api/schema.gni) | Adds malabr.idl to codegen → build produces `gen/.../extensions/common/api/malabr.h` (the `Params::Create` code) and the renderer JS bindings. |
| 3 | [extensions/common/api/_api_features.json](../../extensions/common/api/_api_features.json) (~520) | Namespace availability: `privileged_extension` contexts, requires `permission:malabr`. |
| 4 | [extensions/common/api/_permission_features.json](../../extensions/common/api/_permission_features.json) (~550) | Declares the `malabr` permission string extensions may request. |
| 5 | [extensions/common/mojom/api_permission_id.mojom](../../extensions/common/mojom/api_permission_id.mojom) | Stable enum `kMalabr = 262`. |
| 6 | [extensions/common/permissions/extensions_api_permissions.cc](../../extensions/common/permissions/extensions_api_permissions.cc) | Maps enum ↔ string `"malabr"` (what manifest.json uses). |
| 7 | [extensions/browser/extension_function_histogram_value.h](../../extensions/browser/extension_function_histogram_value.h) (~1970) | `MALABR_FIT=1903 … MALABR_CHECKSTATUS=1906` — required by the DECLARE macro. |
| 8 | [extensions/browser/api/malabr/BUILD.gn](../../extensions/browser/api/malabr/BUILD.gn) + [extensions/browser/api/BUILD.gn:71](../../extensions/browser/api/BUILD.gn#L71) | Compiles our three .cc/.h pairs and links them into the extensions API layer. |

Browser-side feature/launch files (separate from API registration):
`chrome_features.{h,cc}` (flag), `about_flags.cc` + `flag_descriptions.{h,cc}`
(chrome://flags entry), `chrome/browser/BUILD.gn` (`source_set("malabr_feature")`),
`chrome_browser_main.cc` (start/stop hooks), `malabr_manager.{h,cc}` (launcher).

---

## 8. Complete file map

### 8.1 v3.0 hot path

```
Chromium (C++)
  chrome/common/chrome_features.{h,cc}            kMalabrFeature flag definition
  chrome/browser/about_flags.cc, flag_descriptions.{h,cc}   chrome://flags UI
  chrome/browser/chrome_browser_main.cc           calls Start/Stop on the 3 managers
  chrome/browser/malabr_manager.{h,cc}            spawns/kills python3 app.py   [current]
  chrome/browser/ml_server_manager.cc             v1.0 HTTP launcher            [legacy]
  chrome/browser/ml_server_uds_manager.cc         v2.x launcher                 [legacy]
  extensions/common/api/malabr.idl                API schema (→ generated C++ & JS bindings)
  extensions/common/api/{schema.gni,_api_features.json,_permission_features.json}
  extensions/common/mojom/api_permission_id.mojom
  extensions/common/permissions/extensions_api_permissions.cc
  extensions/browser/extension_function_histogram_value.h
  extensions/browser/api/malabr/
    BUILD.gn                                      compiles this dir
    malabr_api.{h,cc}                             4 ExtensionFunction handlers
    mserver_uds.{h,cc}                            framing (header + length prefixes)
    msocket_uds.{h,cc}                            raw blocking POSIX UDS client

Python server            addition_malabr/mserver/
  app.py                                          entrypoint launched by the browser
  malabr_service/config.py                        env-var config (socket path!)
  malabr_service/runtime.py                       UDS listener + per-connection handler
  malabr_service/protocol.py                      framing + FlatBuffer helpers
  malabr_service/supervisor.py                    router, queues, LRU cache, result loop
  malabr_service/training.py                      worker process: sklearn fit + rlimits
  malabr_service/storage.py                       SQLite (clients, models)
  ml.fbs                                          FlatBuffers schema (source of truth)
  ML/*.py                                         GENERATED python bindings

Extension (JS)           addition_malabr/mserver/malabr_test_extension/
  manifest.json                                   permissions: ["malabr"], popup, worker
  popup.html / popup.css / popup.js               test UI (fit→poll→predict→score)
  dmeo.js                                         scripted demo of same flow
  background.js                                   stub (one console.log)
  malabr_js/base_model.js                         SDK core (build request, call chrome.malabr.*)
  malabr_js/{logistic_regression,svc,random_forest_classifier}.js   thin subclasses
  malabr_js/ml_generated.js                       GENERATED js bindings (twin of ML/)
  malabr_js/flatbuffers.js                        flatbuffers runtime lib
```

### 8.2 Legacy / auxiliary (kept for history & benchmarks, not on the hot path)

| Directory | Version | Notes |
|---|---|---|
| `addition_malabr/modelserver/` | v1.0 | Flask/HTTP servers + node/onnx/pytorch variants. |
| `addition_malabr/modelserver_uds/` | v2.0/2.1 | Old UDS server (`app/server.py`, routers, handlers). Launched by `ml_server_uds_manager.cc`. |
| `addition_malabr/reading server extension/` | v1.x | Old HTTP test extension. |
| `addition_malabr/read_server_uds_extension/` | v2.x | Old UDS test extension (`qa_schema.fbs`); pairs with `extensions/browser/api/read_server_uds/`. |
| `addition_malabr/baseline/` | — | In-browser tiny-bert QA baselines. |
| `addition_malabr/benchmark_data/`, `plots/` | — | Benchmark generation & plotting scripts. |
| `addition_malabr/design_doc/`, `learning_and_setup/` | — | Docs (this file lives in design_doc/). |
| `Dockerfile`, `docker-compose.yml`, `script.sh`, `uploads/` | — | Container experiments / helpers. |

---

## 9. Runtime artifacts & change cheat sheet

### Created at runtime (not in git)

| Artifact | Created by | Purpose |
|---|---|---|
| `/tmp/malabr_v3.sck` | runtime.py `bind()` | The UDS endpoint; deleted on shutdown. |
| `addition_malabr/mserver/malabr.db` | storage.py first connect | SQLite metadata (clients, models). |
| `addition_malabr/mserver/models/*.joblib` | training.py after fit | Persisted model artifacts (mmap-loaded). |

### Created at build time (in `out/<dir>/gen/`)

| Artifact | Generator | Consumed by |
|---|---|---|
| `gen/extensions/common/api/malabr.h/.cc` | json_schema_compiler ← malabr.idl | malabr_api.cc (`Params::Create`), renderer bindings |

### If I change X, what else must move

| Change | Must move together |
|---|---|
| Payload schema | `ml.fbs` → regenerate **both** `mserver/ML/` (`flatc --python`) and `malabr_js/ml_generated.js` (`flatc --js`) |
| Socket path | `malabr_api.cc kMLServerUDSPath` ↔ `config.py` MALABR_SOCKET_PATH default |
| Envelope/header format | `mserver_uds.cc GetHeaderPayload/Send` ↔ `protocol.py unpack_client_envelope` + `runtime.py handle_connection` (and keep BE `htonl` ↔ `">I"` in sync) |
| Route strings | `malabr_api.cc kMalabr*Route` ↔ `supervisor.py route_request` |
| New API method (e.g. `generate`) | `malabr.idl` + new class in `malabr_api.{h,cc}` + new enum in `extension_function_histogram_value.h` + SDK method in `base_model.js` + handler in `supervisor.py` (+ Action in `ml.fbs` if it's a new action) |
| New model type | `training.py build_model` + subclass in `malabr_js/` + `popup.js MODEL_REGISTRY` |
| Server launch path | `malabr_manager.cc kMServerUDSPath` (relative to `src/` at runtime) |
