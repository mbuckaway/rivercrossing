# SPDX-License-Identifier: GPL-3.0-only
r"""Run every functional-test file in its own fresh pytest process.

The wx functional suite (tests/functional) fails process-granularly.
Full-suite xdist passes at ``-n 2`` -- the convergent worker count --
accumulate wx/SIP wrapper-cache degradation until late files fail
with ``_support.py:106 LookupError``: a control exists in the XRC
(the error prints it in the child list) yet ``FindWindowByName``
resolves it to nothing, because SIP mapped that address to a stale
wrapper whose C++ object is gone (docs/FUNCTIONAL-SUITE-INSTABILITY.
md section 2.1 and find_control's docstring -- wxWidgets/Phoenix
#2931, Python-SIP/sip#113, wxWidgets/wxWidgets#26789, no released
fix). Measured this session: whole-suite ``-n 2`` runs fail that way
on late files, while EVERY file passes when run alone in a fresh
pytest process. A fresh process has a fresh SIP map -- the only
complete cure -- and pytest's ``--reruns`` re-runs inside the same
poisoned worker, so it cannot absorb the corruption on its own.

This wrapper therefore runs one fresh pytest process per *file*
(``test_*.py`` directly under the directory in argv), never the whole
suite in one process, and never more than two at a time: measured,
more concurrent wx processes crash (the repo's own VM runner logged
4-6 wx-churn segfaults per run at xdist ``-n auto``; ``-n 2`` is the
convergent count, PR #42). tools/functional_rerun.py is unchanged
and remains the whole-suite/acceptance backstop; this tool is
deliberately self-contained -- no functional_rerun import -- and
keeps one retry round of its own: a file that fails its initial
fresh-process run gets exactly one more fresh-process run, because a
bad roll is ~1 in 10 even in a fresh process (noxfile.py's functional
session measurements).

Each file's process is bounded: a file still running after
``PASS_TIMEOUT_S`` (default 1200s, env override
``RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S``) is killed and counts
as failed (exit code 124, mirroring functional_rerun's semantics), so
one hung modal (docs/FUNCTIONAL-SUITE-INSTABILITY.md section 2.4)
costs one file's budget, never a whole 20-40 minute pass.

Usage::

    python tools/functional_perfile.py tests/functional

Concurrency defaults to 2 and is overridable via
``RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS``; noxfile.py maps the
legacy ``RIVERCROSSING_FUNCTIONAL_JOBS`` (the old xdist ``-n`` knob)
onto that env. Exit codes: 0 when every file is green after the
retry round, 1 when any file fails both rounds, 2 on a usage error
(any argv other than exactly one existing directory).
"""

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO, cast

# Per-file process bound. Measured whole-suite passes run 20-40 min at
# 2 workers; a single file -- even a window-heavy one -- fits one
# former per-pass budget with margin, and a hung modal still costs one
# file's bound instead of a whole pass.
PASS_TIMEOUT_S = int(os.environ.get("RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S", "1200"))

# Fresh-process retry rounds for still-failed files. The wx/SIP
# corruption and XRC degradation are process-granular; the repo's own
# measurements (tools/functional_rerun.py) show one fresh rerun leaves
# a ~p^2 residual on window-heavy files and ~4 converge. Three rounds
# here + the initial round match that budget while keeping the suite
# bounded.
_MAX_RETRY_ROUNDS = 3

# wx processes share nothing, but the VM host is the contention point:
# two concurrent fresh wx processes (per-file jobs=2) measured delayed
# frame reaping that leaked windows into the session-end sweep (the
# finish-again frame, ~50% of runs), while jobs=1 converged cleanly
# (measured 2026-09-08). One file at a time is the default;
# RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS overrides.
_DEFAULT_JOBS = 1
_JOBS_ENV = "RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS"

# How long _spawn's stream reader is joined after the child exits.
# The timeout path already used this bound; the normal path needs it
# too -- a third process holding the child's pipes (Windows Error
# Reporting, measured) keeps the reader from EOF.
_READER_JOIN_TIMEOUT_S = 5

# The exit code _spawn reports for a file killed by PASS_TIMEOUT_S
# (mirrors the shell convention for a SIGTERM'd process), so a killed
# file is indistinguishable from a failed one for the retry round.
_TIMEOUT_RC = 124

_Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _worker_count() -> int:
    """Return the per-file concurrency from the env, clamped to >= 1.

    RIVERCROSSING_FUNCTIONAL_PERFILE_JOBS overrides the default of 2;
    a non-numeric value (the legacy xdist ``auto``, for instance)
    falls back to the default rather than crashing the pool.
    """
    try:
        return max(1, int(os.environ.get(_JOBS_ENV, str(_DEFAULT_JOBS))))
    except ValueError:
        return _DEFAULT_JOBS


def test_files(root: Path) -> list[Path]:
    """Return every ``test_*.py`` file directly under *root*, sorted.

    Helper modules (conftest.py, harness.py, pages.py -- anything not
    named ``test_*.py``) are excluded by the pattern itself, so a
    helper is never mistaken for a runnable file, and files under
    nested directories are not part of this suite's flat layout.
    """
    return sorted(path for path in root.glob("test_*.py") if path.is_file())


def _spawn(
    command: list[str], *, timeout: int = PASS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Run *command*, streaming merged output, bounded by *timeout*.

    The child's stdout/stderr are merged into one pipe and a daemon
    reader thread echoes each line to this wrapper's stdout as it
    arrives, so a file's progress is visible live rather than in one
    block after the process exits. If the child is still running after
    *timeout* seconds it is killed, a diagnostic goes to stderr, and
    the result reports exit code 124. Never raises for child failures;
    on a healthy exit the reader thread is joined with a short bound
    (a pipe-holding third process would otherwise stall the join) and
    the full accumulated output is returned.
    """
    proc = subprocess.Popen(  # noqa: S603 -- fixed dev tool argv built by run_file
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []

    def _read() -> None:
        stdout = cast("IO[str]", proc.stdout)
        for line in stdout:
            print(line, end="", flush=True)
            lines.append(line)

    reader = threading.Thread(target=_read, name="functional-perfile-stream", daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=_READER_JOIN_TIMEOUT_S)
        reader.join(timeout=_READER_JOIN_TIMEOUT_S)
        tail = "".join(lines[-20:])
        print(
            f"functional_perfile: file run exceeded {timeout}s — "
            "child terminated (last lines below)",
            file=sys.stderr,
        )
        if tail:
            print(tail, end="", file=sys.stderr)
        return subprocess.CompletedProcess(
            args=command, returncode=_TIMEOUT_RC, stdout="".join(lines), stderr=""
        )
    # Bounded on the normal path too, for the same pipe-holder reason:
    # the daemon reader drains and ends when the holder exits, and
    # *lines* already holds everything captured so far.
    reader.join(timeout=_READER_JOIN_TIMEOUT_S)
    return subprocess.CompletedProcess(
        args=command, returncode=proc.returncode, stdout="".join(lines), stderr=""
    )


def run_file(  # noqa: PLR0913 -- flags/timeout are spec'd knobs; runner= is the test seam
    python: str,
    file: Path,
    *,
    flags: Sequence[str] = ("-o", "faulthandler_timeout=600"),
    timeout: int = PASS_TIMEOUT_S,
    runner: _Runner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one test *file* in its own fresh pytest process.

    The command is ``[python, "-m", "pytest", str(file), "-v",
    "--no-cov", "--reruns", "2", *flags]``: verbose so the file's
    failure lines name the failing test, no coverage (the functional
    suite proves behaviour by driving windows, not by counting lines),
    and pytest's own two in-process reruns ride along for the rare
    single-test flake that is not wrapper-cache corruption. The
    default flags carry ``faulthandler_timeout=600``: a test that
    hangs (a leaked modal, measured) dumps every thread and fails at
    600 s instead of stalling the file until the per-file bound. A
    file still running after *timeout* seconds is killed and reported
    as exit code 124, exactly like functional_rerun's bounded pass.

    Args:
        python: The interpreter that launches pytest -- normally
            ``sys.executable``, so the fresh process shares this venv
            and never depends on a bare ``pytest`` being on PATH.
        file: The test file to run in its own process.
        flags: Extra pytest flags appended after ``--reruns 2``.
        timeout: Per-file bound in seconds; defaults to PASS_TIMEOUT_S.
        runner: Optional callable replacing ``_spawn`` (test seam
            mirroring functional_rerun's ``_Runner`` pattern).

    Returns:
        The child's CompletedProcess: 0 when green, pytest's exit code
        on failure, 124 when the timeout killed the file.
    """
    command = [python, "-m", "pytest", str(file), "-v", "--no-cov", "--reruns", "2", *flags]
    if runner is None:
        return _spawn(command, timeout=timeout)
    return runner(command)


def _round(files: Sequence[Path], *, runner: _Runner | None = None, label: str = "") -> list[Path]:
    """Run *files* through run_file, bounded by the env concurrency.

    Each file's run is announced on stdout (its header line) from the
    worker thread that is about to spawn it, so the header precedes
    that file's own streamed output even under concurrency. Returns
    the files whose run exited non-zero, in *files*' own order --
    ThreadPoolExecutor.map preserves submission order, so a retry
    round re-runs the failed files deterministically.
    """
    jobs = _worker_count()

    def _run_one(file: Path) -> int:
        tag = f"{label} " if label else ""
        print(f"functional_perfile: {tag}{file}", flush=True)
        return run_file(sys.executable, file, runner=runner).returncode

    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="functional-perfile") as pool:
        codes = list(pool.map(_run_one, files))
    return [file for file, code in zip(files, codes, strict=True) if code != 0]


def _summarise(files: Sequence[Path], failed: Sequence[Path], label: str) -> None:
    """Print the ``N files, M failed: ...`` summary for one round."""
    names = ", ".join(str(file) for file in failed) or "none"
    print(
        f"functional_perfile: {label}: {len(files)} files, {len(failed)} failed: {names}",
        file=sys.stderr,
    )


def main(argv: Sequence[str], *, runner: _Runner | None = None) -> int:
    """Run every test file under the directory in *argv*, 2 at a time.

    Collects the directory's ``test_*.py`` files (or, when *argv* names
    a single existing ``test_*.py`` file, just that file), runs each in
    its own fresh pytest process (bounded by PASS_TIMEOUT_S), prints a
    round summary, and -- when any file failed -- runs ONE more round
    of exactly those files in fresh processes as the backstop. Returns
    0 when every file is green after that retry round, 1 when any file
    still fails, and 2 for a usage error (argv other than exactly one
    existing directory or test file; this tool deliberately has no
    other CLI). *runner* is the same test seam run_file takes.
    """
    if len(argv) != 1:
        print(
            "functional_perfile: usage: python tools/functional_perfile.py "
            "<directory-of-test-files | test-file>",
            file=sys.stderr,
        )
        return 2
    root = Path(argv[0])
    if root.is_dir():
        files = test_files(root)
    elif root.is_file() and root.name.startswith("test_"):
        files = [root]
    else:
        print(
            f"functional_perfile: not a directory or test file: {argv[0]}",
            file=sys.stderr,
        )
        return 2
    failed = _round(files, runner=runner, label="")
    _summarise(files, failed, "initial")
    if not failed:
        return 0

    # Fresh-process retry rounds: the wx/SIP corruption and XRC
    # degradation this tool exists to isolate are process-granular, so
    # every round re-runs only the still-failed files in NEW processes.
    # The repo's own measurements (tools/functional_rerun.py) show one
    # retry leaves a ~p^2 residual on window-heavy files; three rounds
    # converge the suite (measured 2026-09-07: attempt 2 needed two).
    for round_number in range(1, _MAX_RETRY_ROUNDS + 1):
        retried = failed
        print(
            f"functional_perfile: retrying {len(retried)} failed file(s) in "
            f"fresh processes (round {round_number})",
            file=sys.stderr,
        )
        failed = _round(retried, runner=runner, label=f"retry{round_number}:")
        _summarise(retried, failed, f"retry{round_number}")
        if not failed:
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
