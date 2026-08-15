# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for ``ui.views._support``'s lookup escalation.

``find_control``'s own docstring documents the measured hazard: under
sustained worker load, wx's wrapper cache can still type a freshly
allocated control as a *different*, already-destroyed control's Python
class, and ``wx.SafeYield()`` alone cannot drain the deferred-deletion
queue that would clear the poisoned cache entry. The fix escalates:
when the SafeYield loop exhausts, it drives idle processing
deterministically -- the machinery ``tests/functional/harness.py``'s
``flush_deferred_deletions`` proves -- which completes
``DeletePendingObjects`` and lets a re-find re-wrap with the correct
type.

The corruption itself is load-dependent and cannot be reproduced
locally on demand (CI's failure logs are the red evidence); each test
pins a piece of the escalation deterministically with monkeypatched
seams: ``wx.SafeYield`` a no-op and ``wx.Window.FindWindowByName``
wrong-typed until the escalated idle pass flips it right. No wx object
is ever constructed -- the same import-only-for-the-seam relationship
``test_theme.py`` already uses -- and the real-wx path stays covered
by the functional suite.
"""

from __future__ import annotations

import re
import types
from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.ui.views import _support

if TYPE_CHECKING:
    from collections.abc import Callable


class _ExpectedControl:
    """The control type a lookup must resolve to (marker stand-in)."""


class _WrongTypedWrapper:
    """A wrapper whose Python type is not the expected control's.

    Mirrors ``find_control``'s documented corruption: the wx wrapper
    cache still types a freshly allocated control as a different,
    already-destroyed control's Python class.
    """


def _window() -> types.SimpleNamespace:
    """Build a window stub whose name/children feed the raise path."""
    return types.SimpleNamespace(GetName=lambda: "test_window", GetChildren=list)


def _escalating_lookup(
    state: dict[str, bool], wrong: object, right: object
) -> Callable[[str, object], object]:
    """Return the seam: wrong-typed until the idle flush flips it.

    SafeYield never retypes (it is a no-op in these tests); only a
    ``ProcessIdle`` pass -- the escalated idle flush -- flips
    ``state["idle_flushed"]``, so a lookup returns *wrong* until the
    escalation has run and *right* afterwards.
    """

    def lookup(_name: str, _parent: object) -> object:
        return right if state["idle_flushed"] else wrong

    return lookup


def _settling_loop(state: dict[str, bool]) -> types.SimpleNamespace:
    """Return a loop stand-in whose first ProcessIdle pass settles.

    The single ``ProcessIdle()`` call flips ``state["idle_flushed"]``
    (the retype a real ``DeletePendingObjects`` would perform) and then
    reports ``False`` so the bounded drain stops immediately.
    """

    def process_idle() -> bool:
        state["idle_flushed"] = True
        return False

    return types.SimpleNamespace(YieldFor=lambda _category: None, ProcessIdle=process_idle)


def test_find_control_given_an_expected_typed_control_returns_it_without_yielding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: a correct-typed wrapper never costs a yield."""
    right = _ExpectedControl()
    monkeypatch.setattr(wx.Window, "FindWindowByName", staticmethod(lambda _name, _parent: right))
    yielded: list[bool] = []
    monkeypatch.setattr(wx, "SafeYield", lambda: yielded.append(True))

    result = _support.find_control(object(), "control_name", _ExpectedControl)

    assert result is right
    assert yielded == []


def test_find_control_when_safeyield_retypes_returns_before_the_idle_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cheap first resort wins: SafeYield retypes, no escalation."""
    wrong = _WrongTypedWrapper()
    right = _ExpectedControl()
    lookups = iter([wrong, right])
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        staticmethod(lambda _name, _parent: next(lookups)),
    )
    monkeypatch.setattr(wx, "SafeYield", lambda: None)
    idle_passes: list[bool] = []
    monkeypatch.setattr(
        wx.EventLoopBase,
        "GetActive",
        lambda: types.SimpleNamespace(
            YieldFor=lambda _category: None,
            ProcessIdle=lambda: idle_passes.append(True) or False,
        ),
    )

    result = _support.find_control(object(), "control_name", _ExpectedControl)

    assert result is right
    assert idle_passes == []


def test_find_control_escalates_to_an_idle_flush_when_safeyield_cannot_retype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The red->green pin: SafeYield never retypes, one idle pass does.

    On the pre-fix code this raises ``LookupError`` after the 25
    SafeYield attempts (the CI failure); after the fix, the escalated
    idle flush flips the wrapper's type and the re-find succeeds.
    """
    state = {"idle_flushed": False}
    wrong = _WrongTypedWrapper()
    right = _ExpectedControl()
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        staticmethod(_escalating_lookup(state, wrong, right)),
    )
    monkeypatch.setattr(wx, "SafeYield", lambda: None)
    monkeypatch.setattr(wx.EventLoopBase, "GetActive", lambda: _settling_loop(state))

    result = _support.find_control(_window(), "control_name", _ExpectedControl)

    assert result is right


def test_find_control_idle_escalation_creates_and_activates_a_loop_when_none_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-active-loop case: create and activate a throwaway loop.

    Without a running ``MainLoop``, ``wx.EventLoopBase.GetActive()`` is
    ``None`` and a useful yield needs a created, activated loop first;
    the escalation must construct exactly one and hand it to the
    activator exactly once.
    """
    state = {"idle_flushed": False}
    wrong = _WrongTypedWrapper()
    right = _ExpectedControl()
    activated: list[object] = []
    loop = _settling_loop(state)
    traits = types.SimpleNamespace(CreateEventLoop=lambda: loop)
    app = types.SimpleNamespace(GetTraits=lambda: traits)

    def _activate(created: object) -> object:
        activated.append(created)
        return types.SimpleNamespace()

    monkeypatch.setattr(wx.EventLoopBase, "GetActive", lambda: None)
    monkeypatch.setattr(wx, "GetApp", lambda: app)
    monkeypatch.setattr(wx, "EventLoopActivator", _activate)
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        staticmethod(_escalating_lookup(state, wrong, right)),
    )
    monkeypatch.setattr(wx, "SafeYield", lambda: None)

    result = _support.find_control(_window(), "control_name", _ExpectedControl)

    assert result is right
    assert activated == [loop]


def test_find_control_when_the_idle_flush_also_cannot_retype_raises_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-5: the raise when both retry stages fail, message pinned."""
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        staticmethod(lambda _name, _parent: _WrongTypedWrapper()),
    )
    monkeypatch.setattr(wx, "SafeYield", lambda: None)
    monkeypatch.setattr(
        wx.EventLoopBase,
        "GetActive",
        lambda: types.SimpleNamespace(
            YieldFor=lambda _category: None,
            ProcessIdle=lambda: False,
        ),
    )

    with pytest.raises(
        LookupError,
        match=re.escape("test_window has no control named 'control_name'"),
    ):
        _support.find_control(_window(), "control_name", _ExpectedControl)


def test_find_control_idle_flush_is_bounded_when_processidle_never_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-settling ProcessIdle stops at the bound, then raises."""
    idle_calls: list[bool] = []
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        staticmethod(lambda _name, _parent: _WrongTypedWrapper()),
    )
    monkeypatch.setattr(wx, "SafeYield", lambda: None)
    monkeypatch.setattr(
        wx.EventLoopBase,
        "GetActive",
        lambda: types.SimpleNamespace(
            YieldFor=lambda _category: None,
            ProcessIdle=lambda: idle_calls.append(True) or True,
        ),
    )

    with pytest.raises(
        LookupError,
        match=re.escape("test_window has no control named 'control_name'"),
    ):
        _support.find_control(_window(), "control_name", _ExpectedControl)

    assert len(idle_calls) == _support.FIND_IDLE_FLUSH_ATTEMPTS + 1
