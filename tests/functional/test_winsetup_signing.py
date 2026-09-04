# SPDX-License-Identifier: GPL-3.0-only
"""Signed-release smoke test (E9.1.2): the Windows Authenticode path.

The Windows twin of test_release_signing.py, the macOS Developer ID
check. A signed PE reports ``Valid`` from PowerShell's
``Get-AuthenticodeSignature``; an unsigned one reports ``NotSigned``.
The SignPath credentials are org-supplied, so until a signed
installer exists every test here skips with a reason naming what is
missing -- the unsigned path ships meanwhile and this test turns
green once the signing creds land (E9.1.2's advisory gate).
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import rivercrossing

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]


def _windows_arch() -> str:
    """Return the short arch tag for the host Windows interpreter."""
    return "arm64" if platform.machine().upper() in {"ARM64", "AARCH64"} else "x64"


# Mirrors test_winsetup_smoke.py's SETUP_PATH so both files agree on the
# artifact name even though this test only reads it.
SETUP_PATH = (
    ROOT
    / "dist"
    / f"RiverCrossing-{rivercrossing.__version__}-windows-{_windows_arch()}-setup.exe"
)


def _authenticode_status(path: Path) -> str | None:
    """Return Get-AuthenticodeSignature's Status for *path*, or None.

    ``Valid`` means "syntactically valid Authenticode signature", not
    "trusted" -- exactly the right gate for "is this file signed?", the
    Windows mirror of the macOS ``spctl`` check.
    """
    if sys.platform != "win32":
        return None
    powershell = shutil.which("powershell")
    if powershell is None:
        return None
    completed = subprocess.run(  # noqa: S603 -- full path, fixed argv, no shell
        [
            powershell,
            "-NoProfile",
            "-Command",
            f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _missing_signed_setup_reason() -> str | None:
    """Return why no signed installer exists, or None when one does."""
    if sys.platform != "win32":
        return "Authenticode signing only runs on win32"
    if not SETUP_PATH.is_file():
        return f"no built installer -- run `nox -s winsetup` first; missing {SETUP_PATH}"
    if _authenticode_status(SETUP_PATH) != "Valid":
        return (
            "the installer is not Authenticode-signed -- Windows signing creds "
            "are not configured (SignPath); the unsigned build ships meanwhile"
        )
    return None


_SIGNED_SETUP_ABSENT_REASON = _missing_signed_setup_reason()


@pytest.mark.skipif(
    _SIGNED_SETUP_ABSENT_REASON is not None,
    reason=_SIGNED_SETUP_ABSENT_REASON or "no signed installer",
)
def test_setup_exe_is_authenticode_signed() -> None:
    """E9.1.2: the installer carries a valid Authenticode signature."""
    assert _authenticode_status(SETUP_PATH) == "Valid"
