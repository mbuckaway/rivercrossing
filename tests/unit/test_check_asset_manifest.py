# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the PDF-font and docs manifests (P7, E9.1.1).

This module pins the new PDF-font half (P7) and the E9.1.1 docs half
against the *source* tree, so the wiring fails fast in the headless
suite without waiting for a PyInstaller build. ``tools/`` is a
dev-script tree, not an installed package, so the module under test
is loaded from its file path (the pattern
test_gen_htmlexport_goldens.py established).
"""

import importlib.util
import re
import shutil
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest

from rivercrossing.cards import Card, Rank, Suit
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.cards_imagelist import CARD_KEYS, UnknownCardCodeError, asset_key

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
    """PyInstaller datas land every doc under ``rivercrossing/docs``.

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
    shutil.copytree(_PACKAGE_DIR / "pdfexport" / "fonts", tmp_path / "pdfexport" / "fonts")
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
    shutil.copytree(_PACKAGE_DIR / "pdfexport" / "fonts", tmp_path / "pdfexport" / "fonts")
    shutil.copytree(
        _PACKAGE_DIR / "htmlexport" / "templates" / "fonts",
        tmp_path / "htmlexport" / "templates" / "fonts",
    )
    (tmp_path / "htmlexport" / "templates" / "fonts" / "OFL.txt").unlink()

    with pytest.raises(
        manifest.MissingAssetError, match=re.escape("htmlexport/templates/fonts/OFL.txt")
    ):
        manifest.docs_data_entries(tmp_path)


# ------------------------------- the stored-code to asset-key match

# Phase 7: the ten's stored code is "10" (``Card.code()`` -> "10D"),
# which is also its bitmap's asset rank ("10d"). These tests pin the
# two spellings against each other: a code that resolved to a bitmap
# while rendering a different rank would ship a face that disagrees
# with its own image, and a surviving "T" alias would hide it.


def _ten_of_diamonds_code() -> str:
    """Return the ten of diamonds' stored code ("10D")."""
    return Card(rank=Rank.TEN, suit=Suit.DIAMONDS).code()


@pytest.mark.parametrize("rank", list(Rank))
def test_asset_key_given_every_natural_card_code_resolves_to_a_shipped_bitmap(
    rank: Rank,
) -> None:
    """Every Card.code() lands on a bitmap key the manifest ships."""
    key = asset_key(Card(rank=rank, suit=Suit.SPADES).code())

    assert key in CARD_KEYS


def test_asset_key_given_the_ten_code_resolves_to_the_10_bitmap_stem() -> None:
    """Card.code()'s "10D" is the "10d" face, never a "Td" lookalike."""
    assert asset_key(_ten_of_diamonds_code()) == "10d"


def test_format_card_rank_text_given_the_ten_equals_its_bitmap_asset_rank() -> None:
    """The image/text match: the rendered "10" is the asset's "10"."""
    code = _ten_of_diamonds_code()
    rendered_rank = format_card(code)[:-1]
    asset_rank = asset_key(code)[:-1]

    assert (rendered_rank, asset_rank) == ("10", "10")


@pytest.mark.parametrize("rank", list(Rank))
def test_format_card_rank_text_equals_the_asset_rank_for_every_rank(rank: Rank) -> None:
    """T-3: no rank's text can drift from its bitmap's token."""
    code = Card(rank=rank, suit=Suit.CLUBS).code()

    assert format_card(code)[:-1] == asset_key(code)[:-1]


def test_asset_key_given_the_retired_t_ten_code_raises_unknown_card_code_error() -> None:
    """T-5: the legacy "T" ten has no bitmap and no surviving alias."""
    with pytest.raises(UnknownCardCodeError, match=re.escape("'TD'")):
        asset_key("TD")


# ------------------------------------------------ templates manifest


def test_required_templates_declares_the_poster_and_wordpress_pages() -> None:
    """A template disappearing must shrink this, not the suite.

    ``poster.html.j2`` is ``htmlexport.render_poster``'s own page and
    ``wordpress.html.j2`` is ``render_wordpress``'s content fragment: a
    bundle without either exports straight into ``TemplateNotFound``.
    ``compiled_css_wp`` is the fragment's scoped stylesheet, derived
    from ``compiled_css`` by the same generator.
    """
    assert set(manifest.REQUIRED_TEMPLATES) == {
        "base.html.j2",
        "macros.html.j2",
        "poster.html.j2",
        "wordpress.html.j2",
        "theme.css",
        "compiled_css",
        "compiled_css_wp",
        "fonts_css",
    }


def test_missing_templates_given_the_real_source_tree_finds_nothing_absent() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.missing_templates(_PACKAGE_DIR) == ()


# -------------------------------------------------- the xrc drift guard

# E1.3.1 / E11: the ten ``.xrc`` files come from spec.md section 15b's
# file map -- ``simulation.xrc`` joined the original nine with the
# Rider Simulator. The manifest is the only authority on which files
# ship, so the check runs both ways: an absent file and an unlisted
# file both fail the build. Otherwise a renamed window would leave the
# manifest naming a file that is gone while shipping one the loader
# never opens.


def test_required_xrc_declares_the_ten_window_files_including_simulation() -> None:
    """A window file disappearing must shrink this, not the suite."""
    assert len(manifest.REQUIRED_XRC) == 10
    assert "simulation.xrc" in manifest.REQUIRED_XRC


def test_extra_xrc_files_given_the_real_source_tree_finds_no_extras() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.extra_xrc_files(manifest.DEFAULT_UI_DIR) == ()


def test_extra_xrc_files_given_an_unlisted_file_names_only_it(tmp_path: Path) -> None:
    """The guard names the stray, so a rename is a one-look fix."""
    shutil.copytree(manifest.DEFAULT_UI_DIR / manifest.XRC_SUBDIR, tmp_path / manifest.XRC_SUBDIR)
    (tmp_path / manifest.XRC_SUBDIR / "stray.xrc").write_text("", encoding="utf-8")

    assert manifest.extra_xrc_files(tmp_path) == ("stray.xrc",)


def test_extra_xrc_files_given_several_unlisted_files_names_them_sorted(tmp_path: Path) -> None:
    """Many strays: the whole set, in a stable order for the caller."""
    shutil.copytree(manifest.DEFAULT_UI_DIR / manifest.XRC_SUBDIR, tmp_path / manifest.XRC_SUBDIR)
    (tmp_path / manifest.XRC_SUBDIR / "zeta.xrc").write_text("", encoding="utf-8")
    (tmp_path / manifest.XRC_SUBDIR / "alpha.xrc").write_text("", encoding="utf-8")

    assert manifest.extra_xrc_files(tmp_path) == ("alpha.xrc", "zeta.xrc")


def test_verify_assets_given_a_stray_xrc_names_the_unlisted_file(tmp_path: Path) -> None:
    """T-5 negative: an extra ``.xrc`` fails the manifest check."""
    shutil.copytree(manifest.DEFAULT_UI_DIR / manifest.XRC_SUBDIR, tmp_path / manifest.XRC_SUBDIR)
    (tmp_path / manifest.XRC_SUBDIR / "stray.xrc").write_text("", encoding="utf-8")

    with pytest.raises(manifest.MissingAssetError, match=re.escape("xrc/stray.xrc")):
        manifest.verify_assets(tmp_path)


def test_data_entries_given_a_stray_xrc_raises_instead_of_listing_entries(
    tmp_path: Path,
) -> None:
    """The spec cannot obtain its datas from a drifted tree."""
    shutil.copytree(manifest.DEFAULT_UI_DIR / manifest.XRC_SUBDIR, tmp_path / manifest.XRC_SUBDIR)
    (tmp_path / manifest.XRC_SUBDIR / "stray.xrc").write_text("", encoding="utf-8")

    with pytest.raises(manifest.MissingAssetError, match=re.escape("xrc/stray.xrc")):
        manifest.data_entries(tmp_path)


def test_verify_assets_given_a_tree_without_the_card_bitmaps_names_them(tmp_path: Path) -> None:
    """T-5 negative: the absent half of the same check still fires."""
    shutil.copytree(manifest.DEFAULT_UI_DIR / manifest.XRC_SUBDIR, tmp_path / manifest.XRC_SUBDIR)

    with pytest.raises(manifest.MissingAssetError, match=re.escape("assets/cards/")):
        manifest.verify_assets(tmp_path)
