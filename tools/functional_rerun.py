# SPDX-License-Identifier: GPL-3.0-only
r"""Re-run failed functional-test FILES in fresh pytest processes.

The functional suite (tests/functional) drives real wx windows, and
wxPython's wrapper cache is process-granular: SIP's C++-pointer-to-
Python map keeps entries for C++-constructed objects (XRC controls,
FindWindowByName results) as long as the Python wrapper lives, so a
wrapper that outlives its C++ object poisons later allocations at
that address with the wrong class (docs/EPIC3-SESSION-SUMMARY.md,
Addendum 2). There is no in-process repair -- a fresh process has a
fresh map. pytest's own ``--reruns`` re-runs inside the same poisoned
worker, so it cannot absorb the corruption.

This wrapper therefore re-runs the *files* that failed, each set in
its own freshly spawned pytest process (same ``--no-cov -n auto
--dist loadfile`` flags), at most twice after the initial run.
Usage::

    python tools/functional_rerun.py pytest tests/functional \\
        --no-cov -n auto --dist loadfile --reruns 2

A leading ``pytest`` token is normalised to ``<python> -m pytest``
with the interpreter that launched this wrapper, so a fresh process
never depends on a bare ``pytest`` being on PATH -- the guest VM's ssh
session, for instance, has no .venv/bin on PATH, yet .venv/bin/python
can import pytest as a module. Any other first token is run verbatim.

Exit codes mirror pytest: 0 when any pass is fully green, the last
pass's pytest code when the rerun budget is exhausted, and any code
outside {0, 1} (interrupt, internal error, xdist worker crash) is
propagated unchanged without further reruns -- a genuine crash is
never masked by a rerun.

Each pass is bounded and streamed. ``_spawn`` merges the child's
stdout/stderr into one pipe and a daemon reader thread echoes every
line to the wrapper's stdout live, so progress is visible while the
pass runs and a hang leaves its last lines in the log instead of
nothing at all. If the child is still running after ``PASS_TIMEOUT_S``
(600s) it is killed and a diagnostic naming the elapsed time and the
last ~20 lines is printed to stderr; the pass then reports exit code
124, which is outside {0, 1} and is therefore propagated without a
rerun, consistent with the crash rule above. Killing the wrapper's
pytest process orphans its xdist workers, but they exit on their own
once the controller's pipe closes.
"""

import re
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, cast

_MAX_RERUNS = 2

# Measured healthy runs are ~2 min (macOS CI) / ~65 s (local VM); on
# windows-latest CI the suite's worst-case crawl reaches ~180-260 s
# (the last window-heavy files dominate), so 300 s raced a healthy
# pass and killed it seconds from green. 600 s clears that crawl with
# margin while still bounding a true hang to a ten-minute pass whose
# timeout (124) triggers the whole-suite fresh-process fallback in
# rerun_failed_files.
PASS_TIMEOUT_S = 600

# How long _spawn's stream reader is joined after the child exits.
# The timeout path already used this bound; the normal path needs it
# too -- a third process holding the child's pipes (Windows Error
# Reporting, measured) keeps the reader from EOF.
_READER_JOIN_TIMEOUT_S = 5

# The exit code _spawn reports for a pass killed by PASS_TIMEOUT_S
# (mirrors the shell convention for a SIGTERM'd process). Outside
# pytest's own {0, 1} set, so rerun_failed_files never mistakes a
# bounded hang for a real crash.
_TIMEOUT_RC = 124

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SUMMARY_NODE_RE = re.compile(r"(FAILED|ERROR) (.+?)(?: - |$)")

_Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def parse_summary(text: str) -> set[str]:
    """Return the distinct FILES named by FAILED/ERROR summary lines.

    pytest's ``-ra`` short summary prints one ``FAILED``/``ERROR``
    line per failing node id, e.g. ``FAILED tests/functional/test_x.
    py::test_y``; fixture-setup failures are ``ERROR`` lines. Each
    node id is truncated at its first ``::`` to yield the file, so
    parametrized ids (``::test_y[case]``) and class-scoped ids
    (``::TestX::test_y``) map to the same file, and a bare-file
    collection error maps to the file itself. Header lines, other
    statuses, and unparseable lines are ignored; ANSI colour codes
    are stripped first (the guest runs with colour). A clean summary
    returns an empty set.

    The marker is searched for anywhere in a line, not just at its
    start: with ``-v`` under xdist the progress lines carry it
    mid-line (``[gw2] [ 82%] FAILED tests/...``), and a timed-out
    pass (124) never prints the ``-ra`` summary, so those streamed
    lines are the only failure record a 124 pass leaves behind.
    """
    files: set[str] = set()
    for line in _ANSI_RE.sub("", text).splitlines():
        match = _SUMMARY_NODE_RE.search(line)
        if match is None:
            continue
        files.add(match.group(2).partition("::")[0])
    return files


# A bare node id line, printed by pytest -v when a test starts
# ("tests/functional/test_x.py::test_y"). Result lines carry a
# "[gwN] [ XX%] PASSED/FAILED/..." prefix instead.
_START_RE = re.compile(r"^(tests/[\w./-]+\.py::\S+)")
_RESULT_RE = re.compile(r"\b(?:PASSED|FAILED|RERUN|SKIPPED)\s+(tests/[\w./-]+\.py::\S+)")


def stalled_file(text: str) -> str | None:
    """Return the file of the last test started but never finished.

    A timed-out pass ends with the stalled test's bare start line and
    no result line for it (measured on windows-latest CI: the suite
    stalls in the last window-heavy file until the pass bound kills
    it). Every bare start line whose node id also appears in a result
    line is ruled out; the survivor -- the last one -- names the file
    the fresh-process rerun must target. Returns ``None`` when every
    started test produced a result, or nothing started.
    """
    started: str | None = None
    resulted: set[str] = set()
    for line in _ANSI_RE.sub("", text).splitlines():
        start = _START_RE.match(line)
        if start is not None:
            started = start.group(1)
            continue
        for match in _RESULT_RE.finditer(line):
            resulted.add(match.group(1))
    if started is None or started in resulted:
        return None
    return started.partition("::")[0]


def _spawn(
    command: list[str], *, timeout: float | None = PASS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Run *command*, streaming output live, bounded by *timeout*.

    The child's stdout/stderr are merged into one pipe (pytest's ``-ra``
    short summary is on stdout anyway) and a daemon reader thread echoes
    each line to the wrapper's stdout as it arrives, so progress is
    visible live rather than in one block after the child exits. If the
    child is still running after *timeout* seconds it is killed, a
    diagnostic naming the timeout and the last lines of output goes to
    stderr, and the result reports exit code 124 -- outside {0, 1}, so
    ``rerun_failed_files`` propagates it without a rerun, exactly like
    a genuine crash. Never raises for child failures; on a healthy pass
    the reader thread is joined with a short bound (a pipe-holding
    third process would otherwise stall the join) and the full
    accumulated output is returned.
    """
    proc = subprocess.Popen(  # noqa: S603 -- fixed dev tool argv from the caller
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

    reader = threading.Thread(target=_read, name="functional-rerun-stream", daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=_READER_JOIN_TIMEOUT_S)
        reader.join(timeout=_READER_JOIN_TIMEOUT_S)
        tail = "".join(lines[-20:])
        elapsed = cast("float", timeout)
        print(
            f"functional_rerun: pass exceeded {elapsed:.0f}s — "
            "child terminated (last lines below)",
            file=sys.stderr,
        )
        if tail:
            print(tail, end="", file=sys.stderr)
        return subprocess.CompletedProcess(
            args=command, returncode=124, stdout="".join(lines), stderr=""
        )
    # Bounded on the normal path too, for the same pipe-holder reason:
    # the daemon reader drains and ends when the holder exits, and
    # *lines* already holds everything captured so far.
    reader.join(timeout=_READER_JOIN_TIMEOUT_S)
    return subprocess.CompletedProcess(
        args=command, returncode=proc.returncode, stdout="".join(lines), stderr=""
    )


def _program_and_args(
    command: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Split a pytest command into (program prefix, remaining args).

    A leading ``pytest`` token is normalised to ``[sys.executable,
    "-m", "pytest"]`` (see the module docstring); anything else is
    passed through verbatim.
    """
    if not command:
        return [], []
    if command[0] == "pytest":
        return [sys.executable, "-m", "pytest"], list(command[1:])
    return [command[0]], list(command[1:])


def _split_paths_and_flags(args: list[str]) -> tuple[list[str], list[str]]:
    """Split *args* into (leading test paths, flags onward).

    All three invocation sites put the test path(s) before the first
    option, and noxfile appends posargs after the flags, so the paths
    are the contiguous run of leading non-option tokens.
    """
    first_flag = next(
        (index for index, token in enumerate(args) if token.startswith("-")),
        len(args),
    )
    return args[:first_flag], args[first_flag:]


def _rerun_command(program: list[str], args: list[str], failed_files: list[str]) -> list[str]:
    """Build a fresh-process rerun command for failed files."""
    _, flags = _split_paths_and_flags(args)
    return program + failed_files + flags


def _rerunnable(files: list[str]) -> bool:
    """Return True when *files* are real test files a rerun can target.

    The session-end sweep fixture failure has no per-test node id, so
    its summary line maps to a conftest path (or nothing at all);
    such entries are not re-runnable, and the caller falls back to a
    whole-suite run instead.
    """
    return bool(files) and all(
        path.is_file() and path.name.startswith("test_") for path in (Path(file) for file in files)
    )


def _run_pass(command: list[str], runner: _Runner, label: str) -> tuple[int, set[str]]:
    """Run one pass, echo its output, and report a progress line."""
    completed = runner(command)
    if runner is not _spawn:
        # Fake runners carry canned output that must still be echoed;
        # the real _spawn already streams every line live, so echoing
        # here would duplicate the pass's output.
        if completed.stdout:
            print(completed.stdout, end="", file=sys.stdout)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
    files = parse_summary(f"{completed.stdout}{completed.stderr}")
    if completed.returncode == _TIMEOUT_RC:
        stalled = stalled_file(f"{completed.stdout}{completed.stderr}")
        if stalled is not None:
            # The pass that timed out never evaluated this file's
            # unfinished test; the fresh-process rerun must cover it.
            files = set(files) | {stalled}
    print(
        f"functional_rerun: {label}: exit {completed.returncode}, "
        f"{len(files)} failed file(s): {', '.join(sorted(files)) or 'none'}",
        file=sys.stderr,
    )
    return completed.returncode, files


def rerun_failed_files(command: Sequence[str], runner: _Runner = _spawn) -> int:
    """Run a pytest command, rerunning failed files in fresh processes.

    Pass 1 runs the whole *command*. Exit 0 ends immediately; an exit
    code outside {0, 1} -- except the bounded pass's own 124 -- is
    propagated unchanged. A 1 exit parses the ``-ra`` summary and
    re-runs only the FAILED/ERROR files (same flags) in a freshly
    spawned process, up to ``_MAX_RERUNS`` times, returning 0 on the
    first fully green pass. A timed-out pass (124) never prints that
    summary, so its file set comes from the streamed progress lines
    (``parse_summary`` searches mid-line) plus the file of the last
    started-but-unfinished test (``stalled_file``); those files are
    re-run fresh the same way -- the one measured remedy for the
    process-granular wx/SIP wrapper-cache corruption the hang signals.
    When a pass's output cannot be mapped to re-runnable files -- no
    FAILED/ERROR lines and no stalled test -- the whole suite is re-run
    once in a fresh process, and that result is returned. Returns the
    final pytest exit code.
    """
    program, args = _program_and_args(command)
    initial_rc, initial_files = _run_pass(program + args, runner, "initial")
    if initial_rc == 0:
        return 0
    if initial_rc not in (0, 1, 124):
        return initial_rc

    failed_files = sorted(initial_files)
    pass_rc = 1
    for attempt in range(1, _MAX_RERUNS + 1):
        if not _rerunnable(failed_files):
            fallback_rc, _ = _run_pass(program + args, runner, "whole-suite fallback")
            return fallback_rc
        pass_rc, pass_files = _run_pass(
            _rerun_command(program, args, failed_files),
            runner,
            f"rerun {attempt}",
        )
        if pass_rc == 0:
            return 0
        if pass_rc not in (0, 1, 124):
            return pass_rc
        failed_files = sorted(pass_files)
    return pass_rc


def main(argv: Sequence[str]) -> int:
    """Run the pytest command in *argv* with fresh-process reruns."""
    return rerun_failed_files(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
