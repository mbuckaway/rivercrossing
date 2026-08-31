# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for ``noxfile._windows_arch`` (Windows ARM64 support).

``_windows_arch`` maps a Windows ``platform.machine()`` spelling to the
short arch tag used in the installer filename, so the NSIS installer is
named ``-windows-x64-setup.exe`` or ``-windows-arm64-setup.exe``. Loaded
via ``importlib`` because ``noxfile.py`` is a root-level script, not an
installed package -- the same pattern ``test_dmg_settings.py`` uses for
``dmg_settings`` and ``test_bundle_smoke.py`` uses for
``check_asset_manifest``.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_NOXFILE_PATH = _ROOT / "noxfile.py"


@pytest.fixture(scope="module")
def noxfile_module() -> object:
    """Load noxfile.py once, failing out if it cannot be imported."""
    spec = importlib.util.spec_from_file_location("noxfile", _NOXFILE_PATH)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {_NOXFILE_PATH}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("AMD64", "x64"),
        ("amd64", "x64"),
        ("x86_64", "x64"),
        ("x64", "x64"),
        ("ARM64", "arm64"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
    ],
)
def test_windows_arch_maps_machine_to_short_tag(
    noxfile_module: object, machine: str, expected: str
) -> None:
    """Each Windows machine spelling maps to the short installer tag."""
    assert noxfile_module._windows_arch(machine) == expected


def test_windows_arch_passes_unknown_strings_through_lowercased(
    noxfile_module: object,
) -> None:
    """An unrecognized spelling is lowercased, never guessed."""
    assert noxfile_module._windows_arch("S390X") == "s390x"
