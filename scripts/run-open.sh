#!/usr/bin/env bash
#
# Run the single "app menu quit" smoke test. This is the local
# developer check for the Windows open/quit crash, so unlike the full
# functional suite it has NO macOS host gate and runs on a Mac.
#
# Usage: run-open.sh [extra pytest args, e.g. -x -k something]

set -uo pipefail

if ! SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  echo "ERROR: could not resolve the script directory" >&2
  exit 1
fi
readonly SCRIPT_DIR

if ! ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"; then
  echo "ERROR: could not resolve the repository root" >&2
  exit 1
fi
readonly ROOT_DIR

readonly VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

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
# Resolve the Python interpreter to run pytest with, preferring the
# project .venv. CI installs with `uv --system` and has no .venv, so
# fall back to `python` from PATH.
# Globals:
#   VENV_PYTHON
# Arguments:
#   None
# Outputs:
#   Writes the resolved python command to stdout
# Returns:
#   0 if python was found, 1 if not
#######################################
resolve_python() {
  if [[ -x "${VENV_PYTHON}" ]]; then
    echo "${VENV_PYTHON}"
    return 0
  fi

  local path_python
  if path_python="$(command -v python)"; then
    echo "${path_python}"
    return 0
  fi

  return 1
}

#######################################
# Run the app menu/quit smoke test from the repository root.
# Globals:
#   ROOT_DIR
# Arguments:
#   Extra arguments forwarded to pytest
# Returns:
#   pytest's exit status (0 = pass); 1 if python could not be located
#######################################
main() {
  if ! cd "${ROOT_DIR}"; then
    err "could not enter ${ROOT_DIR}"
    return 1
  fi

  local py
  if ! py="$(resolve_python)"; then
    err "python not found. Install the dev dependencies with:
  uv pip install -e '.[dev]'
  (or: python -m venv .venv && pip install -e '.[dev]')"
    return 1
  fi

  "${py}" -m pytest tests/functional/test_app_menu_quit.py --no-cov "$@"
  local exit_code=$?
  return "${exit_code}"
}

main "$@"
