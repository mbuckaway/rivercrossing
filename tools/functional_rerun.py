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
"""

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

_MAX_RERUNS = 2

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
    """
    files: set[str] = set()
    for line in _ANSI_RE.sub("", text).splitlines():
        match = _SUMMARY_NODE_RE.match(line)
        if match is None:
            continue
        files.add(match.group(2).partition("::")[0])
    return files


def _spawn(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run *command*, capturing stdout/stderr; never raise."""
    return subprocess.run(  # noqa: S603 -- fixed dev tool argv from the caller
        command, capture_output=True, text=True, check=False
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
    if completed.stdout:
        print(completed.stdout, end="", file=sys.stdout)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    files = parse_summary(f"{completed.stdout}{completed.stderr}")
    print(
        f"functional_rerun: {label}: exit {completed.returncode}, "
        f"{len(files)} failed file(s): {', '.join(sorted(files)) or 'none'}",
        file=sys.stderr,
    )
    return completed.returncode, files


def rerun_failed_files(command: Sequence[str], runner: _Runner = _spawn) -> int:
    """Run a pytest command, rerunning failed files in fresh processes.

    Pass 1 runs the whole *command*. Exit 0 ends immediately; an exit
    code outside {0, 1} is propagated unchanged. A 1 exit parses the
    ``-ra`` summary and re-runs only the FAILED/ERROR files (same
    flags) in a freshly spawned process, up to ``_MAX_RERUNS`` times,
    returning 0 on the first fully green pass. When a pass's summary
    cannot be mapped to re-runnable files, the whole suite is re-run
    once instead, and that result is returned. Returns the final
    pytest exit code.
    """
    program, args = _program_and_args(command)
    initial_rc, initial_files = _run_pass(program + args, runner, "initial")
    if initial_rc == 0:
        return 0
    if initial_rc not in (0, 1):
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
        if pass_rc not in (0, 1):
            return pass_rc
        failed_files = sorted(pass_files)
    return pass_rc


def main(argv: Sequence[str]) -> int:
    """Run the pytest command in *argv* with fresh-process reruns."""
    return rerun_failed_files(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
