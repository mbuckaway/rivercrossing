# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the Add/Edit Team card preview (``_logo_bitmap``).

``AddTeamDialog.show_logo`` renders the staged team's card through
``team_editor._logo_bitmap``: a valid card code draws the packaged card
bitmap scaled into :data:`team_editor.CARD_LOGO_BOX`, and a ``None`` or
unmappable code draws a blank ``wx.NullBitmap``.

A regression made the valid-card arm raise ``TypeError``: the shared
``_scaled_bitmap`` built its ``wx.Image`` with ``wx.Image(bitmap)`` -- a
``wx.Bitmap`` argument, which wxPython 4.3.1 rejects. The wx event
handler swallowed the error, so only the Edit route (which passes a real
card) appeared dead, while Add's ``None`` short-circuited through the
blank arm. The bitmap arm needs a live ``wx.App`` to decode the
packaged PNGs, so this module builds one locally -- the module-cache
strong reference ``tests/unit/ui/test_cards_imagelist_wx.py`` uses,
because an unbound ``wx.App()`` is collected as soon as its fixture
goes out of scope and the interpreter then hangs at exit. No window is
ever created, so the unit process never takes over a desktop.
"""

from functools import cache
from typing import Any

import pytest
import wx

from rivercrossing.ui.views import team_editor

# A dealt code the packaged deck resolves ("AS" -> the "As" asset).
VALID_CARD = "AS"

# The blank-card seam: no code at all, a present-but-empty code and an
# unknown code all render the null bitmap.
BLANK_CARD_CASES: tuple[str | None, ...] = (None, "", "ZZ")


@cache
def _process_wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return the module's one wx.App, creating it on first use.

    Reuses an app another unit module already created (``wx.GetApp()``
    is non-None then), so collection order cannot force a second
    ``wx.App`` construction in one process.
    """
    return wx.GetApp() or wx.App(redirect=False)


@pytest.fixture(scope="module")
def wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Guarantee a live wx.App before any wx object is constructed."""
    return _process_wx_app()


def test_team_editor_logo_bitmap_given_a_valid_card_returns_an_ok_bitmap(
    wx_app: Any,  # noqa: ANN401, ARG001 -- wx ships no stubs
) -> None:
    """A team's card scales into the card box as a decodable bitmap."""
    bitmap = team_editor._logo_bitmap(VALID_CARD)

    assert bitmap.IsOk()
    assert (bitmap.GetWidth(), bitmap.GetHeight()) == team_editor.CARD_LOGO_BOX


@pytest.mark.parametrize("card", BLANK_CARD_CASES)
def test_team_editor_logo_bitmap_given_no_mappable_card_returns_a_null_bitmap(
    wx_app: Any,  # noqa: ANN401, ARG001 -- wx ships no stubs
    card: str | None,
) -> None:
    """No code, an empty code and an unknown code all render blank."""
    bitmap = team_editor._logo_bitmap(card)

    assert not bitmap.IsOk()


def test_scaled_bitmap_given_a_bitmap_within_the_box_returns_it_unchanged(
    wx_app: Any,  # noqa: ANN401, ARG001 -- wx ships no stubs
) -> None:
    """A bitmap already inside the box comes back unchanged."""
    source = team_editor.default_card_images().bitmap("As")

    result = team_editor._scaled_bitmap(source, within=(96, 128), upscale=False)

    assert result is source
