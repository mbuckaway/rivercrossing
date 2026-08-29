#!/usr/bin/env bash
#
# Thin wrapper around `nox -s lint typecheck importlint ids_drift css_drift`
# (CI stage 1 - static checks: ruff, mypy strict, import-linter, ids
# drift, vendored CSS drift). nox is the single source of truth for what
# CI runs; this script only locates nox and forwards arguments to it.
#
# See noxfile.py and design/docs-md/spec.md §14.
#
# Usage: run_lint.sh [extra nox args...]

set -uo pipefail

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
# Run CI stage 1 (static checks) via nox.
# Globals:
#   None
# Arguments:
#   Extra arguments forwarded to nox
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
  "${nox_bin}" -s lint typecheck importlint ids_drift css_drift -- "$@"
  exit_code=$?
  return "${exit_code}"
}

main "$@"
