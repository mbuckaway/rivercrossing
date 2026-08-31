#!/bin/bash
#
# Run RiverCrossing's functional test suite (23 real wx windows) inside
# a disposable Tart macOS VM cloned from the rivercrossing-func-template
# built by scripts/setup_functional_vm.sh. Each run clones the template
# (APFS copy-on-write -- seconds, not minutes), pushes the current
# worktree, runs pytest in the guest through the fresh-process rerun
# wrapper (tools/functional_rerun.py), pulls screenshots back, then
# deletes the clone. The clone's own WindowServer means a crashed run
# cannot foul the host desktop. Isolation contains crashes, it does not
# cure them. Local-dev only -- CI is unaffected.
#
# Exit code contract:
#   2   - tart is not installed
#   3   - the rivercrossing-func-template VM is missing; run
#         scripts/setup_functional_vm.sh first
#   124 - the run exceeded RIVERCROSSING_VM_TIMEOUT (default 1800
#         seconds) and was killed by the watchdog
#   *   - otherwise, the guest's pytest exit code is propagated as-is
#
# Parallelism: RIVERCROSSING_FUNCTIONAL_JOBS (default "auto") sets the
# xdist -n value. The E6-size suite saturates the 4-CPU clone at -n
# auto (measured 2026-08-29: 4-6 wx-churn segfaults per run), so local
# runs may set it to 2 for a deterministic pass.
#
# Usage: scripts/run_functional_tests_vm.sh

set -uo pipefail

readonly TEMPLATE_NAME="rivercrossing-func-template"
readonly CLONE_NAME="rivercrossing-func-$$"
readonly VM_SSH_KEY="${HOME}/.ssh/rivercrossing_vm_ed25519"
readonly VM_TIMEOUT="${RIVERCROSSING_VM_TIMEOUT:-1800}"

# Every clone is a fresh VM with a brand-new host key and no known-hosts
# entry worth keeping once the clone is deleted at the end of the run.
readonly SSH_OPTS=(-i "${VM_SSH_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
readonly SSH_CMD="ssh -i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

if ! SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
  echo "ERROR: could not resolve script directory" >&2
  exit 1
fi
readonly SCRIPT_DIR

if ! REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"; then
  echo "ERROR: could not resolve repository root directory" >&2
  exit 1
fi
readonly REPO_ROOT

CLONE_CREATED="false"

#######################################
# Write a timestamped error message to stderr.
# Arguments:
#   Message text
#######################################
err() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')]: $*" >&2
}

#######################################
# Stop and delete the clone if one was created. Runs on every exit path
# (success, pytest failure, watchdog timeout, ssh failure) via an EXIT
# trap, so the disposable VM never survives the script.
# Globals:
#   CLONE_NAME, CLONE_CREATED
#######################################
# Called only via `trap ... EXIT`; shellcheck's usage analysis loses
# track of trap-invoked functions once the script also calls `exit`
# elsewhere (verified false positive against shellcheck 0.11.0,
# reproduced in isolation outside this file).
# shellcheck disable=SC2329
cleanup_clone() {
  if [[ "${CLONE_CREATED}" != "true" ]]; then
    return 0
  fi
  tart stop "${CLONE_NAME}" >/dev/null 2>&1
  tart delete "${CLONE_NAME}" >/dev/null 2>&1
  return 0
}

#######################################
# Kill target_pid if it is still running once timeout_secs elapses, and
# leave sentinel behind so the caller can tell a watchdog kill from a
# normal exit (macOS has no timeout(1)).
# Arguments:
#   Timeout in seconds, target pid, sentinel file path
#######################################
run_watchdog() {
  local timeout_secs="$1"
  local target_pid="$2"
  local sentinel="$3"

  sleep "${timeout_secs}"
  if kill -0 "${target_pid}" 2>/dev/null; then
    : > "${sentinel}"
    kill -TERM "${target_pid}" 2>/dev/null
  fi
}

#######################################
# Poll `tart ip NAME` then ssh until the guest answers, bounded.
# Globals:
#   SSH_OPTS
# Arguments:
#   VM name
# Outputs:
#   Writes the IP address to stdout on success
# Returns:
#   0 with the IP on stdout, 1 if the guest never became reachable
#######################################
wait_for_guest() {
  local vm_name="$1"
  local attempt=0
  local max_attempts=60
  local ip

  while (( attempt < max_attempts )); do
    ip="$(tart ip "${vm_name}" 2>/dev/null)"
    if [[ -n "${ip}" ]] \
        && ssh "${SSH_OPTS[@]}" -o ConnectTimeout=5 "admin@${ip}" true 2>/dev/null; then
      echo "${ip}"
      return 0
    fi
    sleep 5
    (( attempt += 1 ))
  done

  return 1
}

#######################################
# Push the worktree to the guest, excluding VCS state and build output.
# Globals:
#   SSH_CMD, REPO_ROOT
# Arguments:
#   Guest IP address
# Returns:
#   0 on success, 1 if rsync failed
#######################################
push_worktree() {
  local vm_ip="$1"

  if ! rsync -az --delete --exclude=.git --exclude=.venv --exclude=.nox --exclude=build --exclude=dist --exclude=__pycache__ --exclude=tests/functional/_screenshots -e "${SSH_CMD}" "${REPO_ROOT}/" "admin@${vm_ip}:rivercrossing/"; then
    err "rsync push to admin@${vm_ip}:rivercrossing/ failed"
    return 1
  fi
  return 0
}

#######################################
# Pull screenshots back from the guest. Best-effort: a failed pull only
# warns, it never masks the pytest exit code the caller already holds.
# Globals:
#   SSH_CMD, REPO_ROOT
# Arguments:
#   Guest IP address
#######################################
pull_screenshots() {
  local vm_ip="$1"

  if ! rsync -az -e "${SSH_CMD}" \
      "admin@${vm_ip}:rivercrossing/tests/functional/_screenshots/" \
      "${REPO_ROOT}/tests/functional/_screenshots/"; then
    err "warning: failed to pull tests/functional/_screenshots back from guest"
  fi
}

main() {
  trap 'cleanup_clone' EXIT

  if ! command -v tart >/dev/null 2>&1; then
    err "tart not found on PATH. Install with: brew install openai/tools/tart"
    exit 2
  fi

  if ! tart list 2>/dev/null | grep -q "${TEMPLATE_NAME}"; then
    err "Template ${TEMPLATE_NAME} not found. Run scripts/setup_functional_vm.sh first."
    exit 3
  fi

  # APFS copy-on-write: cloning the template takes seconds, not minutes.
  if ! tart clone "${TEMPLATE_NAME}" "${CLONE_NAME}"; then
    err "tart clone ${TEMPLATE_NAME} -> ${CLONE_NAME} failed"
    exit 1
  fi
  CLONE_CREATED="true"

  # --no-audio: the guest's wx.adv.Sound cues (recorded/flagged/error)
  # must not pass through to the host Mac's speakers during a test run;
  # sound.py already degrades to silence when no audio device exists.
  tart run --no-graphics --no-audio "${CLONE_NAME}" &

  local vm_ip
  if ! vm_ip="$(wait_for_guest "${CLONE_NAME}")"; then
    err "guest ${CLONE_NAME} never became ssh-reachable"
    exit 1
  fi

  if ! push_worktree "${vm_ip}"; then
    exit 1
  fi

  local sentinel
  if ! sentinel="$(mktemp)"; then
    err "could not create watchdog sentinel file"
    exit 1
  fi
  # mktemp above creates the file; remove it so its later existence
  # check means "the watchdog fired" (it recreates the path with
  # `: > "${sentinel}"`), not "mktemp ran".
  rm -f "${sentinel}"

  # shellcheck disable=SC2029
  # The RIVERCROSSING_FUNCTIONAL_JOBS expansion is intentionally
  # client-side: the local value selects the guest's xdist parallelism.
  ssh "${SSH_OPTS[@]}" "admin@${vm_ip}" \
    "cd rivercrossing && .venv/bin/python -m pip install -e '.[dev]' --quiet && .venv/bin/python tools/functional_rerun.py pytest tests/functional -v --no-cov -n '${RIVERCROSSING_FUNCTIONAL_JOBS:-auto}' --dist loadfile --reruns 2" &
  local run_pid=$!

  run_watchdog "${VM_TIMEOUT}" "${run_pid}" "${sentinel}" &
  local watchdog_pid=$!

  local rc
  wait "${run_pid}"
  rc=$?

  # Order matters: kill the watchdog's own sleep child (by parent pid)
  # before the watchdog subshell itself. Once the subshell is gone,
  # its sleep is reparented to launchd and `-P "${watchdog_pid}"` can
  # no longer find it, leaving it to hold stdout/stderr open for the
  # rest of RIVERCROSSING_VM_TIMEOUT even though this script has
  # already exited (macOS has no timeout(1) to manage this for us).
  pkill -P "${watchdog_pid}" 2>/dev/null
  kill "${watchdog_pid}" 2>/dev/null
  wait "${watchdog_pid}" 2>/dev/null

  if [[ -f "${sentinel}" ]]; then
    rm -f "${sentinel}"
    err "guest run exceeded RIVERCROSSING_VM_TIMEOUT=${VM_TIMEOUT}s; killed"
    exit 124
  fi
  rm -f "${sentinel}"

  pull_screenshots "${vm_ip}"

  exit "${rc}"
}

main "$@"
