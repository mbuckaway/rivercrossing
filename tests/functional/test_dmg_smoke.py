# SPDX-License-Identifier: GPL-3.0-only
"""DMG smoke tests (Phase 8, 8.8.4, P8-D7): the built DMG is real.

``nox -s dmg`` builds ``dist/RiverCrossing-<version>.dmg`` from
``installers/dmg_settings.py``; this module mounts it exactly as a
user's Finder would, checks it carries what that settings file
planned, then detaches -- proving the artifact CI uploads is
actually drag-installable, not just present on disk.

Deliberately does **not** launch the ``.app`` from the read-only
mount: Gatekeeper/translocation behaviour for an unsigned app belongs
to the signing work in EPIC 9 (E9.1.3), not here.

Skips, rather than fails, when its prerequisite is absent: off
darwin (dmgbuild/hdiutil are macOS-only) and when nobody has run
``nox -s dmg`` yet -- the same conditional-artifact pattern
test_bundle_smoke.py's own ``bundle_executable`` fixture uses. On the
gating macOS CI job the artifact always exists, so nothing skips
where this suite actually gates.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

import rivercrossing

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
DMG_PATH = DIST / f"RiverCrossing-{rivercrossing.__version__}.dmg"

HDIUTIL = "/usr/bin/hdiutil"

# hdiutil occasionally reports a just-attached read-only volume as
# busy on hosted runners (project-plan.md §4's flake note); bounded
# retries absorb that without leaving a mount behind on a real
# failure.
DETACH_MAX_ATTEMPTS = 5
DETACH_RETRY_SECONDS = 1


@pytest.fixture(scope="module")
def dmg_path() -> Path:
    """Return the built DMG's path, skipping if it can't exist yet."""
    if sys.platform != "darwin":
        pytest.skip("the DMG is only built on darwin")
    if not DMG_PATH.is_file():
        pytest.skip(f"no built DMG -- run `nox -s dmg` first; missing {DMG_PATH}")
    return DMG_PATH


def _detach(mount_point: Path) -> None:
    """Detach *mount_point*, retrying and forcing as a last resort."""
    for attempt in range(DETACH_MAX_ATTEMPTS):
        is_last_attempt = attempt == DETACH_MAX_ATTEMPTS - 1
        cmd = [HDIUTIL, "detach", str(mount_point)]
        if is_last_attempt:
            cmd.append("-force")
        result = subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
            cmd, capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return
        time.sleep(DETACH_RETRY_SECONDS)


@pytest.fixture
def mounted_dmg(dmg_path: Path, tmp_path: Path) -> Path:
    """Attach *dmg_path* read-only and yield the mount point."""
    mount_point = tmp_path / "mnt"
    mount_point.mkdir()
    subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        [
            HDIUTIL,
            "attach",
            "-nobrowse",
            "-readonly",
            "-mountpoint",
            str(mount_point),
            str(dmg_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield mount_point
    finally:
        _detach(mount_point)


def test_dmg_filename_carries_the_package_version(dmg_path: Path) -> None:
    """The artifact name embeds rivercrossing.__version__."""
    assert dmg_path.name == f"RiverCrossing-{rivercrossing.__version__}.dmg"


def test_dmg_passes_hdiutil_verify(dmg_path: Path) -> None:
    """hdiutil itself confirms the image is not corrupt.

    Isolated from the mount/detach fixture on purpose (risk noted in
    the plan): a mount-side flake must never be misattributed to a
    corrupt image, or vice versa.
    """
    result = subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        [HDIUTIL, "verify", str(dmg_path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_mounted_dmg_carries_app_symlink_background_and_volume_icon(
    mounted_dmg: Path,
) -> None:
    """The mounted volume matches dmg_settings.py's planned contents."""
    app_path = mounted_dmg / "RiverCrossing.app"
    applications_link = mounted_dmg / "Applications"
    background_path = mounted_dmg / ".background" / "dmg_background.tiff"
    volume_icon_path = mounted_dmg / ".VolumeIcon.icns"

    assert app_path.is_dir()
    assert applications_link.is_symlink()
    assert applications_link.readlink() == Path("/Applications")
    assert background_path.is_file()
    assert volume_icon_path.is_file()
