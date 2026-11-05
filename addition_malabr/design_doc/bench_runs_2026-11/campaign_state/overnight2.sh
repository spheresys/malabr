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

step "RESUME with the trimmed list approved by the user: remaining runs of the 21 kept, 20 runs cut (see BENCHMARKS_2026-11.md)"
# ---- W3, 2 greedy tabs: OLD cut
ll --mode partitioned --workload W3 --greedy 2
ll --mode shared --workload W3 --greedy 2
ol --workload W3 --greedy 2
new --mode partitioned --workload W3 --greedy 2
new --mode shared --workload W3 --greedy 2
report; save "W3 greedy 2"
# ---- guard off: NEW shared only
new --mode shared --workload W3 --greedy 2 --guard off
report; save "W3 guard off"
# ---- small pool, guard on: OLD and shared guard-off cut
new --mode shared --workload W3 --greedy 2 --guard on --n-ctx 4096 --page 900
new --mode partitioned --workload W3 --greedy 2 --guard on --n-ctx 4096 --page 900
ll --mode partitioned --workload W3 --greedy 2 --n-ctx 4096 --page 900
ll --mode shared --workload W3 --greedy 2 --n-ctx 4096 --page 900
ol --workload W3 --greedy 2 --n-ctx 4096 --page 900
report; save "W3 small pool"
# ---- Chrome: rss_5, relative 4 tabs and stagger cut
sweep
for i in 3 4; do ch rss_$i chrome_rq2_rss.py --sessions 6 --json $OUT/chrome/rss_$i.json; done
report; save "Chrome rss_3-4"
for i in 0 1 2 3 4 5; do ch relative2_8tabs_$i chrome_relative_bench.py --tabs 8 --json $OUT/chrome/relative2_8tabs_$i.json; done
sweep; report; save "Chrome relative rerun, 8 tabs"
step "TRIMMED runs finished"
