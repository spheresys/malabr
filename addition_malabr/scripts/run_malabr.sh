#!/usr/bin/env bash
#
# Launch Chromium with MALABR tunables, and record exactly which config
# produced the run.
#
# Why this exists: every tunable here is a base::FeatureParam rather than a
# C++ constant, specifically so it can be changed WITHOUT a rebuild (a full
# Chromium build on this machine costs 8-9 hours). This script is the place
# those values live, so an experiment is reproducible from one file instead
# of a remembered command line.
#
# phase1_design.md 11a requires calibration numbers be tied to the config
# that produced them -- that is what the run-log at the bottom is for.
#
# Usage:
#   ./run_malabr.sh                                  # defaults below
#   FRAME_READ_TIMEOUT_SECONDS=120 ./run_malabr.sh   # override one value
#   ./run_malabr.sh --dry-run                        # print, do not launch
#   ./run_malabr.sh -- --incognito                   # pass extra chrome args

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHROME_BIN="${CHROME_BIN:-${REPO_ROOT}/out/Default/chrome}"

# ---------------------------------------------------------------------------
# TUNABLES
#
# Add a line here when a new FeatureParam is introduced. The name on the LEFT
# must match the /*name=*/ string in extensions/common/extension_features.cc
# exactly -- a typo is silently ignored by Chromium (it just uses the default),
# which is why the verify step below prints what was actually sent.
# ---------------------------------------------------------------------------
declare -A TUNABLES=(
  # extensions/common/extension_features.cc :: kMalabrFrameReadTimeoutSeconds
  # Per-frame recv() budget in seconds. Placeholder until prefill latency is
  # measured (phase1_design.md 14).
  [frame_read_timeout_seconds]="${FRAME_READ_TIMEOUT_SECONDS:-60}"

  # Scheduler constants (round budget, exclusion bound, stale-visibility
  # timeout) are NOT FeatureParams: they live in mserver/malabr_service/
  # scheduler.py, because the server owns scheduling. Edit them there.
)

FEATURE_NAME="MalabrTunables"

# The interpreter MalabrManager should spawn app.py with.
#
# Not optional in practice: "python3" resolves to whatever is first on PATH,
# and in a Chromium build shell that is the build environment's python, which
# has no llama_cpp. app.py then exits immediately with ModuleNotFoundError, no
# socket is ever created, and every generate() fails with nothing in the UI
# saying why. Read by malabr_manager.cc as MALABR_PYTHON.
MALABR_PYTHON="${MALABR_PYTHON:-/home/chaitu/Desktop/vscode/malabr/bin/python}"
export MALABR_PYTHON

# Shared-KV memory governance (phase1_design.md 8). 1: one shared pool of n_ctx
# cells, per-session soft cap of n_ctx/2, aggregate compaction -- so 1-2 tabs get
# a big context (page summaries fit) and many tabs degrade to fair share. 0: the
# original n_seq_max equal hard slices. The Python default is 0; this launcher
# opts in. Set MALABR_SHARED_KV=0 here to A/B against the partitioned model.
MALABR_SHARED_KV="${MALABR_SHARED_KV:-1}"
export MALABR_SHARED_KV

# CPU governance (phase1_design.md 11), two layers:
#   MALABR_CPU_DUTY  0<d<=1  -- PROGRESSIVE: the engine sleeps proportionally
#     after each round so average CPU lands near n_threads*d, smoothly. 1.0
#     (default) = flat out. Try 0.5 if inference makes the machine feel slow.
#   MALABR_CPU_MAX   "N" cores, "N%", or "0"/"off"  -- HARD: the server moves
#     its own pid into a transient systemd scope with a kernel-enforced cpu.max
#     quota a spike or a bug cannot cross. UNSET = a computed default (half the
#     logical CPUs, never below n_threads+1) -- a backstop that never bites
#     normal operation. Needs a user systemd + busctl; best-effort, falls back
#     to duty-only. Set "off" to disable.
MALABR_CPU_DUTY="${MALABR_CPU_DUTY:-1.0}"
export MALABR_CPU_DUTY
MALABR_CPU_MAX="${MALABR_CPU_MAX:-}"
export MALABR_CPU_MAX

# Separate profile so experiments never disturb a real browsing profile, and
# so a run can be reset by deleting one directory.
USER_DATA_DIR="${USER_DATA_DIR:-/tmp/malabr-profile}"

# Optional unpacked extension to auto-load.
EXTENSION_DIR="${EXTENSION_DIR:-}"

RUN_LOG="${RUN_LOG:-${REPO_ROOT}/addition_malabr/scripts/run_history.log}"

# ---------------------------------------------------------------------------
# Build the --enable-features string:
#   MalabrTunables:name1/value1/name2/value2
# Chromium synthesizes a field trial when params are given without a study,
# verified in base/feature_list.cc::ParseEnableFeatureString.
# ---------------------------------------------------------------------------
params=""
for key in "${!TUNABLES[@]}"; do
  params+="${key}/${TUNABLES[$key]}/"
done
params="${params%/}"   # strip trailing slash

if [[ -n "$params" ]]; then
  ENABLE_FEATURES="${FEATURE_NAME}:${params}"
else
  ENABLE_FEATURES="${FEATURE_NAME}"
fi

# MalabrFeature is a SEPARATE feature from MalabrTunables, and it is
# FEATURE_DISABLED_BY_DEFAULT (chrome/common/chrome_features.cc). It is the
# switch MalabrManager::StartMLServerIfEnabled() checks on its first line, so
# without it the manager returns immediately: no server is spawned, no socket
# appears, and every generate() fails with nothing logged to explain why --
# because nothing was ever attempted. Enabling MalabrTunables alone is not
# enough; it only carries the timeout parameters.
ENABLE_FEATURES="${MALABR_FEATURE_NAME:-MalabrFeature},${ENABLE_FEATURES}"

# ---------------------------------------------------------------------------
# Assemble argv
# ---------------------------------------------------------------------------
DRY_RUN=0
EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --)        shift; EXTRA_ARGS+=("$@"); break ;;
    *)         EXTRA_ARGS+=("$1"); shift ;;
  esac
done

CHROME_ARGS=(
  "--enable-features=${ENABLE_FEATURES}"
  "--user-data-dir=${USER_DATA_DIR}"
  # MALABR logs at INFO; without this they are dropped.
  "--enable-logging=stderr"
  "--v=0"
)

if [[ -n "$EXTENSION_DIR" ]]; then
  # Absolute, because the launch below cd's to the repo root.
  EXTENSION_DIR="$(cd "$EXTENSION_DIR" && pwd)"
  CHROME_ARGS+=("--load-extension=${EXTENSION_DIR}")
fi
if [[ "${NO_SANDBOX:-0}" == "1" ]]; then
  echo "  WARNING: --no-sandbox is set; renderer processes are UNSANDBOXED." >&2
  CHROME_ARGS+=("--no-sandbox")
fi

CHROME_ARGS+=("${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}")

# ---------------------------------------------------------------------------
# Report + record
# ---------------------------------------------------------------------------
echo "MALABR launch config"
echo "  chrome      : ${CHROME_BIN}"
echo "  profile     : ${USER_DATA_DIR}"
echo "  tunables    :"
for key in "${!TUNABLES[@]}"; do
  printf '      %-32s = %s\n' "$key" "${TUNABLES[$key]}"
done
echo "  --enable-features=${ENABLE_FEATURES}"
echo

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[dry run] would exec:"
  printf '  %q ' "$CHROME_BIN" "${CHROME_ARGS[@]}"; echo
  exit 0
fi

# ---------------------------------------------------------------------------
# SUID SANDBOX
#
# A developer build does not produce out/Default/chrome-sandbox unless the
# chrome_sandbox target is built AND the binary is made setuid root. Without
# it Chromium aborts at startup with
#   FATAL:setuid_sandbox_host.cc  The SUID sandbox helper binary is missing
# which looks alarming but has nothing to do with MALABR -- nothing of ours has
# run at that point.
#
# NO_SANDBOX=1 passes --no-sandbox. That genuinely weakens the browser: renderer
# processes lose their sandbox, so a hostile page has more reach. Acceptable for
# a local experiment on a build you made yourself; not something to leave on for
# ordinary browsing. The proper fix is the two sudo commands printed below.
if [[ ! -e "${CHROME_BIN%/*}/chrome-sandbox" && "${NO_SANDBOX:-0}" != "1" ]]; then
  echo "ERROR: ${CHROME_BIN%/*}/chrome-sandbox is missing." >&2
  echo "" >&2
  echo "  Quick, for local testing (weakens the renderer sandbox):" >&2
  echo "      NO_SANDBOX=1 $0" >&2
  echo "" >&2
  echo "  Proper, once (needs sudo):" >&2
  echo "      ./third_party/siso/siso ninja -C out/Default chrome_sandbox" >&2
  echo "      # NOTE the rename: the target builds chrome_sandbox with an" >&2
  echo "      # UNDERSCORE, the runtime looks for chrome-sandbox with a HYPHEN." >&2
  echo "      # cp not mv, so rebuilding the target cannot clobber the setuid copy." >&2
  echo "      sudo cp out/Default/chrome_sandbox out/Default/chrome-sandbox" >&2
  echo "      sudo chown root:root out/Default/chrome-sandbox" >&2
  echo "      sudo chmod 4755 out/Default/chrome-sandbox" >&2
  echo "      export CHROME_DEVEL_SANDBOX=\$PWD/out/Default/chrome-sandbox" >&2
  exit 1
fi

if [[ ! -x "$CHROME_BIN" ]]; then
  echo "ERROR: chrome binary not found or not executable:" >&2
  echo "       ${CHROME_BIN}" >&2
  echo "       Build it first, or set CHROME_BIN=/path/to/chrome" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# STALENESS GUARD
#
# A chrome binary from before your last source edit will run happily and show
# none of your changes -- an easy hour to lose. Compare the binary's mtime
# against the MALABR sources and fail loudly rather than silently testing old
# code. Override with ALLOW_STALE=1 when you genuinely mean to.
# ---------------------------------------------------------------------------
MALABR_SOURCES=(
  "${REPO_ROOT}/extensions/browser/api/malabr"
  "${REPO_ROOT}/extensions/common/api/malabr.idl"
  "${REPO_ROOT}/extensions/common/extension_features.cc"
  "${REPO_ROOT}/extensions/common/extension_features.h"
  "${REPO_ROOT}/extensions/common/api/_api_features.json"
  "${REPO_ROOT}/extensions/common/api/_permission_features.json"
  "${REPO_ROOT}/chrome/browser/malabr_manager.cc"
  "${REPO_ROOT}/chrome/browser/malabr_manager.h"
  # Where the manager is started/stopped, and where MalabrFeature is defined.
  "${REPO_ROOT}/chrome/browser/chrome_browser_main.cc"
  "${REPO_ROOT}/chrome/common/chrome_features.cc"
  "${REPO_ROOT}/chrome/common/chrome_features.h"
)
newest_src=0
for path in "${MALABR_SOURCES[@]}"; do
  [[ -e "$path" ]] || continue
  while IFS= read -r ts; do
    (( ts > newest_src )) && newest_src=$ts
  done < <(find "$path" -type f -printf '%T@\n' 2>/dev/null | cut -d. -f1)
done
chrome_ts=$(stat -c %Y "$CHROME_BIN")

if (( newest_src > chrome_ts )); then
  echo "ERROR: chrome binary is OLDER than MALABR sources -- it does not" >&2
  echo "       contain your changes." >&2
  echo "         chrome built : $(date -d @${chrome_ts} -Is)" >&2
  echo "         newest source: $(date -d @${newest_src} -Is)" >&2
  echo "       Rebuild, or set ALLOW_STALE=1 to run the old binary anyway." >&2
  [[ "${ALLOW_STALE:-0}" == "1" ]] || exit 1
  echo "       ALLOW_STALE=1 set -- continuing with the stale binary." >&2
fi

# One line per run, so a measurement can always be traced back to its config.
# The MALABR_* environment is recorded too: since the scheduler moved into the
# server, most of the config that shapes a run is Python-side env, not
# FeatureParams, and logging --enable-features alone would not reproduce it.
{
  printf '%s\t%s\t%s\t%s\n' \
    "$(date -Is)" \
    "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)" \
    "${ENABLE_FEATURES}" \
    "$(env | grep '^MALABR_' | sort | tr '\n' ' ')"
} >> "$RUN_LOG"

echo "recorded to ${RUN_LOG}"
echo "launching..."
# MalabrManager resolves addition_malabr/mserver/app.py relative to chrome's
# working directory, so launch from the repo root whatever directory this
# script was invoked from.
cd "$REPO_ROOT"
exec "$CHROME_BIN" "${CHROME_ARGS[@]}"
