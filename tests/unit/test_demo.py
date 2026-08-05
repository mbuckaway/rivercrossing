# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.demo (E1.2.4) -- written first, red.

``DemoDataSource`` is the removable hard-coding seam behind D1's
engine-free, database-free UI shell (project-plan.md §5, E1.2.4).
These tests pin it against the canvas's fixture values
(xrc-windows.md) and the seam's own honesty: it satisfies
``DataSource`` (both at runtime and under mypy), and it never
reaches into the wx-bearing app bootstrap it is meant to be
imported *by*.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.demo import DemoDataSource, UnknownPlateError
from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters.data_source import (
    AuditRow,
    Counters,
    DataSource,
    EntryDetail,
    EntryLapRow,
    FeedRow,
    RiderRow,
    RideSummary,
    StandingsRow,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEMO_MODULE = _REPO_ROOT / "src" / "rivercrossing" / "demo.py"

# demo.py's only entry_detail fixture is plate 77 (entry_detail_dlg's
# one canvas example); every other plate must raise.
_KNOWN_PLATES = frozenset({"77"})


# ------------------------------------------------- protocol conformance


def test_demo_data_source_isinstance_satisfies_data_source_protocol() -> None:
    """A ``DemoDataSource`` instance structurally satisfies it."""
    assert isinstance(DemoDataSource(), DataSource)


def test_demo_data_source_conforms_to_data_source_via_mypy_strict() -> None:
    """Mypy proves ``DemoDataSource`` satisfies ``DataSource``.

    Static companion to the runtime isinstance check above: pins
    demo.py's own ``if TYPE_CHECKING`` annotated assignment
    (``_conforms_to_data_source: DataSource = DemoDataSource()``) so
    an incompatible method signature is caught even though that line
    itself never executes.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "mypy", str(_DEMO_MODULE)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------ feed rows


def test_demo_feed_rows_returns_five_rows_newest_first() -> None:
    """``feed_rows()`` returns exactly 5 canvas rows, newest first."""
    rows = DemoDataSource().feed_rows()

    assert [row.plate for row in rows] == ["123", "77", "45", "212", "8"]


def test_demo_feed_rows_matches_the_canvas_fixture_exactly() -> None:
    """Every field of every feed row matches the canvas verbatim."""
    rows = DemoDataSource().feed_rows()

    assert rows == [
        FeedRow(
            time="14:22:41",
            plate="123",
            entry="Sam Ellis",
            lap=4,
            lap_time="22:41",
            total="1:31:04",
            card="9H",
        ),
        FeedRow(
            time="14:22:18",
            plate="77",
            entry="Trail Blazers (T)",
            lap=9,
            lap_time="19:55",
            total="3:02:11",
            card="KS",
        ),
        FeedRow(
            time="14:21:59",
            plate="45",
            entry="J. Okafor",
            lap=6,
            lap_time="07:12",
            total="2:44:30",
            card="held",
            flagged=True,
        ),
        FeedRow(
            time="14:21:30",
            plate="212",
            entry="M. Chen",
            lap=5,
            lap_time="24:02",
            total="2:10:44",
            card="JK",
        ),
        FeedRow(
            time="14:20:52",
            plate="8",
            entry="R. Dubois",
            lap=7,
            lap_time="21:17",
            total="2:58:03",
            card="4D",
        ),
    ]


def test_demo_feed_rows_flags_plate_45_as_the_short_lap_crossing() -> None:
    """The single short-lap crossing (plate 45) is the flagged row."""
    rows = DemoDataSource().feed_rows()

    flagged_plates = [row.plate for row in rows if row.flagged]

    assert flagged_plates == ["45"]


# ------------------------------------------------------------- counters


def test_demo_counters_matches_the_canvas_fixture_exactly() -> None:
    """``counters()`` returns the exact four canvas counter values."""
    counters = DemoDataSource().counters()

    assert counters == Counters(
        crossings=1124, cards_dealt=1092, on_course=42, shoe_remaining=41, shoe_total=108
    )


def test_demo_counters_reports_shoe_as_41_of_108() -> None:
    """The shoe counter matches the canvas's "Shoe 41/108"."""
    counters = DemoDataSource().counters()

    assert (counters.shoe_remaining, counters.shoe_total) == (41, 108)


# -------------------------------------------------------- ride status


def test_demo_ride_status_returns_running_to_match_the_canvas_fixture() -> None:
    """``ride_status()`` reports RUNNING, the console's own fixture."""
    status = DemoDataSource().ride_status()

    assert status == RideStatus.RUNNING


# ---------------------------------------------------------------- rides


def test_demo_rides_returns_the_two_library_fixture_rows() -> None:
    """``rides()`` returns both ``ride_library_dlg`` rows, verbatim."""
    rides = DemoDataSource().rides()

    assert rides == [
        RideSummary(
            name="GORBA EPIC 2026", date="2026-09-20", status=RideStatus.RUNNING, entries=180
        ),
        RideSummary(
            name="Club poker night", date="2026-06-11", status=RideStatus.FINISHED, entries=24
        ),
    ]


# --------------------------------------------------------------- riders


def test_demo_riders_returns_the_four_editor_fixture_rows() -> None:
    """``riders()`` returns ``rider_editor_dlg``'s four rows."""
    riders = DemoDataSource().riders()

    assert riders == [
        RiderRow(plate="123", name="Sam Ellis"),
        RiderRow(plate="77", name="A. Roy", team="Trail Blazers"),
        RiderRow(plate="78", name="K. Singh", team="Trail Blazers"),
        RiderRow(plate="212", name="M. Chen"),
    ]


# --------------------------------------------------------- entry detail


def test_demo_entry_detail_for_plate_77_returns_the_team_fixture() -> None:
    """``entry_detail("77")`` matches ``entry_detail_dlg``'s fixture."""
    detail = DemoDataSource().entry_detail("77")

    assert detail == EntryDetail(
        header="Team · 3 riders · 9 laps · 3:02:11",
        members="A. Roy (77) · K. Singh (78) · L. Marchetti (79)",
        cards_held=("9H", "KS", "KC", "JK", "4D"),
        laps=(
            EntryLapRow(lap=9, time="14:22:18", lap_time="19:55", rider="78", card="KC"),
            EntryLapRow(lap=8, time="14:02:23", lap_time="21:40", rider="77", card="JK"),
        ),
    )


def test_demo_entry_detail_for_plate_77_has_exactly_three_riders() -> None:
    """Entry 77 (Trail Blazers) is a 3-rider team, per the canvas."""
    detail = DemoDataSource().entry_detail("77")

    assert detail.members == "A. Roy (77) · K. Singh (78) · L. Marchetti (79)"


def test_demo_entry_detail_for_unknown_plate_raises_unknown_plate_error() -> None:
    """An unrecognized plate raises, naming the plate in the message."""
    with pytest.raises(UnknownPlateError, match=re.escape("no entry detail for plate '999'")):
        DemoDataSource().entry_detail("999")


@given(plate=st.text().filter(lambda candidate: candidate not in _KNOWN_PLATES))
def test_demo_entry_detail_raises_for_any_plate_outside_the_fixture(plate: str) -> None:
    """Property: any plate outside the fixture always raises, named."""
    expected_message = re.escape(f"no entry detail for plate {plate!r}")

    with pytest.raises(UnknownPlateError, match=expected_message):
        DemoDataSource().entry_detail(plate)


# ------------------------------------------------------------ standings


def test_demo_standings_returns_the_three_placed_fixture_rows() -> None:
    """``standings()`` returns ``results_frame``'s three rows."""
    standings = DemoDataSource().standings()

    assert standings == [
        StandingsRow(
            place=1,
            plate="77",
            entry="Trail Blazers",
            laps=9,
            total="5:44:02",
            best5=("KS", "KC", "KD", "JK", "9H"),
            hand="Four of a kind, kings",
        ),
        StandingsRow(
            place=2,
            plate="123",
            entry="Sam Ellis",
            laps=8,
            total="5:51:17",
            best5=("QH", "JH", "TH", "9H", "8H"),
            hand="Straight flush, queen-high",
        ),
        StandingsRow(
            place=3,
            plate="8",
            entry="R. Dubois",
            laps=7,
            total="5:38:44",
            best5=("AC", "AD", "AH", "4D", "4S"),
            hand="Full house, aces over fours",
        ),
    ]


# ---------------------------------------------------------------- audit


def test_demo_audit_rows_returns_the_two_fixture_rows_newest_first() -> None:
    """``audit_rows()`` returns ``audit_dlg``'s rows, newest first."""
    rows = DemoDataSource().audit_rows()

    assert rows == [
        AuditRow(
            when="14:23:02", who="scorer", action="Void crossing", entry="45", reason="mis-key"
        ),
        AuditRow(
            when="14:21:40",
            who="scorer",
            action="Manual deal 7♦",
            entry="45",
            reason="flag confirmed",
        ),
    ]


# --------------------------------------------------------- seam honesty


_NO_UI_BOOTSTRAP_PROBE = """
import sys
import rivercrossing.demo
leaked = {"wx", "rivercrossing.ui.app"} & set(sys.modules)
assert not leaked, f"rivercrossing.demo leaked bootstrap modules: {leaked}"
"""


def test_demo_module_import_does_not_load_the_ui_bootstrap_or_wx() -> None:
    """Importing rivercrossing.demo never pulls in wx or ui.app.

    demo.py must import ``rivercrossing.ui.presenters.data_source``
    to implement the ``DataSource`` Protocol it satisfies -- that
    module is itself wx-free by its own package docstring's
    contract -- but it must never reach into the wx-bearing
    bootstrap it is meant to be imported *by*. The dependency runs
    one way only: the app bootstrap (``rivercrossing.ui.app``)
    imports demo, never the reverse; this proves demo never imports
    back into it, isolated in a subprocess this test does not
    itself pollute.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _NO_UI_BOOTSTRAP_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
