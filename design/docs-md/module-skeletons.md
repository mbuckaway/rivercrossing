# RiverCrossing — Module Skeletons

*Build plan · v1.0 · July 24 2026 · companion to Spec §11*

Repo layout · public APIs · build order
Names here are binding for implementation

The file-level plan of the repository: where every module lives, its public surface (the skeleton the tdd-python-writer agent writes tests against first), and the order to build. Grounded in current Python packaging practice (src/ layout + pyproject.toml) and the wxPython community's MVP "passive view" pattern — wx appears only in the view layer; presenters and models import no wx and test headless.

### S1 · Layout principles

- **src/ layout, one installable package.** Code lives under `src/rivercrossing/`; tests import the *installed* package (editable install), never the working directory — the standard guard against false-green imports.

- **pyproject.toml is the whole config** — metadata, dependencies, entry points, plus tool tables for ruff, mypy (strict), pytest and coverage. No setup.py, no requirements.txt.

- **MVP, passive view.** Views (wx) are dumb: they render view-models and forward events. Presenters are pure Python — they hold UI logic, call the core, and are unit-tested headless with fake views (satisfying R-71's zero-wx rule and §12's functional harness). Business logic lives below both, in the core modules.

- **Core modules are libraries** (R-71): each imports alone, tests alone, and could ship alone — the card algorithm (`hands`) especially, per the simulation mandate.

- **Nothing invents names.** Module names below are exactly Spec §11's; dialog/window names map 1:1 to the UI designs (ids in comments).

### S2 · Repository tree
```
rivercrossing/
├── pyproject.toml              # PEP 621 metadata + ruff/mypy/pytest/coverage config
├── README.md · LICENSE · CHANGELOG.md
├── .github/workflows/
│   ├── ci.yml                  # §14 six-stage matrix: windows-latest + macos-latest
│   └── release.yml             # tag → PyInstaller → NSIS .exe / notarized .dmg (the unsigned tag release lives in ci.yml since Phase 11; this file arrives with EPIC 9 signing)
├── installers/
│   ├── rivercrossing.spec           # PyInstaller (both OSes, one spec; branded icons since Phase 8)
│   ├── windows.nsi             # NSIS, per-user, unsigned (exists — Phase 9; NSIS replaces Inno Setup, R-01); Authenticode in release.yml (E9.1.2)
│   ├── dmg_settings.py         # dmgbuild config (exists — Phase 8, unsigned); codesign + notarize in release.yml (E9.1.3)
│   └── branding/               # icon + DMG-background SVG sources and their COMMITTED generated
│                               #   artifacts (.icns/.ico/dual-res .tiff — no PNG in git);
│                               #   regenerate with tools/gen_app_icons.py via `nox -s gen_branding`
├── docs/user-guide/            # per the User Guide outline (6a)
├── src/rivercrossing/
│   ├── __init__.py             # __version__ single source
│   ├── __main__.py             # python -m rivercrossing → ui.app.main()
│   ├── py.typed
│   ├── cards.py                # card model + seeded Shoe
│   ├── hands.py                # poker evaluator: eval5 + wild layer + ranking table
│   ├── standings.py            # ordering, tie-breaks ①②③, leaderboards
│   ├── ride.py                 # state machine, crossings, timing, undo; RideConfig (E3.5)
│   ├── roster.py               # in-memory entries/riders/teams + lock matrix (§1–§2, E3)
│   ├── store/
│   │   ├── __init__.py         # Store facade (public API)
│   │   ├── schema.py           # DDL v1 + PRAGMAs (WAL, foreign_keys)
│   │   ├── migrations.py       # linear, numbered, idempotent
│   │   ├── writer.py           # the single async writer task (§10)
│   │   ├── audit.py            # append-only audit log (R-33/R-38)
│   │   └── backup.py           # open + hourly + manual, keep 20 (R-54)
│   ├── csvio.py                # §7 import/export, preview-then-commit
│   ├── htmlexport.py           # §8 Jinja2 renderer (self-contained page)
│   │   └── templates/          #   base.html.j2 + widget macros + vendored CSS/fonts
│   ├── pdfexport.py            # §8b fpdf2 renderer + podium poster (5a–5d)
│   └── ui/
│       ├── app.py              # wx.App bootstrap, theme + session wiring
│       ├── theme.py            # appearance modes via wx.App.SetAppearance (R-03);
│       │                       #   token table deferred — no consumer yet (open item O2)
│       ├── sound.py            # three WAV cues per §10 (recorded/flagged/error)
│       ├── ids.py              # mirror of XRC names — generated from xrc/, drift fails CI (R-05/73)
│       ├── xrc/                # canonical UI: main, setup, riders, detail, results,
│       │                       #   library, audit, settings, dialogs (.xrc — Spec §15b)
│       ├── assets/             # icons, cue WAVs, cards/ (53 bitmaps @1x/2x), fonts
│       ├── presenters/         # pure Python, no wx — one per window
│       │   ├── console.py · setup.py · riders.py · results.py
│       │   ├── library.py · detail.py · audit.py · settings.py
│       └── views/              # wx only — thin loaders binding xrc/ resources, no business logic
│           ├── main_frame.py   # 1a/1b + menubar (2c) + status bar
│           ├── console_panel.py# feed, entry field, counters (1a, 8a–8c)
│           ├── ride_setup.py   # 1c/7a
│           ├── rider_editor.py # 1d/2b + csv preview (3e)
│           ├── entry_detail.py # 1e/7b
│           ├── results_win.py  # 1f
│           ├── ride_library.py # 1g
│           ├── audit_view.py   # Ride ▸ Audit Trail (R-38)
│           └── dialogs.py      # 3a–3f, 4a: settings, DNF, edit-crossing,
│                               #   confirms, resume/exit, about, self-test
└── tests/                      # mirrors src; see S5
```

### S3 · Build order & dependency graph

Strictly bottom-up; each module goes red→green→refactor before the next starts (R-70). Arrows read "imports".

```
1 cards ── no deps
2 hands ── no deps                        (pure algorithm; vectors + Hypothesis + brute force)
3 standings ─→ hands                     (ranking + tie-breaks over evaluated hands)
4 ride ─→ cards                          (state machine deals via Shoe; no DB, no wx)
4b roster ─→ ride                        (E3's store-less models: entries/riders/teams, the
                                          lock matrix, audited mutations the store later persists)
5 store ─→ ride, cards, roster           (persists events; replays them back into RideEngine)
6 csvio ─→ roster, standings             (finished-ride columns + §15 standings CSV need standings)
7 htmlexport / 8 pdfexport ─→ standings, store models
9 ui.presenters ─→ ride, store, standings, csvio, exports
10 ui.views + app ─→ presenters, theme, sound, ids   (wx enters here, nowhere else)
11 installers + CI release lane          (smoke test the built binary, §14)
```

### S4 · Module skeletons — public surfaces

Signatures the tests are written against. Internals are free; these names are not. All dataclasses are frozen unless noted; times are aware UTC datetimes; durations are float seconds.

rivercrossing.cards — deck model & seeded shoe (§4)

```
class Suit(Enum): CLUBS DIAMONDS HEARTS SPADES
class Rank(IntEnum): TWO=2 … TEN=10 JACK=11 QUEEN=12 KING=13 ACE=14
@dataclass Card(rank: Rank | None, suit: Suit | None, joker: bool = False)
    .code() -> str            # "AS", "TD", "JK" — the stored form
    Card.parse(code: str) -> Card
class Shoe:                   # deterministic multi-deck shoe
    __init__(decks: int, jokers_per_deck: int, seed: int)
    deal() -> tuple[Card, int]          # (card, deal_index); raises ShoeEmpty
    reshuffle() -> None                 # new cycle; audit caller logs it (§4)
    remaining: int · dealt: int · cycle: int
    Shoe.replay(decks, jokers_per_deck, seed, deals: int, cycles: int) -> Shoe
    restitute(card: Card) -> None       # Ctrl+Z: the last-dealt card returns to the front (E2.2.1)
    close() -> None                     # ride Finish locks the shoe; deal/reshuffle/restitute
                                        # raise ShoeClosedError afterwards (E2.2.1)
# invariant: same (config, seed) ⇒ identical deal sequence (R-40)
```

rivercrossing.hands — the card algorithm (§5 · R-41/42/44)

```
class HandClass(IntEnum):     # low beats high nothing — order is the local ranking table
    HIGH_CARD PAIR TWO_PAIR TRIPS STRAIGHT FLUSH FULL_HOUSE
    QUADS STRAIGHT_FLUSH ROYAL_FLUSH FIVE_OF_A_KIND
@dataclass EvaluatedHand(cls: HandClass, tiebreak: tuple[int, ...],
                         best5: tuple[Card, ...], jokers_played_as: tuple[Card, ...])
    # total order: (cls, tiebreak); jokers rendered ★-as-card in exports
best_hand(cards: Sequence[Card]) -> EvaluatedHand      # 0..N cards, any joker count;
    # N<5 ⇒ partial-hand rule; whole-field 180×12 < 1 s (R-42)
compare(a: EvaluatedHand, b: EvaluatedHand) -> int
self_test() -> SelfTestReport   # 7,462 distinct-rank sweep + joker vectors;
                                # wired to launch + Help menu; failure blocks Finish (R-44)
```

rivercrossing.standings — ordering & tie-breaks (§5 · R-43/60)

```
class TieBreak(Enum): MOST_LAPS TOTAL_TIME HIGH_CARD_DRAW
@dataclass EntryResult(entry_id, plate, name, kind, laps, total_time,
                       best_lap, cards, hand: EvaluatedHand, dnf: bool)
@dataclass Placed(place: int, result: EntryResult, tie_note: str | None,
                  draw_required: bool)                 # never silently ordered
rank(results, order: tuple[TieBreak, ...]) -> list[Placed]
laps_leaderboard(results, top: int = 10) -> list[Placed]
time_leaderboard(results, top: int = 10) -> list[Placed]   # most laps, then time
hand_name(hand: EvaluatedHand) -> str   # title-case em-dash prose (E6.1.1, D1); raises on an empty hand
tiebreak_order_from_spellings(spellings) -> tuple[TieBreak, ...]   # ride spellings ⇄ members (E6.1.1)
```

rivercrossing.ride — state machine & timing (§3/§6 · R-30…36)

```
class RideStatus(Enum): DRAFT RUNNING FINISHED REOPENED
@dataclass RideConfig(name, event_date, venue, lap_km, organizer, scorer, planned_start,
                      planned_duration_s, min_lap_s, entry_mode, plate_model,
                      max_team_size=4, deck_count=8, jokers_per_deck=2, max_cards=None,
                      tiebreak_order=("laps","total_time","high_card"), logo_path=None)
    # §2 ride-row setup fields; defined here since E3.5, built by ride_setup_dlg,
    # consumed by RideEngine below; EPIC 6's standings imports the tiebreak spellings
class RideEngine:             # pure; wall-clock injected for tests
    __init__(config: RideConfig, shoe: Shoe, clock: Callable[[], datetime])
    start(at: datetime | None = None) -> Event          # button or retro time (R-30)
    set_start_time(at: datetime) -> Event               # lap-1 recompute (3d)
    record_crossing(plate: str, at=None) -> CrossingResult
        # → lap n, lap_time, card | ShortLapFlagged (card held, 1h) | UnknownPlate
    add_crossing_at(plate: str, at: datetime) -> CrossingResult   # RUNNING·REOPENED
    undo_last() -> Event · edit_crossing(id, …) · void_crossing(id, reason)
    reassign_crossing(id, plate) · deal_manual(plate, reason) · void_card(id, reason)
    mark_dnf(entry_id, reason) · move_rider(rider_id, team_id)    # pooled only (R-17)
    stop() -> Event · finish() -> Event · reopen() -> Event       # REOPENED = corrections only
    state: RideStatus · elapsed() · remaining() · on_course: int
    snapshot() -> list[EntryResult]                     # feeds standings live
# every mutation returns an Event the store persists; engine rebuilds via replay(events)
# E4 amendments (2026-08-28): __init__ takes `roster` in addition (duck-typed,
#   annotated under TYPE_CHECKING only — roster.py imports RideStatus from this
#   module at runtime, so a runtime back-import would cycle); the concrete shapes
#   are Event(action, payload) · Crossing(entry_id, seq, crossed_at) ·
#   CrossingResult(accepted, plate, entry_id, entry_name, lap, lap_time, card,
#   flagged, reason) · HeldCrossing(crossing, card); RideEngine exposes `events`
#   (audit log), `held_crossings()`, `confirm_held`/`void_held`,
#   `deal_manual(plate, reason)` and the read accessors `config`, `crossings`,
#   `card_for`, `shoe_remaining`, `shoe_total` (E7 consumes the first three).
```

rivercrossing.roster — in-memory roster & lock matrix (§1–§2 · R-11/12/15/17/20 · E3)

```
class EntryMode(StrEnum): SOLO MIXED · class PlateModel(StrEnum): RIDER_POOLED TEAM_RELAY
@dataclass Entry(plate, display_name, type, riders, status, notes)   # identity, not value
@dataclass Rider(name, plate: str | None, sort_order)
class Roster:                 # one ride's entries/riders; status set by the E4 engine
    __init__(*, entry_mode=SOLO, max_team_size=4, plate_model=RIDER_POOLED)
    create_solo_entry · create_team_entry · create_team_entry_of_one · add_rider_to_team
    move_rider · extract_rider_to_solo · update_entry · delete_entry · mark_has_data
    change_solo_plate · change_pooled_rider_plate · change_team_plate
    next_free_plate() -> str                       # highest numeric + 1
    validate_for_start() -> list[StartViolation]   # R-12's floor, checked at start
    entries · audit_log · status                   # audit events persist via the E5 store
can_edit_structure(status) · can_delete_entry(status, has_data)
can_move_rider(status, plate_model) · can_add_entry() · can_fix_name()
# one plate namespace per ride; a pooled team entry adopts its lowest rider plate;
# teams may be size 1 while DRAFT — the floor is enforced at CSV commit and ride start
```

rivercrossing.store — persistence (§2/§9 · R-50…54)

```
class Store:                  # facade; sqlite3, WAL, foreign_keys ON
    Store.open(path) -> Store              # runs migrations; records session row
    rides() · create_ride(config) · duplicate_ride(id) · delete_ride(id, typed_name)
    load_engine(ride_id) -> RideEngine     # replay events; shoe from stored seed
    append(ride_id, event: Event) -> None  # sync commit path
    audit(ride_id, filter=…) -> list[AuditRow]
    session_state() -> SessionState        # CLEAN_QUIT | CRASHED | RUNNING_AT_EXIT (R-52)
class AsyncWriter:            # §10 single writer; UI awaits put(), never blocks
    put(event) -> Awaitable[None] · drain() · close()
backup.run(path, keep=20) · backup.schedule_hourly(…) · backup.restore(src, dst)
schema.py: rides · entries · riders · crossings · cards · audit · sessions · settings
(columns per Spec §2, incl. status enum with REOPENED, shoe seed, plate_model)
```

rivercrossing.csvio / htmlexport / pdfexport (§7/§8/§8b · R-21/61/62/63)

```
csvio.preview(path, ride) -> ImportPreview      # counts + conflicts; writes nothing;
                                                #   ride = the Roster aggregate until E5's Store
csvio.commit(preview) -> ImportReport · csvio.export(ride, path, *, placed=None) -> None
    # commit applies through the roster's own audited mutators, atomically;
    # ImportReport carries inserted/updated/moved/extracted/joined counts + the audit events
    # export with placed (a FINISHED ride's standings) appends laps/cards/best_hand/total_time (§7)
csvio.export_standings(placed, path, *, show_times=False) -> None   # §15 standings CSV (E6.4.2)
@dataclass ExportOptions(show_times=False, laps_board=True, time_board=False,
                        full_field=True, all_cards=True, lap_km=8.0)  # times hidden by default (R-63)
htmlexport.render(ride, placed, opts, *, logo_src=None, generated=None, logo_path=None) -> str
    # Jinja2 (autoescape, StrictUndefined), base.html.j2 + macros
    # (event_header, podium_card, standings_row, laps_board, time_board, field_row, drawn_row)
    # — STATIC markup, no page JS; vendored Tailwind CSS + fonts inlined, payload JSON
    # embedded (</ escaped as <\/), logo base64 (transparent 1×1 fallback), laps/time boards
    # derived from placed when the options ask; times off ⇒ neither cells nor JSON fields emitted.
    # Tests: golden pages from committed fixtures + JSON round-trip (§8)
pdfexport.render(ride, placed, opts, path, *, letter=True, created_at=None, logo_path=None)
    # fpdf2, deterministic bytes (R-62); aware-UTC creation stamp is the only timestamp
pdfexport.podium_poster(ride, placed, path, *, letter=True, created_at=None, logo_path=None)
    # one-page poster (5d)
```

rivercrossing.ui — MVP shell (§10/§13/§15 · R-02/03/31/73/76)

```
# presenters: pure Python; each takes (view: Protocol, store, engine) and is
# unit-tested with a FakeView. Views implement the Protocol with wx.
class ConsoleView(Protocol):   show_feed(rows) · show_counters(c) · flash_crossing(r)
                               set_state(RideStatus) · focus_entry() · play(cue)
                               show_notice(text) · clear_entry()   # Phase 8: entry-row feedback
class ConsolePresenter:        on_plate_entered(text) · on_undo() · on_arm_stop(bool)
                               on_stop_confirmed() · on_hide_times(bool) · tick()
# same pattern: SetupPresenter (7a radios, defaults per §13) · RidersPresenter (csv)
# ResultsPresenter (1f flags, rerank on tie-break change) · LibraryPresenter (1g)
# DetailPresenter (1e/7b) · AuditPresenter (R-38) · SettingsPresenter (3a)
app.main() -> int              # wx.App; resume dialog per session_state (4a/1h)
theme.apply(app, mode) -> AppearanceResult   # light|dark|system via wx.App.SetAppearance (R-03);
                               # tokens(mode) deferred — no custom-drawn consumer yet (O2)
ids.py: PLATE_INPUT = "plate_input" …   # = XRC names, generated from xrc/ (§15b)
sound.play(Cue.RECORDED | Cue.FLAGGED | Cue.ERROR)   # §10 cues, settings toggle
```

### S5 · tests/ — mirrors src, plus the harness

```
tests/
├── unit/                      # per core module, headless, coverage ≥ 90% (R-71)
│   ├── test_cards.py · test_hands.py · test_standings.py · test_ride.py · test_roster.py
│   ├── test_store.py · test_csvio.py · test_htmlexport.py · test_pdfexport.py
│   └── presenters/            # FakeView-driven presenter tests — still no wx
├── (vectors: src/rivercrossing/vectors/ — the 7,462-rank sweep + joker table ship as package
│                              #   data so the launch self-test reads them from the app, R-44/72)
├── property/                  # Hypothesis: hands invariants, shoe determinism,
│                              #   roster mutation sequences, csv round-trip identity
├── simulations/               # seeded whole rides: 180×6 h, both entry modes,
│   └── test_simulated_rides.py#   both plate models, 0/2/4 jokers, cap on/off (§12)
├── functional/                # real wx, driven via ids.py + direct event injection (§12)
│   ├── harness.py             # find-by-SetName, click, type, dialog hooks
│   ├── pages.py               # page objects per window (1a…8c)
│   └── test_menu_coverage.py  # walks every §15 route in every ride state (R-73)
├── acceptance/
│   └── test_full_race.py      # scripted race incl. kill+relaunch, reopen→finish again (R-74)
└── conftest.py                # tmp DB per test, frozen clock, seeded shoes
```

### S6 · pyproject.toml — the shape

```
[project]  name = "rivercrossing"  requires-python = ">=3.14"
    # import package rivercrossing — renamed with the product (§11)
dependencies = ["wxPython~=4.3.1", "fpdf2", "jinja2"]     # wxWidgets 3.3.3, cp314 wheel;
    # SetAppearance ships in it, so dark mode is live on both platforms (R-03)
    # wxasync is deliberately absent — Spec §10; E5 picks the wx⇄asyncio integration
    # sqlite3 is stdlib; Tailwind CLI is a build-time asset step, not a runtime dep
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "hypothesis", "coverage[toml]",
       "ruff", "mypy", "pyinstaller"]
[project.gui-scripts]  rivercrossing = "rivercrossing.ui.app:main"
[tool.ruff] · [tool.mypy] strict = true · [tool.pytest.ini_options]
[tool.coverage.report] fail_under = 90        # core modules (R-71)
```

Companions: Spec (§11 module table, §12 tests, §14 CI) · Requirements (R-70…R-77) · UI Designs (ids referenced in views/). Any rename here must be reflected in Spec §11 the same day — the two documents are one contract.
