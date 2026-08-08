# SPDX-License-Identifier: GPL-3.0-only
"""Task runner for RiverCrossing.

One entry point for local work and for CI, so the two cannot
drift. Sessions map onto the CI stages in spec.md section 14:

    stage 1 Static     -> lint, typecheck, importlint, ids_drift
    stage 2 Unit       -> unit
    stage 3 Functional -> functional
    stage 5 Build      -> bundle, smoke, dmg, dmg_smoke,
                           winsetup, winsetup_smoke

Run `nox -l` to list them, `nox -s <name>` to run one.
"""

import os
import shutil
import sys
from pathlib import Path

import nox
import nox.command

nox.options.default_venv_backend = "uv|virtualenv"
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["lint", "typecheck", "importlint", "ids_drift", "unit"]

PYTHON = "3.14"
ROOT = Path(__file__).parent
XRC_DIR = ROOT / "src" / "rivercrossing" / "ui" / "xrc"
GEN_IDS = ROOT / "tools" / "gen_ids.py"
GEN_APP_ICONS = ROOT / "tools" / "gen_app_icons.py"
SPEC = ROOT / "installers" / "rivercrossing.spec"
BUNDLE_SMOKE = ROOT / "tests" / "functional" / "test_bundle_smoke.py"
DMG_SETTINGS = ROOT / "installers" / "dmg_settings.py"
DMG_SMOKE = ROOT / "tests" / "functional" / "test_dmg_smoke.py"
APP_PATH = ROOT / "dist" / "RiverCrossing.app"
NSI = ROOT / "installers" / "windows.nsi"
WINSETUP_SMOKE = ROOT / "tests" / "functional" / "test_winsetup_smoke.py"

DEV = "-e.[dev]"


@nox.session(python=PYTHON)
def lint(session):
    """Run ruff over everything (CI stage 1)."""
    session.install("ruff>=0.15")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", "--diff", ".")


@nox.session(python=PYTHON)
def typecheck(session):
    """Run mypy --strict (CI stage 1)."""
    session.install(DEV)
    session.run("mypy")


@nox.session(python=PYTHON)
def importlint(session):
    """Assert wx never leaks out of rivercrossing.ui (R-71)."""
    session.install(DEV)
    session.run("lint-imports")


@nox.session(python=PYTHON)
def unit(session):
    """Run the headless unit suite with the coverage gate."""
    session.install(DEV)
    session.run("pytest", "tests/unit", "-m", "not functional", *session.posargs)


@nox.session(python=PYTHON)
def functional(session):
    """Drive real wx windows (CI stage 3).

    Coverage is off here: the view layer is proven by driving
    windows and asserting what they contain, not by counting
    executed lines.

    Each test *file* runs in its own worker process. Measured: in a
    single shared process, FindWindowByName intermittently fails to
    resolve a control that exists on a freshly loaded main_frame --
    3 failures in 8 full-suite runs, always the same three tests,
    while the same code is deterministic in isolation (12/12 frame
    loads, 400 dialog build/destroy cycles). Something in wx's
    per-process state degrades after several hundred window
    constructions. Bounding each file to its own process took that
    to 6/6 clean runs.

    Plus one auto-retry, which project-plan.md §4 adopts by name as
    this suite's flake control. It is doing real work, not papering
    over a mystery: after isolation the residual rate measured 1 bad
    run in 10 (a failure or, once, a hang near completion), and with
    the retry it measured 10/10 clean. The trigger is narrowed --
    building a frame, moving a splitter sash, destroying it and
    rebuilding fails ~1 in 6 even in a fresh process -- but the
    underlying wx behaviour is not explained, so treat a green suite
    as bounded, not solved.

    --forked would be the wrong tool on macOS: forking a process that
    has already initialised NSApplication is not safe.
    """
    sys.path.insert(0, str(ROOT))
    from tools.functional_gate import host_functional_run_allowed  # noqa: PLC0415

    allowed, message = host_functional_run_allowed(sys.platform, os.environ)
    if not allowed:
        session.error(message)

    session.install(DEV)
    session.run(
        "pytest",
        "tests/functional",
        "--no-cov",
        "-n",
        "auto",
        "--dist",
        "loadfile",
        "--reruns",
        "1",
        *session.posargs,
    )


@nox.session(python=PYTHON)
def gen_ids(session):
    """Regenerate ui/ids.py from the .xrc files (R-05)."""
    session.install(DEV)
    session.run("python", str(GEN_IDS), "--write")


@nox.session(python=PYTHON)
def ids_drift(session):
    """Fail if ui/ids.py disagrees with the .xrc files (R-05).

    Passes vacuously until the .xrc files exist, so the gate can
    be wired into CI before the windows are authored.
    """
    if not GEN_IDS.exists() or not any(XRC_DIR.glob("*.xrc")):
        session.log("no .xrc files yet - nothing to check")
        return
    session.install(DEV)
    session.run("python", str(GEN_IDS), "--check")


@nox.session(python=PYTHON)
def gen_branding(session):
    """Regenerate the committed branding artifacts (P8-D5)."""
    session.install(DEV)
    session.run("python", str(GEN_APP_ICONS))


@nox.session(python=PYTHON)
def bundle(session):
    """Build the unsigned PyInstaller dev bundle (CI stage 5)."""
    if not SPEC.exists():
        session.log("installers/rivercrossing.spec not authored yet")
        return
    session.install(DEV)
    session.run("pyinstaller", "--noconfirm", "--clean", str(SPEC))


@nox.session(python=PYTHON)
def smoke(session):
    """Launch the built bundle and smoke-test it (CI stage 5)."""
    if not BUNDLE_SMOKE.exists():
        session.log("bundle smoke test not authored yet")
        return
    session.install(DEV)
    session.run("pytest", str(BUNDLE_SMOKE), "--no-cov", *session.posargs)


def _project_version() -> str:
    """Read rivercrossing.__version__ straight off src/ (P8-D7).

    Runs in nox's own host process, not a session's venv, so this
    reads the single source of truth (module-skeletons.md S2)
    directly rather than depending on either process having the
    package installed.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import rivercrossing  # noqa: PLC0415 -- only this session needs the version

    return rivercrossing.__version__


@nox.session(python=PYTHON)
def dmg(session):
    """Build the unsigned drag-to-Applications DMG (P8-D7)."""
    if sys.platform != "darwin":
        session.skip("dmgbuild/hdiutil only run on darwin")
    if not APP_PATH.is_dir():
        session.error(f"no built .app -- run `nox -s bundle` first; missing {APP_PATH}")
    session.install(DEV)

    output_dmg = ROOT / "dist" / f"RiverCrossing-{_project_version()}.dmg"
    dmg_args = (
        "dmgbuild",
        "-s",
        str(DMG_SETTINGS),
        "-D",
        f"app={APP_PATH}",
        "RiverCrossing",
        str(output_dmg),
    )
    try:
        session.run(*dmg_args)
    except nox.command.CommandFailed:
        # hdiutil-on-hosted-runners flake (project-plan.md §4): one
        # retry, after clearing whatever partial image it left behind.
        output_dmg.unlink(missing_ok=True)
        session.run(*dmg_args)


@nox.session(python=PYTHON)
def dmg_smoke(session):
    """Mount the built DMG and smoke-test it (CI stage 5, P8-D7)."""
    if not DMG_SMOKE.exists():
        session.log("DMG smoke test not authored yet")
        return
    session.install(DEV)
    session.run("pytest", str(DMG_SMOKE), "--no-cov", *session.posargs)


# Homebrew's makensis 3.12 (arm64) crashes with std::bad_alloc when
# LANG/LC_ALL are unset (upstream NSIS bug #1165); force a UTF-8
# locale rather than trust whatever the calling shell left set.
_MAKENSIS_ENV = {"LANG": "en_GB.UTF-8", "LC_ALL": "en_GB.UTF-8"}
_MAKENSIS_CANDIDATES = (
    "/opt/homebrew/bin/makensis",
    r"C:\Program Files (x86)\NSIS\makensis.exe",
)


def _find_makensis() -> str | None:
    """Return a usable makensis binary, or None if there isn't one."""
    found = shutil.which("makensis")
    if found is not None:
        return found
    for candidate in _MAKENSIS_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _write_synthetic_payload(payload_dir: Path) -> None:
    """Write a compile-smoke-only payload, rebuilt fresh each run.

    Never the real dist/ tree: this is what proves windows.nsi still
    compiles on a machine that cannot build the actual PyInstaller
    onedir, not a stand-in for the real artifact.
    """
    shutil.rmtree(payload_dir, ignore_errors=True)
    internal_dir = payload_dir / "_internal"
    internal_dir.mkdir(parents=True)
    (payload_dir / "rivercrossing.exe").write_bytes(b"MZ\x00\x00fake-exe-for-testing")
    (internal_dir / "data.bin").write_bytes(b"\x00\x01\x02\x03")


@nox.session(python=PYTHON)
def winsetup(session):
    """Compile the unsigned per-user NSIS installer (CI stage 5).

    On win32 this packages the real dist/rivercrossing onedir into
    dist/RiverCrossing-<version>-setup.exe. Off win32 (this Mac) it
    is compile smoke only: a synthetic payload compiles to build/,
    never dist/ -- dist/ stays reserved for the real artifact
    windows-latest CI produces.
    """
    makensis = _find_makensis()
    if makensis is None:
        session.skip(
            "no makensis on this machine -- the real installer is compiled on windows-latest CI"
        )

    version = _project_version()
    if sys.platform == "win32":
        payload = ROOT / "dist" / "rivercrossing"
        if not payload.is_dir():
            session.error(f"no built payload -- run `nox -s bundle` first; missing {payload}")
        outfile = ROOT / "dist" / f"RiverCrossing-{version}-setup.exe"
    else:
        payload = ROOT / "build" / "winsetup-payload"
        _write_synthetic_payload(payload)
        outfile = ROOT / "build" / f"RiverCrossing-{version}-setup.exe"

    session.run(
        makensis,
        f"-DAPPVERSION={version}",
        # Native separators: Windows makensis finds no files behind a
        # forward-slash File glob (measured on windows-latest).
        f"-DPAYLOAD_DIR={payload}",
        f"-DOUTFILE={outfile}",
        str(NSI),
        external=True,
        env=_MAKENSIS_ENV,
    )
    session.log(f"built {outfile}")


@nox.session(python=PYTHON)
def winsetup_smoke(session):
    """Run the Windows installer smoke tests (CI stage 5, Phase 9)."""
    if not WINSETUP_SMOKE.exists():
        session.log("Windows installer smoke test not authored yet")
        return
    session.install(DEV)
    session.run("pytest", str(WINSETUP_SMOKE), "--no-cov", *session.posargs)
