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

**Pre-release, in active development.** EPIC 1 of 9 — the runnable UI shell — is being built now: all 23
windows, the full menu system and every dialog, running on macOS and Windows with demo data behind a
removable seam. No scoring engine and no database yet.

See [design/docs-md/project-plan.md](design/docs-md/project-plan.md) for the full nine-EPIC plan.

## Requirements

- **Python 3.14+**
- **wxPython 4.3.1** (wxWidgets 3.3) — installed automatically; supplies the dark-mode support in R-03
- macOS 13+ or Windows 10/11

macOS is the primary development and CI target today. Windows is supported by design and builds in CI,
but is not currently gated — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Install and run

```bash
git clone https://github.com/mbuckaway/rivercrossing.git
cd rivercrossing

uv venv .venv && uv pip install -e '.[dev]'
# or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'

rivercrossing            # or: python -m rivercrossing
```

## Development

```bash
nox -s lint typecheck importlint ids_drift   # static analysis
nox -s unit                                   # unit tests + coverage gate
nox -s functional                             # drives real wx windows (needs a desktop session)
nox -s bundle smoke                           # build the app bundle, then smoke it
nox -s dmg dmg_smoke                          # macOS: build the unsigned drag-to-Applications DMG, then smoke it
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
| `installers/` | PyInstaller spec, `dmgbuild` settings, and `branding/` (SVG sources + committed icon/DMG artwork) |

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
