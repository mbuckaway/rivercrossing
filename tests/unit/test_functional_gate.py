# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/functional_gate.py (host functional-run gate).

RED phase for the macOS-VM functional lane: ``tools/functional_gate.
py`` does not exist yet. ``host_functional_run_allowed`` will decide
whether a *host* (non-VM) invocation of ``nox -s functional`` may
proceed. On darwin it must not, except under CI or an explicit
opt-out env var, because constructing wx windows on the host desktop
is exactly the hazard the VM lane (test_vm_scripts.py) exists to
avoid; win32 and linux hosts are never at risk, so they are always
allowed.

``tools/`` carries no ``__init__.py`` (module-skeletons.md: it is dev
tooling, excluded from ``[tool.setuptools.packages.find]``), so it is
only importable as an implicit PEP 420 namespace package once its
*parent* directory -- the repo root -- is itself on ``sys.path``. The
editable install only adds ``src/`` (see the repo's ``pyproject.
toml``), so this module inserts the repo root the same way ``nox
file.py`` and ``tools/check_asset_manifest.py`` already insert
``src/``.

The actual ``from tools.functional_gate import ...`` happens inside
the ``gate`` fixture below, not at module level. A bare module-level
import raises ``ModuleNotFoundError`` at *collection* time, and
pytest aborts the whole session on any collection error -- verified
locally: with the import at module level, `pytest tests/unit` was
interrupted before running a single test in any other file, not just
this one. Deferring it into a fixture confines a missing tools/
functional_gate.py to this module's own tests, the same way test_
windows_nsi.py's ``nsi_text`` fixture confines a missing installers/
windows.nsi to that module -- and that per-test failure is this
module's RED state.
"""

import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HOST_SCRIPT_HINT = "scripts/run_functional_tests_vm.sh"
_OPT_OUT_HINT = "RIVERCROSSING_HOST_FUNCTIONAL=1"

_Gate = Callable[[str, Mapping[str, str]], tuple[bool, str]]
_ParamCase = tuple[str, dict[str, str]]

_PERMITTED_CASES: tuple[_ParamCase, ...] = (
    ("win32", {}),
    ("linux", {}),
    ("darwin", {"CI": "true"}),
    ("darwin", {"RIVERCROSSING_HOST_FUNCTIONAL": "1"}),
)

_BLOCKED_CASES: tuple[_ParamCase, ...] = (
    ("darwin", {}),
    ("darwin", {"CI": ""}),
    ("darwin", {"RIVERCROSSING_HOST_FUNCTIONAL": "0"}),
    ("darwin", {"RIVERCROSSING_HOST_FUNCTIONAL": ""}),
    ("darwin", {"RIVERCROSSING_HOST_FUNCTIONAL": "yes"}),
)


@pytest.fixture(scope="module")
def gate() -> _Gate:
    """Return ``host_functional_run_allowed``, imported lazily."""
    # RED: tools/functional_gate.py is not authored yet, so mypy
    # cannot resolve this import; both ignores are expected to become
    # unused (and must be dropped) once the module lands in GREEN.
    from tools.functional_gate import (  # type: ignore[import-not-found]  # noqa: PLC0415
        host_functional_run_allowed,
    )

    return host_functional_run_allowed  # type: ignore[no-any-return]


@pytest.mark.parametrize(("platform", "environ"), _PERMITTED_CASES)
def test_host_functional_run_allowed_when_permitted_returns_true_and_empty_message(
    gate: _Gate, platform: str, environ: Mapping[str, str]
) -> None:
    """win32/linux, CI, or the opt-out env var permit host runs."""
    result = gate(platform, environ)

    assert result == (True, "")


@pytest.mark.parametrize(("platform", "environ"), _BLOCKED_CASES)
def test_host_functional_run_allowed_when_blocked_returns_false_with_guidance(
    gate: _Gate, platform: str, environ: Mapping[str, str]
) -> None:
    """A bare darwin run with no CI/opt-out is refused with guidance."""
    allowed, message = gate(platform, environ)

    assert allowed is False
    assert _HOST_SCRIPT_HINT in message
    assert _OPT_OUT_HINT in message


@given(
    platform=st.text(min_size=1).filter(lambda value: value != "darwin"),
    environ=st.dictionaries(st.text(), st.text()),
)
def test_host_functional_run_allowed_property_non_darwin_ignores_environ(
    gate: _Gate, platform: str, environ: Mapping[str, str]
) -> None:
    """Off darwin, environ contents never change the verdict."""
    result = gate(platform, environ)

    assert result == (True, "")


def test_noxfile_source_references_host_functional_run_allowed() -> None:
    """noxfile.py must wire in the host gate (future GREEN)."""
    noxfile_source = (_REPO_ROOT / "noxfile.py").read_text(encoding="utf-8")

    assert "host_functional_run_allowed" in noxfile_source
