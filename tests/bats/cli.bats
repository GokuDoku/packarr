#!/usr/bin/env bats
# CLI behaviors that need no network: argument parsing, config discovery/validation, and the commands
# that only ever touch local state (status/plan/approve bounds-checking).

load 'helpers'

@test "--help exits 0 and lists the subcommands" {
  run pk --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"init"* ]]
  [[ "$output" == *"check"* ]]
  [[ "$output" == *"web"* ]]
}

@test "--version exits 0 and prints a dotted version number" {
  run pk --version
  [ "$status" -eq 0 ]
  [[ "$output" =~ [0-9]+\.[0-9]+\.[0-9]+ ]]
}

@test "an unknown subcommand fails cleanly" {
  run pk not-a-real-command
  [ "$status" -ne 0 ]
}

@test "init writes a config that itself looks like YAML" {
  run pk init
  [ "$status" -eq 0 ]
  [ -f packarr.yml ]
  grep -q "^sonarr:" packarr.yml
}

@test "init refuses to overwrite an existing config without --force" {
  pk init
  run pk init
  [ "$status" -ne 0 ]
  [[ "$output" == *"exists"* ]]
}

@test "init --force overwrites an existing config" {
  pk init
  echo "# a local edit" >> packarr.yml
  run pk init --force
  [ "$status" -eq 0 ]
  run grep -q "# a local edit" packarr.yml
  [ "$status" -ne 0 ]
}

@test "no config anywhere fails with a clear message, not a traceback" {
  run pk --config /no/such/file.yml status
  [ "$status" -ne 0 ]
  [[ "$output" == *"no config found"* ]]
  [[ "$output" != *"Traceback"* ]]
}

@test "an invalid download_client is rejected with a clear message" {
  write_config "download_client: deluge"
  run pk --config packarr.yml check
  [ "$status" -ne 0 ]
  [[ "$output" == *"download_client"* ]]
  [[ "$output" == *"deluge"* ]]
}

@test "a config missing sonarr credentials is rejected" {
  printf 'sonarr: { url: "", api_key: "" }\n' > packarr.yml
  run pk --config packarr.yml status
  [ "$status" -ne 0 ]
  [[ "$output" == *"sonarr.url"* ]]
}

@test "PACKARR_CONFIG is honored when --config is not given" {
  write_config
  PACKARR_CONFIG="$TEST_DIR/packarr.yml" run pk status
  [ "$status" -eq 0 ]
  [[ "$output" == *"jobs 0"* ]]
}

@test "status on a fresh state reports zero jobs, no network needed" {
  write_config
  run pk --config packarr.yml status
  [ "$status" -eq 0 ]
  [[ "$output" == *"free "* ]]
  [[ "$output" == *"jobs 0"* ]]
}

@test "status is safe to run repeatedly against the same state" {
  write_config
  run pk --config packarr.yml status
  [ "$status" -eq 0 ]
  run pk --config packarr.yml status
  [ "$status" -eq 0 ]
  [[ "$output" == *"jobs 0"* ]]
}

@test "plan on a job index that doesn't exist fails cleanly, not with a traceback" {
  write_config
  run pk --config packarr.yml plan 0
  [ "$status" -ne 0 ]
  [[ "$output" == *"no job #0"* ]]
  [[ "$output" != *"Traceback"* ]]
  [[ "$output" != *"IndexError"* ]]
}

@test "approve on a job index that doesn't exist fails cleanly, not with a traceback" {
  write_config
  run pk --config packarr.yml approve 0
  [ "$status" -ne 0 ]
  [[ "$output" == *"no job #0"* ]]
  [[ "$output" != *"Traceback"* ]]
}

@test "adopt's torrent argument is not restricted to integers (qBittorrent uses hashes)" {
  # A true argparse-level rejection always exits 2; anything else means argparse accepted the value and
  # the failure (if any) happened later, past argument parsing - which is all this checks, deliberately
  # not touching the network to get there.
  write_config
  run pk --config packarr.yml adopt abcdef0123456789abcdef0123456789abcdef01 --series 1
  [ "$status" -ne 2 ]
  [[ "$output" != *"invalid int() value"* ]]
}

@test "adopt still requires --series" {
  run pk adopt 12345
  [ "$status" -eq 2 ]
  [[ "$output" == *"--series"* ]]
}

@test "an uncaught error from a command still exits non-zero without a raw traceback" {
  write_config
  run pk --config packarr.yml adopt abcdef0123456789abcdef0123456789abcdef01 --series 1
  [ "$status" -ne 0 ]
  [[ "$output" != *"Traceback"* ]]
}

@test "audit --help exits 0 and documents --series" {
  run pk audit --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--series"* ]]
}

@test "audit against an unreachable Sonarr fails cleanly, not with a traceback" {
  write_config
  run pk --config packarr.yml audit
  [ "$status" -ne 0 ]
  [[ "$output" != *"Traceback"* ]]
}
