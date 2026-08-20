# SPDX-License-Identifier: GPL-3.0-only
"""Riders presenter -- rider_editor_dlg (1d/2b) + csv_preview_dlg (3e).

``RidersPresenter`` drives ``rider_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` (E3.1.1/E3.1.2) -- unlike every
other presenter in this package, it takes no ``DataSource``, since
the editor reads and writes the roster itself rather than a
display-only projection of it. This replaces the earlier no-op
``(view, data_source)`` shape (E1.2.3) for this presenter only.

The 2026-08-09 follow-on decision lets Add/Save build teams one
rider at a time: "New team..." calls
:meth:`~rivercrossing.roster.Roster.create_team_entry_of_one` (a
transient size-1 team, DRAFT-only, R-12's floor deferred to start
time). Joining an *existing* team composes the same primitive with
:meth:`~rivercrossing.roster.Roster.move_rider` -- not
``create_solo_entry`` + ``move_rider`` as first proposed:
``move_rider`` rejects a solo entry on either side unconditionally
(``tests/unit/test_roster.py``'s
``test_move_rider_into_a_solo_entry_raises_invalid_move_error`` and
its two siblings), so the transient must itself be type TEAM. A
refused join rolls the transient team back with ``delete_entry`` so
the roster stays truly unchanged.

E3.4 extends the same class with csv_preview_dlg's own three entry
points (``on_pick_csv_import``/``on_confirm_csv_import``/
``on_export_csv``), rather than a second presenter class: the brief's
own "csv_preview_dlg wired" scope shares one roster and one lock
matrix with the rider editor, and ``RidersView`` already carried
``show_csv_preview``/``set_import_enabled`` from E1.2.3 for exactly
this. csv_preview_dlg's own view (``ui.views.rider_editor.
CsvPreviewDialog``) pairs with a *second* ``RidersPresenter``
instance over the same live roster, constructed with ``load=False``
(this class's own ``__init__`` docstring) -- it never implements
the rider-editor half of ``RidersView`` for real, the mirror image
of ``RiderEditor``'s own E3.2-era CSV stubs.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from rivercrossing import csvio
from rivercrossing.roster import (
    EntryMode,
    EntryType,
    LockedError,
    PlateModel,
    Rider,
    RosterError,
    can_delete_entry,
)
from rivercrossing.ui.presenters.data_source import RiderRow

if TYPE_CHECKING:
    from pathlib import Path

    from rivercrossing.roster import Entry, Roster

# team_choice's two frozen sentinel entries (xrc-windows.md's Rider
# Editor mock: "-- solo --" first, team display names, "New team..."
# last).
SOLO_TEAM_CHOICE = "— solo —"
NEW_TEAM_CHOICE = "New team…"


@dataclass(frozen=True, slots=True)
class CsvConflict:
    """One conflict row in the CSV import preview (csv_preview_dlg)."""

    row: int
    problem: str


@dataclass(frozen=True, slots=True)
class CsvPreview:
    """The CSV import preview view-model (csv_preview_dlg's summary)."""

    summary: str
    conflicts: tuple[CsvConflict, ...]


@dataclass(frozen=True, slots=True)
class RiderFormValues:
    """The rider editor's form fields, forwarded by the view (R-20).

    ``team`` is always one of team_choice's literal current
    contents: :data:`SOLO_TEAM_CHOICE`, an existing team's display
    name, or :data:`NEW_TEAM_CHOICE` -- the view forwards whatever
    the control currently holds verbatim, never translating it
    (passive view).
    """

    plate: str
    name: str
    team: str


@runtime_checkable
class RidersView(Protocol):
    """View surface for the rider editor and its CSV import preview."""

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render riders_list."""
        ...

    def show_team_choices(self, names: list[str]) -> None:
        """Replace team_choice's content with *names*, in order."""
        ...

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Disable delete_btn once the entry has data (R-15)."""
        ...

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Render csv_preview_dlg's summary line and conflicts."""
        ...

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate wxID_OK "Import" while conflicts > 0."""
        ...

    def show_form(self, *, plate: str, name: str, team: str) -> None:
        """Fill plate_input/name_input/team_choice (add or select)."""
        ...

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide team_choice + the Team column (R-11, solo-only)."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-operation message (roster_infobar, later)."""
        ...

    def prompt_new_team_name(self) -> str | None:
        """Ask for a new team's name; None if the operator cancels."""
        ...


def _rider_pairs(roster: Roster) -> list[tuple[Entry, Rider]]:
    """Return every (entry, rider) pair, in riders_list's row order."""
    return [(entry, rider) for entry in roster.entries for rider in entry.riders]


def _rider_plate(roster: Roster, entry: Entry, rider: Rider) -> str:
    """Return one row's Plate column for the ride's plate_model (R-20).

    team_relay riders carry no plate of their own -- the whole team
    shares the entry's; rider_pooled riders each carry their own.
    """
    if roster.plate_model is PlateModel.TEAM_RELAY:
        return entry.plate
    return cast("str", rider.plate)


def _rider_rows(roster: Roster) -> list[RiderRow]:
    """Map every roster rider onto one riders_list row (R-20)."""
    return [
        RiderRow(
            plate=_rider_plate(roster, entry, rider),
            name=rider.name,
            team=entry.display_name if entry.type is EntryType.TEAM else None,
        )
        for entry, rider in _rider_pairs(roster)
    ]


def _team_choices(roster: Roster) -> list[str]:
    """Return team_choice's content: solo, every team, then new-team."""
    names = [entry.display_name for entry in roster.entries if entry.type is EntryType.TEAM]
    return [SOLO_TEAM_CHOICE, *names, NEW_TEAM_CHOICE]


class RidersPresenter:
    """Presenter for the rider editor (rider_editor_dlg, R-11/15/20).

    See the module docstring for how team growth composes from
    :class:`~rivercrossing.roster.Roster`'s shipped primitives; a relay
    team member's plate change on Save routes through
    ``_apply_plate_change`` → ``Roster.change_team_plate`` (covered by
    ``test_on_save_given_a_relay_team_member_changes_the_teams_plate``).
    """

    def __init__(self, view: RidersView, roster: Roster, *, load: bool = True) -> None:
        """Store the view and roster this presenter drives, and load.

        Args:
            view: The rider-editor or csv-preview view driving this
                roster (module docstring).
            roster: The in-memory roster this presenter reads/writes.
            load: Renders rider_editor_dlg's own initial state
                (``_load()``) when ``True`` (the default, unchanged
                for every existing caller). ``CsvPreviewDialog``
                passes ``False``: its view never implements
                ``show_riders``/``show_team_choices``/
                ``set_team_ui_visible``/``show_form``/
                ``set_delete_enabled`` for real, so nothing may call
                them.
        """
        self.view = view
        self.roster = roster
        self._selected: tuple[Entry, Rider] | None = None
        self._csv_preview: csvio.ImportPreview | None = None
        if load:
            self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from riders_list row *index* (R-20)."""
        entry, rider = _rider_pairs(self.roster)[index]
        self._selected = (entry, rider)
        team = entry.display_name if entry.type is EntryType.TEAM else SOLO_TEAM_CHOICE
        self.view.show_form(
            plate=_rider_plate(self.roster, entry, rider), name=rider.name, team=team
        )
        self.view.set_delete_enabled(
            enabled=can_delete_entry(self.roster.status, has_data=entry.has_data)
        )

    def on_add(self, form: RiderFormValues) -> None:
        """Handle add_btn: create an entry from the form's values.

        A refusal (duplicate plate, a team already at max size, ...)
        shows via :meth:`RidersView.show_validation` and leaves the
        roster unchanged, never raising past this handler. A
        cancelled "New team..." prompt is a no-op: nothing created.
        """
        try:
            created = self._create_entry(form)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        if not created:
            return
        self._refresh_rows()
        self._show_add_form()

    def on_save(self, form: RiderFormValues) -> None:
        """Handle save_btn: rename and/or replate the selected rider.

        A refusal (duplicate plate, the ride has left DRAFT, ...)
        shows via :meth:`RidersView.show_validation` and leaves the
        roster unchanged, never raising past this handler. A no-op
        if nothing is selected.
        """
        if self._selected is None:
            return
        entry, rider = self._selected
        try:
            self._apply_plate_change(entry, rider, form.plate)
            if entry.type is EntryType.SOLO:
                self.roster.update_entry(entry, display_name=form.name)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        rider.name = form.name
        self._refresh_rows()

    def on_delete(self) -> None:
        """Handle delete_btn: remove the selected entry (R-15).

        A refusal (recorded data, or the ride has left DRAFT) shows
        via :meth:`RidersView.show_validation`, naming the reason,
        and never raises past this handler. A no-op if nothing is
        selected.
        """
        if self._selected is None:
            return
        entry, _rider = self._selected
        try:
            self.roster.delete_entry(entry)
        except LockedError as exc:
            self.view.show_validation(str(exc))
            return
        self._refresh_rows()
        self._show_add_form()

    def on_pick_csv_import(self, path: Path) -> None:
        """Preview *path* against this roster; render it (E3.4, R-21).

        Nothing is written -- :func:`~rivercrossing.csvio.preview`'s
        own contract. An unreadable *path* propagates as ``OSError``
        (csvio's own module docstring); the view's own picker seam
        (a real ``wx.FD_FILE_MUST_EXIST`` file dialog) already
        guards against that in practice, so this handler does not
        catch it.
        """
        self._csv_preview = csvio.preview(path, self.roster)
        conflicts = tuple(
            CsvConflict(row=conflict.row, problem=conflict.problem)
            for conflict in self._csv_preview.conflicts
        )
        summary = (
            f"{path.name} → {self._csv_preview.rider_count} riders · "
            f"{self._csv_preview.team_count} teams · {len(conflicts)} conflicts"
        )
        self.view.show_csv_preview(CsvPreview(summary=summary, conflicts=conflicts))
        self.view.set_import_enabled(enabled=len(conflicts) == 0)

    def on_confirm_csv_import(self) -> bool:
        """Commit the last previewed import (E3.4, R-21).

        A no-op returning ``False`` if nothing was ever previewed.
        A refusal (the roster changed since preview, so conflicts
        are present after all) shows via
        :meth:`RidersView.show_validation` and returns ``False``,
        never raising past this handler -- mirroring
        :meth:`on_add`/:meth:`on_save`/:meth:`on_delete`'s own
        refusal shape. Returns ``True`` once the commit actually
        applied, so :class:`~rivercrossing.ui.views.rider_editor.
        CsvPreviewDialog` knows whether to end its own modal loop.

        Never re-renders ``riders_list``/``team_choice`` on success:
        this method's only real caller, ``CsvPreviewDialog``, never
        implements those ``RidersView`` members (module docstring's
        mirror-image split) -- a live ``RiderEditor`` sees the
        imported roster next time it is (re)opened,
        :meth:`__init__` reading it fresh.

        Returns:
            Whether the import actually committed.
        """
        if self._csv_preview is None:
            return False
        try:
            csvio.commit(self._csv_preview)
        except csvio.ImportConflictsPresentError as exc:
            self.view.show_validation(str(exc))
            return False
        return True

    def on_export_csv(self, path: Path) -> None:
        """Export this roster to *path* as CSV (E3.4, R-21)."""
        csvio.export(self.roster, path)

    def refresh(self) -> None:
        """Re-render riders_list/team_choice from the roster (E3.4).

        A public counterpart to :meth:`_refresh_rows`: the one entry
        point a caller outside this presenter uses to catch this
        view up with a roster change it never itself made --
        ``RiderEditor``'s own ``import_btn`` handler calls this after
        a *different* ``RidersPresenter`` instance (``csv_preview_
        dlg``'s own, ``load=False``) commits a CSV import into the
        same roster.
        """
        self._refresh_rows()

    def _create_entry(self, form: RiderFormValues) -> bool:
        """Create *form*'s entry; False if new-team prompt cancels."""
        if form.team == SOLO_TEAM_CHOICE:
            self.roster.create_solo_entry(name=form.name, plate=form.plate)
            return True
        if form.team == NEW_TEAM_CHOICE:
            new_name = self.view.prompt_new_team_name()
            if new_name is None:
                return False
            self._create_new_team(form, new_name)
            return True
        self._join_existing_team(form)
        return True

    def _create_new_team(self, form: RiderFormValues, team_name: str) -> None:
        """Create a transient size-1 team named *team_name* (R-12)."""
        rider = Rider(name=form.name, plate=form.plate)
        entry_plate = form.plate if self.roster.plate_model is PlateModel.TEAM_RELAY else None
        self.roster.create_team_entry_of_one(
            display_name=team_name, rider=rider, plate=entry_plate
        )

    def _join_existing_team(self, form: RiderFormValues) -> None:
        """Fold a new rider onto the existing team named *form.team*.

        Composed from shipped Roster primitives: a transient size-1
        team is created, then folded in via move_rider (see the
        module docstring). A refused fold-in rolls the transient
        back so the roster stays unchanged.
        """
        target = self._find_team_entry(form.team)
        rider = Rider(name=form.name, plate=form.plate)
        entry_plate = form.plate if self.roster.plate_model is PlateModel.TEAM_RELAY else None
        transient = self.roster.create_team_entry_of_one(
            display_name=form.team, rider=rider, plate=entry_plate
        )
        try:
            self.roster.move_rider(rider, to_entry=target)
        except RosterError:
            self.roster.delete_entry(transient)
            raise

    def _find_team_entry(self, display_name: str) -> Entry:
        """Return the TEAM entry named *display_name* (on_add join)."""
        return next(
            entry
            for entry in self.roster.entries
            if entry.type is EntryType.TEAM and entry.display_name == display_name
        )

    def _apply_plate_change(self, entry: Entry, rider: Rider, plate: str) -> None:
        """Change *entry*/*rider*'s plate to *plate*, if it differs."""
        if entry.type is EntryType.SOLO:
            if plate != entry.plate:
                self.roster.change_solo_plate(entry, plate=plate)
        elif self.roster.plate_model is PlateModel.RIDER_POOLED:
            if plate != rider.plate:
                self.roster.change_pooled_rider_plate(rider, plate=plate)
        elif plate != entry.plate:
            self.roster.change_team_plate(entry, plate=plate)

    def _load(self) -> None:
        """Render the editor's full initial state from the roster."""
        self._refresh_rows()
        self.view.set_team_ui_visible(visible=self.roster.entry_mode is EntryMode.MIXED)
        self._show_add_form()

    def _refresh_rows(self) -> None:
        """Re-render riders_list and team_choice from the roster."""
        self.view.show_riders(_rider_rows(self.roster))
        self.view.show_team_choices(_team_choices(self.roster))

    def _show_add_form(self) -> None:
        """Reset the form: next free plate, nothing selected."""
        self._selected = None
        self.view.show_form(plate=self.roster.next_free_plate(), name="", team=SOLO_TEAM_CHOICE)
        self.view.set_delete_enabled(enabled=False)
