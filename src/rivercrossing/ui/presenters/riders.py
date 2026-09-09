# SPDX-License-Identifier: GPL-3.0-only
"""Riders presenter -- rider_editor_dlg (1d/2b) + csv_preview_dlg (3e).

``RidersPresenter`` drives ``rider_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` (E3.1.1/E3.1.2) -- unlike every
other presenter in this package, it takes no ``DataSource``, since
the editor reads and writes the roster itself rather than a
display-only projection of it. This replaces the earlier no-op
``(view, data_source)`` shape (E1.2.3) for this presenter only.

W7 retires the editor's in-form add: ``rider_editor_dlg``'s own
``add_btn`` now opens the dedicated ``add_rider_dlg`` window
(``ui/views/rider_editor.run_add_rider_flow``) instead of adding from
the editor's form. The dialog pairs with a *second* presenter class,
:class:`AddRiderPresenter`, which owns the create logic the editor's
own ``on_add`` used to run -- Add folds a new rider onto an existing
team via :meth:`~rivercrossing.roster.Roster.create_team_entry_of_one`
(a transient size-1 team, DRAFT-only, R-12's floor deferred to start
time) followed by :meth:`~rivercrossing.roster.Roster.move_rider` --
not ``create_solo_entry`` + ``move_rider`` as first proposed:
``move_rider`` rejects a solo entry on either side unconditionally
(``tests/unit/test_roster.py``'s
``test_move_rider_into_a_solo_entry_raises_invalid_move_error`` and
its two siblings), so the transient must itself be type TEAM. A
refused join rolls the transient team back with ``delete_entry`` so
the roster stays truly unchanged. The 2026-09-06 ux-polish follow-on
retired the "New team…" sentinel from this editor entirely:
``team_choice`` now offers solo or an existing team, and naming a
brand-new team happens in the Teams editor, never here. A successful
Add closes the dialog; the editor's own ``RidersPresenter`` then
re-renders through :meth:`RidersPresenter.on_add_committed`.

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

from __future__ import annotations

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
    TeamSizeError,
    can_delete_entry,
    can_edit_structure,
)
from rivercrossing.ui.presenters.data_source import RiderRow

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from rivercrossing.roster import Entry, Roster

__all__ = [
    "SOLO_TEAM_CHOICE",
    "AddRiderPresenter",
    "AddRiderView",
    "CsvConflict",
    "CsvPreview",
    "RiderFormValues",
    "RidersPresenter",
    "RidersView",
]

# team_choice's frozen first entry (xrc-windows.md's Rider Editor
# mock: "-- solo --" first, then team display names -- the retired
# "New team…" sentinel is gone, ux-polish).
SOLO_TEAM_CHOICE = "— solo —"


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
    warnings: tuple[CsvConflict, ...] = ()


@dataclass(frozen=True, slots=True)
class RiderFormValues:
    """The rider editor's form fields, forwarded by the view (R-20).

    ``team`` is always one of team_choice's literal current
    contents: :data:`SOLO_TEAM_CHOICE` or an existing team's
    display name -- the view forwards whatever the control
    currently holds verbatim, never translating it (passive view).
    """

    plate: str
    first_name: str
    last_name: str
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

    def set_save_enabled(self, *, enabled: bool) -> None:
        """Gate save_btn: enabled only when the form is dirty (W7)."""
        ...

    def set_plate_enabled(self, *, enabled: bool) -> None:
        """Toggle plate_input's editability (W7 plate lock)."""
        ...

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Render csv_preview_dlg's summary line and conflicts."""
        ...

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate wxID_OK "Import" while conflicts > 0."""
        ...

    def show_form(  # noqa: PLR0913 -- the passive view fills the four form fields verbatim
        self, *, plate: str, first_name: str, last_name: str, team: str
    ) -> None:
        """Fill the rider editor's form fields (R-20)."""
        ...

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide team_choice + the Team column (R-11, solo-only)."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-operation message (roster_infobar, later)."""
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
    return _pair_rows(roster, _rider_pairs(roster))


def _pair_rows(roster: Roster, pairs: Sequence[tuple[Entry, Rider]]) -> list[RiderRow]:
    """Map *pairs* onto riders_list rows, in the pairs' own order."""
    return [
        RiderRow(
            plate=_rider_plate(roster, entry, rider),
            name=rider.full_name,
            team=entry.display_name if entry.type is EntryType.TEAM else None,
        )
        for entry, rider in pairs
    ]


def _plate_order_key(plate: str) -> tuple[int, int] | tuple[int, str]:
    """Return the numeric-aware sort key of one Plate cell (W7).

    Digit plates order by value (``2`` before ``10``); every non-digit
    relay plate orders after all of them, alphabetically.
    """
    if plate.isdigit():
        return (0, int(plate))
    return (1, plate)


def _visible_pairs(  # noqa: PLR0913 -- the filter/order state bundle (text, column, direction)
    roster: Roster,
    pairs: Sequence[tuple[Entry, Rider]],
    *,
    search_text: str,
    column: int | None,
    ascending: bool,
) -> list[tuple[Entry, Rider]]:
    """Filter *pairs* by *search_text*, then order them (W7).

    *search_text* matches the row's Plate or Name cell as a
    case-insensitive substring (the audit dialog's own precedent);
    a blank search filters nothing. Ordering: ``None`` *column*
    keeps the pairs' given (roster) order; the Plate column uses
    numeric-aware keys (:func:`_plate_order_key`); Name and Team are
    casefolded text, with Team treating solo rows (whose cell reads
    "solo") as the smallest group. ``sorted`` is stable, so equal
    keys keep roster order; *ascending* ``False`` reverses the
    comparison.
    """
    needle = search_text.strip().casefold()
    visible = [
        pair
        for pair in pairs
        if not needle
        or needle in _rider_plate(roster, *pair).casefold()
        or needle in pair[1].full_name.casefold()
    ]
    if column is None:
        return visible
    key = _column_sort_key(roster, column)
    return sorted(visible, key=key, reverse=not ascending)


def _column_sort_key(
    roster: Roster, column: int
) -> Callable[[tuple[Entry, Rider]], tuple[int, int] | tuple[int, str] | str]:
    """Return the sort-key callable for riders_list *column* (W7).

    Columns are the view's Plate | Name | Team order (0..2); the key
    a callable extracts from one (entry, rider) pair.
    """
    if column == 0:

        def plate_key(pair: tuple[Entry, Rider]) -> tuple[int, int] | tuple[int, str]:
            return _plate_order_key(_rider_plate(roster, *pair))

        return plate_key
    if column == 1:

        def name_key(pair: tuple[Entry, Rider]) -> str:
            return pair[1].full_name.casefold()

        return name_key

    def team_key(pair: tuple[Entry, Rider]) -> str:
        entry = pair[0]
        team = entry.display_name if entry.type is EntryType.TEAM else ""
        return team.casefold()

    return team_key


def _team_choices(roster: Roster) -> list[str]:
    """Return team_choice's content: solo, then every team in order."""
    names = [entry.display_name for entry in roster.entries if entry.type is EntryType.TEAM]
    return [SOLO_TEAM_CHOICE, *names]


def _find_team_entry(roster: Roster, display_name: str) -> Entry:
    """Return the TEAM entry named *display_name*.

    Shared by the Add dialog's join and the editor's save-time team
    change; a display name with no entry is a stale-choice
    ``StopIteration`` the caller's own ``RosterError`` guard does not
    catch, mirroring the pre-W7 add flow's identical assumption that
    team_choice's content tracks the roster it was built from.
    """
    return next(
        entry
        for entry in roster.entries
        if entry.type is EntryType.TEAM and entry.display_name == display_name
    )


@runtime_checkable
class AddRiderView(Protocol):
    """View surface for the Add Rider dialog (add_rider_dlg, W7)."""

    def show_team_choices(self, names: list[str]) -> None:
        """Replace team_choice's content with *names*, in order."""
        ...

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide the Team row (R-11: solo-only rides have none)."""
        ...

    def show_form(self, *, plate: str, team: str) -> None:
        """Pre-fill the plate/team fields; names start blank."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-add message on the dialog's infobar."""
        ...


class AddRiderPresenter:
    """Presenter for the Add Rider dialog (add_rider_dlg, W7).

    Owns the create logic the editor's in-form Add used to run (see
    the module docstring): a solo choice creates a solo entry, a team
    choice folds a fresh rider onto that existing team through the
    shipped primitives. ``on_submit`` reports success as a bool so
    the view can close only on a real commit -- a refusal (blank
    name, duplicate plate, full team, ...) leaves the dialog open,
    showing why on its own infobar, and never mutates the roster.
    """

    def __init__(self, view: AddRiderView, roster: Roster) -> None:
        """Store the collaborators and render the dialog's start state.

        Args:
            view: The add_rider_dlg view driving this roster.
            roster: The in-memory roster this presenter reads/writes.
        """
        self.view = view
        self.roster = roster
        self.view.show_team_choices(_team_choices(self.roster))
        self.view.set_team_ui_visible(visible=self.roster.entry_mode is EntryMode.MIXED)
        self.view.show_form(plate=self.roster.next_free_plate(), team=SOLO_TEAM_CHOICE)

    def on_submit(self, form: RiderFormValues) -> bool:
        """Create *form*'s entry, or refuse with a message.

        W7 requires both names: a blank first or last name refuses
        before any plate or team rule runs. A roster refusal
        (duplicate plate, a team at max size, ...) shows via
        :meth:`AddRiderView.show_validation` and leaves the roster
        unchanged, never raising past this handler.

        Returns:
            True only once the entry actually exists.
        """
        if not form.first_name.strip() or not form.last_name.strip():
            self.view.show_validation("First name and last name are required")
            return False
        try:
            self._create_entry(form)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        return True

    def _create_entry(self, form: RiderFormValues) -> None:
        """Create *form*'s entry: solo, or folded onto a team."""
        if form.team == SOLO_TEAM_CHOICE:
            self.roster.create_solo_entry(
                first_name=form.first_name, last_name=form.last_name, plate=form.plate
            )
            return
        self._join_existing_team(form)

    def _join_existing_team(self, form: RiderFormValues) -> None:
        """Fold a new rider onto the existing team named *form.team*.

        Composed from shipped Roster primitives: a transient size-1
        team is created, then folded in via move_rider (see the
        module docstring). A refused fold-in rolls the transient
        back so the roster stays unchanged.
        """
        target = _find_team_entry(self.roster, form.team)
        rider = Rider(first_name=form.first_name, last_name=form.last_name, plate=form.plate)
        entry_plate = form.plate if self.roster.plate_model is PlateModel.TEAM_RELAY else None
        transient = self.roster.create_team_entry_of_one(
            display_name=form.team, rider=rider, plate=entry_plate
        )
        try:
            self.roster.move_rider(rider, to_entry=target)
        except RosterError:
            self.roster.delete_entry(transient)
            raise


class RidersPresenter:
    """Presenter for the rider editor (rider_editor_dlg, R-11/15/20).

    See the module docstring for how team growth composes from
    :class:`~rivercrossing.roster.Roster`'s shipped primitives; a relay
    team member's plate change on Save routes through
    ``_apply_plate_change`` → ``Roster.change_team_plate`` (covered by
    ``test_on_save_given_a_relay_team_member_changes_the_teams_plate``).
    W7: this presenter no longer adds -- the Add dialog's own
    :class:`AddRiderPresenter` does, and this one re-renders after it
    through :meth:`on_add_committed`.
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
        # W7 close-persist flag: True once any add/save/delete has
        # actually committed this session (app.py's editor-close save
        # consults :attr:`roster_changed`).
        self._roster_changed = False
        # W7 search/sort state: what riders_list currently shows, and
        # the two narrowings that decide it (_refresh_rows applies).
        self._visible: list[tuple[Entry, Rider]] = []
        self._search_text = ""
        self._sort_column: int | None = None
        self._sort_ascending = True
        if load:
            self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from riders_list row *index* (R-20, W7).

        *index* addresses the currently *visible* rows -- the
        search/sort narrowed list -- never the raw roster, so the
        selection always lands on the row the operator can see.

        The fresh record's own values are clean by definition, so the
        row lands with save_btn disabled until the form differs
        (:meth:`on_form_changed`).
        """
        entry, rider = self._visible[index]
        self._show_record(entry, rider)

    def on_search_text(self, text: str) -> None:
        """Narrow riders_list to rows matching *text* (W7).

        Matches the row's Plate or Name cell case-insensitively (the
        audit dialog's own precedent). The active sort, if any, keeps
        ordering the survivors; clearing the text restores every row.
        """
        self._search_text = text
        self._refresh_rows()

    def on_sort_by_column(self, column: int) -> None:
        """Sort riders_list by *column*; re-clicking toggles (W7).

        The presenter owns row order (a ``DataViewIndexListModel``
        cannot sort itself -- see the W7 report note), so the view
        forwards header clicks here and re-renders through
        ``show_riders``. The first click on a column sorts ascending;
        clicking the active column again reverses it.
        """
        if self._sort_column == column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True
        self._refresh_rows()

    def on_form_changed(self, form: RiderFormValues) -> None:
        """Re-gate save_btn from the form's own current values (W7).

        The view forwards every plate/name/team edit here; the button
        is enabled exactly while *form* differs from the selected
        record -- a clean form (or no selection at all) disables it,
        so Enter over a clean form is a no-op and Save can never
        rewrite a record the operator did not mean to change.
        """
        self.view.set_save_enabled(enabled=self._is_dirty(form))

    def on_add_committed(self) -> None:
        """Re-render after the Add dialog committed into this roster.

        W7: ``add_rider_dlg``'s own :class:`AddRiderPresenter` did the
        creating; this editor only catches its rows/form up -- refresh
        both list and team_choice, then reset to the no-selection add
        form (next free plate now one higher).
        """
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_save(self, form: RiderFormValues) -> None:
        """Handle save_btn: rename, replate and re-team the selection.

        W7 adds the form's Team field to the save. The chosen team is
        applied through the roster's shipped primitives, each
        direction refusing with its own ``RosterError`` message when
        the ride state or plate model forbids it: a solo rider joins
        a team via ``delete_entry`` + ``add_rider_to_team`` (the csvio
        reshape precedent, capacity pre-checked so a refused join
        never strands the rider), a team member moves between teams
        via ``move_rider``, and leaving for solo extracts via
        ``extract_rider_to_solo`` -- which exists only for
        ``rider_pooled``, so a relay member's "leave" is a refusal,
        not a silent no-op. On a relay ride a team change freezes the
        form's plate value: the plate belongs to the entry, so
        carrying it over would rewrite the *destination* team's plate.

        A successful save refreshes the rows and then re-shows the
        record's own form (:meth:`_show_record`), so team_choice's
        selection survives its own content reset in
        ``_refresh_rows()`` (the W7 #8 fix) and save_btn lands back
        disabled on the now-clean form. A refusal
        (duplicate plate, the ride has left DRAFT, ...) shows via
        :meth:`RidersView.show_validation` and leaves the roster
        unchanged, never raising past this handler. A no-op if
        nothing is selected.
        """
        if self._selected is None:
            return
        entry, rider = self._selected
        try:
            team_changed = self._team_value(entry) != form.team
            if team_changed:
                entry = self._apply_team_change(entry, rider, form.team)
            if not (team_changed and self.roster.plate_model is PlateModel.TEAM_RELAY):
                self._apply_plate_change(entry, rider, form.plate)
            rider.first_name = form.first_name
            rider.last_name = form.last_name
            if entry.type is EntryType.SOLO:
                self.roster.update_entry(entry, display_name=rider.full_name)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return
        self._roster_changed = True
        self._refresh_rows()
        self._show_record(entry, rider)

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
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_pick_csv_import(self, path: Path) -> None:
        """Preview *path* against this roster; render it (E3.4, R-21).

        Nothing is written -- :func:`~rivercrossing.csvio.preview`'s
        own contract. A file that cannot be read raises ``OSError``
        (csvio's own module docstring) -- a permission failure or a
        path that vanished after the picker's must-exist check, say.
        Content problems, including a non-UTF-8 file, preview as
        conflicts rather than raises; a decode or parse ``ValueError``
        that still escapes preview is caught too. Both surface through
        :meth:`RidersView.show_validation` with Import disabled, and
        never raise past this handler: wx swallows an exception that
        escapes the presenter's caller, which would leave the dialog
        open with nothing happening (the measured note
        ``docs/EPIC3-SESSION-SUMMARY.md`` records).
        """
        try:
            self._csv_preview = csvio.preview(path, self.roster)
        except (OSError, ValueError) as exc:
            self.view.show_validation(f"Could not read {path.name}: {exc}")
            self.view.set_import_enabled(enabled=False)
            return
        conflicts = tuple(
            CsvConflict(row=conflict.row, problem=conflict.problem)
            for conflict in self._csv_preview.conflicts
        )
        warnings = tuple(
            CsvConflict(row=warning.row, problem=warning.problem)
            for warning in self._csv_preview.warnings
        )
        summary = (
            f"{path.name} → {self._csv_preview.rider_count} riders · "
            f"{self._csv_preview.team_count} teams · {len(conflicts)} conflicts"
        )
        if warnings:
            summary += f" · {len(warnings)} warnings"
        self.view.show_csv_preview(
            CsvPreview(summary=summary, conflicts=conflicts, warnings=warnings)
        )
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

    def _apply_plate_change(self, entry: Entry, rider: Rider, plate: str) -> None:
        """Change *entry*/*rider*'s plate to *plate*, if it differs.

        A blank or whitespace-only *plate* reaches the roster's own
        non-empty guard (``change_*_plate``), which raises
        ``PlateShapeError`` with the "must not be empty" message --
        W7's central fix for the relay-blank hole, so no blank plate
        is ever stored through this editor.
        """
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
        # W7 plate lock: once the ride has left DRAFT, plate_input is
        # disabled (spec S3:46's start lock) -- the roster refuses the
        # change anyway, but the disabled field says so up front.
        self.view.set_plate_enabled(enabled=can_edit_structure(self.roster.status))

    @property
    def roster_changed(self) -> bool:
        """Return whether this session committed a roster change (W7).

        The app's editor-close persistence hook reads this after the
        modal ends; only real commits (an add, save or delete that
        actually applied) set it, never a refusal or a no-op.
        """
        return self._roster_changed

    def _refresh_rows(self) -> None:
        """Re-render riders_list and team_choice from the roster.

        The search/sort narrowings (:data:`_visible`) are recomputed
        here, so every caller -- initial load, add/save/delete, the
        search and sort handlers themselves -- renders the same
        filtered, ordered list the selection events index into.
        """
        self._visible = _visible_pairs(
            self.roster,
            _rider_pairs(self.roster),
            search_text=self._search_text,
            column=self._sort_column,
            ascending=self._sort_ascending,
        )
        self.view.show_riders(_pair_rows(self.roster, self._visible))
        self.view.show_team_choices(_team_choices(self.roster))

    def _show_add_form(self) -> None:
        """Reset the form: next free plate, nothing selected."""
        self._selected = None
        self.view.show_form(
            plate=self.roster.next_free_plate(), first_name="", last_name="", team=SOLO_TEAM_CHOICE
        )
        self.view.set_delete_enabled(enabled=False)
        self.view.set_save_enabled(enabled=False)

    def _team_value(self, entry: Entry) -> str:
        """Return the form's Team text for a row on *entry* (R-20)."""
        if entry.type is EntryType.TEAM:
            return entry.display_name
        return SOLO_TEAM_CHOICE

    def _is_dirty(self, form: RiderFormValues) -> bool:
        """Return whether *form* differs from the selected record."""
        if self._selected is None:
            return False
        entry, rider = self._selected
        record = RiderFormValues(
            plate=_rider_plate(self.roster, entry, rider),
            first_name=rider.first_name,
            last_name=rider.last_name,
            team=self._team_value(entry),
        )
        return form != record

    def _show_record(self, entry: Entry, rider: Rider) -> None:
        """Fill the form from the record itself; save disabled (W7).

        The one place a selection -- a row click, or the re-show that
        ends a successful save -- fills the form: the record's own
        values are clean by definition, so save_btn lands disabled
        and delete_btn follows the record's own deletability.
        """
        self._selected = (entry, rider)
        self.view.show_form(
            plate=_rider_plate(self.roster, entry, rider),
            first_name=rider.first_name,
            last_name=rider.last_name,
            team=self._team_value(entry),
        )
        self.view.set_delete_enabled(
            enabled=can_delete_entry(self.roster.status, has_data=entry.has_data)
        )
        self.view.set_save_enabled(enabled=False)

    def _apply_team_change(self, entry: Entry, rider: Rider, chosen: str) -> Entry:
        """Apply the form's chosen team to the selected rider (W7).

        Composes the roster's shipped primitives per direction --
        solo joins delete their own entry first, then attach via
        ``add_rider_to_team`` (the csvio reshape precedent) with the
        destination's capacity pre-checked so a refused join can
        never strand the rider after its solo entry is gone; team
        members use ``move_rider`` and leave-for-solo uses
        ``extract_rider_to_solo``, whose own refusal messages name
        the state/model that forbids the direction.

        Returns:
            The entry the rider belongs to after the change.
        """
        if chosen == SOLO_TEAM_CHOICE:
            if entry.type is EntryType.SOLO:
                return entry
            return self.roster.extract_rider_to_solo(rider)
        target = _find_team_entry(self.roster, chosen)
        if entry.type is EntryType.TEAM:
            if entry is not target:
                self.roster.move_rider(rider, to_entry=target)
            return target
        if len(target.riders) + 1 > self.roster.max_team_size:
            msg = (
                f"team size must be at most {self.roster.max_team_size}, "
                f"got {len(target.riders) + 1}"
            )
            raise TeamSizeError(msg)
        self.roster.delete_entry(entry)
        self.roster.add_rider_to_team(rider, to_entry=target)
        return target
