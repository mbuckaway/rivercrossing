# SPDX-License-Identifier: GPL-3.0-only
"""Display view-models and the ``DataSource`` seam (E1.2.3/E1.2.4).

Every presenter reads its screen's display data through one
``DataSource`` -- read-only, and the same shape whether it is backed
by ``rivercrossing.demo`` (E1.2.4, fixture data) or the real
``rivercrossing.store``-backed source that replaces it in EPICs 4-5
(module-skeletons.md ownership table). The row/view-model dataclasses
below are what each window's Protocol (``console.py``, ``riders.py``,
etc.) and this seam pass back and forth; they are deliberately shaped
like the columns each window actually draws (xrc-windows.md), not
like the eventual core domain models (``entry``, ``rider``, ...),
which do not exist yet this early in the build order (S3) and would
tie this UI-only seam to modules several EPICs away.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ride import RideStatus


@dataclass(frozen=True, slots=True)
class FeedRow:
    """One row of the console crossings feed (main_frame's list).

    ``flagged`` drives the bold per-row attribute xrc-windows.md calls
    out as code-side for a held/short-lap crossing.
    """

    time: str
    plate: str
    entry: str
    lap: int
    lap_time: str
    total: str
    card: str
    flagged: bool = False


@dataclass(frozen=True, slots=True)
class Counters:
    """The four console counter chips (crossings/cards/course/shoe)."""

    crossings: int
    cards_dealt: int
    on_course: int
    shoe_remaining: int
    shoe_total: int


@dataclass(frozen=True, slots=True)
class RideSummary:
    """One row of the ride library (ride_library_dlg, rides_list)."""

    name: str
    date: str
    status: RideStatus
    entries: int


@dataclass(frozen=True, slots=True)
class RiderRow:
    """One row of the rider editor (rider_editor_dlg, riders_list).

    ``team`` is ``None`` for a solo rider; the Team column is hidden
    entirely in solo-only rides (a view concern, not this row's).
    """

    plate: str
    name: str
    team: str | None = None


@dataclass(frozen=True, slots=True)
class EntryLapRow:
    """One row of an entry's lap history (entry_detail_dlg's list)."""

    lap: int
    time: str
    lap_time: str
    rider: str
    card: str


@dataclass(frozen=True, slots=True)
class EntryDetail:
    """The entry detail view-model (entry_detail_dlg, 1e).

    ``header``/``members`` are the two pre-rendered summary lines
    (entry_header_lbl/members_lbl); ``cards_held`` is the
    cards_list's icon-mode row content.
    """

    header: str
    members: str
    cards_held: tuple[str, ...]
    laps: tuple[EntryLapRow, ...]


@dataclass(frozen=True, slots=True)
class StandingsRow:
    """One row of the results standings (results_frame, standings_list).

    ``draw_required`` backs the ⚠ badge column xrc-windows.md calls
    out as code-side for byte-identical tied hands (Spec §5).
    """

    place: int
    plate: str
    entry: str
    laps: int
    total: str
    best5: tuple[str, ...]
    hand: str
    draw_required: bool = False


@dataclass(frozen=True, slots=True)
class AuditRow:
    """One row of the audit trail (audit_dlg, audit_list, R-38)."""

    when: str
    who: str
    action: str
    entry: str
    reason: str


@runtime_checkable
class DataSource(Protocol):
    """Read-only display data feeding every presenter.

    One seam, one shape, for every screen's fixture/real data: the
    E1.2.4 ``DemoDataSource`` implements this against the canvas's
    hard-coded rows; a store-backed implementation replaces it,
    unchanged from every presenter's point of view, in EPICs 4-5.
    """

    def feed_rows(self) -> list[FeedRow]:
        """Return the console crossings feed, newest first, cap 30."""
        ...

    def counters(self) -> Counters:
        """Return the four console counter values."""
        ...

    def ride_status(self) -> RideStatus:
        """Return the active ride's current lifecycle status."""
        ...

    def rides(self) -> list[RideSummary]:
        """Return the ride library rows."""
        ...

    def riders(self) -> list[RiderRow]:
        """Return the rider editor rows for the active ride."""
        ...

    def entry_detail(self, plate: str) -> EntryDetail:
        """Return the detail view-model for the entry with ``plate``."""
        ...

    def standings(self) -> list[StandingsRow]:
        """Return the results standings rows, in placed order."""
        ...

    def audit_rows(self) -> list[AuditRow]:
        """Return the audit trail rows, newest first."""
        ...
