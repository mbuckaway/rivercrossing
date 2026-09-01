# SPDX-License-Identifier: GPL-3.0-only
"""The nightly-issue body builder (E9.2.2, R-77).

``tools/nightly_issue.py`` formats the GitHub issue the nightly
acceptance run files when the seeded race fails: the body must carry
the seed verbatim so the failure is reproducible by re-running with the
same ``RIVERCROSSING_ACCEPTANCE_SEED``. These are the "forced-failure
files seed in the issue body (dry-run)" tests the E9.2.2 brief names --
the forced failure is simulated by calling the builder directly with a
known seed, and the assertion is that the seed lands in the body.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))


@pytest.fixture
def nightly_issue() -> ModuleType:
    """Import the tool by path (dev script, not a package)."""
    import nightly_issue  # noqa: PLC0415 -- needs tools/ on sys.path first

    return cast("ModuleType", SimpleNamespace(build_issue_body=nightly_issue.build_issue_body))


def test_forced_failure_body_carries_the_seed_verbatim(nightly_issue: ModuleType) -> None:
    """The seed lands in the body unchanged -- the dry-run proof."""
    body = nightly_issue.build_issue_body(
        seed=20260920, os_label="macos-latest", run_url="https://example.com/runs/1"
    )

    assert "20260920" in body


def test_forced_failure_body_names_the_os_and_the_run(nightly_issue: ModuleType) -> None:
    """The body names the failed leg and links the run."""
    body = nightly_issue.build_issue_body(
        seed=7, os_label="windows-latest", run_url="https://example.com/runs/42"
    )

    assert "windows-latest" in body
    assert "https://example.com/runs/42" in body
    assert "7" in body
