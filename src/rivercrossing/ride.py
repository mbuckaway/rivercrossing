# SPDX-License-Identifier: GPL-3.0-only
"""Ride state machine (spec §3, R-36): the four lifecycle states.

Pure Python only -- no ``wx`` import may ever land in this module
(R-71); the "wx stays inside rivercrossing.ui" import-linter
contract in ``pyproject.toml`` enforces it.
"""

from enum import StrEnum


class RideStatus(StrEnum):
    """A ride's lifecycle state (spec §3).

    A plain ``Enum`` would need callers to unwrap ``.value`` before
    comparing to or storing the spelling read from the database;
    ``StrEnum`` members already *are* that string, so
    ``RideStatus("running") == "running"`` and a member can be
    written straight into the ``ride.status`` column with no
    unwrapping step either side of the round trip.

    Values are the exact lowercase spellings stored in that column
    (spec §2), so ``RideStatus("running")`` round-trips a value read
    straight back out of the database.
    """

    DRAFT = "draft"
    RUNNING = "running"
    FINISHED = "finished"
    REOPENED = "reopened"
