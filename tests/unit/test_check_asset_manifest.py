# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the PDF-font and docs manifests (P7, E9.1.1).

The bundle-smoke suite (tests/functional/test_bundle_smoke.py) asserts
the full manifest against a *built* bundle; this module pins the new
PDF-font half (P7) and the E9.1.1 docs half against the *source* tree,
so the wiring fails fast in the headless suite without waiting for a
PyInstaller build. ``tools/`` is a dev-script tree, not an installed
package, so the module under test is loaded from its file path (the
pattern test_gen_htmlexport_goldens.py established).
"""

import importlib.util
import re
import shutil
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _ROOT / "tools" / "check_asset_manifest.py"
_PACKAGE_DIR = _ROOT / "src" / "rivercrossing"


def _load_manifest(path: Path) -> ModuleType:
    """Load tools/check_asset_manifest.py by path -- not a package."""
    spec = importlib.util.spec_from_file_location("check_asset_manifest", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = _load_manifest(_MANIFEST_PATH)


def test_required_pdf_fonts_declares_the_three_ttf_faces() -> None:
    """A font disappearing must shrink this, not the suite."""
    assert len(manifest.REQUIRED_PDF_FONTS) == 3
    assert set(manifest.REQUIRED_PDF_FONTS) == {
        "Barlow-Regular.ttf",
        "BarlowCondensed-SemiBold.ttf",
        "DejaVuSans.ttf",
    }


def test_source_tree_pdf_font_names_match_the_required_manifest_exactly() -> None:
    """Sets, not counts: a renamed font leaves the sets unequal.

    Only the TTFs count: the OFL license texts are committed but
    deliberately never ship (fpdf2 embeds the faces into the PDF).
    """
    fonts_dir = _PACKAGE_DIR / manifest.PDF_FONTS_SUBDIR
    on_disk = {entry.name for entry in fonts_dir.iterdir() if entry.suffix == ".ttf"}

    assert on_disk == set(manifest.REQUIRED_PDF_FONTS)


def test_missing_pdf_fonts_given_the_real_source_tree_finds_nothing_absent() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.missing_pdf_fonts(_PACKAGE_DIR) == ()


def test_pdfexport_font_entries_maps_every_font_onto_the_package_path() -> None:
    """PyInstaller datas must land under pdfexport/fonts/.

    Pinned to ``manifest.PDF_FONTS_PACKAGE_DEST`` itself, not the
    literal string: PyInstaller's ``datas`` docs call the destination
    the *containing folder* a source lands in, and the vectors
    manifest test below pins its own destination the same way.
    """
    entries = manifest.pdfexport_font_entries(_PACKAGE_DIR)
    destinations = {destination for _source, destination in entries}
    sources = [Path(source) for source, _destination in entries]

    assert destinations == {"rivercrossing/pdfexport/fonts"}
    assert sorted(path.name for path in sources) == sorted(manifest.REQUIRED_PDF_FONTS)


def test_verify_pdf_fonts_given_a_deleted_font_names_the_missing_file(
    tmp_path: Path,
) -> None:
    """T-5 negative: the raise carries the path, not just a count."""
    shutil.copytree(_PACKAGE_DIR / manifest.PDF_FONTS_SUBDIR, tmp_path / "fonts")
    (tmp_path / "fonts" / "DejaVuSans.ttf").unlink()

    with pytest.raises(
        manifest.MissingAssetError, match=re.escape("pdfexport/fonts/DejaVuSans.ttf")
    ):
        manifest.verify_pdf_fonts(tmp_path)


def test_pdfexport_font_entries_given_a_missing_font_raises_instead_of_listing_entries(
    tmp_path: Path,
) -> None:
    """The spec cannot obtain font datas without passing the check."""
    shutil.copytree(_PACKAGE_DIR / manifest.PDF_FONTS_SUBDIR, tmp_path / "fonts")
    (tmp_path / "fonts" / "Barlow-Regular.ttf").unlink()

    with pytest.raises(
        manifest.MissingAssetError, match=re.escape("pdfexport/fonts/Barlow-Regular.ttf")
    ):
        manifest.pdfexport_font_entries(tmp_path)


def test_main_given_a_complete_tree_reports_the_pdf_font_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI's success line names the PDF fonts too."""
    exit_code = manifest.main(["--package-dir", str(_PACKAGE_DIR)])

    expected = f"all {len(manifest.REQUIRED_PDF_FONTS)} required pdf fonts present"
    assert exit_code == 0
    assert expected in capsys.readouterr().out


# ------------------------------------------------- the docs manifest

# E9.1.1: the release bundle's docs -- the user guide (E8.2.2) and the
# four license texts -- land under rivercrossing/docs/, sourced from
# two trees: the guide + project LICENSE at the repo root, and the
# three font OFL texts inside the package next to the fonts they cover.


def test_required_docs_declares_the_five_shipped_docs() -> None:
    """A doc disappearing must shrink this, not the suite."""
    assert len(manifest.REQUIRED_DOCS) == 5
    assert set(manifest.REQUIRED_DOCS) == {
        "user-guide.html",
        "LICENSE",
        "OFL-Barlow.txt",
        "OFL-DejaVu.txt",
        "OFL.txt",
    }


def test_source_tree_doc_names_match_the_required_manifest_exactly() -> None:
    """Sets, not counts: a renamed doc leaves the sets unequal.

    The manifest ships only what ``docs_data_entries`` names, so the
    entry sources' names must equal ``REQUIRED_DOCS`` exactly -- a
    stray or renamed file would leave the sets unequal.
    """
    sources = [Path(source) for source, _destination in manifest.docs_data_entries(_PACKAGE_DIR)]

    assert sorted(path.name for path in sources) == sorted(manifest.REQUIRED_DOCS)


def test_missing_docs_given_the_real_source_tree_finds_nothing_absent() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.missing_docs(_PACKAGE_DIR) == ()


def test_docs_data_entries_maps_every_doc_onto_the_package_docs_path() -> None:
    """PyInstaller datas must land every doc under ``rivercrossing/docs``.

    Pinned to the literal string, not ``manifest.DOCS_PACKAGE_DEST``
    itself: the destination is the *containing folder* PyInstaller
    puts a source's own filename into, so ``"rivercrossing"`` alone
    would drop the guide one directory too high -- exactly where
    ``help.guide_path``'s bundled lookup (``parents[1] / "docs"``)
    would never find it.
    """
    entries = manifest.docs_data_entries(_PACKAGE_DIR)
    destinations = {destination for _source, destination in entries}
    sources = [Path(source) for source, _destination in entries]

    assert destinations == {"rivercrossing/docs"}
    assert sorted(path.name for path in sources) == sorted(manifest.REQUIRED_DOCS)


def test_verify_docs_given_a_deleted_font_license_names_the_missing_file(
    tmp_path: Path,
) -> None:
    """T-5 negative: the raise carries the path, not just a count."""
    shutil.copytree(
        _PACKAGE_DIR / "pdfexport" / "fonts", tmp_path / "pdfexport" / "fonts"
    )
    shutil.copytree(
        _PACKAGE_DIR / "htmlexport" / "templates" / "fonts",
        tmp_path / "htmlexport" / "templates" / "fonts",
    )
    (tmp_path / "pdfexport" / "fonts" / "OFL-Barlow.txt").unlink()

    with pytest.raises(
        manifest.MissingAssetError, match=re.escape("pdfexport/fonts/OFL-Barlow.txt")
    ):
        manifest.verify_docs(tmp_path)


def test_docs_data_entries_given_a_missing_doc_raises_instead_of_listing_entries(
    tmp_path: Path,
) -> None:
    """The spec cannot obtain doc datas without passing the check."""
    shutil.copytree(
        _PACKAGE_DIR / "pdfexport" / "fonts", tmp_path / "pdfexport" / "fonts"
    )
    shutil.copytree(
        _PACKAGE_DIR / "htmlexport" / "templates" / "fonts",
        tmp_path / "htmlexport" / "templates" / "fonts",
    )
    (tmp_path / "htmlexport" / "templates" / "fonts" / "OFL.txt").unlink()

    with pytest.raises(
        manifest.MissingAssetError, match=re.escape("htmlexport/templates/fonts/OFL.txt")
    ):
        manifest.docs_data_entries(tmp_path)
