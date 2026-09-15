# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the shared view helpers (``ui.views._support``).

``_find`` and the card-imagelist cache this module now hosts are
already exercised end to end by every other view's own tests --
each view calls them through the ``_find`` it inherits from
:class:`DialogFindMixin`, so a construction failure there would show
up as a failure in that view's own tests, not silently. The mixin's
window-attribute contract and :func:`associate_model` (unverified
repaint remedy, see its own docstring) are new behaviour, so this
module pins both in isolation, with no window required.

:func:`find_window_by_name` and :func:`find_control` are pinned here
too, over a headless window double rather than a real one: the
recursive ``GetChildren()`` walk replaces
``wx.Window.FindWindowByName``, which on Windows ARM64 (wxPython
4.3.1) fails to resolve children that are present. The double lets
the walk, the settle retry and the exact ``LookupError`` text be
asserted anywhere, and no test needs a desktop to do it.
"""

import re
from unittest.mock import MagicMock, call

import pytest
import wx

from rivercrossing.ui.views import _support


class _DialogView(_support.DialogFindMixin):
    """A view whose loaded XRC window is its own ``dialog``."""

    def __init__(self, dialog: object) -> None:
        """Hold the window to resolve inside."""
        self.dialog = dialog


class _FrameView(_support.DialogFindMixin):
    """A view that resolves inside a ``frame``, as the console does."""

    _window_attr = "frame"

    def __init__(self, frame: object) -> None:
        """Hold the window to resolve inside."""
        self.frame = frame


def test_associate_model_associates_then_refreshes_then_updates() -> None:
    """The exact order this unverified repaint remedy depends on."""
    control = MagicMock()
    model = MagicMock()

    _support.associate_model(control, model)

    assert control.mock_calls == [call.AssociateModel(model), call.Refresh(), call.Update()]


# --------------------------------------- DialogFindMixin's window attr


_MIXIN_CASES = (
    pytest.param(_DialogView, id="dialog"),
    pytest.param(_FrameView, id="frame"),
)


@pytest.mark.parametrize("view_class", _MIXIN_CASES)
def test_dialog_find_mixin_resolves_inside_its_own_window_attribute(
    monkeypatch: pytest.MonkeyPatch, view_class: type
) -> None:
    """The attribute contract: dialog by default, frame overridden.

    Every view's inherited ``_find`` forwards to
    :func:`find_control` with its own loaded window, so the one thing
    the mixin adds over the copies it replaces -- which attribute
    holds that window -- is pinned here for both names.
    """
    window = object()
    view = view_class(window)
    monkeypatch.setattr(_support, "find_control", lambda window, name, kind: (window, name, kind))

    found = view._find("plate_input", str)

    assert found == (window, "plate_input", str)


# ------------------------------ find_window_by_name / find_control


class _FakeWindow:
    """A headless stand-in for a wx window: a name plus its children.

    ``GetName``/``GetChildren`` are the whole interface the control
    lookup uses, and ``isinstance`` against this class stands in for
    the ``wx.Window`` subclass check ``find_control`` applies.
    """

    def __init__(self, name: str, *children: object) -> None:
        """Name this window and hold its direct children."""
        self._name = name
        self._children = list(children)

    def GetName(self) -> str:  # noqa: N802 -- wx API name
        """Return this window's frozen control name."""
        return self._name

    def GetChildren(self) -> list[object]:  # noqa: N802 -- wx API name
        """Return this window's direct children."""
        return self._children


def _refuse_lookup(*_args: object, **_kwargs: object) -> None:
    """Fail the test if anything consults the wx name lookup."""
    pytest.fail("the control lookup must not call wx.Window.FindWindowByName")


def test_find_window_by_name_returns_a_first_level_child_wrapper() -> None:
    """A direct-child match returns that child wrapper, not a copy."""
    target = _FakeWindow("plate_input")
    window = _FakeWindow("main_frame", _FakeWindow("crossings_list"), target)

    assert _support.find_window_by_name(window, "plate_input") is target


def test_find_window_by_name_returns_a_nested_descendant_wrapper() -> None:
    """A descendant below a first-level panel still resolves."""
    target = _FakeWindow("finish_first_btn")
    window = _FakeWindow(
        "resume_dlg",
        _FakeWindow("panel", _FakeWindow("message_lbl"), target),
    )

    assert _support.find_window_by_name(window, "finish_first_btn") is target


def test_find_window_by_name_returns_none_for_a_name_no_descendant_carries() -> None:
    """A name the tree does not carry answers None, not a raise."""
    window = _FakeWindow("main_frame", _FakeWindow("panel", _FakeWindow("plate_input")))

    assert _support.find_window_by_name(window, "record_btn") is None


@pytest.mark.parametrize(
    "child_names",
    [(), ("plate_input",), ("plate_input", "record_btn", "finish_first_btn")],
)
def test_find_window_by_name_returns_none_for_zero_one_or_many_children(
    child_names: tuple[str, ...],
) -> None:
    """T-4: zero, one and many children all answer None for a miss."""
    window = _FakeWindow("main_frame", *[_FakeWindow(name) for name in child_names])

    assert _support.find_window_by_name(window, "undo_btn") is None


def test_find_control_resolves_a_present_child_without_the_wx_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows-ARM64 fix: the walk finds the present child.

    On Windows ARM64 (wxPython 4.3.1) ``wx.Window.FindWindowByName``
    answers ``None`` for children that are present; the scoped
    recursive walk resolves them. The wx lookup is patched to fail
    loudly, so this passes only while ``find_control`` never consults
    it.
    """
    monkeypatch.setattr(wx.Window, "FindWindowByName", _refuse_lookup)
    plate_input = _FakeWindow("plate_input")
    window = _FakeWindow("main_frame", _FakeWindow("panel"), plate_input)

    assert _support.find_control(window, "plate_input", _FakeWindow) is plate_input


@pytest.mark.parametrize(
    "child_names",
    [(), ("plate_input",), ("plate_input", "record_btn", "finish_first_btn")],
)
def test_find_control_raises_lookup_error_naming_the_first_level_children(
    child_names: tuple[str, ...],
) -> None:
    """The raised text is pinned: window, name, child count and names.

    A whole-subtree load gap has to read differently from one
    genuinely missing control, so the first-level child inventory is
    part of the message.
    """
    window = _FakeWindow("main_frame", *[_FakeWindow(name) for name in child_names])
    children = list(child_names)

    with pytest.raises(
        LookupError,
        match=re.escape(
            f"main_frame has no control named 'undo_btn' "
            f"(first-level children: {len(children)} -- {children!r})"
        ),
    ):
        _support.find_control(window, "undo_btn", _FakeWindow)


def test_find_control_re_walks_the_tree_on_each_settle_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transiently wrong wrapper answers the next walk, three in."""
    window = _FakeWindow("main_frame")
    settled = _FakeWindow("plate_input")
    lookups = 0

    def _walk(_window: object, _name: str) -> object:
        nonlocal lookups
        lookups += 1
        return object() if lookups < 3 else settled

    monkeypatch.setattr(_support, "find_window_by_name", _walk)

    assert _support.find_control(window, "plate_input", _FakeWindow) is settled
    assert lookups == 3
