# SPDX-License-Identifier: GPL-3.0-only
"""Windows installer smoke tests (Phase 9 pull-forward, E9.1.2).

task-briefs.md's E9.1.2 amendment replaces Inno Setup with NSIS
(makensis compiles natively on macOS; Inno's compiler needs Wine).
E9.1.2's named tests *are* the spec: "silent install/uninstall on
windows-latest leaves/removes files + Start-menu entry. Done when:
install-run-uninstall green." This module is that spec, split into
two groups that fail for different reasons and on different machines:

Group 1 -- **compile smoke**, any machine with ``makensis`` (this
Mac included, via the Homebrew arm64 build): compiles
``installers/windows.nsi`` against a synthetic payload, never the
real ``dist/`` tree. Currently fails because the script does not
exist yet -- ``makensis`` reports "Can't open script" -- which is
this module's RED for both the positive and the negative case.

Group 2 -- **E9.1.2's own named tests**, win32-only: silent
install, Start-menu entry, registry, launch, silent uninstall. Five
deliberately *stateful, ordered* tests that share one on-disk
installation across the module -- run this file only as plain
single-process pytest (no ``-p xdist`` worker split, no reordering);
splitting or reordering these breaks the shared state test 1 creates
and tests 2-5 depend on. On this macOS machine every test in this
group skips at the ``installed_windows_setup`` fixture, naming
win32 as the missing prerequisite.

Homebrew's makensis 3.12 (arm64) crashes with ``std::bad_alloc``
when ``LANG``/``LC_ALL`` are unset (upstream NSIS bug #1165; the
Homebrew formula's own test sets ``en_GB.UTF-8`` for the same
reason), so every ``makensis`` invocation here passes an explicit
UTF-8 locale rather than inheriting a possibly-empty one.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest

import rivercrossing

if sys.platform == "win32":
    import winreg

pytestmark = pytest.mark.functional

ROOT = Path(__file__).resolve().parents[2]
NSI_PATH = ROOT / "installers" / "windows.nsi"

# shutil.which() first; these are this session's own probe's two
# known install locations when makensis is not on PATH.
_KNOWN_MAKENSIS_PATHS = (
    "/opt/homebrew/bin/makensis",
    r"C:\Program Files (x86)\NSIS\makensis.exe",
)
_UTF8_LOCALE = "en_GB.UTF-8"

COMPILE_TIMEOUT_SECONDS = 60
INSTALL_TIMEOUT_SECONDS = 300
UNINSTALL_RUN_TIMEOUT_SECONDS = 60
# The app is a GUI process: once it reaches its main loop it never
# exits on its own, so the launch probe waits this long for a crash
# to show up, then kills it. Mirrors test_bundle_smoke.py's constant.
LAUNCH_SETTLE_SECONDS = 20
# The uninstaller copies itself to %TEMP% and re-execs before it
# deletes $INSTDIR, so it can return before removal actually
# finishes; poll for a bounded window instead of trusting the exit.
UNINSTALL_POLL_DEADLINE_SECONDS = 60
UNINSTALL_POLL_INTERVAL_SECONDS = 2

SETUP_PATH = ROOT / "dist" / f"RiverCrossing-{rivercrossing.__version__}-setup.exe"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "RiverCrossing"
START_MENU_LNK = (
    Path(os.environ.get("APPDATA", ""))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "RiverCrossing.lnk"
)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing"

_LOG_DIR = ROOT / "build" / "winsetup-logs"


def _makensis_env() -> dict[str, str]:
    """Copy os.environ with a UTF-8 locale forced for makensis.

    See the module docstring: an unset LANG/LC_ALL crashes Homebrew's
    makensis with std::bad_alloc (NSIS bug #1165).
    """
    env = dict(os.environ)
    env["LANG"] = _UTF8_LOCALE
    env["LC_ALL"] = _UTF8_LOCALE
    return env


def _build_payload(payload_dir: Path) -> Path:
    """Write a synthetic payload -- never the real dist/ tree."""
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / "rivercrossing.exe").write_bytes(b"MZ\x00\x00fake-exe-for-testing")
    internal_dir = payload_dir / "_internal"
    internal_dir.mkdir()
    (internal_dir / "data.bin").write_bytes(b"\x00\x01\x02\x03")
    return payload_dir


def _log(name: str, message: str) -> None:
    """Append one diagnostic line to build/winsetup-logs/<name>.log."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    (_LOG_DIR / f"{name}.log").write_text(f"{message}\n", encoding="utf-8")


class _Launch(NamedTuple):
    """What a launched process printed before it stopped or was killed.

    ``returncode`` is ``None`` when the process was still alive at
    :data:`LAUNCH_SETTLE_SECONDS` and had to be killed -- the normal
    outcome for a GUI app that reached its main loop.
    """

    returncode: int | None
    stdout: str
    stderr: str


def _launch(executable: Path) -> _Launch:
    """Launch *executable* and collect its output, killing it after.

    Never leaves a GUI process behind: every path either reaps a
    process that exited on its own or kills one that did not.
    """
    with subprocess.Popen(  # noqa: S603 -- this session's own installed exe
        [str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=LAUNCH_SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return _Launch(None, stdout, stderr)
    return _Launch(process.returncode, stdout, stderr)


# ----------------------------------------------- group 1: compile smoke


@pytest.fixture(scope="module")
def makensis_path() -> str:
    """Return a usable makensis binary, skipping if none exists."""
    found = shutil.which("makensis")
    if found is not None:
        return found
    for candidate in _KNOWN_MAKENSIS_PATHS:
        if Path(candidate).is_file():
            return candidate
    pytest.skip(
        "no makensis on this machine -- the real installer is compiled on windows-latest CI"
    )


def test_windows_nsi_compiles_with_all_defines_to_the_named_outfile(
    makensis_path: str, tmp_path: Path
) -> None:
    """A full ``-D`` set compiles installers/windows.nsi to OUTFILE.

    Currently fails: installers/windows.nsi does not exist yet, so
    makensis reports "Can't open script" and exits non-zero -- this
    module's RED, observed rather than asserted away.
    """
    payload_dir = _build_payload(tmp_path / "payload")
    outfile = tmp_path / f"RiverCrossing-{rivercrossing.__version__}-setup.exe"

    completed = subprocess.run(  # noqa: S603 -- absolute makensis path, fixed argv
        [
            makensis_path,
            f"-DAPPVERSION={rivercrossing.__version__}",
            # Native separators: Windows makensis finds no files
            # behind a forward-slash File glob (measured on
            # windows-latest); POSIX makensis takes both.
            f"-DPAYLOAD_DIR={payload_dir}",
            f"-DOUTFILE={outfile}",
            str(NSI_PATH),
        ],
        env=_makensis_env(),
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert outfile.is_file(), output


def test_windows_nsi_compile_without_appversion_fails_naming_it(
    makensis_path: str, tmp_path: Path
) -> None:
    """Omitting ``-DAPPVERSION`` must abort the compile, naming it.

    Currently fails for a different reason than its eventual GREEN
    state -- the script itself is missing, so the output names the
    missing file rather than the ``!ifndef`` guard's own message --
    but it fails either way, which is this module's RED.
    """
    payload_dir = _build_payload(tmp_path / "payload")
    outfile = tmp_path / f"RiverCrossing-{rivercrossing.__version__}-setup.exe"

    completed = subprocess.run(  # noqa: S603 -- absolute makensis path, fixed argv
        [
            makensis_path,
            f"-DPAYLOAD_DIR={payload_dir}",
            f"-DOUTFILE={outfile}",
            str(NSI_PATH),
        ],
        env=_makensis_env(),
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0, output
    assert "APPVERSION" in output, output


# ------------------------------------------ group 2: E9.1.2 named tests


@pytest.fixture(scope="module")
def installed_windows_setup() -> Path:
    """Skip unless this is win32 with a built installer to drive.

    Gates entry only -- it does not install anything itself. Test 1
    below performs the install every later test in this module reads
    back, in file order; see the module docstring's stateful-order
    warning.
    """
    if sys.platform != "win32":
        pytest.skip("the Windows installer only installs on win32")
    if not SETUP_PATH.is_file():
        pytest.skip(f"no built installer -- run `nox -s winsetup` first; missing {SETUP_PATH}")
    return SETUP_PATH


def test_windows_installer_silent_install_creates_the_app_and_internal_dir(
    installed_windows_setup: Path,
) -> None:
    """/S leaves the frozen exe and its _internal payload in place."""
    completed = subprocess.run(  # noqa: S603 -- this build's own installer
        [str(installed_windows_setup), "/S"],
        capture_output=True,
        text=True,
        timeout=INSTALL_TIMEOUT_SECONDS,
        check=False,
    )
    exe_path = INSTALL_DIR / "rivercrossing.exe"
    internal_dir = INSTALL_DIR / "_internal"
    _log(
        "install",
        f"returncode={completed.returncode} "
        f"exe_exists={exe_path.is_file()} internal_exists={internal_dir.is_dir()}",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert exe_path.is_file()
    assert internal_dir.is_dir()


def test_windows_installer_creates_the_start_menu_shortcut(
    installed_windows_setup: Path,  # noqa: ARG001 -- gate-only, install happened in test 1
) -> None:
    """The Start-menu entry E9.1.2's named test checks for."""
    _log("start_menu", f"exists={START_MENU_LNK.is_file()} path={START_MENU_LNK}")

    assert START_MENU_LNK.is_file()


def test_windows_installer_registers_the_uninstall_entry(
    installed_windows_setup: Path,  # noqa: ARG001 -- gate-only, install happened in test 1
) -> None:
    """DisplayVersion and UninstallString match this exact build."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        display_version, _ = winreg.QueryValueEx(key, "DisplayVersion")
        uninstall_string, _ = winreg.QueryValueEx(key, "UninstallString")
    _log("registry", f"DisplayVersion={display_version} UninstallString={uninstall_string}")

    assert display_version == rivercrossing.__version__
    assert str(INSTALL_DIR) in uninstall_string


def test_windows_installer_installed_app_launches(
    installed_windows_setup: Path,  # noqa: ARG001 -- gate-only, install happened in test 1
) -> None:
    """A GUI process alive at the settle deadline is the pass case."""
    launch = _launch(INSTALL_DIR / "rivercrossing.exe")
    context = f"stdout:\n{launch.stdout}\nstderr:\n{launch.stderr}"
    _log("launch", f"returncode={launch.returncode} stderr_len={len(launch.stderr)}")

    assert launch.returncode in {0, None}, context
    assert "Traceback (most recent call last)" not in launch.stderr, context


def test_windows_installer_silent_uninstall_removes_all_traces(
    installed_windows_setup: Path,  # noqa: ARG001 -- gate-only, install happened in test 1
) -> None:
    """Uninstall clears the install dir, the shortcut, and the key."""
    uninstaller = INSTALL_DIR / "uninstall.exe"
    subprocess.run(  # noqa: S603 -- this build's own uninstaller
        [str(uninstaller), "/S"],
        capture_output=True,
        text=True,
        timeout=UNINSTALL_RUN_TIMEOUT_SECONDS,
        check=False,
    )

    deadline = time.monotonic() + UNINSTALL_POLL_DEADLINE_SECONDS
    while time.monotonic() < deadline and INSTALL_DIR.exists():
        time.sleep(UNINSTALL_POLL_INTERVAL_SECONDS)

    registry_key_gone = False
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        registry_key_gone = True
    _log(
        "uninstall",
        f"installdir_gone={not INSTALL_DIR.exists()} "
        f"shortcut_gone={not START_MENU_LNK.exists()} registry_gone={registry_key_gone}",
    )

    assert not INSTALL_DIR.exists()
    assert not START_MENU_LNK.exists()
    assert registry_key_gone
