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
"""

from unittest.mock import MagicMock, call

import pytest

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
