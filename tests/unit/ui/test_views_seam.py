# SPDX-License-Identifier: GPL-3.0-only
"""Constructor-signature proof for ``ui.views`` (E1.2.4, E5.4.2).

With no fallback ``DataSource`` default left in any of these
constructors, omitting ``data_source`` is no longer a silent default --
it is a ``TypeError`` from Python's own signature enforcement, asserted
here rather than hand-checked. ``RiderEditor``'s own required keyword
changed from ``data_source`` to ``roster`` in E3.2 (it now drives a real
``Roster`` directly rather than a display-only ``DataSource`` projection
of one), so it carries its own dedicated case below instead of joining
the shared ``data_source`` parametrization.
"""

import re

import pytest

from rivercrossing.ui.views.entry_detail import EntryDetailDialog
from rivercrossing.ui.views.main_frame import MainFrame
from rivercrossing.ui.views.results_win import ResultsWindow
from rivercrossing.ui.views.ride_library import RideLibrary
from rivercrossing.ui.views.rider_editor import RiderEditor
from rivercrossing.ui.views.simulator import SimulatorDialog

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


_SIMULATOR_REQUIRED_KWARGS = (
    pytest.param({}, "engine", id="no-kwargs"),
    pytest.param({"roster": object()}, "engine", id="roster-only"),
    pytest.param({"engine": object()}, "roster", id="engine-only"),
)


@pytest.mark.parametrize(("kwargs", "missing"), _SIMULATOR_REQUIRED_KWARGS)
def test_simulator_dialog_construction_without_required_kwarg_raises_type_error(
    kwargs: dict[str, object], missing: str
) -> None:
    """SimulatorDialog requires both engine and roster.

    It drives the live engine through the roster, so neither seam has a
    default -- Python's own signature enforcement is the proof.
    *kwargs* is a placeholder, never a real wx window: the ``TypeError``
    fires during argument binding, before the constructor body runs.
    """
    with pytest.raises(TypeError, match=re.escape(missing)):
        SimulatorDialog(object(), **kwargs)
