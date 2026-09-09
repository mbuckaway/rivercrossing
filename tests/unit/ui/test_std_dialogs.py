# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ui.std_dialogs' native message-dialog helpers.

The four ``show_*`` functions are thin ``wx.MessageDialog`` wiring:
construct with a fixed style, optionally override the OK/Cancel
labels, show modally, destroy, and return the modal id. Real dialogs
need a desktop and would block on ``ShowModal``, so every test swaps
``std_dialogs.wx.MessageDialog`` for a recording double. Importing wx
is safe without a display; constructing wx windows is not, so this
file never creates a window and never builds a ``wx.App``.

Expected style constants below are recomputed from real wx flags, so
the bitwise-equality assertions pin each dialog's exact documented
flag set: ``wx.ICON_INFORMATION`` / ``wx.ICON_WARNING`` /
``wx.ICON_ERROR`` with ``wx.CENTRE`` on every dialog, and ``wx.CANCEL``
plus ``wx.CANCEL_DEFAULT`` on the destructive confirm only.
"""

from functools import partial
from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.ui import std_dialogs

if TYPE_CHECKING:
    from collections.abc import Callable

# --- doubles and case tables -----------------------------------------

_SCRIPTED_MODAL_RESULT = 40001  # a stand-in modal id, no real wx stock id

_INFO_STYLE = wx.OK | wx.CENTRE | wx.ICON_INFORMATION
_WARNING_STYLE = wx.OK | wx.CENTRE | wx.ICON_WARNING
_ERROR_STYLE = wx.OK | wx.CENTRE | wx.ICON_ERROR
_CONFIRM_STYLE = wx.OK | wx.CANCEL | wx.CENTRE | wx.ICON_WARNING | wx.CANCEL_DEFAULT

_OK_LABEL = "Delete ride"
_CANCEL_LABEL = "Keep ride"
_CONFIRM_ACT = partial(std_dialogs.show_confirm, ok_label=_OK_LABEL, cancel_label=_CANCEL_LABEL)

# Rows align with _SHOW_CASE_IDS by index: (act, expected style).
_SHOW_CASES = (
    (std_dialogs.show_info, _INFO_STYLE),
    (std_dialogs.show_warning, _WARNING_STYLE),
    (std_dialogs.show_error, _ERROR_STYLE),
    (_CONFIRM_ACT, _CONFIRM_STYLE),
)
_SHOW_CASE_IDS = ("show_info", "show_warning", "show_error", "show_confirm")
_ALERT_CASES = _SHOW_CASES[:3]
_ALERT_CASE_IDS = _SHOW_CASE_IDS[:3]

_PARENT = object()  # a stand-in owning window; real windows never exist here
_TITLE = "Delete the ride?"
_MESSAGE = "This removes the ride and all its data."


class _FakeMessageDialog:
    """Recording stand-in for ``wx.MessageDialog``; opens no window.

    ``scripted_result`` is what ``ShowModal`` reports; a test can
    override it on the class with monkeypatch when it needs a result
    other than the default sentinel.
    """

    scripted_result: int = _SCRIPTED_MODAL_RESULT

    def __init__(  # noqa: PLR0913, PLR0917 -- mirrors wx.MessageDialog's 4-arg API
        self,
        parent: object,
        message: str,
        caption: str,
        style: int,
    ) -> None:
        self.parent = parent
        self.message = message
        self.caption = caption
        self.style = style
        self.ok_cancel_labels: tuple[str, str] | None = None
        self.show_modal_count = 0
        self.destroy_count = 0

    def SetOKCancelLabels(  # noqa: N802 -- wx API method name the SUT calls
        self, ok_label: str, cancel_label: str
    ) -> None:
        self.ok_cancel_labels = (ok_label, cancel_label)

    def ShowModal(self) -> int:  # noqa: N802 -- wx API method name the SUT calls
        self.show_modal_count += 1
        return self.scripted_result

    def Destroy(self) -> None:  # noqa: N802 -- wx API method name the SUT calls
        self.destroy_count += 1


_FAKE_CREATIONS: list[_FakeMessageDialog] = []


@pytest.fixture
def created_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[_FakeMessageDialog]:
    """Patch ``std_dialogs.wx.MessageDialog`` and record constructions.

    ``std_dialogs`` reaches wx through its own ``wx = require_wx()``
    module global, which is the real wx module, so the patch targets
    that shared object's ``MessageDialog`` attribute. monkeypatch
    restores the real class automatically after each test.
    """
    _FAKE_CREATIONS.clear()
    monkeypatch.setattr(std_dialogs.wx, "MessageDialog", _FakeMessageDialog)
    return _FAKE_CREATIONS


# --- per-function behaviour ------------------------------------------


@pytest.mark.parametrize(("act", "expected_style"), _SHOW_CASES, ids=_SHOW_CASE_IDS)
def test_show_function_constructs_single_message_dialog_with_documented_style(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,
) -> None:
    """One dialog per call, carrying exactly the documented flags."""
    act(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].style == expected_style


@pytest.mark.parametrize(("act", "expected_style"), _SHOW_CASES, ids=_SHOW_CASE_IDS)
def test_show_function_forwards_parent_title_and_message_verbatim(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,  # noqa: ARG001 -- one shared case table
) -> None:
    """The dialog records parent/message/caption exactly as passed."""
    act(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].parent is _PARENT
    assert created_dialogs[0].message == _MESSAGE
    assert created_dialogs[0].caption == _TITLE


def test_show_confirm_sets_dialog_button_labels_from_arguments(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """The confirm names both buttons from the given label text.

    Affirmative label first, cancel label second, both verbatim.
    """
    _CONFIRM_ACT(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].ok_cancel_labels == (_OK_LABEL, _CANCEL_LABEL)


@pytest.mark.parametrize(("act", "expected_style"), _ALERT_CASES, ids=_ALERT_CASE_IDS)
def test_show_alert_functions_leave_stock_ok_button_label_untouched(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,  # noqa: ARG001 -- one shared case table
) -> None:
    """Only the confirm overrides labels; alerts keep wx's stock OK."""
    act(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].ok_cancel_labels is None


@pytest.mark.parametrize(("act", "expected_style"), _SHOW_CASES, ids=_SHOW_CASE_IDS)
def test_show_function_returns_modal_result_and_destroys_the_dialog(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,  # noqa: ARG001 -- one shared case table
) -> None:
    """The caller sees ShowModal's id; the dialog is destroyed."""
    result = act(_PARENT, _TITLE, _MESSAGE)

    assert result == _SCRIPTED_MODAL_RESULT
    assert created_dialogs[0].show_modal_count == 1
    assert created_dialogs[0].destroy_count == 1


@pytest.mark.parametrize(("act", "expected_style"), _SHOW_CASES, ids=_SHOW_CASE_IDS)
def test_show_function_given_none_parent_constructs_unparented_dialog(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,  # noqa: ARG001 -- one shared case table
) -> None:
    """A None parent reaches wx untouched (top-level dialog)."""
    act(None, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].parent is None
