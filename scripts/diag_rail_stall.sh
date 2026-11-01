#!/usr/bin/env bash
# Temporary diagnostic for the residual rail-close stall (#1567). Not for merge.
set -u
TEST="tests/browser/test_react_platform_artifact.py::test_closing_the_rail_keeps_back_to_works_in_view"
run() {  # label, seconds, pytest args...
  local label=$1 limit=$2; shift 2
  echo "::group::$label"
  uv run python -m pytest -q -s --capture=sys -p no:cacheprovider --disable-plugin-autoload "$@" > /tmp/run.log 2>&1 &
  local pid=$! waited=0
  while kill -0 $pid 2>/dev/null && [ $waited -lt $limit ]; do sleep 1; waited=$((waited+1)); done
  if kill -0 $pid 2>/dev/null; then
    echo "HANG in $label after ${limit}s"
    cut -c1-240 /tmp/run.log | tail -n 60
    pkill -9 -f "python -m pytest"; pkill -9 -f chrome-headless-shell
    echo "RESULT $label hang"
  else
    wait $pid; local rc=$?
    if [ "$label" != "${label%-1}" ] || [ $rc -ne 0 ]; then cut -c1-240 /tmp/run.log | tail -n 60; fi
    echo "RESULT $label rc=$rc"
  fi
  echo "::endgroup::"
}
LOG='for (const [target, name] of [[window, "scrollTo"], [window, "scrollBy"], [Element.prototype, "scrollIntoView"], [HTMLElement.prototype, "focus"]]) { const original = target[name]; target[name] = function (...args) { console.log("CALL " + name + " " + (this === window ? "window" : (this.tagName + "." + this.className)).slice(0, 60) + " y=" + Math.round(scrollY) + " t=" + Math.round(performance.now()) + " open=" + location.search.includes("attention=open")); return original.apply(this, args); }; }'
export DIAG_INIT="$LOG" DIAG_NOWAIT=1
for i in $(seq 1 40); do run "main-$i" 30 "$TEST"; done
git apply scripts/diag_candidate.patch
(cd frontend && ML_FRONTEND_OUT_DIR="$RUNNER_TEMP/react-assets-fix" npm run build >/dev/null 2>&1) || echo "BUILD FAILED"
export ML_REACT_ASSET_DIR="$RUNNER_TEMP/react-assets-fix"
for i in $(seq 1 120); do run "fix-$i" 30 "$TEST"; done
grep -c "RESULT" /dev/null >/dev/null
