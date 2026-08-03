# SPDX-License-Identifier: GPL-3.0-only
"""Setup presenter -- ride_setup_dlg (7a), ride configuration.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource


@runtime_checkable
class SetupView(Protocol):
    """View surface for the ride setup dialog (ride_setup_dlg, 7a)."""

    def set_team_fields_enabled(self, *, enabled: bool) -> None:
        """Enable relay_radio/team_size_spin when mixed_radio is set."""
        ...

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock the entry/plate-model group (relay post-start, R-17)."""
        ...


class SetupPresenter:
    """Presenter for the ride setup dialog (ride_setup_dlg, 7a).

    No-op beyond storing its collaborators; Phase 5 wires the
    entry-mode radio dependency and the post-start lock this view
    exposes, plus persisting tiebreak_list reorders.
    """

    def __init__(self, view: SetupView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
