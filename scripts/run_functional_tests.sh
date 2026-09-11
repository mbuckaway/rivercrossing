#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329
# Everything below the early refusal is deliberately-kept dead code awaiting
# the functional-suite rewrite; the unreachability is intended, not a defect.
#
# Thin wrapper around `nox -s functional` (CI stage 3 - drives real wx
# windows). nox is the single source of truth for what CI runs; this
# script only locates nox and forwards arguments to it.
#
# Requires a real desktop session - this cannot run under a virtual
# display (no Xvfb). See design/docs-md/spec.md §14 ("both runners
# have a real desktop session, so wx windows open without a virtual
# display") and noxfile.py.
#
# Usage: run_functional_tests.sh [extra pytest args, e.g. -k foo -x]
#
# DISABLED (2026-09-10): the functional suite is broken and is being
# rewritten from scratch. This wrapper refuses to run until then.

set -uo pipefail

echo "FUNCTIONAL TESTS ARE BROKEN. DO NOT RUN THEM" >&2
exit 1

if ! SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  echo "ERROR: could not resolve script directory" >&2
  exit 1
fi
readonly SCRIPT_DIR

if ! ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"; then
  echo "ERROR: could not resolve repository root directory" >&2
  exit 1
fi
readonly ROOT_DIR

readonly VENV_NOX="${ROOT_DIR}/.venv/bin/nox"

#######################################
# Write a timestamped error message to stderr.
# Arguments:
#   Message text
# Outputs:
#   Writes the message to stderr
#######################################
err() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')]: $*" >&2
}

#######################################
# Resolve the nox executable, preferring the project .venv.
# Globals:
#   VENV_NOX
# Arguments:
#   None
# Outputs:
#   Writes the resolved nox command path to stdout
# Returns:
#   0 if nox was found, 1 if not
#######################################
resolve_nox() {
  if [[ -x "${VENV_NOX}" ]]; then
    echo "${VENV_NOX}"
    return 0
  fi

  local path_nox
  if path_nox="$(command -v nox)"; then
    echo "${path_nox}"
    return 0
  fi

  return 1
}

#######################################
# Run CI stage 3 (functional UI tests) via nox.
# Globals:
#   None
# Arguments:
#   Extra arguments forwarded to pytest
# Returns:
#   nox's own exit status; 1 if nox could not be located
#######################################
main() {
  local nox_bin
  if ! nox_bin="$(resolve_nox)"; then
    local msg
    msg="nox not found. Install it with:
  uv pip install -e '.[dev]'
  (or: python -m venv .venv && pip install -e '.[dev]')"
    err "${msg}"
    return 1
  fi

  local exit_code
  "${nox_bin}" -s functional -- "$@"
  exit_code=$?
  return "${exit_code}"
}

main "$@"
