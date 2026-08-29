# SPDX-License-Identifier: GPL-3.0-only
"""Detail presenter -- entry_detail_dlg (1e) and edit_crossing_dlg (7b).

Pure Python -- no ``wx`` import may ever land here (R-71); the
presenter delegates every dialog-open to the ``DetailView``, which
returns the confirmed submission as one of the frozen request
dataclasses below (or ``None`` on cancel), and the presenter then
calls the matching ``RideEngine`` / ``Roster`` command.

E7.2.1 wires ``entry_detail_dlg``'s six action buttons through this
class (replacing the Phase-5 no-op):

- **Edit crossing…** opens ``edit_crossing_dlg`` in edit mode
  (prefilled plate + current time; ``void_btn`` shown only there). A
  confirmed Save calls :meth:`RideEngine.edit_crossing`; the dialog's
  ``void_btn`` calls :meth:`RideEngine.void_crossing`.
- **Deal card…** opens ``manual_deal_dlg`` and calls
  :meth:`RideEngine.deal_manual` on confirm.
- **Void card…** opens ``void_card_confirm_dlg`` (naming the selected
  lap's dealt card + entry) and calls :meth:`RideEngine.void_card` --
  dealt cards only (held cards stay the review surface's domain).
- **Mark DNF…** opens ``dnf_confirm_dlg`` (naming the entry) and calls
  :meth:`RideEngine.mark_dnf` on confirm.
- **Move rider…** (pooled team entries only) opens the team picker and
  calls :meth:`Roster.move_rider` on confirm.
- **Audit trail** opens ``audit_dlg`` plain -- the viewer + pre-filter
  are E7.3.1's scope; this only wires the button so it opens.

Every engine refusal (wrong ride state, unknown plate, empty reason,
closed shoe, locked move) is caught and surfaced through the view's
notice channel, never a crash -- the same ``ConsolePresenter``
discipline. Each successful command re-renders the entry detail
(:meth:`refresh`) and fires the optional ``on_corrected`` hook the app
bootstrap wires to its live menu binder.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.cards import Card, ShoeClosedError
from rivercrossing.ride import IllegalStateError, UnknownPlateError
from rivercrossing.roster import (
    EntryNotFoundError,
    EntryType,
    InvalidMoveError,
    LockedError,
    PlateModel,
    RiderNotFoundError,
)
from rivercrossing.ui.presenters.data_source import EntryDetail

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import DataSource, EntryLapRow

__all__ = [
    "CardVoid",
    "CrossingEdit",
    "DetailPresenter",
    "DetailView",
    "DnfMark",
    "ManualDeal",
    "RiderMove",
]


@dataclass(frozen=True, slots=True)
class CrossingEdit:
    """One confirmed ``edit_crossing_dlg`` submission (E7.2.1).

    ``entry_id`` is the confirmed plate (the operator may have changed
    the prefill), ``seq`` the crossing's 1-based lap number within the
    entry (or ``None`` when the caller resolves it from the engine's
    latest crossing -- the menu flow), ``crossed_at`` the confirmed
    instant, ``reason`` the audit reason, and ``void`` True when the
    operator chose the dialog's ``void_btn`` instead of Save.
    """

    entry_id: str
    seq: int | None
    crossed_at: datetime | None
    reason: str
    void: bool = False


@dataclass(frozen=True, slots=True)
class ManualDeal:
    """One confirmed ``manual_deal_dlg`` submission (E7.2.1)."""

    plate: str
    reason: str


@dataclass(frozen=True, slots=True)
class CardVoid:
    """One confirmed ``void_card_confirm_dlg`` submission (E7.2.1).

    ``card`` is the dealt card's code (``Card.code()``); the caller
    parses it back with :meth:`Card.parse` when it calls the engine.
    """

    entry_id: str
    card: str
    reason: str


@dataclass(frozen=True, slots=True)
class DnfMark:
    """One confirmed ``dnf_confirm_dlg`` submission (E7.2.1)."""

    entry_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RiderMove:
    """One confirmed move-rider picker submission (E7.2.1).

    ``rider_plate`` names the rider being moved (a ``Rider.plate`` on
    a rider_pooled team); ``to_team`` names the destination entry by
    ``display_name``. The presenter resolves both through the roster
    before calling :meth:`Roster.move_rider`.
    """

    rider_plate: str
    to_team: str


@runtime_checkable
class DetailView(Protocol):
    """View surface for the entry detail dialog (entry_detail_dlg)."""

    def show_entry(self, detail: EntryDetail) -> None:
        """Render the header, members, cards held and laps_list."""
        ...

    def set_move_rider_enabled(self, *, enabled: bool) -> None:
        """Enable move_rider_btn only for rider-pooled team entries."""
        ...

    def selected_lap(self) -> EntryLapRow | None:
        """Return the laps_list row the operator selected, if any.

        ``None`` when no lap row is selected -- the edit/void buttons
        need one concrete crossing to act on.
        """
        ...

    def show_edit_crossing(self, *, adding: bool, plate: str, time: str) -> CrossingEdit | None:
        """Open edit_crossing_dlg prefilled; return the confirmed edit.

        ``adding`` False titles it "Edit Crossing" (void_btn shown),
        True "Add Crossing at Time" (void_btn hidden). Returns the
        confirmed :class:`CrossingEdit`, or ``None`` on cancel.
        """
        ...

    def open_manual_deal(self, *, plate: str) -> ManualDeal | None:
        """Open manual_deal_dlg; return the confirmed deal, or None."""
        ...

    def open_void_card(self, *, card: str, entry: str) -> CardVoid | None:
        """Open void_card_confirm_dlg naming the card + entry.

        *card* is the dealt card's code; *entry* the human entry label
        the confirm's ``card_lbl`` shows (never blank). Returns the
        confirmed :class:`CardVoid`, or ``None`` on cancel.
        """
        ...

    def open_dnf(self, *, entry: str) -> DnfMark | None:
        """Open dnf_confirm_dlg naming the entry; return the confirm."""
        ...

    def open_move_rider(
        self, *, riders: tuple[str, ...], teams: tuple[str, ...]
    ) -> RiderMove | None:
        """Open the team picker; return the confirmed move.

        *riders* is the current entry's rider plates, *teams* the other
        entries' display names. Returns :class:`RiderMove`, or ``None``
        on cancel.
        """
        ...

    def open_audit(self) -> None:
        """Open audit_dlg (plain for now; the pre-filter is E7.3.1)."""
        ...

    def show_notice(self, text: str) -> None:
        """Show a transient notice (the main frame's status bar)."""
        ...


class DetailPresenter:
    """Presenter for the entry detail and edit crossing dialogs.

    Holds ``(view, data_source, plate, engine, roster)``: the view
    renders and opens dialogs, the data source feeds the read-only
    detail, ``plate`` is the entry this dialog is showing, and the
    engine/roster own the write side (corrections / pooled moves).
    ``clock`` is the presenter's own wall-clock seam for the "current
    time" prefill -- injected in tests, defaulting to ``datetime.now``.
    ``on_corrected`` fires after every successful correction so the
    app bootstrap can refresh the live menu enablement.
    """

    def __init__(  # noqa: PLR0913 -- (view, source, plate) + the four optional seams
        self,
        view: DetailView,
        data_source: DataSource,
        *,
        plate: str = "",
        engine: RideEngine | None = None,
        roster: Roster | None = None,
        clock: Callable[[], datetime] | None = None,
        on_corrected: Callable[[], None] | None = None,
    ) -> None:
        """Store the view, data source and seams this presenter drives.

        Args:
            view: The entry-detail view to render into and ask for
                dialog submissions.
            data_source: The read-only display-data seam.
            plate: The entry this dialog is showing; corrections target
                it and its selected laps.
            engine: The ride engine (the corrections write side);
                ``None`` when no live ride is open, in which case the
                correction buttons post a notice instead of acting.
            roster: The live roster (the pooled-move write side and the
                entry-label source); ``None`` without a live ride.
            clock: Wall-clock source for the "current time" prefill;
                defaults to ``datetime.now``.
            on_corrected: Optional hook fired after each successful
                engine/roster command (the app wires its menu binder).
        """
        self.view = view
        self.data_source = data_source
        self.plate = plate
        self.engine = engine
        self.roster = roster
        self._clock = clock if clock is not None else datetime.now
        self._on_corrected = on_corrected

    # ------------------------------------------------------- helpers

    def _current_time(self) -> str:
        """Return the clock's current time as ``HH:MM:SS``."""
        return self._clock().strftime("%H:%M:%S")

    def _entry_label(self) -> str:
        """Return this entry's human confirm label (``plate · name``).

        ``roster.resolve_plate`` resolves a rider's plate to its
        owning entry, so the label names the team for a pooled rider.
        """
        roster = self.roster
        entry = roster.resolve_plate(self.plate) if roster is not None else None
        name = entry.display_name if entry is not None else self.plate
        return f"{self.plate} · {name}"

    def move_rider_enabled(self) -> bool:
        """Return whether move_rider_btn should be enabled.

        Pooled-only (spec §15, R-17): a rider_pooled team entry's
        riders may move; solo entries (one fixed rider) and team-relay
        rides (the plate is the team's identity) never may.
        """
        roster = self.roster
        if roster is None:
            return False
        entry = roster.resolve_plate(self.plate)
        if entry is None:
            return False
        return roster.plate_model is PlateModel.RIDER_POOLED and entry.type is EntryType.TEAM

    def refresh(self) -> None:
        """Re-render the entry detail and the move-rider enablement.

        The data source may no longer resolve this entry after a move
        dissolved it; the empty view-model stands in rather than crash.
        """
        try:
            detail = self.data_source.entry_detail(self.plate)
        except LookupError:
            detail = EntryDetail(header="", members="", cards_held=(), laps=())
        self.view.show_entry(detail)
        self.view.set_move_rider_enabled(enabled=self.move_rider_enabled())

    def _corrected(self) -> None:
        """Re-render and fire the app-level on_corrected hook."""
        self.refresh()
        if self._on_corrected is not None:
            self._on_corrected()

    # ------------------------------------------------------- actions

    def on_edit_crossing_clicked(self) -> None:
        """Handle edit_crossing_btn: edit mode, then edit or void.

        A selected laps_list row supplies the crossing identity; the
        dialog prefills this entry's plate and the current time and
        shows ``void_btn``. A confirmed Save calls
        ``engine.edit_crossing``; the void choice calls
        ``engine.void_crossing``.
        """
        if self.engine is None:
            self.view.show_notice("No live ride to correct")
            return
        lap = self.view.selected_lap()
        if lap is None:
            self.view.show_notice("Select a lap to edit")
            return
        edit = self.view.show_edit_crossing(
            adding=False, plate=self.plate, time=self._current_time()
        )
        if edit is None or edit.seq is None:
            return
        if edit.void:
            try:
                self.engine.void_crossing(edit.entry_id, edit.seq, edit.reason)
            except (IllegalStateError, ValueError) as exc:
                self.view.show_notice(f"Cannot void crossing: {exc}")
                return
            self.view.show_notice("Crossing voided")
        else:
            if edit.crossed_at is None:
                # logic-coverage-exempt: T-3 -- the corrections runner
                # always sets crossed_at on a non-void commit; the
                # guard narrows the optional type for mypy.
                return
            try:
                self.engine.edit_crossing(edit.entry_id, edit.seq, edit.crossed_at, edit.reason)
            except (IllegalStateError, ValueError) as exc:
                self.view.show_notice(f"Cannot edit crossing: {exc}")
                return
            self.view.show_notice("Crossing edited")
        self._corrected()

    def on_deal_card_clicked(self) -> None:
        """Handle deal_card_btn: manual_deal_dlg, then deal_manual."""
        if self.engine is None:
            self.view.show_notice("No live ride to correct")
            return
        deal = self.view.open_manual_deal(plate=self.plate)
        if deal is None:
            return
        try:
            self.engine.deal_manual(deal.plate, deal.reason)
        except (IllegalStateError, UnknownPlateError, ValueError, ShoeClosedError) as exc:
            self.view.show_notice(f"Cannot deal card: {exc}")
            return
        self.view.show_notice("Card dealt")
        self._corrected()

    def on_void_card_clicked(self) -> None:
        """Handle void_card_btn: void the selected lap's dealt card.

        The confirm names the card and entry (never blank); only a
        dealt (credited) card is voidable -- a held card stays the
        review surface's domain (``engine.void_card`` refuses it).
        """
        if self.engine is None:
            self.view.show_notice("No live ride to correct")
            return
        lap = self.view.selected_lap()
        if lap is None:
            self.view.show_notice("Select a lap to void")
            return
        void = self.view.open_void_card(card=lap.card, entry=self._entry_label())
        if void is None:
            return
        try:
            self.engine.void_card(void.entry_id, Card.parse(void.card), void.reason)
        except (IllegalStateError, UnknownPlateError, ValueError) as exc:
            self.view.show_notice(f"Cannot void card: {exc}")
            return
        self.view.show_notice("Card voided")
        self._corrected()

    def on_dnf_clicked(self) -> None:
        """Handle dnf_btn: confirm naming the entry, then mark."""
        if self.engine is None:
            self.view.show_notice("No live ride to correct")
            return
        dnf = self.view.open_dnf(entry=self._entry_label())
        if dnf is None:
            return
        try:
            self.engine.mark_dnf(dnf.entry_id, dnf.reason)
        except (IllegalStateError, UnknownPlateError, ValueError) as exc:
            self.view.show_notice(f"Cannot mark DNF: {exc}")
            return
        self.view.show_notice("Entry marked DNF")
        self._corrected()

    def on_move_rider_clicked(self) -> None:
        """Handle move_rider_btn: the team picker, then the pooled move.

        Only reachable when :meth:`move_rider_enabled` is true (the view
        gates the button); the roster performs the move and the entry
        detail re-renders (a move that dissolves this entry falls back
        to the empty detail in :meth:`refresh`).
        """
        if self.roster is None:
            self.view.show_notice("No live ride to move riders in")
            return
        entry = self.roster.resolve_plate(self.plate)
        if entry is None or entry.type is not EntryType.TEAM:
            self.view.show_notice("Move rider is for pooled team entries")
            return
        riders = tuple(rider.plate or "" for rider in entry.riders)
        teams = tuple(other.display_name for other in self.roster.entries if other is not entry)
        move = self.view.open_move_rider(riders=riders, teams=teams)
        if move is None:
            return
        rider = next((r for r in entry.riders if r.plate == move.rider_plate), None)
        if rider is None:
            self.view.show_notice(f"No rider with plate {move.rider_plate}")
            return
        to_entry = next(
            (other for other in self.roster.entries if other.display_name == move.to_team),
            None,
        )
        if to_entry is None:
            self.view.show_notice(f"No team named {move.to_team}")
            return
        try:
            self.roster.move_rider(rider, to_entry=to_entry)
        except (LockedError, InvalidMoveError, RiderNotFoundError, EntryNotFoundError) as exc:
            self.view.show_notice(f"Cannot move rider: {exc}")
            return
        self.view.show_notice("Rider moved")
        self._corrected()

    def on_audit_clicked(self) -> None:
        """Handle audit_btn: open audit_dlg plain (viewer is E7.3.1)."""
        self.view.open_audit()
