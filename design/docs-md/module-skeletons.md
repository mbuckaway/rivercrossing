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
│   └── ci.yml                  # §14 six-stage matrix: windows-latest + macos-latest (Developer ID signing + notarization for the macOS DMG live in its tag release job, E9.1.3)
├── installers/
│   ├── rivercrossing.spec           # PyInstaller (both OSes, one spec; branded icons since Phase 8)
│   ├── windows.nsi             # NSIS, per-user, unsigned (exists — Phase 9; NSIS replaces Inno Setup, R-01); Windows stays unsigned by decision (R-01, no Authenticode certificate)
│   ├── dmg_settings.py         # dmgbuild config (exists — Phase 8, unsigned); codesign + notarize happen in ci.yml's release job (E9.1.3)
│   └── branding/               # icon + DMG-background SVG sources and their COMMITTED generated
│                               #   artifacts (.icns/.ico/dual-res .tiff — no PNG in git);
│                               #   regenerate with tools/gen_app_icons.py via `nox -s gen_branding`
├── docs/user-guide.html        # the built user guide (6a)
├── src/rivercrossing/
│   ├── __init__.py             # __version__ single source
│   ├── __main__.py             # python -m rivercrossing → ui.app.main()
│   ├── py.typed
│   ├── cards.py                # card model + seeded Shoe
│   ├── hands.py                # poker evaluator: eval5 + wild layer + ranking table
│   ├── standings.py            # ordering, tie-breaks ①②③, leaderboards
│   ├── ride.py                 # state machine, crossings, timing, undo; RideConfig (E3.5)
│   ├── roster.py               # in-memory entries/riders/teams + lock matrix (§1–§2, E3)
│   ├── rider_issues.py         # roster defect report (R-78, §S4)
│   ├── store/
│   │   ├── __init__.py         # Store facade (public API); audit reads via Store.audit_rows
│   │   ├── schema.py           # DDL v1 + PRAGMAs (WAL, foreign_keys); one flattened v1
│   │   │                       #   baseline — no migrations module (Phase 2, SCHEMA_VERSION=1)
│   │   └── backup.py           # open + hourly + manual, keep 20 (R-54)
│   ├── csvio.py                # §7 import/export, preview-then-commit
│   ├── htmlexport.py           # §8 Jinja2 renderer (self-contained page; + poster page)
│   │   └── templates/          #   base.html.j2 + poster.html.j2 + widget macros + vendored CSS/fonts
│   ├── pdfexport.py            # §8b fpdf2 renderer + podium poster (5a–5d)
│   └── ui/
│       ├── app.py              # wx.App bootstrap, theme + session wiring
│       ├── theme.py            # appearance modes via wx.App.SetAppearance (R-03);
│       │                       #   token table deferred — no consumer yet (open item O2)
│       ├── sound.py            # three WAV cues per §10 (recorded/flagged/error)
│       ├── logging.py          # per-invocation NDJSON log (F1): one file per launch, pruned
│       │                       #   to the last 20; the app's one crash log (A2)
│       ├── ids.py              # mirror of XRC names — generated from xrc/, drift fails CI (R-05/73)
│       ├── xrc/                # canonical UI: main, setup, riders, results, library,
│       │                       #   audit, settings, teams, simulation, dialogs (.xrc — Spec §15b)
│       ├── assets/             # icons, cue WAVs, cards/ (53 bitmaps @1x/2x), fonts
│       ├── presenters/         # pure Python, no wx — one per window, plus the shared seam
│       │   ├── console.py · setup.py · riders.py · results.py · audit.py
│       │   ├── library.py · settings.py · teams.py · data_source.py · rider_issues.py
│       │   └── selftest.py · simulator.py
│       └── views/              # wx only — thin loaders binding xrc/ resources, no business logic
│           ├── main_frame.py   # 1a/1b + menubar (2c) + status bar + feed/entry/counters (8a–8c)
│           ├── ride_setup.py   # 1c/7a
│           ├── rider_editor.py # 1d/2b + csv preview (3e)
│           ├── crossing_detail.py # 1e/7b (J2/K2)
│           ├── results_win.py  # 1f
│           ├── ride_library.py # 1g
│           ├── audit.py        # Ride ▸ Audit Trail (R-38)
│           ├── team_editor.py · rider_issues.py · gauges.py · corrections.py
│           ├── settings.py · about.py · selftest.py · shortcuts.py · simulator.py
│           ├── _support.py     # shared window helpers (find by name, load, screen fit)
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
    .code() -> str            # "AS", "10D", "JK" — the stored form
    Card.parse(code: str) -> Card
class Shoe:                   # deterministic multi-deck shoe
    __init__(decks: int, jokers_per_deck: int, seed: int, *, jokers_total: bool = False)
    deal() -> tuple[Card, int]          # (card, deal_index); raises ShoeEmpty
    reshuffle() -> None                 # new cycle; audit caller logs it (§4)
    remaining: int · dealt: int · cycle: int
    Shoe.replay(decks, jokers_per_deck, seed, deals: int, cycles: int, *,
                jokers_total: bool = False) -> Shoe
    restitute(card: Card) -> None       # Ctrl+Z: the last-dealt card returns to the front (E2.2.1)
    close() -> None                     # ride Finish locks the shoe; deal/reshuffle/restitute
                                        # raise ShoeClosedError afterwards (E2.2.1)
# jokers_total selects the mode (§4): False (the S4 default) re-deals jokers_per_deck
#   jokers every cycle; True spends that many jokers once across the ride — the budget
#   persists across reshuffles, and a spent budget deals naturals only
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
self_test() -> SelfTestReport   # six checks: 7,462 distinct-rank sweep + joker vectors +
                                # five-of-a-kind ordering + field timing, then compare()'s
                                # total order and best_hand()'s joker bound;
                                # wired to launch + Help menu; the timing check is ADVISORY
                                # (SelfTestCheck.blocking=False, it times the host), the rest
                                # are blocking and gate Finish unless overridden (R-44)
```

rivercrossing.standings — ordering & tie-breaks (§5 · R-43/60)

```
class TieBreak(Enum): MOST_LAPS TOTAL_TIME HIGH_CARD_DRAW
    # HIGH_CARD_DRAW both orders a fully drawn group by cards.draw_key
    # (rank major, suit minor, spades highest) and stamps the barrier:
    # an undrawn group stops there (R-14 / R-43)
@dataclass EntryResult(entry_id, plate, name, kind, laps, total_time,
                       best_lap, cards, hand: EvaluatedHand, dnf: bool,
                       sex: str | None = None,
                       tiebreak_card: Card | None = None)  # R-14's drawn card
@dataclass Placed(place: int, result: EntryResult, tie_note: str | None,
                  draw_required: bool)                 # never silently ordered
rank(results, order: tuple[TieBreak, ...]) -> list[Placed]
laps_leaderboard(results, top: int = 10) -> list[Placed]
time_leaderboard(results, top: int = 10) -> list[Placed]   # most laps, then time
hand_name(hand: EvaluatedHand) -> str   # title-case em-dash prose (E6.1.1, D1); raises on an empty hand
tiebreak_order_from_spellings(spellings) -> tuple[TieBreak, ...]   # ride spellings ⇄ members (E6.1.1)
# the venue's draw itself lives in cards: high_card_draw(seed, count) deals from
# one fresh, jokerless 52-card deck and draw_key(card) orders it; RideEngine.finish()
# performs and records it, and a replayed tiebreak_draw restores it verbatim
```

rivercrossing.ride — state machine & timing (§3/§6 · R-30…36)

```
class RideStatus(StrEnum): DRAFT RUNNING FINISHED REOPENED
@dataclass RideConfig(name, event_date, venue, lap_km, organizer, scorer, planned_start,
                      planned_duration_s, min_lap_s, entry_mode, plate_model,
                      max_team_size=4, deck_count=8, jokers_per_deck=1, jokers_mode="total",
                      max_cards=None,
                      tiebreak_order=("high_card","laps","total_time"), logo_path=None,
                      hold_short_laps=True)
    # §2 ride-row setup fields; defined here since E3.5, built by ride_setup_dlg,
    # consumed by RideEngine below; EPIC 6's standings imports the tiebreak spellings
class RideEngine:             # pure; wall-clock injected for tests
    __init__(config: RideConfig, shoe: Shoe, clock: Callable[[], datetime])
    start(at: datetime | None = None) -> Event          # button or retro time (R-30)
    set_start_time(at: datetime) -> Event               # lap-1 recompute (3d)
    record_crossing(plate: str, at=None) -> CrossingResult
        # → lap n, lap_time, card | ShortLapFlagged (flagged; card held only under hold_short_laps) | UnknownPlate
    add_crossing_at(plate, crossed_at, reason) -> Event   # RUNNING·REOPENED
    undo_last() -> Event · edit_crossing(entry_id, seq, crossed_at, reason)
    void_crossing(entry_id, seq, reason) · reassign_crossing(seq, new_plate, reason)
    deal_manual(plate, reason) · void_card(entry_id, card, reason) · mark_dnf(plate, reason)
    # rider moves are not the engine's: Roster.move_rider(rider, *, to_entry); pooled only (R-17)
    stop() -> Event · finish(*, self_test_failed_checks=()) -> Event · reopen() -> Event
        # REOPENED = corrections only; finish() also performs and records R-14's
        # high-card draw (one tiebreak_draw event) and writes any overridden
        # self-test check names onto the finish event (E6.4.3)
    state: RideStatus · elapsed() · remaining() · on_course: int
    self_test_unverified: bool   # the last finish overrode a red self-test (E6.4.3)
    snapshot() -> list[EntryResult]                     # feeds standings live;
        # each result carries its own R-14 tiebreak_card
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
# card-sufficiency advisory (ride.py module functions, ride-sim/corrections batch):
#   estimate_cards_needed(config, roster, avg_speed_kmh) -> int | None ·
#   check_card_sufficiency(config, roster, avg_speed_kmh) -> CardCheck | None, where
#   CardCheck(shoe_cards, expected, verdict) and verdict ∈ {NOT_ENOUGH, OK, FAR_TOO_MANY}
#   compares the shoe size at the ride's jokers mode (deck_count × (52 + jokers_per_deck)
#   per deck, deck_count × 52 + jokers_per_deck total) against the crossings a
#   field is expected to record (per rider pooled, per entry relay) — see Spec §4
```

rivercrossing.roster — in-memory roster & lock matrix (§1–§2 · R-11/12/15/17/20 · E3)

```
class EntryMode(StrEnum): SOLO MIXED · class PlateModel(StrEnum): RIDER_POOLED TEAM_RELAY
@dataclass Entry(plate, display_name, type, riders, status, notes, has_data, logo_card)
                 # identity, not value; has_data is the delete guard (R-15)
@dataclass Rider(first_name, last_name="", plate: str | None = None,
                 sex: str | None = None, sort_order=0)
class Roster:                 # one ride's entries/riders; status set by the E4 engine
    __init__(*, entry_mode=SOLO, max_team_size=4, plate_model=RIDER_POOLED)
    create_solo_entry · create_team_entry · create_team_entry_of_one · add_rider_to_team
    move_rider · extract_rider_to_solo · remove_rider · update_entry · delete_entry · mark_has_data
    change_solo_plate · change_pooled_rider_plate · change_team_plate · change_plate   # the model-correct dispatch the editors + fixes share
    next_free_plate() -> str                       # highest numeric + 1
    validate_for_start() -> list[StartViolation]   # R-12's floor, checked at start
    entries · audit_log · status · take_audit_log()  # audit events persist via the E5 store
can_edit_structure(status) · can_delete_entry(status, has_data)
can_move_rider(status, plate_model) · can_add_entry() · can_fix_name()
team_name_key(name) -> str                     # fuzzy team key; the CSV preview and rider_issues share it
# one plate namespace per ride; a pooled team entry adopts its lowest rider plate;
# teams may be size 1 while DRAFT — the floor is enforced at CSV commit and ride start
```

rivercrossing.rider_issues — the roster defect report (R-78 · rider_issues_dlg)

```
rider_issues(roster) -> tuple[RiderIssue, ...]
    # stable report order: team-of-one · missing-name · missing-number · duplicate-name
    #   · duplicate-team-name · near-duplicate-team-name (a ⚠ warning) · duplicate-number
    # team-name checks reuse the CSV importer's identity rules (roster.team_name_key / the normalized name)
```

rivercrossing.store — persistence (§2/§9 · R-50…54)

```
class Store:                  # facade; sqlite3, WAL, foreign_keys ON
    Store.open(path) -> Store              # ensures the one flattened v1 schema; records session row
    rides() · create_ride(config) · duplicate_ride(id) · delete_ride(id, typed_name)
    load_engine(ride_id) -> RideEngine     # replay events; shoe from stored seed
    append(ride_id, event: Event) -> None  # sync commit path (syncs ride.status for lifecycle actions)
    append_roster_event(ride_id, action, payload_json) -> None  # roster plate-change audit row; no status write
    audit_rows(ride_id) -> list[AuditRow]  # the audit viewer's read accessor (E7.3.1)
    session_state() -> SessionState        # CLEAN_QUIT | CRASHED | RUNNING_AT_EXIT (R-52)
# AsyncWriter (§10's single writer: put()/drain()/close()) is deliberately absent —
#   nothing calls it yet, and append() commits synchronously (store/__init__.py)
backup.run(path, keep=20) · backup.schedule_hourly(…) · backup.restore(src, dst)
schema.py: ride · entry · rider · crossing · card · app_session · audit (+ schema_version)
(columns per Spec §2, incl. status enum with REOPENED, shoe seed, plate_model; one flattened
 v1 baseline — no migrations, and no settings table: E8.1.1 keeps settings in a JSON config file)
```

rivercrossing.csvio / htmlexport / pdfexport (§7/§8/§8b · R-21/61/62/63)

```
csvio.preview(path, ride, *, map_unknown_sex_to_male=False, convert_teams_of_one_to_solo=False) -> ImportPreview   # counts + conflicts; writes nothing;
                                                #   ride = the Roster aggregate until E5's Store
                                                #   convert_teams_of_one_to_solo imports a DRAFT one-rider team as a solo entry (both plate models)
csvio.commit(preview) -> ImportReport · csvio.export(ride, path, *, placed=None) -> None
    # commit applies through the roster's own audited mutators, atomically;
    # ImportReport carries inserted/updated/moved/extracted/joined counts + the audit events
    # export with placed (a FINISHED ride's standings) appends laps/cards/best_hand/total_time (§7)
csvio.export_standings(placed, path, *, show_times=False) -> None   # §15 standings CSV (E6.4.2)
@dataclass ExportOptions(show_times=False, laps_board=True, time_board=False,
                        full_field=True, all_cards=True, lap_km=8.0)  # times hidden by default (R-63)
htmlexport.build_payload(ride, placed, opts, generated, team_logos=None) -> RacePayload
    # the shared results model both renderers consume (§8b); LapsBoardRow gains `type`
    # (TEAM/SOLO) so the boards split per kind — the record carries the key
htmlexport.sections(payload, placed) -> Sections
    # per-kind partition + the counts: podium 3/3 (or 3), top lists 5/5 (or 10),
    # laps boards 5/5 (or 10); team rows render plate-less
htmlexport.format_generated(at) -> str
    # "Generated H:MM, Mon D YYYY" — a pure function of its input, no tz conversion (R-62)
htmlexport.render(ride, placed, opts, *, logo_src=None, generated=None, logo_path=None,
                  team_logos=None) -> str
    # Jinja2 (autoescape, StrictUndefined), base.html.j2 + macros
    # (event_header, podium_card, standings_row, laps_board, time_board, field_row, drawn_row)
    # — STATIC markup, no page JS; vendored Tailwind CSS + fonts inlined, payload JSON
    # embedded (</ escaped as <\/), logo base64 (transparent 1×1 fallback), laps/time boards
    # derived from placed when the options ask; times off ⇒ neither cells nor JSON fields emitted.
    # Tests: golden pages from committed fixtures + JSON round-trip (§8)
htmlexport.render_poster(ride, placed, opts, *, logo_path=None, generated=None) -> str
    # the poster page (§8b's 5d content) — poster.html.j2 through the same
    # environment and payload: top 3 teams over top 3 solo riders, or a
    # solo-only field's top 5; one card per placing, no heading for a kind
    # the ride does not have
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
class ConsolePresenter:        on_plate_entered(text) · on_undo() · on_start() · on_finish()
                               on_stop_requested() · on_stop_confirmed() · on_reopen()
                               on_time_columns(*, show_total, show_lap) · tick() · refresh_state()
                               # arm-to-stop is retired; hide-times is on_time_columns (R-37)
# same pattern: SetupPresenter (7a radios, defaults per §13) · RidersPresenter (csv;
# AddRiderPresenter/EditRiderPresenter) · ResultsPresenter (1f flags — its tie-break order
# is read from RideConfig at construction, no rerank control) · AuditPresenter (R-38) ·
# TeamsPresenter (team_editor_dlg) · RiderIssuesPresenter (R-78) · SelfTestPresenter (3f) ·
# SimulatorPresenter (Rider Simulator); library.py/settings.py hold the View protocol only
app.main() -> int              # wx.App; resume dialog per session_state (4a/1h)
theme.apply(app, mode) -> AppearanceResult   # light|dark|system via wx.App.SetAppearance (R-03);
                               # tokens(mode) deferred — no custom-drawn consumer yet (O2)
logging.Logging(path, *, verbose=True) -> Logging   # per-invocation NDJSON log (F1): one file per launch;
                               # .startup()/.launch()/.ride_loaded()/.exception() always write, the
                               # .marker()/.menu()/.dialog()/.button()/.control() trace honours verbose
logging.build_log_path(directory, now) -> Path      # rivercrossing-<YYYYmmdd-HHMMSS>.log inside directory
logging.prune_logs(directory, *, keep=20) -> None   # keep only the most recent invocation logs
ids.py: PLATE_INPUT = "plate_input" …   # = XRC names, generated from xrc/ (§15b)
sound.play(Cue.RECORDED | Cue.FLAGGED | Cue.ERROR)   # §10 cues, settings toggle
```

### S5 · tests/ — mirrors src, plus the one open/quit smoke

```
tests/
├── unit/                      # per core module, headless, coverage ≥ 90% (R-71)
│   ├── test_cards.py · test_hands.py · test_standings.py · test_ride.py · test_roster.py
│   ├── test_store.py · test_csvio.py · test_htmlexport.py · test_pdfexport.py
│   ├── presenters/            # FakeView-driven presenter tests — still no wx
│   ├── ui/                    # view-layer and app-wiring tests
│   └── fixtures/              # csv/htmlexport/pdfexport fixtures + committed goldens
├── (vectors: src/rivercrossing/vectors/ — the 7,462-rank sweep + joker table ship as package
│                              #   data so the launch self-test reads them from the app, R-44/72)
├── functional/                # the ONE permitted functional test: real wx, driven via ids.py
│   └── test_app_menu_quit.py  # open/quit smoke (spec §14) — the Windows open-crash gate
└── conftest.py                # tmp DB per test, frozen clock, seeded shoes
# property/ · simulations/ · acceptance/ are retired and must not be re-created: unit tests
#   plus the one open/quit smoke are the whole suite (spec §14, R-73/R-74)
```

### S6 · pyproject.toml — the shape

```
[project]  name = "rivercrossing"  requires-python = ">=3.14"
    # import package rivercrossing — renamed with the product (§11)
dependencies = ["wxPython~=4.3.1", "fpdf2", "jinja2", "phevaluator", "platformdirs"]
    # wxWidgets 3.3.3, cp314 wheel; SetAppearance ships in it, so dark mode is
    # live on both platforms (R-03). phevaluator backs the eval5 fast path (§5);
    # platformdirs locates the per-user settings.json (E8.1.1).
    # wxasync is deliberately absent — Spec §10; E5 picks the wx⇄asyncio integration
    # sqlite3 is stdlib; Tailwind CLI is a build-time asset step, not a runtime dep
[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "pytest-cov", "pytest-xdist", "pytest-rerunfailures",
       "hypothesis", "defusedxml", "coverage[toml]", "nox", "ruff>=0.15", "mypy",
       "import-linter", "pyinstaller", "pypdf", "pillow",
       "dmgbuild; sys_platform == 'darwin'"]
[project.gui-scripts]  rivercrossing = "rivercrossing.ui.app:main"
[tool.ruff] · [tool.mypy] strict = true · [tool.pytest.ini_options]
[tool.coverage.report] fail_under = 90        # core modules (R-71)
```

Companions: Spec (§11 module table, §12 tests, §14 CI) · Requirements (R-70…R-76) · UI Designs (ids referenced in views/). Any rename here must be reflected in Spec §11 the same day — the two documents are one contract.
