# SPDX-License-Identifier: GPL-3.0-only
"""Shared installer-smoke helpers.

``detach_with_retries`` is the hdiutil-detach poll loop shared by
``test_dmg_smoke.py`` and ``test_release_signing.py``: retry the
detach, forcing on the last attempt. The runner is injected so each
caller keeps its own bounded-spawn helper (the two files name their
timeouts differently, per the tool being waited on).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from pathlib import Path

_RunnerResult = TypeVar("_RunnerResult", bound=subprocess.CompletedProcess[str])

# A bounded spawn that takes the fixed argv and a per-call timeout
# (test_dmg_smoke._run_hdiutil / test_release_signing._run_tool).
_Runner = Callable[..., _RunnerResult]

HDIUTIL = "/usr/bin/hdiutil"


def detach_with_retries(  # noqa: PLR0913 -- one knob per caller's site constants
    run: _Runner,
    mount_point: Path,
    *,
    attempts: int,
    retry_seconds: float,
    timeout_seconds: int,
) -> None:
    """Detach *mount_point* via *run*; retry, force on the last try."""
    for attempt in range(attempts):
        is_last_attempt = attempt == attempts - 1
        cmd = [HDIUTIL, "detach", str(mount_point)]
        if is_last_attempt:
            cmd.append("-force")
        result = run(cmd, timeout=timeout_seconds)
        if result.returncode == 0:
            return
        time.sleep(retry_seconds)
