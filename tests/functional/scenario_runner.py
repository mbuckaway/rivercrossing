# SPDX-License-Identifier: GPL-3.0-only
"""Shared child-process scenario runner (rule of three, Phase 10).

``_spawn_scenario``/``_decode_scenario_output``/``_run_scenario`` and
their three constants were reproduced verbatim across
``test_quit_flow_wx.py``, ``test_theme.py`` and ``test_console_demo.py``
-- each module's own docstring cited "no room for a shared sibling
helper" as the reason to duplicate rather than share. Phase 10 adds a
fourth set of callers (the win32 quit-flow scenarios), which would
have made it a fourth verbatim copy; this module is the resulting
third-occurrence extraction (CODINGSTANDARDS-SIMPLECODE.md's rule of
three).

:func:`run_scenario` spawns ``console_subprocess_scenarios.py <name>``
in a fresh interpreter -- always ``subprocess`` (spawn), never
``os.fork``: forking a process that may already have an initialised
``NSApplication`` is unsafe on macOS, and this session's own
``wx_app`` fixture usually already has one -- decodes its one-line
JSON envelope from stdout, and retries the whole spawn up to
:data:`SCENARIO_SPAWN_ATTEMPTS` times: measured elsewhere in this
suite, a whole process launch can rarely land on a memory layout
where every in-process attempt inside the child already fails, and a
fresh spawn gets an independent layout.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

__all__ = ["run_scenario"]

SCENARIOS_SCRIPT = Path(__file__).resolve().parent / "console_subprocess_scenarios.py"
SCENARIO_TIMEOUT_SECONDS = 60
# The scenario child self-terminates (os._exit(124)) after this many
# seconds, dumping its thread stacks to stderr first. Measured on
# windows-latest CI (PR #9, 2026-08-20/21): a healthy scenario takes
# ~27 s there (a fresh interpreter bootstraps wx and builds the whole
# main window), so the parent's original 30 s timeout was racing it,
# and a hung one (test_windows_close_confirmed_destroys_the_frame)
# stalled the whole functional pass for > 900 s. The child must always
# die before SCENARIO_TIMEOUT_SECONDS, so the parent's timeout-and-kill
# path never engages for a hung scenario -- one fast, named failure
# instead of a suite-stalling hang. Pinned by
# tests/unit/test_scenario_runner.py.
SCENARIO_CHILD_BOUND_SECONDS = SCENARIO_TIMEOUT_SECONDS - 10
SCENARIO_SPAWN_ATTEMPTS = 3


def _run_bounded(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run *command* with a bounded drain; never stall on open pipes.

    stdlib's ``subprocess.run(timeout=...)`` re-drains the pipes
    *without* a timeout after killing a timed-out child on Windows --
    if any third process holds the child's stdout/stderr write ends
    (measured on windows-latest CI, PR #9: a scenario child that died
    mid-teardown left Windows Error Reporting holding its pipes), that
    drain blocks forever and stalls the whole functional pass. This
    variant reads the pipes incrementally through daemon threads (the
    same pattern as tools/functional_rerun.py's ``_spawn``), so the
    partial output is always available, and on timeout kills the child
    and raises a ``TimeoutExpired`` carrying everything captured -- it
    never waits on the pipes themselves, so a third party holding the
    write ends cannot stall it.
    """
    proc = subprocess.Popen(  # noqa: S603 -- fixed dev-tool argv from the caller
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out_lines: list[str] = []
    err_lines: list[str] = []

    def _read(stream: Any, sink: list[str]) -> None:  # noqa: ANN401
        try:
            # Incremental: partial output must land in the sink as
            # lines arrive, not at EOF (that is the whole point).
            for line in stream:
                sink.append(line)  # noqa: PERF402 -- see comment above
        except OSError, ValueError:
            # The parent closed the read end on the timeout path; a
            # reader thread racing the close sees the closed file.
            pass

    tout = threading.Thread(target=_read, args=(proc.stdout, out_lines), daemon=True)
    terr = threading.Thread(target=_read, args=(proc.stderr, err_lines), daemon=True)
    tout.start()
    terr.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
        # Do not close or join the reader threads here: closing the
        # shared TextIOWrapper from this thread would block on its lock
        # until the blocked reader thread finishes its read (measured
        # 30 s with a pipe-holding grandchild), and joining would wait
        # on the same. The daemon readers keep their pipes; they drain
        # and end when the process holding the write ends exits. The
        # partial output they captured before the timeout is already in
        # the sinks.
        raise subprocess.TimeoutExpired(
            proc.args, timeout, "".join(out_lines), "".join(err_lines)
        ) from None
    tout.join()
    terr.join()
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, "".join(out_lines), "".join(err_lines)
    )


def _spawn_scenario(
    name: str, timeout: float = SCENARIO_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Spawn one fresh interpreter running scenario *name*."""
    return _run_bounded([sys.executable, str(SCENARIOS_SCRIPT), name], timeout)


def _decode_scenario_output(
    name: str, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Decode *completed*'s stdout into the scenario's JSON envelope.

    Always returns a dict carrying "ok"/"error"/"data"/"context" --
    even when stdout holds no parseable JSON at all -- so a failure
    message never needs a second code path for "the child produced
    nothing useful".
    """
    context = (
        f"scenario={name!r} returncode={completed.returncode}\n"
        f"--- child stdout ---\n{completed.stdout}\n"
        f"--- child stderr ---\n{completed.stderr}"
    )
    last_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.strip()), ""
    )
    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"no parseable JSON on stdout: {exc}",
            "data": None,
            "context": context,
        }
    result["context"] = context
    return result


def run_scenario(name: str) -> dict[str, Any]:
    """Run scenario *name* in a fresh interpreter; decode its result.

    Retries the *spawn* itself, on top of any in-process retry a
    given scenario in ``console_subprocess_scenarios.py`` already
    does: measured, a whole process launch can rarely land on a
    memory layout where every in-process attempt fails, and a fresh
    spawn gets an independent layout. Returns the first successful
    attempt's envelope, or the last attempt's (failing) one if every
    spawn failed.
    """
    result: dict[str, Any] = {"ok": False, "error": "no attempt ran", "data": None, "context": ""}
    for _attempt in range(SCENARIO_SPAWN_ATTEMPTS):
        try:
            completed = _spawn_scenario(name)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            result = {
                "ok": False,
                "error": f"child timed out after {SCENARIO_TIMEOUT_SECONDS}s",
                "data": None,
                "context": f"scenario={name!r}\nstdout={stdout}\nstderr={stderr}",
            }
            continue
        result = _decode_scenario_output(name, completed)
        if result["ok"]:
            return result
    return result
