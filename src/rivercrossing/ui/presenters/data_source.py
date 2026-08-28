# SPDX-License-Identifier: GPL-3.0-only
"""Display view-models and the ``DataSource`` seam (E1.2.3/E1.2.4).

Every presenter reads its screen's display data through one
``DataSource`` -- read-only, and the same shape whether it is backed
by ``rivercrossing.demo`` (E1.2.4, test-only fixture data since
E5.4.2), the real ``rivercrossing.store``-backed source, the live
``EngineDataSource``, or the ``EmptyDataSource`` empty state. The
row/view-model dataclasses below are what each window's Protocol
(``console.py``, ``riders.py``, etc.) and this seam pass back and
forth; they are deliberately shaped like the columns each window
actually draws (xrc-windows.md), not like the eventual core domain
models (``entry``, ``rider``, ...), which do not exist yet this early
in the build order (S3) and would tie this UI-only seam to modules
several EPICs away.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryType, Roster
from rivercrossing.standings import rank

if TYPE_CHECKING:
    from rivercrossing.ride import Event, RideEngine


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
    """One row of the ride library (ride_library_dlg, rides_list).

    ``ride_id`` is E5.4.1's addition: the store-backed library source
    fills it so Open/Duplicate/Delete can address the real ride row.
    Demo-era rows leave it ``None``.
    """

    name: str
    date: str
    status: RideStatus
    entries: int
    ride_id: int | None = None


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

    One seam, one shape, for every screen's real/empty data: the
    E4.4.1 ``EngineDataSource`` serves the live console; the E5.4.2
    ``EmptyDataSource`` serves the windows with no store-backed data
    yet (results, entry detail, the no-store library); and the E1.2.4
    ``DemoDataSource`` remains as test-only fixture data (importable
    from tests only since E5.4.2).
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


# =================================================== E4.4.1 live source

# R-32's "latest 20-30 crossings" -- the console feed's cap.
FEED_CAP = 30


def format_duration(seconds: float) -> str:
    """Render *seconds* as ``h:mm:ss`` (the clock / feed Total format).

    Negative values clamp to zero (the engine's ``remaining`` may go
    negative; the UI clamps for display, ride.py's own docstring).
    """
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


_SECONDS_PER_HOUR = 3600


def _format_lap_time(seconds: float) -> str:
    """Render a per-lap time as ``m:ss`` (``h:mm:ss`` past an hour)."""
    total = max(0, int(seconds))
    if total >= _SECONDS_PER_HOUR:
        return format_duration(total)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _feed_time(crossed_at: datetime) -> str:
    """Render a crossing instant as local 24-hour ``HH:MM:SS``.

    spec §13: "Times: stored UTC, displayed local 24-hour." An aware
    UTC datetime (the app's real clock) converts to local; a naive one
    (tests, RideConfig.planned_start) is displayed as stored.
    """
    local = crossed_at.astimezone() if crossed_at.tzinfo is not None else crossed_at
    return local.strftime("%H:%M:%S")


def _event_time(event: Event) -> str:
    """Render *event*'s first ISO payload timestamp, or ``""``.

    Every ride-level event payload carries at least one ISO-8601
    timestamp key (crossed_at/actual_start/stopped_at/finished_at/
    reopened_at); the audit row shows whichever applies, locally.
    """
    for key in ("crossed_at", "actual_start", "stopped_at", "finished_at", "reopened_at"):
        value = event.payload.get(key)
        if isinstance(value, str):
            try:
                return _feed_time(datetime.fromisoformat(value))
            except ValueError:
                return ""
    return ""


class EngineDataSource:
    """Real ``DataSource`` over ``(engine, roster)`` (E4.4.1).

    The console's live source: every feed row, counter and status
    derives from a ``RideEngine`` (and its roster), never from a
    display-data seam (E4.4.1's "demo wiring line unused on this
    screen" requirement; the seam itself retired in E5.4.2). The
    non-console methods (``rides``/``riders``/``entry_detail``/
    ``standings``/``audit_rows``) are implemented simply and correctly
    here for the windows that will consume them; E5/E6 replace them
    with richer store-backed versions.

    Doc-silence resolutions (pinned here, this task's own):

    - **cards_dealt counts credited cards.** ``recorded - held``: a
      held card is dealt but not credited (R-34), matching the demo
      fixture's own coherent numbers (1,124 crossings - 32 held =
      1,092 cards dealt). E7's confirm/void surface supersedes this
      reading once card disposition can change after the fact.
    - **standings ``hand`` is a short placeholder.** The human
      "Four of a kind, kings" prose is EPIC 6 display copy
      (standings.py's own docstring); until then the evaluated
      hand's ``HandClass.name`` (e.g. ``"FULL_HOUSE"``) is shown --
      clearly a code, never invented copy.
    - **entry-detail ``rider`` shows the entry's display name.**
      ``Crossing`` stores only ``entry_id``, not which team rider
      crossed (the event payload keeps the typed plate); per-rider
      attribution is E7's correction surface.
    """

    def __init__(self, engine: RideEngine, roster: Roster) -> None:
        """Build the live source over *engine* and its *roster*."""
        self._engine = engine
        self._roster = roster

    def feed_rows(self) -> list[FeedRow]:
        """Return the crossings feed, newest first, cap 30 (R-32)."""
        engine = self._engine
        held = frozenset(held.crossing for held in engine.held_crossings())
        times_by_entry: dict[str, tuple[float, ...]] = {}
        totals_by_entry: dict[str, list[float]] = {}
        for entry in self._roster.entries:
            times = engine.lap_times(entry.plate)
            times_by_entry[entry.plate] = times
            running: list[float] = []
            total = 0.0
            for lap_time in times:
                total += lap_time
                running.append(total)
            totals_by_entry[entry.plate] = running

        rows: list[FeedRow] = []
        for crossing in reversed(engine.crossings[-FEED_CAP:]):
            feed_entry = self._roster.resolve_plate(crossing.entry_id)
            times = times_by_entry.get(crossing.entry_id, ())
            totals = totals_by_entry.get(crossing.entry_id, [])
            flagged = crossing in held
            rows.append(
                FeedRow(
                    time=_feed_time(crossing.crossed_at),
                    plate=crossing.entry_id,
                    entry=feed_entry.display_name if feed_entry is not None else crossing.entry_id,
                    lap=crossing.seq,
                    lap_time=_format_lap_time(
                        times[crossing.seq - 1] if crossing.seq <= len(times) else 0.0
                    ),
                    total=format_duration(
                        totals[crossing.seq - 1] if crossing.seq <= len(totals) else 0.0
                    ),
                    card="held" if flagged else engine.card_for(crossing).code(),
                    flagged=flagged,
                )
            )
        return rows

    def counters(self) -> Counters:
        """Return the four console counter values (R-32)."""
        engine = self._engine
        held = len(engine.held_crossings())
        return Counters(
            crossings=len(engine.crossings),
            cards_dealt=len(engine.crossings) - held,
            on_course=engine.on_course,
            shoe_remaining=engine.shoe_remaining,
            shoe_total=engine.shoe_total,
        )

    def ride_status(self) -> RideStatus:
        """Return the active ride's current lifecycle status."""
        return self._engine.state

    def rides(self) -> list[RideSummary]:
        """Return the ride library rows (minimal: this one ride)."""
        config = self._engine.config
        return [
            RideSummary(
                name=config.name,
                date=config.event_date.isoformat(),
                status=self._engine.state,
                entries=len(self._roster.entries),
            )
        ]

    def riders(self) -> list[RiderRow]:
        """Return the rider editor rows for the active ride."""
        rows: list[RiderRow] = []
        for entry in self._roster.entries:
            if entry.type is EntryType.TEAM:
                rows.extend(
                    RiderRow(
                        plate=rider.plate if rider.plate is not None else entry.plate,
                        name=rider.name,
                        team=entry.display_name,
                    )
                    for rider in entry.riders
                )
            else:
                rows.append(RiderRow(plate=entry.plate, name=entry.display_name))
        return rows

    def entry_detail(self, plate: str) -> EntryDetail:
        """Return the detail view-model for the entry with ``plate``.

        Raises:
            LookupError: If no roster entry owns *plate*.
        """
        entry = self._roster.resolve_plate(plate)
        if entry is None:
            raise LookupError(f"no entry detail for plate {plate!r}")
        engine = self._engine
        times = engine.lap_times(entry.plate)
        laps = tuple(
            EntryLapRow(
                lap=crossing.seq,
                time=_feed_time(crossing.crossed_at),
                lap_time=_format_lap_time(
                    times[crossing.seq - 1] if crossing.seq <= len(times) else 0.0
                ),
                rider=entry.display_name,  # doc-silence: per-rider is E7
                card=engine.card_for(crossing).code(),
            )
            for crossing in engine.crossings
            if crossing.entry_id == entry.plate
        )
        held_cards = tuple(
            held.card.code()
            for held in engine.held_crossings()
            if held.crossing.entry_id == entry.plate
        )
        kind = "Team" if entry.type is EntryType.TEAM else "Solo"
        header = (
            f"{kind} · {len(entry.riders)} riders · {len(laps)} laps · "
            f"{format_duration(sum(times))}"
        )
        members = " · ".join(rider.name for rider in entry.riders)
        return EntryDetail(
            header=header,
            members=members,
            cards_held=held_cards,
            laps=laps,
        )

    def standings(self) -> list[StandingsRow]:
        """Return the results standings rows, in placed order."""
        placed = rank(self._engine.snapshot())
        return [
            StandingsRow(
                place=item.place,
                plate=item.result.plate,
                entry=item.result.name,
                laps=item.result.laps,
                total=format_duration(item.result.total_time),
                best5=tuple(card.code() for card in item.result.hand.best5),
                hand=item.result.hand.cls.name,  # doc-silence: prose is E6
                draw_required=item.draw_required,
            )
            for item in placed
        ]

    def audit_rows(self) -> list[AuditRow]:
        """Return the audit trail rows, newest first."""
        return [
            AuditRow(
                when=_event_time(event),
                who="scorer",
                action=event.action,
                entry=str(event.payload.get("entry_id") or event.payload.get("plate") or ""),
                reason=str(event.payload.get("reason") or ""),
            )
            for event in reversed(self._engine.events)
        ]


class EmptyDataSource:
    """The empty-state ``DataSource`` for screens with no data yet.

    E5.4.2: the demo seam's replacement on the windows E6/E7 have not
    wired to real data -- with no store-backed ride open, the ride
    library, rider editor, results, entry detail and audit screens
    must render a correct EMPTY state rather than demo rows. Every
    method returns the zero/empty value for its screen -- no rows, no
    counters, a DRAFT ride, and an empty entry detail for any plate.
    Production code (the app bootstrap wires it in ``ui.app``), not a
    test double; it satisfies ``DataSource`` exactly like
    ``DemoDataSource`` and ``EngineDataSource`` do.
    """

    def feed_rows(self) -> list[FeedRow]:
        """Return no console crossings feed rows."""
        return []

    def counters(self) -> Counters:
        """Return the zero console counters."""
        return Counters(crossings=0, cards_dealt=0, on_course=0, shoe_remaining=0, shoe_total=0)

    def ride_status(self) -> RideStatus:
        """Return DRAFT -- no ride is open."""
        return RideStatus.DRAFT

    def rides(self) -> list[RideSummary]:
        """Return no ride library rows."""
        return []

    def riders(self) -> list[RiderRow]:
        """Return no rider editor rows."""
        return []

    def entry_detail(self, plate: str) -> EntryDetail:  # noqa: ARG002 -- DataSource's signature, unused by the empty state
        """Return an empty detail view-model for any *plate*.

        The entry-detail window opens with no entry selected; the view
        renders the empty header/members/cards/laps rather than
        crashing on a plate no store-backed entry owns yet (E7 wires
        the real per-entry lookup, R-38's deep-link).
        """
        return EntryDetail(header="", members="", cards_held=(), laps=())

    def standings(self) -> list[StandingsRow]:
        """Return no results standings rows."""
        return []

    def audit_rows(self) -> list[AuditRow]:
        """Return no audit trail rows."""
        return []
