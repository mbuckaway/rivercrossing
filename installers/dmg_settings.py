# SPDX-License-Identifier: GPL-3.0-only
r"""dmgbuild settings for the unsigned RiverCrossing DMG (P8-D7).

module-skeletons.md:33 plans this exact file. Phase 8 pulls the
mechanical half of E9.1.1/E9.1.3 forward -- the drag-to-Applications
layout, volume icon and background -- while codesigning and
notarization stay release.yml's job (E9.1.3).

``dmgbuild`` execs this module as a script with a ``defines`` dict
already present in its namespace, populated from its own ``-D
key=value`` CLI flags::

    dmgbuild -s installers/dmg_settings.py \
        -D app=dist/RiverCrossing.app \
        RiverCrossing dist/RiverCrossing-<version>.dmg

``defines = globals().get("defines", {})`` keeps the module
import-safe under a plain ``import`` too, which is what
tests/unit/test_dmg_settings.py relies on to load it at all.

Measured, and not what module-skeletons.md's plan implied: dmgbuild's
own ``load_settings`` reads this file's text and runs
``exec(compile(source, filename, "exec"), settings, settings)`` --
which populates ``defines`` (verified above) but never sets
``__file__``, unlike a plain ``import`` (or the
``importlib.util.spec_from_file_location`` loader the unit tests
use, which does). ``nox -s dmg`` always runs dmgbuild from the repo
root -- the same working directory a plain ``nox`` invocation uses --
so :data:`_REPO_ROOT` falls back to ``Path.cwd()`` exactly when
``__file__`` is absent.
"""

from pathlib import Path

defines = globals().get("defines", {})

_REPO_ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
_BRANDING_DIR = _REPO_ROOT / "installers" / "branding"

# The .app to package. `nox -s dmg` passes ``-D app=...`` explicitly;
# a plain import (the unit tests) falls back to the dev bundle's
# default output path.
application = defines.get("app", str(_REPO_ROOT / "dist" / "RiverCrossing.app"))

files = [application]
symlinks = {"Applications": "/Applications"}

# dmgbuild copies `icon` verbatim to /.VolumeIcon.icns (must already
# be a real .icns) and `background` with its extension preserved --
# the committed multi-resolution .tiff works directly.
icon = str(_BRANDING_DIR / "RiverCrossing.icns")
background = str(_BRANDING_DIR / "dmg_background.tiff")

# Sized exactly to the background art (660x400); test_dmg_settings.py
# couples this to the committed .tiff so the two can never drift.
window_rect = ((200, 120), (660, 400))
icon_size = 128
icon_locations = {
    "RiverCrossing.app": (170, 200),
    "Applications": (490, 200),
}

format = "UDZO"  # noqa: A001 -- dmgbuild's own settings-module contract, not ours to rename
default_view = "icon-view"
