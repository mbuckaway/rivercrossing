# SPDX-License-Identifier: GPL-3.0-only
"""Rider-issues presenter -- the "Check for Rider Issues..." dialog.

Drives a read-only list of every defect
:func:`~rivercrossing.rider_issues.rider_issues` still reports on a
live, in-memory :class:`~rivercrossing.roster.Roster`, and offers three
one-click fixes:

* a pooled size-1 team's lone rider converts back into their own solo
  entry (:meth:`~rivercrossing.roster.Roster.extract_rider_to_solo`);
* a ``missing-number`` rider is assigned the next free plate;
* a ``duplicate-number`` row renumbers the later claimant.

The two plate fixes write through the one shared
:meth:`~rivercrossing.roster.Roster.change_plate` dispatch, so each
shape picks the model-correct primitive (a pooled solo through
``change_solo_plate``, a pooled team member through
``change_pooled_rider_plate``, a relay entry through
``change_team_plate``) and cannot drift from the rider editor. Every
fix is gated on :func:`~rivercrossing.roster.can_edit_structure`; the
primitives raise :class:`~rivercrossing.roster.LockedError` off DRAFT,
and the presenter refuses first with the same operator-facing reason.

Selection enablement belongs to the view's own reconcile (it reads the
list control's real selection after every render and calls
:meth:`RiderIssuesPresenter.on_row_selected` or
:meth:`RiderIssuesPresenter.on_nothing_selected`); ``_load`` never
gates a button itself.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.rider_issues import RiderIssue, rider_issues
from rivercrossing.roster import PlateModel, RosterError, can_edit_structure

if TYPE_CHECKING:
    from rivercrossing.roster import Roster

__all__ = ["RiderIssueRow", "RiderIssuesPresenter", "RiderIssuesView"]


@dataclass(frozen=True, slots=True)
class RiderIssueRow:
    """One issue-list row (the dialog's frozen view-model)."""

    plate: str
    name: str
    message: str


@runtime_checkable
class RiderIssuesView(Protocol):
    """View surface for the rider-issues dialog."""

    def show_issues(self, rows: list[RiderIssueRow]) -> None:
        """Render the issue list from *rows*, in order."""
        ...

    def show_summary(self, text: str) -> None:
        """Render the issue-count summary line."""
        ...

    def set_convert_solo_enabled(self, *, enabled: bool) -> None:
        """Enable/disable convert_solo_btn for the current selection."""
        ...

    def set_assign_plate_enabled(self, *, enabled: bool) -> None:
        """Enable/disable assign_plate_btn for the current selection."""
        ...

    def set_renumber_enabled(self, *, enabled: bool) -> None:
        """Enable/disable renumber_btn for the current selection."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-operation message (roster_infobar)."""
        ...


def _row_name(issue: RiderIssue) -> str:
    """Return one issue row's Name: the entry's name, else the rider's.

    A team-of-one is entry-scoped (``rider is None``), so it falls
    back to the entry's own display name; an empty display name on a
    rider-scoped issue falls back to that rider's full name.
    """
    if issue.entry.display_name:
        return issue.entry.display_name
    return issue.rider.full_name if issue.rider is not None else ""


class RiderIssuesPresenter:
    """Presenter for the rider-issues dialog.

    Holds ``(view, roster)``: the view renders the issue list and the
    summary, the roster is the in-memory model both read from and --
    for the one corrective action -- written to. ``_selected`` is the
    current issue-list row's issue; ``did_change`` is True once
    :meth:`on_convert_solo` actually applied an edit, so the dialog's
    caller knows whether to reload any sibling list.
    """

    def __init__(self, view: RiderIssuesView, roster: Roster, *, load: bool = True) -> None:
        """Store the view and roster this presenter drives, then load.

        Args:
            view: The rider-issues dialog view this presenter drives.
            roster: The in-memory roster this presenter reads/writes.
            load: Render the initial report when ``True`` (the
                default, unchanged for every existing caller). The
                real view passes ``False``: it constructs the
                presenter before ``self.presenter`` is assigned, then
                calls :meth:`refresh` itself once the assignment is
                done, so the view's own selection reconcile always has
                a presenter to notify.
        """
        self.view = view
        self.roster = roster
        self._selected: RiderIssue | None = None
        self._issues: tuple[RiderIssue, ...] = ()
        self.did_change: bool = False
        if load:
            self._load()

    def on_row_selected(self, index: int) -> None:
        """Select issue-list row *index*; gate the three fix buttons.

        A row index outside this report -- a stale event after a render
        replaced the model -- selects nothing rather than raising, the
        same bounds guard ``views/rider_editor.py``'s own handler
        carries.
        """
        if not 0 <= index < len(self._issues):
            self.on_nothing_selected()
            return
        self._selected = self._issues[index]
        self.view.set_convert_solo_enabled(enabled=self._is_convertible(self._selected))
        self.view.set_assign_plate_enabled(enabled=self._can_assign_plate(self._selected))
        self.view.set_renumber_enabled(enabled=self._can_renumber(self._selected))

    def on_nothing_selected(self) -> None:
        """Clear the selection and disable every fix button.

        The view's selection reconcile calls this when the list control
        holds no live, in-range row, so the disable half of
        enablement is owned here rather than by ``_load`` (which would
        fight the reconcile on every render).
        """
        self._selected = None
        self.view.set_convert_solo_enabled(enabled=False)
        self.view.set_assign_plate_enabled(enabled=False)
        self.view.set_renumber_enabled(enabled=False)

    def refresh(self) -> None:
        """Re-render the report from the roster's current issues.

        The public counterpart to :meth:`_load`: the one entry point a
        caller outside this presenter uses to catch this dialog up
        with a roster change it never itself made -- the view re-lists
        after a nested team/rider editor closes, since that editor
        edits the same in-memory roster.
        """
        self._load()

    def on_open_editor(self) -> str:
        """Return which editor the selection opens, "" if none."""
        if self._selected is None:
            return ""
        return "team" if self._selected.kind == "team-of-one" else "rider"

    def selected_team_name(self) -> str:
        """Return the selected issue's team name, "" if none.

        The team editor's name-keyed preselect key
        (``TeamEditor.select_team_by_name``); a team-of-one is the only
        issue that opens the teams editor.
        """
        if self._selected is None:
            return ""
        return self._selected.entry.display_name

    def selected_plate(self) -> str:
        """Return the rider editor's preselect plate, "" if none.

        A rider-scoped issue carries its rider's own plate; a relay
        duplicate-number issue is entry-scoped (``rider is None``), so
        the entry's own plate stands in. A blank rider plate (a
        missing-number issue) falls back to the entry's plate, which
        matches nothing and leaves the editor on its add form.
        """
        if self._selected is None:
            return ""
        rider = self._selected.rider
        if rider is not None and rider.plate:
            return rider.plate
        return self._selected.entry.plate

    def on_convert_solo(self) -> bool:
        """Convert the selected team-of-one into a solo entry, if legal.

        A refusal (nothing selected, a non-team issue, the wrong plate
        model, or a ride that has left DRAFT) shows via
        :meth:`RiderIssuesView.show_validation` and returns ``False``,
        never raising past this handler -- the same refusal shape as
        every other presenter here. On success ``did_change`` is set
        and the report re-renders.

        Returns:
            Whether the conversion actually applied.
        """
        selected = self._selected
        if selected is None:
            self.view.show_validation("select a team-of-one to convert")
            return False
        if not self._is_convertible(selected):
            self.view.show_validation(self._convert_refusal(selected))
            return False
        try:
            self.roster.extract_rider_to_solo(selected.entry.riders[0])
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        self.did_change = True
        self._load()
        return True

    def on_assign_plate(self) -> bool:
        """Give the selected missing-number rider the next free plate.

        Gated on :func:`~rivercrossing.roster.can_edit_structure` before
        any write (the primitives raise ``LockedError`` off DRAFT); a
        refusal -- nothing selected, the wrong kind, a started ride, or
        a roster error from the write -- shows via
        :meth:`RiderIssuesView.show_validation` and returns ``False``.
        On success ``did_change`` is set and the report re-renders.

        Returns:
            Whether the plate assignment actually applied.
        """
        selected = self._selected
        if selected is None or selected.kind != "missing-number":
            self.view.show_validation("select a missing-number issue to assign a plate")
            return False
        if not can_edit_structure(self.roster.status):
            self.view.show_validation("plates can only be assigned while the ride is draft")
            return False
        return self._apply_plate_fix(selected, plate=self.roster.next_free_plate())

    def on_renumber(self) -> bool:
        """Renumber the selected duplicate-number's later claimant.

        The issue already names the later claimant (the entry/rider the
        duplicate check flagged), so the fix writes the next free plate
        onto exactly that one. Gated on
        :func:`~rivercrossing.roster.can_edit_structure` before any
        write; a refusal shows via
        :meth:`RiderIssuesView.show_validation` and returns ``False``.

        Returns:
            Whether the renumber actually applied.
        """
        selected = self._selected
        if selected is None or selected.kind != "duplicate-number":
            self.view.show_validation("select a duplicate-number issue to renumber")
            return False
        if not can_edit_structure(self.roster.status):
            self.view.show_validation("plates can only be changed while the ride is draft")
            return False
        return self._apply_plate_fix(selected, plate=self.roster.next_free_plate())

    def _apply_plate_fix(self, issue: RiderIssue, *, plate: str) -> bool:
        """Write *plate* through the shared dispatch, then re-render.

        The one shape-aware write both plate fixes share: the roster's
        :meth:`~rivercrossing.roster.Roster.change_plate` picks the
        model-correct primitive from *issue*'s entry (and rider), so a
        pooled team member never routes through the solo primitive that
        would refuse it.
        """
        try:
            self.roster.change_plate(issue.entry, issue.rider, plate=plate)
        except RosterError as exc:
            self.view.show_validation(str(exc))
            return False
        self.did_change = True
        self._load()
        return True

    def _is_convertible(self, issue: RiderIssue) -> bool:
        """Return whether *issue* may be converted to a solo entry."""
        return (
            issue.kind == "team-of-one"
            and bool(issue.entry.riders)
            and self.roster.plate_model is PlateModel.RIDER_POOLED
            and can_edit_structure(self.roster.status)
        )

    def _can_assign_plate(self, issue: RiderIssue) -> bool:
        """Return whether the Assign Plate fix may run on *issue*."""
        return issue.kind == "missing-number" and can_edit_structure(self.roster.status)

    def _can_renumber(self, issue: RiderIssue) -> bool:
        """Return whether the Renumber fix may run on *issue*."""
        return issue.kind == "duplicate-number" and can_edit_structure(self.roster.status)

    def _convert_refusal(self, issue: RiderIssue) -> str:
        """Return why *issue* cannot currently be converted."""
        if issue.kind != "team-of-one":
            return "only a team-of-one can be converted to solo"
        if not issue.entry.riders:
            # W8: an empty team is a size-0 "team-of-one"; there is no
            # rider to extract, so Convert must say so, never raise.
            return "an empty team has no rider to convert to solo"
        if self.roster.plate_model is not PlateModel.RIDER_POOLED:
            return "convert to solo requires a rider-pooled ride"
        return "a team-of-one can only be converted while the ride is draft"

    def _load(self) -> None:
        """Render the full report from the roster's current issues.

        The retained selection survives a re-render only when its exact
        issue is still reported; a roster change that fixed or removed
        it clears ``_selected``. Loading never gates a button itself --
        the view's own reconcile reads the list control's real
        selection right after ``show_issues`` and notifies this
        presenter through :meth:`on_row_selected` or
        :meth:`on_nothing_selected`, so enable and disable have one
        owner and ``_load`` cannot recurse through ``refresh``.
        """
        issues = rider_issues(self.roster)
        self._issues = issues
        if self._selected is not None and self._selected not in issues:
            self._selected = None
        self.view.show_issues(
            [
                RiderIssueRow(
                    plate=issue.entry.plate,
                    name=_row_name(issue),
                    message=issue.message,
                )
                for issue in issues
            ]
        )
        self.view.show_summary(f"{len(issues)} rider issue(s)")
