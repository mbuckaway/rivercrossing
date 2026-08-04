# SPDX-License-Identifier: GPL-3.0-only
"""Honesty tests for the committed branding artifacts (Phase 8, 8.7.4).

Pins that ``installers/branding/`` really carries what P8-D5 promises:
the two SVG masters, the generated ``.icns``/``.ico``/``.tiff``, and
never a ``.png`` (the GitLab hard rule). Pure Pillow and ``struct``
here -- no wx, no ``tools/gen_app_icons.py`` import, so a regeneration
bug in the generator can't also hide the bug in its own tests.
"""

import struct
from pathlib import Path

from PIL import Image

BRANDING_DIR = Path(__file__).resolve().parents[2] / "installers" / "branding"
ICON_SVG_PATH = BRANDING_DIR / "svg" / "icon.svg"
BACKGROUND_SVG_PATH = BRANDING_DIR / "svg" / "dmg_background.svg"
ICNS_PATH = BRANDING_DIR / "RiverCrossing.icns"
ICO_PATH = BRANDING_DIR / "rivercrossing.ico"
BACKGROUND_TIFF_PATH = BRANDING_DIR / "dmg_background.tiff"

# Apple Icon Image type tags for the ten .iconset representations
# (ic04/ic05 are the 16x16 and 32x32 1x icons, stored as raw ARGB;
# the rest are PNG). Cross-checked two ways: (1) the public icns
# format table (Wikipedia "Apple Icon Image format", libicns), and
# (2) directly decoding the actual generated file's payloads with
# Pillow -- 8 of the 10 payloads open as a PNG at exactly the pixel
# size their tag implies (ic11 -> 32x32, ic12 -> 64x64, ic07 ->
# 128x128, ic13/ic08 -> 256x256, ic14/ic09 -> 512x512, ic10 ->
# 1024x1024); ic04/ic05 fail to decode as PNG (they are the raw
# format) and are exactly the two entries Pillow's own reader drops.
EXPECTED_ICNS_TYPE_TAGS = frozenset(
    {b"ic04", b"ic05", b"ic07", b"ic08", b"ic09", b"ic10", b"ic11", b"ic12", b"ic13", b"ic14"}
)

EXPECTED_ICO_SIZES = frozenset({(16, 16), (32, 32), (48, 48), (256, 256)})


def _icns_type_tags(path: Path) -> frozenset[bytes]:
    """Return every top-level entry type tag in an .icns file.

    Measured gap this works around: Pillow's ``IcnsImagePlugin``
    silently drops ``ic04``/``ic05`` -- the two smallest
    representations, stored as raw ARGB rather than PNG -- from
    ``Image.open(...).info["sizes"]``. Verified against the real
    generated file on this machine (Pillow 12.3.0, macOS
    ``iconutil``): ``info["sizes"]`` returned 8 of the 10 expected
    tuples, missing exactly ``(16, 16, 1)`` and ``(32, 32, 1)``. This
    reads the icns container directly instead (Apple Icon Image
    format: 4-byte magic ``b"icns"``, 4-byte big-endian total
    length, then repeated 4-byte type tag + 4-byte entry length +
    payload), so the test asserts on what the file actually
    contains rather than what one library's decoder surfaces.
    """
    data = path.read_bytes()
    magic, total_length = struct.unpack(">4sI", data[:8])
    if magic != b"icns":
        msg = f"not an icns file: {path}"
        raise ValueError(msg)
    offset = 8
    tags = []
    while offset < total_length:
        type_tag, entry_length = struct.unpack(">4sI", data[offset : offset + 8])
        tags.append(type_tag)
        offset += entry_length
    return frozenset(tags)


def _seek_and_size(img: Image.Image, index: int) -> tuple[int, int]:
    """Seek *img* to *index* and return its pixel size there."""
    img.seek(index)
    return img.size


def _tiff_page_sizes(path: Path) -> tuple[tuple[int, int], ...]:
    """Return every page's (width, height) in a multi-page TIFF."""
    with Image.open(path) as img:
        return tuple(_seek_and_size(img, index) for index in range(img.n_frames))


def test_branding_svg_sources_are_committed() -> None:
    """Both SVG masters are tracked source, not generated output."""
    assert ICON_SVG_PATH.exists()
    assert BACKGROUND_SVG_PATH.exists()


def test_committed_icns_parses_and_carries_all_ten_representations() -> None:
    """The committed .icns contains all ten Apple representations.

    Pillow's ``info["sizes"]`` cannot stand in here -- see
    ``_icns_type_tags``'s docstring for the measured gap; this reads
    the container format directly instead.
    """
    type_tags = _icns_type_tags(ICNS_PATH)

    assert type_tags >= EXPECTED_ICNS_TYPE_TAGS


def test_committed_ico_parses_and_carries_the_expected_sizes() -> None:
    """The committed .ico embeds at least the four pinned sizes."""
    with Image.open(ICO_PATH) as ico:
        sizes = set(ico.info["sizes"])

    assert sizes >= EXPECTED_ICO_SIZES


def test_committed_background_tiff_carries_a_one_x_and_a_two_x_page() -> None:
    """The committed background TIFF has exactly a 1x and a 2x page."""
    page_sizes = _tiff_page_sizes(BACKGROUND_TIFF_PATH)

    assert len(page_sizes) == 2
    assert set(page_sizes) == {(660, 400), (1320, 800)}


def test_no_png_is_committed_under_installers_branding() -> None:
    """No .png file exists anywhere under installers/branding (GitLab)."""
    png_paths = list(BRANDING_DIR.rglob("*.png"))

    assert png_paths == []
