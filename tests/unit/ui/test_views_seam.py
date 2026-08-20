# SPDX-License-Identifier: GPL-3.0-only
"""Belt-and-braces seam proof for ``ui.views`` (E1.2.4).

The "only the app bootstrap imports rivercrossing.demo" contract
(pyproject.toml's own import-linter config) once missed five hidden
imports because its own ``source_modules`` list left
``rivercrossing.ui.views`` out -- a contract mis-scoped by the person
who wrote it. This module re-proves the same fact by parsing each
view module's own source with ``ast``, mirroring
``test_app_wiring.py``'s own ``_demo_import_count`` for
``rivercrossing.ui.app``: a test that reads the source directly
cannot be mis-scoped the same way a contract's module list can.

The second half proves the fix's other half: with no fallback to
``DemoDataSource`` left in any of these constructors, omitting
``data_source`` is no longer a silent default -- it is a
``TypeError`` from Python's own signature enforcement, asserted here
rather than hand-checked. ``RiderEditor``'s own required keyword
changed from ``data_source`` to ``roster`` in E3.2 (it now drives a
real ``Roster`` directly rather than a display-only ``DataSource``
projection of one), so it carries its own dedicated case below
instead of joining the shared ``data_source`` parametrization.
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

from rivercrossing.ui.views.entry_detail import EntryDetailDialog
from rivercrossing.ui.views.main_frame import MainFrame
from rivercrossing.ui.views.results_win import ResultsWindow
from rivercrossing.ui.views.ride_library import RideLibrary
from rivercrossing.ui.views.rider_editor import RiderEditor

VIEWS_DIR = Path(inspect.getfile(MainFrame)).resolve().parent

# --- no ui.views module imports rivercrossing.demo (T-3/T-5's contract,
# proven again structurally rather than by the import-linter alone) ---


def _view_module_paths() -> list[Path]:
    """Return every ``.py`` file directly inside ``ui/views``."""
    return sorted(VIEWS_DIR.glob("*.py"))


def _imports_rivercrossing_demo(source: str) -> bool:
    """Return whether *source* imports ``rivercrossing.demo`` at all.

    Counts both import forms (``import rivercrossing.demo`` and
    ``from rivercrossing.demo import ...``), structurally rather
    than by string-matching the source text -- the same technique
    ``test_app_wiring.py``'s own ``_demo_import_count`` uses for
    ``rivercrossing.ui.app``.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "rivercrossing.demo":
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "rivercrossing.demo" for alias in node.names
        ):
            return True
    return False


@pytest.mark.parametrize("module_path", _view_module_paths(), ids=lambda path: path.name)
def test_ui_views_module_source_never_imports_rivercrossing_demo(module_path: Path) -> None:
    """E1.2.4's seam: only ``ui.app`` imports ``rivercrossing.demo``."""
    source = module_path.read_text(encoding="utf-8")

    assert _imports_rivercrossing_demo(source) is False


# --- data_source is required: Python's own signature enforcement ---

_VIEW_CONSTRUCTION_CASES = (
    pytest.param(MainFrame, (object(),), id="MainFrame"),
    pytest.param(RideLibrary, (object(),), id="RideLibrary"),
    pytest.param(EntryDetailDialog, (object(), "77"), id="EntryDetailDialog"),
    pytest.param(ResultsWindow, (object(),), id="ResultsWindow"),
)


@pytest.mark.parametrize(("view_class", "positional_args"), _VIEW_CONSTRUCTION_CASES)
def test_view_construction_without_data_source_raises_type_error(
    view_class: type, positional_args: tuple[object, ...]
) -> None:
    """data_source is required, not defaulted -- Python enforces it.

    *positional_args* are placeholders, never real wx windows: the
    ``TypeError`` fires during argument binding, before the
    constructor body ever touches ``frame``/``dialog``/``plate``.
    """
    with pytest.raises(TypeError, match=re.escape("data_source")):
        view_class(*positional_args)


def test_rider_editor_construction_without_roster_raises_type_error() -> None:
    """RiderEditor's required kwarg is roster, not data_source (E3.2).

    It reads and writes the roster itself rather than a display-only
    ``DataSource`` projection of one (``RidersPresenter``'s own module
    docstring) -- *positional_args* is a placeholder, never a real wx
    window, matching the shared parametrized cases above.
    """
    with pytest.raises(TypeError, match=re.escape("roster")):
        RiderEditor(object())
