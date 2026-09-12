#!/usr/bin/env bash
#
# Run the RiverCrossing app from this checkout -- a source tree or a
# git worktree -- so local changes can be exercised before CI.
#
# The app resolves its own per-user locations itself (platformdirs,
# literal "RiverCrossing"), so this runs with the SAME database,
# settings, export and log paths as an installed bundle. The one
# override, RIVERCROSSING_DB_PATH, is cleared here to keep that parity.
#
# Usage: run.sh [arguments forwarded to the app]

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
# Ensure the project .venv exists, creating and populating it with uv on
# first use so a fresh checkout needs no manual setup.
# Globals:
#   VENV_PYTHON
# Returns:
#   0 when the venv python is present, non-zero otherwise
#######################################
ensure_venv() {
  if [[ -x "${VENV_PYTHON}" ]]; then
    return 0
  fi

  if ! command -v uv >/dev/null 2>&1; then
    err "no .venv and uv is not installed. Create one with:
  python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    return 1
  fi

  echo "creating .venv (first run) ..." >&2
  if ! uv venv .venv; then
    err "uv venv failed"
    return 1
  fi
  if ! uv pip install --python "${VENV_PYTHON}" -e '.[dev]'; then
    err "installing rivercrossing failed"
    return 1
  fi

  return 0
}

#######################################
# Print the per-user locations the app will read and write, so they can
# be checked against an installed bundle (identical by construction).
# Globals:
#   VENV_PYTHON
# Returns:
#   0 (the listed paths are informational)
#######################################
show_paths() {
  "${VENV_PYTHON}" - <<'PY'
from platformdirs import user_config_dir, user_data_dir

data = user_data_dir("RiverCrossing")
config = user_config_dir("RiverCrossing")
print(f"  database : {data}/rides.db")
print(f"  exports  : {data}/exports")
print(f"  settings : {config}/settings.json")
print(f"  logs     : {config}")
print("             rivercrossing-<YYYYMMDD-HHMMSS>.log (one per launch)")
PY
}

#######################################
# Run the app from the repository root with the installed-app paths.
# Globals:
#   ROOT_DIR
#   VENV_PYTHON
# Arguments:
#   Arguments forwarded to the app
# Returns:
#   the app's exit status
#######################################
main() {
  if ! cd "${ROOT_DIR}"; then
    err "could not enter ${ROOT_DIR}"
    return 1
  fi

  if ! ensure_venv; then
    return 1
  fi

  # Parity with an installed bundle, which never sets this variable:
  # use the per-user default database, not an inherited test override.
  unset RIVERCROSSING_DB_PATH

  echo "running rivercrossing from ${ROOT_DIR}, using:" >&2
  show_paths >&2

  "${VENV_PYTHON}" -m rivercrossing "$@"
  local exit_code=$?
  return "${exit_code}"
}

main "$@"
