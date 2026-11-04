#!/bin/bash
# The Chrome runs left when run.sh was detached (no W3 here). Starts nothing after 13:15.
PY=/home/chaitu/Desktop/vscode/malabr/bin/python
OUT=/media/chaitu/chaitanya/malabr/src/addition_malabr/design_doc/bench_runs_2026-11; LOG=$OUT/progress.log
step() { echo "[$(date '+%F %T')] $*" >> $LOG; }
too_late() { [ "$(date +%H%M)" -ge 1315 ]; }
tail --pid=217883 -f /dev/null
step "completed: chrome rss_2"
ch() { name=$1; shift
  if too_late; then step "NOT STARTED (past 13:15): chrome $name"; return; fi
  step "chrome $name"; ( cd /media/chaitu/chaitanya/malabr/src/addition_malabr/scripts && timeout 5400 $PY "$@" ) > $OUT/chrome/$name.txt 2>&1; rc=$?
  [ $rc = 0 ] && step "completed: chrome $name" || step "completed with exit $rc: chrome $name"; }
for i in 3 4 5; do ch rss_$i chrome_rq2_rss.py --sessions 6 --json $OUT/chrome/rss_$i.json; done
ch isolation chrome_rq1_isolation.py
step "Chrome stage finished"
