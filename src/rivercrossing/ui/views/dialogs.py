# SPDX-License-Identifier: GPL-3.0-only
"""Wiring for the spec.md §13 / R-76 dialog-behaviour contract.

Every dialog in ``ui/xrc/*.xrc`` is already authored:
layout, control names, and (per dialog) an XRC ``<default>`` button
and, on the four destructive confirms, an XRC ``<focused>`` marker
too. This module adds no layout and no business logic -- it wires
the small, generic mechanics spec.md §13 asks of *every* dialog that
XRC alone cannot express, and that the individual view/presenter
tasks opening these dialogs would otherwise have to repeat 25 times:

* **Escape safely dismisses a Close-only dialog.** Measured: wx's
  built-in Escape handling (``wxDialogBase``'s ``CHAR_HOOK``) only
  ever searches for ``wxID_CANCEL``, and wx never auto-binds
  ``wxID_CLOSE`` to end a modal (unlike ``wxID_OK``/``CANCEL``/
  ``YES``/``NO``/``APPLY``, which it does). A dialog whose only
  dismiss button is ``wxID_CLOSE`` (``about_dlg``, ``audit_dlg``,
  ``rider_editor_dlg``, ...) is otherwise inert to both Escape and a
  mouse/keyboard activation of that button. :func:`wire_close_button`
  fixes both in one call; it is a no-op on the 17 dialogs that carry
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

``ride_setup_dlg``, ``rider_editor_dlg``, ``csv_preview_dlg``,
``entry_detail_dlg`` and Phase 4's ``team_editor_dlg`` carry no
``<default>`` button at all in their
already-authored XRC, so "Enter activates the marked default button"
has nothing to activate for these five --
:data:`DEFAULT_BUTTON_DECISIONS` is the per-dialog product call
(E1.5.3) that fills the gap, and :data:`FORM_FIRST_FIELDS` is
spec.md §13's matching initial-focus decision for every form dialog,
``rider_editor_dlg`` and ``team_editor_dlg`` included. Both
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
    "delete_ride_message",
    "dnf_message",
    "duplicate_ride_message",
    "finish_again_labels",
    "first_field_for",
    "reassign_message",
    "reopen_ride_message",
    "run_dialog",
    "set_initial_focus",
    "void_card_message",
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
# set_default_button's docstring; Phase 4's team_editor_dlg carries
# the same shape (Save, not Close or Add/Remove) and joins the list.
DEFAULT_BUTTON_DECISIONS: tuple[tuple[str, str], ...] = (
    (ids.RIDE_SETUP_DLG, WX_ID_OK),
    (ids.CSV_PREVIEW_DLG, WX_ID_OK),
    (ids.ENTRY_DETAIL_DLG, WX_ID_CLOSE),
    (ids.RIDER_EDITOR_DLG, ids.SAVE_BTN),
    (ids.TEAM_EDITOR_DLG, ids.SAVE_BTN),
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
    (ids.TEAM_EDITOR_DLG, ids.NAME_INPUT),
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


def delete_ride_message(ride_name: str) -> str:
    """Return ``delete_ride_dlg``'s ``message_lbl`` copy for a ride.

    UX-DESKTOP §4: a destructive confirm must name the object it is
    about to destroy, so this line is not optional -- the E5.3.2
    functional suite asserts the label is non-empty and carries
    *ride_name* (a blank label is a failed assertion, never cosmetic).
    Mirrors library.xrc's own data-bearing sentence.
    """
    return f'Deletes "{ride_name}" and all its data.'


def duplicate_ride_message(ride_name: str) -> str:
    """Return ``duplicate_ride_dlg``'s ``message_lbl`` copy (E5.4.1).

    Names the ride being duplicated (UX-DESKTOP §4), matching
    dialogs.xrc's data-bearing sentence. Non-destructive, so the
    confirm is Enter-safe; the copy's derived name is the Store's
    concern, not this line's.
    """
    return f'Duplicate "{ride_name}" as a new DRAFT ride?'


def reopen_ride_message(ride_name: str) -> str:
    """Return ``reopen_ride_dlg``'s ``message_lbl`` copy (E5.4.1).

    Names the ride being reopened (UX-DESKTOP §4), matching
    dialogs.xrc's data-bearing sentence and spec §3's "reopen for
    corrections" wording.
    """
    return f'Reopen "{ride_name}" for corrections?'


def finish_again_labels() -> tuple[str, str]:
    """Return the REOPENED finish confirm's ``(title, ok_label)`` copy.

    E7.2.2's single primary "Finish again" (spec §3 design 8c; §15's
    "Finish Ride… (Finish again from REOPENED)"): the same
    ``finish_confirm_dlg`` the RUNNING finish route shows, re-labelled
    when the ride is REOPENED -- title and primary button name the
    re-lock, never blank (UX-DESKTOP §4). The first-finish copy stays
    the XRC-authored "Finish Ride?" / "Finish ride".
    """
    return "Finish again?", "Finish again"


_SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
_CARD_JOKER_CODE = "JK"
_CARD_JOKER_DISPLAY = "JK★"


def _format_card_code(code: str) -> str:
    """Render one stored card code's canvas display text.

    ``"9H"`` -> ``"9♥"``; the joker -> ``"JK★"``. The rank character
    is already in its display form (``Card.code()``'s stored form uses
    "T" for ten), so only the suit letter needs converting to a glyph
    -- the same two-line mapping ``results_win.format_card`` owns,
    duplicated here so the confirm dialogs never depend on the
    results window's module (SIMPLECODE Rule 3's second copy).
    """
    if code == _CARD_JOKER_CODE:
        return _CARD_JOKER_DISPLAY
    rank, suit = code[:-1], code[-1]
    return f"{rank}{_SUIT_SYMBOLS[suit]}"


def void_card_message(card_code: str, entry: str) -> str:
    """Return ``void_card_confirm_dlg``'s ``card_lbl`` copy (E7.2.1).

    Names the card being voided and the entry it belongs to
    (``"9♥ — 45 · J. Okafor"``) -- UX-DESKTOP §4: the confirm names
    the object; a blank label is a failed assertion, never cosmetic
    (the same rule the E5.4.1 message helpers pin). Mirrors
    dialogs.xrc's own data-bearing sentence.
    """
    return f"{_format_card_code(card_code)} — {entry}"


def dnf_message(plate: str, name: str) -> str:
    """Return ``dnf_confirm_dlg``'s ``entry_lbl`` copy (E7.2.1).

    Names the entry being marked DNF (``"212 · M. Chen"``), matching
    dialogs.xrc's data-bearing line; a blank label is a failed
    assertion.
    """
    return f"{plate} · {name}"


def reassign_message(crossing_time: str, entry: str) -> str:
    """Return ``reassign_dlg``'s ``crossing_lbl`` copy (E7.2.1).

    Names the crossing being reassigned (``"Crossing 14:21:59 · lap
    credited to 45"``), matching dialogs.xrc's data-bearing line; a
    blank label is a failed assertion.
    """
    return f"Crossing {crossing_time} · lap credited to {entry}"


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
