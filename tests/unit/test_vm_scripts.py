# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the macOS VM functional lane's two scripts.

``scripts/setup_functional_vm.sh`` and ``scripts/run_functional_tests_
vm.sh`` both exist; this module reads each as plain text (mirroring
test_windows_nsi.py's approach to installers/windows.nsi) and pins the
raw directives the scripts must contain, so a later edit that drops a
watchdog, a rsync exclude, or hard-codes a VM name fails here before
it ever reaches a real Tart VM.

Every test below depends on ``_read_script``, which calls
``pytest.fail()`` naming the missing path rather than letting a bare
``FileNotFoundError`` propagate -- the same discipline test_windows_
nsi.py's ``nsi_text`` fixture uses for installers/windows.nsi.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETUP_VM_PATH = _REPO_ROOT / "scripts" / "setup_functional_vm.sh"
_RUN_VM_PATH = _REPO_ROOT / "scripts" / "run_functional_tests_vm.sh"
_VM_SCRIPT_PATHS = (_SETUP_VM_PATH, _RUN_VM_PATH)

# Matches a real `set -e` directive line, never prose that merely
# mentions it in a comment -- the repo bans the directive, not the
# words (CODINGSTANDARDS-SHELL.md).
_SET_E_RE = re.compile(r"(?m)^\s*set -e")

_RSYNC_EXCLUDES = (
    ".git",
    ".venv",
    ".nox",
    "build",
    "dist",
    "__pycache__",
    "tests/functional/_screenshots",
)

_SETUP_VM_SUBSTRINGS = (
    "openai/tools/tart",
    "exit 2",
    "ghcr.io/cirruslabs/macos-tahoe-base:latest",
    "rivercrossing-func-template",
    "tart set",
    "--cpu",
    "--memory",
    "RIVERCROSSING_VM_CPU",
    "RIVERCROSSING_VM_MEMORY",
    "ssh-copy-id",
    "brew install",
    "python@3.14",
    "rsync",
    "venv",
    "pip install -e",
    ".[dev]",
)

_RUN_VM_SUBSTRINGS = (
    "rivercrossing-func-template",
    "exit 3",
    "setup_functional_vm.sh",
    "tart clone",
    "tart run",
    "--no-graphics",
    "tart stop",
    "tart delete",
    "tools/functional_perfile.py ${RIVERCROSSING_VM_TEST_PATHS:-tests/functional}",
    "RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S",
    "RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS",
    "RIVERCROSSING_VM_TIMEOUT",
    "5400",
    "RIVERCROSSING_FUNCTIONAL_JOBS",
    "RIVERCROSSING_VM_TEST_PATHS",
    "exit 124",
)

_RUN_VM_DOCUMENTED_EXIT_CODES = ("exit 2", "exit 3", "exit 124")


def _read_script(path: Path) -> str:
    """Return *path*'s full text, or fail naming the missing script."""
    if not path.is_file():
        pytest.fail(f"{path.relative_to(_REPO_ROOT)} is missing from the working tree")
    return path.read_text(encoding="utf-8")


def _rsync_lines(text: str) -> str:
    """Every line mentioning rsync, joined -- scopes the exclude pins.

    Whichever exact flag syntax the script ends up using
    (``--exclude=X`` vs ``--exclude X``), the excluded path always
    appears somewhere on the rsync invocation's own line(s).
    """
    lines = [line for line in text.splitlines() if "rsync" in line]
    if not lines:
        pytest.fail("no rsync invocation found in scripts/run_functional_tests_vm.sh")
    return "\n".join(lines)


def _ssh_lines(text: str) -> str:
    """Every line mentioning ssh, joined -- scopes the readiness pin."""
    lines = [line for line in text.splitlines() if "ssh" in line]
    if not lines:
        pytest.fail("no ssh invocation found in scripts/setup_functional_vm.sh")
    return "\n".join(lines)


def _tart_run_lines(text: str) -> str:
    """Every line invoking `tart run`, joined -- scopes the boot pin."""
    lines = [line for line in text.splitlines() if "tart run" in line]
    if not lines:
        pytest.fail("no `tart run` invocation found in scripts/setup_functional_vm.sh")
    return "\n".join(lines)


@pytest.fixture(scope="module")
def shellcheck_path() -> str:
    """Return the shellcheck binary, skipping if none is installed."""
    found = shutil.which("shellcheck")
    if found is None:
        pytest.skip("shellcheck not installed on this host")
    return found


@pytest.fixture(scope="module")
def setup_vm_text() -> str:
    """Return scripts/setup_functional_vm.sh's full text."""
    return _read_script(_SETUP_VM_PATH)


@pytest.fixture(scope="module")
def run_vm_text() -> str:
    """Return scripts/run_functional_tests_vm.sh's full text."""
    return _read_script(_RUN_VM_PATH)


# ------------------------------------------------- shared, both scripts


@pytest.mark.parametrize("script_path", _VM_SCRIPT_PATHS, ids=lambda path: path.name)
def test_vm_script_exists_and_is_executable(script_path: Path) -> None:
    """Both VM scripts exist on disk and carry the executable bit."""
    assert script_path.is_file()
    assert os.access(script_path, os.X_OK)


@pytest.mark.parametrize("script_path", _VM_SCRIPT_PATHS, ids=lambda path: path.name)
def test_vm_script_first_line_is_bash_shebang(script_path: Path) -> None:
    """Both scripts declare ``#!/bin/bash`` as their very first line."""
    text = _read_script(script_path)
    first_line = text.splitlines()[0]

    assert first_line == "#!/bin/bash"


@pytest.mark.parametrize("script_path", _VM_SCRIPT_PATHS, ids=lambda path: path.name)
def test_vm_script_never_uses_set_dash_e(script_path: Path) -> None:
    """The repo bans ``set -e``; only a real directive line counts."""
    text = _read_script(script_path)

    assert _SET_E_RE.search(text) is None


@pytest.mark.parametrize("script_path", _VM_SCRIPT_PATHS, ids=lambda path: path.name)
def test_vm_script_passes_shellcheck(shellcheck_path: str, script_path: Path) -> None:
    """Both scripts lint clean under shellcheck when it is present."""
    try:
        result = subprocess.run(  # noqa: S603 -- absolute shellcheck path, fixed argv
            [shellcheck_path, str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"shellcheck on {script_path.name} timed out after {exc.timeout}s")

    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------- scripts/setup_functional_vm.sh


@pytest.mark.parametrize("substring", _SETUP_VM_SUBSTRINGS)
def test_setup_vm_script_references_required_directive(setup_vm_text: str, substring: str) -> None:
    """Every tart/brew/venv directive the setup script needs appears."""
    assert substring in setup_vm_text


def test_setup_script_waits_for_ssh_reachability(setup_vm_text: str) -> None:
    """The boot-wait path must probe ssh, not just poll `tart ip`.

    Measured: setup's wait_for_vm_ip only polls `tart ip`, so
    ssh-copy-id fired before the guest's sshd was actually up and the
    script died ("ssh-copy-id to admin@192.168.64.2 failed"). run_
    functional_tests_vm.sh's wait_for_guest already probes ssh with a
    ConnectTimeout before trusting the IP; setup needs the same shape.
    """
    ssh_lines = _ssh_lines(setup_vm_text)

    assert "ConnectTimeout" in ssh_lines


def test_setup_script_boots_headless(setup_vm_text: str) -> None:
    """The template's first boot must be headless, not windowed.

    RiverCrossing needs no TCC grants, so there is no reason for a
    visible first boot; the per-run `tart run` in run_functional_
    tests_vm.sh already carries `--no-graphics` (test_run_vm_script_
    references_required_directive pins that one), and the template's
    one-time boot in setup needs the same flag.
    """
    tart_run_lines = _tart_run_lines(setup_vm_text)

    assert "--no-graphics" in tart_run_lines


# ----------------------------- scripts/run_functional_tests_vm.sh


@pytest.mark.parametrize("substring", _RUN_VM_SUBSTRINGS)
def test_run_vm_script_references_required_directive(run_vm_text: str, substring: str) -> None:
    """Every tart/rsync/watchdog directive the run needs appears."""
    assert substring in run_vm_text


@pytest.mark.parametrize("excluded", _RSYNC_EXCLUDES)
def test_run_vm_script_rsync_push_excludes_required_path(run_vm_text: str, excluded: str) -> None:
    """The workspace push never carries build artefacts or VCS state."""
    rsync_lines = _rsync_lines(run_vm_text)

    assert excluded in rsync_lines


def test_run_vm_script_pulls_screenshots_back_after_the_run(run_vm_text: str) -> None:
    """Screenshots are excluded from the push, then pulled back once.

    The path is mentioned at least twice: once as a push exclude
    (test_run_vm_script_rsync_push_excludes_required_path already
    pins that) and once more as the pull-back target this test pins.
    """
    occurrences = run_vm_text.count("tests/functional/_screenshots")

    assert occurrences >= 2


@pytest.mark.parametrize("exit_code_text", _RUN_VM_DOCUMENTED_EXIT_CODES)
def test_run_vm_script_documents_each_exit_code(run_vm_text: str, exit_code_text: str) -> None:
    """The comment block names every exit code an operator sees."""
    assert exit_code_text in run_vm_text
