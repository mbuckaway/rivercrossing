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
   *bundled* ``.xrc`` copies.
3. **A missing asset fails the build.** Not first paint. Asserted by
   running the real ``pyinstaller`` against a copy of the tree with
   one bitmap deleted, and requiring a non-zero exit that names the
   file with no ``dist/`` output produced.

``nox -s bundle`` runs before ``nox -s smoke``, so the bundle
normally exists; a developer running the functional suite without
having built one gets a skip naming what is missing, never a
confusing error.

**Where "main_frame opens" is asserted, and why here.** The brief
asks the launched bundle to open ``main_frame``. It cannot yet:
``ui/app.py``'s ``main()`` builds a ``wx.App`` and returns, with no
frame and no main loop, so the built binary launches and exits 0
without drawing anything. Until the bootstrap opens a window, the
claim is asserted where it can be: every window is loaded from the
bundle's *own* packaged ``.xrc`` copies, and those copies are
proven byte-identical to the source tree the stage-3 suite drives.
Measured with a throwaway frozen ``.app`` over the same spec: all
23 windows load, the card imagelist decodes its 53 bitmaps and the
DataView feed builds its 7 columns from inside a bundle. When the
bootstrap lands, add the window assertion to the launch test.
"""

import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here
from typing import Any, NamedTuple

import harness
import pages
import pytest
import wx.xrc

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]
SOURCE_UI = ROOT / "src" / "rivercrossing" / "ui"
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
    process that exited on its own or kills one that did not.
    """
    with subprocess.Popen(  # noqa: S603 -- a path this repo's own build produced
        [str(executable)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=LAUNCH_SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return Launch(None, stdout, stderr)
    return Launch(process.returncode, stdout, stderr)


def _digests(ui_dir: Path) -> dict[str, str]:
    """Map every required asset's relative path to its sha256."""
    return {
        relative: hashlib.sha256((ui_dir / relative).read_bytes()).hexdigest()
        for relative in manifest.required_relative_paths()
    }


def _names_present(ui_dir: Path, subdir: str) -> set[str]:
    """List what *ui_dir*'s *subdir* actually holds on disk."""
    return {entry.name for entry in (ui_dir / subdir).iterdir() if entry.is_file()}


def _unresolved(window: Any, names: tuple[str, ...]) -> list[str]:  # noqa: ANN401
    """Return every name in *names* that *window* cannot resolve."""
    return [name for name in names if wx.Window.FindWindowByName(name, window) is None]


def _broken_tree(destination: Path, delete: str) -> Path:
    """Copy the build inputs to *destination*, minus one asset.

    Copies ``src/``, ``tools/`` and ``installers/`` -- everything the
    spec reads -- so the build is hermetic and the deletion cannot
    touch the real tree.

    Args:
        destination: An existing, empty directory to copy into.
        delete: The asset to remove, relative to ``ui/``.

    Returns:
        The copied spec file's path.
    """
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info")
    for tree in ("src", "tools", "installers"):
        shutil.copytree(ROOT / tree, destination / tree, ignore=ignore)
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


# ----------------------------------------------------- the built bundle


@pytest.fixture(scope="module")
def bundle_ui_dirs() -> tuple[Path, ...]:
    """Every built layout's packaged ``rivercrossing/ui`` directory."""
    absent = [str(path) for path in SHIPPED_UI_DIRS if not path.is_dir()]
    if absent:
        pytest.skip(f"no built bundle -- run `nox -s bundle` first; missing {', '.join(absent)}")
    return SHIPPED_UI_DIRS


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
