# SPDX-License-Identifier: GPL-3.0-only
"""Detail presenter -- entry_detail_dlg (1e) and edit_crossing_dlg (7b).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource, EntryDetail


@runtime_checkable
class DetailView(Protocol):
    """View surface for the entry detail dialog (entry_detail_dlg)."""

    def show_entry(self, detail: EntryDetail) -> None:
        """Render the header, members, cards held and laps_list."""
        ...

    def set_move_rider_enabled(self, *, enabled: bool) -> None:
        """Enable move_rider_btn only for rider-pooled entries."""
        ...

    def show_edit_crossing(self, *, adding: bool, plate: str, time: str) -> None:
        """Open edit_crossing_dlg, titled for edit vs add-at-time."""
        ...


class DetailPresenter:
    """Presenter for the entry detail and edit crossing dialogs.

    No-op beyond storing its collaborators; Phase 5 wires row
    selection and the edit/deal/void/move/DNF actions to this view.
    """

    def __init__(self, view: DetailView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
