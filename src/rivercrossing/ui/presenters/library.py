# SPDX-License-Identifier: GPL-3.0-only
"""Library presenter -- ride_library_dlg (1g), the ride list.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import RideSummary

__all__ = ["LibraryView"]


@runtime_checkable
class LibraryView(Protocol):
    """View surface for the ride library (ride_library_dlg, 1g)."""

    def show_rides(self, rows: list[RideSummary]) -> None:
        """Render rides_list."""
        ...

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Disable wxID_DELETE while the selected ride is RUNNING."""
        ...
