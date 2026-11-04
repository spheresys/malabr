#!/bin/bash
# The baseline campaign, strictly one benchmark at a time. MALABR NEW is not run.
PY=/home/chaitu/Desktop/vscode/malabr/bin/python
OUT=/media/chaitu/chaitanya/malabr/src/addition_malabr/design_doc/bench_runs_2026-11; mkdir -p $OUT/raw $OUT/chrome
OLD=/tmp/claude-1000/-media-chaitu-chaitanya-malabr-src/08315610-55c0-4645-a355-34f92fc3622c/scratchpad/camp/oldtree/addition_malabr/mserver
LOG=$OUT/progress.log
step() { echo "[$(date '+%F %T')] $*" >> $LOG; }
c() { step "$*"; ( cd $OLD && timeout 18000 $PY tests/campaign.py --out $OUT/raw "$@" ) >> $OUT/console.txt 2>$OUT/stderr_last.txt; rc=$?; [ $rc != 0 ] && { step "FAILED rc=$rc: $*"; tail -5 $OUT/stderr_last.txt >> $LOG; }; }
old() { c --system malabr --tag old "$@"; }
ll() { c --system llamacpp "$@"; }
ol() { c --system ollama "$@"; }
ch() { name=$1; shift; step "chrome $name"; ( cd /media/chaitu/chaitanya/malabr/src/addition_malabr/scripts && timeout 5400 $PY "$@" ) > $OUT/chrome/$name.txt 2>&1 || step "chrome $name exit $?"; }
step "git $(git -C /media/chaitu/chaitanya/malabr/src rev-parse --short HEAD); OLD engine = 610c233, its tests and calibration.py from HEAD"
# The existing scheduling benchmarks on OLD, measured calibration: continuity with the published numbers.
bb() { n=$1; shift; step "old bench $n"; ( cd $OLD && timeout 3600 $PY tests/$n.py "$@" --json $OUT/raw/old_bench_$n.json ) > $OUT/raw/old_bench_$n.txt 2>&1; }
bb baseline_bench --tabs 8; bb pipeline_bench; bb relative_repeat; bb cancellation_bench
for w in W1 W2 W5; do
  old --mode partitioned --workload $w
  old --mode shared --workload $w
  ll --mode partitioned --workload $w
  ll --mode shared --workload $w
  ol --workload $w
done
# W4: n=20, except where one trial fills 8k-16k tokens (about 6 min each): n=8.
old --mode partitioned --workload W4
old --mode shared --workload W4 --trials 8
ll --mode partitioned --workload W4
ll --mode shared --workload W4 --trials 8
ol --workload W4
ll --mode partitioned --workload R1; ll --mode shared --workload R1; ol --workload R1
step "chrome start"
for i in 0 1 2 3 4 5; do ch relative_8tabs_$i chrome_relative_bench.py --tabs 8 --json $OUT/chrome/relative_8tabs_$i.json; done
for i in 0 1 2 3 4 5; do ch relative_4tabs_$i chrome_relative_bench.py --tabs 4 --json $OUT/chrome/relative_4tabs_$i.json; done
for i in 0 1 2 3 4 5; do ch relative_8tabs_stagger_$i chrome_relative_bench.py --tabs 8 --stagger 2 --json $OUT/chrome/relative_8tabs_stagger_$i.json; done
ch cancel_8tabs chrome_rq4_cancel.py --tabs 8 --runs 5
ch memory chrome_rq2_memory.py --tabs 6 --runs 5
for i in 1 2 3 4 5; do ch rss_$i chrome_rq2_rss.py --sessions 6 --json $OUT/chrome/rss_$i.json; done
ch isolation chrome_rq1_isolation.py
step "W3 start"
for g in 0 1 2; do
  old --mode partitioned --workload W3 --greedy $g
  old --mode shared --workload W3 --greedy $g
  ll --mode partitioned --workload W3 --greedy $g
  ll --mode shared --workload W3 --greedy $g
  ol --workload W3 --greedy $g
done
old --mode shared --workload W3 --greedy 2 --guard off
old --mode shared --workload W3 --greedy 2 --n-ctx 4096 --page 900
old --mode shared --workload W3 --greedy 2 --guard off --n-ctx 4096 --page 900
old --mode partitioned --workload W3 --greedy 2 --n-ctx 4096 --page 900
ll --mode partitioned --workload W3 --greedy 2 --n-ctx 4096 --page 900
ll --mode shared --workload W3 --greedy 2 --n-ctx 4096 --page 900
ol --workload W3 --greedy 2 --n-ctx 4096 --page 900
step "campaign done"
