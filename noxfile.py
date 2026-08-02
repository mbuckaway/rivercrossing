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
    """
    session.install(DEV)
    session.run("pytest", "tests/functional", "--no-cov", *session.posargs)


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
