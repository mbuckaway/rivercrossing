# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the data_source module's display-format helpers.

``EngineDataSource`` / ``DataSource`` / ``EmptyDataSource`` behaviour
is driven through the console, results and demo suites (test_console.py,
test_results.py, test_demo.py). These tests pin the module's two pure
display formatters those suites only reach indirectly: the one-hour
switch of ``_format_lap_time`` (durations past ``h:mm:ss``) and
``_event_time``'s refusal arm for a payload timestamp that is not
ISO-8601. Pure functions over plain values -- no wx, no I/O, so there
is nothing to fake or mock (T-10).
"""

import pytest

from rivercrossing.ride import Event
from rivercrossing.ui.presenters import data_source as data_source_module

# ------------------------------------------------- lap-time format


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (3599, "59:59"),  # one second under the hour: still m:ss
        (3600, "1:00:00"),  # the exact hour flips the h:mm:ss arm on
        (3601, "1:00:01"),  # one second past the hour
        (7200, "2:00:00"),  # two full hours
    ],
    ids=["under_hour_control", "exact_hour", "one_second_past_hour", "two_hours"],
)
def test_format_lap_time_given_duration_of_an_hour_or_more_renders_h_mm_ss(
    seconds: float, expected: str
) -> None:
    """Lap times >= 1 h switch from m:ss to h:mm:ss (no zero pad)."""
    assert data_source_module._format_lap_time(seconds) == expected


# ----------------------------------------------------- event time


def test_event_time_given_non_iso_timestamp_renders_empty_string() -> None:
    """An unparseable payload timestamp refuses: the row shows ""."""
    event = Event(
        action="record_crossing",
        payload={"entry_id": "12", "crossed_at": "not an ISO timestamp"},
    )

    assert data_source_module._event_time(event) == ""


def test_event_time_given_payload_without_timestamp_key_renders_empty_string() -> None:
    """A payload with no ISO timestamp key renders "" (exhausted)."""
    event = Event(action="stop", payload={"entry_id": "12", "reason": "track blocked"})

    assert data_source_module._event_time(event) == ""


def test_event_time_given_iso_timestamp_renders_hh_mm_ss() -> None:
    """A parseable naive timestamp renders as stored, 24-hour clock."""
    event = Event(
        action="record_crossing",
        payload={"entry_id": "12", "crossed_at": "2026-09-20T10:30:00"},
    )

    assert data_source_module._event_time(event) == "10:30:00"
