# SPDX-License-Identifier: GPL-3.0-only
"""Draw the 53 card bitmaps at 1x and 2x (task brief E1.3.2).

Writes ``{rank}{suit}.png`` (24x32) and ``{rank}{suit}-2x.png``
(48x64) for the 52 faces, plus ``joker.png`` / ``joker-2x.png`` --
the naming the imagelist loader and the packaging manifest both
depend on. The ``contact-sheet.png`` that sits beside the design
originals is documentation and is deliberately not produced here.

Palette (design/templates/theme.css, "mono steel ... no red"):
clubs and spades take ``--color-ink``, hearts, diamonds and the
joker take ``--color-steel-700``, and the joker's frame is
``--color-steel-600``. Every colour in the deck satisfies
r <= g <= b, which is the checkable form of "no red".

Faces are drawn at 4x and area-averaged down, so the small bitmaps
get real antialiasing rather than hinted stair-steps. The
reduction is BOX rather than LANCZOS on purpose: LANCZOS rings,
and its overshoot puts pixels a shade or two outside the palette,
breaking r <= g <= b. An area average is a convex blend of the
colours actually drawn, so it cannot. Suit pips are polygons
rather than glyphs -- the card-suit codepoints are missing from
most sans faces, and drawing them keeps the output identical on
macOS and Windows. Rank text uses the Aileron face bundled inside
Pillow, for the same reason.

Usage::

    python tools/gen_card_bitmaps.py            # rewrite the deck
    python tools/gen_card_bitmaps.py --out DIR  # draw elsewhere

This redraws the deck from scratch. It is not a bit-for-bit
reproduction of the starter art in ``design/assets/cards/``, which
a different (browser) pipeline rasterized; it reproduces that
set's filenames, sizes, layout and palette. design/README.md:
"tasks E1.3.2 and E4.4.3 commit the generator scripts and may
regenerate them -- keep the file names".
"""

import argparse
import math
import sys
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "src" / "rivercrossing" / "ui" / "assets" / "cards"

RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUITS = ("c", "d", "h", "s")
JOKER = "joker"
ACCENT_SUITS = ("d", "h")

SCALE_SUFFIXES = {"": 1, "-2x": 2}
CARD_SIZE = (24, 32)

# theme.css: --color-ink, --color-steel-700, --color-steel-600.
WHITE = (255, 255, 255, 255)
INK = (29, 31, 32, 255)
ACCENT = (65, 97, 128, 255)
BORDER = (196, 198, 199, 255)
JOKER_BORDER = (89, 126, 163, 255)

SUPERSAMPLE = 4

# Layout in 1x card pixels. The rank sits in the upper half and the
# pip in the lower, both centred, as the starter art has them.
RANK_CENTRE = (12.0, 9.0)
PIP_CENTRE = (12.0, 22.0)
STAR_CENTRE = (12.0, 10.0)
LEGEND_CENTRE = (12.0, 23.0)

RANK_CAP_HEIGHT = 8.0
RANK_MAX_WIDTH = 11.0
PIP_SIZE = 8.0
STAR_SIZE = 12.0
LEGEND_CAP_HEIGHT = 5.5
LEGEND_MAX_WIDTH = 12.0

# Aileron's cap height as a fraction of its em, measured with
# FreeTypeFont.getbbox("H"); the drawing code works in cap heights.
CAP_HEIGHT_RATIO = 0.72

# Faux bold: Pillow's stroke grows the glyph by this many card
# pixels on each side, which brings Aileron Regular's stems up to
# the weight the starter art uses.
STROKE_PIXELS = 0.25

Point = tuple[float, float]
Polygon = list[Point]


class Spot(NamedTuple):
    """Where a drawn element sits, in supersampled device pixels."""

    x: float
    y: float
    size: float


class Legend(NamedTuple):
    """A run of text, in supersampled device pixels."""

    x: float
    y: float
    cap_height: float
    max_width: float


def _circle(spot: Spot) -> Polygon:
    """Return a circle of diameter ``spot.size`` as a polygon."""
    radius = spot.size / 2
    steps = 48
    return [
        (
            spot.x + radius * math.cos(2 * math.pi * step / steps),
            spot.y + radius * math.sin(2 * math.pi * step / steps),
        )
        for step in range(steps)
    ]


def _heart(spot: Spot) -> list[Polygon]:
    """Return a heart: two lobes over a downward triangle."""
    size = spot.size
    lobe = Spot(spot.x, spot.y, size * 0.52)
    return [
        _circle(lobe._replace(x=spot.x - size * 0.24, y=spot.y - size * 0.22)),
        _circle(lobe._replace(x=spot.x + size * 0.24, y=spot.y - size * 0.22)),
        [
            (spot.x - size * 0.50, spot.y - size * 0.17),
            (spot.x + size * 0.50, spot.y - size * 0.17),
            (spot.x, spot.y + size * 0.50),
        ],
    ]


def _turned(polygons: list[Polygon], pivot: Point) -> list[Polygon]:
    """Return *polygons* rotated a half turn about *pivot*."""
    px, py = pivot
    return [[(2 * px - x, 2 * py - y) for x, y in polygon] for polygon in polygons]


def _stem(spot: Spot) -> Polygon:
    """Return the flared stem shared by the spade and the club."""
    size = spot.size
    return [
        (spot.x - size * 0.06, spot.y + size * 0.06),
        (spot.x + size * 0.06, spot.y + size * 0.06),
        (spot.x + size * 0.26, spot.y + size * 0.50),
        (spot.x - size * 0.26, spot.y + size * 0.50),
    ]


def _spade(spot: Spot) -> list[Polygon]:
    """Return a spade: an upturned heart over a stem."""
    body = Spot(spot.x, spot.y - spot.size * 0.13, spot.size * 0.84)
    return [*_turned(_heart(body), (body.x, body.y)), _stem(spot)]


def _club(spot: Spot) -> list[Polygon]:
    """Return a club: three lobes over a stem."""
    size = spot.size
    lobe = Spot(spot.x, spot.y, size * 0.46)
    return [
        _circle(lobe._replace(y=spot.y - size * 0.24)),
        _circle(lobe._replace(x=spot.x - size * 0.27, y=spot.y + size * 0.08)),
        _circle(lobe._replace(x=spot.x + size * 0.27, y=spot.y + size * 0.08)),
        _stem(spot),
    ]


def _diamond(spot: Spot) -> list[Polygon]:
    """Return a diamond as a single polygon."""
    size = spot.size
    return [
        [
            (spot.x, spot.y - size * 0.50),
            (spot.x + size * 0.38, spot.y),
            (spot.x, spot.y + size * 0.50),
            (spot.x - size * 0.38, spot.y),
        ]
    ]


def _star(spot: Spot) -> list[Polygon]:
    """Return a five-pointed star as a single polygon."""
    outer = spot.size / 2
    inner = outer * 0.42
    return [
        [
            (
                spot.x
                + (outer if step % 2 == 0 else inner)
                * math.cos(-math.pi / 2 + step * math.pi / 5),
                spot.y
                + (outer if step % 2 == 0 else inner)
                * math.sin(-math.pi / 2 + step * math.pi / 5),
            )
            for step in range(10)
        ]
    ]


PIP_SHAPES = {"c": _club, "d": _diamond, "h": _heart, "s": _spade}


def _fitted_font(text: str, legend: Legend) -> ImageFont.FreeTypeFont:
    """Return Aileron at *legend*'s cap height, narrowed to fit.

    Two-character runs -- "10" on the tens, "JK" on the joker --
    have to shrink to sit inside the same card as a single "A".
    """
    size = max(1, round(legend.cap_height / CAP_HEIGHT_RATIO))
    font = ImageFont.load_default(size=size)
    left, _top, right, _bottom = font.getbbox(text)
    width = right - left
    if width <= legend.max_width:
        return font
    return ImageFont.load_default(size=max(1, round(size * legend.max_width / width)))


class _Face:
    """One supersampled card being drawn, before reduction."""

    def __init__(self, scale: int) -> None:
        """Start a blank white face at *scale* times card size."""
        self.scale = scale
        self.factor = scale * SUPERSAMPLE
        size = (CARD_SIZE[0] * self.factor, CARD_SIZE[1] * self.factor)
        self.image = Image.new("RGBA", size, WHITE)
        self.draw = ImageDraw.Draw(self.image)

    def spot(self, centre: Point, size: float) -> Spot:
        """Convert a card-pixel centre and size to device pixels."""
        return Spot(centre[0] * self.factor, centre[1] * self.factor, size * self.factor)

    def frame(self, colour: tuple[int, int, int, int]) -> None:
        """Outline the card one card-pixel thick at every scale."""
        width, height = self.image.size
        self.draw.rectangle((0, 0, width - 1, height - 1), outline=colour, width=self.factor)

    def polygons(self, shapes: list[Polygon], colour: tuple[int, int, int, int]) -> None:
        """Fill every polygon in *shapes* with *colour*."""
        for shape in shapes:
            self.draw.polygon(shape, fill=colour)

    def pip(self, suit: str, centre: Point) -> None:
        """Draw *suit*'s pip, in that suit's palette colour."""
        colour = ACCENT if suit in ACCENT_SUITS else INK
        self.polygons(PIP_SHAPES[suit](self.spot(centre, PIP_SIZE)), colour)

    def legend(self, centre: Point, cap_height: float, max_width: float) -> Legend:
        """Convert a text run's card-pixel metrics to device pixels."""
        return Legend(
            centre[0] * self.factor,
            centre[1] * self.factor,
            cap_height * self.factor,
            max_width * self.factor,
        )

    def text(self, text: str, legend: Legend, colour: tuple[int, int, int, int]) -> None:
        """Stamp *text* centred on *legend*, faux-bolded by a stroke."""
        self.draw.text(
            (legend.x, legend.y),
            text,
            font=_fitted_font(text, legend),
            fill=colour,
            anchor="mm",
            stroke_width=max(1, round(STROKE_PIXELS * self.factor)),
            stroke_fill=colour,
        )

    def reduced(self) -> Image.Image:
        """Return the face at its final pixel size."""
        target = (CARD_SIZE[0] * self.scale, CARD_SIZE[1] * self.scale)
        return self.image.resize(target, Image.Resampling.BOX)


def render_face(rank: str, suit: str, scale: int) -> Image.Image:
    """Draw one rank-and-suit card at *scale*."""
    face = _Face(scale)
    face.frame(BORDER)
    colour = ACCENT if suit in ACCENT_SUITS else INK
    face.text(rank, face.legend(RANK_CENTRE, RANK_CAP_HEIGHT, RANK_MAX_WIDTH), colour)
    face.pip(suit, PIP_CENTRE)
    return face.reduced()


def render_joker(scale: int) -> Image.Image:
    """Draw the joker at *scale*: a steel star over a JK legend."""
    face = _Face(scale)
    face.frame(JOKER_BORDER)
    face.polygons(_star(face.spot(STAR_CENTRE, STAR_SIZE)), ACCENT)
    face.text("JK", face.legend(LEGEND_CENTRE, LEGEND_CAP_HEIGHT, LEGEND_MAX_WIDTH), ACCENT)
    return face.reduced()


def render(stem: str, scale: int) -> Image.Image:
    """Draw the card named by an asset *stem* at *scale*."""
    if stem == JOKER:
        return render_joker(scale)
    return render_face(stem[:-1], stem[-1], scale)


def deck_stems() -> list[str]:
    """List the 53 asset stems, in the order they are drawn."""
    return [f"{rank}{suit}" for rank in RANKS for suit in SUITS] + [JOKER]


def generate_deck(out_dir: Path) -> list[Path]:
    """Draw all 53 faces at both scales into *out_dir*.

    Returns:
        The paths written, in the order they were drawn.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for stem in deck_stems():
        for suffix, scale in SCALE_SUFFIXES.items():
            path = out_dir / f"{stem}{suffix}.png"
            render(stem, scale).save(path, "PNG")
            written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """Draw the deck, by default over the packaged assets."""
    parser = argparse.ArgumentParser(description="Draw the 53 card bitmaps at 1x and 2x.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"directory to write into (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args(argv)
    written = generate_deck(args.out)
    print(f"wrote {len(written)} card bitmaps to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
