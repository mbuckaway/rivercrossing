# SPDX-License-Identifier: GPL-3.0-only
"""The 53-card imagelist and its bitmaps (E1.3.2).

spec.md section 15b puts the card imagelist on the code side of the
XRC boundary: XRC declares the ``wxDataViewCtrl`` shell, code
populates its columns and the bitmaps they draw. This module is
that code -- it owns ``ui/assets/cards/`` and turns it into a
populated ``wx.ImageList``.

Two things are worth knowing before reading further.

**Two naming conventions meet here.** ``Card.code()`` (see
module-skeletons.md S4) returns the *stored* form -- ``"AS"``,
``"TD"``, ``"JK"``: uppercase, with ``T`` for ten. The bitmap files
use the *asset* form -- ``As.png``, ``10d.png``, ``joker.png``:
mixed case, with ``10`` spelled out. :func:`asset_key` is the only
bridge between them, and it is a pure function so it can be tested
without a display. ``rivercrossing.cards`` does not exist yet (it
lands in EPIC 2, E2.2.1), so the parameter is typed ``str``; it
will accept ``Card.code()`` output unchanged when that module
arrives.

**A missing bitmap is a startup failure.** Every loader path runs
:func:`verify_card_assets` over all 106 files -- both scales, not
just the one being loaded -- before a single ``wx.Bitmap`` is
built. A bundle that shipped without its cards then dies while the
app is starting instead of drawing a blank cell in the middle of a
race.
"""

from pathlib import Path
from typing import Any

from rivercrossing.ui import require_wx

__all__ = [
    "ASSET_RANKS",
    "ASSET_SUITS",
    "BITMAP_SIZES",
    "CARD_KEYS",
    "JOKER_CODE",
    "JOKER_KEY",
    "SCALES",
    "SCALE_1X",
    "SCALE_2X",
    "CardImageList",
    "MissingCardAssetError",
    "UnknownCardCodeError",
    "asset_filename",
    "asset_key",
    "card_asset_paths",
    "cards_dir",
    "load_card_image_list",
    "preferred_scale",
    "verify_card_assets",
]

SCALE_1X = "1x"
SCALE_2X = "2x"
SCALES = (SCALE_1X, SCALE_2X)

# design/README.md, assets/cards: 24x32 plus a 48x64 "-2x" variant.
BITMAP_SIZES = {SCALE_1X: (24, 32), SCALE_2X: (48, 64)}

# Filename stems, not display text: ten is "10" and suits are lower
# case, which is what design/assets/cards/ ships.
ASSET_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
ASSET_SUITS = ("c", "d", "h", "s")

JOKER_KEY = "joker"
JOKER_CODE = "JK"

CARD_KEYS = (
    *(f"{rank}{suit}" for rank in ASSET_RANKS for suit in ASSET_SUITS),
    JOKER_KEY,
)

# Stored rank characters map onto asset rank tokens one for one --
# only ten differs, so the table is derived rather than retyped.
_RANK_BY_CODE = {("T" if rank == "10" else rank): rank for rank in ASSET_RANKS}
_SUIT_BY_CODE = {suit.upper(): suit for suit in ASSET_SUITS}


class UnknownCardCodeError(ValueError):
    """Raised when a card code has no bitmap behind it."""


class MissingCardAssetError(FileNotFoundError):
    """Raised when a card bitmap is absent or cannot be decoded.

    Subclasses ``FileNotFoundError`` so a bundle-integrity failure
    reads as what it is: a file that should have been packaged.
    """


def cards_dir() -> Path:
    """Return the packaged card-bitmap directory."""
    return Path(__file__).resolve().parent / "assets" / "cards"


def asset_key(code: str) -> str:
    """Return the asset key for a stored card code.

    Args:
        code: The stored form -- ``"AS"``, ``"TD"``, ``"JK"``. This
            is what ``Card.code()`` will return once
            ``rivercrossing.cards`` lands (E2.2.1).

    Returns:
        The bitmap filename stem: ``"As"``, ``"10d"``, ``"joker"``.

    Raises:
        UnknownCardCodeError: If *code* names no card in the deck.
    """
    if code == JOKER_CODE:
        return JOKER_KEY
    # Slices rather than indexes: a code of any other length lands
    # on "" or a multi-character key, neither of which is in a map.
    rank = _RANK_BY_CODE.get(code[:1])
    suit = _SUIT_BY_CODE.get(code[1:])
    if rank is None or suit is None:
        raise UnknownCardCodeError(f"unknown card code {code!r}")
    return f"{rank}{suit}"


def asset_filename(key: str, scale: str) -> str:
    """Return the bitmap filename for *key* at *scale*.

    Raises:
        ValueError: If *scale* is not one of :data:`SCALES`.
    """
    if scale not in BITMAP_SIZES:
        raise ValueError(f"unknown card bitmap scale {scale!r}")
    suffix = "" if scale == SCALE_1X else f"-{scale}"
    return f"{key}{suffix}.png"


def _every_asset_filename() -> list[str]:
    """List all 106 filenames the package is expected to ship."""
    return [asset_filename(key, scale) for scale in SCALES for key in CARD_KEYS]


def verify_card_assets(directory: Path) -> None:
    """Assert *directory* holds every card bitmap, at both scales.

    Both scales are checked whichever one is about to be loaded: a
    window can move onto a Retina display after startup, and the
    2x set has to be there when it does.

    Raises:
        MissingCardAssetError: Naming every absent file, so one run
            reports the whole shortfall rather than the first gap.
    """
    missing = [name for name in _every_asset_filename() if not (directory / name).is_file()]
    if missing:
        raise MissingCardAssetError(f"card bitmaps missing from {directory}: {', '.join(missing)}")


def card_asset_paths(directory: Path, scale: str) -> dict[str, Path]:
    """Map every card key to its bitmap in *directory* at *scale*.

    Verifies the whole set first, so no caller can skip the check.
    Files in *directory* that are not part of the deck -- the
    ``contact-sheet.png`` that ships beside the design originals,
    for one -- are never members: the map is built from
    :data:`CARD_KEYS`, not from a directory listing.
    """
    verify_card_assets(directory)
    return {key: directory / asset_filename(key, scale) for key in CARD_KEYS}


def preferred_scale(content_scale_factor: float) -> str:
    """Return the scale to draw with on a display of this density.

    ``wx.Window.GetContentScaleFactor()`` reports 1.0 on a plain
    display and 2.0 on a Retina one; Windows reports fractional
    values such as 1.25. Anything above 1.0 would upscale the
    24x32 face, so the 48x64 is used from there on.
    """
    return SCALE_2X if content_scale_factor > 1.0 else SCALE_1X


def _load_bitmap(wx: Any, path: Path) -> Any:  # noqa: ANN401 -- wx ships no stubs
    """Decode one PNG, failing loudly rather than drawing nothing.

    A failed decode is reported by wxWidgets through its logging
    system, which pops a dialog once a main loop is running.
    ``wx.LogNull`` keeps that out of a startup crash; the raised
    error carries the path instead.
    """
    with wx.LogNull():
        bitmap = wx.Bitmap(str(path), wx.BITMAP_TYPE_PNG)
    if not bitmap.IsOk():
        raise MissingCardAssetError(f"card bitmap could not be decoded: {path}")
    return bitmap


class CardImageList:
    """The 53 card faces at one scale, as a ``wx.ImageList``.

    Attributes:
        scale: The scale key this list was built at.
        directory: Where its bitmaps were read from.
        image_list: The populated ``wx.ImageList``, ready to hand
            to a ``wxDataViewCtrl`` column.
        keys: The 53 asset keys it holds.
    """

    def __init__(self, scale: str, directory: Path | None = None) -> None:
        """Load every card bitmap at *scale* into a new imagelist.

        Raises:
            MissingCardAssetError: If any of the 106 files is
                absent or undecodable.
            ValueError: If *scale* is not one of :data:`SCALES`.
        """
        wx = require_wx()
        self.scale = scale
        self.directory = cards_dir() if directory is None else directory
        paths = card_asset_paths(self.directory, scale)
        width, height = BITMAP_SIZES[scale]
        self.image_list: Any = wx.ImageList(width, height)
        self._bitmaps: dict[str, Any] = {
            key: _load_bitmap(wx, path) for key, path in paths.items()
        }
        self._indexes: dict[str, int] = {
            key: int(self.image_list.Add(self._bitmaps[key])) for key in CARD_KEYS
        }

    @property
    def keys(self) -> frozenset[str]:
        """Return the asset keys this imagelist holds."""
        return frozenset(self._indexes)

    def index_of(self, key: str) -> int:
        """Return the imagelist index of the bitmap for *key*."""
        return self._indexes[key]

    def bitmap(self, key: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the ``wx.Bitmap`` loaded for *key*."""
        return self._bitmaps[key]

    def index_for_code(self, code: str) -> int:
        """Return the imagelist index for a stored card code.

        Raises:
            UnknownCardCodeError: If *code* names no card.
        """
        return self._indexes[asset_key(code)]


def load_card_image_list(scale: str = SCALE_1X, directory: Path | None = None) -> CardImageList:
    """Build the card imagelist, verifying the bundle first."""
    return CardImageList(scale, directory)
