# SPDX-License-Identifier: GPL-3.0-only
"""Library presenter -- ride_library_dlg (1g), the ride list.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource, RideSummary


@runtime_checkable
class LibraryView(Protocol):
    """View surface for the ride library (ride_library_dlg, 1g)."""

    def show_rides(self, rows: list[RideSummary]) -> None:
        """Render rides_list."""
        ...

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Disable wxID_DELETE while the selected ride is RUNNING."""
        ...


class LibraryPresenter:
    """Presenter for the ride library (ride_library_dlg, 1g).

    No-op beyond storing its collaborators; Phase 5 wires row
    selection and Open/New/Duplicate/Delete to this view.
    """

    def __init__(self, view: LibraryView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
