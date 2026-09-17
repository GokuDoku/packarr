# Shared setup for the bats suite. `load 'helpers'` from a .bats file in this directory.
#
# Everything here runs with no network and no live Sonarr/download client, same promise the pytest suite
# already keeps - these tests exercise the packaged `packarr` CLI end to end (argument parsing, config
# validation, local-state commands, the web UI's HTTP behavior), which unit tests never touch directly.

PACKARR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Prefer an installed console script (what CI has after `pip install -e .`); fall back to running the
# package straight out of the repo so this suite also works untouched in a plain checkout.
pk() {
  if command -v packarr >/dev/null 2>&1; then
    packarr "$@"
  else
    PYTHONPATH="$PACKARR_ROOT" python3 -m packarr.cli "$@"
  fi
}

setup() {
  TEST_DIR="$(mktemp -d)"
  cd "$TEST_DIR" || exit 1
}

teardown() {
  cd "$PACKARR_ROOT" || true
  rm -rf "$TEST_DIR"
}

# A config that's valid enough to load (so tests can reach status/plan/approve/web) but never actually
# reaches Sonarr: .invalid is a reserved TLD (RFC 2606) guaranteed to fail DNS resolution immediately
# rather than hang, and nothing in this suite exercises a code path that would call it. Extra YAML lines
# can be appended via $1.
write_config() {
  mkdir -p "$TEST_DIR/state"
  cat > "$TEST_DIR/packarr.yml" <<EOF
sonarr: { url: http://sonarr.invalid:8989, api_key: fake }
paths: { state_dir: $TEST_DIR/state, downloads_local: $TEST_DIR, downloads_sonarr: $TEST_DIR }
${1:-}
EOF
}
