# SPDX-License-Identifier: GPL-3.0-only
"""Rider-issues presenter -- the "Check for Rider Issues..." dialog.

Drives a read-only list of every defect
:func:`~rivercrossing.rider_issues.rider_issues` still reports on a
live, in-memory :class:`~rivercrossing.roster.Roster`, and offers one
corrective action: converting a pooled size-1 team's lone rider back
into their own solo entry (:meth:`~rivercrossing.roster.Roster.
extract_rider_to_solo`) -- the only issue with a safe one-click fix.

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

    def __init__(self, view: RiderIssuesView, roster: Roster) -> None:
        """Store the view and roster this presenter drives, then load.

        Args:
            view: The rider-issues dialog view this presenter drives.
            roster: The in-memory roster this presenter reads/writes.
        """
        self.view = view
        self.roster = roster
        self._selected: RiderIssue | None = None
        self._issues: tuple[RiderIssue, ...] = ()
        self.did_change: bool = False
        self._load()

    def on_row_selected(self, index: int) -> None:
        """Select issue-list row *index*; gate convert_solo_btn."""
        self._selected = self._issues[index]
        self.view.set_convert_solo_enabled(enabled=self._is_convertible(self._selected))

    def on_open_editor(self) -> str:
        """Return which editor the selection opens, "" if none."""
        if self._selected is None:
            return ""
        return "team" if self._selected.kind == "team-of-one" else "rider"

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

    def _is_convertible(self, issue: RiderIssue) -> bool:
        """Return whether *issue* may be converted to a solo entry."""
        return (
            issue.kind == "team-of-one"
            and self.roster.plate_model is PlateModel.RIDER_POOLED
            and can_edit_structure(self.roster.status)
        )

    def _convert_refusal(self, issue: RiderIssue) -> str:
        """Return why *issue* cannot currently be converted."""
        if issue.kind != "team-of-one":
            return "only a team-of-one can be converted to solo"
        if self.roster.plate_model is not PlateModel.RIDER_POOLED:
            return "convert to solo requires a rider-pooled ride"
        return "a team-of-one can only be converted while the ride is draft"

    def _load(self) -> None:
        """Render the full report from the roster's current issues."""
        issues = rider_issues(self.roster)
        self._issues = issues
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
        self.view.set_convert_solo_enabled(enabled=False)
