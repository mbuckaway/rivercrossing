# SPDX-License-Identifier: GPL-3.0-only
"""Layering tests (E1.1.3): RideStatus contents, and the wx-free core.

R-71: core logic modules import zero ``wx``. The import-linter
contract in ``pyproject.toml`` is the enforcement mechanism at lint
time; the subprocess test below proves the same thing at import
time, in an interpreter this test does not itself pollute.
"""

import re
import subprocess
import sys

import pytest

from rivercrossing.ride import RideStatus


def test_ride_status_members_are_exactly_the_four_lifecycle_states() -> None:
    """No accidental fifth state has crept in."""
    member_names = {member.name for member in RideStatus}
    assert member_names == {"DRAFT", "RUNNING", "FINISHED", "REOPENED"}


@pytest.mark.parametrize(
    ("member", "stored_value"),
    [
        (RideStatus.DRAFT, "draft"),
        (RideStatus.RUNNING, "running"),
        (RideStatus.FINISHED, "finished"),
        (RideStatus.REOPENED, "reopened"),
    ],
)
def test_ride_status_value_matches_spec_stored_spelling(
    member: RideStatus, stored_value: str
) -> None:
    """Each member's ``.value`` matches spec §2's stored spelling."""
    assert member.value == stored_value


def test_ride_status_construction_with_unknown_value_raises_value_error() -> None:
    """An unrecognized stored value is rejected outright."""
    with pytest.raises(ValueError, match=re.escape("'not_a_state' is not a valid RideStatus")):
        RideStatus("not_a_state")


def test_ride_module_import_does_not_load_wx() -> None:
    """Importing rivercrossing.ride never pulls wx into sys.modules.

    Runs in a fresh subprocess interpreter rather than inspecting
    ``sys.modules`` in this process: an earlier test in the same
    pytest session (test_packaging.py) already imports real wx
    successfully, so an in-process check would find ``wx`` cached
    regardless of what ``rivercrossing.ride`` itself imports, and
    would pass even if this module started importing wx tomorrow.
    """
    probe = (
        "import sys\n"
        "import rivercrossing.ride\n"
        "assert 'wx' not in sys.modules, 'wx leaked into rivercrossing.ride'\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
