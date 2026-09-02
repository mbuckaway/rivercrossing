# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app-side Fault-B guard (degraded XRC load).

Production mirror of ``harness.load_window_verified``: under worker
load the process-global ``wx.xrc.XmlResource`` singleton can silently
skip a subtree during a load, so ``main_frame`` comes back missing a
control ``MainFrame.__init__`` later ``_find``s -- which surfaces as a
bare ``LookupError`` from ``ui.views._support.find_control``. The
app-side fix (``ui.app._load_frame_verified``) verifies the frame's
required controls and rebuilds once from a fresh private resource.

These tests never construct a real window: ``FindWindowByName`` is
monkeypatched to simulate a skipped subtree, and the frame/resource
objects are ``MagicMock``s, so the verify/rebuild decision logic runs
headless (the same reason ``test_view_support.py`` carries no window).
"""

import re
from unittest.mock import MagicMock

import pytest
import wx

from rivercrossing.ui import app, ids
from rivercrossing.ui.views.main_frame import REQUIRED_CONTROLS

pytestmark = pytest.mark.functional


def test_required_controls_lists_exactly_the_init_find_controls() -> None:
    """The verify tuple is the single source for __init__'s 17 finds.

    Pins the contract so the guard can never silently drift from
    ``MainFrame.__init__``: if a control is added/removed there without
    updating this tuple, this test fails.
    """
    assert REQUIRED_CONTROLS == (
        ids.CROSSINGS_LIST,
        ids.MAIN_SPLITTER,
        ids.PLATE_INPUT,
        ids.RECORD_BTN,
        ids.LAST_CROSSING_LBL,
        ids.RIDE_NAME_LBL,
        ids.RIDE_STATUS_LBL,
        ids.CROSSINGS_COUNT_LBL,
        ids.CARDS_COUNT_LBL,
        ids.ON_COURSE_LBL,
        ids.SHOE_LBL,
        ids.START_BTN,
        ids.ARM_STOP_CHK,
        ids.STOP_BTN,
        ids.UNDO_BTN,
        ids.CLOCK_ELAPSED_LBL,
        ids.CLOCK_REMAINING_LBL,
    )


def test_missing_required_control_returns_none_when_all_controls_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame where every required name resolves is complete."""
    required = (ids.CROSSINGS_LIST, ids.MAIN_SPLITTER)
    frame = MagicMock()
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: object())

    assert app._missing_required_control(frame, required) is None


def test_missing_required_control_returns_the_first_skipped_control_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first name XRC skipped (does not resolve) is reported."""
    required = (ids.CROSSINGS_LIST, ids.LAST_CROSSING_LBL)
    frame = MagicMock()
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda name, _parent=None: None if name == ids.LAST_CROSSING_LBL else object(),
    )

    assert app._missing_required_control(frame, required) == ids.LAST_CROSSING_LBL


def test_missing_required_control_returns_none_for_an_empty_contract() -> None:
    """An empty required tuple means the frame is trivially complete."""
    frame = MagicMock()

    assert app._missing_required_control(frame, ()) is None


def test_load_frame_verified_returns_the_first_frame_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete singleton load returns untouched (no rebuild)."""
    required = (ids.CROSSINGS_LIST,)
    frame = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = frame
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: object())

    def _fresh_must_not_run() -> None:
        raise AssertionError("a complete frame must not trigger a rebuild")

    monkeypatch.setattr(app, "_fresh_xrc_resource", _fresh_must_not_run)

    result = app._load_frame_verified(resource, required)

    assert result is frame
    resource.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    frame.Destroy.assert_not_called()


def test_load_frame_verified_rebuilds_once_from_a_fresh_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded singleton load is rebuilt once from a fresh resource.

    The rebuilt frame is returned; the degraded frame is destroyed only
    after the fresh build (mirroring the harness ordering).
    """
    required = (ids.CROSSINGS_LIST,)
    degraded = MagicMock()
    rebuilt = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = rebuilt
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, parent=None: None if parent is degraded else object(),
    )

    result = app._load_frame_verified(resource, required)

    assert result is rebuilt
    resource.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    fresh.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    degraded.Destroy.assert_called_once_with()
    rebuilt.Destroy.assert_not_called()


def test_load_frame_verified_raises_when_rebuild_is_still_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-incomplete rebuild raises the find_control error."""
    required = (ids.CROSSINGS_LIST, ids.LAST_CROSSING_LBL)
    degraded = MagicMock()
    rebuilt = MagicMock()
    rebuilt.GetName.return_value = ids.MAIN_FRAME
    rebuilt.GetChildren.return_value = []
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = rebuilt
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: None)

    with pytest.raises(
        LookupError,
        match=re.escape(
            f"{ids.MAIN_FRAME} has no control named {ids.CROSSINGS_LIST!r} "
            "(first-level children: 0 -- [])"
        ),
    ):
        app._load_frame_verified(resource, required)

    degraded.Destroy.assert_called_once_with()
    rebuilt.Destroy.assert_called_once_with()


def test_load_frame_verified_raises_when_the_fresh_resource_has_no_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh resource that cannot rebuild raises an error."""
    required = (ids.CROSSINGS_LIST,)
    degraded = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = None
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: None)

    with pytest.raises(
        LookupError,
        match=re.escape(f"fresh XmlResource found no window named {ids.MAIN_FRAME!r} to rebuild"),
    ):
        app._load_frame_verified(resource, required)

    degraded.Destroy.assert_called_once_with()


def test_load_xrc_resources_memoizes_the_global_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process-global XRC resource is loaded once and reused.

    Re-loading on every ``build_main_window`` call re-parses the .xrc
    files, and a later re-parse can re-roll the Fault-B degradation
    the guard exists to work around. The app loader memoizes the
    singleton, matching the harness's load-once pattern.
    """
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    monkeypatch.setattr(app, "_loaded_xrc_resource", None)
    get_calls = 0

    def _fake_get() -> MagicMock:
        nonlocal get_calls
        get_calls += 1
        return MagicMock()

    monkeypatch.setattr(wx.xrc.XmlResource, "Get", staticmethod(_fake_get))

    first = app._load_xrc_resources()
    second = app._load_xrc_resources()

    assert first is second
    assert get_calls == 1
