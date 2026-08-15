# SPDX-License-Identifier: GPL-3.0-only
"""R-76 / spec.md §13 dialog behaviour, asserted per dialog (E1.5.3).

Five rules, driven and observed as honestly as this out-of-focus,
non-frontmost desktop session (measured throughout this suite and
in ``harness.py``'s own module docstring) allows:

* **Esc always cancels, never a destructive path.** Driven for
  real: ``wx.KeyEvent(wx.wxEVT_CHAR_HOOK)`` with ``WXK_ESCAPE``,
  posted directly to a dialog's event handler, reproduces wx's own
  built-in Escape handling without needing OS-level key focus
  (measured -- unlike ``wx.UIActionSimulator``, whose ``Char()``
  reports success while delivering nothing here). ``ShowModal``'s
  return value is a first-class, genuinely observed fact, not a
  proxy.
* **Enter activates the marked default button.** Measured: no
  synthetic event this suite can post (``CHAR_HOOK``, ``KEY_DOWN``,
  targeted at the dialog or the button) reproduces a real Enter
  keypress's native default-button activation -- that dispatch is
  native-platform "key equivalent" handling outside wx's own C++
  event system, and requires the window to hold real OS key focus,
  which this session never has. The proxy used instead: (a)
  ``GetDefaultItem()`` names the expected control -- a real,
  first-class fact about how the dialog is wired -- and (b)
  clicking that exact control (direct event injection, proven
  reliable) ends the dialog with the expected result. Together
  they prove "the right button is marked default, and activating
  it by any means does the right thing" without claiming Enter
  itself was observed.
* **Initial focus.** ``wx.Window.FindFocus()``/``HasFocus()`` are
  measured unobservable in this session (``None``/``False`` even
  immediately after an explicit ``SetFocus()`` -- a terminal-
  launched, unbundled Python is never the frontmost macOS app).
  Two different proxies apply depending on the rule:
  - Destructive confirms (Cancel must get initial focus): the
    already-authored XRC co-declares ``<default>1</default>`` and
    ``<focused>1</focused>`` on the *same* Cancel button
    (dialogs.xrc/library.xrc, read directly). ``GetDefaultItem()``
    naming Cancel is asserted as the strongest available
    in-process proxy; the XRC files' own header comments record
    the same "could not verify" limitation independently.
  - Form dialogs (first field must get initial focus): this
    suite's own :func:`rivercrossing.ui.views.dialogs.
    set_initial_focus` is the code under test, so its call to
    ``SetFocus()`` is spied on directly (monkeypatching a real wx
    control's bound method is a platform/GUI I/O boundary, T-10)
    -- a genuine proof that *our* code targets the right control,
    not a claim about resulting OS focus.
* **Tab is trapped inside the dialog.** Native, unmodifiable
  behaviour of a real ``wx.Dialog`` shown via ``ShowModal()`` on
  both target OSes -- there is no keystroke this harness can
  inject to make Tab escape a modal loop, and positing a Tab-cycle
  "proof" that cannot actually leave the dialog either way would
  prove nothing. Asserted structurally instead: every one of the
  21 dialogs really is a ``wx.Dialog`` (never a frame), which is
  what makes the OS trap Tab in the first place.
* **Focus returns to the control that opened the dialog.** Fully
  within this suite's control: :func:`rivercrossing.ui.views.
  dialogs.run_dialog` is the one wrapper every other view will
  show a dialog through, and its ``finally: opener.SetFocus()`` is
  spied on the same way as the form-dialog proxy above -- a real
  proof of *our* code's behaviour, deterministic regardless of OS
  focus.

Two gaps reported rather than invented around in the original round
(``ride_setup_dlg``/``rider_editor_dlg``/``csv_preview_dlg``/
``entry_detail_dlg`` carrying no ``<default>`` at all;
``resume_dlg`` carrying neither ``wxID_CANCEL`` nor ``wxID_CLOSE``)
were taken to the product owner and decided in an E1.5.3 follow-up.
:func:`dialogs.set_default_button` and :func:`dialogs.
wire_escape_to` apply those decisions; the "before" state each one
fixes is still asserted here too, since the underlying already-
authored XRC is genuinely unchanged. A first cut of
``wire_escape_to`` had its own bug, caught here rather than
elsewhere -- :func:`test_wire_escape_to_dismisses_resume_dlg`'s
docstring records both the wrong measurement that revealed it and
the fixed one that replaced it, since the wrong one is exactly why
the fix's ``EVT_BUTTON`` bind is not "redundant-looking" dead code.
"""

import re
from dataclasses import dataclass
from typing import Any

import harness
import pages
import pytest

from rivercrossing.ui import ids
from rivercrossing.ui.views import dialogs

pytestmark = pytest.mark.functional

wx = harness.wx

_TIMEOUT_SENTINEL = -999
_RIDE_NAME = "Club poker night"


# ----------------------------------------------------------- helpers


def _show(xrc_resource: Any, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Load, show and pump *name* from *xrc_resource*."""
    dialog = harness.load_window(xrc_resource, name, frame=False)
    try:
        dialog.Show()
        harness.pump()
    except Exception:  # Fault A: any post-load failure must close the dialog
        harness.close_window(dialog)
        raise
    return dialog


def _end_modal_if_undecided(dialog: Any) -> None:  # noqa: ANN401
    """Fire the safety-net ``EndModal`` only when nothing else has.

    Measured on windows-latest (run 31015653629): wxMSW does not
    clear ``IsModal()`` within the pending-events pass that runs the
    action's own ``EndModal``, so an unguarded, same-pass sentinel
    overwrote every successful dialog result with ``-999`` -- 47
    tests red on Windows, green on macOS. The return-code guard is
    what closes that: a decided dialog carries its real result here
    even while ``IsModal()`` is still true.
    """
    if not dialog.IsModal() or dialog.GetReturnCode() != 0:
        return
    dialog.EndModal(_TIMEOUT_SENTINEL)


def _arm_safety_net(dialog: Any) -> None:  # noqa: ANN401
    """Queue the sentinel one pending-events pass after *action*.

    Re-queuing gives the modal loop a pass to unwind after the
    action ends the dialog, so the sentinel check sees the decided
    state instead of racing it.
    """
    wx.CallAfter(_end_modal_if_undecided, dialog)


def _run_with_action(dialog: Any, action: Any, runner: Any) -> int:  # noqa: ANN401
    """Call *runner* while scheduling *action* once the loop pumps.

    A safety-net ``EndModal`` is armed right after *action* so a
    probe that turns out not to end the dialog (the ``resume_dlg``
    gap) cannot hang the suite forever with no user present.
    *runner* is usually ``dialog.ShowModal`` but can be any zero-arg
    callable that ends up calling it, e.g. :func:`dialogs.run_dialog`.
    """
    wx.CallAfter(action)
    wx.CallAfter(_arm_safety_net, dialog)
    return int(runner())


def _send_escape(dialog: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
    """Post a real Escape ``CHAR_HOOK`` at *dialog* (proven to work)."""
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(wx.WXK_ESCAPE)
    dialog.GetEventHandler().ProcessEvent(event)


def _spy_on_set_focus(control: Any) -> list[bool]:  # noqa: ANN401
    """Monkeypatch *control*'s ``SetFocus``, recording each call.

    ``SetFocus()`` is a platform/GUI I/O boundary (T-10), the same
    category as ``datetime.now`` or a filesystem call -- legitimate
    to spy on directly, since ``FindFocus()``/``HasFocus()`` are
    measured unobservable in this session (module docstring).
    """
    original = control.SetFocus
    calls: list[bool] = []

    def _spy() -> None:
        calls.append(True)
        original()

    control.SetFocus = _spy
    return calls


# --------------------------------------------------- Escape -> Cancel

# Every dialog whose XRC declares a wxID_CANCEL button: wx's
# built-in Escape handling already ends these with wxID_CANCEL, no
# wiring needed. Each row also calls wire_close_button() first,
# which proves that function's no-op branch (T-3: it has no
# wxID_CLOSE to find).
_HAS_NATIVE_CANCEL = (
    ids.RIDE_SETUP_DLG,
    ids.SET_START_DLG,
    ids.STOP_CONFIRM_DLG,
    ids.FINISH_CONFIRM_DLG,
    ids.CONTINUE_OR_NEW_DLG,
    ids.EXIT_RUNNING_DLG,
    ids.EXIT_CONFIRM_DLG,
    ids.CSV_PREVIEW_DLG,
    ids.EDIT_CROSSING_DLG,
    ids.REASSIGN_DLG,
    ids.MANUAL_DEAL_DLG,
    ids.DNF_CONFIRM_DLG,
    ids.DELETE_RIDE_DLG,
    ids.SETTINGS_DLG,
)


@pytest.mark.parametrize("dialog_name", _HAS_NATIVE_CANCEL)
def test_escape_ends_modal_with_cancel_for_dialogs_with_native_cancel(
    dialog_name: str, xrc_resource: object
) -> None:
    """R-76: Esc always cancels -- native wx, genuinely driven."""
    dialog = _show(xrc_resource, dialog_name)
    dialogs.wire_close_button(dialog)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog), dialog.ShowModal)
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CANCEL


# ------------------------------------------------ Escape/click -> Close

# Close-only dialogs (no wxID_CANCEL): wire_close_button is
# required for Escape, and for a click on Close, to do anything at
# all (measured: wx auto-binds wxID_OK/CANCEL/YES/NO/APPLY, never
# CLOSE).
_CLOSE_ONLY = (
    ids.RIDER_EDITOR_DLG,
    ids.ENTRY_DETAIL_DLG,
    ids.RIDE_LIBRARY_DLG,
    ids.AUDIT_DLG,
    ids.ABOUT_DLG,
    ids.SHORTCUTS_DLG,
    ids.SELFTEST_DLG,
)


@pytest.mark.parametrize("dialog_name", _CLOSE_ONLY)
def test_wire_close_button_escape_ends_modal_with_close(
    dialog_name: str, xrc_resource: object
) -> None:
    """R-76: Esc safely dismisses a Close-only dialog once wired."""
    dialog = _show(xrc_resource, dialog_name)
    dialogs.wire_close_button(dialog)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog), dialog.ShowModal)
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CLOSE


@pytest.mark.parametrize("dialog_name", _CLOSE_ONLY)
def test_wire_close_button_click_ends_modal_with_close(
    dialog_name: str, xrc_resource: object
) -> None:
    """A click on Close, once wired, also ends the dialog cleanly."""
    dialog = _show(xrc_resource, dialog_name)
    dialogs.wire_close_button(dialog)

    try:
        result = _run_with_action(
            dialog, lambda: harness.click(dialog, pages.WX_ID_CLOSE), dialog.ShowModal
        )
    finally:
        harness.close_window(dialog)

    assert result == wx.ID_CLOSE


def test_resume_dlg_escape_has_no_effect_before_wire_escape_to(xrc_resource: object) -> None:
    """``resume_dlg`` has neither Cancel nor Close, unwired.

    No longer an unresolved gap -- the product owner decided
    Escape's target (see :func:`test_wire_escape_to_dismisses_
    resume_dlg` below) -- but this remains true of the raw,
    already-authored XRC on its own: ``wire_close_button`` correctly
    no-ops here too (there is no ``wxID_CLOSE`` to find), so Escape
    has no effect before :func:`dialogs.wire_escape_to` is applied --
    proven by the timeout sentinel firing, not a real dismissal.
    """
    dialog = _show(xrc_resource, ids.RESUME_DLG)
    dialogs.wire_close_button(dialog)

    try:
        result = _run_with_action(dialog, lambda: _send_escape(dialog), dialog.ShowModal)
    finally:
        harness.close_window(dialog)

    assert result == _TIMEOUT_SENTINEL


def test_wire_escape_to_dismisses_resume_dlg(xrc_resource: object) -> None:
    """Product decision: resume_dlg's Escape routes to ``library_btn``.

    R-76 requires Escape always cancel, but resume_dlg deliberately
    carries neither Cancel nor Close because on launch there is
    genuinely nothing to cancel yet (see the test above). "Open
    library" stops no ride and loses no data, so it is the
    non-committal, Escape-safe path; "Continue ride" resumes timing
    and is not. Driven for real with the same ``CHAR_HOOK``
    technique proven earlier in this file -- Escape genuinely ends
    the modal, not a proxy.

    The contract under test is really an equivalence -- Escape and a
    direct click on ``library_btn`` must report the *same* result --
    so two dialog instances are shown here, one dismissed each way
    (a dialog can only be shown/dismissed once). This is exactly the
    equivalence that silently broke in a first cut of
    :func:`dialogs.wire_escape_to`, kept here as the reason its
    ``EVT_BUTTON`` bind is not "redundant-looking" dead code: measured
    with only ``SetEscapeId`` set (no bind), Escape did end the modal,
    but ``ShowModal`` returned ``wx.ID_CANCEL`` (5101) rather than
    ``library_btn``'s own distinct, XRCID-generated id -- wx falls
    back to its generic cancel path when nothing handles the emulated
    click, so the caller could not tell an Escape from a click on the
    very button Escape was pointed at. ``wire_escape_to`` now binds
    the handler too (mirroring :func:`dialogs.wire_close_button`),
    and both routes measure identical below.
    """
    escape_dialog = _show(xrc_resource, ids.RESUME_DLG)
    dialogs.wire_escape_to(escape_dialog, ids.LIBRARY_BTN)
    library_btn_id = harness.find_control(escape_dialog, ids.LIBRARY_BTN).GetId()

    try:
        escape_result = _run_with_action(
            escape_dialog, lambda: _send_escape(escape_dialog), escape_dialog.ShowModal
        )
    finally:
        harness.close_window(escape_dialog)

    click_dialog = _show(xrc_resource, ids.RESUME_DLG)
    dialogs.wire_escape_to(click_dialog, ids.LIBRARY_BTN)

    try:
        click_result = _run_with_action(
            click_dialog,
            lambda: harness.click(click_dialog, ids.LIBRARY_BTN),
            click_dialog.ShowModal,
        )
    finally:
        harness.close_window(click_dialog)

    assert (escape_result, click_result) == (library_btn_id, library_btn_id)


def test_wire_escape_to_given_unknown_control_raises(xrc_resource: object) -> None:
    """T-5: :func:`dialogs.wire_escape_to`'s only ``raise``."""
    dialog = _show(xrc_resource, ids.RESUME_DLG)

    try:
        with pytest.raises(
            dialogs.MissingDialogControlError,
            match=re.escape("has no control named 'no_such_control'"),
        ):
            dialogs.wire_escape_to(dialog, "no_such_control")
    finally:
        harness.close_window(dialog)


# ----------------------------------------- Enter / default button


@dataclass(frozen=True)
class _DefaultClickCase:
    """One dialog, its default control, and clicking it's result."""

    dialog_name: str
    default_name: str
    expected_result: int


# Every dialog whose default button is bound to actually end the
# modal once wire_close_button() has been applied (a no-op for the
# ten that already carry a stock wxID_OK/CANCEL). Excludes
# continue_or_new_dlg, resume_dlg (continue_btn has no bound
# EndModal yet) and ride_library_dlg (wxID_OPEN is not bound
# either) -- see the E1.5.3 report.
_DEFAULT_CLICK_CASES = (
    _DefaultClickCase(ids.SET_START_DLG, pages.WX_ID_OK, wx.ID_OK),
    _DefaultClickCase(ids.STOP_CONFIRM_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.FINISH_CONFIRM_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.EXIT_RUNNING_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.EXIT_CONFIRM_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.EDIT_CROSSING_DLG, pages.WX_ID_OK, wx.ID_OK),
    _DefaultClickCase(ids.REASSIGN_DLG, pages.WX_ID_OK, wx.ID_OK),
    _DefaultClickCase(ids.MANUAL_DEAL_DLG, pages.WX_ID_OK, wx.ID_OK),
    _DefaultClickCase(ids.DNF_CONFIRM_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.DELETE_RIDE_DLG, pages.WX_ID_CANCEL, wx.ID_CANCEL),
    _DefaultClickCase(ids.SETTINGS_DLG, pages.WX_ID_OK, wx.ID_OK),
    _DefaultClickCase(ids.AUDIT_DLG, pages.WX_ID_CLOSE, wx.ID_CLOSE),
    _DefaultClickCase(ids.ABOUT_DLG, pages.WX_ID_CLOSE, wx.ID_CLOSE),
    _DefaultClickCase(ids.SHORTCUTS_DLG, pages.WX_ID_CLOSE, wx.ID_CLOSE),
    _DefaultClickCase(ids.SELFTEST_DLG, pages.WX_ID_CLOSE, wx.ID_CLOSE),
)


@pytest.mark.parametrize("case", _DEFAULT_CLICK_CASES, ids=lambda c: c.dialog_name)
def test_click_default_button_ends_modal_with_expected_result(
    case: _DefaultClickCase, xrc_resource: object
) -> None:
    """R-76: the marked default is right, and does the right thing."""
    dialog = _show(xrc_resource, case.dialog_name)
    dialogs.wire_close_button(dialog)
    # Read the name now: harness.close_window's Destroy() reaps the
    # control tree, and touching a reaped wx object afterwards --
    # even a "harmless" query -- segfaults the interpreter
    # (measured, harness.py's own module docstring).
    default_item_name = dialog.GetDefaultItem().GetName()

    try:
        result = _run_with_action(
            dialog, lambda: harness.click(dialog, case.default_name), dialog.ShowModal
        )
    finally:
        harness.close_window(dialog)

    assert (default_item_name, result) == (case.default_name, case.expected_result)


_STATIC_DEFAULT_ONLY = (
    (ids.CONTINUE_OR_NEW_DLG, ids.CONTINUE_BTN),
    (ids.RESUME_DLG, ids.CONTINUE_BTN),
    (ids.RIDE_LIBRARY_DLG, pages.WX_ID_OPEN),
)


@pytest.mark.parametrize(("dialog_name", "expected_default_name"), _STATIC_DEFAULT_ONLY)
def test_dialog_default_item_names_expected_control_no_bound_click_yet(
    dialog_name: str, expected_default_name: str, xrc_resource: object
) -> None:
    """The default is correctly marked; ending the modal is future work.

    ``continue_btn``/``wxID_OPEN`` carry no
    ``SetAffirmativeId``/``EndModal`` binding yet (measured: a
    click on either currently does nothing) -- that belongs to the
    ride-lifecycle/library tasks that give these buttons their real
    behaviour, not to R-76 wiring.
    """
    dialog = _show(xrc_resource, dialog_name)

    try:
        default_item_name = dialog.GetDefaultItem().GetName()
    finally:
        harness.close_window(dialog)

    assert default_item_name == expected_default_name


_NO_DEFAULT_DECLARED = (
    ids.RIDE_SETUP_DLG,
    ids.RIDER_EDITOR_DLG,
    ids.CSV_PREVIEW_DLG,
    ids.ENTRY_DETAIL_DLG,
)


@pytest.mark.parametrize("dialog_name", _NO_DEFAULT_DECLARED)
def test_dialog_default_item_is_none_before_set_default_button_is_applied(
    dialog_name: str, xrc_resource: object
) -> None:
    """The XRC fact :func:`dialogs.set_default_button` exists to fix.

    These four already-authored dialogs still carry no ``<default>``
    button at all -- unchanged, since ``.xrc`` files are not this
    module's to edit. No longer an unresolved gap: the product
    owner decided the four assignments below, and
    :func:`dialogs.set_default_button` applies them at runtime (see
    the tests immediately following this one).
    """
    dialog = _show(xrc_resource, dialog_name)

    try:
        default_item = dialog.GetDefaultItem()
    finally:
        harness.close_window(dialog)

    assert default_item is None


@pytest.mark.parametrize(("dialog_name", "control_name"), dialogs.DEFAULT_BUTTON_DECISIONS)
def test_set_default_button_makes_it_the_dialogs_default(
    dialog_name: str, control_name: str, xrc_resource: object
) -> None:
    """Product decision: each of the four gap dialogs gets a default.

    ``ride_setup_dlg``/``csv_preview_dlg`` -> ``wxID_OK`` (obvious
    primary); ``entry_detail_dlg`` -> ``wxID_CLOSE`` (a read-only
    view whose actions are explicit buttons, not a submit). The
    fourth, ``rider_editor_dlg``, is pinned in its own dedicated
    test below rather than only here. This table lives in
    ``dialogs.py`` -- ``test_app_bootstrap.py`` asserts the app's
    own route path applies the identical table, never a copy.
    """
    dialog = _show(xrc_resource, dialog_name)
    dialogs.set_default_button(dialog, control_name)

    try:
        default_item_name = dialog.GetDefaultItem().GetName()
    finally:
        harness.close_window(dialog)

    assert default_item_name == control_name


def test_set_default_button_rider_editor_defaults_to_save_not_close_or_add(
    xrc_resource: object,
) -> None:
    """Pinned: do not "tidy" this to ``wxID_CLOSE`` or ``add_btn``.

    Product owner's call (E1.5.3 follow-up): after typing into
    Plate/Name/Team, Enter should commit the edit in place, not
    silently discard it (``wxID_CLOSE``'s default action) and not
    create a duplicate entry alongside it (``add_btn``). ``save_btn``
    is the only one of the three that matches "commit this edit."
    """
    dialog = _show(xrc_resource, ids.RIDER_EDITOR_DLG)
    dialogs.set_default_button(dialog, ids.SAVE_BTN)

    try:
        default_item_name = dialog.GetDefaultItem().GetName()
    finally:
        harness.close_window(dialog)

    assert default_item_name == ids.SAVE_BTN


def test_set_default_button_given_unknown_control_raises(xrc_resource: object) -> None:
    """T-5: :func:`dialogs.set_default_button`'s only ``raise``."""
    dialog = _show(xrc_resource, ids.RIDE_SETUP_DLG)

    try:
        with pytest.raises(
            dialogs.MissingDialogControlError,
            match=re.escape("has no control named 'no_such_control'"),
        ):
            dialogs.set_default_button(dialog, "no_such_control")
    finally:
        harness.close_window(dialog)


# --------------------------------------- destructive confirms -> Cancel

# finish_confirm_dlg joined this set on the coordinator's explicit
# decision (E1.5.3 follow-up): it locks the ride and computes final
# standings, and its XRC default is already Cancel -- it was left
# out of the original four only because it carries no <focused>
# marker alongside that default, which was reported rather than
# guessed at. exit_confirm_dlg joined in Phase 8 (P8-D1): quitting
# with no ride running is destructive too, and its XRC co-declares
# <default> and <focused> on Cancel from the start.
_DESTRUCTIVE = (
    ids.STOP_CONFIRM_DLG,
    ids.DNF_CONFIRM_DLG,
    ids.DELETE_RIDE_DLG,
    ids.EXIT_RUNNING_DLG,
    ids.FINISH_CONFIRM_DLG,
    ids.EXIT_CONFIRM_DLG,
)


@pytest.mark.parametrize("dialog_name", _DESTRUCTIVE)
def test_destructive_confirm_default_item_is_cancel(
    dialog_name: str, xrc_resource: object
) -> None:
    """R-76: a destructive confirm's default is Cancel, never the verb.

    The same already-authored XRC co-declares
    ``<focused>1</focused>`` on this identical Cancel control
    (dialogs.xrc/library.xrc, read directly) -- real OS focus
    itself is unobservable here (module docstring), so
    ``GetDefaultItem()`` naming Cancel is the strongest available
    in-process proxy for "the safe path also gets initial focus."
    """
    dialog = _show(xrc_resource, dialog_name)

    try:
        default_item_name = dialog.GetDefaultItem().GetName()
    finally:
        harness.close_window(dialog)

    assert default_item_name == pages.WX_ID_CANCEL


# --------------------------------------------- form dialog first focus


@pytest.mark.parametrize(("dialog_name", "field_name"), dialogs.FORM_FIRST_FIELDS)
def test_set_initial_focus_calls_setfocus_on_first_field(
    dialog_name: str, field_name: str, xrc_resource: object
) -> None:
    """R-76: a form dialog's initial focus targets its first field.

    Genuinely observed at the level this suite controls: our own
    :func:`dialogs.set_initial_focus` calls ``SetFocus()`` on the
    named control -- a spy on that instance's bound method, not a
    claim about resulting OS focus (module docstring). This table
    lives in ``dialogs.py`` -- ``test_app_bootstrap.py`` asserts the
    app's own route path applies the identical table, never a copy.
    """
    dialog = _show(xrc_resource, dialog_name)
    field = harness.find_control(dialog, field_name)
    calls = _spy_on_set_focus(field)

    try:
        dialogs.set_initial_focus(dialog, field_name)
    finally:
        harness.close_window(dialog)

    assert calls == [True]


def test_set_initial_focus_given_unknown_control_raises(xrc_resource: object) -> None:
    """T-5: :func:`dialogs.set_initial_focus`'s only ``raise``."""
    dialog = _show(xrc_resource, ids.SET_START_DLG)

    try:
        with pytest.raises(
            dialogs.MissingDialogControlError,
            match=re.escape("has no control named 'no_such_control'"),
        ):
            dialogs.set_initial_focus(dialog, "no_such_control")
    finally:
        harness.close_window(dialog)


# -------------------------------------- R-18 delete type-to-confirm


def test_bind_delete_confirmation_gate_starts_disabled(xrc_resource: object) -> None:
    """R-18: ``wxID_DELETE`` starts disabled before typing anything."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)

    try:
        dialogs.bind_delete_confirmation_gate(dialog, _RIDE_NAME)
        enabled = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert enabled is False


_DELETE_GATE_CASES = (
    ("", False),  # empty (T-4 nullable/empty boundary)
    (_RIDE_NAME.lower(), False),  # near-miss: case difference
    (_RIDE_NAME + " ", False),  # near-miss: trailing space
    (_RIDE_NAME[:-1], False),  # near-miss: one character short
    (_RIDE_NAME, True),  # exact match
)


@pytest.mark.parametrize(("typed_text", "expected_enabled"), _DELETE_GATE_CASES)
def test_bind_delete_confirmation_gate_enables_only_on_exact_match(
    typed_text: str,
    expected_enabled: bool,  # noqa: FBT001 -- parametrize row value, not a call-site flag
    xrc_resource: object,
) -> None:
    """R-18: near-miss -- case, space, one char short -- stays off."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)
    dialogs.bind_delete_confirmation_gate(dialog, _RIDE_NAME)

    try:
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, typed_text)
        enabled = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert enabled is expected_enabled


def test_bind_delete_confirmation_gate_redisables_after_clearing_exact_match(
    xrc_resource: object,
) -> None:
    """Both branches of the equality gate happen in one session."""
    dialog = _show(xrc_resource, ids.DELETE_RIDE_DLG)
    dialogs.bind_delete_confirmation_gate(dialog, _RIDE_NAME)

    try:
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, _RIDE_NAME)
        harness.type_text(dialog, ids.CONFIRM_NAME_INPUT, "")
        enabled = harness.find_control(dialog, pages.WX_ID_DELETE).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert enabled is False


# ------------------------------------------ focus returns to opener

_RUN_DIALOG_CASES = (
    (ids.STOP_CONFIRM_DLG, wx.ID_CANCEL),  # native Cancel path
    (ids.SET_START_DLG, wx.ID_CANCEL),  # native Cancel path, a form dialog
    (ids.ABOUT_DLG, wx.ID_CLOSE),  # Close-only, needs run_dialog's own wiring
)


@pytest.mark.parametrize(("dialog_name", "expected_result"), _RUN_DIALOG_CASES)
def test_run_dialog_returns_result_and_restores_opener_focus(
    dialog_name: str, expected_result: int, xrc_resource: object
) -> None:
    """R-76: focus always returns to the opener, whichever way it ends.

    Genuinely observed at the level this suite controls: the spy
    proves *our* ``run_dialog`` wrapper called ``SetFocus()`` on
    the opener; resulting OS focus is unobservable here (module
    docstring), same rationale as the form-dialog proxy above.
    Escape is the dismissal for every row, including the Close-only
    one -- proving ``run_dialog`` applies its own
    ``wire_close_button`` step before showing, since a raw Escape
    does nothing on an unwired Close-only dialog (measured, see the
    earlier Close-only rows).
    """
    dialog = _show(xrc_resource, dialog_name)
    opener = wx.Frame(None)
    calls = _spy_on_set_focus(opener)

    try:
        result = _run_with_action(
            dialog, lambda: _send_escape(dialog), lambda: dialogs.run_dialog(dialog, opener)
        )
    finally:
        harness.close_window(dialog)
        harness.close_window(opener)

    assert (result, calls) == (expected_result, [True])


# ------------------------------------------------------- Tab is trapped

_ALL_DIALOG_SPECS = tuple(spec for spec in pages.WINDOWS if not spec.is_frame)


def test_all_dialogs_declare_exactly_twenty_two_rows() -> None:
    """A dialog disappearing from ``pages.WINDOWS`` must shrink this."""
    assert len(_ALL_DIALOG_SPECS) == 22


@pytest.mark.parametrize("spec", _ALL_DIALOG_SPECS, ids=lambda s: s.name)
def test_dialog_is_a_real_wx_dialog_so_tab_stays_trapped(
    spec: pages.WindowSpec, xrc_resource: object
) -> None:
    """R-76: Tab trapping is native to a ``wx.Dialog`` shown modally.

    Structural, not keystroke-driven: no synthetic Tab event this
    harness can inject would prove containment either way (the
    same ``UIActionSimulator`` limitation as Esc/Enter), so this
    asserts the one precondition that makes the OS trap Tab in the
    first place -- every one of the 21 rows really is a
    ``wx.Dialog``, never a ``wx.Frame``.
    """
    window = harness.load_window(xrc_resource, spec.name, frame=False)

    try:
        is_dialog = isinstance(window, wx.Dialog)
    finally:
        harness.close_window(window)

    assert is_dialog is True


# ------------------------------- Fault A: the load-construct seam
# (hosted-runner red, deterministic here: pump() is forced to raise
# between the load and the caller's try/finally, and the just-loaded
# dialog must not be left fully alive -- see _show's guard.)


def test_show_closes_the_dialog_when_a_post_load_step_raises(
    xrc_resource: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: a failure between load and try must not leak.

    ``_show`` loads, shows and pumps *before* the test's own
    ``try/finally``; under hosted-runner load a step between the load
    and the caller's ``try`` can raise (the ``_support.find_control``
    retry-exhaustion ``LookupError`` a caller's own view construction
    raises through this same seam, for one) and the just-loaded dialog
    then leaks fully alive, rerun-masked by ``--reruns 2``. Pump is
    forced to raise here so the leak is reproduced deterministically:
    red until ``_show`` closes the dialog on the way out.
    """

    def _pump_that_raises() -> None:
        raise LookupError("simulated post-load failure")

    monkeypatch.setattr(harness, "pump", _pump_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated post-load failure")):
        _show(xrc_resource, ids.RIDER_EDITOR_DLG)

    assert wx.Window.FindWindowByName(ids.RIDER_EDITOR_DLG) is None
