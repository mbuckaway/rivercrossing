# RiverCrossing

Poker-run ride timing and scoring for Windows and macOS.

RiverCrossing is a keyboard-first desktop application for running poker-run bike rides. The operator
types a rider's plate number and presses Enter at each lap crossing; the app records the lap, deals a
card from a seeded virtual multi-deck shoe, and at the finish scores every entry's best five-card poker
hand — jokers wild, five of a kind above a royal flush — applies the configured tie-breaks, and publishes
results as a self-contained HTML page, a PDF report, a podium poster and a CSV.

It is built to survive a bad day at the scorer's table: every crossing commits to SQLite as it happens,
elapsed time comes from the wall clock rather than an in-memory timer, and a crash, an accidental stop or
a full restart costs at most the keystroke in flight.

Built for the [GORBA](https://gorba.ca) EPIC & MTB Festival, but nothing in it is specific to that event —
lap length, duration, shoe composition, card cap and tie-break order are all per-ride settings.

## Status

**EPIC 9 of 9 — v1.0.0.** The scoring engine (cards, hands, standings, ride) is wired into the running
app, every crossing persists to SQLite through the store, and results export as HTML, PDF and CSV.
Corrections — edit, void, reassign, reopen — land in an append-only audit trail, and the macOS and
Windows installers are built and smoke-tested.

See [design/docs-md/project-plan.md](design/docs-md/project-plan.md) for the full nine-EPIC plan.

## Requirements

- **Python 3.14+**
- **wxPython 4.3.1** (wxWidgets 3.3) — installed automatically; supplies the dark-mode support in R-03
- macOS 13+ or Windows 10/11

macOS and Windows both gate CI: every push runs the full static/unit/functional stages and builds the
installable artifacts on both platforms — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Install and run

```bash
git clone https://github.com/mbuckaway/rivercrossing.git
cd rivercrossing

uv venv .venv && uv pip install -e '.[dev]'
# or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'

rivercrossing            # or: python -m rivercrossing
```

## Testing on Windows

Every CI run builds two Windows installers, `RiverCrossing-<version>-windows-x64-setup.exe`
and `RiverCrossing-<version>-windows-arm64-setup.exe`, and uploads them as build artifacts.

1. Download the installer matching your Windows architecture (`-x64` or `-arm64`) from the newest
   [Release](https://github.com/mbuckaway/rivercrossing/releases) — no GitHub login needed.
   Between releases, the newest **CI** run on the
   [Actions](https://github.com/mbuckaway/rivercrossing/actions) page carries the same installers
   as the **rivercrossing-setup-windows-x64** and **rivercrossing-setup-windows-arm64** artifacts
   (unzip them; artifacts need a GitHub login).
2. Run the setup executable. **SmartScreen may warn that the app is unrecognised** — the installer is
   Authenticode-signed via SignPath once the `SIGNPATH_*` config lands, but a new-but-signed app still
   shows the "unrecognised app" warning until SmartScreen reputation accrues, so click **More info**,
   then **Run anyway** on early releases.
3. The app installs per-user — no administrator prompt — under
   `%LOCALAPPDATA%\Programs\RiverCrossing`, with a Start-menu entry. Launch **RiverCrossing** from the
   Start menu. The app opens its store-backed main window: a ride that was running at the last exit
   resumes from SQLite, otherwise you land in the empty state (no demo data), ready to create or open
   a ride.
4. Uninstall from Windows **Settings ▸ Apps**, or run `uninstall.exe` from the install directory.
   Removal cleans the install directory, the Start-menu entry and the registry entry.

## Development

```bash
nox -s lint typecheck importlint ids_drift   # static analysis
nox -s unit                                   # unit tests + coverage gate
nox -s functional                             # drives real wx windows (needs a desktop session)
nox -s bundle smoke                           # build the app bundle, then smoke it
nox -s dmg dmg_smoke                          # macOS: build the unsigned drag-to-Applications DMG, then smoke it
nox -s winsetup winsetup_smoke                # Windows installer: compile (native makensis) + smoke
nox -s gen_branding                           # regenerate the committed icon/DMG artwork from the SVGs
```

`scripts/*.sh` wrap these for convenience. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, the
gates, and the wxPython testing gotchas worth knowing before you write a UI test.

## Repository layout

| Path | Contents |
|---|---|
| `src/rivercrossing/` | The application package. Core modules are pure Python; `wx` appears only under `ui/` |
| `src/rivercrossing/ui/xrc/` | The canonical UI — every window authored as sizer-based XRC |
| `tests/` | `unit/` (headless) and `functional/` (drives real wx windows) |
| `design/` | The complete build contract: requirements, engineering spec, 23 window designs, templates, golden fixtures |
| `tools/` | Developer tooling — the `ids.py` generator, the wxPython toolkit probe |
| `installers/` | PyInstaller spec, `dmgbuild` settings, the NSIS installer script (`windows.nsi`), and `branding/` (SVG sources + committed icon/DMG artwork) |

`design/` is worth reading before changing anything. It is written to be sufficient on its own:
`docs-md/requirements.md` is the acceptance authority, `docs-md/spec.md` the engineering spec, and
`docs-md/xrc-windows.md` the implementation truth for the UI, including the frozen control names that the
test harness finds widgets by.

## Design principles

- **XRC-first.** Every window, dialog and menubar is authored in sizer-based XRC and loaded from it — no
  absolute positioning, no restyled controls, no custom-drawn chrome. Native platforms should look native.
- **Keyboard-first.** The scorer should never have to look down. Every command has a keyboard path, and
  the plate entry field never blocks.
- **The clock is the wall clock.** There is no in-memory timer to lose.
- **Deterministic dealing.** The shoe is Fisher-Yates shuffled from a stored seed, so replaying the seed
  reproduces every card — the deal is auditable, not merely random.
- **Corrections over deletions.** Undo is a compensating write; nothing with recorded data is ever
  deleted, and every mutation lands in an append-only audit log with a reason.

## Licence

[GNU General Public License v3.0 only](LICENSE).
