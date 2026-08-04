# SPDX-License-Identifier: GPL-3.0-only
"""Task runner for RiverCrossing.

One entry point for local work and for CI, so the two cannot
drift. Sessions map onto the CI stages in spec.md section 14:

    stage 1 Static     -> lint, typecheck, importlint, ids_drift
    stage 2 Unit       -> unit
    stage 3 Functional -> functional
    stage 5 Build      -> bundle, smoke

Run `nox -l` to list them, `nox -s <name>` to run one.
"""

from pathlib import Path

import nox

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
