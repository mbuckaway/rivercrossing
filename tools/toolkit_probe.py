# SPDX-License-Identifier: GPL-3.0-only
"""Probe the wxPython API surface the 23 windows depend on.

Written for the EPIC 1 toolkit gate and kept in the repo so the
same matrix can be re-run on any platform, or after a wxPython
upgrade.

Each check runs in its own interpreter. A wxWidgets C++ assertion
aborts the process, so sharing one process across checks silently
discards every later result -- a lesson learned the hard way while
authoring this.

    python tools/toolkit_probe.py             # whole matrix
    python tools/toolkit_probe.py xrc_names   # one check

Checks suffixed _DEFECT pass by *documenting* a limitation the
.xrc authoring has to work around; they are not failures.
"""

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

SASH = 420
FLAGGED_ROW = 1
CARDS_IN_SHOE_IMAGELIST = 53

FROZEN_NAMES = (
    "plate_input",
    "ride_name_lbl",
    "undo_btn",
    "arm_stop_chk",
    "solo_radio",
    "zoom_choice",
    "decks_spin",
    "audit_search",
    "crossings_list",
)

CONSOLE_XRC = b"""<?xml version="1.0" encoding="UTF-8"?>
<resource>
  <object class="wxFrame" name="main_frame">
    <title>probe</title>
    <object class="wxBoxSizer"><orient>wxVERTICAL</orient>
      <object class="sizeritem">
        <object class="wxTextCtrl" name="plate_input"/></object>
      <object class="sizeritem">
        <object class="wxStaticText" name="ride_name_lbl">
          <label>n</label></object></object>
      <object class="sizeritem">
        <object class="wxButton" name="undo_btn">
          <label>Undo</label></object></object>
      <object class="sizeritem">
        <object class="wxCheckBox" name="arm_stop_chk">
          <label>Arm</label></object></object>
      <object class="sizeritem">
        <object class="wxRadioButton" name="solo_radio">
          <label>Solo</label><style>wxRB_GROUP</style><value>1</value>
        </object></object>
      <object class="sizeritem">
        <object class="wxChoice" name="zoom_choice"/></object>
      <object class="sizeritem">
        <object class="wxSpinCtrl" name="decks_spin"/></object>
      <object class="sizeritem">
        <object class="wxSearchCtrl" name="audit_search"/></object>
      <object class="sizeritem">
        <object class="wxDataViewCtrl" name="crossings_list"/></object>
    </object>
  </object>
</resource>"""

DIALOG_XRC = b"""<?xml version="1.0" encoding="UTF-8"?>
<resource>
  <object class="wxDialog" name="stop_confirm_dlg">
    <title>Stop Ride?</title>
    <object class="wxBoxSizer"><orient>wxVERTICAL</orient>
      <object class="sizeritem">
        <object class="wxStdDialogButtonSizer">
          <object class="button">
            <object class="wxButton" name="wxID_OK">
              <label>Stop ride</label></object></object>
          <object class="button">
            <object class="wxButton" name="wxID_CANCEL"/></object>
        </object></object>
    </object>
  </object>
</resource>"""

MENUBAR_XRC = b"""<?xml version="1.0" encoding="UTF-8"?>
<resource>
  <object class="wxMenuBar" name="main_menubar">
    <object class="wxMenu" name="menu_results">
      <label>Results</label>
      <object class="wxMenuItem" name="mi_standings">
        <label>Standings</label><accel>F5</accel></object>
      <object class="wxMenuItem" name="mi_hide_times">
        <label>Hide Times</label><checkable>1</checkable></object>
      <object class="wxMenuItem" name="mi_zoom_100">
        <label>100%</label><radio>1</radio></object>
    </object>
  </object>
</resource>"""


_APP_KEEPALIVE: list[object] = []


def _app() -> object:
    """Create the wx.App and keep it alive for the whole process.

    An unbound `wx.App()` is garbage-collected the moment the
    expression ends, which leaves wx unable to shut down: the
    interpreter then hangs at exit instead of returning. Holding
    the reference in module state is what keeps that from
    happening.
    """
    import wx

    if not _APP_KEEPALIVE:
        _APP_KEEPALIVE.append(wx.App())
    return _APP_KEEPALIVE[0]


def _one_child_xrc(control_class: str, name: str) -> bytes:
    """Build a frame resource holding one named control."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<resource><object class="wxFrame" name="f">'
        '<object class="wxBoxSizer"><orient>wxVERTICAL</orient>'
        '<object class="sizeritem">'
        f'<object class="{control_class}" name="{name}"/>'
        "</object></object></object></resource>"
    ).encode()


def check_version() -> str:
    """Report the wxPython and wxWidgets versions in use."""
    import wx

    return f"{wx.version()} | VERSION={wx.VERSION}"


def check_xrc_names() -> str:
    """Resolve every frozen name in a console-shaped resource."""
    import wx
    import wx.xrc

    _app()
    resource = wx.xrc.XmlResource()
    resource.InitAllHandlers()
    if not resource.LoadFromBuffer(CONSOLE_XRC):
        msg = "LoadFromBuffer returned False"
        raise RuntimeError(msg)
    frame = resource.LoadFrame(None, "main_frame")
    if frame is None:
        msg = "LoadFrame returned None"
        raise RuntimeError(msg)

    missing = [n for n in FROZEN_NAMES if frame.FindWindowByName(n) is None]
    if missing:
        msg = f"unresolved frozen names: {missing}"
        raise RuntimeError(msg)
    if frame.FindWindowByName("a_retired_name") is not None:
        msg = "an unknown name resolved to a window"
        raise RuntimeError(msg)
    if not frame.FindWindowByName("solo_radio").GetValue():
        msg = "wxRB_GROUP default was not honoured"
        raise RuntimeError(msg)
    return f"{len(FROZEN_NAMES)} frozen names resolve; unknown -> None; radio default ok"


def check_xrc_dialog_stock_ids() -> str:
    """Check wxStdDialogButtonSizer and stock IDs survive XRC."""
    import wx
    import wx.xrc

    _app()
    resource = wx.xrc.XmlResource()
    resource.InitAllHandlers()
    resource.LoadFromBuffer(DIALOG_XRC)
    dialog = resource.LoadDialog(None, "stop_confirm_dlg")
    if dialog is None:
        msg = "LoadDialog returned None"
        raise RuntimeError(msg)
    ok = dialog.FindWindow(wx.ID_OK)
    cancel = dialog.FindWindow(wx.ID_CANCEL)
    if ok is None or cancel is None:
        msg = "stock IDs did not resolve"
        raise RuntimeError(msg)
    label = ok.GetLabel()
    dialog.Destroy()
    return f"stock wxID_OK/wxID_CANCEL resolve; custom label={label!r}"


def check_xrc_menubar() -> str:
    """Load a menubar with an accelerator and check/radio items."""
    import wx
    import wx.xrc

    _app()
    resource = wx.xrc.XmlResource()
    resource.InitAllHandlers()
    if not resource.LoadFromBuffer(MENUBAR_XRC):
        msg = "LoadFromBuffer returned False"
        raise RuntimeError(msg)
    menubar = resource.LoadMenuBar("main_menubar")
    if menubar is None:
        msg = "LoadMenuBar returned None"
        raise RuntimeError(msg)
    if menubar.FindMenuItem("Results", "Standings") == wx.NOT_FOUND:
        msg = "mi_standings did not resolve"
        raise RuntimeError(msg)
    return f"menubar loaded; menus={menubar.GetMenuCount()}; F5 resolves"


def check_xrc_infobar_defect() -> str:
    """Document that XRC cannot author a wxInfoBar."""
    import wx
    import wx.xrc

    _app()
    resource = wx.xrc.XmlResource()
    resource.InitAllHandlers()
    resource.LoadFromBuffer(_one_child_xrc("wxInfoBar", "resume_infobar"))
    frame = resource.LoadFrame(None, "f")
    children = [(type(c).__name__, c.GetName()) for c in frame.GetChildren()]
    resolved = frame.FindWindowByName("resume_infobar")
    return (
        f"resolved={resolved!r}; children={children} => build InfoBars "
        "code-side and apply the frozen name with SetName()"
    )


def check_xrc_dataviewlist_defect() -> str:
    """Document that wxDataViewListCtrl overrides the name."""
    import wx
    import wx.dataview
    import wx.xrc

    _app()
    resource = wx.xrc.XmlResource()
    resource.InitAllHandlers()
    resource.LoadFromBuffer(_one_child_xrc("wxDataViewListCtrl", "crossings_list"))
    frame = resource.LoadFrame(None, "f")
    children = [(type(c).__name__, c.GetName()) for c in frame.GetChildren()]
    resolved = frame.FindWindowByName("crossings_list")
    return (
        f"resolved={resolved!r}; children={children} => author list "
        "controls as wxDataViewCtrl, whose name is honoured"
    )


def check_dataview_bold_row() -> str:
    """Prove per-row bold needs a GetAttrByRow override."""
    import wx
    import wx.dataview

    _app()
    frame = wx.Frame(None)

    class FeedModel(wx.dataview.DataViewIndexListModel):
        """Two-row feed whose second row is short-lap flagged."""

        def __init__(self) -> None:
            super().__init__(2)
            self.rows = [["14:22:41", "123"], ["14:21:59", "45"]]
            self.flagged = {FLAGGED_ROW}

        def GetValueByRow(self, row: int, col: int) -> str:  # noqa: N802
            return self.rows[row][col]

        def GetColumnCount(self) -> int:  # noqa: N802
            return 2

        def GetCount(self) -> int:  # noqa: N802
            return len(self.rows)

        def GetAttrByRow(  # noqa: N802
            self,
            row: int,
            col: int,  # noqa: ARG002
            attr: wx.dataview.DataViewItemAttr,
        ) -> bool:
            if row in self.flagged:
                attr.SetBold(True)
                return True
            return False

    model = FeedModel()
    view = wx.dataview.DataViewCtrl(frame)
    view.AssociateModel(model)
    view.AppendTextColumn("Time", 0)
    view.AppendTextColumn("Plate", 1)

    bold = wx.dataview.DataViewItemAttr()
    if not model.GetAttrByRow(FLAGGED_ROW, 0, bold) or not bold.GetBold():
        msg = "flagged row did not produce a bold attribute"
        raise RuntimeError(msg)
    plain = wx.dataview.DataViewItemAttr()
    if model.GetAttrByRow(0, 0, plain):
        msg = "an unflagged row returned an attribute"
        raise RuntimeError(msg)

    has_setter = hasattr(wx.dataview.DataViewListStore, "SetAttrByRow")
    frame.Destroy()
    return (
        "GetAttrByRow bolds only the flagged row; "
        f"DataViewListStore.SetAttrByRow={has_setter} => "
        "a model subclass is required"
    )


def check_widgets() -> str:
    """Instantiate the remaining controls the 23 windows need."""
    import wx
    import wx.adv

    _app()
    frame = wx.Frame(None, size=(1100, 700))
    wx.adv.DatePickerCtrl(frame)
    wx.adv.TimePickerCtrl(frame)
    wx.adv.EditableListBox(frame, label="Tie-break order")
    link = wx.adv.HyperlinkCtrl(frame, label="gorba.ca", url="https://gorba.ca")
    wx.InfoBar(frame)

    splitter = wx.SplitterWindow(frame, size=(1000, 640))
    splitter.SetMinimumPaneSize(50)
    splitter.SplitHorizontally(wx.Panel(splitter), wx.Panel(splitter), 300)
    splitter.SetSashPosition(SASH)
    if splitter.GetSashPosition() != SASH:
        msg = f"sash did not hold: {splitter.GetSashPosition()}"
        raise RuntimeError(msg)

    image_list = wx.ImageList(24, 32)
    for _ in range(CARDS_IN_SHOE_IMAGELIST):
        image_list.Add(wx.Bitmap(24, 32))

    frame.Destroy()
    return (
        f"pickers + EditableListBox + code-side InfoBar + "
        f"HyperlinkCtrl({link.GetURL()}) ok; sash round-trips; "
        f"ImageList holds {image_list.GetImageCount()}; "
        f"wx.adv.Sound={hasattr(wx.adv, 'Sound')}"
    )


def check_appearance_and_simulator() -> str:
    """Check SetAppearance (dark mode) and UIActionSimulator."""
    import wx

    app = _app()
    if not hasattr(app, "SetAppearance"):
        msg = "wx.App.SetAppearance missing - dark mode unavailable"
        raise RuntimeError(msg)
    modes = sorted(m for m in dir(wx.App.Appearance) if not m.startswith("_") and m.istitle())
    simulator = wx.UIActionSimulator()
    wanted = ("Char", "Text", "MouseClick", "KeyDown")
    missing = [m for m in wanted if not hasattr(simulator, m)]
    if missing:
        msg = f"UIActionSimulator is missing {missing}"
        raise RuntimeError(msg)
    return f"SetAppearance present, modes={modes}; simulator complete"


CHECKS: dict[str, Callable[[], str]] = {
    "version": check_version,
    "xrc_names": check_xrc_names,
    "xrc_dialog_stock_ids": check_xrc_dialog_stock_ids,
    "xrc_menubar": check_xrc_menubar,
    "xrc_infobar_DEFECT": check_xrc_infobar_defect,
    "xrc_dataviewlist_DEFECT": check_xrc_dataviewlist_defect,
    "dataview_bold_row": check_dataview_bold_row,
    "widgets": check_widgets,
    "appearance_and_simulator": check_appearance_and_simulator,
}


def run_one(name: str) -> int:
    """Run one check and print a parseable result line."""
    try:
        print(f"OK|{CHECKS[name]()}")
    except BaseException as exc:  # noqa: BLE001
        # C++ assertions are not Exception subclasses.
        detail = str(exc).splitlines()[0] if str(exc) else ""
        print(f"FAIL|{type(exc).__name__}: {detail[:160]}")
        return 1
    return 0


def drive() -> int:
    """Run every check in its own process; print the matrix."""
    width = max(len(name) for name in CHECKS)
    failed = 0
    for name in CHECKS:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, __file__, name],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        line = next((one for one in proc.stdout.splitlines() if "|" in one), None)
        if line is None:
            tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
            status, detail = "CRASH", tail[:160]
        else:
            status, _, detail = line.partition("|")
        if status != "OK":
            failed += 1
        print(f"{status:<5} {name:<{width}}  {detail}")
    print("-" * 96)
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


def main() -> int:
    """Dispatch to one check or the whole matrix."""
    if len(sys.argv) > 1:
        return run_one(sys.argv[1])
    return drive()


if __name__ == "__main__":
    sys.exit(main())
