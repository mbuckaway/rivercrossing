# SPDX-License-Identifier: GPL-3.0-only
"""``SettingsDialog``: ``settings_dlg`` (3a), the app-wide preferences.

E8.1.2 finishes the dialog E8.1.1's presenter stubbed: this thin view
renders the current :class:`AppSettings` into the appearance radios,
the sound/hide-times checkboxes and the zoom choice, and OK collects a
fresh :class:`AppSettings` for the app's ``on_save`` callback (which
persists + applies it -- the appearance mirror to the View-menu
radios). ``backup_now_btn`` stays inert: File ▸ Back Up Database… is a
later epic's task, and wiring a button to nothing would fake a feature
(settings.xrc's own comment).
"""

from typing import TYPE_CHECKING, Any

import wx

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.settings import ZOOM_LADDER, AppSettings, appearance_for_radio
from rivercrossing.ui.theme import ThemeMode
from rivercrossing.ui.views._support import find_control

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["SettingsDialog"]


class SettingsDialog:
    """Code-side behaviour for ``settings_dlg`` (3a).

    Implements ``SettingsView`` (module-skeletons.md's presenter
    contract) directly on the dialog's own controls: ``show_settings``
    renders the current :class:`AppSettings`, and OK collects a fresh
    one (carrying over the two layout fields, which have no control)
    and hands it to ``on_save``.
    """

    def __init__(
        self,
        dialog: wx.Dialog,
        *,
        settings: AppSettings,
        on_save: Callable[[AppSettings], None],
    ) -> None:
        """Decorate an already-loaded ``settings_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``settings.xrc``.
            settings: The current :class:`AppSettings` to render; its
                ``splitter_sash``/``window_geometry`` are carried into
                whatever OK collects.
            on_save: Called with the collected settings when OK is
                clicked; the app bootstrap wires it to persist + apply.
        """
        self.dialog = dialog
        self.on_save = on_save

        self.system_radio = self._find(ids.APPEARANCE_SYSTEM_RADIO, wx.RadioButton)
        self.light_radio = self._find(ids.APPEARANCE_LIGHT_RADIO, wx.RadioButton)
        self.dark_radio = self._find(ids.APPEARANCE_DARK_RADIO, wx.RadioButton)
        self.sound_chk = self._find(ids.SOUND_CHK, wx.CheckBox)
        self.hide_times_chk = self._find(ids.HIDE_TIMES_CHK, wx.CheckBox)
        self.zoom_choice = self._find(ids.ZOOM_CHOICE, wx.Choice)

        self.show_settings(settings)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.
        """
        return find_control(self.dialog, name, expected_type)

    def show_settings(self, settings: AppSettings) -> None:
        """Render *settings* into the dialog's controls (SettingsView).

        Each radio is set explicitly (one true, the others false), so
        the render never depends on wx's radio-group auto-uncheck.
        ``load_settings`` clamps ``zoom_percent`` onto
        :data:`ZOOM_LADDER`, so ``index`` always resolves.
        """
        self._settings = settings
        self.system_radio.SetValue(settings.appearance == ThemeMode.SYSTEM.value)
        self.light_radio.SetValue(settings.appearance == ThemeMode.LIGHT.value)
        self.dark_radio.SetValue(settings.appearance == ThemeMode.DARK.value)
        self.sound_chk.SetValue(settings.sound_on)
        self.hide_times_chk.SetValue(settings.hide_times)
        self.zoom_choice.SetSelection(ZOOM_LADDER.index(settings.zoom_percent))

    def collect_settings(self) -> AppSettings:
        """Read the controls into a fresh :class:`AppSettings`.

        The two layout fields (``splitter_sash``/``window_geometry``)
        have no dialog control, so the current values carry over
        unchanged.
        """
        return AppSettings(
            appearance=appearance_for_radio(
                light=self.light_radio.GetValue(),
                dark=self.dark_radio.GetValue(),
            ),
            sound_on=bool(self.sound_chk.GetValue()),
            hide_times=bool(self.hide_times_chk.GetValue()),
            zoom_percent=ZOOM_LADDER[self.zoom_choice.GetSelection()],
            splitter_sash=self._settings.splitter_sash,
            window_geometry=self._settings.window_geometry,
        )

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx handler signature; EndModal is explicit, no Skip needed
        """Collect the controls, fire ``on_save``, then end the modal.

        Cancel needs no handler: wx binds Escape and a click on
        ``wxID_CANCEL`` itself, and ``dialogs.run_dialog``'s
        ``wire_close_button`` is a no-op for a Cancel dialog.
        """
        self.on_save(self.collect_settings())
        self.dialog.EndModal(wx.ID_OK)
