#!/bin/bash
# Overnight campaign: one run at a time; a finished item is never run again.
S=/tmp/claude-1000/-media-chaitu-chaitanya-malabr-src/08315610-55c0-4645-a355-34f92fc3622c/scratchpad/camp
R=/media/chaitu/chaitanya/malabr/src; A=$R/addition_malabr; NEWT=$A/mserver; OLDT=$S/oldtree/addition_malabr/mserver
OUT=$A/design_doc/bench_runs_2026-11; LOG=$OUT/progress.log; COPY=~/malabr_design_doc_copy
PY=/home/chaitu/Desktop/vscode/malabr/bin/python
BUILD=$(git -C $R rev-parse --short=12 design-freeze-2026-11) || exit 2
MACHINE="$(hostname)-$(lscpu | sed -n 's/^Model name: *//p' | tr -cd 'A-Za-z0-9' | cut -c1-24)"
CALDIR=$OUT/calibrations/${MACHINE}_${BUILD}; mkdir -p $CALDIR $OUT/raw $OUT/chrome
DEV=$(stat -c %d /media/chaitu/chaitanya/malabr)
step() { echo "[$(date '+%F %T')] $*" >> $LOG; }
drive_ok() { [ "$(stat -c %d /media/chaitu/chaitanya/malabr 2>/dev/null)" = "$DEV" ] && [ -d $A/models ]; }
drive_lost() { mkdir -p $COPY; echo "[$(date '+%F %T')] drive lost during: $1" >> $COPY/OVERNIGHT_DRIVE_LOST.txt; exit 99; }
sweep() { for n in chrome llama-server ollama; do p=$(pgrep -x $n); [ -n "$p" ] && kill $p; done; sleep 3
          for n in chrome llama-server ollama; do p=$(pgrep -x $n); [ -n "$p" ] && kill -9 $p; done; }
save() { # commit the raw results and copy them: called between runs
  nice -n 19 ionice -c3 git -C $R add -f $OUT $A/mserver/BENCHMARKS_2026-11.md 2>/dev/null
  git -C $R diff --cached --quiet || nice -n 19 ionice -c3 git -C $R commit -q -m "MALABR: campaign raw results: $1"
  mkdir -p $COPY/bench_runs_2026-11; nice -n 19 ionice -c3 cp -r $OUT/. $COPY/bench_runs_2026-11/
  step "saved: $1 ($(git -C $R log -1 --format=%h))"; }
report() { ( cd $NEWT && nice -n 19 $PY tests/campaign_report.py $OUT ) >> $OUT/report.log 2>&1; }
# item NAME DIR TIMEOUT OUTFILE ENV... -- CMD...
item() { name=$1; dir=$2; limit=$3; out=$4; shift 4; envs=(); while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  grep -qF "completed: $name |" $LOG && return 0
  [ -e $S/STOP ] && { step "STOPPED cleanly before: $name"; report; save "stopped"; exit 0; }
  drive_ok || drive_lost "before $name"
  for attempt in 1 2; do
    sweep
    read -r _ total used _ < <(free -m | sed -n 3p); avail=$(free -m | awk 'NR==2{print $7}')
    pct=$(( total ? used * 100 / total : 0 )); note=""; [ $pct -gt 80 ] && note=" UNDER MEMORY PRESSURE (swap ${pct}%)"
    step "start (try $attempt): $name | swap ${pct}% avail ${avail}MB load $(cut -d' ' -f1 /proc/loadavg)$note"
    ( cd $dir && exec setsid env "${envs[@]}" timeout $limit "$@" ) >> $out 2> $OUT/stderr_last.txt &
    child=$!
    while kill -0 $child 2>/dev/null; do
      sleep 5
      drive_ok || { kill -TERM -- -$child 2>/dev/null; sleep 2; kill -KILL -- -$child 2>/dev/null; drive_lost "$name"; }
    done
    wait $child; rc=$?
    if [ $rc = 0 ]; then step "completed: $name | try $attempt, $note"; return 0; fi
    step "run failed rc=$rc (try $attempt): $name | $(tail -3 $OUT/stderr_last.txt | tr '\n' ' ' | cut -c1-300)"
    drive_ok || drive_lost "$name"
    sleep 10
  done
  step "FAILED: $name | after 2 tries, rc=$rc"; return 1; }
FAST="env PYTHONUNBUFFERED=1"
new() { n=$(echo "$*" | tr -d '-' | tr ' ' '_'); mode=partitioned; ctx=16384; prev=""; for x in "$@"; do [ "$prev" = "--mode" ] && mode=$x; [ "$prev" = "--n-ctx" ] && ctx=$x; prev=$x; done
  item "new $*" $NEWT 18000 $OUT/console.txt MALABR_BENCH_CALIBRATION=$CALDIR/${mode}_${ctx}x8_t3.json -- $PY tests/campaign.py --out $OUT/raw --system malabr --tag new "$@"; }
old() { item "old $*" $OLDT 18000 $OUT/console.txt X=1 -- $PY tests/campaign.py --out $OUT/raw --system malabr --tag old "$@"; }
ll() { item "llama $*" $OLDT 18000 $OUT/console.txt X=1 -- $PY tests/campaign.py --out $OUT/raw --system llamacpp "$@"; }
ol() { item "ollama $*" $OLDT 18000 $OUT/console.txt X=1 -- $PY tests/campaign.py --out $OUT/raw --system ollama "$@"; }
ch() { n=$1; shift; item "chrome $n" $A/scripts 5400 $OUT/chrome/$n.txt X=1 -- $PY "$@"; }

FIXB=$(git -C $R rev-parse --short=12 design-fix-2026-11-06) || exit 2
fix() { mode=partitioned; ctx=16384; prev=""; for x in "$@"; do [ "$prev" = "--mode" ] && mode=$x; [ "$prev" = "--n-ctx" ] && ctx=$x; prev=$x; done
  item "fix $*" $NEWT 18000 $OUT/console.txt MALABR_BENCH_CALIBRATION=$CALDIR/${mode}_${ctx}x8_t3.json -- $PY tests/campaign.py --out $OUT/raw_fix --system malabr --tag fix "$@"; }
step "FIX RERUN 2 start: build $FIXB; W2, R1, W5, W4 partitioned, same harness and calibrations as the W1 and W3 reruns"
fix --mode partitioned --workload W2
fix --mode shared --workload W2
report; save "fix rerun: W2"
fix --mode partitioned --workload R1
fix --mode shared --workload R1
report; save "fix rerun: R1"
fix --mode partitioned --workload W5
fix --mode shared --workload W5
report; save "fix rerun: W5"
fix --mode partitioned --workload W4
report; save "fix rerun: W4 partitioned"
step "FIX RERUN 2 finished"
