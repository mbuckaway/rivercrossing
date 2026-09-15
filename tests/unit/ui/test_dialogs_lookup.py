# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the dialog control lookup (``views.dialogs``).

``views.dialogs`` carries its own copy of the scoped control lookup --
``_support.find_control``'s shape, raising
:class:`~rivercrossing.ui.views.dialogs.MissingDialogControlError`
instead. It resolves through
``_support.find_window_by_name``'s recursive ``GetChildren()`` walk,
never ``wx.Window.FindWindowByName``, which on Windows ARM64
(wxPython 4.3.1) does not resolve children that are present. Plain
control doubles are enough here: no real dialog, and no desktop, is
needed to pin the walk, the type check or the error text.
"""

from __future__ import annotations

import re

import pytest
import wx

from rivercrossing.ui.views import dialogs


class _FakeControl:
    """A headless control double: a name, an id and its children."""

    def __init__(self, name: str, control_id: int = 0, *children: object) -> None:
        """Name the control, give it *control_id*, hold its children."""
        self._name = name
        self._control_id = control_id
        self._children = list(children)

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Return this control's frozen name."""
        return self._name

    def GetId(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return this control's id."""
        return self._control_id

    def GetChildren(self) -> list[object]:  # noqa: N802 -- wx API name the SUT calls
        """Return this control's direct children."""
        return self._children


class _FakeDialog(_FakeControl):
    """A dialog double that records the wiring the SUT applies."""

    def __init__(self, name: str, *children: object) -> None:
        """Name this dialog and hold its direct children."""
        super().__init__(name, 0, *children)
        self.escape_ids: list[int] = []
        self.bindings: list[object] = []

    def SetEscapeId(self, escape_id: int) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the escape id the SUT sets."""
        self.escape_ids.append(escape_id)

    def Bind(  # noqa: N802 -- wx API name the SUT calls
        self,
        _event: object,
        _handler: object,
        source: object,
    ) -> None:
        """Record the control the SUT binds the dismiss handler to."""
        self.bindings.append(source)


def _refuse_lookup(*_args: object, **_kwargs: object) -> None:
    """Fail the test if anything consults the wx name lookup."""
    pytest.fail("the dialog lookup must not call wx.Window.FindWindowByName")


def test_control_resolves_a_child_without_the_wx_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows-ARM64 fix at this lookup copy: the walk finds it."""
    monkeypatch.setattr(wx.Window, "FindWindowByName", _refuse_lookup)
    plate_input = _FakeControl("plate_input")
    dialog = _FakeDialog("rider_editor_dlg", _FakeControl("panel", 0, plate_input))

    found = dialogs._control(dialog, "plate_input", _FakeControl)

    assert found is plate_input


def test_control_raises_naming_the_dialog_and_the_missing_control() -> None:
    """T-5: an absent name raises, naming the dialog and the control."""
    dialog = _FakeDialog("resume_dlg", _FakeControl("message_lbl"))

    with pytest.raises(
        dialogs.MissingDialogControlError,
        match=re.escape("dialog 'resume_dlg' has no control named 'continue_btn'"),
    ):
        dialogs._control(dialog, "continue_btn", _FakeControl)


def test_control_raises_when_the_only_match_is_the_wrong_type() -> None:
    """A present control of the wrong class still counts as missing."""
    dialog = _FakeDialog("resume_dlg", _FakeControl("continue_btn"))

    with pytest.raises(
        dialogs.MissingDialogControlError,
        match=re.escape("dialog 'resume_dlg' has no control named 'continue_btn'"),
    ):
        dialogs._control(dialog, "continue_btn", wx.Button)


def test_wire_close_button_given_no_close_control_leaves_the_dialog_alone() -> None:
    """The guard: a dialog with no ``wxID_CLOSE`` sets nothing."""
    dialog = _FakeDialog("about_dlg", _FakeControl("ok_btn"))

    dialogs.wire_close_button(dialog)

    assert dialog.escape_ids == []
    assert dialog.bindings == []


def test_wire_close_button_given_a_close_control_routes_escape_and_click() -> None:
    """Escape and the click both end the dialog with Close's own id."""
    close_button = _FakeControl("wxID_CLOSE", 5101)
    dialog = _FakeDialog("about_dlg", close_button)

    dialogs.wire_close_button(dialog)

    assert dialog.escape_ids == [5101]
    assert dialog.bindings == [close_button]
