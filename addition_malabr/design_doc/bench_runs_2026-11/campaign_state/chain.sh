#!/bin/bash
# Waits for overnight.sh (pid 14360) to exit, then starts the trimmed list once.
S=/tmp/claude-1000/-media-chaitu-chaitanya-malabr-src/08315610-55c0-4645-a355-34f92fc3622c/scratchpad/camp; L=/media/chaitu/chaitanya/malabr/src/addition_malabr/design_doc/bench_runs_2026-11; LOG=$L/progress.log
tail --pid=14360 -f /dev/null
sleep 2
if tail -n 3 $LOG | grep -q 'STOPPED cleanly before'; then
  rm -f $S/STOP
  echo "[$(date '+%F %T')] chain: overnight.sh exited cleanly, starting trimmed list" >> $LOG
  exec $S/overnight2.sh
else
  echo "[$(date '+%F %T')] chain: overnight.sh did not stop cleanly, trimmed list NOT started" >> $LOG
fi
