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
from pathlib import Path
from typing import Any

__all__ = ["run_scenario"]

SCENARIOS_SCRIPT = Path(__file__).resolve().parent / "console_subprocess_scenarios.py"
SCENARIO_TIMEOUT_SECONDS = 30
# The scenario child self-terminates (os._exit(124)) after this many
# seconds, dumping its thread stacks to stderr first. Measured on
# windows-latest CI (PR #9, 2026-08-20): a hung scenario
# (test_windows_close_confirmed_destroys_the_frame) stalled the whole
# functional pass for > 900 s. The child must always die before
# SCENARIO_TIMEOUT_SECONDS, so the parent's timeout-and-kill path
# never engages for a hung scenario -- one fast, named failure instead
# of a suite-stalling hang. Pinned by
# tests/unit/test_scenario_runner.py.
SCENARIO_CHILD_BOUND_SECONDS = SCENARIO_TIMEOUT_SECONDS - 5
SCENARIO_SPAWN_ATTEMPTS = 3


def _spawn_scenario(name: str) -> subprocess.CompletedProcess[str]:
    """Spawn one fresh interpreter running scenario *name*."""
    return subprocess.run(  # noqa: S603 -- sys.executable + a fixed repo-local script path
        [sys.executable, str(SCENARIOS_SCRIPT), name],
        capture_output=True,
        text=True,
        timeout=SCENARIO_TIMEOUT_SECONDS,
        check=False,
    )


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
