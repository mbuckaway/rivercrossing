# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the card bitmap loader (E1.3.2).

Everything here runs without ``wx`` and without a display: the
stored-code to asset-key mapping, the frozen 53-key set, the
on-disk inventory of ``ui/assets/``, the mono-steel palette
invariant, the startup validation that turns a packaging mistake
into a crash before the first paint, and the parity of
``tools/gen_card_bitmaps.py`` with the committed output.

Building a real ``wx.ImageList`` from these files is the separate
functional suite; nothing here may import ``wx``.
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

import rivercrossing.ui
from rivercrossing.ui.cards_imagelist import (
    BITMAP_SIZES,
    CARD_KEYS,
    JOKER_CODE,
    JOKER_KEY,
    SCALE_1X,
    SCALE_2X,
    SCALES,
    MissingCardAssetError,
    UnknownCardCodeError,
    asset_filename,
    card_asset_paths,
    cards_dir,
    preferred_scale,
    verify_card_assets,
)
from rivercrossing.ui.cards_imagelist import (
    asset_key as to_asset_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GEN_PATH = _REPO_ROOT / "tools" / "gen_card_bitmaps.py"

_UI_DIR = Path(rivercrossing.ui.__file__).resolve().parent
_SOUNDS_DIR = _UI_DIR / "assets" / "sounds"

# design/assets/cards/ ships a contact sheet next to the faces. It
# documents the deck; it is not a member of the imagelist and is
# not packaged.
CONTACT_SHEET = "contact-sheet.png"

# design/docs-md/sound-cues.md via design/README.md.
SOUND_CUES = ("error.wav", "flagged.wav", "recorded.wav")

# theme.css: --color-ink, --color-steel-700, --color-steel-600.
INK = (29, 31, 32, 255)
ACCENT = (65, 97, 128, 255)
BORDER = (196, 198, 199, 255)
JOKER_BORDER = (89, 126, 163, 255)

CARD_FILE_COUNT = 106

# module-skeletons.md S4: Card.code() -> "AS", "TD", "JK".
CODE_CASES = (
    ("AS", "As"),
    ("TD", "10d"),
    ("JK", "joker"),
    ("2C", "2c"),
    ("KH", "Kh"),
    ("9S", "9s"),
    ("QD", "Qd"),
    ("JC", "Jc"),
    ("TH", "10h"),
    ("AC", "Ac"),
)

BAD_CODES = (
    "",
    "A",
    "S",
    "ASX",
    "1S",
    "0C",
    "TX",
    "as",
    "aS",
    "As",
    "10D",
    "XY",
    "JOKER",
    "jk",
    "A ",
    "♠A",
)

SCALE_SIZE_CASES = ((SCALE_1X, (24, 32)), (SCALE_2X, (48, 64)))

FILENAME_CASES = (
    (SCALE_1X, "As", "As.png"),
    (SCALE_2X, "As", "As-2x.png"),
    (SCALE_1X, JOKER_KEY, "joker.png"),
    (SCALE_2X, JOKER_KEY, "joker-2x.png"),
    (SCALE_1X, "10d", "10d.png"),
    (SCALE_2X, "10d", "10d-2x.png"),
)

# wx reports 1.0 on a plain display and 2.0 on a Retina one;
# Windows reports fractional factors such as 1.25 and 1.5. Anything
# above 1.0 would upscale the 24x32 face, so the 48x64 is used.
SCALE_FACTOR_CASES = (
    (0.5, SCALE_1X),
    (1.0, SCALE_1X),
    (1.001, SCALE_2X),
    (1.25, SCALE_2X),
    (1.5, SCALE_2X),
    (2.0, SCALE_2X),
    (3.0, SCALE_2X),
)

REMOVABLE_FILES = ("As.png", "As-2x.png", "joker.png", "9s-2x.png")

SUIT_COLOUR_CASES = (
    ("2c", INK),
    ("As", INK),
    ("Ah", ACCENT),
    ("10d", ACCENT),
    (JOKER_KEY, ACCENT),
)

BORDER_COLOUR_CASES = (("2c", BORDER), ("Ah", BORDER), (JOKER_KEY, JOKER_BORDER))

ALL_CARD_FILES = tuple(asset_filename(key, scale) for scale in SCALES for key in CARD_KEYS)


def _load_generator(path: Path) -> ModuleType:
    """Load tools/gen_card_bitmaps.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_card_bitmaps", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_card_bitmaps = _load_generator(_GEN_PATH)


def _colours(path: Path) -> set[tuple[int, int, int, int]]:
    """Return every distinct RGBA colour in one card bitmap."""
    with Image.open(path) as image:
        counted = image.convert("RGBA").getcolors(maxcolors=1 << 18)
    return {colour for _count, colour in counted}


def _stocked_dir(target: Path) -> Path:
    """Copy the packaged card bitmaps into *target* and return it."""
    target.mkdir(parents=True, exist_ok=True)
    for name in ALL_CARD_FILES:
        target.joinpath(name).write_bytes((cards_dir() / name).read_bytes())
    return target


@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the whole deck once, into a throwaway directory."""
    target = tmp_path_factory.mktemp("regenerated")
    gen_card_bitmaps.generate_deck(target)
    return target


# --- stored code -> asset key ---


@pytest.mark.parametrize(("code", "key"), CODE_CASES)
def test_asset_key_with_stored_code_returns_the_asset_filename_stem(code: str, key: str) -> None:
    """Stored AS -> As, TD -> 10d, JK -> joker (module-skeletons S4)."""
    assert to_asset_key(code) == key


@pytest.mark.parametrize("code", BAD_CODES)
def test_asset_key_with_unmappable_code_raises_unknown_card_code_error(
    code: str,
) -> None:
    """An unmappable code is named in the error, never guessed at."""
    with pytest.raises(UnknownCardCodeError, match=re.escape(f"{code!r}")):
        to_asset_key(code)


def test_asset_key_maps_every_stored_code_onto_the_frozen_key_set() -> None:
    """The 52 faces plus the joker cover the key set exactly once."""
    codes = [f"{rank}{suit}" for rank in "23456789TJQKA" for suit in "CDHS"]

    keys = [to_asset_key(code) for code in [*codes, JOKER_CODE]]

    assert sorted(keys) == sorted(CARD_KEYS)


@given(st.text(max_size=4))
def test_asset_key_with_arbitrary_text_either_raises_or_returns_a_known_key(
    text: str,
) -> None:
    """Property: no input ever yields a key with no bitmap behind it."""
    try:
        key = to_asset_key(text)
    except UnknownCardCodeError:
        return

    assert key in CARD_KEYS


# --- the frozen key set ---


def test_card_keys_holds_fifty_three_entries() -> None:
    """52 faces + 1 joker -- spec.md 15b's code-side imagelist."""
    assert len(CARD_KEYS) == 53


def test_card_keys_is_exactly_the_ranks_by_suits_plus_the_joker() -> None:
    """Built independently here so a typo in either list shows up."""
    expected = {
        f"{rank}{suit}"
        for rank in ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
        for suit in ("c", "d", "h", "s")
    }

    assert set(CARD_KEYS) == expected | {JOKER_KEY}


def test_card_keys_holds_no_duplicate() -> None:
    """A duplicate would silently shrink the imagelist."""
    assert len(set(CARD_KEYS)) == len(CARD_KEYS)


def test_card_keys_excludes_the_contact_sheet() -> None:
    """The contact sheet documents the deck; it is not a card."""
    assert Path(CONTACT_SHEET).stem not in CARD_KEYS


# --- filenames ---


@pytest.mark.parametrize(("scale", "key", "filename"), FILENAME_CASES)
def test_asset_filename_returns_the_committed_name(scale: str, key: str, filename: str) -> None:
    """1x is bare, 2x carries the "-2x" suffix (task brief E1.3.2)."""
    assert asset_filename(key, scale) == filename


def test_asset_filename_with_unknown_scale_raises_value_error() -> None:
    """Only the two documented scales exist."""
    with pytest.raises(ValueError, match=re.escape("'3x'")):
        asset_filename("As", "3x")


# --- what is actually on disk ---


def test_packaged_cards_directory_holds_exactly_the_committed_file_set() -> None:
    """106 files: 53 faces at two scales, and nothing else."""
    names = sorted(path.name for path in cards_dir().iterdir())

    assert names == sorted(ALL_CARD_FILES)


def test_packaged_cards_directory_holds_one_hundred_six_files() -> None:
    """The count the packaging manifest has to reproduce."""
    assert len(list(cards_dir().iterdir())) == CARD_FILE_COUNT


def test_packaged_cards_directory_omits_the_contact_sheet() -> None:
    """design/assets/cards/ ships 107 files; only 106 are shipped on."""
    assert not (cards_dir() / CONTACT_SHEET).exists()


def test_packaged_sounds_directory_holds_the_three_console_cues() -> None:
    """Recorded, flagged, error -- design/README.md assets/sounds."""
    names = sorted(path.name for path in _SOUNDS_DIR.iterdir())

    assert names == sorted(SOUND_CUES)


# --- pixel sizes and palette of the committed bitmaps ---


@pytest.mark.parametrize(("scale", "size"), SCALE_SIZE_CASES)
def test_bitmap_sizes_declares_the_documented_pixel_size(
    scale: str, size: tuple[int, int]
) -> None:
    """24x32 at 1x, 48x64 at 2x -- design/README.md assets/cards."""
    assert BITMAP_SIZES[scale] == size


@pytest.mark.parametrize("scale", SCALES)
def test_packaged_card_bitmap_measures_its_documented_size(scale: str) -> None:
    """Measured from the files, not inferred from their names."""
    paths = card_asset_paths(cards_dir(), scale)

    sizes = {Image.open(path).size for path in paths.values()}

    assert sizes == {BITMAP_SIZES[scale]}


@pytest.mark.parametrize("name", ALL_CARD_FILES)
def test_packaged_card_bitmap_stays_inside_the_mono_steel_palette(name: str) -> None:
    """No red: every colour satisfies r <= g <= b (design/README.md)."""
    off_palette = [
        colour
        for colour in _colours(cards_dir() / name)
        if not colour[0] <= colour[1] <= colour[2]
    ]

    assert off_palette == []


@pytest.mark.parametrize(("key", "colour"), SUIT_COLOUR_CASES)
def test_packaged_card_bitmap_paints_its_suit_in_the_expected_ink(
    key: str, colour: tuple[int, int, int, int]
) -> None:
    """Clubs/spades take the ink, hearts/diamonds the steel accent."""
    assert colour in _colours(cards_dir() / asset_filename(key, SCALE_1X))


@pytest.mark.parametrize(("key", "colour"), BORDER_COLOUR_CASES)
def test_packaged_card_bitmap_draws_its_documented_border(
    key: str, colour: tuple[int, int, int, int]
) -> None:
    """The joker's frame is steel-600; every face card's is neutral."""
    assert colour in _colours(cards_dir() / asset_filename(key, SCALE_1X))


# --- startup validation ---


def test_verify_card_assets_with_the_packaged_directory_returns_none() -> None:
    """The shipped tree is complete, so startup does not raise."""
    assert verify_card_assets(cards_dir()) is None


def test_verify_card_assets_ignores_a_stray_contact_sheet(tmp_path: Path) -> None:
    """A 107th file in the directory never becomes a 54th card."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath(CONTACT_SHEET).write_bytes(b"not a card")

    assert verify_card_assets(stocked) is None


@pytest.mark.parametrize("name", REMOVABLE_FILES)
def test_verify_card_assets_with_one_file_removed_raises_naming_it(
    tmp_path: Path, name: str
) -> None:
    """A packaging mistake crashes startup, not the first paint."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath(name).unlink()

    with pytest.raises(MissingCardAssetError, match=re.escape(name)):
        verify_card_assets(stocked)


def test_verify_card_assets_reports_every_missing_file_not_only_the_first(
    tmp_path: Path,
) -> None:
    """The sweep is complete, so one run lists the whole shortfall."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("2c.png").unlink()
    stocked.joinpath("joker-2x.png").unlink()

    with pytest.raises(MissingCardAssetError) as caught:
        verify_card_assets(stocked)

    assert "2c.png" in str(caught.value)
    assert "joker-2x.png" in str(caught.value)


def test_verify_card_assets_with_an_empty_directory_names_the_whole_deck(
    tmp_path: Path,
) -> None:
    """An unpopulated bundle fails loudly with the full shortfall."""
    empty = tmp_path / "cards"
    empty.mkdir()

    with pytest.raises(MissingCardAssetError) as caught:
        verify_card_assets(empty)

    named = [name for name in ALL_CARD_FILES if name in str(caught.value)]
    assert len(named) == CARD_FILE_COUNT


def test_verify_card_assets_with_a_directory_holding_a_lookalike_name_raises(
    tmp_path: Path,
) -> None:
    """A Td.png does not satisfy 10d.png -- the naming is frozen."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("10d.png").rename(stocked / "Td.png")

    with pytest.raises(MissingCardAssetError, match=re.escape("10d.png")):
        verify_card_assets(stocked)


# --- path map ---


@pytest.mark.parametrize("scale", SCALES)
def test_card_asset_paths_returns_one_path_per_card_key(scale: str) -> None:
    """The loader consumes 53 paths, one per key, at one scale."""
    paths = card_asset_paths(cards_dir(), scale)

    assert sorted(paths) == sorted(CARD_KEYS)


def test_card_asset_paths_points_at_the_scale_it_was_asked_for() -> None:
    """The 2x map really does address the "-2x" files."""
    paths = card_asset_paths(cards_dir(), SCALE_2X)

    assert paths[JOKER_KEY].name == "joker-2x.png"


def test_card_asset_paths_with_an_incomplete_directory_raises(tmp_path: Path) -> None:
    """Validation runs before any path is handed to the toolkit."""
    stocked = _stocked_dir(tmp_path / "cards")
    stocked.joinpath("Kh.png").unlink()

    with pytest.raises(MissingCardAssetError, match=re.escape("Kh.png")):
        card_asset_paths(stocked, SCALE_2X)


# --- display scale ---


@pytest.mark.parametrize(("factor", "scale"), SCALE_FACTOR_CASES)
def test_preferred_scale_picks_the_face_that_needs_no_upscaling(factor: float, scale: str) -> None:
    """1.0 takes the 24x32; anything denser takes the 48x64."""
    assert preferred_scale(factor) == scale


# --- generator parity ---


def test_generate_deck_writes_exactly_the_committed_file_set(generated_dir: Path) -> None:
    """Regeneration reproduces the packaged names, one for one."""
    names = sorted(path.name for path in generated_dir.iterdir())

    assert names == sorted(path.name for path in cards_dir().iterdir())


def test_generate_deck_omits_the_contact_sheet(generated_dir: Path) -> None:
    """The generator draws cards; the contact sheet is documentation."""
    assert not (generated_dir / CONTACT_SHEET).exists()


@pytest.mark.parametrize("scale", SCALES)
def test_generate_deck_matches_the_committed_pixel_sizes(generated_dir: Path, scale: str) -> None:
    """Same dimensions as the bitmaps already in the package."""
    paths = card_asset_paths(generated_dir, scale)

    sizes = {Image.open(path).size for path in paths.values()}

    assert sizes == {BITMAP_SIZES[scale]}


@pytest.mark.parametrize("name", ALL_CARD_FILES)
def test_generate_deck_stays_inside_the_mono_steel_palette(generated_dir: Path, name: str) -> None:
    """The redrawn deck keeps the no-red invariant of the starters."""
    off_palette = [
        colour
        for colour in _colours(generated_dir / name)
        if not colour[0] <= colour[1] <= colour[2]
    ]

    assert off_palette == []


@pytest.mark.parametrize(("key", "colour"), SUIT_COLOUR_CASES)
def test_generate_deck_paints_its_suit_in_the_expected_ink(
    generated_dir: Path, key: str, colour: tuple[int, int, int, int]
) -> None:
    """Same ink and steel accent as the committed bitmaps."""
    assert colour in _colours(generated_dir / asset_filename(key, SCALE_1X))


@pytest.mark.parametrize(("key", "colour"), BORDER_COLOUR_CASES)
def test_generate_deck_draws_the_documented_border(
    generated_dir: Path, key: str, colour: tuple[int, int, int, int]
) -> None:
    """The joker keeps its steel-600 frame after regeneration."""
    assert colour in _colours(generated_dir / asset_filename(key, SCALE_1X))


def test_generate_deck_output_passes_the_loaders_startup_check(
    generated_dir: Path,
) -> None:
    """What the generator writes is what the loader accepts."""
    assert verify_card_assets(generated_dir) is None


def test_generate_deck_is_deterministic(tmp_path: Path, generated_dir: Path) -> None:
    """Two runs of the script produce byte-identical bitmaps."""
    again = tmp_path / "again"
    gen_card_bitmaps.generate_deck(again)

    differing = [
        name
        for name in ALL_CARD_FILES
        if (again / name).read_bytes() != (generated_dir / name).read_bytes()
    ]

    assert differing == []
