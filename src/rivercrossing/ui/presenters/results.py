# SPDX-License-Identifier: GPL-3.0-only
"""Results presenter -- results_frame (1f), standings and publishing.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.htmlexport import ExportOptions
    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow


@runtime_checkable
class ResultsView(Protocol):
    """View surface for the results window (results_frame, 1f)."""

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """Render standings_list (draw-required rows carry a flag)."""
        ...

    def set_stale(self, *, stale: bool) -> None:
        """Show/hide stale_infobar after reopened corrections."""
        ...

    def show_publish_options(self, options: ExportOptions) -> None:
        """Reflect the publish checkboxes (show_times_chk and peers)."""
        ...


class ResultsPresenter:
    """Presenter for the results window (results_frame, 1f).

    No-op beyond storing its collaborators; Phase 5 wires live
    re-ranking on tiebreak_list reorder and the publish-option flags
    this view exposes.
    """

    def __init__(self, view: ResultsView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
