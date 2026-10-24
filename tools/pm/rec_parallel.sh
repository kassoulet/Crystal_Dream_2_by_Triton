#!/bin/bash
# Record the main run as 20-second segments in parallel, each continuing from a VM snapshot (SNAP_EVERY=20 in an earlier plain run) so the vector log has warmed up:
# segment k runs from snapshot s(20(k-1)) and records [20k, 20(k+1)).  Segments 0 and 1 start at boot (the snapshot at t=0 predates the setup keys).
#   usage: rec_parallel.sh SNAPDIR OUTDIR END_SECONDS [JOBS]        output: OUTDIR/seg_00 ... seg_NN, in time order
SNAPS=$(realpath "$1"); OUT=$2; END=$3; JOBS=${4:-10}; HERE=$(dirname "$(realpath "$0")"); mkdir -p "$OUT"
N=$(( (END + 19) / 20 ))
seg() {
  k=$1; from=$(( k * 20 )); to=$(( from + 20 )); [ $to -gt $END ] && to=$END
  d="$OUT/seg_$(printf %02d $k)"; [ -f "$d/done" ] && return
  rm -rf "$d"; if [ $k -le 1 ]; then unset SNAPSHOT; else export SNAPSHOT="$SNAPS/s$(printf %04d $(( (k - 1) * 20 ))).bin"; fi
  REC_FROM=$from python3 -u "$HERE/record_full.py" "$d" $to > "$d.log" 2>&1 && grep -q 'all done' "$d.log" && touch "$d/done"
}
export -f seg; export SNAPS OUT END HERE
seq 0 $(( N - 1 )) | xargs -P "$JOBS" -I{} bash -c 'seg {}'
