# SPDX-License-Identifier: GPL-3.0-only
"""PyInstaller spec for the RiverCrossing dev bundle (E1.6.1).

module-skeletons.md S2 lists exactly one spec -- "PyInstaller (both
OSes, one spec)" -- so the platform differences live inside it, in
one ``sys.platform`` branch at the bottom: macOS additionally wraps
the onedir folder in a ``.app`` with an ``Info.plist``.

spec.md section 14 stage 5, dev-bundle mode: **onedir, unsigned**.
Signing, notarization, the ``.dmg`` and the Inno installer are EPIC
9 (E9.1.2 / E9.1.3), as are the app icons -- there is no ``.icns``
or ``.ico`` in the tree to point at yet.

Two things here are load-bearing and easy to get wrong.

**Assets land on the package path.** ``Analysis(datas=...)`` comes
from ``tools/check_asset_manifest.py``, which maps every ``.xrc``,
card bitmap and WAV cue to ``rivercrossing/ui/...`` inside the
bundle. A frozen module's ``__file__`` points at
``sys._MEIPASS/rivercrossing/ui/__init__.pyc``, so
``cards_imagelist.cards_dir()`` and ``harness.xrc_directory()`` --
both of which resolve assets relative to the package -- only find
anything if the data sits on that same relative path. That same
call verifies the manifest first, which is what makes a missing
asset fail *this build* rather than the app's first paint.

**The UI is reached by name, not by import.** Windows come from XRC
at runtime, so PyInstaller's import graph cannot see most of the
package -- ``collect_submodules`` ships all of ``rivercrossing`` for
that reason -- and the wx submodules whose classes appear only as
XRC ``class=`` strings are named explicitly below.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 -- PyInstaller injects SPECPATH
sys.path.insert(0, str(ROOT / "tools"))

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

import rivercrossing  # noqa: E402 -- check_asset_manifest puts src/ on the path
from check_asset_manifest import data_entries  # noqa: E402 -- needs the path above

APP_NAME = "RiverCrossing"  # the .app and its display name
EXE_NAME = "rivercrossing"  # the executable and the onedir folder
# Reverse-DNS form of the project's own repository URL
# (pyproject.toml [project.urls] Repository). E9.1.3 must confirm it
# against whatever identifier the Apple developer account signs.
BUNDLE_ID = "io.github.mbuckaway.rivercrossing"

ENTRY_SCRIPT = ROOT / "src" / "rivercrossing" / "__main__.py"
UI_DIR = ROOT / "src" / "rivercrossing" / "ui"

# The wx submodules the XRC files reach by class name. Measured, by
# building this spec both ways and diffing the packaged module list
# and dylibs:
#
# * wx.xrc is load-bearing. Without it the bundle ships no
#   _xrc/_xml extension modules and no libwx_*_xrc/libwx_baseu_xml
#   at all, so not one window could load. Nothing in the app's
#   import graph names it -- the harness does, and the harness is
#   not in the bundle.
# * wx.adv arrives transitively today, and wx.dataview because the
#   view modules import it. Both are still declared: XRC reaches
#   wxTimePickerCtrl, wxDatePickerCtrl, wxHyperlinkCtrl and
#   wxEditableListBox (wx.adv) and wxDataViewCtrl (wx.dataview) by
#   name from a file, so no import site guarantees them, and a
#   refactor that dropped the last `import wx.dataview` would
#   silently degrade every DataView window instead of failing here.
#
# wx.html is deliberately absent -- no .xrc uses wxHtmlWindow; add
# it when About > Licenses... does. wx.lib is not collected
# wholesale either (measured: wx.lib.newevent raises
# ModuleNotFoundError inside a built bundle), so any future wx.lib
# use needs its own entry.
WX_HIDDEN_IMPORTS = ["wx.adv", "wx.dataview", "wx.xrc"]

HIDDEN_IMPORTS = [*WX_HIDDEN_IMPORTS, *collect_submodules("rivercrossing")]

INFO_PLIST = {
    # Measured: CFBundleName is what the app menu is titled and what
    # "Quit <name>"/"Hide <name>" read, so without it macOS labels
    # them from the executable ("Quit rivercrossing").
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleExecutable": EXE_NAME,
    # BUNDLE's own `version` argument only sets
    # CFBundleShortVersionString; macOS wants the build version too.
    "CFBundleVersion": rivercrossing.__version__,
    # A window that draws at 1x on a Retina display would make the
    # 2x half of the card deck (cards_imagelist's -2x files) dead
    # weight; without this key macOS scales a 1x backing store up.
    "NSHighResolutionCapable": True,
    # R-03's dark theme: opting out of the legacy Aqua-only
    # appearance is what lets the process see the OS dark setting at
    # all, which wx.App.SetAppearance then follows.
    "NSRequiresAquaSystemAppearance": False,
}

analysis = Analysis(  # noqa: F821 -- PyInstaller injects Analysis
    [str(ENTRY_SCRIPT)],
    pathex=[str(ROOT / "src")],
    datas=data_entries(UI_DIR),
    hiddenimports=HIDDEN_IMPORTS,
)

pyz = PYZ(analysis.pure)  # noqa: F821 -- PyInstaller injects PYZ

executable = EXE(  # noqa: F821 -- PyInstaller injects EXE
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,  # onedir: the binaries ride in COLLECT
    name=EXE_NAME,
    strip=False,
    upx=False,
    console=False,
    # A GUI app with no terminal: PyInstaller's windowed-traceback
    # handler would pop a modal on an unhandled exception, and CI has
    # nobody to dismiss it -- the same exit-time hang wx's own log
    # target causes (tests/functional/conftest.py). Off, so a crash
    # goes to stderr where the smoke test reads it.
    disable_windowed_traceback=True,
    argv_emulation=False,
    codesign_identity=None,  # unsigned dev bundle (spec.md section 14)
    entitlements_file=None,
    icon=None,  # no .icns/.ico in the tree yet -- E9.1.1
)

collected = COLLECT(  # noqa: F821 -- PyInstaller injects COLLECT
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=EXE_NAME,
)

if sys.platform == "darwin":
    # The .app is what makes this a real foreground GUI process, and
    # it matters more than it looks. Measured with a throwaway frozen
    # app over this same spec: launched through LaunchServices
    # (`open -a`), the window is active, wx.Window.FindFocus()
    # returns the focused control, and macOS moves About / Settings /
    # Quit out of the XRC menus into the app menu. Launching the same
    # binary straight out of Contents/MacOS from a terminal gets none
    # of that -- it inherits the terminal's bundle identity, the
    # window never activates and FindFocus() stays None.
    app = BUNDLE(  # noqa: F821 -- PyInstaller injects BUNDLE
        collected,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier=BUNDLE_ID,
        version=rivercrossing.__version__,
        info_plist=INFO_PLIST,
    )
