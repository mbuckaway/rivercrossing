# SPDX-License-Identifier: GPL-3.0-only
"""Headless pin for the Standings route's always-available state.

Results ▸ Standings (``mi_standings``) is the ``ROUTE_TABLE`` row whose
``enabled_when=ALWAYS`` makes it the window that must open with no
store-backed ride and no console presenter threaded at all.
``app._open_target`` loads ``results_dlg`` and ``app._decorate`` then
binds ``ResultsWindow`` over the module-level ``app._EMPTY_SOURCE``
fallback (app.py's RESULTS_DLG branch); the live presenter's engine
source and rank order are swapped in only when one is present. This
module drives that route headlessly -- a fake resource window, the
``dialogs.run_dialog`` modal seam and the ``ResultsWindow`` view class
swapped for a recorder, the pattern ``test_app_write_guards.py``'s
``_open_target`` tests and ``test_app_ride_menu.py``'s ``_decorate``
tests already established -- and pins the exact decoration the empty
state receives. No wx window, app or dialog is ever constructed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivercrossing.roster import Roster
from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands
from rivercrossing.ui.views import results_win

if TYPE_CHECKING:
    import pytest


class _NoticeFrame:
    """A frame double: status notices are captured, nothing else."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record one status-bar notice."""
        self.notices.append(text)


class _FakeWindow:
    """A wx-window-shaped stub: closable, nothing else."""

    def IsBeingDeleted(self) -> bool:  # noqa: N802 -- wx API name
        """Report this stub is never mid-delete."""
        return False

    def Destroy(self) -> None:  # noqa: N802 -- wx API name
        """No-op: there is no real window to destroy."""


class _FakeResource:
    """A resource-shaped stub returning the one fake window."""

    def __init__(self, window: _FakeWindow) -> None:
        """Store the window every LoadDialog call returns."""
        self.window = window

    def LoadDialog(  # noqa: N802 -- wx API name
        self, _parent: object, _name: object
    ) -> _FakeWindow:
        """Return the one fake window for the requested target."""
        return self.window


def test_open_target_given_standings_with_no_presenter_uses_the_empty_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The always-available Standings route opens the empty state.

    With no presenter there is no live engine to read rows, rank order
    or entry mode from, so ``_decorate`` must hand ``ResultsWindow``
    the module's shared empty source and none of the live-presenter
    arguments (tie-break order, watermark, entry mode, export
    callback). That is what keeps the dialog renderable with zero rows
    and its export buttons disabled instead of raising.
    """
    captured: dict[str, object] = {}

    def _record_results_window(dialog: object, **kwargs: object) -> None:
        captured["dialog"] = dialog
        captured.update(kwargs)

    monkeypatch.setattr(results_win, "ResultsWindow", _record_results_window)
    monkeypatch.setattr(app_module.zoom, "apply_to", lambda _window: None)
    monkeypatch.setattr(app_module, "_apply_dialog_defaults", lambda _window, _route: None)
    from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- the patched modal seam

    monkeypatch.setattr(dialogs, "run_dialog", lambda _dialog, opener: 0)  # noqa: ARG005 -- the SUT calls opener=; the stub ignores it
    window = _FakeWindow()
    context = app_module._RouteContext(
        frame=_NoticeFrame(),
        resource=_FakeResource(window),
        roster=Roster(),
        app=None,
        theme_controller=None,
    )

    app_module._open_target(context, commands.route_for_id("mi_standings"))

    assert set(captured) == {"dialog", "data_source"}
    assert captured["dialog"] is window
    assert captured["data_source"] is app_module._EMPTY_SOURCE
