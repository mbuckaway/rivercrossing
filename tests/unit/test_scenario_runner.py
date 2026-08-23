# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tests/functional/scenario_runner.py (child scenarios).

The scenario child's hard bound must always fire before the parent's
``subprocess.run(timeout=SCENARIO_TIMEOUT_SECONDS)``, or a single hung
scenario stalls the whole functional pass: measured on windows-latest
CI (PR #9, 2026-08-20), the ``windows_close_confirmed_destroys``
scenario hung the worker for > 900 s until the rerun wrapper's own
pass bound killed the suite. The child self-terminates at
``SCENARIO_CHILD_BOUND_SECONDS`` with a stack dump, so the parent
never engages its timeout-and-kill path for a hung scenario.
``tests/functional/`` carries no ``__init__.py``, so it is only
importable as an implicit PEP 420 namespace package once the directory
is on ``sys.path`` -- the same insertion test_functional_rerun.py
makes for ``tools/``, and for the same reason the import is deferred
into a fixture (a missing scenario_runner.py would otherwise abort
collection for the whole tests/unit session).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))


@pytest.fixture(scope="module")
def scenario_runner_module() -> ModuleType:
    """Return tests/functional.scenario_runner, imported lazily."""
    import scenario_runner  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast("ModuleType", scenario_runner)


def test_child_bound_fires_before_parent_timeout(scenario_runner_module: ModuleType) -> None:
    """The child's self-terminate bound must beat the parent's timeout.

    The parent's ``subprocess.run(timeout=SCENARIO_TIMEOUT_SECONDS)``
    raises ``TimeoutExpired`` and retries up to three times; a child
    that dies first with a stack dump turns a suite-stalling hang into
    one fast, named failure instead.
    """
    assert (
        scenario_runner_module.SCENARIO_CHILD_BOUND_SECONDS
        < scenario_runner_module.SCENARIO_TIMEOUT_SECONDS
    )


def test_child_bound_is_positive(scenario_runner_module: ModuleType) -> None:
    """A bound of zero would fire before a healthy scenario can run."""
    assert scenario_runner_module.SCENARIO_CHILD_BOUND_SECONDS > 0


def test_run_bounded_times_out_with_partial_output_when_pipes_stay_open(
    scenario_runner_module: ModuleType,
) -> None:
    """A pipe-holding grandchild must not stall the bounded drain.

    Measured on windows-latest CI (PR #9): a scenario child that died
    mid-teardown left its stdout/stderr write ends open (a grandchild
    process holding them), and stdlib ``subprocess.run``'s unbounded
    post-kill drain stalled the whole functional pass for 900 s. The
    drain must be bounded and still surface the child's partial output.
    """
    child = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
        "stdout=sys.stdout, stderr=sys.stderr); "
        "print('CHILD_STARTED', flush=True); time.sleep(30)"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        scenario_runner_module._run_bounded([sys.executable, "-c", child], timeout=0.5)
    elapsed = time.monotonic() - start

    assert elapsed < 10, f"drain stalled {elapsed:.1f}s with the pipes held open"
    assert "CHILD_STARTED" in (excinfo.value.stdout or "")
