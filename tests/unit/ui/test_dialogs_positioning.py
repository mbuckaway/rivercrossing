# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for Phase 11 H1/H2's dialog seam and copy.

``views.dialogs.run_dialog`` is the one seam every XRC dialog shows
through. H1 makes it re-parent the loaded dialog to the opener's own
top-level window and centre it there before ``ShowModal``, so every
modal lands over the console rather than wherever the platform put
it. Real ``wx.Dialog`` windows need a desktop (and would block on
``ShowModal``), so the tests drive a recording fake and stub only the
two wx-touching collaborators the seam also calls --
``wire_close_button`` and the light-mode panel tint -- exactly the
way ``test_std_dialogs.py`` swaps ``wx.MessageDialog``.

The second half pins the copy H2 moved out of the four retired XRC
dialogs, so the ride-naming sentences those windows carried cannot
go blank unnoticed (UX-DESKTOP §4: a confirm names its object).
"""

import json
import string
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.logging import VERBOSE_LOG_NAME, VerboseLog
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    from pathlib import Path

_SCRIPTED_MODAL_RESULT = 40001  # a stand-in modal id, no real wx stock id


class _FakeDialog:
    """Record the positioning calls; report a scripted modal result."""

    def __init__(
        self, modal_result: int = _SCRIPTED_MODAL_RESULT, name: str = "settings_dlg"
    ) -> None:
        """Start empty; report *modal_result* from ShowModal."""
        self.modal_result = modal_result
        self.name = name
        self.reparented_to: object = "NOT_REPARENTED"
        self.centre_on_parent_calls = 0
        self.show_modal_calls = 0

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Report the dialog's frozen XRC name."""
        return self.name

    def Reparent(self, parent: object) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the re-parent target."""
        self.reparented_to = parent

    def CentreOnParent(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one centring call."""
        self.centre_on_parent_calls += 1

    def ShowModal(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Record the show and report the scripted result."""
        self.show_modal_calls += 1
        return self.modal_result


class _FakeOpener:
    """A window double whose top-level ancestor is scripted."""

    def __init__(self, top_level: object, name: str = "mi_settings") -> None:
        """Return *top_level* from GetTopLevelParent."""
        self._top_level = top_level
        self.name = name
        self.focus_calls = 0

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Report the opener's frozen name."""
        return self.name

    def GetTopLevelParent(self) -> object:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted top-level window."""
        return self._top_level

    def SetFocus(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one focus restore."""
        self.focus_calls += 1


@pytest.fixture
def tinted_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Stub the seam's two wx-touching collaborators; record the tint.

    ``wire_close_button`` walks a real dialog's children, the theme
    tint reads the live system appearance, and the verbose log reads
    the live ``wx.App``, so all three are swapped for recorders: they
    are the GUI I/O boundary of this seam (T-10), not the logic under
    test. A test that wants the log drives its own replacement (see
    :func:`test_run_dialog_given_a_verbose_log_records_the_dialog_open`).
    """
    tinted: list[object] = []
    monkeypatch.setattr(dialogs, "wire_close_button", lambda _dialog: None)
    monkeypatch.setattr(dialogs.theme, "apply_light_mode_panel_bg", tinted.append)
    monkeypatch.setattr(dialogs, "_active_verbose_log", lambda: None)
    return tinted


def test_run_dialog_reparents_the_dialog_to_the_openers_top_level_window(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """H1: the opener's top-level window becomes the dialog's parent."""
    top_level = object()
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener(top_level))

    assert dialog.reparented_to is top_level


def test_run_dialog_centres_the_dialog_on_its_parent_before_showing_it(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """H1: one CentreOnParent per show, so the modal lands on top."""
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener(object()))

    assert (dialog.centre_on_parent_calls, dialog.show_modal_calls) == (1, 1)


def test_run_dialog_returns_the_modal_result(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """The caller sees ShowModal's id, unchanged."""
    dialog = _FakeDialog(modal_result=-7)

    result = dialogs.run_dialog(dialog, _FakeOpener(object()))

    assert result == -7


def test_run_dialog_restores_focus_to_the_opener_after_the_modal(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """spec.md §13's last dialog rule still runs after H1."""
    opener = _FakeOpener(object())

    dialogs.run_dialog(_FakeDialog(), opener)

    assert opener.focus_calls == 1


def test_run_dialog_applies_the_light_mode_tint_before_showing(
    tinted_dialogs: list[object],
) -> None:
    """H1 left the ux-polish tint in place: tinted once."""
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener(object()))

    assert tinted_dialogs == [dialog]


# --- F4: the verbose log's dialog record -----------------------------


def test_run_dialog_given_a_verbose_log_records_the_dialog_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tinted_dialogs: list[object],  # noqa: ARG001
) -> None:
    """F4: the one dialog seam records the dialog's name and opener."""
    log = VerboseLog(tmp_path / VERBOSE_LOG_NAME)
    monkeypatch.setattr(dialogs, "_active_verbose_log", lambda: log)

    dialogs.run_dialog(_FakeDialog(name="settings_dlg"), _FakeOpener(object(), name="mi_settings"))

    records = [
        json.loads(line)
        for line in (tmp_path / VERBOSE_LOG_NAME).read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [record["msg"] for record in records] == ["dialog settings_dlg (from mi_settings)"]


def test_run_dialog_without_a_verbose_log_still_shows_the_dialog(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture stubs the log to None
) -> None:
    """F4: an app with no log shows the dialog unchanged."""
    dialog = _FakeDialog()

    result = dialogs.run_dialog(dialog, _FakeOpener(object()))

    assert (result, dialog.show_modal_calls) == (_SCRIPTED_MODAL_RESULT, 1)


# --- H2: the copy the four retired XRC dialogs carried ----------------


def test_finish_ride_message_states_the_lock_and_the_reopen_offer() -> None:
    """The finish confirm explains what finishing does."""
    message = dialogs.finish_ride_message()

    assert "evaluator self-test" in message
    assert "reopen" in message.lower()


def test_duplicate_ride_message_names_the_ride_and_the_copied_parts() -> None:
    """Duplicate names the ride and promises setup + roster only."""
    message = dialogs.duplicate_ride_message("GORBA EPIC 2026")

    assert 'Duplicate "GORBA EPIC 2026" as a new DRAFT ride?' in message
    assert "no timing data" in message


def test_reopen_ride_message_names_the_ride_and_the_recompute() -> None:
    """Reopen names the ride and what the corrections state costs."""
    message = dialogs.reopen_ride_message("Club poker night")

    assert 'Reopen "Club poker night" for corrections?' in message
    assert "recompute" in message


_RIDE_NAME_STRATEGY = st.text(
    alphabet=string.ascii_letters + string.digits + " -",
    min_size=1,
    max_size=40,
).filter(lambda name: name.strip() == name)


@given(ride_name=_RIDE_NAME_STRATEGY)
def test_ride_confirm_messages_given_any_ride_name_embed_it_verbatim(
    ride_name: str,
) -> None:
    """T-7 property: each confirm quotes the ride name exactly once."""
    duplicate = dialogs.duplicate_ride_message(ride_name)
    reopen = dialogs.reopen_ride_message(ride_name)

    assert duplicate.count(f'"{ride_name}"') == 1
    assert reopen.count(f'"{ride_name}"') == 1
