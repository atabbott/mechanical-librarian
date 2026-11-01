#!/usr/bin/env bash
# Temporary diagnostic for the rail-closing browser hang. Not for merge.
set -u
TEST="tests/browser/test_react_platform_artifact.py::test_closing_the_rail_keeps_back_to_works_in_view"
run() {  # label, seconds, pytest args...
  local label=$1 limit=$2; shift 2
  echo "::group::$label"
  uv run python -m pytest -q -s --capture=sys -p no:cacheprovider --disable-plugin-autoload "$@" &
  local pid=$! waited=0
  while kill -0 $pid 2>/dev/null && [ $waited -lt $limit ]; do sleep 1; waited=$((waited+1)); done
  if kill -0 $pid 2>/dev/null; then
    echo "HANG in $label after ${limit}s"
    ps -ef --forest | grep -E "chrome|playwright|node|pytest" | grep -v grep | cut -c1-220
    for p in $(pgrep -f "python -m pytest"); do sudo .venv/bin/py-spy dump --pid $p || true; done
    for p in $(pgrep -f "chrome-headless-shell" | head -3); do echo "-- chrome $p"; sudo cat /proc/$p/status | grep -E "State|Threads"; sudo cat /proc/$p/wchan; echo; done
    pkill -9 -f "python -m pytest"; pkill -9 -f chrome-headless-shell
    echo "RESULT $label hang"
  else
    wait $pid; echo "RESULT $label rc=$?"
  fi
  echo "::endgroup::"
}
uv pip install py-spy >/dev/null 2>&1 || true
run "file-durations" 600 tests/browser/test_react_platform_artifact.py tests/browser/test_review_browser.py --durations=25
for i in $(seq 1 70); do run "single-$i" 90 "$TEST"; done
grep -c . /dev/null
