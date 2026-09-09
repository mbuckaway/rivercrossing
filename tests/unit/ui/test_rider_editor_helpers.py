# SPDX-License-Identifier: GPL-3.0-only
"""Headless W7 pins for views/rider_editor pure helpers.

The W7 rider-editor rework changes two pure facts before any window
opens: the canvas minimum becomes 1280x560 with both dimensions
applied, and a solo rider's Team cell reads the word "solo" instead
of the em dash (the user copy: 'the team in the list must be
"solo"'). Real-window geometry and row pins stay functional
(``test_rider_editor.py``, ``test_lists_demo.py``); what needs no wx
window is pinned here.
"""

from rivercrossing.ui.presenters.data_source import RiderRow
from rivercrossing.ui.views import rider_editor
from rivercrossing.ui.views.rider_editor import format_team


def test_format_team_given_a_solo_row_renders_the_word_solo() -> None:
    """A solo rider's Team cell is the literal word "solo" (W7)."""
    assert format_team(RiderRow(plate="123", name="Sam Ellis", team=None)) == "solo"


def test_format_team_given_a_team_row_renders_the_team_display_name() -> None:
    """A team rider keeps their team's display name as the cell."""
    assert (
        format_team(RiderRow(plate="77", name="A. Roy", team="Trail Blazers")) == "Trail Blazers"
    )


def test_rider_editor_min_size_is_1280_by_560() -> None:
    """W7 rework: the canvas redraws the editor at 1280x560."""
    assert rider_editor.MIN_SIZE == (1280, 560)
