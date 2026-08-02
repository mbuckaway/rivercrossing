# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for the card imagelist (E1.3.2).

Everything here needs a live ``wx.App``: ``wx.Bitmap`` decodes the
PNGs and ``wx.ImageList`` is the object spec.md section 15b puts on
the code side of the XRC boundary. The headless half -- the
stored-code mapping, the file inventory and the startup validation
as a pure filesystem check -- is ``tests/unit/test_cards_imagelist``.

The ``wx.App`` is created once and kept alive by a module-level
cache: an unbound ``wx.App()`` is collected as soon as the fixture
that built it goes out of scope, and the interpreter then hangs at
exit.
"""

import re
from functools import cache
from pathlib import Path  # noqa: TC003 -- pytest reads fixture annotations at runtime
from typing import Any

import pytest

from rivercrossing.ui import require_wx
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
    cards_dir,
    load_card_image_list,
)

pytestmark = pytest.mark.functional

NON_JOKER_KEYS = tuple(key for key in CARD_KEYS if key != JOKER_KEY)

SIZE_CASES = tuple((scale, key) for scale in SCALES for key in CARD_KEYS)

CODE_CASES = (
    (SCALE_1X, "AS", "As"),
    (SCALE_1X, "TD", "10d"),
    (SCALE_1X, JOKER_CODE, JOKER_KEY),
    (SCALE_2X, "2C", "2c"),
    (SCALE_2X, JOKER_CODE, JOKER_KEY),
)


@cache
def _wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return the process-wide wx.App, creating it on first use.

    ``functools.cache`` holds the only strong reference, which is
    the point: the app must outlive every fixture scope.
    """
    wx = require_wx()
    return wx.GetApp() or wx.App()


@pytest.fixture(scope="session")
def wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Guarantee a live wx.App before any bitmap is decoded."""
    return _wx_app()


@pytest.fixture(scope="module")
def image_lists(wx_app: Any) -> dict[str, CardImageList]:  # noqa: ANN401, ARG001
    """Load the packaged deck once, at both scales.

    Takes ``wx_app`` for ordering only: the app has to exist
    before the first ``wx.Bitmap`` is decoded.
    """
    return {scale: load_card_image_list(scale) for scale in SCALES}


def _stocked_dir(target: Path) -> Path:
    """Copy the packaged card bitmaps into *target* and return it."""
    target.mkdir(parents=True, exist_ok=True)
    names = [asset_filename(key, scale) for scale in SCALES for key in CARD_KEYS]
    for name in names:
        target.joinpath(name).write_bytes((cards_dir() / name).read_bytes())
    return target


def _pixels(bitmap: Any) -> bytes:  # noqa: ANN401 -- wx ships no stubs
    """Return one bitmap's raw RGB bytes, for identity comparisons."""
    return bytes(bitmap.ConvertToImage().GetData())


# --- 53 keys at each scale ---


@pytest.mark.parametrize("scale", SCALES)
def test_card_image_list_exposes_fifty_three_keys(
    image_lists: dict[str, CardImageList], scale: str
) -> None:
    """52 faces plus the joker, loaded at this scale."""
    assert len(image_lists[scale].keys) == 53


@pytest.mark.parametrize("scale", SCALES)
def test_card_image_list_key_set_is_the_frozen_deck(
    image_lists: dict[str, CardImageList], scale: str
) -> None:
    """No key is dropped or invented while loading."""
    assert image_lists[scale].keys == frozenset(CARD_KEYS)


@pytest.mark.parametrize("scale", SCALES)
def test_card_image_list_holds_fifty_three_images(
    image_lists: dict[str, CardImageList], scale: str
) -> None:
    """Every key made it into the wx.ImageList itself."""
    assert image_lists[scale].image_list.GetImageCount() == 53


@pytest.mark.parametrize("scale", SCALES)
def test_card_image_list_includes_the_joker(
    image_lists: dict[str, CardImageList], scale: str
) -> None:
    """Jokers are wild (spec.md section 5) and must render."""
    assert JOKER_KEY in image_lists[scale].keys


# --- the joker is its own face ---


@pytest.mark.parametrize("key", NON_JOKER_KEYS)
def test_card_image_list_joker_bitmap_differs_from_every_face(
    image_lists: dict[str, CardImageList], key: str
) -> None:
    """A joker that rendered as a 9 of hearts would misreport a hand."""
    loaded = image_lists[SCALE_1X]

    assert _pixels(loaded.bitmap(JOKER_KEY)) != _pixels(loaded.bitmap(key))


def test_card_image_list_gives_every_key_a_distinct_index(
    image_lists: dict[str, CardImageList],
) -> None:
    """53 keys, 53 different rows in the imagelist."""
    indexes = [image_lists[SCALE_1X].index_of(key) for key in CARD_KEYS]

    assert sorted(indexes) == list(range(53))


# --- measured bitmap sizes ---


@pytest.mark.parametrize(("scale", "key"), SIZE_CASES)
def test_card_image_list_bitmap_measures_the_documented_size(
    image_lists: dict[str, CardImageList], scale: str, key: str
) -> None:
    """24x32 at 1x, 48x64 at 2x -- read off the decoded bitmap."""
    bitmap = image_lists[scale].bitmap(key)

    assert (bitmap.GetWidth(), bitmap.GetHeight()) == BITMAP_SIZES[scale]


@pytest.mark.parametrize("scale", SCALES)
def test_card_image_list_declares_its_scale_to_the_toolkit(
    image_lists: dict[str, CardImageList], scale: str
) -> None:
    """wx.ImageList is fixed-size; it must agree with the bitmaps."""
    size = image_lists[scale].image_list.GetSize(0)

    assert (size[0], size[1]) == BITMAP_SIZES[scale]


# --- lookup by stored card code ---


@pytest.mark.parametrize("case", CODE_CASES)
def test_card_image_list_index_for_code_matches_the_mapped_key(
    image_lists: dict[str, CardImageList], case: tuple[str, str, str]
) -> None:
    """The view holds Card.code(); the imagelist speaks asset keys."""
    scale, code, key = case

    loaded = image_lists[scale]

    assert loaded.index_for_code(code) == loaded.index_of(key)


def test_card_image_list_index_for_unmappable_code_raises(
    image_lists: dict[str, CardImageList],
) -> None:
    """A bad code is refused rather than drawn as some other card."""
    with pytest.raises(UnknownCardCodeError, match=re.escape("'ZZ'")):
        image_lists[SCALE_1X].index_for_code("ZZ")


# --- a missing asset stops startup ---


@pytest.mark.usefixtures("wx_app")
def test_load_card_image_list_with_a_missing_card_raises_naming_the_file(
    tmp_path: Path,
) -> None:
    """The bundle is checked before the first wx.Bitmap is built."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("Kh.png").unlink()

    with pytest.raises(MissingCardAssetError, match=re.escape("Kh.png")):
        load_card_image_list(SCALE_1X, stocked)


@pytest.mark.usefixtures("wx_app")
def test_load_card_image_list_at_one_x_still_rejects_a_missing_two_x_card(
    tmp_path: Path,
) -> None:
    """A window can move to a Retina display mid-ride: both ship."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("Kh-2x.png").unlink()

    with pytest.raises(MissingCardAssetError, match=re.escape("Kh-2x.png")):
        load_card_image_list(SCALE_1X, stocked)


@pytest.mark.usefixtures("wx_app")
def test_load_card_image_list_with_an_unreadable_card_raises_naming_the_file(
    tmp_path: Path,
) -> None:
    """A truncated PNG is a packaging failure, not a blank cell."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("Kh.png").write_bytes(b"not a png")

    with pytest.raises(MissingCardAssetError, match=re.escape("Kh.png")):
        load_card_image_list(SCALE_1X, stocked)


@pytest.mark.usefixtures("wx_app")
def test_load_card_image_list_with_a_stray_contact_sheet_loads_fifty_three(
    tmp_path: Path,
) -> None:
    """An extra file in the directory never becomes a 54th image."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("contact-sheet.png").write_bytes(b"not a card")

    loaded = load_card_image_list(SCALE_1X, stocked)

    assert loaded.image_list.GetImageCount() == 53
