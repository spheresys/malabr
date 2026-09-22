#!/usr/bin/env python3
"""Provision Chrome's built-in on-device model (Gemini Nano) for baseline runs.

Why this is not just a flag: in Chromium 127 the component that holds the model
is NOT downloaded until `should_install()` holds in
`on_device_model_component.cc`, which needs FIVE things at once --

  enabled_by_feature             --enable-features below
  enabled_by_enterprise_policy   default-allowed, nothing to do
  device_capable                 the measured performance class must be in
                                 `compatible_on_device_performance_classes`
                                 (default "3,4,5,6"); "*" accepts any, which is
                                 what chrome://flags calls BypassPerfRequirement
  disk_space_available           20 GB free ON THE VOLUME HOLDING THE PROFILE
                                 (DIR_COMPONENT_USER lives under user-data-dir)
  on_device_feature_recently_used  some page must have CALLED the API within
                                 7 days -- CanCreateSession() is what records it

and the criteria are only evaluated in `OnStartup()`. So the first launch can
never have the model: it exists to record the feature use. The component is
registered on the SECOND launch, downloads in the background, and only then
does canCreateTextSession() go from "no" to "readily".

That is the whole dance this script automates.

  python3 nano_provision.py            # run it; it restarts Chrome itself
  python3 nano_provision.py --wait 900 # allow longer for the download
"""
import argparse, json, os, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nano_cdp import WS, http_json  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHROME = os.environ.get("CHROME_BIN", os.path.join(REPO_ROOT, "out/Default/chrome"))

# Default profile deliberately on the big volume: the 20 GB install check is
# made against the profile's filesystem, and this machine's root has ~2 GB free.
PROFILE = os.environ.get("NANO_PROFILE", "/media/chaitu/chaitanya/malabr-nano-profile")
PORT = int(os.environ.get("NANO_PORT", "9333"))

FEATURES = ",".join([
    # blink: exposes window.ai (runtime_enabled_features.json5 ModelExecutionAPI)
    "EnableModelExecutionAPI",
    # browser: the model-execution machinery at all
    "OptimizationGuideModelExecution",
    # browser: the on-device path, with the perf-class gate opened to any class
    "OptimizationGuideOnDeviceModel:compatible_on_device_performance_classes/*",
])


def launch(extra_args=()):
    args = [
        CHROME,
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={PORT}",
        f"--enable-features={FEATURES}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-backgrounding-occluded-windows",
        *extra_args,
        "about:blank",
    ]
    if os.environ.get("NO_SANDBOX", "1") == "1":
        args.insert(1, "--no-sandbox")
    log = open("/tmp/nano_chrome.log", "ab")
    log.write(f"\n\n===== launch {time.strftime('%F %T')} =====\n".encode())
    log.flush()
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)


def attach():
    targets = http_json(PORT, timeout=90)
    page = next(t for t in targets if t["type"] == "page")
    ws = WS(page["webSocketDebuggerUrl"])
    ws.call("Runtime.enable")
    return ws


def availability(ws):
    """'readily' | 'after-download' | 'no'.

    Calling this is not passive: AIManagerImpl -> CanCreateSession() ->
    OnDeviceEligibleFeatureUsed(), which writes the
    kLastTimeOnDeviceEligibleFeatureWasUsed pref. That write is the entire
    purpose of the first phase.
    """
    return ws.evaluate("window.ai ? window.ai.canCreateTextSession() : 'no-api'",
                       timeout=60)


def component_dirs():
    base = os.path.join(PROFILE, "OptGuideOnDeviceModel")
    if not os.path.isdir(base):
        return []
    return sorted(os.listdir(base))


def du_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


def kill(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=600,
                    help="seconds to wait for the component download")
    ap.add_argument("--fresh", action="store_true",
                    help="delete the profile first")
    args = ap.parse_args()

    if not os.path.exists(CHROME):
        sys.exit(f"chrome not built at {CHROME}")
    if args.fresh and os.path.isdir(PROFILE):
        print(f"removing {PROFILE}")
        shutil.rmtree(PROFILE)
    os.makedirs(PROFILE, exist_ok=True)

    free_gb = shutil.disk_usage(PROFILE).free / 1e9
    print(f"profile      : {PROFILE}")
    print(f"free space   : {free_gb:.1f} GB   (install needs 20 GB on THIS volume)")
    if free_gb < 21:
        print("  WARNING: below the 20 GB install threshold; the component will")
        print("  never register. Move the profile to a bigger volume.")

    # ---- phase 1: record the feature use -------------------------------
    print("\n[1/2] recording feature use (this launch cannot have the model yet)")
    proc = launch()
    try:
        ws = attach()
        print("      window.ai exposed :", bool(ws.evaluate("!!window.ai", await_promise=False)))
        print("      availability      :", availability(ws))
        time.sleep(3)          # let the pref write land
        ws.close()
    finally:
        kill(proc)
    print("      restarting so OnStartup() re-evaluates the install criteria")

    # ---- phase 2: the component should now register and download -------
    print(f"\n[2/2] waiting up to {args.wait}s for the component to download")
    proc = launch()
    try:
        ws = attach()
        deadline = time.time() + args.wait
        last = None
        while time.time() < deadline:
            avail = availability(ws)
            dirs = component_dirs()
            size = du_mb(os.path.join(PROFILE, "OptGuideOnDeviceModel")) if dirs else 0
            line = f"      t+{int(time.time()-(deadline-args.wait)):4d}s  {avail:<14} component={dirs or '-'} {size:.0f} MB"
            if line != last:
                print(line, flush=True)
                last = line
            if avail == "readily":
                print("\n  READY. Chrome's built-in model is usable as a baseline.")
                out = ws.evaluate("""(async () => {
                    const s = await window.ai.createTextSession();
                    const r = await s.prompt('Reply with one word: ready');
                    s.destroy(); return r;
                })()""", timeout=300)
                print("  smoke prompt ->", repr(out))
                print(f"\n  Keep this profile: {PROFILE}")
                return 0
            time.sleep(15)
        print(f"\n  Still '{avail}' after {args.wait}s.")
        print("  Open chrome://on-device-internals in this profile for the reason,")
        print("  and check /tmp/nano_chrome.log.")
        return 1
    finally:
        kill(proc)


if __name__ == "__main__":
    sys.exit(main())
