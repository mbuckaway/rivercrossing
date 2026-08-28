# SPDX-License-Identifier: GPL-3.0-only
"""The ``DataSource`` seam's hard-coded fixture data (E1.2.4).

``DemoDataSource`` implements ``rivercrossing.ui.presenters.
data_source.DataSource`` with the exact values the frozen canvas
(xrc-windows.md) shows for ``main_frame``, ``ride_library_dlg``,
``rider_editor_dlg``, ``entry_detail_dlg``, ``results_frame`` and
``audit_dlg`` -- letting D1 show a fully populated UI with no engine
and no database (project-plan.md §5, E1.2.4).

**E5.4.2 retired the seam from the app path but kept the module as
test-only fixture data.** The app bootstrap's one wiring line to this
module is gone (no production module imports ``rivercrossing.demo``
-- import-linter contract; the windows read a real engine/store
source or the ``EmptyDataSource`` empty state), and the module itself
stays because tests still import it: ``tests/functional/``'s
view-capability suites drive populated rows through
``_lists_common.demo_seeded_roster``/``DemoDataSource``, and
``tests/unit/test_demo.py`` pins its values. E1.2.4's removable seam
is therefore retired from the app path, not deleted; deleting the
module later is a test-only change. Do not grow real business logic
here -- if a change starts to look like anything other than constant
fixture data, it belongs somewhere else.
"""

from typing import TYPE_CHECKING

from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters.data_source import (
    AuditRow,
    Counters,
    EntryDetail,
    EntryLapRow,
    FeedRow,
    RiderRow,
    RideSummary,
    StandingsRow,
)

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource

# ----------------------------------------------------------- console

_FEED_ROWS: tuple[FeedRow, ...] = (
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
)

_COUNTERS = Counters(
    crossings=1124,
    cards_dealt=1092,
    on_course=42,
    shoe_remaining=41,
    shoe_total=108,
)

# ------------------------------------------------------------- rides

_RIDES: tuple[RideSummary, ...] = (
    RideSummary(
        name="GORBA EPIC 2026",
        date="2026-09-20",
        status=RideStatus.RUNNING,
        entries=180,
    ),
    RideSummary(
        name="Club poker night",
        date="2026-06-11",
        status=RideStatus.FINISHED,
        entries=24,
    ),
)

# ------------------------------------------------------------ riders

_RIDERS: tuple[RiderRow, ...] = (
    RiderRow(plate="123", name="Sam Ellis"),
    RiderRow(plate="77", name="A. Roy", team="Trail Blazers"),
    RiderRow(plate="78", name="K. Singh", team="Trail Blazers"),
    RiderRow(plate="212", name="M. Chen"),
)

# ----------------------------------------------------- entry detail

# xrc-windows.md's entry_detail_dlg cards_list shows "Cards held (9)"
# but truncates the row to "9H KS KC JK 4D..."; the remaining four
# cards are not legible in the canvas and are not invented here.
_ENTRY_DETAILS: dict[str, EntryDetail] = {
    "77": EntryDetail(
        header="Team · 3 riders · 9 laps · 3:02:11",
        members="A. Roy (77) · K. Singh (78) · L. Marchetti (79)",
        cards_held=("9H", "KS", "KC", "JK", "4D"),
        laps=(
            EntryLapRow(lap=9, time="14:22:18", lap_time="19:55", rider="78", card="KC"),
            EntryLapRow(lap=8, time="14:02:23", lap_time="21:40", rider="77", card="JK"),
        ),
    ),
}


class UnknownPlateError(LookupError):
    """Raised by ``entry_detail`` for a plate with no fixture data."""


# -------------------------------------------------------- standings

_STANDINGS: tuple[StandingsRow, ...] = (
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
)

# ------------------------------------------------------------- audit

_AUDIT_ROWS: tuple[AuditRow, ...] = (
    AuditRow(
        when="14:23:02",
        who="scorer",
        action="Void crossing",
        entry="45",
        reason="mis-key",
    ),
    AuditRow(
        when="14:21:40",
        who="scorer",
        action="Manual deal 7♦",
        entry="45",
        reason="flag confirmed",
    ),
)


class DemoDataSource:
    """Fixture ``DataSource`` for D1's engine-free, database-free UI.

    Every method returns the same constant rows every time -- there
    is nothing here to configure or vary, which is exactly what
    makes this seam removable in EPIC 5 with no ripple effect.
    """

    def feed_rows(self) -> list[FeedRow]:
        """Return the console crossings feed, newest first."""
        return list(_FEED_ROWS)

    def counters(self) -> Counters:
        """Return the four console counter values."""
        return _COUNTERS

    def ride_status(self) -> RideStatus:
        """Return the active ride's current lifecycle status.

        RUNNING -- the console canvas's fixture ride is mid-run.
        """
        return RideStatus.RUNNING

    def rides(self) -> list[RideSummary]:
        """Return the ride library rows."""
        return list(_RIDES)

    def riders(self) -> list[RiderRow]:
        """Return the rider editor rows for the active ride."""
        return list(_RIDERS)

    def entry_detail(self, plate: str) -> EntryDetail:
        """Return the detail view-model for the entry with ``plate``.

        Raises:
            UnknownPlateError: If no fixture entry has ``plate``.
        """
        detail = _ENTRY_DETAILS.get(plate)
        if detail is None:
            raise UnknownPlateError(f"no entry detail for plate {plate!r}")
        return detail

    def standings(self) -> list[StandingsRow]:
        """Return the results standings rows, in placed order."""
        return list(_STANDINGS)

    def audit_rows(self) -> list[AuditRow]:
        """Return the audit trail rows, newest first."""
        return list(_AUDIT_ROWS)


if TYPE_CHECKING:
    # Static-only proof that DemoDataSource satisfies DataSource:
    # mypy evaluates this assignment: an incompatible signature
    # above would fail typecheck even though this line never runs.
    _conforms_to_data_source: DataSource = DemoDataSource()
