# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the macOS VM functional lane's two scripts.

``scripts/setup_functional_vm.sh`` and ``scripts/run_functional_tests_
vm.sh`` do not exist yet -- this module is their specification,
written before either script. It reads each as plain text (mirroring
test_windows_nsi.py's approach to installers/windows.nsi) and pins the
raw directives the scripts must contain, so a later edit that drops a
watchdog, a rsync exclude, or hard-codes a VM name fails here before
it ever reaches a real Tart VM.

Every test below depends on ``_read_script``, which calls
``pytest.fail()`` naming the missing path rather than letting a bare
``FileNotFoundError`` propagate -- the same discipline test_windows_
nsi.py's ``nsi_text`` fixture uses for installers/windows.nsi. That
failure, and the plain ``assert script_path.is_file()`` failures
below, are this module's RED state.

The trailing "behavioral" tests run the real run_functional_tests_
vm.sh under a stub-tool ``PATH`` sandbox (``tart``/``ssh``/``rsync``
stand-ins on a ``tmp_path`` bin directory prepended to ``PATH``): no
substring pin can see that the watchdog sentinel is created by
``mktemp`` itself and only ever truncated, never removed before its
own existence check, so a clean run still reads back exit 124.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

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
    "pytest tests/functional --no-cov -n auto --dist loadfile --reruns 2",
    "RIVERCROSSING_VM_TIMEOUT",
    "1800",
    "exit 124",
)

_RUN_VM_DOCUMENTED_EXIT_CODES = ("exit 2", "exit 3", "exit 124")


def _read_script(path: Path) -> str:
    """Return *path*'s full text, or fail naming the missing script."""
    if not path.is_file():
        pytest.fail(f"{path.relative_to(_REPO_ROOT)} does not exist yet")
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
    result = subprocess.run(  # noqa: S603 -- absolute shellcheck path, fixed argv
        [shellcheck_path, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )

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


# --------------------- behavioral: run_functional_tests_vm.sh

# `tart list`/`tart ip` answer with fixed values; every other tart
# subcommand (clone/stop/delete/run) falls through the unmatched case
# straight to the trailing `exit 0`.
_TART_STUB = """\
#!/bin/bash
case "$1" in
  list)
    echo "rivercrossing-func-template"
    ;;
  ip)
    echo "127.0.0.1"
    ;;
esac
exit 0
"""

_RSYNC_STUB = """\
#!/bin/bash
exit 0
"""

_SSH_SUCCEED_STUB = """\
#!/bin/bash
exit 0
"""

# The reachability probe (`ssh ... true`) and the guest pytest
# invocation share one binary; only the pytest command line names
# "pytest", so that is what tells the two apart. `exec sleep` (not a
# plain `sleep` statement) replaces this stub's own process image:
# measured, a plain `sleep 20` left an orphaned grandchild behind once
# the watchdog's SIGTERM killed the stub's bash process, and that
# orphan -- inheriting the same stdout/stderr pipe -- kept subprocess.
# run(capture_output=True) blocking for the full 20s despite the
# script itself already having exited. `exec` makes this stub die
# exactly the way a real ssh binary would.
_SSH_HANG_ON_PYTEST_STUB = """\
#!/bin/bash
if [[ "$*" == *pytest* ]]; then
  exec sleep 20
fi
exit 0
"""

_STUB_SUBPROCESS_TIMEOUT_SECONDS = 60


def _write_stub(bin_dir: Path, name: str, script: str) -> None:
    """Write an executable stub named *name* into *bin_dir*."""
    stub_path = bin_dir / name
    stub_path.write_text(script, encoding="utf-8")
    stub_path.chmod(0o755)


def _stub_env(bin_dir: Path, vm_timeout_seconds: str) -> dict[str, str]:
    """Copy os.environ with *bin_dir* first on PATH; set the timeout.

    HOME stays real: the script only reads ``~/.ssh`` path strings to
    build an ``-i`` flag, and ``ssh`` itself is stubbed, so no real key
    is ever touched.
    """
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["RIVERCROSSING_VM_TIMEOUT"] = vm_timeout_seconds
    return env


class _ScriptRun(NamedTuple):
    """A finished run_functional_tests_vm.sh invocation."""

    returncode: int
    output: str


def _run_with_stubs(bin_dir: Path, vm_timeout_seconds: str, tmp_path: Path) -> _ScriptRun:
    """Run run_functional_tests_vm.sh against the stubbed tool PATH.

    stdout/stderr go to a real file, never a pipe. A killed stub can
    leave an unwaited grandchild running (the watchdog's own ``sleep``
    becomes exactly this once its subshell is SIGTERM'd) that still
    holds the same fd open; measured, with
    ``subprocess.run(capture_output=True)`` that orphan's inherited
    pipe end kept Python blocked reading for its full remaining
    sleep, tens of seconds after the script itself had already exited
    with its real return code. A regular file has no "wait for every
    writer to close" semantics, so this returns the moment the script
    process itself does.
    """
    log_path = tmp_path / "run.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        result = subprocess.run(  # noqa: S603 -- fixed repo-local script path, stubbed PATH
            [str(_RUN_VM_PATH)],
            cwd=_REPO_ROOT,
            env=_stub_env(bin_dir, vm_timeout_seconds),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=_STUB_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    return _ScriptRun(result.returncode, log_path.read_text(encoding="utf-8"))


# The scripts under test are macOS-only Tart tooling; executing them
# (and their bash tool stubs) needs a POSIX shell, absent on Windows.
_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the VM lane scripts are macOS tooling; the stub sandbox needs a POSIX shell",
)


@_POSIX_ONLY
def test_run_script_exits_zero_when_guest_run_succeeds(tmp_path: Path) -> None:
    """A clean run exits 0 and never trips the watchdog.

    Regression pin: the sentinel file used to be created by ``mktemp``
    itself and only truncated by the watchdog, so the timeout check
    reported exit 124 on every run, clean or not.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "tart", _TART_STUB)
    _write_stub(bin_dir, "ssh", _SSH_SUCCEED_STUB)
    _write_stub(bin_dir, "rsync", _RSYNC_STUB)

    run = _run_with_stubs(bin_dir, "30", tmp_path)

    assert run.returncode == 0, run.output


@_POSIX_ONLY
def test_run_script_exits_124_when_guest_run_hangs(tmp_path: Path) -> None:
    """The watchdog kills a guest run that outlives the timeout."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "tart", _TART_STUB)
    _write_stub(bin_dir, "ssh", _SSH_HANG_ON_PYTEST_STUB)
    _write_stub(bin_dir, "rsync", _RSYNC_STUB)

    started = time.monotonic()
    run = _run_with_stubs(bin_dir, "2", tmp_path)
    elapsed_seconds = time.monotonic() - started

    assert run.returncode == 124, run.output
    assert elapsed_seconds < 15


_PIPED_RUN_HARD_TIMEOUT_SECONDS = 30
_PIPED_RUN_BOUND_SECONDS = 8


@_POSIX_ONLY
def test_run_script_releases_stdout_promptly_when_piped(tmp_path: Path) -> None:
    """A real piped invocation must not block on the watchdog's sleep.

    Production defect, not just a test-harness pitfall:
    ``scripts/run_functional_tests_vm.sh 2>&1 | tee run.log`` pipes
    stdout/stderr for real. Once the guest run finishes, ``kill
    "${watchdog_pid}"`` (line 214) kills the watchdog's subshell
    while it is still blocked in its own ``sleep "${timeout_secs}"``
    (line 90); that subshell dies, but the ``sleep`` it forked is
    orphaned and keeps running for the rest of RIVERCROSSING_VM_
    TIMEOUT, still holding the inherited pipe's write end open. A
    piped reader (a real shell pipeline, or Python's own
    ``communicate()``) cannot see EOF, and therefore cannot finish
    draining the pipe, until that orphan also exits -- even though
    the script process itself already exited with its real code
    seconds earlier. This pins release time only; the sentinel bug
    already covers the wrong exit code separately.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir, "tart", _TART_STUB)
    _write_stub(bin_dir, "ssh", _SSH_SUCCEED_STUB)
    _write_stub(bin_dir, "rsync", _RSYNC_STUB)

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed repo-local script path, stubbed PATH
            [str(_RUN_VM_PATH)],
            cwd=_REPO_ROOT,
            env=_stub_env(bin_dir, "15"),
            capture_output=True,
            text=True,
            timeout=_PIPED_RUN_HARD_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"piped stdout was not released within {_PIPED_RUN_HARD_TIMEOUT_SECONDS}s: {exc}"
        )
    elapsed_seconds = time.monotonic() - started

    assert elapsed_seconds < _PIPED_RUN_BOUND_SECONDS, completed.stdout + completed.stderr
