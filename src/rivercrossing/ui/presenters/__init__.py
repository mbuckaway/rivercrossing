# SPDX-License-Identifier: GPL-3.0-only
"""Pure-Python presenters, one per window family (R-71, passive view).

Each window gets a ``*View`` ``Protocol`` (the screen's display
surface) and a matching ``*Presenter`` (pure Python, holding
``(view, data_source)``). Views render view-models and forward
events; presenters hold UI logic and are unit-tested headless with
``FakeView`` doubles; business logic lives below both, in the core
modules (module-skeletons.md S1). No module in this package may ever
import ``wx`` -- the import-linter contract in ``pyproject.toml``
grows to cover this package once it exists, per R-71.

**Why this is not a single-implementation interface (SIMPLECODE
Rule 4):** every ``*View`` Protocol here has, or is about to have,
two real implementations -- the ``FakeView`` test doubles in
``tests/unit/presenters/test_protocols.py`` today, and the wx-backed
view under ``rivercrossing.ui.views`` from Phase 5 on. That is a
second real implementer, not a speculative one added "in case." R-71
*requires in writing* that core UI logic have zero wx imports; the
only way to satisfy that requirement and still let a real wx window
drive a presenter is a structural boundary a fake can also satisfy --
exactly the scale-readiness carve-out SIMPLECODE:40 describes
("a pattern a present, written requirement demands ... is essential
complexity and stays"). Collapsing this layer back into the view
would violate R-71, not simplify toward it -- do not "simplify" it
away.

``DataSource`` (in ``data_source.py``) is the analogous seam on the
data side: one read-only Protocol every presenter takes, satisfied
today by nothing (its production implementer, ``DemoDataSource``,
lands in task E1.2.4) and by a store-backed source in EPICs 4-5.
"""

from rivercrossing.ui.presenters.audit import AuditPresenter, AuditView
from rivercrossing.ui.presenters.console import ConsolePresenter, ConsoleView, Cue
from rivercrossing.ui.presenters.data_source import (
    AuditRow,
    Counters,
    DataSource,
    EntryDetail,
    EntryLapRow,
    FeedRow,
    RiderRow,
    RideSummary,
    StandingsRow,
)
from rivercrossing.ui.presenters.detail import DetailPresenter, DetailView
from rivercrossing.ui.presenters.library import LibraryPresenter, LibraryView
from rivercrossing.ui.presenters.results import ResultsPresenter, ResultsView
from rivercrossing.ui.presenters.riders import (
    CsvConflict,
    CsvPreview,
    RidersPresenter,
    RidersView,
)
from rivercrossing.ui.presenters.settings import AppSettings, SettingsPresenter, SettingsView
from rivercrossing.ui.presenters.setup import SetupPresenter, SetupView

__all__ = [
    "AppSettings",
    "AuditPresenter",
    "AuditRow",
    "AuditView",
    "ConsolePresenter",
    "ConsoleView",
    "Counters",
    "CsvConflict",
    "CsvPreview",
    "Cue",
    "DataSource",
    "DetailPresenter",
    "DetailView",
    "EntryDetail",
    "EntryLapRow",
    "FeedRow",
    "LibraryPresenter",
    "LibraryView",
    "ResultsPresenter",
    "ResultsView",
    "RideSummary",
    "RiderRow",
    "RidersPresenter",
    "RidersView",
    "SettingsPresenter",
    "SettingsView",
    "SetupPresenter",
    "SetupView",
    "StandingsRow",
]
