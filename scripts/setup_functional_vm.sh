#!/bin/bash
#
# One-time provisioning of the reusable Tart macOS VM template used to
# run RiverCrossing's functional test suite. Local macOS functional
# tests open 23 real wx windows and take over the developer's desktop;
# a crashed run can foul desktop state. This script builds a template
# VM (its own WindowServer) so nothing touches the host session.
# Isolation contains crashes, it does not cure them. Local-dev only --
# CI is unaffected.
#
# Interactive: prompts once for the guest "admin" account password to
# seed key-based ssh access. Re-running this script is idempotent -- it
# re-provisions the existing template rather than failing.
#
# Usage: scripts/setup_functional_vm.sh

set -uo pipefail

readonly TEMPLATE_NAME="rivercrossing-func-template"
readonly BASE_IMAGE="ghcr.io/cirruslabs/macos-tahoe-base:latest"
readonly VM_SSH_KEY="${HOME}/.ssh/rivercrossing_vm_ed25519"
readonly VM_CPU="${RIVERCROSSING_VM_CPU:-4}"
readonly VM_MEMORY="${RIVERCROSSING_VM_MEMORY:-8192}"

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

#######################################
# Write a timestamped error message to stderr.
# Arguments:
#   Message text
#######################################
err() {
  echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')]: $*" >&2
}

#######################################
# Clone the base image into the template VM, unless it already exists.
# Globals:
#   TEMPLATE_NAME, BASE_IMAGE
# Returns:
#   0 on success, 1 if the clone failed
#######################################
ensure_template_cloned() {
  if tart list 2>/dev/null | grep -q "${TEMPLATE_NAME}"; then
    echo "Template ${TEMPLATE_NAME} already exists; re-provisioning it."
    return 0
  fi

  echo "Cloning ${BASE_IMAGE} (downloads ~25 GB the first time)..."
  if ! tart clone "${BASE_IMAGE}" "${TEMPLATE_NAME}"; then
    err "tart clone ${BASE_IMAGE} -> ${TEMPLATE_NAME} failed"
    return 1
  fi
  return 0
}

#######################################
# Poll `tart ip NAME` then the guest's sshd until reachable, bounded.
# A key-only success check cannot double as this readiness probe: on
# first-ever setup the key exists locally but is not yet installed in
# the guest, so key auth never succeeds before ssh-copy-id runs it.
# Treat "sshd answered but rejected the key" (stderr's "Permission
# denied") as reachable too -- ssh-copy-id can proceed from there --
# and only a still-refused or timed-out connection as not ready yet.
# Globals:
#   VM_SSH_KEY
# Arguments:
#   VM name
# Outputs:
#   Writes the IP address to stdout on success
# Returns:
#   0 with the IP on stdout, 1 if the guest never became reachable
#######################################
wait_for_vm_ip() {
  local vm_name="$1"
  local attempt=0
  local max_attempts=60
  local ip
  local probe_output

  while (( attempt < max_attempts )); do
    ip="$(tart ip "${vm_name}" 2>/dev/null)"
    if [[ -n "${ip}" ]]; then
      if probe_output="$(ssh -i "${VM_SSH_KEY}" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "admin@${ip}" true 2>&1)"; then
        echo "${ip}"
        return 0
      fi
      if [[ "${probe_output}" == *"Permission denied"* ]]; then
        echo "${ip}"
        return 0
      fi
    fi
    sleep 5
    (( attempt += 1 ))
  done

  return 1
}

#######################################
# Generate the VM's dedicated ssh key pair if it does not exist yet.
# Globals:
#   VM_SSH_KEY
# Returns:
#   0 on success, 1 if ssh-keygen failed
#######################################
ensure_ssh_key() {
  if [[ -f "${VM_SSH_KEY}" ]]; then
    return 0
  fi

  echo "Generating ${VM_SSH_KEY}..."
  if ! ssh-keygen -t ed25519 -N "" -f "${VM_SSH_KEY}"; then
    err "ssh-keygen failed for ${VM_SSH_KEY}"
    return 1
  fi
  return 0
}

#######################################
# Run remote_cmd on the guest, extending PATH with the guest's
# Homebrew prefix -- non-interactive ssh sessions don't source the
# guest's login PATH, so bare `brew`/`python3.14` would not resolve.
# Globals:
#   VM_SSH_KEY
# Arguments:
#   Guest IP address, remote command string
# Returns:
#   The remote command's exit status
#######################################
ssh_guest() {
  local vm_ip="$1"
  local remote_cmd="$2"

  ssh -i "${VM_SSH_KEY}" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null "admin@${vm_ip}" \
    "export PATH=\"/opt/homebrew/bin:\${PATH}\"; ${remote_cmd}"
}

#######################################
# Push the repository tree to the guest, excluding VCS state and build
# output -- the same excludes scripts/run_functional_tests_vm.sh uses
# for every later per-run push, so the trees match.
# Globals:
#   VM_SSH_KEY, REPO_ROOT
# Arguments:
#   Guest IP address
# Returns:
#   0 on success, 1 if rsync failed
#######################################
push_initial_code() {
  local vm_ip="$1"

  if ! rsync -az --exclude=.git --exclude=.venv --exclude=.nox --exclude=build --exclude=dist --exclude=__pycache__ --exclude=tests/functional/_screenshots -e "ssh -i ${VM_SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" "${REPO_ROOT}/" "admin@${vm_ip}:rivercrossing/"; then
    err "initial rsync push to admin@${vm_ip}:rivercrossing/ failed"
    return 1
  fi
  return 0
}

main() {
  if ! command -v tart >/dev/null 2>&1; then
    err "tart not found on PATH. Install with: brew install openai/tools/tart"
    exit 2
  fi

  if ! ensure_template_cloned; then
    exit 1
  fi

  if ! tart set "${TEMPLATE_NAME}" --cpu "${VM_CPU}" --memory "${VM_MEMORY}"; then
    err "tart set --cpu ${VM_CPU} --memory ${VM_MEMORY} failed"
    exit 1
  fi

  if ! ensure_ssh_key; then
    exit 1
  fi

  # Headless: RiverCrossing needs no TCC grants, so nothing requires a
  # visible first boot. The only interaction left is the ssh-copy-id
  # password prompt below, in the terminal. --no-audio keeps the
  # template's boot (and any app it ever launches) off the host
  # speakers -- the functional suite's WAV cues must never pass through.
  echo "Booting ${TEMPLATE_NAME} (headless)..."
  tart run --no-graphics --no-audio "${TEMPLATE_NAME}" &

  local vm_ip
  if ! vm_ip="$(wait_for_vm_ip "${TEMPLATE_NAME}")"; then
    err "${TEMPLATE_NAME} never answered 'tart ip' or its ssh port"
    exit 1
  fi

  echo "Copying ssh key to admin@${vm_ip}; the guest password is 'admin'."
  if ! ssh-copy-id -i "${VM_SSH_KEY}.pub" -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null "admin@${vm_ip}"; then
    err "ssh-copy-id to admin@${vm_ip} failed"
    exit 1
  fi

  if ! ssh_guest "${vm_ip}" "brew install python@3.14 rsync"; then
    err "guest 'brew install python@3.14 rsync' failed"
    exit 1
  fi

  if ! ssh_guest "${vm_ip}" "mkdir -p rivercrossing"; then
    err "guest 'mkdir -p rivercrossing' failed"
    exit 1
  fi

  echo "Pushing the repository tree to admin@${vm_ip}:rivercrossing/..."
  if ! push_initial_code "${vm_ip}"; then
    exit 1
  fi

  local venv_cmd
  venv_cmd="cd rivercrossing && python3.14 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'"
  if ! ssh_guest "${vm_ip}" "${venv_cmd}"; then
    err "guest venv setup failed"
    exit 1
  fi

  # Flush guest disk state before stopping: measured an authorized_keys
  # write lost when `tart stop` ran before the guest had synced it.
  if ! ssh_guest "${vm_ip}" "sync"; then
    err "guest 'sync' failed (continuing to tart stop anyway)"
  fi

  if ! tart stop "${TEMPLATE_NAME}"; then
    err "tart stop ${TEMPLATE_NAME} failed (template may still be running)"
  fi

  cat <<EOF
${TEMPLATE_NAME} is provisioned and stopped.
Run scripts/run_functional_tests_vm.sh to execute the functional suite.
EOF
}

main "$@"
