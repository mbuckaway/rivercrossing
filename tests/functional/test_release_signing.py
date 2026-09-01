# SPDX-License-Identifier: GPL-3.0-only
"""Signed-release smoke tests (E9.1.3): the Developer ID path is real.

task-briefs.md's E9.1.3 owns the signing half of stage 6: Developer
ID codesign, notarytool staple and the ``spctl`` gate, plus launching
the ``.app`` from the mounted image -- the half ``test_dmg_smoke.py``
deliberately excludes while the app is unsigned (Gatekeeper/
translocation). The Apple signing credentials are org-supplied under
the E1.1.2 contract, so until a signed artifact exists every test
here skips with a reason naming what is missing; the unsigned path
ships meanwhile and the signed path is green once creds land.

Two claims, because they fail for different reasons:

1. **Gatekeeper accepts the signed ``.app``.** ``spctl --assess
   --type open --context context:primary-signature`` is the
   user-facing "can this be opened" check a Developer ID signature
   has to pass. The dev bundle's adhoc signature (PyInstaller's
   default) is rejected, so the test skips unless a real Developer ID
   signed ``.app`` exists.
2. **The app launches from the read-only mounted image.** ``nox -s
   dmg`` bakes ``dist/RiverCrossing-<version>.dmg``; the fixture
   mounts it exactly as a user's Finder would (``hdiutil attach
   -nobrowse -readonly``) and the launch uses the same bounded
   settle-and-kill probe as ``test_bundle_smoke.py``'s ``_launch``.
   Launching an unsigned app from a mount is exactly the
   Gatekeeper/translocation case the unsigned suite avoids, so this
   skips unless the mounted ``.app`` carries a Developer ID
   signature.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest
import scenario_runner

import rivercrossing

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
APP_PATH = DIST / "RiverCrossing.app"
DMG_PATH = DIST / f"RiverCrossing-{rivercrossing.__version__}.dmg"

CODESIGN = "/usr/bin/codesign"
HDIUTIL = "/usr/bin/hdiutil"
SPCTL = "/usr/sbin/spctl"

# The app is a GUI process: once it reaches its main loop it never
# exits on its own, so the launch probe waits this long for a crash
# to show up, then kills it. Mirrors test_bundle_smoke.py's constant.
LAUNCH_SETTLE_SECONDS = 20

# hdiutil occasionally reports a just-attached read-only volume as
# busy on hosted runners (project-plan.md §4's flake note); bounded
# retries absorb that without leaving a mount behind on a real
# failure. Mirrors test_dmg_smoke.py's constants.
DETACH_MAX_ATTEMPTS = 5
DETACH_RETRY_SECONDS = 1


def _developer_id_signature(app_path: Path) -> bool:
    """Return whether *app_path* carries a Developer ID signature.

    ``codesign -dv`` prints its verdict to stderr; the unsigned dev
    bundle reports ``Signature=adhoc``, while a Developer ID signed
    app names the ``Developer ID Application`` authority.
    """
    completed = subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        [CODESIGN, "-dv", str(app_path)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return False
    return "Developer ID Application" in completed.stderr


def _missing_signed_app_reason() -> str | None:
    """Return why no signed ``.app`` exists, or None when it does.

    The skip reasons name the missing artifact/credentials so a bare
    host run reports exactly what would have to land for the signed
    path to engage.
    """
    if sys.platform != "darwin":
        return "Developer ID signing only runs on darwin"
    if not APP_PATH.is_dir():
        return f"no built .app -- run `nox -s bundle` first; missing {APP_PATH}"
    if not _developer_id_signature(APP_PATH):
        return (
            "no Developer ID-signed .app -- Apple signing creds are not "
            "configured (E1.1.2 contract); the dev bundle's adhoc signature "
            "cannot pass Gatekeeper"
        )
    return None


_SIGNED_APP_ABSENT_REASON = _missing_signed_app_reason()


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


def _launch(executable: Path) -> tuple[int | None, str, str]:
    """Launch *executable* and collect its output, killing it after.

    Returns ``(returncode, stdout, stderr)``; ``returncode`` is
    ``None`` when the process was still alive at
    :data:`LAUNCH_SETTLE_SECONDS` and had to be killed -- the normal
    outcome for a GUI app that reached its main loop.
    """
    try:
        completed = scenario_runner._run_bounded([str(executable)], timeout=LAUNCH_SETTLE_SECONDS)
    except subprocess.TimeoutExpired as exc:
        return None, exc.stdout or "", exc.stderr or ""
    return completed.returncode, completed.stdout, completed.stderr


@pytest.mark.skipif(
    _SIGNED_APP_ABSENT_REASON is not None,
    reason=_SIGNED_APP_ABSENT_REASON or "no signed artifact",
)
def test_signed_app_passes_spctl_assessment() -> None:
    """E9.1.3: Gatekeeper accepts the Developer ID-signed ``.app``."""
    completed = subprocess.run(  # noqa: S603 -- absolute path, fixed argv list, no shell
        [
            SPCTL,
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            str(APP_PATH),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.fixture(scope="module")
def signed_dmg_path() -> Path:
    """Return the built DMG's path, skipping if it can't exist yet."""
    if sys.platform != "darwin":
        pytest.skip("the DMG is only built on darwin")
    if not DMG_PATH.is_file():
        pytest.skip(f"no built DMG -- run `nox -s dmg` first; missing {DMG_PATH}")
    return DMG_PATH


@pytest.fixture
def mounted_signed_dmg(signed_dmg_path: Path, tmp_path: Path) -> Path:
    """Attach *signed_dmg_path* read-only and yield the mount point.

    Skips when the mounted image does not hold a Developer ID-signed
    ``.app``: launching an unsigned app from a read-only mount is the
    Gatekeeper/translocation case ``test_dmg_smoke.py`` deliberately
    avoids, and E9.1.3 only promises the launch once the signed path
    exists.
    """
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
            str(signed_dmg_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        app_path = mount_point / "RiverCrossing.app"
        if not app_path.is_dir():
            pytest.skip(f"mounted DMG carries no RiverCrossing.app -- missing {app_path}")
        if not _developer_id_signature(app_path):
            pytest.skip(
                "the mounted app is not Developer ID-signed -- Apple signing "
                "creds are absent (E1.1.2 contract); unsigned mounts are not "
                "launchable under Gatekeeper/translocation"
            )
        yield mount_point
    finally:
        _detach(mount_point)


def test_signed_app_launches_from_the_mounted_dmg(mounted_signed_dmg: Path) -> None:
    """E9.1.3: the signed app starts from the mounted image."""
    executable = mounted_signed_dmg / "RiverCrossing.app" / "Contents" / "MacOS" / "rivercrossing"
    returncode, stdout, stderr = _launch(executable)
    context = f"stdout:\n{stdout}\nstderr:\n{stderr}"

    assert returncode in {0, None}, context
    assert "Traceback (most recent call last)" not in stderr, context
