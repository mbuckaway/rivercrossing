# SPDX-License-Identifier: GPL-3.0-only
"""Shared module-level helpers for the split list-window suites.

``test_lists_demo.py`` was split into three files (demo, entry-detail,
results) so the two heaviest functional files spread their per-worker
window churn across ``--dist loadfile`` workers (the wrapper-cache
corruption remedy). The constants and helpers every one of the three
files needs live here, under a name pytest never collects; each file
keeps its own private helpers (the per-window ``_StubSource`` classes,
the module-scoped ``shared_*`` fixtures) inline.
"""

import itertools
from typing import Any
from unittest.mock import MagicMock

from rivercrossing.demo import DemoDataSource
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids

MAX_SCREEN_WIDTH = 1366
MAX_SCREEN_HEIGHT = 768

# --- xrc-windows.md's own tables, transcribed independently of demo.py
# so a transcription mistake in either place is caught by the other
# disagreeing, not by this test checking demo.py against itself. ---
# (CANVAS_RIDES/CANVAS_RIDERS/CANVAS_STANDINGS/CANVAS_ENTRY_HEADER/
# CANVAS_ENTRY_MEMBERS/CANVAS_LAPS left _lists_common with E5.4.2: the
# app's no-store library, bootstrap rider editor, results and entry
# detail now assert the empty state; the entry-detail bitmap
# capability suite still drives the two card-key sets below from demo,
# the test-only fixture.)

CANVAS_LAPS_CARD_KEYS = ("Kc", "joker")  # KC -> Kc, JK -> joker (asset_key)
CANVAS_CARDS_HELD_KEYS = ("9h", "Ks", "Kc", "joker", "4d")  # demo.py's own 5-of-9 fixture

CANVAS_PUBLISH_DEFAULTS = (
    (ids.SHOW_TIMES_CHK, False),
    (ids.LAPS_BOARD_CHK, True),
    (ids.TIME_BOARD_CHK, False),
    (ids.FULL_FIELD_CHK, True),
    (ids.ALL_CARDS_CHK, True),
)


def _model_row(model: Any, row: int, columns: range) -> tuple[str, ...]:  # noqa: ANN401
    """Return every text cell of *row*, in column order."""
    return tuple(model.GetValueByRow(row, col) for col in columns)


def demo_seeded_roster() -> Roster:
    """Build the mixed, rider_pooled roster demo's four rows seed.

    The E3.2-era ``rivercrossing.ui.app._seed_roster(DemoDataSource())``
    helper moved here by E5.4.2: the bootstrap no longer seeds a roster
    from a data source (no store-backed ride is open -- the roster is
    empty), and the only remaining callers are tests building a seeded
    mixed roster from the test-only demo fixture (test_rider_editor.py,
    test_harness.py). ``DemoDataSource`` stays importable from tests.
    """
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=4,
    )
    for team_name, rows in itertools.groupby(DemoDataSource().riders(), key=lambda row: row.team):
        if team_name is None:
            for row in rows:
                roster.create_solo_entry(first_name=row.name, last_name="", plate=row.plate)
            continue
        roster.create_team_entry(
            display_name=team_name,
            riders=[Rider(first_name=row.name, last_name="", plate=row.plate) for row in rows],
        )
    return roster


def _spy_repaint(control: Any) -> tuple[MagicMock, MagicMock]:  # noqa: ANN401
    """Replace *control*'s Refresh/Update with spies; return both.

    Monkeypatching a real wx control's bound methods is a
    platform/GUI I/O boundary (T-10), the same category
    ``test_dialog_behavior.py``'s own ``_spy_on_set_focus`` already
    treats as legitimate to spy on directly in this codebase.

    *control* must stay referenced by a local in the caller for as
    long as the spy needs to see calls: measured (a throwaway probe
    script, per this repo's convention), wxPython's wrapper cache is
    weak, and a ``FindWindowByName`` result with no other surviving
    Python reference is collected -- the *next* lookup of the same
    control then builds a brand-new wrapper, missing this one's
    instance attributes entirely.
    """
    refresh, update = MagicMock(), MagicMock()
    control.Refresh = refresh
    control.Update = update
    return refresh, update
