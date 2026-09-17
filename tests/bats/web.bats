#!/usr/bin/env bats
# `packarr web` end to end: the process starts, serves real HTTP over loopback, and shuts down cleanly.
# Loopback only - no external network - so this stays exactly as network-free as the rest of the suite.
# Requires curl.

load 'helpers'

WEB_PORT=18787

start_web() {
  write_config "web: { listen: 127.0.0.1:$WEB_PORT }"
  # Deliberately not going through pk() here: backgrounding that function would capture the wrapper
  # subshell's PID in $!, not the actual server process, so `kill "$WEB_PID"` later wouldn't reliably
  # reach it. Resolve the real command once and background that directly instead.
  if command -v packarr >/dev/null 2>&1; then
    packarr --config "$TEST_DIR/packarr.yml" web &
  else
    PYTHONPATH="$PACKARR_ROOT" python3 -m packarr.cli --config "$TEST_DIR/packarr.yml" web &
  fi
  WEB_PID=$!
  for _ in $(seq 1 50); do
    if curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT/"; then
      return 0
    fi
    sleep 0.1
  done
  echo "packarr web never came up on :$WEB_PORT" >&2
  return 1
}

teardown() {
  if [ -n "${WEB_PID:-}" ]; then
    kill "$WEB_PID" 2>/dev/null
    wait "$WEB_PID" 2>/dev/null
  fi
  cd "$PACKARR_ROOT" || true
  rm -rf "$TEST_DIR"
}

@test "packarr web serves the held-jobs list page" {
  start_web
  run curl -s "http://127.0.0.1:$WEB_PORT/"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Held jobs"* ]]
}

@test "packarr web reports no held jobs on a fresh state" {
  start_web
  run curl -s "http://127.0.0.1:$WEB_PORT/"
  [[ "$output" == *"No held jobs"* ]]
}

@test "packarr web returns 404 for a job index that doesn't exist" {
  start_web
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/job/0"
  [ "$status" -eq 0 ]
  [ "$output" -eq 404 ]
}

@test "packarr web returns 404 for an unknown path" {
  start_web
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/nope"
  [ "$output" -eq 404 ]
}

@test "packarr web's approve endpoint rejects GET (it's a POST-only action)" {
  start_web
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/job/0/approve"
  [ "$output" -eq 404 ]  # do_GET has no route for this path; only do_POST does
}

@test "packarr web shuts down cleanly on SIGTERM" {
  start_web
  kill "$WEB_PID"
  for _ in $(seq 1 50); do
    kill -0 "$WEB_PID" 2>/dev/null || break
    sleep 0.1
  done
  run kill -0 "$WEB_PID"
  [ "$status" -ne 0 ]
  WEB_PID=""  # already gone; don't let teardown try again
}
