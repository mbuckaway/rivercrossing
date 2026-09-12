# SPDX-License-Identifier: GPL-3.0-only
"""Riders presenter -- rider_editor_dlg (1d/2b) + csv_preview_dlg (3e).

``RidersPresenter`` drives ``rider_editor_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` (E3.1.1/E3.1.2) -- unlike every
other presenter in this package, it takes no ``DataSource``, since
the editor reads and writes the roster itself rather than a
display-only projection of it. This replaces the earlier no-op
``(view, data_source)`` shape (E1.2.3) for this presenter only.

1.0.12 retires the editor's in-form save. ``rider_editor_dlg``'s own
form is display-only (``riders.xrc``: every text field read-only, no
``save_btn``), and its two buttons open the dedicated
``add_rider_dlg`` window (``ui/views/rider_editor.
run_add_rider_flow``/``run_edit_rider_flow``) over the row the
operator selected. That one dialog pairs with one of two presenter
classes sharing the one :class:`AddRiderView` surface:
:class:`AddRiderPresenter` (blank form) or :class:`EditRiderPresenter`
(record preloaded), whose ``on_submit`` writes the form back through
the shared update mutators (:func:`_apply_form_changes` -- team, then
plate, then the rider's own fields, names and sex) rather than the
Add path's create primitives. A committed Add or Edit re-renders
the editor through :meth:`RidersPresenter.on_add_committed` /
:meth:`RidersPresenter.on_edit_committed`, so the open editor never
shows a stale roster.

Add folds a new rider onto an existing team through
:meth:`~rivercrossing.roster.Roster.add_rider_to_team` directly. The
rider attaches to the chosen team in place, so the pooled
RUNNING/REOPENED carve-out comes from
:func:`~rivercrossing.roster.can_move_rider`, and a team already at
``max_team_size`` raises ``TeamSizeError`` before anything changes --
the same primitive the Edit path's team change uses
(:func:`_apply_team_change`). The 2026-09-06 ux-polish follow-on
retired the "New team…" sentinel from this editor entirely:
``team_choice`` now offers solo or an existing team, and naming a
brand-new team happens in the Teams editor, never here. A successful
Add or Edit closes the dialog; the editor's own ``RidersPresenter``
then re-renders.

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
from rivercrossing.ui.rider_columns import SOLO_TEAM_TEXT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from rivercrossing.roster import Entry, Roster

__all__ = [
    "SOLO_TEAM_CHOICE",
    "SOLO_TEAM_TEXT",
    "AddRiderPresenter",
    "AddRiderView",
    "CsvConflict",
    "CsvPreview",
    "EditRiderPresenter",
    "RiderFormValues",
    "RidersPresenter",
    "RidersView",
]

# team_choice's frozen first entry: the bare word "solo" first, then
# every team's display name. The "-- solo --" wording of
# xrc-windows.md's Rider Editor mock is retired (ux-polish), as is
# its "New team…" sentinel.
SOLO_TEAM_CHOICE = "solo"

# ``SOLO_TEAM_TEXT`` above is imported from ``ui.rider_columns``
# (Phase 3): the one wx-free home for the rider lists' shared cell
# text, so the presenter, ``views/rider_editor.py`` and
# ``views/main_frame.py`` cannot spell a solo rider's Team cell three
# different ways.


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
    ``sex`` is the Add/Edit dialog's own Sex dropdown: ``"M"``,
    ``"F"``, or ``None`` for the blank (unknown) choice -- the blank
    item never arrives as ``""``, matching ``Rider.sex``.
    """

    plate: str
    first_name: str
    last_name: str
    team: str
    sex: str | None = None


@runtime_checkable
class RidersView(Protocol):
    """View surface for the rider editor and its CSV import preview.

    The editor's form is display-only (1.0.12 B1): the view carries no
    save gate and no plate lock any more -- both moved to the Add/Edit
    dialog's own :class:`AddRiderView` surface. What remains here is
    what the editor does: render rows, render the selected record,
    gate delete, and read the one verdict the delete confirm returns.
    """

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render riders_list.

        Rows arrive in the roster's own (search-filtered) order; the
        list's native header sort re-orders what it displays.
        """
        ...

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Disable delete_btn once the entry has data (R-15)."""
        ...

    def confirm(  # noqa: PLR0913 -- (title, message) + 2 button labels, mirroring std_dialogs.show_confirm
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
    ) -> bool:
        """Ask a destructive confirm; return whether OK was chosen (B3).

        The view owns the parent window and opens the native confirm
        (``ui.std_dialogs.show_confirm``); the presenter reads only the
        boolean verdict, so the flow stays headless-testable -- the
        same seam ``ConsoleView.confirm`` carries.
        """
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


def _team_cell(entry: Entry) -> str:
    """Return one row's Team cell text for the search filter (W7).

    A team renders its display name; a solo renders the literal
    :data:`SOLO_TEAM_TEXT` -- the same text the shared Team column in
    ``ui.rider_columns`` draws, computed here so no ``wx``-touching
    module reaches the presenter (R-71).
    """
    return entry.display_name if entry.type is EntryType.TEAM else SOLO_TEAM_TEXT


def _rider_rows(roster: Roster) -> list[RiderRow]:
    """Map every roster rider onto one riders_list row (R-20)."""
    return _pair_rows(roster, _rider_pairs(roster))


def _pair_row(roster: Roster, entry: Entry, rider: Rider) -> RiderRow:
    """Map one (entry, rider) pair onto its riders_list row (R-20)."""
    return RiderRow(
        plate=_rider_plate(roster, entry, rider),
        name=rider.full_name,
        team=entry.display_name if entry.type is EntryType.TEAM else None,
        sex=rider.sex,
    )


def _pair_rows(roster: Roster, pairs: Sequence[tuple[Entry, Rider]]) -> list[RiderRow]:
    """Map *pairs* onto riders_list rows, in the pairs' own order."""
    return [_pair_row(roster, entry, rider) for entry, rider in pairs]


def _visible_pairs(
    roster: Roster,
    pairs: Sequence[tuple[Entry, Rider]],
    *,
    search_text: str,
) -> list[tuple[Entry, Rider]]:
    """Filter *pairs* by *search_text* (W7).

    *search_text* matches the row's Plate, Name or Team cell as a
    case-insensitive substring (the audit dialog's own precedent); a
    blank search filters nothing. A solo row's Team cell is the literal
    "solo" (:func:`_team_cell`), so searching "solo" finds every solo
    rider. The survivors keep the pairs' given (roster) order: the
    list's own native header sort re-orders what it displays, from the
    shared column's ``sort_key`` through
    :meth:`~rivercrossing.ui.views._support.RiderRowListModel.Compare`.
    """
    needle = search_text.strip().casefold()
    return [
        pair
        for pair in pairs
        if not needle
        or needle in _rider_plate(roster, *pair).casefold()
        or needle in pair[1].full_name.casefold()
        or needle in _team_cell(pair[0]).casefold()
    ]


def _team_choices(roster: Roster) -> list[str]:
    """Return team_choice's content: solo, then every team in order."""
    names = [entry.display_name for entry in roster.entries if entry.type is EntryType.TEAM]
    return [SOLO_TEAM_CHOICE, *names]


def _find_team_entry(roster: Roster, display_name: str) -> Entry:
    """Return the TEAM entry named *display_name*.

    Shared by the Add dialog's join and the edit write-back's team
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


def _team_value(entry: Entry) -> str:
    """Return the form's Team text for a row on *entry* (R-20)."""
    if entry.type is EntryType.TEAM:
        return entry.display_name
    return SOLO_TEAM_CHOICE


def _rename_rider(  # noqa: PLR0913 -- (roster, entry, rider) + both name halves + sex
    roster: Roster,
    entry: Entry,
    rider: Rider,
    *,
    first_name: str,
    last_name: str,
    sex: str | None,
) -> None:
    """Write *rider*'s own fields; a solo rename follows (R-20).

    The rider's own fields are the two name halves and ``sex`` -- the
    three things the Add/Edit dialog edits that belong to the rider
    rather than to the entry. A team keeps its own display name, so
    only a SOLO entry's display name follows its rider's.
    """
    rider.first_name = first_name
    rider.last_name = last_name
    rider.sex = sex
    if entry.type is EntryType.SOLO:
        roster.update_entry(entry, display_name=rider.full_name)


def _apply_team_change(  # noqa: PLR0913, PLR0917 -- (roster, entry, rider) + the choice
    roster: Roster, entry: Entry, rider: Rider, chosen: str
) -> Entry:
    """Apply the form's chosen team to *rider* (W7).

    Composes the roster's shipped primitives per direction -- solo
    joins delete their own entry first, then attach via
    ``add_rider_to_team`` (the csvio reshape precedent) with the
    destination's capacity pre-checked so a refused join can never
    strand the rider after its solo entry is gone; team members use
    ``move_rider`` and leave-for-solo uses ``extract_rider_to_solo``,
    whose own refusal messages name the state/model that forbids the
    direction.

    Returns:
        The entry the rider belongs to after the change.
    """
    if chosen == SOLO_TEAM_CHOICE:
        if entry.type is EntryType.SOLO:
            return entry
        return roster.extract_rider_to_solo(rider)
    target = _find_team_entry(roster, chosen)
    if entry.type is EntryType.TEAM:
        if entry is not target:
            roster.move_rider(rider, to_entry=target)
        return target
    if len(target.riders) + 1 > roster.max_team_size:
        msg = f"team size must be at most {roster.max_team_size}, got {len(target.riders) + 1}"
        raise TeamSizeError(msg)
    roster.delete_entry(entry)
    roster.add_rider_to_team(rider, to_entry=target)
    return target


def _apply_form_changes(  # noqa: PLR0913, PLR0917 -- (roster, entry, rider) + the form
    roster: Roster, entry: Entry, rider: Rider, form: RiderFormValues
) -> Entry:
    """Apply *form* to *rider*'s record; return the rider's entry (B2).

    The one write-back the edit dialog runs, in the order a change
    means: the chosen team first (a move changes which entry owns the
    plate), then the plate -- skipped on a relay ride when the team
    changed, since the plate belongs to the entry and carrying the
    form's value over would rewrite the *destination* team's plate --
    then the names. A ``RosterError`` from an earlier step leaves the
    later ones untouched, so a refused edit never half-applies.

    The plate step is the roster's own shared
    :meth:`~rivercrossing.roster.Roster.change_plate` dispatch (the
    same one the rider-issues fixes use), so the shape rule has one
    home; a blank or whitespace-only *form.plate* reaches that
    dispatch's non-empty guard (``change_*_plate``) and is refused
    there -- W7's central fix for the relay-blank hole, so no blank
    plate is ever stored through this editor.
    """
    team_changed = _team_value(entry) != form.team
    if team_changed:
        entry = _apply_team_change(roster, entry, rider, form.team)
    if not (team_changed and roster.plate_model is PlateModel.TEAM_RELAY):
        roster.change_plate(entry, rider, plate=form.plate)
    _rename_rider(
        roster,
        entry,
        rider,
        first_name=form.first_name,
        last_name=form.last_name,
        sex=form.sex,
    )
    return entry


@runtime_checkable
class AddRiderView(Protocol):
    """View surface for add_rider_dlg, in either mode (W7, 1.0.12 B2).

    One window serves both modes, so one protocol covers both:
    ``show_form`` carries all five fields (Add passes the names blank
    and the sex unset, Edit preloads the record's), and
    ``set_plate_enabled`` is the spec S3:46 start lock the editor's
    own retired form used to own.
    """

    def show_team_choices(self, names: list[str]) -> None:
        """Replace team_choice's content with *names*, in order."""
        ...

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show/hide the Team row (R-11: solo-only rides have none)."""
        ...

    def set_plate_enabled(self, *, enabled: bool) -> None:
        """Toggle plate_input's editability (S3:46's start lock)."""
        ...

    def show_form(  # noqa: PLR0913 -- the passive view fills the five form fields verbatim
        self,
        *,
        plate: str,
        first_name: str,
        last_name: str,
        team: str,
        sex: str | None,
    ) -> None:
        """Pre-fill the five fields (Add leaves the names blank)."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-add message on the dialog's infobar."""
        ...


class AddRiderPresenter:
    """Presenter for add_rider_dlg's Add mode (W7).

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
        # spec S3:46's start lock (1.0.12 B2): once the ride has left
        # DRAFT the plate field is disabled -- the roster refuses the
        # change anyway, but the disabled field says so up front.
        self.view.set_plate_enabled(enabled=can_edit_structure(self.roster.status))
        self.view.show_form(
            plate=self.roster.next_free_plate(),
            first_name="",
            last_name="",
            team=SOLO_TEAM_CHOICE,
            sex=None,
        )

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
                first_name=form.first_name,
                last_name=form.last_name,
                plate=form.plate,
                sex=form.sex,
            )
            return
        self._join_existing_team(form)

    def _join_existing_team(self, form: RiderFormValues) -> None:
        """Fold a new rider onto the existing team named *form.team*.

        Uses :meth:`~rivercrossing.roster.Roster.add_rider_to_team`
        directly: the rider attaches to the chosen team in place, so
        the pooled RUNNING/REOPENED carve-out comes from
        :func:`~rivercrossing.roster.can_move_rider`, and a team
        already at ``max_team_size`` raises ``TeamSizeError`` before
        anything changes.
        """
        target = _find_team_entry(self.roster, form.team)
        rider = Rider(
            first_name=form.first_name,
            last_name=form.last_name,
            plate=form.plate,
            sex=form.sex,
        )
        self.roster.add_rider_to_team(rider, to_entry=target)


class EditRiderPresenter:
    """Presenter for add_rider_dlg's Edit Rider… mode (1.0.12 B2).

    The mirror of :class:`AddRiderPresenter` over the same window and
    the same :class:`AddRiderView` surface: the constructor preloads
    the record (plate per the ride's plate model, both names, the
    owning team) and applies spec S3:46's plate lock, and
    ``on_submit`` writes the form back through the shared update
    mutators -- never the Add path's create primitives. A refusal
    (blank name, duplicate plate, a team at max size, the ride having
    left DRAFT, ...) shows via :meth:`AddRiderView.show_validation`
    and reports ``False`` so the view leaves the dialog open.
    """

    def __init__(  # noqa: PLR0913 -- (view, roster) + the record being edited
        self,
        view: AddRiderView,
        roster: Roster,
        *,
        entry: Entry,
        rider: Rider,
    ) -> None:
        """Store the collaborators and preload the record's fields.

        Args:
            view: The add_rider_dlg view driving this roster.
            roster: The in-memory roster this presenter reads/writes.
            entry: The entry *rider* is on when the dialog opens.
            rider: The rider being edited.
        """
        self.view = view
        self.roster = roster
        self.entry = entry
        self.rider = rider
        self.view.show_team_choices(_team_choices(self.roster))
        self.view.set_team_ui_visible(visible=self.roster.entry_mode is EntryMode.MIXED)
        # B2's plate lock: the same S3:46 rule the Add mode applies.
        self.view.set_plate_enabled(enabled=can_edit_structure(self.roster.status))
        self.view.show_form(
            plate=_rider_plate(self.roster, entry, rider),
            first_name=rider.first_name,
            last_name=rider.last_name,
            team=_team_value(entry),
            sex=rider.sex,
        )

    def on_submit(self, form: RiderFormValues) -> bool:
        """Apply *form* to the record, or refuse with a message.

        Both names are required, as in the Add mode. A roster refusal
        (duplicate plate, a full destination team, the ride having left
        DRAFT, ...) shows via :meth:`AddRiderView.show_validation` and
        leaves the roster's live records unchanged, never raising past
        this handler.

        Returns:
            True only once the write-back actually applied.
        """
        if not form.first_name.strip() or not form.last_name.strip():
            self.view.show_validation("First name and last name are required")
            return False
        try:
            self.entry = _apply_form_changes(self.roster, self.entry, self.rider, form)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        return True


class RidersPresenter:
    """Presenter for the rider editor (rider_editor_dlg, R-11/15/20).

    See the module docstring for how team growth composes from
    :class:`~rivercrossing.roster.Roster`'s shipped primitives; a
    relay team member's plate change routes through
    :meth:`~rivercrossing.roster.Roster.change_plate` →
    ``change_team_plate`` (pinned by the edit-presenter suite's
    relay-member case). 1.0.12: this presenter
    no longer writes at all -- the Add/Edit dialog's own presenters
    do, and this one re-renders after them through
    :meth:`on_add_committed` / :meth:`on_edit_committed`.
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
                ``show_riders``/``set_team_ui_visible``/``show_form``/
                ``set_delete_enabled`` for real, so nothing may call
                them.
        """
        self.view = view
        self.roster = roster
        self._selected: tuple[Entry, Rider] | None = None
        self._csv_preview: csvio.ImportPreview | None = None
        # Phase 3: csv_preview_dlg's "Map unknown sex to Male" checkbox.
        # The picked path needs no field of its own -- it is already
        # retained as ``self._csv_preview.source_path``.
        self._map_unknown_sex_to_male = False
        # Phase E: csv_preview_dlg's "Convert teams of 1 to solo"
        # checkbox, the second opt-in _preview_csv threads into csvio.
        self._convert_teams_of_one_to_solo = False
        # W7 close-persist flag: True once any add/edit/delete has
        # actually committed this session (app.py's editor-close save
        # consults :attr:`roster_changed`).
        self._roster_changed = False
        # W7 search state: what riders_list currently shows, and the
        # one narrowing that decides it (_refresh_rows applies). Row
        # order is the list's own native header sort, so this presenter
        # holds no sort state at all.
        self._visible: list[tuple[Entry, Rider]] = []
        self._search_text = ""
        if load:
            self._load()

    def on_row_selected(self, index: int) -> None:
        """Fill the form from riders_list row *index* (R-20, W7).

        *index* addresses the currently *visible* rows -- the
        search-filtered list -- never the raw roster, so the selection
        always lands on the row the operator can see. It is the model
        row index the view reads back, and the model's rows are this
        same visible list, so the list's own native sort cannot move
        the selection onto a different record. An index outside that
        list is a stale event (the row it pointed at was deleted or
        filtered out from under the view), so the guard here is the
        presenter's own half of the view's selection check, and it
        simply fills nothing.
        """
        if not 0 <= index < len(self._visible):
            return
        entry, rider = self._visible[index]
        self._show_record(entry, rider)

    def on_search_text(self, text: str) -> None:
        """Narrow riders_list to rows matching *text* (W7).

        Matches the row's Plate, Name or Team cell case-insensitively
        (the audit dialog's own precedent) -- so a team name or the
        literal "solo" finds its rows too, not just name/plate. The
        survivors keep the roster's own order (the list's native header
        sort paints its own order on top); clearing the text restores
        every row.
        """
        self._search_text = text
        self._refresh_rows()

    def on_add_committed(self) -> None:
        """Re-render after the Add dialog committed into this roster.

        W7: ``add_rider_dlg``'s own :class:`AddRiderPresenter` did the
        creating; this editor only catches its own rows and form up --
        refresh the list, then reset to the no-selection add form
        (next free plate now one higher).
        """
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_edit_committed(self, rider: Rider) -> None:
        """Re-render after the Edit dialog committed into this roster.

        1.0.12 B3: the Edit dialog's own
        :class:`EditRiderPresenter` wrote the record; this editor
        catches its rows up, then re-shows *rider*'s own record -- the
        entry looked up fresh from the roster, since a team change
        moved the rider onto a different one -- so the visible form
        agrees with the row and ``_persist_rider_editor_changes``
        saves the changed roster when the editor closes.

        A *rider* the roster no longer holds (deleted while the dialog
        was open) cannot be re-shown, so the editor falls back to the
        add form rather than filling a stale record.
        """
        self._roster_changed = True
        self._refresh_rows()
        entry = self._entry_of(rider)
        if entry is None:
            self._show_add_form()
            return
        self._show_record(entry, rider)

    def on_delete(self) -> None:
        """Handle delete_btn: confirm, then remove that rider (R-15).

        1.0.12 B3: a delete is irreversible, so the view asks the
        operator first (``RidersView.confirm``, naming the *rider* --
        a team-member row must not be confirmed as the team) and this
        handler returns early on Cancel -- nothing is attempted, no
        message shown, nothing flagged. The removal goes through
        :meth:`~rivercrossing.roster.Roster.remove_rider`, so OK
        deletes only the selected rider: a solo rider's own entry
        goes, a team member leaves their team standing. A refusal
        from the roster (recorded data, or the ride has left DRAFT)
        shows via :meth:`RidersView.show_validation`, naming the
        reason, and never raises past this handler. A no-op if nothing
        is selected -- including the confirm, which never opens over
        an empty form.
        """
        if self._selected is None:
            return
        _, rider = self._selected
        if not self.view.confirm(
            "Delete rider?",
            f'Delete "{rider.full_name}" from this ride?',
            ok_label="Delete",
            cancel_label="Cancel",
        ):
            return
        try:
            self.roster.remove_rider(rider)
        except LockedError as exc:
            self.view.show_validation(str(exc))
            return
        self._roster_changed = True
        self._refresh_rows()
        self._show_add_form()

    def on_pick_csv_import(self, path: Path) -> None:
        """Preview *path* against this roster; render it (E3.4, R-21).

        The picker's entry point; the preview and render both live in
        :meth:`_preview_csv`, which this delegates to (Phase 3 shared
        it with :meth:`on_toggle_map_unknown_sex`).
        """
        self._preview_csv(path)

    def _preview_csv(self, path: Path) -> None:
        """Preview *path* and render the result (E3.4, R-21).

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

        Phase 3 threads :attr:`_map_unknown_sex_to_male` into every
        preview, so the toggle's re-run and a fresh pick agree; Phase E
        threads :attr:`_convert_teams_of_one_to_solo` the same way.
        """
        try:
            self._csv_preview = csvio.preview(
                path,
                self.roster,
                map_unknown_sex_to_male=self._map_unknown_sex_to_male,
                convert_teams_of_one_to_solo=self._convert_teams_of_one_to_solo,
            )
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

    def on_toggle_map_unknown_sex(self, *, enabled: bool) -> None:
        """Handle ``map_unknown_sex_chk``; re-preview if one is open.

        *enabled* is the checkbox's new state. With no preview yet
        this only records the flag -- the next pick honours it. With
        one, the same retained path is re-previewed, so the conflicts
        list and Import button reflect the new mapping immediately
        (``csvio.preview`` writes nothing; re-running it is free).
        """
        self._map_unknown_sex_to_male = enabled
        if self._csv_preview is not None:
            self._preview_csv(self._csv_preview.source_path)

    def on_toggle_convert_teams_of_one(self, *, enabled: bool) -> None:
        """Handle ``convert_teams_of_one_chk``; re-preview on toggle.

        *enabled* is the checkbox's new state. With no preview yet this
        only records the flag -- the next pick honours it. With one,
        the same retained path is re-previewed, so the summary's
        team count and the conflicts list reflect the DRAFT-only
        conversion immediately (``csvio.preview`` writes nothing).
        """
        self._convert_teams_of_one_to_solo = enabled
        if self._csv_preview is not None:
            self._preview_csv(self._csv_preview.source_path)

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

    def _load(self) -> None:
        """Render the editor's full initial state from the roster.

        No plate lock here any more (1.0.12 B1): the editor's own form
        is display-only, and the spec S3:46 lock moved with the editing
        to :class:`AddRiderPresenter`/:class:`EditRiderPresenter`. The
        list opens in the roster's own order: the operator's header
        sort belongs to the control, not this presenter.
        """
        self._refresh_rows()
        self.view.set_team_ui_visible(visible=self.roster.entry_mode is EntryMode.MIXED)
        self._show_add_form()

    @property
    def selected(self) -> tuple[Entry, Rider] | None:
        """Return the record the form is showing, or ``None`` (B3).

        The view reads this to hand the Edit dialog its record; it is
        the same ``(entry, rider)`` :meth:`_show_record` stored, so a
        row the operator can see is what opens for editing.
        """
        return self._selected

    @property
    def roster_changed(self) -> bool:
        """Return whether this session committed a roster change (W7).

        The app's editor-close persistence hook reads this after the
        modal ends; only real commits (an add, edit or delete that
        actually applied) set it, never a refusal or a no-op.
        """
        return self._roster_changed

    def _refresh_rows(self) -> None:
        """Re-render riders_list from the roster.

        The search narrowing (:data:`_visible`) is recomputed here, so
        every caller -- initial load, add/edit/delete and the search
        handler itself -- renders the same filtered list the selection
        events index into. Phase 3 dropped the team_choice refresh: the
        editor's Team field is a read-only display of the selected
        record, not a choice.
        """
        self._visible = _visible_pairs(
            self.roster,
            _rider_pairs(self.roster),
            search_text=self._search_text,
        )
        self.view.show_riders(_pair_rows(self.roster, self._visible))

    def _show_add_form(self) -> None:
        """Reset the form: next free plate, nothing selected."""
        self._selected = None
        self.view.show_form(
            plate=self.roster.next_free_plate(), first_name="", last_name="", team=SOLO_TEAM_CHOICE
        )
        self.view.set_delete_enabled(enabled=False)

    def _entry_of(self, rider: Rider) -> Entry | None:
        """Return the entry *rider* is on, else ``None``."""
        return next((entry for entry in self.roster.entries if rider in entry.riders), None)

    def _show_record(self, entry: Entry, rider: Rider) -> None:
        """Fill the form from the record itself (B1/B3).

        The one place a selection -- a row click, a committed edit or a
        delete's re-render -- fills the form: the record's own values
        are shown verbatim, and delete_btn follows the record's own
        deletability.
        """
        self._selected = (entry, rider)
        self.view.show_form(
            plate=_rider_plate(self.roster, entry, rider),
            first_name=rider.first_name,
            last_name=rider.last_name,
            team=_team_value(entry),
        )
        self.view.set_delete_enabled(
            enabled=can_delete_entry(self.roster.status, has_data=entry.has_data)
        )
