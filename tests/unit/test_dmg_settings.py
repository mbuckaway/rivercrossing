# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for installers/dmg_settings.py (Phase 8, 8.8.3, P8-D7).

``dmgbuild`` execs the settings file with a ``defines`` dict already
present in its namespace, populated from ``-D key=value`` CLI flags.
A plain ``import`` has no such name, so the module must guard with
``defines = globals().get("defines", {})`` to stay import-safe -- the
property these tests depend on to load it at all.

Loaded by path, the same way tests/unit/test_ids_gen.py:28-39 loads
tools/gen_ids.py: ``installers/`` is a config tree, not an installed
package.
"""

import importlib.util
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

import pytest
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DMG_SETTINGS_PATH = _REPO_ROOT / "installers" / "dmg_settings.py"
_BRANDING_DIR = _REPO_ROOT / "installers" / "branding"
_BACKGROUND_TIFF_PATH = _BRANDING_DIR / "dmg_background.tiff"


def _load_dmg_settings(path: Path) -> ModuleType:
    """Load installers/dmg_settings.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("dmg_settings", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dmg_settings() -> ModuleType:
    """Import installers/dmg_settings.py once for every test here."""
    return _load_dmg_settings(_DMG_SETTINGS_PATH)


def test_window_rect_size_matches_the_background_tiff_one_x_page(
    dmg_settings: ModuleType,
) -> None:
    """The Finder window is sized exactly to the 1x background page.

    Couples the geometry to the committed art: a background re-render
    at a different size would make this fail rather than silently
    letterbox in Finder.
    """
    _origin, size = dmg_settings.window_rect
    with Image.open(_BACKGROUND_TIFF_PATH) as background:
        one_x_page_size = background.size

    assert size == one_x_page_size


def test_symlinks_map_applications_to_slash_applications(dmg_settings: ModuleType) -> None:
    """The drag target is real /Applications, not a relative link."""
    assert dmg_settings.symlinks == {"Applications": "/Applications"}


def test_volume_icon_and_background_point_at_committed_branding_files(
    dmg_settings: ModuleType,
) -> None:
    """Both the volume icon and background resolve to tracked art."""
    assert Path(dmg_settings.icon).is_file()
    assert Path(dmg_settings.background).is_file()


def test_format_is_udzo_and_files_lists_exactly_the_app(dmg_settings: ModuleType) -> None:
    """UDZO compression, and the only payload is the app bundle."""
    assert dmg_settings.format == "UDZO"
    assert dmg_settings.files == [dmg_settings.application]


def test_icon_locations_sit_inside_the_window_rect(dmg_settings: ModuleType) -> None:
    """Both drawn icon positions fall within the Finder window.

    dmgbuild writes ``icon_locations`` straight into each item's
    ``.DS_Store`` ``Iloc`` entry (dmgbuild/core.py), which Finder
    always reads relative to the window's own content area -- (0, 0)
    top-left -- never offset by ``window_rect``'s on-screen (x, y),
    which only places the window on the *screen*.
    """
    _origin, (width, height) = dmg_settings.window_rect
    positions = dmg_settings.icon_locations.values()

    assert all(0 <= x <= width and 0 <= y <= height for x, y in positions)


def test_default_app_path_is_the_dist_rivercrossing_app(dmg_settings: ModuleType) -> None:
    """With no ``-D app=...`` override, the default targets dist/."""
    assert dmg_settings.application == str(_REPO_ROOT / "dist" / "RiverCrossing.app")
