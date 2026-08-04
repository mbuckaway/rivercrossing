# SPDX-License-Identifier: GPL-3.0-only
"""Render the app icon and DMG background from their SVG sources.

Phase 8 pulls the mechanical half of E9.1.1 forward (P8-D5/P8-D6):
this generator rasterises ``installers/branding/svg/icon.svg`` and
``installers/branding/svg/dmg_background.svg`` into the three
platform artifacts the app and its DMG ship with -- ``RiverCrossing.
icns`` (app icon and DMG volume icon), ``rivercrossing.ico``
(Windows EXE icon, advisory -- no Windows build machine) and
``dmg_background.tiff`` (Apple dual-resolution TIFF for the DMG
window). The three outputs are committed alongside their SVG
sources; ``tools/gen_card_bitmaps.py`` is the precedent for a
committed generator whose output is also tracked (design/README.md:
"tasks E1.3.2 and E4.4.3 commit the generator scripts and may
regenerate them").

GitLab (the target host, P8-D5) forbids committing ``.png`` files, so
every PNG this pipeline renders is an intermediate that lands under
``--build-dir`` (default ``build/branding``, gitignored) and is
never written into the tracked tree.

Regenerate, don't retouch: edit the SVG sources and rerun this
script (``nox -s gen_branding``); never hand-edit the generated
``.icns``/``.ico``/``.tiff``.

Usage::

    python tools/gen_app_icons.py             # regenerate everything
    python tools/gen_app_icons.py --branding-dir DIR --build-dir DIR

Requires ``rsvg-convert`` (``brew install librsvg``) plus the
macOS-only ``/usr/bin/iconutil`` and ``/usr/bin/tiffutil``.
"""

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence  # noqa: TC003 -- dev CLI, no perf-sensitive import path
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANDING_DIR = REPO_ROOT / "installers" / "branding"
DEFAULT_BUILD_DIR = REPO_ROOT / "build" / "branding"

ICON_SVG_NAME = "icon.svg"
BACKGROUND_SVG_NAME = "dmg_background.svg"

ICONSET_DIR_NAME = "AppIcon.iconset"
ICNS_NAME = "RiverCrossing.icns"
ICO_NAME = "rivercrossing.ico"
BACKGROUND_TIFF_NAME = "dmg_background.tiff"

# Apple's ten-representation .iconset naming convention: each 1x name
# is paired with an "@2x" retina variant rendered at double the
# pixel size.
ICONSET_ENTRIES: tuple[tuple[str, int], ...] = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)

# Windows .ico convention: one file embeds every size from 16 to 256.
ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
ICO_MASTER_PIXELS = 256

# The DMG background: 1x at the exact window_rect size, 2x at double.
BACKGROUND_SIZES: tuple[tuple[int, int], tuple[int, int]] = ((660, 400), (1320, 800))


class RsvgConvertMissingError(RuntimeError):
    """Raised when ``rsvg-convert`` cannot be found on ``PATH``."""


class MissingSvgSourceError(RuntimeError):
    """Raised when a required SVG source file does not exist."""


def iconset_entries() -> tuple[tuple[str, int], ...]:
    """Return the ten Apple .iconset file names, paired with pixels."""
    return ICONSET_ENTRIES


def ico_sizes() -> tuple[int, ...]:
    """Return the Windows .ico's embedded pixel sizes, 16 to 256."""
    return ICO_SIZES


def background_sizes() -> tuple[tuple[int, int], tuple[int, int]]:
    """Return the DMG background's 1x and 2x pixel dimensions."""
    return BACKGROUND_SIZES


def svg_source_path(branding_dir: Path, name: str) -> Path:
    """Return the path to one SVG source under *branding_dir*/svg."""
    return branding_dir / "svg" / name


def iconset_dir(build_dir: Path) -> Path:
    """Return the .iconset directory rsvg-convert renders into."""
    return build_dir / ICONSET_DIR_NAME


def iconset_png_path(build_dir: Path, name: str) -> Path:
    """Return the build-dir path for one Apple .iconset PNG."""
    return iconset_dir(build_dir) / name


def ico_master_png_path(build_dir: Path) -> Path:
    """Return the build-dir path for the .ico's 256px master render."""
    return build_dir / "ico_master.png"


def background_png_path(build_dir: Path, width: int, height: int) -> Path:
    """Return the build-dir path for one DMG-background render."""
    return build_dir / f"dmg_background_{width}x{height}.png"


def png_output_paths(build_dir: Path) -> tuple[Path, ...]:
    """List every PNG the pipeline renders, all under *build_dir*.

    GitLab forbids committing PNGs (P8-D5); this is both the
    pipeline's own render-target list and what a test asserts stays
    confined to the gitignored build directory.
    """
    iconset_paths = tuple(iconset_png_path(build_dir, name) for name, _ in ICONSET_ENTRIES)
    background_paths = tuple(background_png_path(build_dir, w, h) for w, h in BACKGROUND_SIZES)
    return (*iconset_paths, ico_master_png_path(build_dir), *background_paths)


def _check_rsvg_convert_available() -> None:
    """Confirm ``rsvg-convert`` is on ``PATH``.

    Raises:
        RsvgConvertMissingError: ``rsvg-convert`` cannot be found.
    """
    if shutil.which("rsvg-convert") is not None:
        return
    msg = "rsvg-convert not found on PATH -- install it with: brew install librsvg"
    raise RsvgConvertMissingError(msg)


def _check_svg_sources_exist(branding_dir: Path) -> None:
    """Confirm both SVG sources exist under *branding_dir*/svg.

    Raises:
        MissingSvgSourceError: A required SVG source is absent.
    """
    for name in (ICON_SVG_NAME, BACKGROUND_SVG_NAME):
        path = svg_source_path(branding_dir, name)
        if not path.exists():
            msg = f"missing SVG source: {path}"
            raise MissingSvgSourceError(msg)


def _run_rsvg_convert(svg_path: Path, out_path: Path, size: tuple[int, int]) -> None:
    """Rasterise *svg_path* to *out_path* at *size* (w, h) pixels."""
    width, height = size
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # rsvg-convert is looked up via shutil.which before every call, so
    # PATH is already verified -- the partial path below is intentional.
    cmd = [
        "rsvg-convert",
        "--width",
        str(width),
        "--height",
        str(height),
        "--output",
        str(out_path),
        str(svg_path),
    ]
    subprocess.run(cmd, check=True)  # noqa: S603


def _render_iconset(branding_dir: Path, build_dir: Path) -> Path:
    """Render the ten Apple .iconset PNGs; return the .iconset dir."""
    svg_path = svg_source_path(branding_dir, ICON_SVG_NAME)
    for name, pixels in ICONSET_ENTRIES:
        _run_rsvg_convert(svg_path, iconset_png_path(build_dir, name), (pixels, pixels))
    return iconset_dir(build_dir)


def _run_iconutil(iconset_path: Path, icns_path: Path) -> None:
    """Convert an .iconset directory to .icns via /usr/bin/iconutil."""
    subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        ["/usr/bin/iconutil", "-c", "icns", "-o", str(icns_path), str(iconset_path)],
        check=True,
    )


def _render_ico(branding_dir: Path, build_dir: Path) -> None:
    """Render the 256px master and write the multi-size Windows .ico."""
    svg_path = svg_source_path(branding_dir, ICON_SVG_NAME)
    master_path = ico_master_png_path(build_dir)
    _run_rsvg_convert(svg_path, master_path, (ICO_MASTER_PIXELS, ICO_MASTER_PIXELS))
    with Image.open(master_path) as master:
        master.save(branding_dir / ICO_NAME, sizes=[(size, size) for size in ICO_SIZES])


def _render_background(branding_dir: Path, build_dir: Path) -> None:
    """Render the 1x/2x backgrounds and combine into a dual-res TIFF."""
    svg_path = svg_source_path(branding_dir, BACKGROUND_SVG_NAME)
    rendered = []
    for width, height in BACKGROUND_SIZES:
        png_path = background_png_path(build_dir, width, height)
        _run_rsvg_convert(svg_path, png_path, (width, height))
        rendered.append(png_path)
    one_x, two_x = rendered
    subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        [
            "/usr/bin/tiffutil",
            "-cathidpicheck",
            str(one_x),
            str(two_x),
            "-out",
            str(branding_dir / BACKGROUND_TIFF_NAME),
        ],
        check=True,
    )


def generate_branding(branding_dir: Path, build_dir: Path) -> None:
    """Run the full pipeline: app icon, Windows .ico, DMG background.

    Raises:
        RsvgConvertMissingError: ``rsvg-convert`` is not on ``PATH``.
        MissingSvgSourceError: A required SVG source is absent.
    """
    _check_rsvg_convert_available()
    _check_svg_sources_exist(branding_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    iconset_path = _render_iconset(branding_dir, build_dir)
    _run_iconutil(iconset_path, branding_dir / ICNS_NAME)
    _render_ico(branding_dir, build_dir)
    _render_background(branding_dir, build_dir)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--branding-dir``/``--build-dir`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branding-dir", type=Path, default=DEFAULT_BRANDING_DIR)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: render the icon, .ico and DMG background."""
    args = _build_parser().parse_args(argv)
    try:
        generate_branding(args.branding_dir, args.build_dir)
    except (RsvgConvertMissingError, MissingSvgSourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote branding artifacts to {args.branding_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
