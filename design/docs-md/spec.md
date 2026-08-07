# RiverCrossing — Engineering Spec

*Engineering spec · v0.1 · July 23 2026*

Target: Python 3.14 · wxPython 4.3 · async
Windows + macOS · SQLite

### 1 · Event model

Reference event: the [GORBA EPIC](https://gorba.ca/events/gorba-epic/) — a 6-hour poker run on an 8 km closed loop (riding 10:00–16:00). An **entry** is a solo rider or, when the ride's entry mode allows it, a relay team sharing one plate; one rider per entry is on course at a time. Team size runs from 2 up to the ride's **max riders per team** — an entry box at setup, default 4, hard limit 10 (the EPIC uses 4). Mixed rides also choose a **plate model**: Rider plates — pooled (**default** — every rider carries a unique plate, draws one card per lap against it with **no per-rider limit** — one rider may out-lap the whole team — and the team's hand is scored from the pooled cards; the ride-level cap X, when set, applies to the entry's pooled total) or Team plate — relay (one plate per entry, one rider on course at a time — the EPIC's format, chosen at setup). **New rides default to solo-only** — team fields and columns stay hidden until "Solo + teams" is selected at setup (the EPIC runs mixed). In mixed mode, teams are fully configurable **right up to the moment the ride starts**: create, rename, resize (2 up to the ride's max), move riders between entries, convert solo ⇄ team — in the editor or by re-importing CSV as many times as needed. Starting the ride locks entry structure (plates, types, membership); rider-name spelling fixes stay allowed and are audit-logged. Every completed lap crosses the timing line once and deals the entry one card from a virtual shoe. Placings are by best 5-card poker hand from all cards held. Laps and times are recorded but unofficial. Nothing here is EPIC-specific: lap length, duration, shoe, cap and tie-breaks are all per-ride settings.

### 2 · Database — SQLite, one file, many rides

PRAGMA journal_mode=WAL · synchronous=NORMAL · foreign_keys=ON. One transaction per operator action. Timestamps are UTC epoch; display is local. Auto-backup: copy of rides.db on open and hourly while running.

| Table | Columns (″·″ separated; *italic = nullable*) |
|---|---|
| ride | id · name · event_date · venue · course_name · lap_km · organizer · scorer · *logo_png BLOB* · planned_start · planned_duration_s · *actual_start* · *finished_at* · status (draft \| running \| finished \| reopened) · entry_mode (solo \| mixed — **default solo**; mixed enables teams) · max_team_size (2–10, default 4) · plate_model (rider_pooled \| team_relay — default rider_pooled) · min_lap_s · deck_count · jokers_per_deck · *max_cards* (NULL = one per lap, uncapped) · tiebreak_order JSON · rng_seed · created_at · updated_at |
| entry | id · ride_id → · plate (UNIQUE per ride) · display_name · type (solo \| team) · team_size (2 ≤ n ≤ ride.max_team_size) · status (active \| dnf) · *dnf_at* · *notes* |
| rider | id · entry_id → · name · *plate* (rider_pooled rides: unique per ride; crossings and cards attribute to the rider's plate and pool to the entry) · sort_order · *emergency_contact* · *waiver_signed* · *ccn_reg_id* (optional fields from race-timing practice; no age/category fields anywhere — out of scope by decision) |
| crossing | id · ride_id → · entry_id → · *rider_id* (which team member, if tracked) · seq (1..n per entry) · crossed_at · lap_s · flag (none \| short \| manual) · voided · *void_reason* |
| card | id · ride_id → · entry_id → · *crossing_id* (NULL = added manually) · *shoe_index* · rank (2–14, 0 = joker) · *suit* (s h d c) · state (held \| dealt \| voided) · dealt_at |
| app_session | id · opened_at · *closed_at* (written on clean exit — NULL means the previous session crashed) · *active_ride_id* · heartbeat_at (touched every 30 s while a ride runs) — how reopening knows a ride was running and whether the close was clean or a crash. |
| audit | id · ride_id → · at · action · payload_json — every mutation: record, undo, void, reassign plate, deal, manual card, DNF, setting change. Undo = compensating write, never DELETE. |

### 3 · Ride state machine

```
DRAFT
  → start (button, or “set start time” if the gun was missed)
RUNNING
  → finish
FINISHED
  ⇄ reopen / finish again
REOPENED
```

- **Elapsed time is wall-clock:** `now − actual_start`. There is no in-memory timer to lose — quit, crash or “stop” never costs time or data.

- **Stop** is a UI guard, not a state, and takes three deliberate acts: the console Stop button is disabled until the *Arm* checkbox beside it is ticked, pressing it then opens the confirm dialog, and only confirming locks the entry field. The checkbox disarms itself after use or after 10 s untouched. Pressing Start on a ride that already has crossings asks *“Continue ride?”* — continue keeps `actual_start` unchanged; “start a new ride” archives first.

- **Exit is caught:** quitting the app (File ▸ Exit / ⌘Q / app-menu Quit, or the window × on Windows) with a ride RUNNING opens the exit dialog — Cancel · Finish ride first… · Quit-keep-running. Quitting writes `app_session.closed_at` and leaves the ride RUNNING; the wall clock keeps counting while the app is closed. **The app never exits without confirmation** (Phase 8 amendment): with no ride RUNNING the same quit paths open `exit_confirm_dlg` (Cancel default + focused). **On macOS the window × never quits** — it hides the window and the app keeps running (Dock click reopens it via a `MacReopenApp` override); Dock ▸ Quit and log-out arrive as a session-end query and run the same confirm flow.

- **Resume on open:** on launch, ride status = running always opens the resume dialog. Session bookkeeping picks the copy: closed_at present → "You quit at 12:41 — the ride kept running"; closed_at NULL → crash, "closed unexpectedly at 12:41" (last heartbeat). Continue picks up mid-ride; every crossing was committed when it happened.

- **DRAFT is the roster window:** entries and teams stay fully editable — including delete — via UI or repeated CSV imports, until start. Start locks plates, entry types and membership; after start only new plates and name fixes. Entries with recorded data are never deleted, only DNF'd or voided. **Rider-pooled rides stay editable while RUNNING:** riders may move between teams mid-event (mis-entries, team switches) — the rider's plate, crossings and cards travel with them, every move audit-logged. Relay rides keep the start lock (the plate *is* the team's identity).

- **Set start time…** retro-fixes `actual_start` and recomputes lap-1 times; logged to audit.

- **REOPENED is corrections-only** (a distinct status, not RUNNING): the clock stays closed and live plate entry stays off; the operator adds a missed crossing *at an explicit time*, edits/voids crossings and cards, or moves riders (pooled). Standings recompute on every change and on tie-break reordering; *Finish again* re-locks to FINISHED. Published exports older than the latest correction are flagged stale.

- **Audit trail viewer** (Ride ▸ Audit Trail…): read-only, newest-first table of the audit log — when · who (operator) · action · entry · reason — filterable by entry and action; Entry detail links straight to it pre-filtered. In pooled mode, "Move to team…" on a rider row opens a team picker and lands here too.

- **Delete ride** (library only): type the ride's name to confirm; an automatic database backup is written first; a RUNNING ride is never deletable.

### 4 · The shoe — dealing cards

The shoe is `deck_count × (52 + jokers_per_deck)` cards (default 8 decks × 2 jokers = 432 for a 180-entry field — the XRC canvas draws 2 decks, so the *default* is an open question owned by the ride-setup work in E3/E4; the XRC declares no value and the presenter supplies it), Fisher-Yates shuffled with the stored `rng_seed`. A crossing deals `shoe[deal_index++]`; the index is recoverable from dealt-card count, so the deal is **deterministic and auditable** — replaying the seed reproduces every card. Duplicates across entries are expected (multi-deck); an empty shoe reshuffles (seed+1) with an audit entry. Short-lap crossings deal into state *held* until the operator confirms or voids. Manual add/void is always available from the entry detail.

### 5 · Hand evaluation — best 5 of N, jokers wild

| # | Hand — best → worst | Example |
|---|---|---|
| 1 | Five of a Kind, wild or natural | 9 9 9 9 ★ |
| 2 | Royal Flush | A K Q J 10 suited |
| 3 | Straight Flush | 8 7 6 5 4 suited |
| 4 | Four of a Kind | Q Q Q Q 7 |
| 5 | Full House | J J J 4 4 |
| 6 | Flush | K J 8 6 2 suited |
| 7 | Straight (A high or low) | 9 8 7 6 5 |
| 8 | Three of a Kind | 7 7 7 K J |
| 9 | Two Pair | K K 5 5 A |
| 10 | One Pair | A A K J 8 |
| 11 | High Card | A Q 9 5 3 |

Within a class, standard kicker comparison — the 7,462 distinct natural ranks are stored as one integer per entry, so sorting the field is a plain sort. Ship the rank table locally; self-test on startup against known vectors (wheel straight, joker five-of-a-kind, …).

```
best_hand(cards):                # n ≤ ~15
  naturals, j = split_jokers(cards)
  if j >= 5: return FIVE_OF_KIND(Ace)
  best = -inf
  # a wild never hurts → all jokers play
  for subset in combinations(naturals, 5 - j):
    for fill in completions(subset, j):
      # candidates pruned to: ranks in subset,
      # straight-completing ranks, suits present,
      # aces — ≤ ~20 per wild (52 also fine)
      best = max(best, eval5(subset + fill))
  return best
# eval5 = phevaluator/treys + a five-of-a-kind
# check ranked above straight flush.
# n=12, j=2 → C(10,3)=120 subsets ≈ <1 ms;
# whole 180-entry field well under a second.
```

A joker always plays as whichever natural card — or, with more than one joker, whichever combination of natural cards — turns the hand into the best hand reachable, never the first legal completion. A joker may duplicate a card already held elsewhere in the same hand: the multi-deck shoe (§4) means a repeated card is not a foul, and refusing to reuse a suit just because it appears elsewhere in the hand would silently settle for a worse hand than the cards actually support. The table below is the joker vector set the startup self-test checks (§5's own "known vectors" line, above); `tests/vectors/joker_vectors.csv` encodes it row for row.

*One joker*

| Natural cards | Best hand | Joker plays as |
|---|---|---|
| A A A A | Five of a Kind, aces | a fifth ace (any suit — duplicate-legal) |
| 9 9 9 9 | Five of a Kind, nines | a fifth nine |
| A K Q J suited | Royal Flush | 10 of the same suit |
| 8 7 6 5 suited | Straight Flush, 9-high | 9 of the same suit — not 4, which only reaches 8-high |
| A 2 3 4 suited | Straight Flush, 5-high (the wheel) | 5 of the same suit — beats stopping at an ace-high flush |
| Q Q Q 7 | Four of a Kind, queens | the fourth queen |
| J J 4 4 | Full House, jacks over fours | a third jack — jacks-over-fours beats fours-over-jacks |
| K J 8 6 suited | Flush, ace-high | an ace of the same suit, maximizing the kicker |
| 9 8 7 6 | Straight, 10-high | 10 — not 5, which only reaches 9-high |
| A 2 3 4 | Straight, 5-high (the wheel) | 5 |
| 9 8 6 5 | Straight, 9-high | 7, filling the inside gap |
| J 10 9 8 | Straight, queen-high | queen — not 7, which only reaches jack-high |
| 9 8 7 5 suited | Straight Flush, 9-high | 6 of the same suit — beats stopping at an ace-high flush |
| 7 7 K Q | Three of a Kind, sevens | a third seven |
| A A K Q | Three of a Kind, aces | a third ace — a joker on a pair never falls back to two pair |
| A K 9 5 | One Pair, aces | a second ace, the highest pair reachable here |

*Two jokers*

| Natural cards | Best hand | Jokers play as |
|---|---|---|
| A A A | Five of a Kind, aces | two more aces |
| 9 9 9 | Five of a Kind, nines | two more nines |
| A K Q suited | Royal Flush | jack and 10 of the same suit |
| 7 6 5 suited | Straight Flush, 9-high | 9 and 8 of the same suit |
| A 2 3 suited | Straight Flush, 5-high (the wheel) | 5 and 4 of the same suit |
| Q J 9 suited | Straight Flush, king-high | king and 10 of the same suit — the fixed natural 9 puts a royal out of reach |
| K K 2 | Four of a Kind, kings | the two remaining kings — beats stopping at a full house, twos over kings |
| K 7 2 | Three of a Kind, kings | two more kings, the highest rank available |
| 9 8 7 | Straight, jack-high | jack and 10 |

*Three or more jokers*

| Natural cards | Best hand |
|---|---|
| A A | Five of a Kind, aces |
| A | Five of a Kind, aces |
| (none) | Five of a Kind, aces — the `j >= 5` shortcut above |

**Card cap X** (optional per ride): only the first X dealt cards score; later laps still count for laps/time. Entries holding fewer than 5 cards still rank: their cards form the best partial hand, and a missing kicker always ranks below any present one (a 4-card ace-high sits under every 5-card ace-high). A multi-deck shoe can deal one entry two physically identical cards, and within that entry's own hand they rank exactly as the physical cards they are — a pair, three, or four of a kind, or a flush whose kickers happen to repeat a rank, never a dealing error — with five identical cards, wild-assisted or all natural, either way Five of a Kind. Hands this produces outside the 7,462-entry natural table order the same way every hand does: by class, then by the standard kicker comparison. **Ties** between byte-identical hand ranks resolve by the ride's ordered rules — ① most laps ② shortest total time ③ high-card draw at the venue (app flags “draw required”). The order is editable after the finish; standings re-run instantly.

### 6 · Timing rules

- Lap time = this crossing − the entry's previous crossing (seq 1: − actual_start). For teams this *includes* pit/handoff time — relay by design.

- Crossings under `min_lap_s` (default 18:00 for an 8 km loop) flag *short*: usually a double-entry or mistyped plate. Crossing records; card is held.

- Total time = last crossing − actual_start. Leaderboards: laps DESC, then total ASC. Times shown to 1 s.

- Corrections: undo last (compensating write) · void/edit any crossing · reassign a crossing's plate · DNF keeps all laps/cards, listed and marked.

### 7 · CSV import / export

```
plate,entry_name,type,rider_1,rider_2,rider_3,rider_4,notes
127,Dirt Dynamos,team3,Sarah Okafor,Priya Nair,Tom Hale,,
61,Marc Tremblay,solo,,,,,
```

- **Format:** UTF-8 with header; `type ∈ solo | teamN` with N from 2 up to the ride’s max riders per team (≤ 10); `rider_1…rider_N` columns match (the example shows a max-4 ride). Rows exceeding the ride’s max land in the conflict report.

- **Rider-pooled rides use one row per rider instead:** `plate,name,team_name,notes` — riders sharing a team_name form a team (blank = solo). Re-imports may regroup teams; while RUNNING a changed team_name is treated as a membership move (audit-logged), not a conflict.

- **Import semantics:** match on plate (update in place), insert new plates, and list every conflict before anything is written — preview first, commit second, nothing touched on preview.

- **Teams come from the file:** `type` + `rider_1…rider_N` fully define membership; repeated imports keep applying while the ride is in DRAFT (late roster from the registration system, morning-of changes).

- **Once RUNNING:** imports may only add new plates or fix name spellings; structural changes are rejected into the conflict report.

- **Export** mirrors the columns; a finished ride adds `laps, cards, best_hand, total_time`.

### 8 · HTML results export

One self-contained file per publish. Working samples: [epic-2026-results.html](../exports/epic-2026-results.html) (times shown) · [epic-2026-results-no-times.html](../exports/epic-2026-results-no-times.html) (times omitted per R-63 — no time data embedded). **The light template is the only published look** — theme is not an export option.

- **Static markup, rendered by Jinja2.** Every section is rendered at export; the page ships zero script logic and works with JS disabled. The only `<script>` on the page is the data record below.

- **Embedded data record.** The full results payload as JSON in a classic `<script type="application/json" id="race-data">` block — machine-readable, round-trip tested, never a rendering source; every `</` escaped as `<\/`.

- **CSS compiled once at package build, not at export.** CI runs the Tailwind 4 CLI against the frozen template and vendors the output (plus Barlow / Barlow Condensed woff2 subsets, base64) into `rivercrossing.htmlexport` — exporting needs no Node, no CDN, no network. The samples preview with the browser build + Google Fonts as dev stand-ins only. The organizer logo embeds as base64.

- **No invented data.** A value withheld by an option (times, R-63) is absent from both markup and JSON — conditional Jinja blocks omit the cells entirely rather than hiding them.

- **Controls & widgets.** Tailwind ships none and the published page needs none — it is read-only (header, counters, podium cards, tables, card chips): plain utilities plus a frozen custom-class allowlist (`@theme` tokens, .bp/.c corner marks, .chip, .t-col, .no-print). No component library; if v2 ever adds interactive widgets, the pre-approved choice is daisyUI (CSS-only Tailwind plugin, no browser JS) — JS-driven kits (Flowbite, Preline) are ruled out for generated offline files.

- **Templates (binding, written):** [base.html.j2](../templates/base.html.j2) (document + context contract in its header, dev/production head split) · [macros.html.j2](../templates/macros.html.j2) (chip/chips + the eight section macros incl. footer) · [theme.css](../templates/theme.css) (frozen Tailwind source) — copy verbatim into `src/rivercrossing/htmlexport/templates/`. One base template + widget macros; the payload is the frozen `ExportOptions`/standings dataclasses.

- **Render method.** `htmlexport.render()` builds the context dict from the dataclasses (plus the `racejson` filter that escapes `</`), creates `Environment(PackageLoader("rivercrossing.htmlexport", "templates"), autoescape=True, undefined=StrictUndefined)`, and returns `base.html.j2` rendered. Each section is one macro call — `event_header · podium_card · standings_row · laps_board · time_board · field_row · drawn_row` — mirrored 1:1 by the `<!-- j2: -->` comments in the samples.

- **Tests.** Golden-file: committed payload fixtures render to committed golden pages byte-for-byte (goldens regenerate deliberately on template changes) · JSON round-trip re-parses the embedded record and asserts value-identity with the payload model · CI asserts the export holds zero external references (works from `file://`).

### 8b · PDF results export

Same standings model, second renderer: `rivercrossing.pdfexport` emits a print-ready PDF with the same sections and flags as the HTML export (podium, top ten, optional laps/time boards, full field, show/hide times, all cards drawn). Engine: **fpdf2** — pure Python, zero native dependencies, so it bundles cleanly into both installers and renders deterministically (same input ⇒ byte-stable output, ideal for golden-file tests). Letter/A4 selectable; header carries the organizer logo + ride name, footer "Page n of N · generated …"; card graphics drawn as rank + suit glyphs in the steel scheme. *Alternative considered:* WeasyPrint would reuse the HTML template outright (CSS paged media, headers/footers), but drags Pango/Cairo/GDK-PixBuf native libraries into the installers — revisit only if pixel-parity with the web page becomes a requirement. Browser-based converters (Playwright, wkhtmltopdf) are out: no browser dependency in a timing app. Runs off-loop like the HTML export; filename `{ride-slug}-results.pdf`. A "Podium poster" flag additionally emits `{ride-slug}-podium.pdf` — one celebratory page (top 3, large card faces) for the prize table.

### 9 · Failure matrix

| Event | Behaviour |
|---|---|
| App closed (Exit / ⌘Q, or × on Windows) while running | Exit dialog: Cancel · Finish first · Quit-keep-running. Ride stays RUNNING on wall clock; relaunch → resume dialog ("you quit at…"). On macOS × only hides the window — the app keeps running. |
| App closed with no ride running | Quit confirm (`exit_confirm_dlg`, Cancel default) — the app never exits without confirmation (Phase 8). |
| App crash / force-quit while running | Relaunch → resume dialog ("closed unexpectedly at…", from session heartbeat); elapsed continues from wall clock; last committed crossing intact (WAL). |
| Stop pressed by accident | Start again → “Continue ride?” — continue keeps original start; zero loss. |
| Start missed at the gun | “Set start time…” back-dates actual_start; lap-1 times recompute. |
| Power loss mid-write | SQLite WAL rolls back the partial transaction only. |
| Same plate twice in under min-lap | Short-lap flag; card held for confirm / void / reassign. |
| System clock jumps (NTP) | UTC timestamps; warn on regression > 2 s; monotonic guard on seq. |

### 10 · Stack, theming & packaging

- **Async design:** the entry field never blocks — crossings commit through an async DB writer (sqlite3 on a single worker via `asyncio.to_thread` — one writer, WAL, no lock contention); the ride clock, 30 s session heartbeat, hourly backups and standings recompute run as background tasks, never inline in a handler; CSV import and HTML export run off-loop with progress callbacks. Core modules stay sync pure functions (hands, standings) — async lives at the store/UI boundary, so only there do tests need `pytest-asyncio`. **The wx⇄asyncio integration itself is chosen in EPIC 5**, where the async writer first appears and nothing earlier needs it (E1–E4 hold no database). `wxasync` is ruled out: 0.49 (2023) works functionally on this stack but cannot be torn down — one teardown path segfaults, another hangs, while a plain `wx.App` on the identical stack exits cleanly. Beyond CI, a segfault on quit would make every clean exit indistinguishable from a crash, which is exactly the signal `app_session.closed_at` and the resume dialog's wording rest on (R-52).

- **Stack:** Python 3.14 · wxPython 4.3.1 / wxWidgets 3.3.3 baseline — 4.3.0 shipped 2026-07-28 and 4.3.1 on 2026-07-30, both with cp314 wheels (all windows from XRC resources — §15b, less the three classes XRC cannot name, which are built in code; wxDataViewCtrl feed with card bitmaps from a 53-card imagelist; System/Light/Dark radios live on both platforms — 4.3.1 supplies `wx.App.SetAppearance`, so no capability check and no disabled radio; native controls never restyled) · sqlite3 stdlib · `phevaluator` for natural 5-card ranks + the thin wild layer above (unit-tested) · platformdirs for the db location.

- **Sound on crossing** (Settings toggle, default on): three bundled WAV cues so the operator never has to look down — ① *recorded*: one short click the instant a crossing commits · ② *rejected*: a low double-buzz for an unknown plate or empty Enter (the entry field also shakes/red-borders) · ③ *held*: a distinct two-tone alert when a short-lap crossing holds its card. Everything else is silent (undo and menu actions use the status bar). Played via `wx.adv.Sound` async — never blocks the entry field; identical files on both platforms, shipped in the installers. The player sits behind a `rivercrossing.ui.audio` interface so functional tests inject a fake and assert which cue fired (no audio hardware in CI).

- **Light/dark:** one token table (the two palettes in the UI doc) drives all custom-drawn panels; follow the OS via `wx.SystemSettings/SystemAppearance`, with a manual override in settings.

- **Installers:** PyInstaller builds → Windows: NSIS (.exe installer, per-user, unsigned — see §14; Phase 9 amendment: NSIS replaces Inno Setup, because makensis compiles natively on macOS and Inno's compiler does not) · macOS: .app in a .dmg via dmgbuild, Developer ID codesigned + notarized. CI matrix builds both per tag (§14). Bundles include the third-party license texts shown by About ▸ Licenses… — a redistribution obligation for the OSS dependencies.

### 11 · Modules — complete, testable libraries

Core modules are pure Python with **zero wx imports** — every one imports and tests headless. File-level repo layout, public APIs and build order: [Module Skeletons](module-skeletons.md) (binding companion). The UI layer is a thin shell that calls them.

| Module | Responsibility · key tests |
|---|---|
| **rivercrossing.cards** | Card model, seeded Shoe (Fisher-Yates, deal_index, reshuffle). Tests: deal determinism from seed, exhaustion/reshuffle, composition n×(52+j). |
| **rivercrossing.hands** | The card algorithm (§5): eval5 + wild layer + five-of-a-kind, rank compare. Tests: known-vector table for every hand class, wheel, joker cases, cap X; Hypothesis property tests — adding a card never lowers the rank, wild result ≥ any natural completion, agreement with a brute-force C(n,5)×52ʲ reference on thousands of random hands. |
| **rivercrossing.standings** | Ordering, tie-break rules ①②③, leaderboards. Tests: crafted tie fixtures, rule-reorder re-runs, DNF placement. |
| **rivercrossing.ride** | State machine, crossings, lap/total timing, min-lap flagging, undo. Tests: retro start-time fix, stop/continue, short-lap hold, compensating-write undo. |
| **rivercrossing.store** | SQLite persistence, migrations, audit log, backups. Tests: kill a subprocess mid-transaction and reopen — last committed crossing intact (WAL); replay audit → identical state. |
| **rivercrossing.csvio / .htmlexport / .pdfexport** | §7 / §8 / §8b. Tests: round-trip import→export, conflict report, exported HTML parsed back — JSON island equals computed standings; PDF opened with pypdf in tests — page count, standings text and options flags asserted, plus a golden-file byte comparison on a fixed seed. |
| **rivercrossing.ui** | wxPython windows only — no business logic. Tested functionally, below. |

### 12 · TDD & the testing system

- **Process:** tests are written first by the `tdd-python-writer` agent (red), implementation follows (green), then refactor — per module, in the dependency order above. pytest + pytest-asyncio + coverage; gate ≥ 90% on core modules; ruff + mypy strict as "good pythoning" guardrails.

- **Simulation suite (card algorithm):** seeded whole-ride simulations — e.g. 180 entries × 6 h with random lap distributions, solo-only and mixed, **both plate models** (relay and rider-pooled incl. mid-ride team moves), 0/2/4 jokers, with and without cap X, shoe exhaustion mid-ride. Invariants asserted: cards dealt = non-voided crossings − held; standings are a total order; identical hands resolve exactly by the configured rule; replaying the seed reproduces every deal and the final standings byte-for-byte.

- **Functional UI tests:** every entry box, button and list carries its stable snake_case **XRC name** (§15b); a pytest harness finds controls by name and drives them by **direct event injection** — `SetValue()` fires `EVT_TEXT`, a posted `wx.CommandEvent` fires `EVT_BUTTON`, both measured — so typing a plate, Enter, undo, dialogs and toggles are all validated against the visible list contents. Injection is the primary mechanism, not a fallback: from a terminal-launched interpreter `wx.UIActionSimulator` reports `True` and delivers nothing, because the process never becomes the OS-active app, and `Text(str)` raises `TypeError` on this build. Real input events are re-measured, never assumed, if a signed bundle ever makes the app frontmost.

- **Menu coverage test:** a parametrized test walks every menu item and asserts it routes to its §15 target (window opens, dialog opens, or command fires) in both entry modes and all three ride states.

- **Full race run-through (acceptance):** a scripted end-to-end test creates a ride with `min_lap_s` lowered (it is an ordinary ride setting, so a test race runs in seconds) — CSV import → start → hundreds of simulated crossings typed through the real entry field → accidental stop → continue → hard kill + relaunch against the same db → finish → **reopen → add a missed crossing at time + void one → finish again** → export HTML → parse and assert the corrected standings. Must pass 100% on Windows and macOS CI before a build ships.

### 13 · UI behavior standards

Grounded in platform dialog guidelines (Windows ContentDialog, Carbon/Primer accessibility patterns). These rules are testable and the functional harness asserts them per dialog.

- **Dialogs:** Esc always cancels (= Cancel, never a destructive path); Enter activates the marked default button. Initial focus: confirmation dialogs → the primary button; *destructive* confirms (void, delete, start-over) → **Cancel**, so a reflex Enter is safe; form dialogs (add entry, edit crossing, set start time) → the first input field. Tab is trapped inside the dialog; on close, focus returns to the control that opened it.

- **Every control has a stable name** (its XRC `name` attribute, §15b) — the same ids serve the functional tests and screen-reader labels.

- **Console focus:** the plate field owns focus; recording keeps focus there; Space always returns it; opening any window never steals it mid-keystroke (input buffered).

- **Radio groups always have a default** — no unselected groups anywhere (defaults: solo-only · plate model rider-pooled · one card per lap · 2 jokers · tie-break ① laps · theme System · Add entry → Solo · manual card → from shoe).

- **Console states:** DRAFT — clock shows planned start, entry field disabled with "Start the ride to record crossings", primary action is Start Ride (designed: 8a); RUNNING — as designed; FINISHED — result banner with Reopen + Results + export buttons, feed read-only, entry disabled (designed: 8b); REOPENED — corrections banner, edits highlighted in the feed, single primary "Finish again" (designed: 8c). Empty feeds show one-line hints, never blank panels.

- **Windows:** single instance per database; console minimum 1100×700 (fits 1366×768 — the canvas figure, which is implementation truth; declared as `<size>` and re-applied with `SetMinSize()`, since XRC has no window-level minsize, §15b); dialogs resizable per R-05 — R-04's 90–150% text zoom cannot reflow inside a fixed dialog; per-monitor DPI aware (v2) on Windows; View zoom scales type 90–150%.

- **Times:** stored UTC, displayed local 24-hour; elapsed as h:mm:ss. Settings offers **"Hide times on the console"** — toggleable while a ride runs: lap/total columns and per-crossing times disappear from the console (it's-not-a-race mode); the ride clock stays (the operator must know when the window closes), and timestamps are still recorded underneath for corrections and optional publication.

- **Buttons:** one primary per surface; destructive actions never the default; three-button dialogs order Ghost · Secondary · Primary left→right.

### 14 · CI — building & testing a GUI app

GitHub Actions, matrix `windows-latest + macos-latest` — both runners have a real desktop session, so wx windows open without a virtual display (no Xvfb; that's only needed if a Linux target is ever added). GUI suites are kept lean by design: logic lives in the sync core modules (§11), so the bulk of the pyramid runs headless-fast.

**Both platforms gate.** The temporary macOS-only deviation (recorded here and in R-75 from EPIC 1 until Phase 10) is reversed: Windows testers are now available, every previously known Windows failure was root-caused and fixed in EPIC 1 Phase 10 (the modal-harness sentinel clobber, MSW default-item focus, the scenario close prompt, Fit()-derived sizing, mypy output color), and the windows-latest legs run the same blocking stages as macOS with the same screenshot/diagnostic artifacts.

| Stage (both OSes) | What runs · gate |
|---|---|
| **1 · Static** | ruff + mypy strict — fail fast, no GUI needed. |
| **2 · Unit + property** | pytest on cards/hands/standings/ride/store/exports incl. Hypothesis + simulation suite; coverage ≥ 90% gate; pytest-asyncio at the store boundary. |
| **3 · Functional UI** | The §12 harness drives real wx windows on the runner's desktop. Flake control: event-driven waits (never bare sleeps), one auto-retry per test, and a full-screen PNG captured on failure and uploaded as an artifact. |
| **4 · Acceptance** | The full race run-through (§12) — CSV in, hundreds of typed crossings, stop/continue, kill + relaunch, quit + relaunch, finish, HTML + PDF exports parsed and asserted. Must pass 100%. |
| **5 · Build** | PyInstaller bundles (incl. WAVs, fonts, user guide, license texts) → **smoke test: launch the built binary**, open the sample db, record one crossing via the harness, quit clean. |
| **6 · Package (tags)** | Windows: NSIS .exe, **unsigned** (Phase 9: NSIS replaces Inno Setup; no Authenticode cert available — the guide documents the SmartScreen "More info → Run anyway" step; the runner installs NSIS via `choco install nsis`) · macOS: dmgbuild → Developer ID codesign + notarytool staple (Apple developer account available). Installers uploaded as release artifacts with checksums. Phase 11 pulled the **unsigned** half of this stage forward: a version tag publishes the GitHub release with both unsigned installers and checksums; signing (Authenticode, Developer ID + notarytool) remains here for EPIC 9. |

Every PR runs stages 1–4; main additionally runs 5; version tags run every wired stage and **publish** the release (Phase 11 amendment: "draft" became "publish" — a draft is invisible to anyone without repo access, which defeats the tester-download purpose; stage 4 joins the tag run when EPIC 9 lands it, and a tag whose name mismatches `rivercrossing.__version__` fails the release job instead of publishing wrong artifacts). Stage 5 runs in **dev-bundle mode from EPIC 1 (D1) onward** — PyInstaller onedir, unsigned, uploaded as runnable Windows + macOS artifacts on every main build — and graduates to full installers in EPIC 9. Since EPIC 1 Phase 8 the dev bundle carries the real app icon and the macOS leg additionally builds and smoke-tests an **unsigned** drag-to-Applications `.dmg` (dmgbuild, Applications symlink, background, volume icon); Developer ID signing and notarization remain stage 6 / E9.1.3. Since EPIC 1 Phase 9 the Windows leg additionally builds the Windows dev bundle and the **unsigned** per-user NSIS installer (`installers/windows.nsi`, compiled by `nox -s winsetup`), drives E9.1.2's silent install/launch/uninstall tests, and uploads both artifacts; Authenticode signing remains stage 6 / E9.1.2. A nightly job replays the acceptance race with fresh random seeds and files the seed on failure so any hand-evaluation edge case is reproducible.

### 15 · Menu → screen & dialog map

One menu tree on both platforms (wx relocates About / Settings / Quit into the macOS app menu; Ctrl ⇒ ⌘). Every item routes through the same command layer as its on-screen button. "OS-native" = standard file/save picker, deliberately not custom. *Design* ids are mock ids in [ui-designs-retired.md](ui-designs-retired.md) (retired — flow reference only).

**Three routes have no frozen window.** Duplicate Ride…, Reopen Ride and Void Card… all cite the "3d pattern" — a confirm dialog that exists only in the retired hi-fi designs, so no window in the XRC canvas covers them. The gap is owned by **EPIC 5** (Duplicate Ride…, Reopen Ride) and **EPIC 7** (Void Card…), each authored mock-first with its control names registered in §15b before any UI code. EPIC 1 routes all three to a flagged sentinel the menu-coverage test asserts as such — never an invented window name.

| Menu item | Opens / does | Design | Enabled when |
|---|---|---|---|
| **File ▸ New Ride…** | Ride setup window (blank) | 1c | always |
| **File ▸ Ride Library** | Ride library window | 1g | always |
| **File ▸ Duplicate Ride…** | Duplicate-ride dialog → new DRAFT ride — no frozen window; E5 authors it, E1 shows the sentinel | 3d | a ride is open |
| **File ▸ Import Riders CSV…** | OS-native picker → import report dialog | 3e | ride open (structure edits: DRAFT only) |
| **File ▸ Export Riders CSV…** | OS-native save dialog | — | ride open |
| **File ▸ Back Up Database…** | OS-native save dialog | — | always |
| **File ▸ Settings…** | Settings window | 3a | always |
| **File ▸ Exit** | Ride RUNNING → exit dialog (Cancel · Finish first · Quit-keep-running); otherwise → quit confirm (`exit_confirm_dlg`) — the app never exits without confirmation. Reopen → resume dialog | 4a · 1h | always |
| **Ride ▸ Start Ride** | Starts; with existing data → Continue-ride dialog | 1h | DRAFT, or stopped RUNNING |
| **Ride ▸ Stop Ride…** | Stop confirm dialog (console button additionally requires the Arm checkbox) | 3d | RUNNING |
| **Ride ▸ Set Start Time…** | Set-start-time dialog (lap-1 recompute) | 3d | RUNNING · REOPENED |
| **Ride ▸ Finish Ride…** | Finish confirm → Results window ("Finish again" from REOPENED) | 3d → 1f | RUNNING · REOPENED |
| **Ride ▸ Reopen Ride** | Confirm (3d pattern) → RUNNING — no frozen window; E5 authors it, E1 shows the sentinel | 3d | FINISHED |
| **Ride ▸ Audit Trail…** | Read-only audit viewer (§3) — when · who · action · entry · reason, filterable | — | ride open, ≥1 audit row |
| **Ride ▸ Ride Setup…** | Ride setup window (this ride) | 1c | ride open (locks tighten after start) |
| **Riders ▸ Rider Editor** | Rider editor window | 1d / 2b | ride open |
| **Riders ▸ Add Rider/Entry…** | Rider editor in new-entry mode — plate field focused | 3b | ride open (new plates any time) |
| **Riders ▸ Mark DNF…** | DNF confirm dialog | 3b | RUNNING · REOPENED |
| **Riders ▸ Entry Detail…** | Entry detail window | 1e | ride open |
| **Cards ▸ Undo Last Crossing** | Direct command + status-bar notice (no dialog) | 1a | RUNNING, ≥1 crossing |
| **Cards ▸ Add Crossing at Time…** | Edit-crossing dialog in "add" mode — plate + explicit time (a rider missed at the line) | 3c · 8c | RUNNING · REOPENED |
| **Cards ▸ Edit Crossing…** | Edit-crossing dialog | 3c | RUNNING · REOPENED, ≥1 crossing |
| **Cards ▸ Reassign Plate…** | Reassign dialog | 3c | RUNNING · REOPENED, ≥1 crossing |
| **Cards ▸ Deal Manual Card…** | Manual-deal dialog | 3c | RUNNING · REOPENED |
| **Cards ▸ Void Card…** | From entry detail, cards row → confirm (3d pattern) — no frozen window for the confirm; E7 authors it, E1 shows the sentinel | 1e | RUNNING · REOPENED, entry has cards |
| **Cards ▸ Review Held Cards** | Focuses console review panel / short-lap dialog | 1a · 1h | held cards > 0 (shows count) |
| **Results ▸ Standings** | Results window | 1f | ride open (live while running) |
| **Results ▸ Generate HTML…** | OS-native save dialog → writes file (§8) | 1f | FINISHED |
| **Results ▸ Export PDF…** | OS-native save dialog → writes file (§8b) | 1f | FINISHED |
| **Results ▸ Podium Poster PDF…** | OS-native save dialog → one-page poster (§8b) | 5d | FINISHED |
| **Results ▸ Export Standings CSV…** | OS-native save dialog → standings rows (place, plate, entry, laps, hand[, times]) | 1f | FINISHED |
| **Results ▸ Preview in Browser** | Opens last export in default browser | — | an export exists |
| **Results ▸ Tie-break Order…** | Focuses the tie-break control in Results | 1f | ride open |
| **View ▸ Theme · Hide Times · Zoom** | Direct commands — theme radio trio, hide-times check item, zoom radios — mirrored in Settings | 3a | always |
| **Help ▸ User Guide** | Opens bundled docs/user-guide.html in browser — 10 chapters + 2 appendices, section anchors deep-linked from dialog Help buttons; screenshots regenerated by the functional-test harness each release | 6a | always |
| **Help ▸ Keyboard Shortcuts** | Shortcuts dialog | 3f | always |
| **Help ▸ Run Evaluator Self-test** | Self-test result dialog (§12) | 3f | always |
| **Help ▸ About RiverCrossing** | About box | 3f | always |

### 15b · XRC appendix — windows, files & naming (canonical)

Every window and dialog is defined in **wxWidgets XRC** (sizer-based, resizable, native controls, no absolute positioning, no restyling). The native window designs live in [XRC Windows](xrc-windows.md) — control names annotated there are the XRC `name` attributes and are **frozen**: tests find widgets by them (wxWindow.FindWindowByName), so a rename is a breaking change. The Industry-styled hi-fi mockups are retired as implementation reference. Baseline: **wxPython 4.3.1 / wxWidgets 3.3.3** — cp314 wheels, and the release that supplies wxApp.SetAppearance, so the dark theme needs no capability check (XRC colour syntax available but unused — native colours only). Measured on this baseline: macOS applies appearance changes at runtime to existing windows, while MSW returns `CannotChange` once any top-level window exists — a Windows theme change therefore takes effect at next launch, and the app says so in the status bar.

- **Naming:** snake_case; suffix by role — `_frame _dlg _panel _lbl _input _btn _chk _radio _choice _list _picker _spin _infobar`; menu items `mi_<action>`; standard buttons use stock IDs (wxID_OK, wxID_CANCEL, wxID_CLOSE, wxID_DELETE, wxID_OPEN, wxID_NEW, wxID_EXIT, wxID_ABOUT). Names unique within their top-level window — so a name may repeat across windows, and several do: `plate_input`, `reason_input`, `continue_btn`, `message_lbl`. `ui/ids.py` mirrors these names 1:1 (generated from the .xrc files in CI — drift fails the build): **173 constants** — 24 windows, 45 `mi_*` menu items, 104 controls (Phase 8 added `exit_confirm_dlg` and `record_btn`). The generator is the authoritative count; the drift gate keeps it honest.

- **Two names added past the canvas, in four windows:** `finish_first_btn` in `exit_running_dlg` — the third button §15 and R-51 both describe, which the canvas drew with two (missing functionality, not styling) · `message_lbl` in `delete_ride_dlg`, `continue_or_new_dlg` and `resume_dlg` — each interpolates live ride data into a line the canvas left unnamed, so a presenter had nothing to write to and the line rendered blank; in the delete confirm that also breached UX-DESKTOP §4's "name the object in a destructive confirmation". One shared name, legal because uniqueness is required only within a window.

- **Files (src/rivercrossing/ui/xrc/):** main.xrc → main_frame + main_menubar · setup.xrc → ride_setup_dlg · riders.xrc → rider_editor_dlg + csv_preview_dlg · detail.xrc → entry_detail_dlg · results.xrc → results_frame · library.xrc → ride_library_dlg + delete_ride_dlg · audit.xrc → audit_dlg · settings.xrc → settings_dlg · dialogs.xrc → set_start_dlg, stop_confirm_dlg, finish_confirm_dlg, continue_or_new_dlg, resume_dlg, exit_running_dlg, exit_confirm_dlg, edit_crossing_dlg, reassign_dlg, manual_deal_dlg, dnf_confirm_dlg, about_dlg, selftest_dlg, shortcuts_dlg.

- **Menu item names** (map 1:1 to §15 rows): File — mi_new_ride, mi_open_library, mi_duplicate_ride, mi_backup_now, mi_import_csv, mi_export_csv, wxID_PREFERENCES (Settings…), wxID_EXIT · Ride — mi_start_ride, mi_stop_ride, mi_set_start_time, mi_finish_ride, mi_reopen_ride, mi_audit_trail, mi_ride_setup · Riders — mi_rider_editor, mi_add_entry, mi_mark_dnf, mi_entry_detail · Cards — mi_undo_crossing, mi_add_crossing_at, mi_edit_crossing, mi_reassign_plate, mi_deal_manual, mi_void_card, mi_review_held · Results — mi_standings (F5), mi_export_html, mi_export_pdf, mi_export_poster, mi_export_results_csv, mi_preview_browser, mi_tiebreak_order · View — mi_theme_system / mi_theme_light / mi_theme_dark (radio items), mi_hide_times (check item), mi_zoom_90…mi_zoom_150 (radio items) · Help — mi_user_guide, mi_shortcuts, mi_selftest, wxID_ABOUT.

- **Declared in XRC vs code:** XRC owns structure, labels, sizers, styles (wxRB_GROUP radio groups inside wxStaticBoxSizer, wxStdDialogButtonSizer button rows, wxSplitterWindow, wxDataViewCtrl shells). Code owns: DataView columns/rows/attributes, card imagelist (53 bitmaps @1x/2x from ui/assets/cards/), InfoBar construction + messages + show/hide, splitter sash restore, window minimum sizes, radio menu-item defaults, per-state menu enabling, SetAppearance. Custom-drawn widgets: none.

- **Three classes cannot be authored in XRC at all** (measured on 4.3.1 / wxWidgets 3.3.3): **wxInfoBar** — the handler yields a generic `wx.Control`, not a `wx.InfoBar`, and silently drops `name`, so the four info bars (`resume_infobar`, `reopened_infobar`, `finished_infobar`, `stale_infobar`) are built with `wx.InfoBar()` in code and given their frozen names with `SetName()` · **wxDataViewListCtrl** — its handler hard-forces the control name to `dataviewCtrl`, so no frozen name resolves; all ten list controls are therefore authored as **wxDataViewCtrl**, whose name *is* honoured, and per-row attributes come from a `DataViewIndexListModel` subclass overriding `GetAttrByRow` (there is no setter — `SetAttrByRow` and `SetItemAttr` do not exist) · **wxMenuBar** — also drops its name: `main_menubar` is the resource id `XmlResource.LoadMenuBar()` loads by, and it never resolves through `FindWindowByName`.

- **Further code-side items, all measured:** `<checked>` is a no-op on **radio** menu items (the handler applies it only to `wxITEM_CHECK`), so the theme and zoom defaults — `mi_theme_system`, `mi_zoom_100` — are checked in code after `LoadMenuBar`; `<value>` on a `wxRadioButton` does work, so the in-window radios keep their XRC defaults · XRC has **no window-level `minsize`** (only sizeritem and wxSplitterWindow), so window minimums are declared as `<size>` and re-applied with `SetMinSize()` · `wxStdDialogButtonSizer` positions only OK/Yes/Save/Apply/No/Cancel/Close/Help — `wxID_OPEN`, `wxID_NEW`, `wxID_DELETE` and every custom button (`finish_first_btn`, `continue_btn`, `archive_new_btn`, `library_btn`, `void_btn`, `rerun_btn`) are created and resolve by name but are left unpositioned at `(-1,-1)`, so they belong in a sibling `wxBoxSizer` · a bare `&` in a label is a mnemonic and is stripped on macOS — author `&&`, and read labels back with `GetLabelText()` · one control cannot carry both a frozen name and a stock id, since the id comes *from* the name, so `continue_btn` (annotated wxID_OK in `continue_or_new_dlg` and `resume_dlg`) keeps its name and the affirmative behaviour is wired in code with `SetAffirmativeId()`.

Companions: [requirements](requirements.md) · [XRC window designs (implementation truth)](xrc-windows.md) · [module skeletons](module-skeletons.md) · [project plan](project-plan.md) · [task briefs](task-briefs.md) · [hi-fi designs (retired — flow reference)](ui-designs-retired.md) · [results sample](../exports/epic-2026-results.html) / [no-times](../exports/epic-2026-results-no-times.html). Event facts from gorba.ca; hand-evaluation approach per standard evaluators (7,462-rank tables, best-5-of-N).
