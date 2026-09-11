# SPDX-License-Identifier: GPL-3.0-only
"""Show one-shot alerts and destructive confirms as native wx dialogs.

XRC-authored dialogs load and show through ``views.dialogs.run_dialog``,
which wires their stock ids and light-mode panel tint. The six
functions here cover the code-side native dialogs that need no XRC
resource: one-shot alerts (``show_info``, ``show_warning``,
``show_error``) and the three confirms -- ``show_confirm`` (a
destructive question, warning icon), ``show_danger`` (a destructive
question that discards data, so it carries the error icon rather than
the warning one) and ``show_prompt`` (a non-destructive question,
information icon, OK default). Each function constructs a plain
``wx.MessageDialog``, shows it modally, destroys it, and returns the
modal id.

The three confirms share :func:`_confirm`: the icon is what separates
warning from error, and ``default_ok`` decides whether wx's
``CANCEL_DEFAULT`` marker is added -- every destructive question
defaults to Cancel so a reflex Enter never destroys data, while
``show_prompt`` leaves OK as the default because its action loses
nothing.

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

__all__ = [
    "show_confirm",
    "show_danger",
    "show_error",
    "show_info",
    "show_prompt",
    "show_warning",
]

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


def _confirm(  # noqa: PLR0913, PLR0917 -- (parent, title, message, 2 labels) + 2 policy flags
    parent: wx.Window | None,
    title: str,
    message: str,
    ok_label: str,
    cancel_label: str,
    *,
    icon: int,
    default_ok: bool,
) -> int:
    """Show *message* as a two-button confirm and return the modal id.

    The shared core of the three public confirms: *icon* is the only
    thing separating a warning from an error, and *default_ok* decides
    whether ``wx.CANCEL_DEFAULT`` is added (a destructive question
    defaults to Cancel; a non-destructive one leaves OK as the default
    so Enter answers Yes, not No).

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The question shown under the caption.
        ok_label: The confirm button's text.
        cancel_label: The cancel button's text.
        icon: The ``wx.ICON_*`` flag for the question's severity.
        default_ok: Whether Enter activates the confirm button.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` on confirm,
        ``wx.ID_CANCEL`` on cancel).
    """
    flags = wx.OK | wx.CANCEL | wx.CENTRE | icon
    if not default_ok:
        flags |= wx.CANCEL_DEFAULT
    dialog = wx.MessageDialog(parent, message, title, flags)
    try:
        dialog.SetOKCancelLabels(ok_label, cancel_label)
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

    Cancel is the default button: every current caller asks a
    destructive question, so a reflex Enter must never confirm by
    accident. *ok_label* and *cancel_label* name the two buttons
    verbatim.

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
    return _confirm(
        parent,
        title,
        message,
        ok_label,
        cancel_label,
        icon=wx.ICON_WARNING,
        default_ok=False,
    )


def show_danger(  # noqa: PLR0913, PLR0917 -- (parent, title, message) + 2 button labels
    parent: wx.Window | None,
    title: str,
    message: str,
    ok_label: str,
    cancel_label: str,
) -> int:
    """Show *message* as a data-losing confirm and return the modal id.

    :func:`show_confirm`'s shape with the error icon in place of the
    warning one: Ride ▸ Clear Ride… discards a ridden ride's crossings,
    cards and audit trail, which reads as an error rather than a
    caution. Cancel is the default button for the same measured reason
    as the confirm's -- Enter must never destroy data by accident.

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
    return _confirm(
        parent,
        title,
        message,
        ok_label,
        cancel_label,
        icon=wx.ICON_ERROR,
        default_ok=False,
    )


def show_prompt(  # noqa: PLR0913, PLR0917 -- (parent, title, message) + 2 button labels
    parent: wx.Window | None,
    title: str,
    message: str,
    ok_label: str,
    cancel_label: str,
) -> int:
    """Show *message* as a non-destructive confirm; return its modal id.

    The information icon with OK as the default button: H2's Duplicate
    Ride… and Reopen Ride confirms lose nothing, so a reflex Enter may
    confirm them and the question reads as a prompt rather than a
    warning. *ok_label* and *cancel_label* still name both buttons.

    Args:
        parent: The owning window, or ``None`` for an unparented
            dialog.
        title: The dialog caption.
        message: The question shown under the caption.
        ok_label: The confirm button's text.
        cancel_label: The cancel button's text.

    Returns:
        ``ShowModal``'s result (``wx.ID_OK`` on confirm,
        ``wx.ID_CANCEL`` on cancel).
    """
    return _confirm(
        parent,
        title,
        message,
        ok_label,
        cancel_label,
        icon=wx.ICON_INFORMATION,
        default_ok=True,
    )
