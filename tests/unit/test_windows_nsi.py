# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for installers/windows.nsi (Phase 9 pull-forward, E9.1.2).

task-briefs.md's E9.1.2 amendment replaces Inno Setup with NSIS
(makensis compiles natively on macOS; Inno's compiler needs Wine).
This module is the Windows twin of test_dmg_settings.py: it reads
``installers/windows.nsi`` as plain text and pins the raw directives
the script must contain, so a later edit that drops a registry write
or hard-codes a version fails here before it ever reaches a Windows
runner.

``installers/windows.nsi`` does not exist yet -- every test below
depends on the ``nsi_text`` fixture, which fails outright (naming the
missing path) rather than letting a bare ``FileNotFoundError``
propagate. That failure is this module's RED state.

Section-aware, not one global substring search: ``SetShellVarContext
current`` and the registry writes must appear in the *install*
section, and the shortcut/registry/RMDir cleanup in the *uninstall*
section specifically. Confirmed against a real ``makensis`` compile in
this session's probe: NSIS only routes a section into the uninstaller
when it is titled exactly ``"Uninstall"`` (any other title produces
``Error: no Uninstall section specified, but WriteUninstaller used``),
so ``Section "Install"`` / ``Section "Uninstall"`` is the actual
compiler contract, not a guess.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NSI_PATH = _REPO_ROOT / "installers" / "windows.nsi"

_REQUIRED_DEFINES = ("APPVERSION", "PAYLOAD_DIR", "OUTFILE")

_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing"
_REGISTRY_VALUE_NAMES = (
    "DisplayName",
    "DisplayVersion",
    "UninstallString",
    "QuietUninstallString",
    "DisplayIcon",
    "InstallLocation",
)

# CODINGSTANDARDS-PYTHON.md:41 -- comments wrap at 72 chars, code at 99.
_VERSION_LITERAL_RE = re.compile(r"\b\d+\.\d+\.\d+\b")


@pytest.fixture(scope="module")
def nsi_text() -> str:
    """Return installers/windows.nsi's full text, read once here."""
    if not _NSI_PATH.is_file():
        pytest.fail(f"installers/windows.nsi does not exist -- missing {_NSI_PATH}")
    return _NSI_PATH.read_text(encoding="utf-8")


def _section_body(text: str, title: str) -> str:
    """Body text between Section "<title>" and SectionEnd."""
    pattern = re.compile(rf'Section\s+"{re.escape(title)}".*?\n(.*?)\nSectionEnd', re.DOTALL)
    match = pattern.search(text)
    if match is None:
        pytest.fail(f'no Section "{title}" ... SectionEnd block in installers/windows.nsi')
    return match.group(1)


def _guard_body(text: str, define_name: str) -> str:
    """Body of *define_name*'s !ifndef/!endif guard."""
    pattern = re.compile(rf"!ifndef\s+{define_name}\b(.*?)!endif", re.DOTALL)
    match = pattern.search(text)
    if match is None:
        pytest.fail(f"no !ifndef {define_name} ... !endif guard in installers/windows.nsi")
    return match.group(1)


def _line_containing(text: str, needle: str) -> str:
    """Return the first line of *text* that contains *needle*."""
    for line in text.splitlines():
        if needle in line:
            return line
    pytest.fail(f"no line containing {needle!r} in installers/windows.nsi")


# ------------------------------------------------- compile-time defines


@pytest.mark.parametrize("define_name", _REQUIRED_DEFINES)
def test_windows_nsi_guards_each_required_define_with_ifndef_error(
    nsi_text: str, define_name: str
) -> None:
    """A missing -D<name> must abort the compile, never proceed."""
    guard_body = _guard_body(nsi_text, define_name)

    assert "!error" in guard_body
    assert define_name in guard_body


# ------------------------------------------------- top-level directives


def test_windows_nsi_declares_app_name_rivercrossing(nsi_text: str) -> None:
    """Name "RiverCrossing" -- inherited by pages and shortcuts."""
    assert 'Name "RiverCrossing"' in nsi_text


def test_windows_nsi_enables_unicode(nsi_text: str) -> None:
    """Unicode true -- required for non-ASCII PAYLOAD_DIR paths."""
    assert "Unicode true" in nsi_text


def test_windows_nsi_requests_per_user_execution_level(nsi_text: str) -> None:
    """No UAC prompt: install/uninstall both run per-user."""
    assert "RequestExecutionLevel user" in nsi_text


def test_windows_nsi_installs_under_local_appdata_programs(nsi_text: str) -> None:
    """Per-user root, never Program Files (R-01's unsigned .exe)."""
    assert r'InstallDir "$LOCALAPPDATA\Programs\RiverCrossing"' in nsi_text


def test_windows_nsi_outfile_directive_uses_the_outfile_define(nsi_text: str) -> None:
    """The compiled installer's path always comes from ``-DOUTFILE``."""
    assert 'OutFile "${OUTFILE}"' in nsi_text


@pytest.mark.parametrize("directive", ["Icon", "UninstallIcon"])
def test_windows_nsi_names_the_branded_ico_for_installer_and_uninstaller(
    nsi_text: str, directive: str
) -> None:
    """Both the installer and the uninstaller carry the branded .ico."""
    assert f'{directive} "branding/rivercrossing.ico"' in nsi_text


@pytest.mark.parametrize(
    "page_directive",
    ["Page directory", "Page instfiles", "UninstPage uninstConfirm", "UninstPage instfiles"],
)
def test_windows_nsi_declares_the_classic_install_and_uninstall_pages(
    nsi_text: str, page_directive: str
) -> None:
    """All four classic pages appear -- no Modern UI wizard."""
    assert page_directive in nsi_text


# -------------------------------------------------- the install section


def test_windows_nsi_install_section_sets_shell_var_context_current(nsi_text: str) -> None:
    """Shell vars ($SMPROGRAMS etc.) resolve to the current user."""
    install_section = _section_body(nsi_text, "Install")

    assert "SetShellVarContext current" in install_section


def test_windows_nsi_install_section_sets_the_output_path_to_installdir(nsi_text: str) -> None:
    """Every subsequent ``File`` writes under ``$INSTDIR``."""
    install_section = _section_body(nsi_text, "Install")

    assert 'SetOutPath "$INSTDIR"' in install_section


def test_windows_nsi_install_section_stages_the_payload_directory_recursively(
    nsi_text: str,
) -> None:
    """The whole PyInstaller payload ships, not a hand-picked subset.

    The filespec joins with a backslash: Windows makensis finds no
    files behind a forward-slash glob (measured on windows-latest),
    while POSIX makensis converts the backslash (measured locally).
    """
    install_section = _section_body(nsi_text, "Install")

    assert r'File /r "${PAYLOAD_DIR}\*"' in install_section


def test_windows_nsi_install_section_creates_the_start_menu_shortcut(nsi_text: str) -> None:
    """The Start-menu entry E9.1.2's named test checks for."""
    install_section = _section_body(nsi_text, "Install")

    assert (
        r'CreateShortcut "$SMPROGRAMS\RiverCrossing.lnk" "$INSTDIR\rivercrossing.exe"'
        in install_section
    )


def test_windows_nsi_install_section_writes_the_uninstaller(nsi_text: str) -> None:
    """uninstall.exe is what the shortcut/registry entries name."""
    install_section = _section_body(nsi_text, "Install")

    assert r'WriteUninstaller "$INSTDIR\uninstall.exe"' in install_section


def test_windows_nsi_install_section_writes_under_the_uninstall_registry_key(
    nsi_text: str,
) -> None:
    """Every registry write below targets the one HKCU key."""
    install_section = _section_body(nsi_text, "Install")

    assert _UNINSTALL_KEY in install_section


@pytest.mark.parametrize("value_name", _REGISTRY_VALUE_NAMES)
def test_windows_nsi_install_section_writes_each_required_registry_value(
    nsi_text: str, value_name: str
) -> None:
    """All six Programs-and-Features values are written on install."""
    install_section = _section_body(nsi_text, "Install")
    line = _line_containing(install_section, f'"{value_name}"')

    assert line.strip().startswith("WriteRegStr HKCU")


def test_windows_nsi_display_name_registry_value_is_rivercrossing(nsi_text: str) -> None:
    """The name shown in Programs and Features is the product name."""
    install_section = _section_body(nsi_text, "Install")
    line = _line_containing(install_section, '"DisplayName"')

    assert '"RiverCrossing"' in line


def test_windows_nsi_display_version_registry_value_references_appversion(
    nsi_text: str,
) -> None:
    """Version is the compile-time define, never a literal."""
    install_section = _section_body(nsi_text, "Install")
    line = _line_containing(install_section, '"DisplayVersion"')

    assert "${APPVERSION}" in line


def test_windows_nsi_quiet_uninstall_string_ends_with_the_silent_flag(nsi_text: str) -> None:
    """CI's silent uninstall depends on this exact ``/S`` suffix."""
    install_section = _section_body(nsi_text, "Install")
    line = _line_containing(install_section, '"QuietUninstallString"')
    trimmed = line.rstrip().removesuffix('"').removesuffix("'").rstrip()

    assert trimmed.endswith("/S")


# ------------------------------------------------ the uninstall section


def test_windows_nsi_uninstall_section_sets_shell_var_context_current(nsi_text: str) -> None:
    """The uninstaller resolves $SMPROGRAMS for the same user, too."""
    uninstall_section = _section_body(nsi_text, "Uninstall")

    assert "SetShellVarContext current" in uninstall_section


def test_windows_nsi_uninstall_section_deletes_the_start_menu_shortcut(nsi_text: str) -> None:
    """Uninstall must remove exactly the shortcut install created."""
    uninstall_section = _section_body(nsi_text, "Uninstall")

    assert r'Delete "$SMPROGRAMS\RiverCrossing.lnk"' in uninstall_section


def test_windows_nsi_uninstall_section_deletes_the_uninstall_registry_key(nsi_text: str) -> None:
    """A leftover key would ghost-list the app in Programs/Features."""
    uninstall_section = _section_body(nsi_text, "Uninstall")

    assert f'DeleteRegKey HKCU "{_UNINSTALL_KEY}"' in uninstall_section


def test_windows_nsi_uninstall_section_removes_installdir_recursively(nsi_text: str) -> None:
    """The whole per-user install tree goes, not just the top files."""
    uninstall_section = _section_body(nsi_text, "Uninstall")

    assert 'RMDir /r "$INSTDIR"' in uninstall_section


# ------------------------------------------------ no hard-coded version


def test_windows_nsi_never_hard_codes_a_dotted_version_literal(nsi_text: str) -> None:
    """The only version anywhere in the script is ``${APPVERSION}``."""
    assert _VERSION_LITERAL_RE.findall(nsi_text) == []
