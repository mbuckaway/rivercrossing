# SPDX-License-Identifier: GPL-3.0-only
"""Help ▸ User Guide / F1: open the bundled user guide (E8.2.2).

The ``mi_user_guide`` route (a COMMAND row -- commands.py) opens
``docs/user-guide.html`` in the OS-default browser, deep-linked to
the anchor for whatever window is active, so Help from the settings
dialog lands on the Settings chapter and Help from a correction
dialog lands on Fixing mistakes. :data:`ANCHOR_BY_WINDOW` is that
window-name -> guide-anchor map; the route handler in ``ui.app``
resolves the active top-level window and calls :func:`anchor_for`
with its name.

Everything here is wx-free -- the map, the lookup, the guide path
and the URL building all run without a display, mirroring
``zoom.py``/``theme.py`` -- so the mapping logic is exactly what
R-71's >=90% branch-coverage gate covers in the headless suite; the
active-window resolution lives in ``ui.app``'s route handler and is
proven by the functional scenarios (tests/functional/
test_user_guide.py).

The guide ships in the repo's ``docs/`` directory beside the package
for now; E9 bundles it into the installed package, where
:func:`guide_path`'s module-relative resolution follows it.
"""

import webbrowser
from pathlib import Path

from rivercrossing.ui import ids

__all__ = [
    "ANCHOR_BY_WINDOW",
    "DEFAULT_ANCHOR",
    "GuideMissingError",
    "anchor_for",
    "guide_path",
    "open_guide",
]

DEFAULT_ANCHOR = "getting-started"

# Window/dialog XRC name -> user-guide anchor (docs/user-guide.html's
# section ids). Every value must exist as an ``id="..."`` in the guide;
# tests/unit/ui/test_help.py enforces that, so a guide rewrite that
# renames a section fails here before a deep link can go stale.
ANCHOR_BY_WINDOW: dict[str, str] = {
    # Windows/dialogs with their own chapter.
    ids.SETTINGS_DLG: "settings",
    ids.ABOUT_DLG: "about",
    ids.SHORTCUTS_DLG: "appendix-a-shortcuts",
    ids.RIDE_SETUP_DLG: "setting-up-a-ride",
    ids.RIDER_EDITOR_DLG: "riders-entries",
    ids.CSV_PREVIEW_DLG: "appendix-b-csv-reference",
    ids.ENTRY_DETAIL_DLG: "entry-detail",
    ids.RESULTS_FRAME: "results-window",
    ids.RIDE_LIBRARY_DLG: "getting-started",
    ids.AUDIT_DLG: "fixing-mistakes",
    # The correction dialogs all land on Fixing mistakes.
    ids.EDIT_CROSSING_DLG: "fixing-mistakes",
    ids.REASSIGN_DLG: "fixing-mistakes",
    ids.MANUAL_DEAL_DLG: "fixing-mistakes",
    ids.DNF_CONFIRM_DLG: "fixing-mistakes",
    ids.VOID_CARD_CONFIRM_DLG: "fixing-mistakes",
    # The lifecycle dialogs land on Stopping, quitting & recovery.
    ids.STOP_CONFIRM_DLG: "stopping-quitting-recovery",
    ids.CONTINUE_OR_NEW_DLG: "stopping-quitting-recovery",
    ids.RESUME_DLG: "stopping-quitting-recovery",
    ids.EXIT_RUNNING_DLG: "stopping-quitting-recovery",
    ids.EXIT_CONFIRM_DLG: "stopping-quitting-recovery",
    ids.DELETE_RIDE_DLG: "stopping-quitting-recovery",
    ids.SET_START_DLG: "stopping-quitting-recovery",
    # Finishing & standings: the finish confirm and the two mock-first
    # confirms that act on a finished ride.
    ids.FINISH_CONFIRM_DLG: "finishing-standings",
    ids.DUPLICATE_RIDE_DLG: "finishing-standings",
    ids.REOPEN_RIDE_DLG: "finishing-standings",
}


class GuideMissingError(FileNotFoundError):
    """Raised when the bundled user-guide file is absent."""


def anchor_for(window_name: str | None) -> str:
    """Return the user-guide anchor for *window_name* (E8.2.2).

    Every deep-linked opener lands on the chapter that explains it; a
    window with no mapping -- the main frame, or an unknown name --
    falls back to the guide's opening chapter.

    Args:
        window_name: A top-level window's XRC name, or ``None`` when
            no window is active.

    Returns:
        The guide anchor for *window_name*.
    """
    if window_name is None:
        return DEFAULT_ANCHOR
    return ANCHOR_BY_WINDOW.get(window_name, DEFAULT_ANCHOR)


def guide_path() -> Path:
    """Return the bundled user-guide file's path (E8.2.2).

    Prefers the E9 bundled location (``rivercrossing/docs/`` beside
    this module) and falls back to the source checkout's own
    ``docs/`` directory, so the same call works before and after the
    guide is bundled into the installed package.

    Returns:
        The guide's absolute path.

    Raises:
        GuideMissingError: If the guide file is absent.
    """
    module_path = Path(__file__).resolve()
    for candidate in (
        module_path.parents[1] / "docs" / "user-guide.html",
        module_path.parents[3] / "docs" / "user-guide.html",
    ):
        if candidate.is_file():
            return candidate
    raise GuideMissingError(
        f"user guide not found at {module_path.parents[1] / 'docs' / 'user-guide.html'}"
    )


def open_guide(anchor: str | None = None) -> str:
    """Open the user guide at *anchor* in the OS-default browser.

    The URL is the return value -- the seam the app posts to the
    status bar and the functional suite captures -- so this stays
    thin about what it opens.

    Args:
        anchor: The guide anchor (section id) to deep-link to;
            defaults to :data:`DEFAULT_ANCHOR`.

    Returns:
        The opened ``file://`` URL, including the ``#anchor``
        fragment.

    Raises:
        GuideMissingError: If the guide file is absent.
    """
    resolved = anchor if anchor is not None else DEFAULT_ANCHOR
    url = f"{guide_path().as_uri()}#{resolved}"
    webbrowser.open(url)
    return url
