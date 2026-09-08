# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the wx half of ``ui.cards_imagelist`` (E1.3.2).

The headless half -- the stored-code mapping, the file inventory and
the startup validation as a pure filesystem check -- lives in
``tests/unit/test_cards_imagelist.py`` and never imports wx. This
module pins the real-toolkit half: :class:`CardImageList` decoding
the packaged PNGs into a ``wx.ImageList``.

This file is deliberately NOT named ``test_cards_imagelist.py``:
pytest's default (prepend) import mode imports every non-package
test module by its bare basename, and the pre-existing
``tests/unit/test_cards_imagelist.py`` already owns that name -- a
same-basename sibling here would abort collection of the whole
``tests/unit`` tree with an import-file mismatch. The ``_wx`` suffix
mirrors the functional suite's own split file
(``tests/functional/test_cards_imagelist_wx.py``), which runs in a
separate pytest process and shares no import namespace with this
module.

``wx.Bitmap`` decoding and ``wx.ImageList`` construction need a live
``wx.App``, so this module builds one locally -- a module-cache
strong reference, because an unbound ``wx.App()`` is collected as
soon as the fixture that built it goes out of scope and the
interpreter then hangs at exit (the measured pattern
``tests/functional/conftest.py`` documents; that session fixture is
not available to the unit process, so the pattern is reproduced
here). No window is ever created, so the unit process never takes
over a desktop.

The spawned-subprocess scenarios in
``tests/functional/test_cards_imagelist_wx.py`` additionally prove
pixel identity and the Retina 2x set; this module keeps the proof to
keys, indexes, bitmap validity, the decode-failure path and the
scale-validation error.
"""

import re
from functools import cache
from typing import TYPE_CHECKING, Any

import pytest
import wx

from rivercrossing.ui.cards_imagelist import (
    BITMAP_SIZES,
    CARD_KEYS,
    JOKER_CODE,
    JOKER_KEY,
    SCALE_1X,
    SCALE_2X,
    SCALES,
    CardImageList,
    MissingCardAssetError,
    UnknownCardCodeError,
    asset_filename,
    load_card_image_list,
)

if TYPE_CHECKING:
    from pathlib import Path

INDEX_FOR_CODE_CASES = (
    ("AS", "As"),
    ("TD", "10d"),
    ("2C", "2c"),
    (JOKER_CODE, JOKER_KEY),
)


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


@pytest.fixture(scope="module")
def image_list_1x(wx_app: Any) -> CardImageList:  # noqa: ANN401, ARG001 -- wx ships no stubs
    """Load the packaged 1x deck once, shared by every lookup test.

    Takes ``wx_app`` for ordering only: the app must exist before the
    first ``wx.Bitmap`` is decoded.
    """
    return load_card_image_list(SCALE_1X)


def test_card_image_list_at_1x_loads_the_full_deck(wx_app: Any) -> None:  # noqa: ANN401, ARG001
    """Every deck key is present with a decodable 24x32 bitmap."""
    image_list = load_card_image_list(SCALE_1X)

    assert image_list.keys == frozenset(CARD_KEYS)
    assert image_list.image_list.GetImageCount() == 53
    bitmap = image_list.bitmap("As")
    assert bitmap.IsOk()
    assert (bitmap.GetWidth(), bitmap.GetHeight()) == BITMAP_SIZES[SCALE_1X]


def test_card_image_list_at_2x_loads_the_retina_size_bitmaps(wx_app: Any) -> None:  # noqa: ANN401, ARG001
    """The Retina set decodes at 48x64 with the same 53 keys."""
    image_list = CardImageList(SCALE_2X)

    assert image_list.keys == frozenset(CARD_KEYS)
    assert image_list.image_list.GetImageCount() == 53
    bitmap = image_list.bitmap(JOKER_KEY)
    assert bitmap.IsOk()
    assert (bitmap.GetWidth(), bitmap.GetHeight()) == BITMAP_SIZES[SCALE_2X]


@pytest.mark.parametrize(
    ("key", "expected_index"),
    [(key, index) for index, key in enumerate(CARD_KEYS)],
)
def test_card_image_list_index_of_given_each_declared_key_returns_its_position(
    image_list_1x: CardImageList, key: str, expected_index: int
) -> None:
    """The imagelist row for a key is its CARD_KEYS position."""
    assert image_list_1x.index_of(key) == expected_index


@pytest.mark.parametrize(("code", "key"), INDEX_FOR_CODE_CASES)
def test_card_image_list_index_for_code_given_a_stored_code_matches_its_key(
    image_list_1x: CardImageList, code: str, key: str
) -> None:
    """The view hands Card.code(); the imagelist speaks asset keys."""
    assert image_list_1x.index_for_code(code) == image_list_1x.index_of(key)


def test_card_image_list_index_for_code_given_an_unknown_code_raises_naming_it(
    image_list_1x: CardImageList,
) -> None:
    """A code outside the deck is refused, never another card."""
    with pytest.raises(UnknownCardCodeError, match=re.escape("'ZZ'")):
        image_list_1x.index_for_code("ZZ")


def test_card_image_list_given_an_undecodable_bitmap_raises_naming_the_failure(
    wx_app: Any,  # noqa: ANN401, ARG001
    tmp_path: Path,
) -> None:
    """A truncated PNG is a packaging failure, never a blank cell."""
    directory = tmp_path / "cards"
    directory.mkdir()
    for name in (asset_filename(key, scale) for scale in SCALES for key in CARD_KEYS):
        directory.joinpath(name).write_bytes(b"not a png")

    with pytest.raises(MissingCardAssetError, match=re.escape("could not be decoded")):
        CardImageList(SCALE_1X, directory)


def test_card_image_list_given_an_unknown_scale_raises_naming_it(wx_app: Any) -> None:  # noqa: ANN401, ARG001
    """A scale outside 1x/2x is refused before any image is built."""
    with pytest.raises(ValueError, match=re.escape("unknown card bitmap scale")):
        CardImageList("3x")
