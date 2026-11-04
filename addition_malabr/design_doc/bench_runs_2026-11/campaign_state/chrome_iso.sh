#!/bin/bash
PY=/home/chaitu/Desktop/vscode/malabr/bin/python
R=/media/chaitu/chaitanya/malabr/src/addition_malabr
OUT=$R/design_doc/bench_runs_2026-11; LOG=$OUT/progress.log
step() { echo "[$(date '+%F %T')] $*" >> $LOG; }
tail --pid=217883 -f /dev/null
step "completed: chrome rss_2 (run under memory pressure, swap full)"
step "chrome isolation"; ( cd $R/scripts && timeout 5400 $PY chrome_rq1_isolation.py ) > $OUT/chrome/isolation.txt 2>&1; rc=$?
[ $rc = 0 ] && step "completed: chrome isolation" || step "completed with exit $rc: chrome isolation"
