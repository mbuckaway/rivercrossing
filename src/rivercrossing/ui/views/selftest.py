# SPDX-License-Identifier: GPL-3.0-only
"""``SelfTestDialog``: ``selftest_dlg`` (3f), the evaluator self-test.

Wires ``selftest_output`` (a read-only monospace ``wxTextCtrl``,
dialogs.xrc's own comment explains the "teletype" font choice) and
``rerun_btn`` to :class:`~rivercrossing.ui.presenters.selftest.
SelfTestPresenter`, which runs ``rivercrossing.hands.self_test`` --
the frozen R-44 hook -- and renders its report into the canvas's
dotted PASS/FAIL lines. ``wxID_CLOSE`` needs no wiring here: every
dialog's Escape/click-to-dismiss handling for that stock id already
comes from ``ui.views.dialogs.wire_close_button``, applied once by
``ui.views.dialogs.run_dialog`` around every dialog this codebase
shows (module docstring there).
"""

from typing import Any

import wx

from rivercrossing.hands import self_test
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.selftest import SelfTestPresenter
from rivercrossing.ui.views._support import find_control

__all__ = ["SelfTestDialog"]


class SelfTestDialog:
    """Code-side behaviour for ``selftest_dlg`` (3f).

    Implements ``SelfTestView`` (module-skeletons.md's presenter
    contract) directly on the dialog's own controls: no separate
    view-model row type exists for this dialog, since its whole
    content is the four rendered report lines.
    """

    def __init__(self, dialog: wx.Dialog) -> None:
        """Decorate an already-loaded ``selftest_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``dialogs.xrc``.
        """
        self.dialog = dialog
        self.output = self._find(ids.SELFTEST_OUTPUT, wx.TextCtrl)
        self.rerun_button = self._find(ids.RERUN_BTN, wx.Button)

        self.presenter = SelfTestPresenter(self, self_test)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_rerun, self.rerun_button)

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.
        """
        return find_control(self.dialog, name, expected_type)

    def _on_rerun(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``rerun_btn``: ask the presenter to run again."""
        event.Skip()
        self.presenter.on_rerun()

    def show_lines(self, lines: tuple[str, ...]) -> None:
        """Render *lines* into ``selftest_output`` (SelfTestView)."""
        self.output.SetValue("\n".join(lines))

    def set_rerun_busy(self, *, busy: bool) -> None:
        """Toggle ``rerun_btn``'s enabled state (SelfTestView)."""
        self.rerun_button.Enable(not busy)
