# SPDX-License-Identifier: GPL-3.0-only
"""Wiring for the spec.md §13 / R-76 dialog-behaviour contract.

Every one of the 21 dialogs in ``ui/xrc/*.xrc`` is already authored:
layout, control names, and (per dialog) an XRC ``<default>`` button
and, on the four destructive confirms, an XRC ``<focused>`` marker
too. This module adds no layout and no business logic -- it wires
the small, generic mechanics spec.md §13 asks of *every* dialog that
XRC alone cannot express, and that the individual view/presenter
tasks opening these dialogs would otherwise have to repeat 21 times:

* **Escape safely dismisses a Close-only dialog.** Measured: wx's
  built-in Escape handling (``wxDialogBase``'s ``CHAR_HOOK``) only
  ever searches for ``wxID_CANCEL``, and wx never auto-binds
  ``wxID_CLOSE`` to end a modal (unlike ``wxID_OK``/``CANCEL``/
  ``YES``/``NO``/``APPLY``, which it does). A dialog whose only
  dismiss button is ``wxID_CLOSE`` (``about_dlg``, ``audit_dlg``,
  ``rider_editor_dlg``, ...) is otherwise inert to both Escape and a
  mouse/keyboard activation of that button. :func:`wire_close_button`
  fixes both in one call; it is a no-op on the 13 dialogs that carry
  a ``wxID_CANCEL`` instead, so callers never need to branch on which
  case a given dialog is.
* **Form dialogs focus their first field, not their default button.**
  ``set_start_dlg``, ``edit_crossing_dlg``, ``reassign_dlg``,
  ``manual_deal_dlg``, ``ride_setup_dlg`` and ``rider_editor_dlg``
  each mark ``wxID_OK`` (or nothing -- see the open gap below) as
  default so Enter still submits the form, but spec.md §13 wants the
  *initial focus* on the first input field instead.
  :func:`set_initial_focus` does that one ``SetFocus()`` call.
* **The R-18 type-to-confirm delete gate.** ``wxID_DELETE`` in
  ``delete_ride_dlg`` starts disabled and only ever re-enables on a
  byte-for-byte match of the typed ride name -- no case-fold, no
  ``.strip()``, per UX-DESKTOP §4's type-to-confirm rule.
* **Focus returns to the opener.** spec.md §13's last dialog rule.
  :func:`run_dialog` is the one entry point every other view wires a
  dialog's ``ShowModal`` through; it always restores focus to the
  caller-supplied *opener*, whichever way the dialog ends.

``ride_setup_dlg``, ``rider_editor_dlg``, ``csv_preview_dlg`` and
``entry_detail_dlg`` carry no ``<default>`` button at all in their
already-authored XRC, so "Enter activates the marked default button"
has nothing to activate for these four --
:data:`DEFAULT_BUTTON_DECISIONS` is the per-dialog product call
(E1.5.3) that fills the gap, and :data:`FORM_FIRST_FIELDS` is
spec.md §13's matching initial-focus decision for every form dialog,
``rider_editor_dlg`` included. Both
are the one place these decisions are recorded -- ``app.py``'s
``_apply_dialog_defaults`` applies them when a real menu route opens
the dialog, and ``tests/functional/test_dialog_behavior.py`` asserts
them directly against a raw XRC-loaded dialog; neither copies the
other's table.
"""

from typing import Any

from rivercrossing.ui import ids, require_wx

wx = require_wx()

__all__ = [
    "DEFAULT_BUTTON_DECISIONS",
    "FORM_FIRST_FIELDS",
    "WX_ID_CLOSE",
    "WX_ID_OK",
    "MissingDialogControlError",
    "bind_delete_confirmation_gate",
    "default_button_for",
    "first_field_for",
    "run_dialog",
    "set_initial_focus",
    "wire_close_button",
]

# Real XRC names FindWindowByName resolves, but excluded from ui/ids.py
# by tools/gen_ids.py's STOCK_IDS set (spec.md §15b) -- the same two
# stock ids tests/functional/pages.py names for the identical reason;
# production code cannot import that test-only module, so these are
# the one place it repeats the (immutable, wx-defined) literal.
WX_ID_OK = "wxID_OK"
WX_ID_CLOSE = "wxID_CLOSE"

# E1.5.3's product decision: the four already-authored dialogs with no
# XRC <default> each get one (module docstring). rider_editor_dlg's
# own choice -- Save, not Close or Add -- is explained in
# set_default_button's docstring.
DEFAULT_BUTTON_DECISIONS: tuple[tuple[str, str], ...] = (
    (ids.RIDE_SETUP_DLG, WX_ID_OK),
    (ids.CSV_PREVIEW_DLG, WX_ID_OK),
    (ids.ENTRY_DETAIL_DLG, WX_ID_CLOSE),
    (ids.RIDER_EDITOR_DLG, ids.SAVE_BTN),
)

# spec.md §13's initial-focus decision for every form dialog: the
# first input field, never the default button (set_initial_focus's
# own docstring).
FORM_FIRST_FIELDS: tuple[tuple[str, str], ...] = (
    (ids.SET_START_DLG, ids.START_DATE_PICKER),
    (ids.EDIT_CROSSING_DLG, ids.PLATE_INPUT),
    (ids.REASSIGN_DLG, ids.NEW_PLATE_INPUT),
    (ids.MANUAL_DEAL_DLG, ids.PLATE_INPUT),
    (ids.RIDE_SETUP_DLG, ids.NAME_INPUT),
    (ids.RIDER_EDITOR_DLG, ids.PLATE_INPUT),
)


def default_button_for(dialog_name: str) -> str | None:
    """Return the recorded default button for *dialog_name*, if any.

    ``None`` if *dialog_name* carries no recorded decision -- most
    dialogs already declare their own ``<default>`` in XRC and need
    none.
    """
    return next((btn for name, btn in DEFAULT_BUTTON_DECISIONS if name == dialog_name), None)


def first_field_for(dialog_name: str) -> str | None:
    """Return :data:`FORM_FIRST_FIELDS`'s entry for *dialog_name*.

    ``None`` if *dialog_name* is not a form dialog with a recorded
    first-field decision.
    """
    return next((field for name, field in FORM_FIRST_FIELDS if name == dialog_name), None)


class MissingDialogControlError(LookupError):
    """A frozen control name did not resolve inside a dialog."""


def _control(dialog: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return the control named *name* inside *dialog*.

    Mirrors ``tests/functional/harness.find_control``'s shape: the
    lookup always passes *dialog* as the explicit ``parent`` argument
    to ``FindWindowByName``, never as an instance-method call, since
    the latter silently searches every top-level window in the
    process instead (measured, harness.py's own module docstring).

    Raises:
        MissingDialogControlError: If *name* does not resolve inside
            *dialog*.
    """
    control = wx.Window.FindWindowByName(name, dialog)
    if control is None:
        raise MissingDialogControlError(
            f"dialog {dialog.GetName()!r} has no control named {name!r}"
        )
    return control


def wire_close_button(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Make Escape, and a click, safely dismiss *dialog* via Close.

    No-op when *dialog* has no ``wxID_CLOSE`` control -- every dialog
    that instead carries ``wxID_CANCEL`` needs no wiring here, since
    wx already binds Escape and a click on Cancel by itself.
    """
    close_button = wx.Window.FindWindowByName("wxID_CLOSE", dialog)
    if close_button is None:
        return
    dialog.SetEscapeId(close_button.GetId())
    dialog.Bind(wx.EVT_BUTTON, lambda event: dialog.EndModal(event.GetId()), close_button)


def wire_escape_to(dialog: Any, control_name: str) -> None:  # noqa: ANN401
    """Route Escape to the named button when no Cancel or Close exists.

    R-76 says Escape always cancels, but wx's native handling only
    ever looks for ``wxID_CANCEL``, and ``resume_dlg`` deliberately
    carries neither Cancel nor Close: the canvas draws "Continue
    ride" and "Open library" because on launch there is genuinely
    nothing to cancel. Pointing Escape at the non-committal button
    satisfies R-76 without inventing a third one, and honours §13's
    rule that Escape is never the destructive path -- opening the
    library stops no ride and loses no data.

    ``SetEscapeId`` alone is not enough, and measuring that is the
    only reason this is right: with just the escape id set, Escape
    does end the modal but ``ShowModal`` returns ``wxID_CANCEL``
    rather than the button's own id, because wx falls back to its
    generic cancel path when nothing handles the emulated click. The
    caller would then be unable to tell an Escape from a click on
    the button it was pointed at. Binding the handler too -- exactly
    as ``wire_close_button`` does -- makes both routes report the
    same result.
    """
    button = _control(dialog, control_name)
    dialog.SetEscapeId(button.GetId())
    dialog.Bind(wx.EVT_BUTTON, lambda event: dialog.EndModal(event.GetId()), button)


def set_default_button(dialog: Any, control_name: str) -> None:  # noqa: ANN401
    """Mark the named button as *dialog*'s default, for Enter.

    Four already-authored dialogs declare no ``<default>`` in XRC, so
    Enter did nothing in them -- measured via ``GetDefaultItem()``,
    and a breach of R-76's "Enter = default". Set here rather than in
    the .xrc files so the choice sits next to the reasoning: the
    rider editor defaults to Save because Enter after typing into
    Plate/Name/Team should commit the edit, not create a duplicate
    entry (Add) or silently discard it (Close).
    """
    _control(dialog, control_name).SetDefault()


def set_initial_focus(dialog: Any, control_name: str) -> None:  # noqa: ANN401
    """Focus the control named *control_name* in *dialog*.

    Used for a form dialog's first input field (spec.md §13): its
    marked default button stays the affirmative action for Enter,
    but the field the operator's fingers are already near gets the
    initial focus instead.
    """
    _control(dialog, control_name).SetFocus()


def bind_delete_confirmation_gate(dialog: Any, ride_name: str) -> None:  # noqa: ANN401
    """Keep ``wxID_DELETE`` disabled until *ride_name* is typed exactly.

    R-18 / UX-DESKTOP §4: an irreversible delete requires a
    byte-for-byte type-to-confirm match -- a case difference or a
    stray trailing space must not enable the button.
    """
    confirm_input = _control(dialog, "confirm_name_input")
    delete_button = _control(dialog, "wxID_DELETE")
    delete_button.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

    def _on_text(event: Any) -> None:  # noqa: ANN401
        delete_button.Enable(confirm_input.GetValue() == ride_name)
        event.Skip()

    dialog.Bind(wx.EVT_TEXT, _on_text, confirm_input)


def run_dialog(dialog: Any, opener: Any) -> int:  # noqa: ANN401 -- wx ships no stubs
    """Show *dialog* modally, always returning focus to *opener* after.

    The one entry point every other view wires a dialog's display
    through: it applies :func:`wire_close_button` first, then shows
    *dialog* and restores focus to *opener* in a ``finally`` block so
    it happens whichever way the dialog ends (spec.md §13's last
    dialog rule).

    Args:
        dialog: A loaded, not-yet-shown ``wx.Dialog``.
        opener: The control that is about to open *dialog* and
            should reclaim focus once it ends.

    Returns:
        ``ShowModal``'s return value.
    """
    wire_close_button(dialog)
    try:
        return int(dialog.ShowModal())
    finally:
        opener.SetFocus()
