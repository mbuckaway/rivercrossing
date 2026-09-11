# SPDX-License-Identifier: GPL-3.0-only
"""``SettingsDialog``: ``settings_dlg`` (3a), the app-wide preferences.

E8.1.2 finishes the dialog E8.1.1's presenter stubbed: this thin view
renders the current :class:`AppSettings` into the appearance radios
and the sound/hide-times/verbose-log checkboxes, and OK collects a
fresh :class:`AppSettings` for the app's ``on_save`` callback (which
persists + applies it). W13 (notes #12): the text-zoom choice left
the dialog -- View ▸ Zoom is the single zoom surface -- so
``zoom_percent`` carries through OK unchanged, like the layout
fields. ux-polish wires ``backup_now_btn``: the app hands this view
an ``on_backup_now`` callback that runs the real R-54 manual backup
(File ▸ Back Up Database…'s own action) and surfaces the written path
or failure, so the button is no longer an inert fake (settings.xrc's
own comment predates the wiring).
"""

from typing import TYPE_CHECKING, Any

import wx

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.settings import AppSettings, appearance_for_radio
from rivercrossing.ui.theme import ThemeMode
from rivercrossing.ui.views._support import find_control

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["SettingsDialog"]


class SettingsDialog:
    """Code-side behaviour for ``settings_dlg`` (3a).

    Implements ``SettingsView`` (module-skeletons.md's presenter
    contract) directly on the dialog's own controls: ``show_settings``
    renders the current :class:`AppSettings`, OK collects a fresh one
    (carrying over the zoom and two layout fields, which have no
    control -- zoom lives on the View menu, W13) and hands it to
    ``on_save``, and ``backup_now_btn`` fires the app's
    ``on_backup_now`` seam (ux-polish: the R-54 manual backup File ▸
    Back Up Database… runs; the dialog stays open so the operator can
    keep editing). F1's ``verbose_log_chk`` is rendered and collected
    exactly like ``sound_chk``.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, settings, on_save, on_backup_now): the view's four construction seams
        self,
        dialog: wx.Dialog,
        *,
        settings: AppSettings,
        on_save: Callable[[AppSettings], None],
        on_backup_now: Callable[[], None],
    ) -> None:
        """Decorate an already-loaded ``settings_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``settings.xrc``.
            settings: The current :class:`AppSettings` to render; its
                ``zoom_percent``/``splitter_sash``/``window_geometry``
                are carried into whatever OK collects (none has a
                dialog control; zoom lives on the View menu, W13).
            on_save: Called with the collected settings when OK is
                clicked; the app bootstrap wires it to persist + apply.
            on_backup_now: Called when ``backup_now_btn`` is clicked;
                the app bootstrap wires it to run the manual database
                backup and surface the written path (or failure).
        """
        self.dialog = dialog
        self.on_save = on_save
        self.on_backup_now = on_backup_now

        self.system_radio = self._find(ids.APPEARANCE_SYSTEM_RADIO, wx.RadioButton)
        self.light_radio = self._find(ids.APPEARANCE_LIGHT_RADIO, wx.RadioButton)
        self.dark_radio = self._find(ids.APPEARANCE_DARK_RADIO, wx.RadioButton)
        self.sound_chk = self._find(ids.SOUND_CHK, wx.CheckBox)
        self.hide_times_chk = self._find(ids.HIDE_TIMES_CHK, wx.CheckBox)
        self.verbose_log_chk = self._find(ids.VERBOSE_LOG_CHK, wx.CheckBox)
        self.backup_now_btn = self._find(ids.BACKUP_NOW_BTN, wx.Button)

        self.show_settings(settings)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_backup_now, self.backup_now_btn)

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
        ``zoom_percent`` has no control since W13 (View ▸ Zoom is the
        single zoom surface); it is carried through OK untouched.
        """
        self._settings = settings
        self.system_radio.SetValue(settings.appearance == ThemeMode.SYSTEM.value)
        self.light_radio.SetValue(settings.appearance == ThemeMode.LIGHT.value)
        self.dark_radio.SetValue(settings.appearance == ThemeMode.DARK.value)
        self.sound_chk.SetValue(settings.sound_on)
        self.hide_times_chk.SetValue(settings.hide_times)
        self.verbose_log_chk.SetValue(settings.verbose_logging)

    def collect_settings(self) -> AppSettings:
        """Read the controls into a fresh :class:`AppSettings`.

        The zoom and two layout fields (``zoom_percent``/
        ``splitter_sash``/``window_geometry``) have no dialog control,
        so the current values carry over unchanged.
        """
        return AppSettings(
            appearance=appearance_for_radio(
                light=self.light_radio.GetValue(),
                dark=self.dark_radio.GetValue(),
            ),
            sound_on=bool(self.sound_chk.GetValue()),
            hide_times=bool(self.hide_times_chk.GetValue()),
            verbose_logging=bool(self.verbose_log_chk.GetValue()),
            zoom_percent=self._settings.zoom_percent,
            splitter_sash=self._settings.splitter_sash,
            window_geometry=self._settings.window_geometry,
        )

    def _on_backup_now(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx handler signature
        """Run the app's manual-backup seam (ux-polish, R-54).

        The dialog stays open after the backup, exactly as a real
        settings panel behaves: the app's callback writes the backup
        and surfaces the path (or failure) on the main frame's status
        bar, and the operator keeps or closes the dialog as usual.
        """
        self.on_backup_now()

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx handler signature; EndModal is explicit, no Skip needed
        """Collect the controls, fire ``on_save``, then end the modal.

        Cancel needs no handler: wx binds Escape and a click on
        ``wxID_CANCEL`` itself, and ``dialogs.run_dialog``'s
        ``wire_close_button`` is a no-op for a Cancel dialog.
        """
        self.on_save(self.collect_settings())
        self.dialog.EndModal(wx.ID_OK)
