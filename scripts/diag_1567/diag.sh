#!/usr/bin/env bash
# Temporary diagnostic for #1567 (residual rail-close stall). Not for merge.
set -u
D=scripts/diag_1567
TEST="tests/browser/test_react_platform_artifact.py::test_closing_the_rail_keeps_back_to_works_in_view"
declare -A HANGS RUNS
run() {  # arm, i, cmd...
  local arm=$1 i=$2; shift 2
  "$@" > /tmp/run.log 2>&1 &
  local pid=$! w=0
  while kill -0 $pid 2>/dev/null && [ $w -lt 40 ]; do sleep 1; w=$((w+1)); done
  RUNS[$arm]=$(( ${RUNS[$arm]:-0} + 1 ))
  if kill -0 $pid 2>/dev/null; then
    HANGS[$arm]=$(( ${HANGS[$arm]:-0} + 1 ))
    echo "RESULT $arm-$i HANG"; tail -n 15 /tmp/run.log | cut -c1-240
    pkill -9 -f "pytest -q" ; pkill -9 -f "repro_" ; pkill -9 -f chrome-headless-shell; sleep 1
  else
    wait $pid; local rc=$?
    echo "RESULT $arm-$i rc=$rc t=$w"
    [ $rc -ne 0 ] && tail -n 15 /tmp/run.log | cut -c1-240
  fi
}
arms() {  # suffix
  export DIAG_NOWAIT=1
  for i in $(seq 1 40); do run "app-$1" $i uv run --no-sync python -m pytest -q -p no:cacheprovider --disable-plugin-autoload "$TEST"; done
  for i in $(seq 1 80); do run "min-$1" $i uv run --no-sync python $D/repro_run.py; done
  for i in $(seq 1 40); do run "scan-$1" $i uv run --no-sync python $D/repro_scan.py; done
}
uv run --no-sync python -c 'from importlib.metadata import version; print("playwright", version("playwright"))'
arms pinned
uv pip install playwright==1.64.1
uv run --no-sync playwright install chromium-headless-shell
uv run --no-sync python -c 'from importlib.metadata import version; print("playwright", version("playwright"))'
arms new
echo "== SUMMARY"
for a in "${!RUNS[@]}"; do echo "$a: ${HANGS[$a]:-0} hangs / ${RUNS[$a]}"; done | sort
