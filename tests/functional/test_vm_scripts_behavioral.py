# SPDX-License-Identifier: GPL-3.0-only
"""VM-lane behavioral tests for run_functional_tests_vm.sh.

Moved here from ``tests/unit/test_vm_scripts.py``: a unit test must
not drive the functional lane, and ``tests/unit/`` holds unit tests
only. Kept rather than deleted so their regression pins survive the
functional-suite rewrite. Nothing in this directory runs while the
lane is disabled -- ``tests/functional/conftest.py`` exits at
collection.

Each test runs the real run_functional_tests_vm.sh under a stub-tool
``PATH`` sandbox (``tart``/``ssh``/``rsync`` stand-ins on a
``tmp_path`` bin directory prepended to ``PATH``): no substring pin
can see that the watchdog sentinel is created by ``mktemp`` itself
and only ever truncated, never removed before its own existence
check, so a clean run still reads back exit 124. Only executing the
script catches that, hence the return-code pins below.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUN_VM_PATH = _REPO_ROOT / "scripts" / "run_functional_tests_vm.sh"

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
if [[ "$*" == *functional_perfile* || "$*" == *pytest* ]]; then
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
