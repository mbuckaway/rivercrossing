# Contributing to RiverCrossing

Distilled from `design/docs-md/project-plan.md` §2–§3 and `design/docs-md/spec.md` §12/§14. Those
documents are the contract; this file is the working summary. Agent-specific guidance lives in
[AGENTS.md](AGENTS.md).

## Getting set up

Python 3.14 is required. [uv](https://docs.astral.sh/uv/) is recommended but optional — nothing in
`pyproject.toml` is uv-specific.

```bash
uv venv .venv && uv pip install -e '.[dev]'
# or, without uv:
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

Virtual environments are always named `.venv` and are never committed.

## Running the gauntlet

`noxfile.py` is the single task runner; CI invokes exactly these sessions, so a green local run means a
green CI run. `scripts/*.sh` are thin convenience wrappers around them.

```bash
nox -s lint typecheck importlint ids_drift   # stage 1 · static
nox -s unit                                   # stage 2 · unit + coverage gate
nox -s functional                             # stage 3 · real wx windows
nox -s bundle smoke                           # stage 5 · build, then smoke the binary
nox -s dmg dmg_smoke                          # stage 5 · unsigned DMG (macOS only)
nox -s winsetup winsetup_smoke                # stage 5 · unsigned Windows installer (see below)
nox                                           # stages 1 + 2
```

Stage 3 needs a **real desktop session** — no virtual display. It cannot run headless. On macOS,
run it inside the Tart VM (next section): a bare `nox -s functional` on a Mac refuses unless
`RIVERCROSSING_HOST_FUNCTIONAL=1` is set. CI is exempt — the hosted runners set `CI`.

## macOS functional tests — run them in a VM

Stage 3 opens all 23 windows for real. On your own Mac that takes over the desktop: the windows
steal focus while you type, and a crashed run can leave the session in a bad state (measured on a
sibling project — a crashed GUI suite left a synthesized key auto-repeating into every app). A
local [Tart](https://tart.run/) VM isolates the run: the guest has its own WindowServer, so
nothing touches your session. Isolation contains a crash; it does not cure one.

One-time setup — the base image download is about 25 GB:

```bash
brew install openai/tools/tart      # Tart moved from Cirrus Labs to OpenAI in 2026
scripts/setup_functional_vm.sh      # clone the base image, size it, provision, install deps
```

The setup script boots the template headless and asks for the guest password (`admin`) in the
terminal during `ssh-copy-id`. Every later run is non-interactive, and no VM window ever opens.

Each run:

```bash
scripts/run_functional_tests_vm.sh
```

The run script clones the template (APFS copy-on-write — seconds), boots it headless, pushes the
worktree with rsync over ssh, runs the same pytest command as CI stage 3, pulls
`tests/functional/_screenshots/` back, and deletes the clone. A crashed run dies with its clone.

| Exit code | Meaning |
|---|---|
| 2 | `tart` is not installed |
| 3 | the template VM is missing — run `scripts/setup_functional_vm.sh` |
| 124 | watchdog timeout (`RIVERCROSSING_VM_TIMEOUT`, default 1800 s) |
| other | the guest pytest exit code, passed through |

- The guest needs no Accessibility or Screen Recording grants: the harness injects wx events
  directly and screenshots through a `MemoryDC` — no OS-level input, no `screencapture`. (The
  `macos-*-base` images would tolerate TCC-gated tooling anyway: they ship SIP-disabled and
  pre-seed Accessibility/PostEvent/ScreenCapture grants for ssh-launched automation, and they
  auto-login `admin` into a real Aqua session. One Sequoia+ limit: Screen Recording never works
  for an ad-hoc-signed venv python — shell out to `/usr/sbin/screencapture` if ever needed.)
- WARNING: never pass `--vnc` or `--vnc-experimental` to `tart run` without `--no-graphics` —
  those flags open Screen Sharing.app on the host desktop, defeating the isolation.
- To force a host-desktop run (CI-parity debugging): `RIVERCROSSING_HOST_FUNCTIONAL=1 nox -s
  functional`.
- VM sizing: `RIVERCROSSING_VM_CPU` (default 4) and `RIVERCROSSING_VM_MEMORY` (default 8192 MB),
  read by the setup script.
- License: Tart ships under FSL-1.1-ALv2 — free for this local-dev use. Review the license before
  any org-wide or CI adoption.

Measured on 2026-08-07 (Apple Silicon host, Tart 2.35.0, `macos-tahoe-base` guest):

- Image download: 27.1 GB compressed; the template VM allocates 50 GB and uses ~31 GB.
- Full run cycle (clone → boot → rsync → suite → pull → delete): **43 s** wall clock.
- Suite inside the guest: **678 passed, 45 skipped in 20.4 s** (`-n auto`, 4 vCPUs) — the same
  pass count as CI stage 3; the skips are the normal platform skips.
- No Accessibility or Screen Recording prompts appeared — the no-TCC analysis holds.
- A dropped network connection mid-pull is safe: pulled layers are cached, so re-running
  `tart clone` (or the setup script) resumes in seconds.
- The guest filesystem is synced before `tart stop` — a fast stop without it was measured to
  lose a just-written `authorized_keys`.

## The gates (CI blocks merge)

| Gate | Enforced by |
|---|---|
| `ruff check` + `ruff format --check` clean | stage 1 |
| `mypy --strict` clean | stage 1 |
| **≥ 90% line AND branch coverage** on core modules (R-71) | `--cov-branch --cov-fail-under=90` |
| `wx` imported only under `rivercrossing.ui` (R-71) | import-linter contract |
| `ui/ids.py` matches the `.xrc` files (R-05) | `nox -s ids_drift` |
| Every window, control and menu route drivable by the harness (R-73) | stage 3 |

**Both platforms gate.** Stages 1–3 run as a `macos-latest` + `windows-latest` matrix and both
stage-5 build jobs block, exactly as R-75 and spec §14 always intended. The EPIC 1 deviation that made
macOS the only gate (no Windows test machine) was reversed in Phase 10 after every known Windows
failure was root-caused and fixed; the Windows legs upload the same probe screenshots, coverage and
failure artifacts as macOS.

## How we work

- **Test-first, always.** Strict Red-Green-Refactor. Write the failing tests named in the task brief,
  watch them fail, then write the minimum implementation that passes. A change whose first commit is not
  tests is rejected (R-70).
- **One task = one unit of work.** Task ids come from `design/docs-md/task-briefs.md`; the tests each
  brief names *are* the specification. Do not invent extra scope.
- **Committed test fixtures live beside their suite.** CSV import fixtures are in
  `tests/unit/fixtures/csv/` (the clean-180 EPIC-shaped file plus the named negatives), the
  id-generator fixtures in `tests/unit/fixtures/ids/`, and the golden card vectors ship inside the
  package (`src/rivercrossing/vectors/`) so the app self-tests against the same data.
- **The design docs are the contract.** If something is not in them, ask. If two of them disagree, stop
  and ask — do not pick a reading and proceed.
- **XRC names are frozen** (`spec.md` §15b). Tests find widgets by them, so a rename is a breaking
  change. `ui/ids.py` is generated, never hand-edited.
- **Native look.** Load all UI from sizer-based XRC. Never restyle standard controls, position
  absolutely, or add custom-drawn chrome.

## Commits and pull requests

Conventional Commits, scope = module. Test-first is visible in the history as a pair:

```
test(hands): E2.1.1 red
feat(hands): E2.1.1 green
```

- Branch from `master` as `topic/<something-descriptive>`. **Never commit to `master`.**
- PR title `E2.1.1 — eval5 rank table`; body cites the R-ids and window ids it satisfies.
- **No AI attribution.** Never add "Generated with Claude", AI co-author trailers, or similar text to
  commits, code, comments, PR bodies or docs.

## Coding standards

Read the relevant standard before writing code — they are binding, not advisory:

| Editing | Read |
|---|---|
| `.py` | [CODINGSTANDARDS-PYTHON.md](CODINGSTANDARDS-PYTHON.md) |
| `.sh` | [CODINGSTANDARDS-SHELL.md](CODINGSTANDARDS-SHELL.md) |
| any code | [CODINGSTANDARDS-SIMPLECODE.md](CODINGSTANDARDS-SIMPLECODE.md) |
| user-facing UI | [CODINGSTANDARDS-UX.md](CODINGSTANDARDS-UX.md) + [CODINGSTANDARDS-UX-DESKTOP.md](CODINGSTANDARDS-UX-DESKTOP.md) |

Every `.py` file starts with exactly one licence line and nothing else:

```python
# SPDX-License-Identifier: GPL-3.0-only
```

## Testing wxPython — measured gotchas

These cost real debugging time; they are not theoretical.

- A wxWidgets **C++ assertion aborts the interpreter.** Constructing a control with invalid parameters
  (for example `wx.adv.HyperlinkCtrl` with neither a label nor a url) kills the process and discards
  every later result in that run. Pass valid parameters, and isolate risky construction.
- **`Destroy()` is deferred.** A destroyed-but-not-yet-reaped window still answers `FindWindowByName` in
  the same process and will silently contaminate the next assertion. Destroy explicitly and yield.
- **Two XRC classes cannot be authored in XRC** and must be built code-side:
  - `wxInfoBar` — XRC yields a generic `Control`, not a `wx.InfoBar`, and drops the `name`.
  - `wxDataViewListCtrl` — its handler hard-forces the name to `dataviewCtrl`. Use `wxDataViewCtrl`
    instead, whose `name` is honoured, with a `DataViewIndexListModel` subclass. Per-row attributes
    (bold short-lap rows) require overriding `GetAttrByRow`; there is no setter.
- Use event-driven waits, never bare `sleep`. Stage 3 allows one auto-retry and uploads a screenshot on
  failure.

## Regenerating the branding artifacts

The app icon and DMG background are authored as SVGs (`installers/branding/svg/`) and shipped as
**committed** generated artifacts — `RiverCrossing.icns`, `rivercrossing.ico` and the dual-resolution
`dmg_background.tiff`. Nothing rasterised is generated in CI; regeneration is a local macOS task:

```bash
brew install librsvg        # provides rsvg-convert; iconutil/tiffutil ship with macOS
nox -s gen_branding         # renders, assembles, and overwrites the committed artifacts
```

PNG intermediates land only under `build/branding/` (gitignored) — **no `.png` file is ever
committed** (repository policy), and `tests/unit/test_branding_assets.py` enforces that along with
the artifacts' structure (all ten `.icns` representations, the `.ico` sizes, the 1×/2× TIFF pages).
There is deliberately no byte-drift gate: `rsvg-convert` output varies across librsvg versions, so
the honesty tests pin structure, not bytes. Edit the SVGs, regenerate, commit both together.

## The Windows installer — local loop

`installers/windows.nsi` is the unsigned per-user Windows installer (the E9.1.2 pull-forward, Phase 9).
NSIS compiles it, and `makensis` cross-compiles natively on macOS — the reason NSIS replaced Inno Setup
in the design contract (R-01, Phase 9 amendment):

```bash
brew install makensis       # native arm64 NSIS -- no Wine
nox -s winsetup             # off-win32: compile smoke against a synthetic payload under build/
nox -s winsetup_smoke       # compile tests everywhere; install/launch/uninstall tests are win32-only
```

The real artifact only ever comes from CI: PyInstaller cannot cross-compile, so the Windows payload
exists only on a Windows runner. The blocking `build-windows` job builds the dev bundle, compiles
`dist/RiverCrossing-<version>-setup.exe` (NSIS arrives via `choco install nsis` — the windows-2025
image does not preinstall it), and drives E9.1.2's silent install → launch → uninstall tests. The
setup `.exe`, the Windows dev bundle and the `build/winsetup-logs/` diagnostics upload as artifacts on
every outcome.

Measured quirk, encoded in `noxfile.py` and the smoke tests: `makensis` crashes with `std::bad_alloc`
when `LANG`/`LC_ALL` are unset (NSIS bug 1165), so every invocation forces a UTF-8 locale.

## Releasing

A version tag is the whole release procedure — the pipeline does the rest (spec §14 stage 6,
unsigned until EPIC 9):

1. Bump `__version__` in `src/rivercrossing/__init__.py` and add the version's CHANGELOG section.
2. Merge to `master` through a PR (the tag must sit on gated code).
3. `git tag v<version> && git push origin v<version>` — the exact `v<version>` spelling matters:
   the release job fails on a tag that does not match `rivercrossing.__version__`.
4. The tag runs the full two-OS gauntlet; when green, the `release` job publishes the GitHub
   release with `RiverCrossing-<version>-macos.dmg`, `RiverCrossing-<version>-windows-setup.exe`
   and `SHA256SUMS.txt`. Nothing publishes if any gate is red.

## CI secrets contract

Named here so the release lane in EPIC 9 has a defined interface. The organisation supplies the values;
nothing in EPIC 1 consumes them.

| Secret | Used by | Purpose |
|---|---|---|
| `APPLE_ID` | EPIC 9 (E9.1.3) | Apple Developer account for `notarytool` |
| `APPLE_ID_PASSWORD` | EPIC 9 (E9.1.3) | App-specific password for notarisation |
| `APPLE_TEAM_ID` | EPIC 9 (E9.1.3) | Developer ID team identifier |
| `CERT_P12` | EPIC 9 (E9.1.3) | Base64 Developer ID Application certificate |
| `CERT_P12_PASSWORD` | EPIC 9 (E9.1.3) | Passphrase for the above |
| — | Windows | **Unused in v1.** The Windows installer ships unsigned by decision (R-01); the user guide documents the SmartScreen "More info → Run anyway" step. |

Until the Apple credentials land, the macOS packaging stage emits an unsigned `.dmg` and the notarisation
gate is advisory.
