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
    for p in $(pgrep -f "chrome-headless-shell"); do echo "-- chrome $p $(tr '\0' ' ' < /proc/$p/cmdline | grep -o -- '--type=[a-z-]*')"; for t in /proc/$p/task/*; do echo "   $(cat $t/comm) $(grep State $t/status | cut -f2) $(sudo cat $t/wchan)"; done; done
    pkill -9 -f "python -m pytest"; pkill -9 -f chrome-headless-shell
    echo "RESULT $label hang"
  else
    wait $pid; echo "RESULT $label rc=$?"
  fi
  echo "::endgroup::"
}
uv pip install py-spy >/dev/null 2>&1 || true
echo "::group::durations"
timeout 1500 uv run python -m pytest -q --capture=sys -p no:cacheprovider --disable-plugin-autoload tests/browser --durations=40 2>&1 | tail -60
echo "::endgroup::"
for i in $(seq 1 40); do run "fixed-$i" 30 "$TEST"; done
sed -i '/^ *expect(heading).to_be_focused()$/d' tests/browser/test_react_platform_artifact.py
grep -c "expect(heading).to_be_focused()" tests/browser/test_react_platform_artifact.py
for i in $(seq 1 40); do run "apponly-$i" 30 "$TEST"; done
