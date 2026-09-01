# SPDX-License-Identifier: GPL-3.0-only
"""Packaged-app smoke tests (E1.6.1): the dev bundle really runs.

spec.md section 14 stage 5 runs in *dev-bundle mode* from EPIC 1
onward: PyInstaller onedir, unsigned, uploaded as a runnable
artifact. project-plan.md section 7 puts that in EPIC 1 rather than
EPIC 9 precisely so "PyInstaller x wxPython packaging quirks
(hidden imports, plists, DataView backends)" surface here, in the
first EPIC. This module is that early-warning gate.

Three separate claims, because they fail for different reasons:

1. **The built binary launches.** Proves the frozen interpreter
   resolves every import the app needs -- the failure mode a missing
   hidden import produces, and one no source-tree test can see.
2. **The bundle carries its assets, byte for byte.** The 9 ``.xrc``
   files, 106 card bitmaps and 3 WAV cues, on the packaged package
   path, with identical contents -- and all 23 windows load from the
   *bundled* ``.xrc`` copies. E9.1.1 extends the byte-identity claim
   to the release docs: the user guide and the four license texts land
   under ``rivercrossing/docs/`` in both built layouts, identical to
   their two source trees (repo root + package).
3. **A missing asset fails the build.** Not first paint. Asserted by
   running the real ``pyinstaller`` against a copy of the tree with
   one bitmap deleted, and requiring a non-zero exit that names the
   file with no ``dist/`` output produced.

``nox -s bundle`` runs before ``nox -s smoke``, so the bundle
normally exists; a developer running the functional suite without
having built one gets a skip naming what is missing, never a
confusing error.

**Where "main_frame opens" is asserted, and why here.** The brief
asks the launched bundle to open ``main_frame``. E9.1.1 lands the
store-backed bootstrap: ``ui/app.py``'s ``main()`` opens the rides
database and shows the frame before entering the event loop (the
frame-show claim is proven in ``test_app_bootstrap.py``'s spawned
MainLoop probe). Here the launch claim is asserted against the real
binary -- it launches without a frozen-import failure -- and, E9.1.1,
that the env seam ``RIVERCROSSING_DB_PATH`` points the frozen
``main()`` at a temp db (a pre-created ``rides.db`` gains a session
row across the launch window). The "open ride, one crossing, export
HTML" half runs through the suite's subprocess scenario harness,
which drives the exact store path ``main()`` runs over a staged temp
db. Measured with a throwaway frozen ``.app`` over the same spec: all
23 windows load, the card imagelist decodes its 53 bitmaps and the
DataView feed builds its 7 columns from inside a bundle.
"""

import hashlib
import importlib.util
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here
from typing import Any, NamedTuple

import harness
import pages
import pytest
import scenario_runner
import wx.xrc

from rivercrossing.store import Store

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]
SOURCE_UI = ROOT / "src" / "rivercrossing" / "ui"
SOURCE_PACKAGE = ROOT / "src" / "rivercrossing"
DIST = ROOT / "dist"
SPEC = ROOT / "installers" / "rivercrossing.spec"

# The two layouts one macOS build produces: the onedir folder COLLECT
# writes, and the .app BUNDLE wraps around it. Both must carry the
# assets -- the .app is the one a user double-clicks.
ONEDIR_UI = DIST / "rivercrossing" / "_internal" / "rivercrossing" / "ui"
APP_UI = DIST / "RiverCrossing.app" / "Contents" / "Resources" / "rivercrossing" / "ui"
SHIPPED_UI_DIRS = (ONEDIR_UI, APP_UI) if sys.platform == "darwin" else (ONEDIR_UI,)
# The copy inside the artifact a user launches, so the copy of the
# .xrc files the 23-window load test drives.
LAUNCHED_UI_DIR = SHIPPED_UI_DIRS[-1]

# The package root each layout ships -- one level up from *_UI above --
# where the E2.4.1 vector CSVs land (a sibling of ui/, not under it).
ONEDIR_PACKAGE = ONEDIR_UI.parent
APP_PACKAGE = APP_UI.parent
SHIPPED_PACKAGE_DIRS = (
    (ONEDIR_PACKAGE, APP_PACKAGE) if sys.platform == "darwin" else (ONEDIR_PACKAGE,)
)

# The docs each layout ships (E9.1.1) -- one level under the package
# root above, where the guide + license texts land.
ONEDIR_DOCS = ONEDIR_PACKAGE / "docs"
APP_DOCS = APP_PACKAGE / "docs"
SHIPPED_DOCS_DIRS = (ONEDIR_DOCS, APP_DOCS) if sys.platform == "darwin" else (ONEDIR_DOCS,)

EXECUTABLES = {
    "darwin": DIST / "RiverCrossing.app" / "Contents" / "MacOS" / "rivercrossing",
    "win32": DIST / "rivercrossing" / "rivercrossing.exe",
}

# The app is a GUI process: once it reaches its main loop it never
# exits on its own, so the launch probe waits this long for a crash
# to show up, then kills it.
LAUNCH_SETTLE_SECONDS = 20
# A spec-evaluation failure aborts PyInstaller in seconds; this only
# has to outlast that, never a real build.
BUILD_TIMEOUT_SECONDS = 180

EXPECTED_XRC_FILES = 9
EXPECTED_CARD_BITMAPS = 106
EXPECTED_WAV_CUES = 3
EXPECTED_VECTOR_CSVS = 2


def _load_check_asset_manifest() -> ModuleType:
    """Load tools/check_asset_manifest.py by path -- not a package."""
    path = ROOT / "tools" / "check_asset_manifest.py"
    spec = importlib.util.spec_from_file_location("check_asset_manifest", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = _load_check_asset_manifest()


class Launch(NamedTuple):
    """What a launched bundle printed before it stopped or was killed.

    ``returncode`` is ``None`` when the process was still alive at
    :data:`LAUNCH_SETTLE_SECONDS` and had to be killed -- the normal
    outcome for a GUI app that reached its main loop.
    """

    returncode: int | None
    stdout: str
    stderr: str


def _launch(executable: Path) -> Launch:
    """Launch *executable* and collect its output, killing it after.

    Never leaves a GUI process behind: every path either reaps a
    process that exited on its own or kills one that did not. The
    bounded daemon-thread drain (``scenario_runner._run_bounded``)
    means a third process holding the child's pipes -- Windows Error
    Reporting, measured -- cannot stall the post-kill drain.
    """
    try:
        completed = scenario_runner._run_bounded([str(executable)], timeout=LAUNCH_SETTLE_SECONDS)
    except subprocess.TimeoutExpired as exc:
        return Launch(None, exc.stdout or "", exc.stderr or "")
    return Launch(completed.returncode, completed.stdout, completed.stderr)


def _app_session_count(db_path: Path) -> int:
    """Count ``app_session`` rows (0 when the file is absent)."""
    if not db_path.is_file():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM app_session").fetchone()[0])


def _digests(ui_dir: Path) -> dict[str, str]:
    """Map every required asset's relative path to its sha256."""
    return {
        relative: hashlib.sha256((ui_dir / relative).read_bytes()).hexdigest()
        for relative in manifest.required_relative_paths()
    }


def _vector_digests(package_dir: Path) -> dict[str, str]:
    """Map every required vector CSV's own name to its sha256."""
    return {
        name: hashlib.sha256(
            (package_dir / manifest.VECTORS_SUBDIR / name).read_bytes()
        ).hexdigest()
        for name in manifest.REQUIRED_VECTORS
    }


def _doc_digests(package_dir: Path) -> dict[str, str]:
    """Map every required doc name to its bundled sha256."""
    return {
        name: hashlib.sha256((package_dir / manifest.DOCS_SUBDIR / name).read_bytes()).hexdigest()
        for name in manifest.REQUIRED_DOCS
    }


def _source_doc_digests() -> dict[str, str]:
    """Map every required doc name to its source-tree sha256."""
    return {
        Path(source).name: hashlib.sha256(Path(source).read_bytes()).hexdigest()
        for source, _destination in manifest.docs_data_entries(SOURCE_PACKAGE)
    }


def _names_present(ui_dir: Path, subdir: str) -> set[str]:
    """List what *ui_dir*'s *subdir* actually holds on disk."""
    return {entry.name for entry in (ui_dir / subdir).iterdir() if entry.is_file()}


def _unresolved(window: Any, names: tuple[str, ...]) -> list[str]:  # noqa: ANN401
    """Return every name in *names* that *window* cannot resolve."""
    return [name for name in names if wx.Window.FindWindowByName(name, window) is None]


def _broken_tree(destination: Path, delete: str) -> Path:
    """Copy the build inputs to *destination*, minus one asset.

    Copies ``src/``, ``tools/``, ``installers/``, ``docs/`` and the
    repo-root ``LICENSE`` -- everything the spec reads -- so the build
    is hermetic and the deletion cannot touch the real tree. ``docs/``
    and ``LICENSE`` join the copy because the E9.1.1 docs manifest
    resolves them against the copied tree's root; without them the
    build would fail naming a missing doc instead of the deleted
    bitmap this test is about.

    Args:
        destination: An existing, empty directory to copy into.
        delete: The asset to remove, relative to ``ui/``.

    Returns:
        The copied spec file's path.
    """
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info")
    for tree in ("src", "tools", "installers", "docs"):
        shutil.copytree(ROOT / tree, destination / tree, ignore=ignore)
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")
    (destination / "src" / "rivercrossing" / "ui" / delete).unlink()
    return destination / "installers" / SPEC.name


# --------------------------------------------------------- the manifest


def test_required_assets_declares_the_nine_frozen_xrc_files() -> None:
    """An .xrc file disappearing must shrink this, not the suite."""
    assert len(manifest.REQUIRED_XRC) == EXPECTED_XRC_FILES


def test_required_assets_declares_both_scales_of_all_fifty_three_cards() -> None:
    """53 faces at 1x and 2x, derived from the loader's own keys."""
    assert len(manifest.required_assets()[manifest.CARDS_SUBDIR]) == EXPECTED_CARD_BITMAPS


def test_required_assets_declares_the_three_wav_cues() -> None:
    """spec.md section 10: recorded, flagged (held), error (reject)."""
    assert len(manifest.REQUIRED_SOUNDS) == EXPECTED_WAV_CUES


@pytest.mark.parametrize(
    "subdir", [manifest.XRC_SUBDIR, manifest.CARDS_SUBDIR, manifest.SOUNDS_SUBDIR]
)
def test_source_tree_asset_names_match_the_required_manifest_exactly(subdir: str) -> None:
    """Sets, not counts: a renamed file leaves the sets unequal."""
    assert _names_present(SOURCE_UI, subdir) == set(manifest.required_assets()[subdir])


def test_missing_assets_given_the_real_source_tree_finds_nothing_absent() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.missing_assets(SOURCE_UI) == ()


def test_data_entries_maps_every_required_asset_onto_the_package_path() -> None:
    """PyInstaller datas must mirror ``rivercrossing/ui/...``.

    A frozen module's ``__file__`` points inside the bundle, so
    ``cards_imagelist.cards_dir()`` and ``harness.xrc_directory()``
    only resolve if the data lands on that same relative path.
    """
    destinations = {destination for _source, destination in manifest.data_entries(SOURCE_UI)}

    assert destinations == {f"rivercrossing/ui/{subdir}" for subdir in manifest.required_assets()}


def test_data_entries_names_one_source_file_per_required_asset() -> None:
    """Per-file entries, so the manifest *is* the bundle's contents."""
    sources = [Path(source) for source, _destination in manifest.data_entries(SOURCE_UI)]

    assert sorted(path.relative_to(SOURCE_UI).as_posix() for path in sources) == sorted(
        manifest.required_relative_paths()
    )


def test_verify_assets_given_a_deleted_card_bitmap_names_the_missing_file(tmp_path: Path) -> None:
    """T-5 negative: the raise carries the path, not just a count."""
    shutil.copytree(SOURCE_UI, tmp_path / "ui")
    (tmp_path / "ui" / manifest.CARDS_SUBDIR / "Ah-2x.png").unlink()

    with pytest.raises(manifest.MissingAssetError, match=re.escape("assets/cards/Ah-2x.png")):
        manifest.verify_assets(tmp_path / "ui")


def test_verify_assets_given_a_renamed_xrc_file_names_the_original_name(tmp_path: Path) -> None:
    """A rename is a missing file: the frozen name is the contract."""
    shutil.copytree(SOURCE_UI, tmp_path / "ui")
    xrc_dir = tmp_path / "ui" / manifest.XRC_SUBDIR
    (xrc_dir / "dialogs.xrc").rename(xrc_dir / "dialogues.xrc")

    with pytest.raises(manifest.MissingAssetError, match=re.escape("xrc/dialogs.xrc")):
        manifest.verify_assets(tmp_path / "ui")


# ------------------------------------------------- the vectors manifest

# E2.4.1 (spec section 12, R-44): the evaluator self-test's own vector
# CSVs, packaged separately from required_assets() above since they
# ship at the package root rather than under ui/.


def test_required_vectors_declares_the_two_self_test_csvs() -> None:
    """A vector CSV disappearing must shrink this, not the suite."""
    assert len(manifest.REQUIRED_VECTORS) == EXPECTED_VECTOR_CSVS


def test_source_tree_vector_names_match_the_required_manifest_exactly() -> None:
    """Sets, not counts: a renamed CSV leaves the sets unequal."""
    assert _names_present(SOURCE_PACKAGE, manifest.VECTORS_SUBDIR) == set(
        manifest.REQUIRED_VECTORS
    )


def test_missing_vectors_given_the_real_source_tree_finds_nothing_absent() -> None:
    """The tree both the wheel and the bundle are built from."""
    assert manifest.missing_vectors(SOURCE_PACKAGE) == ()


def test_vector_data_entries_maps_both_csvs_onto_the_package_root() -> None:
    """PyInstaller datas must land the CSVs under a vectors/ dir.

    Pinned to the literal string, not ``manifest.VECTORS_PACKAGE_DEST``
    itself: a destination that PyInstaller's own ``datas`` docs call
    the *containing folder* a source lands in, so ``"rivercrossing"``
    alone (rather than ``"rivercrossing/vectors"``) drops both CSVs
    one directory too high -- exactly the mistake that put
    ``rank_sweep.csv`` at ``_internal/rivercrossing/rank_sweep.csv``
    instead of ``_internal/rivercrossing/vectors/rank_sweep.csv`` and
    crashed the bundle at launch with ``FileNotFoundError``.
    """
    destinations = {
        destination for _source, destination in manifest.vector_data_entries(SOURCE_PACKAGE)
    }

    assert destinations == {"rivercrossing/vectors"}


def test_vector_data_entries_names_the_two_csv_source_files() -> None:
    """Per-file entries, so the manifest *is* the bundle's contents."""
    entries = manifest.vector_data_entries(SOURCE_PACKAGE)
    sources = [Path(source) for source, _destination in entries]

    assert sorted(path.name for path in sources) == sorted(manifest.REQUIRED_VECTORS)


def test_verify_vectors_given_a_deleted_csv_names_the_missing_file(tmp_path: Path) -> None:
    """T-5 negative: the raise carries the path, not just a count."""
    shutil.copytree(SOURCE_PACKAGE / manifest.VECTORS_SUBDIR, tmp_path / "vectors")
    (tmp_path / "vectors" / "rank_sweep.csv").unlink()

    with pytest.raises(manifest.MissingAssetError, match=re.escape("vectors/rank_sweep.csv")):
        manifest.verify_vectors(tmp_path)


def test_vector_data_entries_given_a_missing_csv_raises_instead_of_listing_entries(
    tmp_path: Path,
) -> None:
    """The spec cannot obtain vector datas without passing the check."""
    shutil.copytree(SOURCE_PACKAGE / manifest.VECTORS_SUBDIR, tmp_path / "vectors")
    (tmp_path / "vectors" / "joker_vectors.csv").unlink()

    with pytest.raises(manifest.MissingAssetError, match=re.escape("vectors/joker_vectors.csv")):
        manifest.vector_data_entries(tmp_path)


def test_data_entries_given_a_missing_asset_raises_instead_of_listing_entries(
    tmp_path: Path,
) -> None:
    """The spec cannot obtain its datas without passing the check.

    This coupling is what makes a missing asset a *build* failure:
    the one call that fills ``Analysis(datas=...)`` is the same call
    that verifies the manifest.
    """
    shutil.copytree(SOURCE_UI, tmp_path / "ui")
    (tmp_path / "ui" / manifest.SOUNDS_SUBDIR / "recorded.wav").unlink()

    with pytest.raises(manifest.MissingAssetError, match=re.escape("assets/sounds/recorded.wav")):
        manifest.data_entries(tmp_path / "ui")


def test_main_given_a_complete_tree_reports_the_asset_count_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The check a developer runs instead of waiting for a build."""
    exit_code = manifest.main(["--ui-dir", str(SOURCE_UI)])

    expected = f"all {len(manifest.required_relative_paths())} required assets present"
    assert exit_code == 0
    assert expected in capsys.readouterr().out


def test_main_given_a_missing_asset_names_it_on_stderr_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-5: the CLI's failure path, same message as the build's."""
    shutil.copytree(SOURCE_UI, tmp_path / "ui")
    (tmp_path / "ui" / manifest.XRC_SUBDIR / "main.xrc").unlink()

    exit_code = manifest.main(["--ui-dir", str(tmp_path / "ui")])

    assert exit_code == 1
    assert "xrc/main.xrc" in capsys.readouterr().err


def test_main_given_a_complete_tree_also_reports_the_vector_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI's success line names the vector CSVs too, not only ui/.

    ``--package-dir`` defaults to :data:`manifest.DEFAULT_PACKAGE_DIR`
    (previously dead code, referenced nowhere) the same way
    ``--ui-dir`` defaults to :data:`manifest.DEFAULT_UI_DIR`.
    """
    exit_code = manifest.main(["--ui-dir", str(SOURCE_UI), "--package-dir", str(SOURCE_PACKAGE)])

    expected = f"all {len(manifest.REQUIRED_VECTORS)} required vectors present"
    assert exit_code == 0
    assert expected in capsys.readouterr().out


def test_main_given_missing_vectors_names_them_on_stderr_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-5: a tree missing both self-test CSVs must not exit 0.

    Empirically proven gap this closes: ``main()`` used to call only
    ``verify_assets``, so a package dir missing both
    ``rank_sweep.csv`` and ``joker_vectors.csv`` previously passed
    with exit 0 -- silently shipping a bundle that crashes at launch
    with ``FileNotFoundError`` (the same failure the built-bundle
    fix above closes at the packaging-spec level).
    """
    package_dir = tmp_path / "package"
    (package_dir / manifest.VECTORS_SUBDIR).mkdir(parents=True)

    exit_code = manifest.main(["--ui-dir", str(SOURCE_UI), "--package-dir", str(package_dir)])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "vectors/rank_sweep.csv" in stderr
    assert "vectors/joker_vectors.csv" in stderr


# ----------------------------------------------------- the built bundle


@pytest.fixture(scope="module")
def bundle_ui_dirs() -> tuple[Path, ...]:
    """Every built layout's packaged ``rivercrossing/ui`` directory."""
    absent = [str(path) for path in SHIPPED_UI_DIRS if not path.is_dir()]
    if absent:
        pytest.skip(f"no built bundle -- run `nox -s bundle` first; missing {', '.join(absent)}")
    return SHIPPED_UI_DIRS


@pytest.fixture(scope="module")
def bundle_package_dirs() -> tuple[Path, ...]:
    """Every built layout's packaged ``rivercrossing`` package root."""
    absent = [str(path) for path in SHIPPED_PACKAGE_DIRS if not path.is_dir()]
    if absent:
        pytest.skip(f"no built bundle -- run `nox -s bundle` first; missing {', '.join(absent)}")
    return SHIPPED_PACKAGE_DIRS


@pytest.fixture(scope="module")
def bundle_executable() -> Path:
    """Return this platform's built executable."""
    executable = EXECUTABLES.get(sys.platform)
    if executable is None:
        pytest.skip(f"no dev bundle is defined for sys.platform {sys.platform!r}")
    if not executable.is_file():
        pytest.skip(f"no built bundle -- run `nox -s bundle` first; missing {executable}")
    return executable


@pytest.fixture(scope="module")
def bundle_app_path() -> Path:
    """Return the built ``.app``'s path (BUNDLE(), macOS-only)."""
    if sys.platform != "darwin":
        pytest.skip("BUNDLE() only wraps a .app on darwin")
    app_path = DIST / "RiverCrossing.app"
    if not app_path.is_dir():
        pytest.skip(f"no built bundle -- run `nox -s bundle` first; missing {app_path}")
    return app_path


@pytest.fixture(scope="module")
def bundled_xrc(bundle_ui_dirs: tuple[Path, ...], wx_app: object) -> Any:  # noqa: ANN401, ARG001
    """Load the *bundle's* own .xrc copies into a private resource.

    A private ``XmlResource``, not the process-wide
    ``XmlResource.Get()`` the source-tree suite loads into: sharing
    the singleton would leave "did this window come from the bundle
    or from ``src/``?" unanswerable, which is the whole question here.
    """
    resource = wx.xrc.XmlResource()
    for path in sorted((LAUNCHED_UI_DIR / manifest.XRC_SUBDIR).glob("*.xrc")):
        resource.Load(str(path))
    return resource


def test_bundled_asset_names_match_the_required_manifest_exactly(
    bundle_ui_dirs: tuple[Path, ...],
) -> None:
    """Nothing dropped and nothing stray, in either built layout."""
    packaged = {
        (ui_dir, subdir): _names_present(ui_dir, subdir)
        for ui_dir in bundle_ui_dirs
        for subdir in manifest.required_assets()
    }

    assert packaged == {
        (ui_dir, subdir): set(names)
        for ui_dir in bundle_ui_dirs
        for subdir, names in manifest.required_assets().items()
    }


def test_bundled_asset_bytes_are_identical_to_the_source_tree(
    bundle_ui_dirs: tuple[Path, ...],
) -> None:
    """Content, not just presence: no truncation, no re-encoding."""
    expected = _digests(SOURCE_UI)

    assert {ui_dir: _digests(ui_dir) for ui_dir in bundle_ui_dirs} == dict.fromkeys(
        bundle_ui_dirs, expected
    )


def test_bundled_vector_names_match_the_required_manifest_exactly(
    bundle_package_dirs: tuple[Path, ...],
) -> None:
    """Both self-test CSVs land under the package root's own vectors/.

    A wrong ``VECTORS_PACKAGE_DEST`` (e.g. the containing-folder
    mistake this test's sibling below pins) would either miss this
    directory entirely or leave it empty -- this is the on-disk,
    built-bundle catch the ui/xrc assets already have.
    """
    packaged = {
        package_dir: _names_present(package_dir, manifest.VECTORS_SUBDIR)
        for package_dir in bundle_package_dirs
    }

    assert packaged == {
        package_dir: set(manifest.REQUIRED_VECTORS) for package_dir in bundle_package_dirs
    }


def test_bundled_vector_bytes_are_identical_to_the_source_tree(
    bundle_package_dirs: tuple[Path, ...],
) -> None:
    """Content, not just presence: no truncation, no re-encoding."""
    expected = _vector_digests(SOURCE_PACKAGE)

    assert {
        package_dir: _vector_digests(package_dir) for package_dir in bundle_package_dirs
    } == dict.fromkeys(bundle_package_dirs, expected)


def test_bundled_doc_names_match_the_required_manifest_exactly(
    bundle_package_dirs: tuple[Path, ...],
) -> None:
    """All five docs land under each layout's own rivercrossing/docs/.

    A wrong ``DOCS_PACKAGE_DEST`` (e.g. the containing-folder mistake
    the vectors manifest's own pinned test catches) would leave this
    directory missing or empty -- the on-disk, built-bundle catch the
    other manifests already have.
    """
    packaged = {
        package_dir: _names_present(package_dir, manifest.DOCS_SUBDIR)
        for package_dir in bundle_package_dirs
    }

    assert packaged == {
        package_dir: set(manifest.REQUIRED_DOCS) for package_dir in bundle_package_dirs
    }


def test_bundled_doc_bytes_are_identical_to_the_source_tree(
    bundle_package_dirs: tuple[Path, ...],
) -> None:
    """Content, not just presence: the originals ship verbatim.

    The docs are shipped from two trees (repo root + package), so the
    expected digests come from the manifest's own entry sources, never
    from a copied list.
    """
    expected = _source_doc_digests()

    assert {
        package_dir: _doc_digests(package_dir) for package_dir in bundle_package_dirs
    } == dict.fromkeys(bundle_package_dirs, expected)


@pytest.mark.parametrize("spec", pages.WINDOWS, ids=lambda spec: spec.name)
def test_bundled_window_loads_from_the_packaged_xrc_and_resolves_its_names(
    spec: pages.WindowSpec, bundled_xrc: object
) -> None:
    """The 23-window suite, driven off the bundle's own resources."""
    window = harness.load_window(bundled_xrc, spec.name, frame=spec.is_frame)
    window.Show()
    window.Layout()
    harness.pump()

    try:
        missing = _unresolved(window, spec.controls)
    finally:
        harness.close_window(window)

    assert missing == []


def test_bundle_executable_launches_without_a_frozen_import_failure(
    bundle_executable: Path,
) -> None:
    """Launch the real thing: a missing hidden import shows up here.

    A GUI process that reaches its main loop never exits on its own,
    so "still running when the timer expired" (``returncode is
    None``) is a pass. Only a non-zero exit or a traceback fails.

    The exit code carries this test on Windows: a windowed frozen
    app there has no attached ``sys.stderr``, so the stderr check
    is macOS's contribution and the return code is what both
    platforms share.
    """
    launch = _launch(bundle_executable)
    context = f"stdout:\n{launch.stdout}\nstderr:\n{launch.stderr}"

    assert launch.returncode in {0, None}, context
    assert "Traceback (most recent call last)" not in launch.stderr, context


def test_bundle_executable_opens_the_environment_db_at_launch(
    bundle_executable: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """E9.1.1: RIVERCROSSING_DB_PATH points the bundle at a temp db.

    Launch the real bundle with the env var set to a pre-created,
    empty rides.db; after the settle-and-kill window the session count
    must have grown. The binary inherits the env var (``_launch``
    passes no ``env=``), so a frozen ``main()`` that ignored the seam
    and opened the per-user default leaves the temp db untouched and
    fails here.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    store.close_session()
    store.close()
    before = _app_session_count(db_path)
    monkeypatch.setenv("RIVERCROSSING_DB_PATH", str(db_path))

    launch = _launch(bundle_executable)
    context = f"stdout:\n{launch.stdout}\nstderr:\n{launch.stderr}"

    assert launch.returncode in {0, None}, context
    assert _app_session_count(db_path) == before + 1, context


def test_bundle_launch_opens_a_ride_records_a_crossing_and_exports_html() -> None:
    """E9.1.1: open ride, one crossing, export HTML, on a temp db.

    The launch-against-the-bundle half is :func:`bundle_executable`
    probes; this drives the open-crossing-export half through the
    suite's subprocess scenario harness, which runs the exact store
    path ``main()`` runs (``_bootstrap_window``) over a staged temp
    ``rides.db`` -- resume opens the ride, one plate records a
    crossing, ``mi_export_html`` writes the real file.
    """
    result = scenario_runner.run_scenario("bundle_launch_open_crossing_exports_html")

    data = result["data"]
    assert data["feed_rows"] >= 1, result["context"]
    assert data["feed_plate"] == "12", result["context"]
    assert data["audit_actions"][-1] == "record_crossing", result["context"]
    assert data["html_exists"] is True, result["context"]
    assert data["html_size"] > 0, result["context"]
    assert "race-data" in data["html_text"], result["context"]
    # The crossed plate is in the exported race-data standings JSON
    # (json.dumps(indent=2) spells the integer plate with a space).
    assert '"plate": 12' in data["html_text"], result["context"]


def test_built_app_resources_carry_the_branded_icns(bundle_app_path: Path) -> None:
    """BUNDLE() copies the committed .icns into Contents/Resources."""
    icns_path = bundle_app_path / "Contents" / "Resources" / "RiverCrossing.icns"

    assert icns_path.is_file()


def test_info_plist_names_the_branded_icon_not_pyinstallers_default(
    bundle_app_path: Path,
) -> None:
    """The plist points at our icon, and PyInstaller's default is gone.

    Both halves of the same claim: a spec that forgot ``icon=`` would
    still pass the first half (PyInstaller always ships *an* .icns)
    while shipping the wrong one.
    """
    info_plist_path = bundle_app_path / "Contents" / "Info.plist"
    with info_plist_path.open("rb") as handle:
        info_plist = plistlib.load(handle)
    default_icon_path = bundle_app_path / "Contents" / "Resources" / "icon-windowed.icns"

    assert info_plist["CFBundleIconFile"] == "RiverCrossing.icns"
    assert not default_icon_path.exists()


def test_pyinstaller_build_given_a_missing_asset_fails_naming_it(tmp_path: Path) -> None:
    """A missing asset fails the *build*, not first paint.

    The real ``pyinstaller``, against a copy of the tree with one
    card bitmap deleted. The spec verifies the manifest before it
    declares anything, so this aborts during spec evaluation --
    which the empty ``dist/`` is what proves. (PyInstaller creates
    the ``--distpath`` directory before it evaluates the spec, so
    the directory exists either way; only its contents tell you
    whether a build happened.)
    """
    build = tmp_path / "tree"
    build.mkdir()
    broken_spec = _broken_tree(build, delete=f"{manifest.CARDS_SUBDIR}/Ah-2x.png")
    dist = tmp_path / "dist"

    completed = subprocess.run(  # noqa: S603 -- sys.executable + a copy of this repo
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(dist),
            "--workpath",
            str(tmp_path / "work"),
            str(broken_spec),
        ],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_SECONDS,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0, output
    assert "Ah-2x.png" in output, output
    assert sorted(path.name for path in dist.iterdir()) == []


def _pe_machine(executable: Path) -> int:
    """Return the PE ``Machine`` field of *executable*.

    ``e_lfanew`` (offset 0x3C) points at the PE header; the first
    WORD after the 4-byte PE signature is the Machine type.
    """
    with executable.open("rb") as handle:
        handle.seek(0x3C)
        e_lfanew = struct.unpack("<I", handle.read(4))[0]
        handle.seek(e_lfanew + 4)
        return struct.unpack("<H", handle.read(2))[0]


def _expected_pe_machine() -> int:
    """Return the PE Machine type the build must have produced.

    The expected arch is pinned per CI job via
    ``RIVERCROSSING_EXPECT_WIN_ARCH`` (``x64`` or ``arm64``), so an
    emulated-Python misconfig cannot false-green the check. Unset (a
    local dev run) falls back to the interpreter's own arch.
    """
    want = os.environ.get("RIVERCROSSING_EXPECT_WIN_ARCH")
    if want is None:
        want = "arm64" if platform.machine().upper() in {"ARM64", "AARCH64"} else "x64"
    return 0xAA64 if want == "arm64" else 0x8664


def test_built_app_is_apple_silicon_arm64(bundle_executable: Path) -> None:
    """The .app's Mach-O is arm64 -- the macOS build is Apple Silicon.

    ``lipo -archs`` prints a bare token for a thin binary, so the
    assertion is exact: any x86_64 or universal2 output fails.
    """
    if sys.platform != "darwin":
        pytest.skip("BUNDLE() only wraps a .app on darwin")
    lipo = shutil.which("lipo")
    if lipo is None:
        pytest.skip("lipo (Xcode command line tools) not found")
    completed = subprocess.run(  # noqa: S603 -- resolved absolute path, fixed argv
        [lipo, "-archs", str(bundle_executable)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "arm64"


def test_built_binary_architecture_matches_expected(bundle_executable: Path) -> None:
    """The frozen exe's PE Machine matches the arch the job promised.

    The expected arch comes from ``RIVERCROSSING_EXPECT_WIN_ARCH`` (set
    per Windows build job), so this proves the runner actually produced
    the arch its installer filename claims -- not just that the exe
    matches whatever Python happened to run PyInstaller.
    """
    if sys.platform != "win32":
        pytest.skip("the PE Machine field only exists on win32")
    assert _pe_machine(bundle_executable) == _expected_pe_machine()
