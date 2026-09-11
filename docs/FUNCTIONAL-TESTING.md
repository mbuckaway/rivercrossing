# RiverCrossing — Writing Functional Tests

How RiverCrossing functional tests **must** be written. This document replaces the
earlier direct-injection guidance; the suite is being reworked to drive the real app
through real input, and this is the contract the rewrite targets.

Read it before adding or rewriting any test under `tests/functional/`.

---

## 1. The mandate

A functional test is not a unit test with a window. A functional test:

1. **loads the entire application** through the real bootstrap — the real
   `RiverCrossingApp`, the real `main_frame`, the real store, the real wiring — never a
   single dialog or control constructed in isolation; and
2. **drives it the way an operator drives it** — real mouse clicks, real keystrokes,
   real menu/accelerator input through `wx.UIActionSimulator`, with modals handled by
   `wx.ModalDialogHook` so nothing ever blocks the run.

The prior approach — loading one dialog at a time and injecting synthetic
`ProcessEvent`/`SetValue` events — is deprecated. It tests parts, not the app, and it is
the source of the suite's unreliability (including the modal hangs). The rewrite below
replaces it.

---

## 2. The required stack

| Component | Role | Status |
|---|---|---|
| `pytest` + `pytest-xdist` | process isolation: one `wx.App` per worker process | adopt |
| `WidgetTestCase`-style base (ported from wxPython's `wtc.py`) | the fixture that owns the app, the frame, the loop, and teardown | add |
| `wx.UIActionSimulator` | the interaction layer — real clicks, keys, text, menus | adopt as the driver |
| `wx.ModalDialogHook` (registered globally in the fixture) | intercept and answer every modal so the run never hangs | **required** |
| XRC frozen names (`ui/ids.py`) via `FindWindowByName` | the only selectors | keep |
| `EventCounter` | count events fired, instead of asserting only end-state | add |
| `waitUntil(predicate, timeout)` | event-driven waits; never `sleep` | add |
| direct `ProcessEvent` injection | **reserved** for menus and cases where real input is disproportionately fragile — named explicitly | narrow exception |
| a disposable Tart VM (macOS) | the isolated desktop session the suite runs in | required (§7) |

---

## 3. Process isolation with pytest-xdist

Each xdist worker is its own process, so each worker owns a single, clean `wx.App` and a
fresh SIP wrapper map. This is what makes the wrapper-cache corruption and cross-test
leakage impossible: a test that crashes its window cannot poison another worker's
controls, and a fresh process has nothing stale to resolve.

- Run the suite distributed across workers (`pytest -n 2`; the count is tuned per runner).
- Never use `--forked` on macOS: forking a process that has already initialised
  `NSApplication` is unsafe. xdist's spawn-based workers are the mechanism.
- The current `tools/functional_perfile.py` per-file-subprocess wrapper and the
  `scenario_runner` child-spawn pattern are the old isolation model being replaced by
  plain xdist workers, each running a whole-app fixture.

---

## 4. The `WidgetTestCase`-style base class

Port wxPython's own `unittests/wtc.py` base to pytest. It owns the one `wx.App`, a shown
frame, the yield primitive, and the teardown that force-collects. The essential pieces:

```python
class WidgetTestCase:
    def setUp(self):
        self.app = wx.App()
        wx.Log.SetActiveTarget(wx.LogStderr())
        self.frame = wx.Frame(None, title="WTC: " + type(self).__name__)
        self.frame.Show()
        self.frame.PostSizeEvent()

    def tearDown(self):
        def _cleanup():
            for tlw in wx.GetTopLevelWindows():
                if tlw:
                    if isinstance(tlw, wx.Dialog) and tlw.IsModal():
                        tlw.EndModal(0)
                        wx.CallAfter(tlw.Destroy)
                    else:
                        tlw.Close(force=True)
            wx.WakeUpIdle()
        timer = wx.PyTimer(_cleanup)
        timer.Start(100)
        self.app.MainLoop()
        del self.app
        gc.collect()          # evicts stale SIP wrapper entries

    def myYield(self, eventsToProcess=wx.EVT_CATEGORY_ALL):
        # A real event loop must be active before Yield does anything useful.
        evtLoop = self.app.GetTraits().CreateEventLoop()
        activator = wx.EventLoopActivator(evtLoop)   # restores the old loop on exit
        evtLoop.YieldFor(eventsToProcess)
```

Two things carry over exactly as `wtc.py` does them, and both are load-bearing:

- **`gc.collect()` in teardown** — without it a reference cycle keeps the `wx.App` and
  its window wrappers alive, and they get reaped later mid-suite and crash a later test.
- **`myYield` creates and activates a real event loop** — before `MainLoop` runs there
  is no loop, so a bare `wx.Yield()` does nothing; the loop must be created and activated
  first.

In pytest form this becomes a session- or class-scoped fixture rather than an
`unittest.TestCase`, but the operations are identical.

---

## 5. Load the entire app

Do not `LoadDialog`/`LoadFrame` a single window. Build the app the way `main()` does,
from the real bootstrap seams in `src/rivercrossing/ui/app.py`:

```python
import wx
from rivercrossing.ui import app as app_module

app = app_module.build_app()                     # the one live RiverCrossingApp
wx.Log.SetActiveTarget(wx.LogStderr())
frame, store = app_module._bootstrap_window(     # open the store + build the real window
    app,
    db_path=tmp_rides_db,                        # a staged, per-test rides.db
)
frame.Show()
```

- `build_app()` (`app.py:3309`) constructs the real `RiverCrossingApp`.
- `_bootstrap_window(...)` (`app.py:3314`) is the store-backed bootstrap split out of
  `main()` precisely so a test can drive the whole open-store-and-build path without
  entering `MainLoop` (which blocks). It opens the `Store` and calls
  `build_main_window(...)` (`app.py:2957`) — the function that loads every packaged XRC
  resource, wires the console, attaches the menubar, applies the accelerator table, binds
  every §15 route, and wires the quit paths.
- The window is **complete but not yet shown** when built; `Show()` then focus it (see §6).

Select controls **by their frozen XRC names** through `FindWindowByName`, scoped to the
frame so a same-named control in another window cannot resolve:

```python
plate_input = frame.FindWindowByName(ids.PLATE_INPUT)
```

`ui/ids.py` is generated from the `.xrc` files — the frozen registry (`spec.md` §15b).

---

## 6. Interaction via `wx.UIActionSimulator`

`wx.UIActionSimulator` is the interaction layer. It synthesizes real OS-level input —
the same input a user's mouse and keyboard produce — so the app's native handlers,
focus traversal, default-button dispatch, and accelerator table all run.

The one operational requirement of real input: **the target window must be shown and
focused.** Before simulating, raise and focus it:

```python
frame.Raise()
frame.SetFocus()
app.SetTopWindow(frame)
```

Then drive:

```python
sim = wx.UIActionSimulator()

# Click a control: compute its screen point, then click it.
control = frame.FindWindowByName(ids.RECORD_BTN)
rect = control.GetScreenRect()
sim.MouseMove(rect.GetCentre())
sim.MouseClick()

# Type text: focus the field, then type the keys.
plate_input.SetFocus()
sim.Text("12")
sim.Char(wx.WXK_RETURN)   # Enter → EVT_TEXT_ENTER
```

Methods to use: `MouseMove`, `MouseDown`, `MouseUp`, `MouseClick`, `MouseDblClick`,
`KeyDown`, `KeyUp`, `Char`, `Text`. Notes from the official docs:

- `MouseMove` takes **screen coordinates**.
- `Text()` types a US-ASCII string; digits and punctuation assume the standard QWERTY
  (US) layout.
- Pair every `KeyDown` with a matching `KeyUp` so a modifier is not left held.

Because the app must actually receive the input, the suite runs in a **real desktop
session** — CI's hosted desktop, or the Tart VM locally (§7). That is the environment
where "drive it as a user" is meaningful.

---

## 7. Running the suite — the Tart VM harness (macOS)

Functional tests open 23 real windows and, with `wx.UIActionSimulator`, drive real OS
input. On a developer's Mac that takes over the desktop — windows steal focus while you
type, and a crashed run can leave the session in a bad state. On macOS the suite runs
inside a **disposable Tart VM** whose guest has its own WindowServer, so nothing touches
the host session and the app is genuinely foregrounded in the guest (which real input
requires).

**Host runs are refused by default.** `nox -s functional` on macOS calls
`tools/functional_gate.py` (`host_functional_run_allowed`) and refuses unless `CI` is
set (a hosted runner, already isolated) or `RIVERCROSSING_HOST_FUNCTIONAL=1` records an
explicit opt-out. Never run the suite bare on the host desktop.

**One-time setup:**

```bash
brew install openai/tools/tart      # Tart moved from Cirrus Labs to OpenAI in 2026
scripts/setup_functional_vm.sh      # ~25 GB base image; prompts for guest password 'admin'
```

`setup_functional_vm.sh` clones `ghcr.io/cirruslabs/macos-tahoe-base:latest` into the
`rivercrossing-func-template` template, sizes it (`RIVERCROSSING_VM_CPU` default 4,
`RIVERCROSSING_VM_MEMORY` default 8192 MB), seeds a dedicated ssh key
(`~/.ssh/rivercrossing_vm_ed25519`), installs Python 3.14 + rsync, pushes the worktree,
and creates the guest `.venv`. It is idempotent — re-running re-provisions the template.

**Each run:**

```bash
scripts/run_functional_tests_vm.sh
```

The run script clones the template (APFS copy-on-write — seconds), boots it **headless**
(`tart run --no-graphics --no-audio`), waits for ssh, rsyncs the current worktree,
installs the dev deps, runs the suite inside the guest, pulls
`tests/functional/_screenshots/` back, and deletes the clone. The clone dies with a
crashed run; isolation contains a crash, it does not cure one.

| Exit code | Meaning |
|---|---|
| 2 | `tart` not installed |
| 3 | `rivercrossing-func-template` missing — run `scripts/setup_functional_vm.sh` |
| 124 | watchdog timeout (`RIVERCROSSING_VM_TIMEOUT`; the script defaults to 5400 s) |
| other | the guest's pytest exit code, passed through |

Knobs: `RIVERCROSSING_VM_TEST_PATHS` (default `tests/functional`) selects the suite;
`RIVERCROSSING_FUNCTIONAL_JOBS` (default 1) and
`RIVERCROSSING_FUNCTIONAL_PERFILE_TIMEOUT_S` (default 900) tune the guest run. (The
guest currently invokes `tools/functional_perfile.py`; §3's xdist-worker model is what
this rewrite replaces it with.)

Warnings:

- Never pass `--vnc`/`--vnc-experimental` to `tart run` without `--no-graphics` — those
  flags open Screen Sharing.app on the host desktop, defeating the isolation.
- The guest needs no Accessibility or Screen Recording grants for the app to be
  foregrounded: the `macos-*-base` images ship SIP-disabled and pre-seed
  Accessibility/PostEvent/ScreenCapture grants for ssh-launched automation, and
  auto-login `admin` into a real Aqua session.

---

## 8. Modals via `wx.ModalDialogHook` — required

The modal hangs come from letting a modal block the run with no user to dismiss it. The
fix is the hook: register a `wx.ModalDialogHook` **globally in the fixture**, and every
modal the app opens is intercepted before it can block.

`Enter(dialog)` is called before any modal is shown. If it returns anything other than
`wx.ID_NONE`, the dialog is **not shown at all** and `ShowModal()` returns that value
immediately — no blocking, no hang. Register one hook in the fixture:

```python
class AutoAnswerDialogHook(wx.ModalDialogHook):
    """Answer every modal the app opens; never let one block the run."""

    def __init__(self, answers: dict[str, int]):
        super().__init__()
        self._answers = answers
        self.seen: list[str] = []

    def Enter(self, dialog):
        self.seen.append(dialog.GetName())
        # Assert on the dialog's own controls here if a test must verify
        # its contents — the dialog object is live and complete at this point.
        return self._answers.get(dialog.GetName(), wx.ID_CANCEL)

    def Exit(self, dialog):
        pass
```

Register it once per fixture (the hook's destructor unregisters it):

```python
hook = AutoAnswerDialogHook({
    ids.STOP_CONFIRM_DLG: wx.ID_OK,
    ids.FINISH_CONFIRM_DLG: wx.ID_OK,
})
hook.Register()
```

Because `Enter` holds the live dialog before skipping it, a test can still assert on the
modal's controls (its message label, its buttons) — the contents are verified in `Enter`,
and the answer is returned without ever entering the blocking `ShowModal` loop. This is
the mechanism that makes modal hangs impossible by construction.

---

## 9. `EventCounter` and `waitUntil`

Two small helpers every functional test uses. Both are required; neither exists yet —
they land with the rewrite.

```python
class EventCounter:
    """Count how many times one event type fires on a window."""

    def __init__(self, window, event_type):
        self.count = 0
        window.Bind(event_type, self._on_event)

    def _on_event(self, _event):
        self.count += 1


def wait_until(predicate, *, timeout_s=5.0, app=None):
    """Pump the loop until *predicate* is true, then raise."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        app.Yield()               # or the WidgetTestCase.myYield() above
    raise AssertionError("condition not met within timeout")
```

- `EventCounter` proves an action fired its event (a click reached its handler), not just
  that the end state happens to look right.
- `wait_until` replaces every hand-rolled settle loop and every `time.sleep`.

---

## 10. The recipe — one whole-app functional test

```python
# SPDX-License-Identifier: GPL-3.0-only
"""A whole-app functional test: the real app, real input, hook-answered modals."""

import wx

from rivercrossing.ui import app as app_module, ids


def test_typed_plate_records_via_real_input(app_fixture) -> None:
    app, frame, store = app_fixture           # WidgetTestCase-style fixture, app shown+focused
    hook = AutoAnswerDialogHook({})             # answer any modal; see §8
    hook.Register()

    plate_input = frame.FindWindowByName(ids.PLATE_INPUT)
    plate_input.SetFocus()
    sim = wx.UIActionSimulator()
    sim.Text("12")
    sim.Char(wx.WXK_RETURN)                    # Enter → EVT_TEXT_ENTER → presenter

    wait_until(lambda: _feed_plates(frame) == ("12",), app=app)

    assert _feed_plates(frame) == ("12")
    # ...and the tearDown (gc.collect + close all top-level windows) runs automatically.
```

Steps, in order:

1. The fixture builds the **whole app** (`build_app` + `_bootstrap_window`) and shows it.
2. The fixture registers the **`wx.ModalDialogHook`** so no modal can block.
3. Focus the target control, then **simulate the real action** with `UIActionSimulator`.
4. **`wait_until`** the visible effect.
5. Assert the visible state; use **`EventCounter`** where the point is "the event fired".
6. Teardown (base class) closes every top-level window and `gc.collect()`s.

---

## 11. When direct `ProcessEvent` injection is allowed

The exception, not the default. Use direct injection **only** for:

- **Menu dispatch** — `frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, wx.xrc.XRCID(item_id)))`,
  where reconstructing a real menu click is disproportionate;
- **cases where real input is disproportionately fragile** — and then only after a real
  attempt is shown to be unreliable in the target session.

The test name must declare the layer: `…_via_injection` vs `…_real_input`. A test that
drives real input is the norm; an injection test is a named, reviewed exception.

---

## 12. Anti-patterns (the old way — do not do these)

- Loading a single dialog with `LoadDialog` and driving it in isolation.
- `SetValue()` / `SetSelection()` / `Select()` + a hand-posted `wx.CommandEvent` as the
  primary driver.
- `run_modal` / `dismiss_modal` timer-rearm scheduling instead of `wx.ModalDialogHook`.
- `tools/functional_perfile.py` / `scenario_runner` subprocess-per-file isolation instead
  of xdist workers.
- `time.sleep`, or hand-rolled settle loops instead of `wait_until`.
- Asserting only end-state and never counting the event (`EventCounter`).
- Module-scoped shared windows reused across tests; a whole-app fixture per test is the
  norm, and xdist gives each worker its own process.
- Running the suite bare on the host desktop instead of in the Tart VM (§7).

---

## 13. New-test checklist

- [ ] Loads the **whole app** through `build_app` + `_bootstrap_window`/`build_main_window`.
- [ ] Drives input with `wx.UIActionSimulator` (click / text / key), after raising and focusing the window.
- [ ] Registers a `wx.ModalDialogHook` in the fixture; no test lets a modal block.
- [ ] Finds controls by frozen XRC name (`ui/ids.py`), scoped to the frame.
- [ ] Waits with `wait_until`; never `time.sleep`.
- [ ] Asserts visible state, and uses `EventCounter` where the event itself is the point.
- [ ] Declares the interaction layer in the test name (`_real_input` / `_via_injection`).
- [ ] Tears down through the base class (`Close(force=True)` + `gc.collect()`).
- [ ] Uses xdist worker isolation; no `--forked` on macOS.
- [ ] Runs in the Tart VM on macOS (`scripts/run_functional_tests_vm.sh`); never bare on the host.

---

## 14. Outstanding fixes for the rewrite

The target model (§3, §6–§8) and the code as it stands do not yet line up. These are the
verified gaps, tracked here so none is lost. **They come first: the first functional-test
update must land these fixes before any new tests are written on the new stack.**

- **VM runner still drives the old per-file runner.** `scripts/run_functional_tests_vm.sh:229-230`
  runs `tools/functional_perfile.py` in the guest; §3 replaces it with xdist workers.
  Fix: invoke `pytest -n 2` (xdist) in the guest and drop the per-file concurrency knobs.
- **`noxfile.py`'s functional-session docstring is stale.** It says per-file concurrency
  is "2 at a time"/"2-worker default", but `tools/functional_perfile.py:80` sets
  `_DEFAULT_JOBS = 1`. Fix: rewrite the `functional` session to run xdist and delete the
  obsolete per-file prose.
- **`CONTRIBUTING.md:39-42` documents the retired runner.** Its
  `RIVERCROSSING_FUNCTIONAL_JOBS` (default `auto`) and
  `RIVERCROSSING_FUNCTIONAL_PASS_TIMEOUT_S` (default 600) describe the old xdist
  `-n`/pass-timeout model. Fix: update that section to the xdist-worker command and the
  current knobs.
- **`scripts/run_functional_tests_vm.sh` disagrees with itself on the watchdog default.**
  The header comment (lines 18-19) says `RIVERCROSSING_VM_TIMEOUT` defaults to 1800 s; the
  code (`:42`) uses 5400 s. Fix: make the comment match the code (5400 s).
- **`CONTRIBUTING.md:79-84` still describes direct injection.** It says the harness
  "injects wx events directly … no OS-level input", which §6 reverses. Fix: rewrite that
  note to the `UIActionSimulator` real-input model.

## 15. References

Repo (authoritative):

- `src/rivercrossing/ui/app.py` — `build_app` (3309), `build_main_window` (2957),
  `_bootstrap_window` (3314), `main` (3373), `_bind_routes` (2715).
- `src/rivercrossing/ui/ids.py` — the frozen XRC-name registry (`spec.md` §15b).
- `scripts/setup_functional_vm.sh` — one-time Tart VM template provisioning.
- `scripts/run_functional_tests_vm.sh` — disposable-VM run of the functional suite.
- `tools/functional_gate.py` — refuses host macOS runs outside CI.
- `CONTRIBUTING.md` — running stage 3 in a real desktop session (Tart VM / CI).

External (verified):

- `wx.UIActionSimulator` — <https://docs.wxpython.org/wx.UIActionSimulator.html>
- `wx.ModalDialogHook` — <https://docs.wxpython.org/wx.ModalDialogHook.html>
- wxPython's test base `wtc.py` (the source of the `WidgetTestCase` port) —
  <https://raw.githubusercontent.com/wxWidgets/Phoenix/master/unittests/wtc.py>
- `test_modalhook.py` (the canonical hook example) —
  <https://github.com/wxWidgets/Phoenix/blob/master/unittests/test_modalhook.py>
- pytest-xdist (process isolation) — <https://pytest-xdist.readthedocs.io/en/stable/>
