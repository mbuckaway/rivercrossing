# SPDX-License-Identifier: GPL-3.0-only
"""Console presenter -- main_frame (1a), the live-timing screen.

``ConsoleView``/``ConsolePresenter`` are module-skeletons.md's
verbatim contract (ui.presenters section) -- names and signatures
below are binding, not derived. Pure Python -- no ``wx`` import may
ever land here (R-71).
"""

from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ride import RideStatus
    from rivercrossing.ui.presenters.data_source import Counters, DataSource, FeedRow


class Cue(Enum):
    """Audio cue identifiers for console feedback (Spec §10).

    Presenter-local placeholder: ``rivercrossing.ui.sound`` (module-
    skeletons.md's ``sound.play(Cue.RECORDED | Cue.FLAGGED |
    Cue.ERROR)``) owns the real cue player and its ``Cue`` type once
    that module lands; ``ui/sound.py`` is outside this task's file
    batch, so ``ConsoleView.play`` is typed against this interim enum
    using the same member names module-skeletons.md gives.
    """

    RECORDED = "recorded"
    FLAGGED = "flagged"
    ERROR = "error"


@runtime_checkable
class ConsoleView(Protocol):
    """View surface for the main console (main_frame, 1a)."""

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first."""
        ...

    def show_counters(self, c: Counters) -> None:
        """Render the four counter chips."""
        ...

    def flash_crossing(self, r: FeedRow) -> None:
        """Highlight the just-recorded crossing (last_crossing_lbl)."""
        ...

    def set_state(self, status: RideStatus) -> None:
        """Reflect the ride's lifecycle state (clock, entry, banner)."""
        ...

    def focus_entry(self) -> None:
        """Return keyboard focus to the plate entry field."""
        ...

    def show_notice(self, text: str) -> None:
        """Show a transient notice (the status bar's first field)."""
        ...

    def clear_entry(self) -> None:
        """Empty the plate entry field."""
        ...

    def play(self, cue: Cue) -> None:
        """Play the audio cue for the given event."""
        ...


class ConsolePresenter:
    """Presenter for the main console (main_frame, 1a).

    No-op beyond storing its collaborators -- Phase 5 binds these
    methods to the real wx view; the bodies here are the contract
    module-skeletons.md names, not yet the behaviour behind it.
    """

    def __init__(self, view: ConsoleView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source

    def on_plate_entered(self, text: str) -> None:
        """Handle Enter (or Record) with the plate entry's text.

        D1 has no ride engine yet (EPIC 4): a blank/whitespace-only
        submission only returns focus (A3); anything else posts the
        placeholder "recorded" notice (A5), clears the field, then
        refocuses -- in that order, so a second, wrongly re-fired
        submit for the same keypress cannot repeat the notice before
        the field is emptied.
        """
        plate = text.strip()
        if not plate:
            self.view.focus_entry()
            return
        self.view.show_notice(f"Plate {plate} — recording engine lands in EPIC 4")
        self.view.clear_entry()
        self.view.focus_entry()

    def on_undo(self) -> None:
        """Handle Undo last (Ctrl+Z / undo_btn)."""

    def on_arm_stop(self, *, armed: bool) -> None:
        """Handle the arm_stop_chk toggle guarding stop_btn."""

    def on_stop_confirmed(self) -> None:
        """Handle confirmation from stop_confirm_dlg."""

    def on_hide_times(self, *, hide: bool) -> None:
        """Handle the hide-times setting toggling live."""

    def tick(self) -> None:
        """Handle a periodic clock/feed refresh tick."""
