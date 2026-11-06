#!/bin/bash
# DIAGNOSTIC runs for the W3 shared regression, one at a time. Not results.
D=/tmp/claude-1000/-media-chaitu-chaitanya-malabr-src/08315610-55c0-4645-a355-34f92fc3622c/scratchpad/diag
A=/media/chaitu/chaitanya/malabr/src/addition_malabr
NEWT=$A/mserver; OLDT=$D/../camp/oldtree/addition_malabr/mserver
CAL=$(ls -d $A/design_doc/bench_runs_2026-11/calibrations/*_35e90668b6d4)/shared_16384x8_t3.json
PY=/home/chaitu/Desktop/vscode/malabr/bin/python
OUT=$D/out; mkdir -p $OUT
OLDCAL=$OLDT/tests/bench_calibration_shared_16384x8_t3.json
run() { # label tree hyp extra...
  l=$1; t=$2; h=$3; shift 3
  [ -e $OUT/$l.json ] && return 0
  [ -e $D/STOP ] && exit 0
  echo "[$(date '+%F %T')] start $l swap $(free -m | awk 'NR==3{print $3}')MB load $(cut -d' ' -f1 /proc/loadavg)" >> $D/log.txt
  ( cd $t && MALABR_BENCH_CALIBRATION=$CAL timeout 1800 $PY $D/diag.py $l $h $OUT --old-cal $OLDCAL "$@" ) >> $D/console.txt 2>> $D/stderr.txt
  echo "[$(date '+%F %T')] end $l rc=$?" >> $D/log.txt
}
run new_baseline $NEWT baseline
run a_share_off  $NEWT a
run b_old_cal    $NEWT b
run c_limit_4096 $NEWT c
run d_shortest   $NEWT d
run small_pool_new_g2 $NEWT baseline --greedy 2 --n-ctx 4096 --page 900
echo "[$(date '+%F %T')] end new set" >> $D/log.txt
( cd $OLDT && env -u MALABR_BENCH_CALIBRATION timeout 1800 $PY $D/diag.py old_baseline baseline $OUT ) >> $D/console.txt 2>> $D/stderr.txt
echo "[$(date '+%F %T')] end old_baseline rc=$?" >> $D/log.txt
echo "[$(date '+%F %T')] DONE" >> $D/log.txt
