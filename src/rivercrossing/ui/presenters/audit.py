# SPDX-License-Identifier: GPL-3.0-only
"""Audit presenter -- audit_dlg, the read-only audit trail (R-38).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import AuditRow, DataSource

__all__ = [
    "ACTION_CHOICES",
    "ALL_ACTIONS",
    "AuditPresenter",
    "AuditView",
]

# audit_dlg's action_choice default: no action filter (audit.xrc's own
# XRC lands the choice on index 0, which is this label).
ALL_ACTIONS = "All actions"

# audit.xrc's flat action_choice, in the .xrc's own declared order: one
# row per audited action, each carrying the label the dropdown draws and
# the action string the audit trail records. Every action the app can
# audit appears exactly once -- the engine's own event actions
# (``ride.py``'s ``apply`` dispatch) plus the roster mutations Store
# persists as ``user_action`` rows -- so the filter is a straight
# equality test on the row's own action. ``test_audit.py`` pins this
# tuple against the .xrc's item order, so the two cannot drift.
ACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("Record Crossing", "record_crossing"),
    ("Add Crossing at Time", "add_crossing_at"),
    ("Edit Crossing", "edit_crossing"),
    ("Void Crossing", "void_crossing"),
    ("Undo Last Crossing", "undo"),
    ("Reassign Crossing", "reassign"),
    ("Record Miss", "record_miss"),
    ("Assign Plate to Miss", "assign_plate_to_miss"),
    ("Deal Bonus Card", "deal_manual"),
    ("Confirm Held Card", "confirm_held"),
    ("Void Held Card", "void_held"),
    ("Return to Held", "return_to_held"),
    ("Void Card", "void_card"),
    ("Move Rider", "move_rider"),
    ("Add Rider to Team", "add_rider_to_team"),
    ("Extract Rider to Solo", "extract_rider_to_solo"),
    ("Change Solo Plate", "change_solo_plate"),
    ("Change Pooled Rider Plate", "change_pooled_rider_plate"),
    ("Change Team Plate", "change_team_plate"),
    ("Remove Rider", "remove_rider"),
    ("Mark DNF", "dnf"),
    ("Shoe Reshuffle", "shoe_reshuffle"),
    ("Start Ride", "start"),
    ("Continue Ride", "continue"),
    ("Set Start Time", "set_start_time"),
    ("Stop Ride", "stop"),
    ("Finish Ride", "finish"),
    ("Reopen Ride", "reopen"),
)


@runtime_checkable
class AuditView(Protocol):
    """View surface for the audit trail dialog (audit_dlg)."""

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """Render audit_list, newest first."""
        ...

    def set_entry_filter(self, entry: str) -> None:
        """Pre-fill audit_search when deep-linked from entry detail."""
        ...


class AuditPresenter:
    """Presenter for the audit trail dialog (audit_dlg, R-38).

    E7.3.1 makes the E7.2.1 no-op live: the presenter reads
    :meth:`data_source.audit_rows` (newest first), narrows by the two
    filters -- ``audit_search`` matches the entry's plate OR display
    name (resolved through the optional roster, so a pooled team
    member's plate finds its team name), and ``action_choice`` matches
    the one action it names (:data:`ACTION_CHOICES`) -- and renders
    through :meth:`AuditView.show_audit_rows`. A deep-linked entry
    (entry detail's audit button, R-38) pre-fills the search through
    :meth:`AuditView.set_entry_filter` and starts the search on that
    plate.
    """

    def __init__(  # noqa: PLR0913 -- (view, data_source) + the three filter seams
        self,
        view: AuditView,
        data_source: DataSource,
        *,
        roster: Roster | None = None,
        entry_filter: str = "",
        action_filter: str = ALL_ACTIONS,
    ) -> None:
        """Store the collaborators and render the first filtered view.

        Args:
            view: The audit dialog view this presenter drives.
            data_source: The read-only display-data seam (its
                ``audit_rows`` feeds the list).
            roster: The live roster, resolving a recorded plate to its
                entry's display name for the search filter; ``None``
                searches by plate alone.
            entry_filter: The deep-linked entry's plate (entry detail's
                audit button, R-38); pre-fills audit_search and starts
                the search narrowed to it.
            action_filter: The audited action to start on, or
                :data:`ALL_ACTIONS` for no action filter.
        """
        self.view = view
        self.data_source = data_source
        self._roster = roster
        self._entry_filter = entry_filter
        self._action_filter = action_filter
        if entry_filter:
            view.set_entry_filter(entry_filter)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the source and re-render the filtered rows."""
        self.view.show_audit_rows(self._filtered(self.data_source.audit_rows()))

    def on_search_text(self, text: str) -> None:
        """Handle an audit_search text change (plate/name filter)."""
        self._entry_filter = text
        self.refresh()

    def on_action_selected(self, action: str) -> None:
        """Handle an action_choice selection (one action's rows).

        *action* is an audited action string
        (:data:`ACTION_CHOICES`), or :data:`ALL_ACTIONS` -- the view
        resolves the dropdown's label before forwarding.
        """
        self._action_filter = action
        self.refresh()

    def _filtered(self, rows: list[AuditRow]) -> list[AuditRow]:
        """Return *rows* narrowed by the current search and action.

        Both filters narrow the same query (audit.xrc's own note): the
        search text matches a row's plate or its entry's display name,
        and the chosen action keeps only its own rows. An empty search
        and :data:`ALL_ACTIONS` filter nothing.
        """
        needle = self._entry_filter.strip().casefold()
        return [
            row
            for row in rows
            if (not needle or self._matches(row, needle)) and self._matches_action(row)
        ]

    def _matches_action(self, row: AuditRow) -> bool:
        """Return whether *row* survives the current action filter.

        :data:`ALL_ACTIONS` -- ``action_choice``'s first entry -- is the
        no-filter value, so it keeps every row; any other selection
        keeps only the rows whose own action is exactly that one.
        """
        if self._action_filter == ALL_ACTIONS:
            return True
        return row.action == self._action_filter

    def _matches(self, row: AuditRow, needle: str) -> bool:
        """Return whether *row*'s plate or display name has *needle*."""
        if needle in row.entry.casefold():
            return True
        display = self._display_name(row.entry)
        return needle in display.casefold()

    def _display_name(self, plate: str) -> str:
        """Return the roster entry *plate* resolves to, or ``""``.

        ``Roster.resolve_plate`` also resolves a rider_pooled member's
        own plate to its team, so a team rider's rows match the team's
        display name.
        """
        if self._roster is None:
            return ""
        entry = self._roster.resolve_plate(plate)
        return entry.display_name if entry is not None else ""
