# SPDX-License-Identifier: GPL-3.0-only
"""Riders presenter -- rider_editor_dlg (1d/2b) + csv_preview_dlg (3e).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource, RiderRow


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


@runtime_checkable
class RidersView(Protocol):
    """View surface for the rider editor and its CSV import preview."""

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render riders_list."""
        ...

    def show_team_choices(self, names: list[str]) -> None:
        """Populate team_choice ("-- solo --"/"New team..." + names)."""
        ...

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """Disable delete_btn once the entry has data (R-15)."""
        ...

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """Render csv_preview_dlg's summary line and conflicts."""
        ...

    def set_import_enabled(self, *, enabled: bool) -> None:
        """Gate wxID_OK "Import" while conflicts > 0."""
        ...


class RidersPresenter:
    """Presenter for the rider editor and its CSV import preview.

    No-op beyond storing its collaborators; Phase 5 wires roster
    edits and the E3.3 CSV preview-then-commit flow to this view.
    """

    def __init__(self, view: RidersView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
