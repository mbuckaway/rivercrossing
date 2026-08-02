# SPDX-License-Identifier: GPL-3.0-only
"""Settings presenter -- settings_dlg (3a), app-wide preferences.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource


@dataclass(frozen=True, slots=True)
class AppSettings:
    """The settings_dlg fields (appearance, sound, times, zoom)."""

    appearance: str
    sound_on: bool
    hide_times: bool
    zoom_percent: int


@runtime_checkable
class SettingsView(Protocol):
    """View surface for the settings dialog (settings_dlg, 3a)."""

    def show_settings(self, settings: AppSettings) -> None:
        """Render the current appearance/sound/times/zoom values."""
        ...


class SettingsPresenter:
    """Presenter for the settings dialog (settings_dlg, 3a).

    No-op beyond storing its collaborators; Phase 5 wires the
    appearance radios, sound/hide-times toggles, zoom choice and
    backup_now_btn to this view.
    """

    def __init__(self, view: SettingsView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
