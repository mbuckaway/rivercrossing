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

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))


@pytest.fixture(scope="module")
def scenario_runner_module() -> ModuleType:
    """Return tests/functional.scenario_runner, imported lazily."""
    import scenario_runner  # noqa: PLC0415

    return scenario_runner


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
