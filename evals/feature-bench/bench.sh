#!/bin/bash
# bench.sh <machine name> <work root> [rounds] [cases] [conditions]: the runs PROTOCOL.md describes,
# one at a time. Run from the repository root with PRAT's virtual environment active (or PY set to
# its python). Each run's record lands in
# evals/feature-bench/results/<machine>/<case>-<condition>-<round>.json.
# Conditions: plain, plainlow (plain at effort low, see ADDENDUM-1.md), single, single+pack, swarm.
set -u
MACHINE=$1; WORK=$2; ROUNDS=${3:-5}; CASES=${4:-"giftcards megastore coverage"}; ONLY=${5:-}
PY=${PY:-python}
# macOS has no timeout unless coreutils is installed.
LIMIT=$(command -v timeout || command -v gtimeout || true); [ -n "$LIMIT" ] && LIMIT="$LIMIT 1500"
OUT=evals/feature-bench/results/$MACHINE
mkdir -p "$OUT" "$WORK"

one() { # one <case name> <case dir> <condition> <round>
  local name=$1 dir=$2 condition=$3 round=$4 mode=$3 label=$1-$3-$4 prep="" effort=""
  [ -n "$ONLY" ] && [[ " $ONLY " != *" $condition "* ]] && return
  [ -f "$OUT/$label.json" ] && { echo "have $label"; return; }
  [ "$condition" = "single+pack" ] && { mode=single; prep=1; }
  [ "$condition" = "plainlow" ] && { mode=plain; effort=low; }
  echo "$(date +%H:%M:%S) start $label"
  PROOF_PREP=$prep PROOF_PLAIN_EFFORT=$effort $LIMIT "$PY" evals/feature-bench/run.py "$dir" "$mode" "$WORK/$label" > "$WORK/$label.log" 2>&1
  local status=$?
  if [ -f "$WORK/$label/result.json" ]; then cp "$WORK/$label/result.json" "$OUT/$label.json"
  else printf '{"mode": "%s", "final_status": "no result (exit %s)", "seconds": null, "hidden_check": {"passed": false}}\n' "$mode" "$status" > "$OUT/$label.json"; fi
  echo "$(date +%H:%M:%S) done  $label: $(grep -o '"seconds": [0-9.a-z]*' "$OUT/$label.json" | head -1) $(grep -o '"passed": [a-z]*' "$OUT/$label.json" | head -1)"
}

for round in $(seq 1 "$ROUNDS"); do
  for case in $CASES; do
    case $case in
      giftcards) for c in plain plainlow single; do one giftcards evals/swarm/giftcards $c $round; done ;;
      megastore) for c in plain plainlow single swarm; do one megastore evals/swarm-xl/megastore $c $round; done ;;
      coverage)  for c in plain plainlow single single+pack; do one coverage evals/packbench/coverage-stale-files $c $round; done ;;
    esac
  done
done
echo "BENCH-DONE $MACHINE"
