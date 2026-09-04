# SPDX-License-Identifier: GPL-3.0-only
"""The bundle asset manifest, and the build-time gate over it.

``installers/rivercrossing.spec`` fills ``Analysis(datas=...)`` from
:func:`data_entries`, which verifies the manifest before it returns
anything. A missing or renamed asset therefore aborts the build --
task brief E1.6.1: "a missing asset fails the build, not first
paint" -- instead of shipping a bundle that dies while a window is
drawing, or worse, draws a blank cell mid-race.

The expectation is *derived*, never a directory listing: the ten
``.xrc`` files come from spec.md section 15b's file map, the 106
card bitmaps from ``cards_imagelist``'s own deck keys and scales,
and the three WAV cues from spec.md section 10. A listing would
happily agree with a tree that had lost a file.

:func:`vector_data_entries` is the same idea for a second, separate
manifest: the two evaluator self-test CSVs (E2.4.1, R-44) ship at the
package *root* rather than under ``ui/``, so they get their own small
manifest instead of a root-level entry inside ``required_assets()``,
whose every other entry is ``ui/``-relative.

``docs_data_entries`` is the E9.1.1 addition: the user guide and the
four license texts ship under ``rivercrossing/docs/``, sourced from
two trees -- the guide and the project ``LICENSE`` at the repo root,
and the three font OFL texts inside the package next to the fonts they
cover.

Run it directly to check a tree without waiting for a build::

    python tools/check_asset_manifest.py
    python tools/check_asset_manifest.py --ui-dir path/to/ui
    python tools/check_asset_manifest.py --package-dir path/to/pkg

``main()`` checks all five manifests -- ``verify_assets``,
``verify_vectors``, ``verify_templates``, ``verify_pdf_fonts`` and
``verify_docs`` -- so a tree missing either the ``ui/`` assets, the
two self-test vector CSVs, the five htmlexport template artifacts, the
three PDF report TTFs or any of the five docs fails this direct check
the same way it would fail the real build.
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
DEFAULT_PACKAGE_DIR = _ROOT / "src" / "rivercrossing"

XRC_SUBDIR = "xrc"
CARDS_SUBDIR = "assets/cards"
SOUNDS_SUBDIR = "assets/sounds"

# spec.md section 15b, "Files (src/rivercrossing/ui/xrc/)" -- the ten
# files that hold the frozen windows.
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
    "teams.xrc",
)

# spec.md section 10's three cues (recorded / held / rejected), named
# as module-skeletons.md's ui/sound.py line spells them.
REQUIRED_SOUNDS: tuple[str, ...] = ("error.wav", "flagged.wav", "recorded.wav")

# Where the data must land inside the bundle. A frozen module's
# ``__file__`` points at ``sys._MEIPASS/rivercrossing/ui/...``, so
# ``cards_imagelist.cards_dir()`` and ``harness.xrc_directory()``
# resolve only if the assets sit on that same relative path.
PACKAGE_DEST = "rivercrossing/ui"

# E2.4.1 (spec section 12, R-44): the evaluator self-test's own vector
# CSVs, moved into the package so a bundled app can self-test at
# launch with no ``tests/`` tree riding along. These ship at the
# package *root* (``rivercrossing.hands`` resolves them relative to
# its own ``__file__``, a sibling of ``ui/``) -- a separate manifest
# from ``required_assets()``/``data_entries()`` above, which are all
# ``ui/``-relative, rather than folding a root-level asset into a dict
# whose every other entry (and destination) is scoped one level down.
VECTORS_SUBDIR = "vectors"
REQUIRED_VECTORS: tuple[str, ...] = ("joker_vectors.csv", "rank_sweep.csv")
# PyInstaller's own datas contract: the destination is the containing
# folder a source lands in, not the file's own final path -- so this
# must include the vectors leaf itself, matching PACKAGE_DEST's own
# shape above. Measured: naming only the bare package root here put
# both CSVs one directory too high once bundled, and hands.py's own
# loader never found them there -- a launch-time crash, not the
# build-time failure E1.6.1's own goal asks for (missed once here).
VECTORS_PACKAGE_DEST = f"rivercrossing/{VECTORS_SUBDIR}"

# E6.2.1: the frozen results templates and the two vendored CSS
# artifacts (spec section 8). ``htmlexport.render`` reads the templates
# via Jinja2's PackageLoader, and the page inlines compiled_css +
# fonts_css, so all five must land under
# ``rivercrossing/htmlexport/templates/`` in the bundle. The base64
# fonts_css ships instead of the ``fonts/`` woff2 sources (they never
# ride along); the manifest therefore names the artifacts, not the
# font files.
HTMLEXPORT_TEMPLATES_SUBDIR = "htmlexport/templates"
REQUIRED_TEMPLATES: tuple[str, ...] = (
    "base.html.j2",
    "macros.html.j2",
    "theme.css",
    "compiled_css",
    "fonts_css",
)
HTMLEXPORT_PACKAGE_DEST = f"rivercrossing/{HTMLEXPORT_TEMPLATES_SUBDIR}"

# P7 (E6.3.1): the PDF report's three TTF faces (spec section 8b).
# ``pdfexport.render`` calls ``add_font`` on these at render time and
# fpdf2 embeds them into the PDF bytes, so the bundle must carry them
# under ``rivercrossing/pdfexport/fonts/`` -- the path
# ``pdfexport._FONTS_DIR`` resolves from its own ``__file__``. Only
# the three TTFs ride along; the OFL license texts stay in the tree
# (they commit, they do not ship).
PDF_FONTS_SUBDIR = "pdfexport/fonts"
REQUIRED_PDF_FONTS: tuple[str, ...] = (
    "Barlow-Regular.ttf",
    "BarlowCondensed-SemiBold.ttf",
    "DejaVuSans.ttf",
)
PDF_FONTS_PACKAGE_DEST = f"rivercrossing/{PDF_FONTS_SUBDIR}"

# E9.1.1: the release bundle's docs -- the user guide (E8.2.2) and the
# license texts the bundle must carry: the project's GPL-3.0 LICENSE,
# and the OFL texts for the three vendored font faces (Barlow, Barlow
# Condensed, DejaVu). All five land under ``rivercrossing/docs/`` in
# the bundle, where ``rivercrossing.ui.help.guide_path`` resolves the
# guide from its own ``__file__`` (``module_path.parents[1] / "docs" /
# "user-guide.html"``). The sources are not one tree: the guide and
# the project license live at the repo root beside ``tools/``, and the
# three font licenses ride in the package next to the fonts they
# cover. The originals ship verbatim -- SIMPLECODE rule 1: nothing in
# the bundle renders an aggregate notices file, so none is authored.
DOCS_SUBDIR = "docs"
REQUIRED_DOCS: tuple[str, ...] = (
    "user-guide.html",
    "LICENSE",
    "OFL-Barlow.txt",
    "OFL-DejaVu.txt",
    "OFL.txt",
)
DOCS_PACKAGE_DEST = f"rivercrossing/{DOCS_SUBDIR}"

# Where each shipped doc lives in the source tree, per name. The
# repo-root pair resolve against the module's own ``_ROOT``; the
# package pair resolve against the package dir the spec passes in.
_REPO_ROOT_DOCS: dict[str, str] = {
    "user-guide.html": "docs/user-guide.html",
    "LICENSE": "LICENSE",
}
_PACKAGE_DOCS: dict[str, str] = {
    "OFL-Barlow.txt": "pdfexport/fonts/OFL-Barlow.txt",
    "OFL-DejaVu.txt": "pdfexport/fonts/OFL-DejaVu.txt",
    "OFL.txt": "htmlexport/templates/fonts/OFL.txt",
}


def _doc_source_paths(package_dir: Path) -> dict[str, Path]:
    """Map every required doc name to its source path."""
    sources = {name: _ROOT / relative for name, relative in _REPO_ROOT_DOCS.items()}
    sources.update({name: package_dir / relative for name, relative in _PACKAGE_DOCS.items()})
    return sources


def _doc_relative_paths() -> dict[str, str]:
    """Map every required doc name to its source-relative path."""
    return {**_REPO_ROOT_DOCS, **_PACKAGE_DOCS}


def missing_docs(package_dir: Path) -> tuple[str, ...]:
    """List every required doc absent from its source location."""
    sources = _doc_source_paths(package_dir)
    return tuple(
        relative for name, relative in _doc_relative_paths().items() if not sources[name].is_file()
    )


def verify_docs(package_dir: Path) -> None:
    """Assert the tree ships every required doc.

    Raises:
        MissingAssetError: Naming every absent file.
    """
    missing = missing_docs(package_dir)
    if missing:
        raise MissingAssetError(f"docs missing from {package_dir}: {', '.join(missing)}")


def docs_data_entries(package_dir: Path) -> list[tuple[str, str]]:
    """Return PyInstaller ``(source, destination)`` pairs, docs.

    Raises:
        MissingAssetError: If any required doc is absent.
    """
    verify_docs(package_dir)
    return [(str(source), DOCS_PACKAGE_DEST) for source in _doc_source_paths(package_dir).values()]


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


def missing_vectors(package_dir: Path) -> tuple[str, ...]:
    """List every required vector CSV absent from *package_dir*."""
    return tuple(
        f"{VECTORS_SUBDIR}/{name}"
        for name in REQUIRED_VECTORS
        if not (package_dir / VECTORS_SUBDIR / name).is_file()
    )


def verify_vectors(package_dir: Path) -> None:
    """Assert *package_dir* ships every self-test vector CSV.

    Raises:
        MissingAssetError: Naming every absent file.
    """
    missing = missing_vectors(package_dir)
    if missing:
        raise MissingAssetError(f"vectors missing from {package_dir}: {', '.join(missing)}")


def vector_data_entries(package_dir: Path) -> list[tuple[str, str]]:
    """Return PyInstaller ``(source, destination)`` pairs, vectors.

    Raises:
        MissingAssetError: If any required vector CSV is absent.
    """
    verify_vectors(package_dir)
    return [
        (str(package_dir / VECTORS_SUBDIR / name), VECTORS_PACKAGE_DEST)
        for name in REQUIRED_VECTORS
    ]


def missing_templates(package_dir: Path) -> tuple[str, ...]:
    """List every required template absent from *package_dir*."""
    return tuple(
        f"{HTMLEXPORT_TEMPLATES_SUBDIR}/{name}"
        for name in REQUIRED_TEMPLATES
        if not (package_dir / HTMLEXPORT_TEMPLATES_SUBDIR / name).is_file()
    )


def verify_templates(package_dir: Path) -> None:
    """Assert *package_dir* ships every htmlexport template artifact.

    Raises:
        MissingAssetError: Naming every absent file.
    """
    missing = missing_templates(package_dir)
    if missing:
        raise MissingAssetError(f"templates missing from {package_dir}: {', '.join(missing)}")


def htmlexport_data_entries(package_dir: Path) -> list[tuple[str, str]]:
    """Return PyInstaller ``(source, destination)`` pairs, templates.

    Raises:
        MissingAssetError: If any required template artifact is absent.
    """
    verify_templates(package_dir)
    return [
        (str(package_dir / HTMLEXPORT_TEMPLATES_SUBDIR / name), HTMLEXPORT_PACKAGE_DEST)
        for name in REQUIRED_TEMPLATES
    ]


def missing_pdf_fonts(package_dir: Path) -> tuple[str, ...]:
    """List every required PDF font absent from *package_dir*."""
    return tuple(
        f"{PDF_FONTS_SUBDIR}/{name}"
        for name in REQUIRED_PDF_FONTS
        if not (package_dir / PDF_FONTS_SUBDIR / name).is_file()
    )


def verify_pdf_fonts(package_dir: Path) -> None:
    """Assert *package_dir* ships every PDF report TTF.

    Raises:
        MissingAssetError: Naming every absent file.
    """
    missing = missing_pdf_fonts(package_dir)
    if missing:
        raise MissingAssetError(f"pdf fonts missing from {package_dir}: {', '.join(missing)}")


def pdfexport_font_entries(package_dir: Path) -> list[tuple[str, str]]:
    """Return PyInstaller ``(source, destination)`` pairs, PDF fonts.

    Raises:
        MissingAssetError: If any required PDF font is absent.
    """
    verify_pdf_fonts(package_dir)
    return [
        (str(package_dir / PDF_FONTS_SUBDIR / name), PDF_FONTS_PACKAGE_DEST)
        for name in REQUIRED_PDF_FONTS
    ]


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--ui-dir``/``--package-dir`` argument parser."""
    parser = argparse.ArgumentParser(description="Check the bundle asset manifest.")
    parser.add_argument("--ui-dir", type=Path, default=DEFAULT_UI_DIR)
    parser.add_argument("--package-dir", type=Path, default=DEFAULT_PACKAGE_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Check all five trees for a missing asset; 0 if complete."""
    args = _build_parser().parse_args(argv)
    try:
        verify_assets(args.ui_dir)
        verify_vectors(args.package_dir)
        verify_templates(args.package_dir)
        verify_pdf_fonts(args.package_dir)
        verify_docs(args.package_dir)
    except MissingAssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.ui_dir}: all {len(required_relative_paths())} required assets present")
    print(f"{args.package_dir}: all {len(REQUIRED_VECTORS)} required vectors present")
    print(f"{args.package_dir}: all {len(REQUIRED_TEMPLATES)} required templates present")
    print(f"{args.package_dir}: all {len(REQUIRED_PDF_FONTS)} required pdf fonts present")
    print(f"{args.package_dir}: all {len(REQUIRED_DOCS)} required docs present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
