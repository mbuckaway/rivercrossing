# SPDX-License-Identifier: GPL-3.0-only
"""Show one-shot alerts and destructive confirms as native wx dialogs.

XRC-authored dialogs load and show through ``views.dialogs.run_dialog``,
which wires their stock ids and light-mode panel tint. The four
functions here cover the code-side native dialogs that need no XRC
resource: one-shot alerts (``show_info``, ``show_warning``,
``show_error``) and the destructive confirms (``show_confirm``). Each
function constructs a plain ``wx.MessageDialog``, shows it modally,
destroys it, and returns the modal id.

The XRC wiring deliberately does not apply here. The panel tint is for
XRC-drawn dialogs whose background wx cannot restyle natively; a
native message dialog must not be tinted. Escape already ends a
``wx.MessageDialog`` through its stock ``wxID_CANCEL``, so no
close-button wiring is needed, and ``ShowModal`` returns focus to the
owning window itself, so no focus-restore helper runs either.
"""

from typing import TYPE_CHECKING

from rivercrossing.ui import require_wx

if TYPE_CHECKING:
    import wx  # mypy-only: annotations need the module symbol (py3.14 lazy)

__all__ = ["show_confirm", "show_error", "show_info", "show_warning"]

wx = require_wx()


def show_info(parent: wx.Window | None, title: str, message: str) -> int:
    """Show *message* as an information alert and return the modal id.

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The text shown under the caption.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` when the operator closes
        the alert).
    """
    dialog = wx.MessageDialog(parent, message, title, wx.OK | wx.CENTRE | wx.ICON_INFORMATION)
    try:
        return int(dialog.ShowModal())
    finally:
        dialog.Destroy()


def show_warning(parent: wx.Window | None, title: str, message: str) -> int:
    """Show *message* as a warning alert and return the modal id.

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The text shown under the caption.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` when the operator closes
        the alert).
    """
    dialog = wx.MessageDialog(parent, message, title, wx.OK | wx.CENTRE | wx.ICON_WARNING)
    try:
        return int(dialog.ShowModal())
    finally:
        dialog.Destroy()


def show_error(parent: wx.Window | None, title: str, message: str) -> int:
    """Show *message* as an error alert and return the modal id.

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The text shown under the caption.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` when the operator closes
        the alert).
    """
    dialog = wx.MessageDialog(parent, message, title, wx.OK | wx.CENTRE | wx.ICON_ERROR)
    try:
        return int(dialog.ShowModal())
    finally:
        dialog.Destroy()


def show_confirm(  # noqa: PLR0913, PLR0917 -- (parent, title, message) + 2 button labels
    parent: wx.Window | None,
    title: str,
    message: str,
    ok_label: str,
    cancel_label: str,
) -> int:
    """Show *message* as a destructive confirm and return the modal id.

    ``wx.CANCEL_DEFAULT`` is hard-coded: every current caller asks a
    destructive question, so the default button is Cancel and Enter
    never confirms by accident. *ok_label* and *cancel_label* name the
    two buttons verbatim.

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The destructive question shown under the caption.
        ok_label: The confirm button's text.
        cancel_label: The cancel button's text.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` on confirm,
        ``wx.ID_CANCEL`` on cancel).
    """
    dialog = wx.MessageDialog(
        parent,
        message,
        title,
        wx.OK | wx.CANCEL | wx.CENTRE | wx.ICON_WARNING | wx.CANCEL_DEFAULT,
    )
    try:
        dialog.SetOKCancelLabels(ok_label, cancel_label)
        return int(dialog.ShowModal())
    finally:
        dialog.Destroy()
