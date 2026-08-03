# SPDX-License-Identifier: GPL-3.0-only
"""The bundle asset manifest, and the build-time gate over it.

``installers/rivercrossing.spec`` fills ``Analysis(datas=...)`` from
:func:`data_entries`, which verifies the manifest before it returns
anything. A missing or renamed asset therefore aborts the build --
task brief E1.6.1: "a missing asset fails the build, not first
paint" -- instead of shipping a bundle that dies while a window is
drawing, or worse, draws a blank cell mid-race.

The expectation is *derived*, never a directory listing: the nine
``.xrc`` files come from spec.md section 15b's file map, the 106
card bitmaps from ``cards_imagelist``'s own deck keys and scales,
and the three WAV cues from spec.md section 10. A listing would
happily agree with a tree that had lost a file.

Run it directly to check a tree without waiting for a build::

    python tools/check_asset_manifest.py
    python tools/check_asset_manifest.py --ui-dir path/to/ui
"""

import argparse
import sys
from collections.abc import Sequence  # noqa: TC003 -- dev CLI, not a hot import path
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# Ahead of anything installed: the manifest describes *this* tree,
# and this module also has to run from a copy of the repo that was
# never pip-installed (the smoke suite builds one to prove a missing
# asset fails the build).
sys.path.insert(0, str(_ROOT / "src"))

from rivercrossing.ui.cards_imagelist import (  # noqa: E402 -- needs the path above
    CARD_KEYS,
    SCALES,
    asset_filename,
)

DEFAULT_UI_DIR = _ROOT / "src" / "rivercrossing" / "ui"

XRC_SUBDIR = "xrc"
CARDS_SUBDIR = "assets/cards"
SOUNDS_SUBDIR = "assets/sounds"

# spec.md section 15b, "Files (src/rivercrossing/ui/xrc/)" -- the nine
# files that hold all 23 windows.
REQUIRED_XRC: tuple[str, ...] = (
    "audit.xrc",
    "detail.xrc",
    "dialogs.xrc",
    "library.xrc",
    "main.xrc",
    "results.xrc",
    "riders.xrc",
    "settings.xrc",
    "setup.xrc",
)

# spec.md section 10's three cues (recorded / held / rejected), named
# as module-skeletons.md's ui/sound.py line spells them.
REQUIRED_SOUNDS: tuple[str, ...] = ("error.wav", "flagged.wav", "recorded.wav")

# Where the data must land inside the bundle. A frozen module's
# ``__file__`` points at ``sys._MEIPASS/rivercrossing/ui/...``, so
# ``cards_imagelist.cards_dir()`` and ``harness.xrc_directory()``
# resolve only if the assets sit on that same relative path.
PACKAGE_DEST = "rivercrossing/ui"


class MissingAssetError(FileNotFoundError):
    """Raised when a tree is missing an asset the bundle must ship.

    Subclasses ``FileNotFoundError`` for the same reason
    ``cards_imagelist.MissingCardAssetError`` does: a packaging
    shortfall reads as what it is, a file that should be there.
    """


def required_cards() -> tuple[str, ...]:
    """List all 106 card bitmap filenames, both scales.

    Derived from ``cards_imagelist``'s own ``CARD_KEYS`` and
    ``SCALES`` so the build gate and the runtime loader cannot
    disagree about what the deck is.
    """
    return tuple(sorted(asset_filename(key, scale) for key in CARD_KEYS for scale in SCALES))


def required_assets() -> dict[str, tuple[str, ...]]:
    """Map each asset subdirectory to the filenames it must hold."""
    return {
        XRC_SUBDIR: REQUIRED_XRC,
        CARDS_SUBDIR: required_cards(),
        SOUNDS_SUBDIR: REQUIRED_SOUNDS,
    }


def required_relative_paths() -> tuple[str, ...]:
    """List every required asset as a ``ui/``-relative POSIX path."""
    return tuple(
        f"{subdir}/{name}" for subdir, names in required_assets().items() for name in names
    )


def missing_assets(ui_dir: Path) -> tuple[str, ...]:
    """List every required asset absent from *ui_dir*, in order."""
    return tuple(
        relative for relative in required_relative_paths() if not (ui_dir / relative).is_file()
    )


def verify_assets(ui_dir: Path) -> None:
    """Assert *ui_dir* holds every asset the bundle must ship.

    Raises:
        MissingAssetError: Naming every absent file, so one run
            reports the whole shortfall rather than the first gap.
    """
    missing = missing_assets(ui_dir)
    if missing:
        raise MissingAssetError(f"assets missing from {ui_dir}: {', '.join(missing)}")


def data_entries(ui_dir: Path) -> list[tuple[str, str]]:
    """Return PyInstaller ``(source, destination)`` pairs for *ui_dir*.

    One entry per file rather than per directory, so the manifest is
    literally what the bundle contains and a stray file cannot ride
    along. Verifies first: the spec cannot obtain its datas without
    passing the check.

    Raises:
        MissingAssetError: If any required asset is absent.
    """
    verify_assets(ui_dir)
    return [
        (str(ui_dir / subdir / name), f"{PACKAGE_DEST}/{subdir}")
        for subdir, names in required_assets().items()
        for name in names
    ]


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--ui-dir`` argument parser."""
    parser = argparse.ArgumentParser(description="Check the bundle asset manifest.")
    parser.add_argument("--ui-dir", type=Path, default=DEFAULT_UI_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Report any missing asset in the given tree; 0 if complete."""
    args = _build_parser().parse_args(argv)
    try:
        verify_assets(args.ui_dir)
    except MissingAssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.ui_dir}: all {len(required_relative_paths())} required assets present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
