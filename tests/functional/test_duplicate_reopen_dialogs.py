# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for the two mock-first ride confirms (E5.4.1, H2).

File ▸ Duplicate Ride… and Ride ▸ Reopen Ride were the two §15 rows
with no frozen window until E5.4.1. H2 then retired those two authored
ride confirms for the native ``std_dialogs.show_prompt`` -- the
non-destructive confirm whose OK is
the default button, so a reflex Enter is safe (spec.md §13) -- shown
through app.py's ``_open_ride_confirm``.

These tests pin the two rows' contract at that seam: the prompt's
title and affirmative label come from app.py's own
``_RIDE_CONFIRM_PROMPTS`` table, the message is the row's copy helper
naming the ride and is never blank (UX-DESKTOP §4), and a cancelled
prompt reports "not confirmed" without acting. The native dialog's own
mechanics (Escape, default button, Cancel) belong to ``std_dialogs``
and are pinned at the unit level; this harness cannot click a native
message dialog programmatically (measured 2026-09-09), so the module
function is swapped for a recorder -- the same seam
``tests/acceptance/race_child.py``'s ``_native_confirm`` uses.

Like the rest of ``tests/functional/``, these run only in the Tart VM
-- never directly on the host (the suite opens real wx windows).
"""

import types
from typing import TYPE_CHECKING, Any

import harness
import pytest
import wx

from rivercrossing.ui import app as app_module
from rivercrossing.ui import std_dialogs
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.functional

_RIDE_NAME = "GORBA EPIC 2026"


def _prompt_context(frame: Any) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Return a route-context stand-in owning the prompt's parent."""
    return types.SimpleNamespace(frame=frame)


@pytest.mark.parametrize(
    ("target", "helper", "title", "ok_label"),
    [
        ("duplicate_ride", dialogs.duplicate_ride_message, "Duplicate Ride", "Duplicate"),
        ("reopen_ride", dialogs.reopen_ride_message, "Reopen Ride", "Reopen"),
    ],
    ids=["duplicate_ride", "reopen_ride"],
)
def test_ride_confirm_asks_the_native_prompt_with_the_rows_copy(  # noqa: PLR0913, PLR0917 -- parametrize row + fixture
    target: str,
    helper: Callable[[str], str],
    title: str,
    ok_label: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E5.4.1/H2: the native prompt gets the row's own frozen copy."""
    frame = wx.Frame(None)
    calls: list[tuple[Any, ...]] = []

    def _record(  # noqa: PLR0913, PLR0917 -- mirrors std_dialogs.show_prompt's signature
        parent: object,
        prompt_title: str,
        message: str,
        prompt_ok_label: str,
        cancel_label: str,
    ) -> int:
        calls.append((parent, prompt_title, message, prompt_ok_label, cancel_label))
        return int(wx.ID_OK)

    try:
        monkeypatch.setattr(std_dialogs, "show_prompt", _record)
        confirmed = app_module._open_ride_confirm(
            _prompt_context(frame), target, helper(_RIDE_NAME)
        )
    finally:
        harness.close_window(frame)

    assert confirmed is True
    assert calls[0][0] is frame
    assert calls[0][1:] == (title, helper(_RIDE_NAME), ok_label, "Cancel")


def test_ride_confirm_cancelled_reports_not_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-76: a cancelled prompt reports False and never acts."""
    frame = wx.Frame(None)

    try:
        monkeypatch.setattr(
            std_dialogs, "show_prompt", lambda *_args, **_kwargs: int(wx.ID_CANCEL)
        )
        confirmed = app_module._open_ride_confirm(
            _prompt_context(frame),
            "duplicate_ride",
            dialogs.duplicate_ride_message(_RIDE_NAME),
        )
    finally:
        harness.close_window(frame)

    assert confirmed is False


@pytest.mark.parametrize(
    "helper",
    [dialogs.duplicate_ride_message, dialogs.reopen_ride_message],
    ids=lambda value: getattr(value, "__name__", value),
)
def test_ride_message_helper_never_blank_and_names_the_ride(
    helper: Callable[[str], str],
) -> None:
    """UX-DESKTOP §4: the confirm names its ride; a blank line fails.

    The message helper's output is what ``_open_ride_confirm`` hands
    the native prompt, so a blank return here would render a blank
    confirmation -- a failed assertion, never a cosmetic one (the same
    rule E5.2.2 pinned for resume_dlg).
    """
    label = helper(_RIDE_NAME)

    assert label != ""
    assert _RIDE_NAME in label
