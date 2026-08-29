# SPDX-License-Identifier: GPL-3.0-only
"""Audit presenter -- audit_dlg, the read-only audit trail (R-38).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import AuditRow, DataSource

__all__ = [
    "ACTION_BUCKETS",
    "ALL_ACTIONS",
    "AuditPresenter",
    "AuditView",
]

# audit_dlg's action_choice default: no action filter (audit.xrc's own
# XRC lands the choice on index 0, which is this label).
ALL_ACTIONS = "All actions"

# §15-D's action-bucket mapping, the audit_dlg filter set (audit.xrc's
# six items). The actions in NO bucket -- start, continue,
# set_start_time, stop, finish, reopen -- appear only under
# "All actions", exactly as the canvas draws them.
ACTION_BUCKETS: dict[str, frozenset[str]] = {
    "Crossing edits": frozenset(
        {
            "record_crossing",
            "undo",
            "edit_crossing",
            "void_crossing",
            "add_crossing_at",
            "reassign",
        }
    ),
    "Card deals/voids": frozenset({"deal_manual", "confirm_held", "void_held", "void_card"}),
    "Moves": frozenset(
        {
            "move_rider",
            "add_rider_to_team",
            "extract_rider_to_solo",
            "change_solo_plate",
            "change_pooled_rider_plate",
            "change_team_plate",
        }
    ),
    "DNF": frozenset({"dnf"}),
    "Shoe reshuffle": frozenset({"shoe_reshuffle"}),
}


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
    the §15-D action bucket -- and renders through
    :meth:`AuditView.show_audit_rows`. A deep-linked entry (entry
    detail's audit button, R-38) pre-fills the search through
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
            action_filter: The action_choice bucket to start on;
                defaults to :data:`ALL_ACTIONS` (no action filter).
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

    def on_action_selected(self, bucket: str) -> None:
        """Handle an action_choice selection (§15-D bucket filter)."""
        self._action_filter = bucket
        self.refresh()

    def _filtered(self, rows: list[AuditRow]) -> list[AuditRow]:
        """Return *rows* narrowed by the current search and bucket.

        Both filters narrow the same query (audit.xrc's own note): the
        search text matches a row's plate or its entry's display name,
        and the chosen bucket keeps only its mapped actions. An empty
        search and :data:`ALL_ACTIONS` filter nothing.
        """
        needle = self._entry_filter.strip().casefold()
        actions = ACTION_BUCKETS.get(self._action_filter)
        return [
            row
            for row in rows
            if (not needle or self._matches(row, needle))
            and (actions is None or row.action in actions)
        ]

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
