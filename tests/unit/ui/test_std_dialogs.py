# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ui.std_dialogs' native message-dialog helpers.

The eight ``show_*`` functions are thin ``wx.MessageDialog`` wiring:
construct with a fixed style, optionally override the button labels,
show modally, destroy, and return the modal id. Real dialogs
need a desktop and would block on ``ShowModal``, so every test swaps
``std_dialogs.wx.MessageDialog`` for a recording double. Importing wx
is safe without a display; constructing wx windows is not, so this
file never creates a window and never builds a ``wx.App``.

Expected style constants below are recomputed from real wx flags, so
the bitwise-equality assertions pin each dialog's exact documented
flag set: ``wx.ICON_INFORMATION`` / ``wx.ICON_WARNING`` /
``wx.ICON_ERROR`` with ``wx.CENTRE`` on every dialog. The two
destructive confirms (``show_confirm`` warns, ``show_danger`` errors)
also carry ``wx.CANCEL`` + ``wx.CANCEL_DEFAULT``, while the
non-destructive ``show_prompt`` and ``show_retry`` carry
``wx.CANCEL`` but leave OK as the default button -- a reflex Enter
must never destroy data, and must never block a safe action either.
``show_retry`` is the publish failure's question (Part B): the error
icon because it reports a failure, and Retry as the default because
retrying a failed publish loses nothing.

``show_three_choice`` is the one dialog with three outcomes -- Yes
confirms, No voids, Cancel leaves the record alone -- so it carries
``wx.YES_NO | wx.CANCEL`` with ``wx.CANCEL_DEFAULT``: only Cancel is
safe to reach by a reflex Enter, and it neither confirms nor voids.
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
_DANGER_STYLE = wx.OK | wx.CANCEL | wx.CENTRE | wx.ICON_ERROR | wx.CANCEL_DEFAULT
_PROMPT_STYLE = wx.OK | wx.CANCEL | wx.CENTRE | wx.ICON_INFORMATION
_RETRY_STYLE = wx.OK | wx.CANCEL | wx.CENTRE | wx.ICON_ERROR
_THREE_CHOICE_STYLE = wx.YES_NO | wx.CANCEL | wx.CENTRE | wx.ICON_QUESTION | wx.CANCEL_DEFAULT

_OK_LABEL = "Delete ride"
_CANCEL_LABEL = "Keep ride"
_YES_LABEL = "Confirm crossing"
_NO_LABEL = "Void crossing"
_LEAVE_LABEL = "Leave as is"
_CONFIRM_ACT = partial(std_dialogs.show_confirm, ok_label=_OK_LABEL, cancel_label=_CANCEL_LABEL)
_DANGER_ACT = partial(std_dialogs.show_danger, ok_label=_OK_LABEL, cancel_label=_CANCEL_LABEL)
_PROMPT_ACT = partial(std_dialogs.show_prompt, ok_label=_OK_LABEL, cancel_label=_CANCEL_LABEL)
_RETRY_ACT = partial(std_dialogs.show_retry, retry_label=_OK_LABEL, cancel_label=_CANCEL_LABEL)
_THREE_CHOICE_ACT = partial(
    std_dialogs.show_three_choice,
    yes_label=_YES_LABEL,
    no_label=_NO_LABEL,
    cancel_label=_LEAVE_LABEL,
)

# Rows align with _SHOW_CASE_IDS by index: (act, expected style).
_SHOW_CASES = (
    (std_dialogs.show_info, _INFO_STYLE),
    (std_dialogs.show_warning, _WARNING_STYLE),
    (std_dialogs.show_error, _ERROR_STYLE),
    (_CONFIRM_ACT, _CONFIRM_STYLE),
    (_DANGER_ACT, _DANGER_STYLE),
    (_PROMPT_ACT, _PROMPT_STYLE),
    (_RETRY_ACT, _RETRY_STYLE),
    (_THREE_CHOICE_ACT, _THREE_CHOICE_STYLE),
)
_SHOW_CASE_IDS = (
    "show_info",
    "show_warning",
    "show_error",
    "show_confirm",
    "show_danger",
    "show_prompt",
    "show_retry",
    "show_three_choice",
)
_ALERT_CASES = _SHOW_CASES[:3]
_ALERT_CASE_IDS = _SHOW_CASE_IDS[:3]
# The four labelled questions name their own buttons; the three alerts
# keep wx's.
_LABELLED_CONFIRM_CASES = (
    (_CONFIRM_ACT, _CONFIRM_STYLE),
    (_DANGER_ACT, _DANGER_STYLE),
    (_PROMPT_ACT, _PROMPT_STYLE),
    (_RETRY_ACT, _RETRY_STYLE),
)
_LABELLED_CONFIRM_CASE_IDS = ("show_confirm", "show_danger", "show_prompt", "show_retry")

# Phase 11 H2: the icon and the default button are what separate the
# labelled two-button questions -- (act, expected icon, whether Cancel
# is the default). show_retry (Part B) is the one error-icon question
# whose default is the OK/Retry side.
_CONFIRM_ICON_AND_DEFAULT_CASES = (
    (_CONFIRM_ACT, wx.ICON_WARNING, True),
    (_DANGER_ACT, wx.ICON_ERROR, True),
    (_PROMPT_ACT, wx.ICON_INFORMATION, False),
    (_RETRY_ACT, wx.ICON_ERROR, False),
)
_CONFIRM_ICON_AND_DEFAULT_CASE_IDS = (
    "show_confirm",
    "show_danger",
    "show_prompt",
    "show_retry",
)

# Every modal id the three-choice dialog can return.
_THREE_CHOICE_RESULT_CASES = (wx.ID_YES, wx.ID_NO, wx.ID_CANCEL)
_THREE_CHOICE_RESULT_CASE_IDS = ("yes", "no", "cancel")

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
        self.yes_no_cancel_labels: tuple[str, str, str] | None = None
        self.show_modal_count = 0
        self.destroy_count = 0
        _FAKE_CREATIONS.append(self)

    def SetOKCancelLabels(  # noqa: N802 -- wx API method name the SUT calls
        self, ok_label: str, cancel_label: str
    ) -> None:
        self.ok_cancel_labels = (ok_label, cancel_label)

    def SetYesNoCancelLabels(  # noqa: N802 -- wx API method name the SUT calls
        self, yes_label: str, no_label: str, cancel_label: str
    ) -> None:
        self.yes_no_cancel_labels = (yes_label, no_label, cancel_label)

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


@pytest.mark.parametrize(
    ("act", "expected_style"),
    _LABELLED_CONFIRM_CASES,
    ids=_LABELLED_CONFIRM_CASE_IDS,
)
def test_confirm_family_sets_dialog_button_labels_from_arguments(
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_style: int,  # noqa: ARG001 -- one shared case table
) -> None:
    """Every confirm names its buttons from the given label text.

    Affirmative label first, cancel label second, both verbatim.
    """
    act(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].ok_cancel_labels == (_OK_LABEL, _CANCEL_LABEL)


@pytest.mark.parametrize(
    ("act", "expected_icon", "cancel_is_default"),
    _CONFIRM_ICON_AND_DEFAULT_CASES,
    ids=_CONFIRM_ICON_AND_DEFAULT_CASE_IDS,
)
def test_confirm_family_sets_icon_and_default_button_per_destructiveness(  # noqa: PLR0913
    created_dialogs: list[_FakeMessageDialog],
    act: Callable[[object, str, str], int],
    expected_icon: int,
    *,
    cancel_is_default: bool,
) -> None:
    """H2: warnings/errors default to Cancel; the prompt to OK."""
    act(_PARENT, _TITLE, _MESSAGE)

    style = created_dialogs[0].style
    assert style & expected_icon == expected_icon
    assert bool(style & wx.CANCEL_DEFAULT) is cancel_is_default


def test_show_danger_differs_from_show_confirm_only_in_its_icon(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """D3: Clear Ride's confirm reads as an error, not a warning."""
    _DANGER_ACT(_PARENT, _TITLE, _MESSAGE)

    style = created_dialogs[0].style
    assert style & wx.ICON_ERROR == wx.ICON_ERROR
    assert style & wx.ICON_WARNING == 0
    assert style & wx.CANCEL_DEFAULT == wx.CANCEL_DEFAULT


def test_show_retry_given_a_failed_publish_keeps_retry_as_the_default_button(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """Part B: retrying loses nothing, so a reflex Enter retries.

    The publish failure's own shape: the error icon (it reports a
    failure) with OK left as the default, unlike ``show_danger``'s
    error icon plus ``wx.CANCEL_DEFAULT``.
    """
    _RETRY_ACT(_PARENT, _TITLE, _MESSAGE)

    style = created_dialogs[0].style
    assert style & wx.ICON_ERROR == wx.ICON_ERROR
    assert style & (wx.OK | wx.CANCEL) == wx.OK | wx.CANCEL
    assert style & wx.CANCEL_DEFAULT == 0


def test_show_retry_given_no_labels_names_its_buttons_retry_and_cancel(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """The defaults are the wording the publish failure shows."""
    std_dialogs.show_retry(_PARENT, _TITLE, _MESSAGE)

    assert created_dialogs[0].ok_cancel_labels == ("Retry", "Cancel")


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


# --- three-choice dialog ---------------------------------------------


def test_show_three_choice_sets_yes_no_cancel_labels_from_arguments(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """Yes, No then Cancel name the three buttons, all verbatim."""
    _THREE_CHOICE_ACT(_PARENT, _TITLE, _MESSAGE)

    assert len(created_dialogs) == 1
    assert created_dialogs[0].yes_no_cancel_labels == (_YES_LABEL, _NO_LABEL, _LEAVE_LABEL)


@pytest.mark.parametrize(
    "scripted_result", _THREE_CHOICE_RESULT_CASES, ids=_THREE_CHOICE_RESULT_CASE_IDS
)
def test_show_three_choice_returns_the_operators_chosen_modal_id(
    created_dialogs: list[_FakeMessageDialog],
    monkeypatch: pytest.MonkeyPatch,
    scripted_result: int,
) -> None:
    """Yes, No and Cancel each reach the caller unchanged."""
    monkeypatch.setattr(_FakeMessageDialog, "scripted_result", scripted_result)

    result = _THREE_CHOICE_ACT(_PARENT, _TITLE, _MESSAGE)

    assert result == scripted_result
    assert created_dialogs[0].show_modal_count == 1
    assert created_dialogs[0].destroy_count == 1


def test_show_three_choice_given_three_labels_makes_cancel_the_default(
    created_dialogs: list[_FakeMessageDialog],
) -> None:
    """Cancel is always the default button, never a caller's choice.

    The one real caller (the held-card review prompt) asks a
    destructive-or-void question with the same severity as every other
    ``show_three_choice``, so the default button and the icon are fixed
    here rather than parameterized.
    """
    _THREE_CHOICE_ACT(_PARENT, _TITLE, _MESSAGE)

    style = created_dialogs[0].style
    assert style == _THREE_CHOICE_STYLE
    assert style & (wx.YES_NO | wx.CANCEL) == wx.YES_NO | wx.CANCEL
