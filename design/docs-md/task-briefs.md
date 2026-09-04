# RiverCrossing — Task Briefs

*RiverCrossing · Part 2 · agent-ready task briefs · all nine EPICs · July 24 2026*

Companion to the [project plan](project-plan.md). One brief = one agent session (a brief listing two task ids is still one session, two PRs). Every brief: **Goal · Refs · Tests first · Implement · Done when**. Refs cite real files — read them before writing a line. Ground sources: [Requirements](requirements.md) · [Spec](spec.md) · [XRC canvas](xrc-windows.md) · [Skeletons](module-skeletons.md) · [templates](../templates/base.html.j2) · [golden samples](../exports/epic-2026-results.html). Mock-first check: the 23 canvas screens have frozen mockups, but three §15 routes have no window at all — Duplicate Ride… and Reopen Ride (E5), the Void Card… confirm (E7). Each needs a mock-first step per plan §2 inside its owning brief; E1 routes them to a flagged sentinel and invents nothing.

### Ground rules (from plan §2–§3 — binding in every session)

`tdd-python-writer` writes the named failing tests first; first commit `test(scope): E#.#.# red`, then `feat(scope): E#.#.# green`; one task per PR, title `E#.#.# — name`, body cites R-ids + mock anchors. Gates: ruff + mypy strict clean; ≥90% line + branch on core modules; XRC names frozen (§15b) — ids.py regenerated, never hand-edited; wx imports only under `rivercrossing.ui`; `rivercrossing.demo` importable only from ui bootstrap + tests. Tests ARE the spec: each brief's cases include at least one negative case; property-based where named. Where a brief says "harness", the harness drives controls by **direct event injection** — `SetValue()` for text, a posted `wx.CommandEvent` for buttons — not `wx.UIActionSimulator`, which reports success and delivers nothing from a process that never becomes the OS-active app (Spec §12, plan §4).

### Sequencing & parallelization

```
E1 (serial: 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6)          = D1, v0.1
   ├─ Lane A (engine, headless):  E2.1 → E2.2 → E2.3 → E2.4
   └─ Lane B (roster):            E3.1 → E3.2 ∥ E3.3 → E3.4
E4 (needs E2 + E3) → E5 (needs E4)
   ├─ Lane C: E6.1 → E6.2 ∥ E6.3 → E6.4     (E6.2.1 CSS step can start any time after E1)
   ├─ Lane D: E7.1 → E7.2 ∥ E7.3
   └─ Lane E: E8.1 ∥ E8.2
E9 last (needs all; 9.1.3 additionally needs org credentials)
```

### Stub & hand-off ownership

| Interface / stub | Created by | Enabled / consumed by |
|---|---|---|
| ids.py generator + name registry test | E1.2.1 | every UI task thereafter |
| DataSource protocol; DemoDataSource wiring line | E1.2.4 | real sources E4/E5; wiring removed E5.4.2 |
| Payload dataclasses (ExportOptions, records) | E1.2.2 | E6.2 renderers (fields frozen from E1) |
| Evaluator finish-gate hook (self-test green?) | E2.4.1 | E6.4.3 finish flow |
| Held-card release + manual-deal engine path | E4.3.2 (stub) | E7.2.1 wires dialogs to it |
| stale_infobar in results_frame | E6.4.1 (hidden) | E7.3.2 triggers on corrections |
| Accelerator table (single source) | E1.4.1 | E8.2.1 shortcuts_dlg rows |
| PyInstaller dev-bundle specs | E1.6.1 | E9.1.1 hardens into release bundles + installers |
| CI secrets contract (signing) | E1.1.2 (names them) | org supplies · E9.1.3 consumes |
| Roster surface (roster.py: Roster, lock matrix, validate_for_start) · RideConfig (ride.py) · in-memory audit events | E3 | E4 engine (plate→entry resolution, has_data, start gate) · E5 store persists the events |

### E1 · Runnable UI shell (D1) — entry gate: none (first work)

- **E1.1.1 Repo bootstrap** · Goal: installable skeleton matching the layout doc so every later session lands in known paths. Refs: Skeletons (whole tree + pyproject block), plan §3. Tests first: `tests/unit/test_packaging.py` — editable install imports `rivercrossing`; version string PEP 440; negative: importing `rivercrossing.ui` without wx installed raises cleanly. Implement: src tree, pyproject (wxPython~=4.3.1, fpdf2, jinja2; dev extras — no `wxasync`, per Spec §10), CONTRIBUTING.md from plan §2/§3. Done when: pip install -e . + pytest green on macOS.

- **E1.1.2 CI stages 1–3 skeleton** · Goal: the gauntlet exists before any feature code. Refs: Spec §14 table, plan §4. Tests first: a quarantined probe test proves a red stage blocks merge (then removed in the same PR — its removal is the assertion). Implement: ci.yml matrix windows-latest+macos-latest, stages ruff/mypy → pytest+coverage → functional placeholder + stage-5 dev-bundle job (PyInstaller onedir, unsigned, artifact upload — allowed-empty until E1.6.1); screenshot-artifact upload step; secrets contract documented (APPLE_ID, CERT_P12, WIN unused-v1). macOS was the blocking gate with the windows-latest leg advisory while no Windows test machine existed — the temporary deviation recorded in Spec §14 and R-75, reversed in EPIC 1 Phase 10: both platforms block. Done when: PR checks show the named stages green on both platforms.

- **E1.1.3 Layering contract** · Goal: wx can never leak into core. Refs: Skeletons dependency diagram. Tests first: import-linter contract red (a deliberate wx import in rivercrossing.ride), then removed. Implement: importlinter config in pyproject + CI hook. Done when: contract green; the deliberate-violation commit is in history proving the gate fires.

- **E1.2.1 ids.py generator** · Goal: XRC names are the single registry, generated never typed. Refs: Spec §15b naming rules; R-05. Tests first: `tests/unit/test_ids_gen.py` — fixture .xrc → constants (PLATE_INPUT="plate_input"); duplicate name in one window fails, while the same name in two windows collapses to one constant and is legal (§15b requires uniqueness only within a window — `plate_input`, `reason_input`, `continue_btn` and `message_lbl` all recur); drift (constant missing vs xrc) fails; suffix-convention warnings. Implement: `tools/gen_ids.py` + CI step. Done when: regen is idempotent and drift fails the build.

- **E1.2.2 Payload dataclasses** · Goal: freeze the data contract the UI and exporters share. Refs: base.html.j2 header (context contract), Skeletons ExportOptions line, R-63. Tests first: `tests/unit/test_payload.py` — defaults (show_times=False, laps_board=True, time_board=False, full_field=True, all_cards=True, lap_km=8.0); frozen=True raises on mutation; to_record() camelCase keys match the golden sample JSON keys exactly; negative: unknown key rejected. Implement: dataclasses + to_record(). Done when: record keys == keys parsed from epic-2026-results.html's race-data block.

- **E1.2.3 Presenter protocols** · Goal: every window gets a view-interface so logic is testable without wx. Refs: Skeletons ui.presenters. Tests first: `tests/unit/presenters/test_protocols.py` — FakeView satisfies each Protocol; presenters accept DataSource; negative: missing method fails typecheck (mypy snapshot test). Implement: protocols + no-op presenters. Done when: mypy strict green with FakeViews.

- **E1.2.4 Demo seam** · Goal: hard-coded display data lives in ONE removable package. Refs: XRC canvas fixture values (#mainframe rows, #librarydlg, #entrydetail). Tests first: `tests/unit/test_demo.py` — DemoDataSource satisfies DataSource; fixture invariants (plate 45 flagged, team 77 has 3 riders, shoe 41/108); lint test: demo imported only from app bootstrap + tests (red first via a planted bad import). Implement: rivercrossing/demo.py. Done when: lint rule green; app not yet wired (that's E1.5).

- **E1.3.1 Author the 9 .xrc files** (sessions may split main+setup / riders+detail+results / rest) · Goal: all 23 windows exist as sizer-based XRC with frozen names. Refs: XRC canvas — every window block + its code-side footnotes; §15b file map and its measured list of what XRC cannot express; canvas radio defaults (solo, pooled, jokers-2, appearance-System — `<value>` on a wxRadioButton is honoured, unlike `<checked>` on a radio *menu* item). Tests first: `tests/functional/test_xrc_load.py` — parametrized: each file loads via wx.xrc, every §15b control name resolves, radio defaults as drawn, wxStdDialogButtonSizer present in dialogs; the lists are wxDataViewCtrl, never wxDataViewListCtrl (whose handler forces the name `dataviewCtrl`); the four info bars are absent from the resources by design and `main_menubar` comes from `XmlResource.LoadMenuBar()`, not `FindWindowByName`; window minimums declared as `<size>` and re-applied with `SetMinSize()`; negative: unknown name lookup returns None (asserted for one retired name). Implement: hand-authored .xrc. Done when: 23/23 load with names + defaults on Windows and macOS.

- **E1.3.2 Card bitmaps** · Goal: 53-card imagelist (52 + joker) @1x/2x. Refs: canvas chip styling (mono steel, no red — hearts/diamonds accent-toned), Spec §15b code-side list. Tests first: loader returns 53 keys ×2 scales; joker distinct; sizes match DataView row height; negative: missing asset raises at startup not at first paint. Implement: scripted PIL/wx drawing generator committed with its output — a starter set is already generated and bundled (assets/cards/, 24×32 + 48×64 "-2x" suffix, mono steel palette); regenerate from the committed script, keep the naming. Done when: assets render in the smoke screenshots.

- **E1.3.3 Per-screen smoke** · Goal: the forever-test — every screen loads, shows, closes. Refs: plan §4. Tests first: parametrized over all 23 (Show → names resolve, info bars after their code-side construction and the menubar through LoadMenuBar → screenshot artifact → Close returns). Implement: window loader helpers until green. Done when: green on macOS locally + both OSes in CI.

- **E1.4.1 Menubar + routes** · Goal: every §15 row reaches its target. Refs: Spec §15 table (38 rows: File 8 · Ride 7 · Riders 4 · Cards 7 · Results 7 · View 1 · Help 4) + §15b mi_* names; accelerators Enter/Ctrl+Z/F1/F5. Tests first: `tests/functional/test_menu_coverage.py` — walk every row, assert window/dialog/command target; items resolve through the loaded menubar, not FindWindowByName (wxMenuBar drops its name — §15b); radio-item defaults mi_theme_system + mi_zoom_100 are checked in code, since `<checked>` is a no-op on radio items; macOS relocation (About/Settings/Quit in app menu); the three windowless routes (Duplicate Ride…, Reopen Ride, Void Card…) hit a flagged sentinel the walk asserts as a sentinel, not a window; negative: an unrouted mi_ id fails the walk. Implement: menubar XRC + command table (the accelerator table other features consume). Done when: coverage walk green both OSes.

- **E1.4.2 State enablement** · Goal: menus + buttons obey ride state. Refs: §15 "Enabled when" column; canvas footnotes (DRAFT/FINISHED/REOPENED deltas). Tests first: parametrized (item × state DRAFT/RUNNING/FINISHED/REOPENED) from a table literally transcribed from §15; negative: stop_btn without armed checkbox stays disabled (R-35). Implement: mocked state provider + enablement binder. Done when: table test green.

- **E1.5.1 Console demo display** · Goal: main_frame shows the canvas's exact demo picture. Refs: #mainframe (feed rows, counters, flagged row bold, InfoBars, splitter, statusbar). Tests first: harness asserts row count 5, flagged row attrs, counter labels, sash persistence across relaunch, InfoBar hidden by default; hide-times mock removes Lap time/Total columns. Implement: DataView columns + demo bindings — row attributes come from a `DataViewIndexListModel` subclass overriding `GetAttrByRow` (there is no setter), the Card column declares `DataViewBitmapRenderer("wxBitmap")` explicitly because `AppendBitmapColumn`'s default registers against `wxBitmapBundle` and silently drops a plain `wx.Bitmap`, and the info bars are constructed with `wx.InfoBar()` and named with `SetName()`. Done when: screenshot matches canvas structurally on both OSes (bold flag included — riskiest widget verified).

- **E1.5.2 Lists demo display** · Goal: library/editor/detail/results show demo data. Refs: #librarydlg #ridereditor #entrydetail #resultsframe rows. Tests first: per-window row/column assertions incl. results publish-checkbox defaults (times ✗, laps ✓, time-board ✗, full ✓, all-cards ✓) and Team column hidden in solo-only mock. Implement: bindings. Done when: assertions green.

- **E1.5.3 Dialog behavior** · Goal: R-76 mechanics everywhere. Refs: R-76; canvas stock-ID footers. Tests first: per-dialog — Esc cancels, Enter fires default, destructive confirms focus Cancel, focus returns to opener; `message_lbl` in delete_ride_dlg / continue_or_new_dlg / resume_dlg carries its interpolated line and is asserted non-empty (a blank line is what earned that name); negative: Delete in delete_ride_dlg disabled until exact name typed. Implement: wiring only. Done when: parametrized suite green.

- **E1.6.1 CI dev bundles (Windows + macOS)** · Goal: CI builds the runnable mockup for both OSes so anyone can download and click through D1. Refs: Spec §14 (stage 5 dev-bundle mode), Skeletons assets/ + ui/xrc/. Tests first: packaged-app smoke — launch the bundle, main_frame opens, the 23-window smoke suite passes against the bundle; asset-manifest completeness (xrc, cards, WAVs) vs source tree; negative: a missing asset fails the build, not first paint. Implement: PyInstaller onedir specs (unsigned) + CI artifact upload on main. Done when: both artifacts download from CI and pass smoke on clean Windows 11 + macOS images.

- **E1.6.2 D1 walkthrough + tag** · Goal: prove D1 end-to-end on the CI-built bundles. Refs: plan E1 exit criteria. Tests first: scripted tour = menu walk + drive every named control + screenshots, executed against the E1.6.1 bundle on both OSes. Implement: fixes only. Done when: v0.1 tagged with both bundles attached, exit criteria checked off in PR.

### E2 · Deal & score engine — entry gate: E1 exit (contracts + CI)

- **E2.1.1 eval5 rank table** · Goal: exact 5-card evaluator. Refs: Spec §5; tests/vectors sweep data. Tests first: `tests/unit/test_hands.py` — all 7,462 distinct ranks hit exactly once across the class sweep; wheel A-2-3-4-5 ranks below 6-high straight; flush > straight; negative: 4-card input raises. Implement: table-driven eval5. Done when: sweep green < 5 s.

- **E2.1.2 Joker layer + five-of-a-kind** · Goal: wilds resolve to the best legal hand; 5-of-a-kind tops straight flush. Refs: Spec §5 joker vector table (28 rows). Tests first: the 28 vectors verbatim; two jokers → AAAAA♦-free five-aces; negative: joker never resolves to a card already held (duplicate-legal decks aside — assert resolution maximizes rank, not uniqueness). Implement: wild expansion over eval5. Done when: vector table green.

- **E2.1.3 Best-5-of-N + card cap** · Goal: best hand from any N with optional cap X (first X dealt score; later laps still count laps/time — R-13, Spec §5). Tests first: N=4 (<5 cards ranks as partial per §5), N=12 with j=2 subset math, cap fixtures where card 11 would improve but X=10 blocks; property: adding a card never lowers the hand. Implement: C(n,k) subsets + memo. Done when: fixtures + property green, 180×12 field < 1 s.

- **E2.1.4 Hypothesis properties** · Goal: fuzz the evaluator. Tests first (they are the task): rank total-order transitivity; permutation invariance; joker-count monotonicity; serialization round-trip. Done when: 10k examples green in CI budget.

- **E2.2.1 Shoe** · Goal: seeded, auditable dealing. Refs: Spec §4 (Fisher-Yates, deal_index, reshuffle cycle), canvas statusbar "Shoe cycle 1 · seed 8843". Tests first: `tests/unit/test_cards.py` — same seed same sequence; exhaustion (2×54=108 dealt) reshuffles into cycle 2 with derived seed; undo restitution returns card to front; negative: deal after close raises. Implement: Shoe. Done when: determinism + exhaustion green.

- **E2.2.2 Shoe config** · Goal: decks × jokers (0/2/4) per ride setup. Refs: #setupdlg Cards fieldset; R-13. Tests first: composition counts per config; jokers=0 never deals a joker (property). Done when: config matrix green.

- **E2.3.1 Simulation suite** · Goal: whole-ride confidence. Refs: plan E2.3; R-16 uncapped pooled. Tests first: seeded sims (180 entries × 6 h; solo, mixed-pooled, mixed-relay) assert shoe accounting exact, every hand evaluable, one rider out-lapping teammates scores to the team pool, runtime budget. Done when: sims in CI stage 2 under 60 s.

- **E2.4.1 Self-test dialog live** · Goal: user-visible proof + the finish-gate hook. Refs: #selftestdlg; Spec §12. Tests first: harness — dialog runs real suite, output lines end PASS, rerun works; hook `evaluator_selftest() -> Report` exposed; negative: injected broken table → dialog shows FAIL and hook reports red. Done when: green dialog on both OSes from the real evaluator.

### E3 · Roster — entry gate: E1 exit · parallel with E2

- **E3.1.1 Models + invariants** · Goal: entries/riders/teams with hard rules. Refs: R-11/12/16/20; Spec §1–§2. Tests first: `tests/unit/test_roster.py` — plate unique per ride; team size 2–max(≤10); solo default; pooled vs relay plate shapes; negative: 11-rider team, duplicate plate, team in solo-only ride all raise. Implement: models. Done when: property + negative suite green.

- **E3.1.2 Lock matrix** · Goal: editability by state and plate model. Refs: R-15/17. Tests first: DRAFT free edit incl. delete; post-start relay locked; post-start pooled allows audited rider moves; entry with data undeletable (DNF/void only). Done when: matrix test green.

- **E3.2.1 + E3.2.2 Editor live** · Goal: rider_editor on real models. Refs: #ridereditor; R-20. Tests first: harness — add (next-free plate prefilled), save, delete-blocked-with-data path, team_choice incl. "New team…", solo-only hides Team column; negative: duplicate plate shows validation, not crash. Implement: presenter + bindings replacing demo rows. Done when: all editor flows green via harness.

- **E3.3.1 CSV preview** · Goal: preview-then-commit, nothing written on preview. Refs: Spec §7 column spec; R-21; #csvdlg. Tests first: `tests/unit/test_csvio.py` — fixture files: clean-180 (EPIC-shaped), dup-plate, missing-name, team-over-max, relay/pooled forms (rider_1…rider_N); preview counts + conflicts exact; filesystem untouched (tmpdir assert). Done when: fixtures green.

- **E3.3.2 Commit + re-import** · Goal: import applies atomically; re-import reshapes teams pre-start (R-17/21). Tests first: commit then re-import moved rider → team membership updated, audit rows written; negative: commit with conflicts refuses. Done when: reshape scenario green.

- **E3.3.3 Export round-trip** · Goal: export → import is identity. Tests first: property — random roster → export → preview shows 0 conflicts → commit → equal models. Done when: round-trip green incl. teams.

- **E3.4.1 + E3.4.2 Preview dialog + solo variant** · Goal: csv_preview_dlg wired (Import disabled while conflicts>0 — stock wxID_OK gating) and editor solo/mixed presentation switch. Refs: #csvdlg footnote, R-11. Tests first: harness both states. Done when: green both OSes.

- **E3.5.1 + E3.5.2 Ride setup live** *(added scope, approved 2026-08-08)* · Goal: ride_setup_dlg on a validated RideConfig. Refs: #setupdlg; R-11/12/13/16; Spec §2/§4. Tests first: RideConfig bounds in `tests/unit/test_ride.py`; presenter defaults (decks 8), enablement and the R-17 lock in `tests/unit/presenters/test_setup.py`; harness — defaults exact, mixed enables team fields, cap_chk gates cap_spin, OK builds the config, locked-relay variant. Done when: dialog green both OSes; §4's deck default recorded as 8.

### E4 · Live ride, in-memory — entry gate: E2 + E3 exits

- **E4.1.1 State machine** · Goal: DRAFT→RUNNING→FINISHED→REOPENED with guards. Refs: Spec §2; R-30/36. Tests first: `tests/unit/test_ride.py` transition table incl. illegal moves raise; wall-clock source injected (fake clock); elapsed/remaining derive from clock not timers. Done when: table green.

- **E4.1.2 + E4.1.3 Set-start-time + stop/continue** · Goal: retro start recompute (lap-1) + stop keeps truth, continue resumes seamlessly. Refs: R-30; Spec §2/§3; #setstartdlg #stopdlg #continuedlg. Tests first: retro-fix recomputes only lap-1 totals; audit row; stop → crossings refused with cue; continue → no time lost (fake clock advanced). Done when: scenarios green.

- **E4.2.1 + E4.2.4 Crossings + unknown-plate rejection cue** · Goal: plate+Enter → lap, timestamps, feedback payload < 100 ms. Refs: R-31/32. Tests first: lap increments per entry (pooled team: rider's plate credits team, uncapped — R-16); unknown plate → rejected + error cue event, focus retained; perf budget test. Done when: green incl. perf.

- **E4.2.2 Min-lap flags + held cards** · Goal: fast crossings flag and hold their card (R-34). Tests first: crossing < min_lap flags, card held not dealt, review list exposes it; confirm releases (deals held card), void discards. Done when: hold/release/void green.

- **E4.2.3 Undo** · Goal: one-keystroke undo of last crossing with shoe restitution (R-33). Tests first: undo removes lap, returns card to shoe front, audit row written; negative: undo with zero crossings disabled. Done when: green.

- **E4.3.1 + E4.3.2 Dealing + held/manual path stub** · Goal: one card per counted crossing from the shoe; expose `deal_manual(plate, reason)` + release path as engine API (dialog wiring is E7's). Tests first: card-per-lap accounting vs shoe deal_index; cap X stops scoring not dealing? — no: per R-13 laps past cap still count, cards past cap still dealt but non-scoring (assert both); manual deal audited. Done when: accounting exact in sim replay.

- **E4.4.1 Console live** · Goal: swap console demo → engine (DataSource impl over RideEngine). Refs: #mainframe; R-32. Tests first: harness — typed plate appears in feed ≤ 100 ms with card chip; counters update; flagged row bold + review panel count. Done when: console runs a ride with demo wiring line unused on this screen.

- **E4.4.2 Arm/stop/finish flows** · Goal: R-35 three deliberate acts + finish confirm. Tests first: harness — arm enables stop, auto-clears after use/timeout; stop confirm Cancel default; finish gate hook consulted (stubbed green until E6.4.3). Done when: flows green.

- **E4.4.3 Sound cues** · Goal: recorded/flagged/error WAVs per Spec §10, behind the Settings toggle (R-31). Tests first: cue events emitted per outcome (audio backend faked); toggle mutes; missing WAV falls back silent not crash (negative). A starter cue set is bundled (assets/sounds/: recorded 70 ms tick · flagged 280 ms two-tone down-chirp · error 300 ms low buzz). Done when: cue tests green + manual listen note in PR.

- **E4.4.4 Mini acceptance** · Goal: 20-rider scripted race, min-lap lowered, through the real UI. Tests first: the script IS the test — start, 60 crossings incl. flags + undo, stop/continue, finish; standings hand-verified fixture. Done when: green on both OSes in CI stage 4.

### E5 · Persistence & crash recovery — entry gate: E4 exit

- **E5.1.1 Schema + migrations** · Goal: multi-ride SQLite per Spec §6 (ride metadata R-10 incl. logo blob). Tests first: `tests/unit/test_store.py` — create/migrate/idempotent re-open; v0→v1 migration fixture; negative: future schema version refuses politely. Done when: green.

- **E5.1.2 Event replay** · Goal: store is an event log; replay rebuilds engine state exactly. Tests first: property — random event sequences: live state == replayed state (the E4 sim generator reused). Done when: equivalence property green.

- **E5.1.3 Crash consistency** · Goal: R-50 — lose at most the uncommitted keystroke. Tests first: subprocess race killed at random points (WAL on); reopen → last committed crossing intact, no corruption (integrity_check). Done when: 50-kill loop green in CI.

- **E5.2.1 + E5.2.3 Session bookkeeping + exit flow** · Goal: clean-quit vs crash recorded; exit-with-running-ride dialog (R-51/52). Refs: #exitdlg. Tests first: unclean flag set on open, cleared on clean close; exit dialog wording + quit keeps ride timing (fake clock across process restart); all three buttons exercised — Cancel (the default, so a reflex Enter is safe), `finish_first_btn` routing to the finish flow, and Quit-keep-running (R-51, §13 ordering). Done when: green.

- **E5.2.2 Resume + reopened banners** · Goal: launch with running ride → resume_dlg (crash vs quit wording); reopened_infobar on console. Refs: #resumedlg, #mainframe InfoBar. Tests first: harness relaunch scenarios both wordings — the presenter writes them into `message_lbl` (ride name + quit-vs-crash), and a blank label is a failed assertion, not a cosmetic one; Continue resumes with elapsed correct; Open library path; reopened_infobar is code-constructed and named with SetName(). Done when: both paths green.

- **E5.3.1 Backups** · Goal: open + hourly + manual, keep 20 (R-54). Tests first: rotation at 21; hourly tick (fake clock); Back-up-now writes valid DB (opens + integrity). Done when: green.

- **E5.3.2 Delete guard** · Goal: R-18 exactly. Refs: #deletedlg. Tests first: harness — Delete disabled until exact name; the ride's name appears in `message_lbl` (UX-DESKTOP §4: a destructive confirm names its object); backup written before delete; RUNNING ride: item disabled. Done when: green.

- **E5.4.1 Library live** · Goal: ride_library_dlg on the real DB (open/new/duplicate — setup+roster only, R-15). **Mock-first step first:** §15's Duplicate Ride… dialog and Reopen Ride confirm have no frozen window (they cite the retired 3d pattern), so this session produces both mockups and registers their control names in §15b before any UI code (plan §2), replacing E1.4.1's sentinel. Tests first: duplicate copies no timing data; open switches console context; the two new windows load, resolve their names and honour R-76. Done when: green.

- **E5.4.2 Demo retirement** · Goal: remove the DemoDataSource wiring line; demo becomes tests-only. Tests first: lint rule tightened (no bootstrap exemption) red-then-green; full smoke suite still green (screens now show real/empty states). Done when: app paths demo-free; empty-state screenshots attached.

### E6 · Results & publishing — entry gate: E2 + E5 exits (6.2.1 may start after E1)

- **E6.1.1 Standings + tie-breaks** · Goal: ordering with rules ①②③ reorderable, instant re-rank, changeable after finish (R-14); DNF block last. Tests first: `tests/unit/test_standings.py` — crafted identical-hand ties resolved per order; reorder re-runs; high-card-draw records the draw; DNF keeps laps/cards (R-33 path). Done when: fixtures green.

- **E6.1.2 Leaderboards** · Goal: most-laps and fastest (most laps, then shortest elapsed to last crossing). Tests first: fixture where pure-time order differs from laps-then-time (negative guard against sorting by time alone). Done when: green.

- **E6.2.1 Vendored CSS build step** · Goal: compile once in CI: Tailwind CLI × (base.html.j2 + theme.css) → compiled_css; Barlow/Barlow Condensed woff2 subsets → fonts_css (base64 @font-face). Refs: Spec §8; htmlexport/templates/theme.css. Tests first: build output contains .bp/.chip rules + both font families; checksum recorded; CI fails if theme.css newer than vendored artifact (staleness gate); zero url(http references. Done when: artifact committed + gate live.

- **E6.2.2 HTML render + goldens** · Goal: htmlexport.render() per the template contract. Refs: base.html.j2 + macros.html.j2 headers; R-61; golden samples. Tests first: `tests/unit/test_htmlexport.py` — fixtures parsed from the two samples' race-data blocks render to committed goldens byte-for-byte (regenerated deliberately this once from the real renderer, then frozen); racejson escapes `</` (fixture with `</script>` in a team name — negative/injection case); StrictUndefined raises on missing key; zero external refs. Done when: goldens + injection + offline checks green.

- **E6.2.3 No-times variant** · Goal: R-63 — times absent from markup AND JSON. Tests first: rendered no-times page contains no t-col markup, no total/bestLap/avg keys, empty timeBoard; title suffix "(no times)". Done when: assertions green against the no-times golden.

- **E6.3.1 PDF report / E6.3.2 poster** (two sessions) · Goal: fpdf2 renderer per UI Designs 5a–5c, then the one-page podium poster 5d; deterministic bytes (R-62). Tests first: fixed metadata → identical bytes across two runs and across OSes (CI artifact diff); section flags mirror ExportOptions; poster is a single page at Letter. Done when: byte-determinism green both OSes.

- **E6.4.1 Results window live** · Goal: results_frame standings + publish checkboxes → ExportOptions; stale_infobar constructed in code and named with SetName(), present but hidden (E7 triggers) — XRC cannot author a wxInfoBar (§15b). Refs: #resultsframe. Tests first: harness — checkbox toggles change rendered exports (times case doubles as R-63 UI proof); tie rows badge. Done when: green.

- **E6.4.2 Results menu** · Goal: §15 Results rows live (Standings F5, Generate HTML, Export PDF, Poster, Standings CSV, Preview in Browser, Tie-break Order) with FINISHED gating. Tests first: extend menu-coverage walk with the real actions writing tmp files. Done when: walk green.

- **E6.4.3 Finish gate** · Goal: finish requires evaluator self-test green (Spec §2, E2.4.1 hook). Tests first: hook red → finish confirm blocked with message; hook green → proceeds. Done when: both branches green.

### E7 · Corrections & audit — entry gate: E5 exit (stale-flag needs E6.4.1)

- **E7.1.1 Audited command layer** · Goal: edit/void crossing, add-at-time, reassign, manual deal, void card, DNF — each a command requiring a reason, writing one audit row (R-33). Tests first: per-command audit assertions (when/who/action/entry/reason); negative: empty reason refused. Done when: suite green.

- **E7.1.2 Recompute cascades** · Goal: corrections rebuild laps/times/cards consistently. Tests first: property — history + corrections replayed == direct corrected history (reuses E5.1.2 machinery); void mid-ride crossing renumbers later laps. Done when: property green.

- **E7.2.1 Correction dialogs live** · Goal: wire #editcrossing (both titles), #reassigndlg, #dealdlg, #dnfdlg + entry-detail buttons to E7.1/E4.3.2 paths. **Mock-first step first:** the Void Card… confirm has no frozen window (§15 cites the retired 3d pattern), so mock it and register its names in §15b before wiring, replacing E1.4.1's sentinel. Tests first: harness per dialog incl. edit-vs-add prefill, DNF reversible, void-card from cards row. Done when: every §15 Cards-menu route exercises its command.

- **E7.2.2 REOPENED mode** · Goal: R-36 corrections-only console + "Finish again" re-rank. Tests first: harness — entry disabled, corrections enabled, edited rows highlighted, finish-again produces new standings. Done when: green.

- **E7.3.1 Audit viewer** · Goal: audit_dlg newest-first with plate search + action filter (R-38). Tests first: harness filter scenarios; 1000-row perf budget. Done when: green.

- **E7.3.2 Stale-export flag** · Goal: corrections after an export show stale_infobar until re-export. Tests first: export → correct → banner shown; re-export clears. Done when: green.

### E8 · Settings & assistance — entry gate: E5 exit · parallel with E6/E7

- **E8.1.1 Settings persistence** · Goal: all settings survive relaunch (R-04 zoom incl.). Refs: #settingsdlg. Tests first: set → relaunch → read for every control incl. sash + window geometry. Done when: green.

- **E8.1.2 Appearance** · Goal: R-03 exactly — System/Light/Dark all live on both platforms, applied through `wx.App.SetAppearance`, which the 4.3.1 baseline supplies; System follows the OS. No radio is ever disabled and no "needs wxPython 4.3" hint exists. Tests first: each radio applies its appearance without restart; System tracks the OS setting; the capability fake stays as a guard against a build regressing the API away, but its "hasn't" arm has no real-world case on the pinned floor — assert the guard's fallback, do not design UI for it. Done when: matrix green per-OS.

- **E8.1.3 + E8.1.4 Hide-times + zoom** · Goal: hide-times toggles console columns mid-ride (R-63 companion); zoom 90–150% relayouts (R-04). Tests first: columns present/absent live; zoom changes font scale on console + dialogs; View-menu radio mirrors Settings. Done when: green.

- **E8.2.1 Shortcuts dialog** · Goal: rows generated from the E1.4.1 accelerator table (cannot drift). Refs: #shortcutsdlg. Tests first: every accelerator appears exactly once; adding a fake accelerator in test shows up (generativity proof). Done when: green.

- **E8.2.2 User guide** · Goal: build docs/user-guide.html from the 6a outline (10 chapters + 2 appendices); F1 + per-dialog Help anchors; screenshots regenerated by the harness each release. Refs: UI Designs #6a; §15 Help row. Tests first: guide builds; every Help button's anchor exists; screenshot refresh job produces current images. Done when: F1 opens the right anchor from three sampled dialogs.

- **E8.2.3 About box** · Goal: version from package metadata; ride logo fallback to app icon; gorba link. Refs: #aboutdlg. Tests first: fallback path (no logo set); version matches pyproject. Done when: green.

### E9 · Packaging & release — entry gate: all exits · external: signing creds (contract E1.1.2)

- **E9.1.1 Release bundles** · Goal: harden the E1.6.1 dev-bundle specs into release bundles — full assets/templates/guide/WAVs, version metadata, icons. *Phase 8 (EPIC 1 follow-up) already delivered the icons: SVG sources + committed `.icns`/`.ico` under `installers/branding/`, wired into the spec; what remains here is the release-asset completeness (templates, guide, license texts) and version metadata hardening.* Tests first: packaged-app smoke (launch, open ride, one crossing, export HTML) driven via the harness against the bundle; asset-manifest completeness vs source tree. Done when: smoke green on clean CI images.

- **E9.1.2 Windows installer** · Goal: NSIS per-user .exe, Authenticode-signed via SignPath when the `SIGNPATH_*` config is present, unsigned fallback until then (Spec §10/§14; Phase 9 amendment: NSIS replaces Inno Setup). *Phase 9 (EPIC 1 follow-up) already delivered the unsigned half: `installers/windows.nsi` (per-user, Start-menu entry, HKCU uninstall key, branded icon, no hard-coded version), `nox -s winsetup` / `winsetup_smoke` (native makensis compile smoke on macOS), and the windows-latest packaging job (blocking since Phase 10) that compiles the setup .exe, runs the silent install/launch/uninstall suite and uploads the artifacts. The Authenticode signing wiring (installer + bootloader via SignPath, advisory-gated on the `SIGNPATH_*` config) is in place; what remains is the SignPath onboarding — a version tag publishes signed installers when the config is present and unsigned installers until then.* Tests first: silent install/uninstall on windows-latest leaves/removes files + Start-menu entry. Done when: install-run-uninstall green.

- **E9.1.3 macOS dmg + notarization** · Goal: dmgbuild, Developer ID codesign + notarize. *Owner: org supplies APPLE creds (contract E1.1.2); until then stage emits unsigned dmg and the notarize gate is advisory.* *Phase 8 (EPIC 1 follow-up) already delivered the unsigned half: `installers/dmg_settings.py` (Applications symlink, dual-res background, volume icon), `nox -s dmg` / `dmg_smoke`, and the mount-and-verify smoke on the macOS CI gate. What remains here: Developer ID codesign, notarytool staple, the spctl gate, and launching the app from the mounted image (deliberately excluded unsigned — Gatekeeper/translocation). One flagged verification: the DMG background convention should be re-checked on an older macOS than the macOS-26 build host before release (open Apple-forum report of backgrounds authored on 26 not rendering pre-Tahoe).* Tests first: spctl assessment (skipped-with-reason when creds absent); dmg mounts + app launches. Done when: signed path green once creds land; unsigned path green meanwhile.

- **E9.2.1 Full acceptance race** · Goal: R-74 verbatim — CSV in, hundreds of typed crossings, stop/continue, kill+relaunch, quit+relaunch, finish, all four exports verified vs fixtures. Done when: green both OSes in stage 4 (this is the release gate).

- **E9.2.2 + E9.2.3 Nightly + release** · Goal: nightly seeded race filing the seed on failure (R-77); tag-triggered release drafting with artifacts. Tests first: forced-failure files seed in the issue body (dry-run); tag dry-run attaches installers + checksums. Done when: one real nightly green + v1.0 draft produced.

### Review of the brief set — findings & resolutions

| # | Finding (checked against plan + sources) | Resolution |
|---|---|---|
| TB-1 | ExportOptions defaults contradicted the mockups: Skeletons said laps_board=False, all_cards=False, but the results window mockup + golden samples show laps ✓ and all-cards ✓ (time-board ✗, times ✗). | Skeletons corrected to laps_board=True, all_cards=True; E1.2.2 tests pin the aligned defaults. |
| TB-2 | Barlow woff2 subsets (fonts_css) had no producing task anywhere in the plan. | Folded into E6.2.1 (build step produces both compiled_css and fonts_css; tests assert both families). |
| TB-3 | Held-card release / manual-deal engine path was built in E4 but wired in E7 with no named owner for the seam. | Ownership table row added; E4.3.2 exposes the API, E7.2.1 consumes it. |
| TB-4 | Card-cap semantics risked drift: R-13 says laps past cap still count — briefs must not stop dealing at the cap. | E4.3.1 asserts cards past X are dealt but non-scoring; E2.1.3 asserts scoring uses only the first X. |
| TB-5 | Golden byte-for-byte tests vs the hand-assembled samples would fail on loop whitespace (known caveat from the template hand-off). | E6.2.2 regenerates the goldens once from the real renderer against the samples' fixtures, verifies value-parity, then freezes bytes. |
| TB-6 | Injection case missing: a team name containing "</script>" would break the embedded JSON if racejson mis-escaped. | Added as a named negative test in E6.2.2. |
| TB-7 | E6.2.1 could silently ship stale vendored CSS after a theme.css edit. | Staleness gate added: CI fails if theme.css is newer than the committed artifact checksum. |
| TB-8 | Mock-first rule check: all 23 canvas windows have frozen mockups, incl. shortcuts_dlg added in the last audit — but §15 routes three menu items at a dialog that exists only in the retired hi-fi designs (Duplicate Ride…, Reopen Ride, Void Card…), so the canvas does not in fact cover the menu map. | Mock-first steps required after all, folded into the owning briefs: E5.4.1 (Duplicate Ride… + the Reopen Ride confirm) and E7.2.1 (Void Card… confirm). E1.4.1 routes all three to a flagged sentinel meanwhile. |
| TB-9 | EPIC 6 shipped with two additions and one process override (approved 2026-08-28): (a) spec §7's "finished ride adds laps, cards, best_hand, total_time" columns had no owning task; (b) the §15 "Export Standings CSV…" writer had no named module; (c) the build ran as ONE PR for the whole EPIC, not one per task. | (a) Folded into E6 as `csvio.export(ride, path, *, placed=None)`; (b) `csvio.export_standings(placed, path, *, show_times=False)` (all CSV I/O in csvio; its dependency line now includes standings); (c) product decision — the epic ships as PR #12 with per-task test-first commits; the "one task per PR" rule is overridden for EPIC 6 and recorded in project-plan.md's E6 write-back. |
