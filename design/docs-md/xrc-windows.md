# RiverCrossing — XRC Window Designs

RiverCrossing — XRC window designs (implementation truth · retires the Industry hi-fi mockups)

Native wxWidgets controls only, sizer-based, Windows look shown (identical structure on macOS). Every interactive control carries its `xrc_name` annotation — these are the XRC `name` attributes, canonical and frozen; standard buttons use stock IDs (wxID_OK, wxID_CANCEL, wxID_CLOSE, wxID_DELETE, wxID_EXIT, wxID_ABOUT). Naming rules + window↔file map: Spec §15b.

- **Canvas caveats (HTML approximation):** browser form controls stand in for wx natives; exact spacing/fonts come from sizers + system fonts, not these pixels; sizes below are minimums expressed in dialog units at build time. **Global code-side items (not expressible in XRC):** ① DataView columns + row data + per-row attributes (bold flagged rows, red suits) — appended in code, the attributes through a `DataViewIndexListModel` subclass overriding `GetAttrByRow`, since no setter exists; ② card imagelist population (53 card bitmaps @1x/2x); ③ wxInfoBar **construction** + message text + Show/Hide calls, and the console's two custom gauges — the `RaceClock` dials (`elapsed_clock`/`remaining_clock`) and the `StopLight` (`ride_status_light`) — built code-side into main.xrc's `elapsed_clock_panel`/`remaining_clock_panel`/`ride_status_panel` placeholder slots and named with `SetName()`, because XRC cannot author a `wx.Control` subclass (ux-polish); ④ splitter sash position restore from settings; ⑤ menu enable/disable per ride state (§15); ⑥ theme: `wx.App.SetAppearance` on the 4.3.1 / wxWidgets 3.3.3 baseline — all three appearance radios live on both platforms (measured: macOS applies at runtime to existing windows; `Appearance::System` pins the NSAppearance current at the call instead of restoring follow-the-system, so the app re-applies System on `wx.EVT_SYS_COLOUR_CHANGED`, best-effort; MSW on 3.3.3 returns `CannotChange` once a top-level window exists, so a Windows theme change takes effect at next launch and the status bar says so); ⑦ window minimum sizes via `SetMinSize()` (XRC has no window-level minsize); ⑧ the zoom menu-item default `mi_zoom_100` (`<checked>` is a no-op on radio items; W13 removed the View-menu theme trio — the Settings appearance radios are the single theme surface, so the old `mi_theme_system` default went with them). Everything else drawn here is declared in XRC.

- **Three classes cannot be authored in XRC** (measured on 4.3.1 / wxWidgets 3.3.3 — full detail in Spec §15b): **wxInfoBar** yields a generic `wx.Control` and drops its `name`, so all eight code-side info bars (`resume_infobar`/`reopened_infobar`/`finished_infobar` in main_frame, `stale_infobar` in results_frame, `setup_infobar` in ride_setup_dlg, `roster_infobar`/`csv_infobar` in the rider editor, `teams_infobar` in the Teams Editor — §15b) are built in code and named with `SetName()` · **wxDataViewListCtrl**'s handler hard-forces the name `dataviewCtrl`, so every list control below is a **wxDataViewCtrl**, whose name is honoured · **wxMenuBar** drops its name too: `main_menubar` loads via `XmlResource.LoadMenuBar()` and never resolves through `FindWindowByName`. Custom `wx.Control` subclasses are equally un-authorable in XRC: the console's `RaceClock` dials and `StopLight` (views/gauges.py) are built into main.xrc's placeholder panels and named with `SetName()` — the InfoBar rule extended (ux-polish). Also measured: `wxStdDialogButtonSizer` positions only OK/Yes/Save/Apply/No/Cancel/Close/Help, so `wxID_OPEN`, `wxID_NEW`, `wxID_DELETE` and the custom buttons annotated below live in a sibling `wxBoxSizer`; a bare `&` in a label is a mnemonic and is stripped on macOS — author `&&` and read labels with `GetLabelText()`.

A · Main frame

RiverCrossing — GORBA EPIC & MTB Festival 2026`main_frame`— ▢ ✕

FileRideRidersCardsResultsViewHelp`main_menubar (the resource id LoadMenuBar() loads by — wxMenuBar drops its name, so it never resolves through FindWindowByName) · items mi_* — see §15b`

ⓘ This ride was running when the app closed. Continue timing on wall clock?Continue rideOpen library`resume_infobar · reopened_infobar (wxInfoBar — built in code and named with SetName(); XRC cannot author one; hidden by default)`

GORBA EPIC & MTB Festival 2026 `ride_name_lbl`

◉ ● ◉ — the lit lamp rides the state `ride_status_light (StopLight · three stacked circles — green RUNNING · amber DRAFT/REOPENED · red FINISHED · built code-side into ride_status_panel and named with SetName(); colour is never the sole channel — the status label spells the state)`
RUNNING `ride_status_lbl`

Elapsed — (dial) — 4:22:41      Remaining — (dial) — 1:37:19

`elapsed_clock_panel · remaining_clock_panel (XRC placeholder panels — ux-polish): each clock column reads caption ("Elapsed"/"Remaining"), then the RaceClock dial (elapsed_clock/remaining_clock — code-side SetName(), views/gauges.py), then the numeric readout (clock_elapsed_lbl/clock_remaining_lbl). The elapsed dial fills toward 1.0 and the remaining dial drains toward 0.0 as the ride runs — both fractions over the planned duration, clamped, zeroed in DRAFT`

Start ride   Arm   Stop ride…

`start_btn · stop_btn — round green GO / red STOP wxBitmapButtons (ux-polish): the SVG glyphs (gauges.py go_bundle/stop_bundle) are applied code-side, and the labels are re-applied there too ("Start ride"/"Stop ride…"), because the XRC bitmap-button handler ignores <label> — the buttons stay text-named for assistive tech · arm_stop_chk ("Arm", R-35)`

Record crossing `(the entry row is framed by a native wxStaticBoxSizer so the operator can always find it — Phase 8)`

Plate`plate_input (focused · larger type via <font><sysfont>wxSYS_DEFAULT_GUI_FONT</sysfont><relativesize>1.5</relativesize></font> — relative so the 90–150% zoom still applies, never an absolute point size · wider DIP <size> · <hint> "Plate number")`
Record (Enter)`record_btn`
✓ 123 · Sam Ellis · Lap 4 · 22:41 · dealt 9♥`last_crossing_lbl`
Undo last (Ctrl+Z)`undo_btn`

| Time | Plate | Entry | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 14:22:41 | 123 | Sam Ellis | 4 | 22:41 | 1:31:04 | 9♥ |
| 14:22:18 | 77 | Trail Blazers (T) | 9 | 19:55 | 3:02:11 | K♠ |
| 14:21:59 | 45 | J. Okafor | 6 | 07:12 ⚑ | 2:44:30 | held |
| 14:21:30 | 212 | M. Chen | 5 | 24:02 | 2:10:44 | JK★ |
| 14:20:52 | 8 | R. Dubois | 7 | 21:17 | 2:58:03 | 4♦ |

`crossings_list (wxDataViewCtrl · newest first · last 30)`

Crossings
1 124

Cards dealt
1 092

On course
42

Shoe
41/108

`crossings_count_lbl · cards_count_lbl · on_course_lbl · shoe_lbl`
Needs Review   Riders — `review_notebook (wxNotebook · two tabs — ux-polish; the notebookpage node's own name is dropped by the XRC handler, so the names live on the page children below)`

Plate | Lap | Lap time   `flagged_list (wxDataViewCtrl — the R-34 flag rows, three sortable columns)`
Review… `review_btn — and the Cards ▸ Review Held Cards route both land on this page (SetSelection(0) + focus)`

Plate | Name | Team   `console_riders_list (wxDataViewCtrl — the live roster; a solo rider's Team cell is "—"; rows refreshed on the 1 s tick) — activating a rider row (double-click or Enter) opens rider_editor_dlg pre-selected at that rider's plate (the app wires the view's set_on_open_rider seam)`

epic-2026.prdb

Saved 14:22:41

Shoe cycle 1 · seed 8843

`main_statusbar`

⚠ code-side: feed columns/rows + flagged-row attrs (a DataViewIndexListModel subclass overriding GetAttrByRow — there is no setter); card column bitmaps from imagelist; InfoBar construction + text + show/hide; sash position (main_splitter); state variants — DRAFT: clock 0:00:00, start_btn enabled, plate_input disabled with "start the ride to record" hint (record_btn tracks plate_input's enablement in every state) · FINISHED: entry row hidden, result banner InfoBar (finished_infobar) with Reopen/Results buttons — W11: the two buttons are wx.InfoBar AddButton children, named code-side with SetName (`finished_reopen_btn`/`finished_results_btn`; the InfoBar rule — they never appear in ui/ids.py). The banner message is "Ride finished — results are ready." Reopen runs the mi_reopen_ride confirm flow; View results opens the results frame · REOPENED: corrections banner, entry disabled, edited rows highlighted. ux-polish header: the two `RaceClock` dials (`elapsed_clock`/`remaining_clock`) are inserted code-side between each clock column's caption and numeric readout in `elapsed_clock_panel`/`remaining_clock_panel` (fractions over planned duration — elapsed fills toward 1.0, remaining drains toward 0.0, both 0.0 in DRAFT), the `StopLight` (`ride_status_light`) sits above `ride_status_lbl` inside `ride_status_panel` — green RUNNING · amber DRAFT/REOPENED · red FINISHED, `console.stop_light_mode`, and colour is never the sole channel — and `start_btn`/`stop_btn` are `wxBitmapButton`s carrying gauges.py's round green GO / red STOP SVG glyphs with "Start ride"/"Stop ride…" re-applied in code (the XRC handler ignores bitmap-button labels). Review sidebar: a two-tab `review_notebook` wxNotebook — "Needs Review" (index 0: `flagged_list` Plate | Lap | Lap time + `review_btn`) and "Riders" (`console_riders_list` Plate | Name | Team over the live roster, refreshed on the 1 s tick) — and the rider-row activation seam (double-click or Enter) opens `rider_editor_dlg` pre-selected at that rider's plate. Hide-times setting removes Lap time/Total columns + times in last_crossing_lbl; clock stays; clock_elapsed_lbl/clock_remaining_lbl reserve a fixed minimum width and re-layout on update so long elapsed/remaining text never overlaps the Start/Stop controls (R-55). Min frame 1100×700, fits 1366×768 — declared as <size> and re-applied with SetMinSize(); Spec §13 now states the same figure.

B · Ride setup & lifecycle dialogs

Ride Setup`ride_setup_dlg`✕

Name
DatePlanned start
VenueLap length km
OrganizerScorer
Duration h:mMin lap m:s
Logo

`name_input · date_picker · start_time_picker · venue_input · lap_km_spin · organizer_input · scorer_input · duration_input · min_lap_input · logo_picker`
Entries
 Solo riders only
 Solo + teams (default)

Max riders per team (2–10)

 Rider plates — pooled (default): each rider draws per lap, uncapped; team hand from pooled cards
 Team plate — relay: one plate per team, one rider on course (EPIC)

`solo_radio · mixed_radio — teams is the default: mixed_radio carries the XRC <value>1</value> (ux-polish), so "Solo + teams (default)" is what a fresh setup shows; solo-only is the opt-out · team_size_spin · pooled_radio · relay_radio (enabled only when mixed_radio)`

Cards

Decks Jokers/deck:
 0 2 4
 Card cap

Tie-break order ① Most laps ② Total time ③ High-card draw ▲▼

`decks_spin · jokers_0_radio · jokers_2_radio (default) · jokers_4_radio · cap_chk · cap_spin · tiebreak_list (wxEditableListBox reorder arrows)`

OKCancel

`wxID_OK · wxID_CANCEL (wxStdDialogButtonSizer)`

⚠ code-side: entry/plate-model group locks after start (relay) or stays editable (pooled, R-17); tiebreak_list reorder persisted (its ①②③ numbering here is illustration, not row text); decks_spin's value — the XRC declares none and the presenter supplies **8** (Spec §4; settled by the E3.5 ride-setup work — the canvas's 2 was a mock artifact). ux-polish: the minimum-setup rule — OK (and a DRAFT ride's Start, ride.py's own start gate) refuses a config with a blank name/venue/organizer/scorer or a non-positive lap length (`ride.setup_minimum_violations`), joining every reason into one `setup_infobar` refusal (the code-side wxInfoBar, §15b) and leaving the dialog open; the ride's date, planned start and logo are not part of the minimum rule — a logo is fully optional and the date/time pickers always hold a value, while duration/min-lap keep their own parse-and-positivity validation. All fields plain XRC.

Set Start Time`set_start_dlg`✕

Started at

`start_date_picker · start_time_picker`

Lap-1 times recompute from this moment.

OKCancel

Stop Ride?`stop_confirm_dlg`✕

The clock stops for everyone. Riders still on course keep their laps; no cards are dealt after stop. You can continue the ride later without losing anything.

Stop rideCancel

`wxID_OK "Stop ride" · wxID_CANCEL (default + focused)`

Finish Ride?`finish_confirm_dlg`✕

Locks entry and computes final standings (evaluator self-test must be green). You can reopen later for corrections.

Finish rideCancel

`wxID_OK "Finish ride" · wxID_CANCEL (default)`

Duplicate Ride`duplicate_ride_dlg`✕

Duplicate **"GORBA EPIC 2026"** as a new DRAFT ride?

Copies the ride's setup and full rider list — no timing data.

CancelDuplicate

`message_lbl (the ride's name is interpolated — UX-DESKTOP §4) · wxID_OK "Duplicate" (default + focused — non-destructive, E5.4.1) · wxID_CANCEL`

Reopen Ride`reopen_ride_dlg`✕

Reopen **"GORBA EPIC 2026"** for corrections?

Reopen for corrections? Standings recompute on export.

CancelReopen

`message_lbl (the ride's name is interpolated — UX-DESKTOP §4) · wxID_OK "Reopen" (default + focused — non-destructive, E5.4.1) · wxID_CANCEL`

Resume Ride`resume_dlg`✕

"GORBA EPIC 2026" is still running — it kept timing on the wall clock. (Wording swaps for crash: "The app closed unexpectedly…" — session_state)

Continue rideOpen library

`message_lbl (ride name + quit/crash wording interpolated) · continue_btn (wxID_OK, default) · library_btn`

Ride Is Running`exit_running_dlg`✕

Quitting won't stop the ride — it keeps timing on the wall clock, and you'll be asked to continue when you reopen.

CancelFinish ride first…Quit — keep ride running

`wxID_CANCEL (default + focused) · finish_first_btn · wxID_OK "Quit — keep ride running"`

⚠ three buttons per Spec §15 and R-51, ordered per §13 Ghost · Secondary · Primary; the canvas drew two, and the requirements are the acceptance authority — missing functionality, not styling. finish_first_btn sits in a sibling wxBoxSizer because wxStdDialogButtonSizer positions only the stock ids it recognises.

Quit RiverCrossing?`exit_confirm_dlg`✕

Are you sure you want to quit? No ride is running.

CancelQuit

`wxID_CANCEL (default + focused) · wxID_OK "Quit"`

⚠ added in EPIC 1 Phase 8: §15 and R-51 originally said "otherwise quits"; amended so the app never exits without confirmation (destructive-confirm pattern per §13/R-76 — Cancel default + focused). Stock IDs only; the message line is static and carries no name. On macOS the window ✕ hides the app (Dock click reopens it), so only ⌘Q / app-menu Quit / File ▸ Exit reach a quit dialog there; on Windows ✕ runs the same confirm flow.

No Ride Open`no_ride_dlg`✕

No ride is open. `message_lbl (the "no ride open" state line)`

Open library…Create new ride…

`open_library_btn ("Open library…" — secondary; Escape's target, wired with dialogs.wire_escape_to, the resume dialog's own non-committal mapping) · create_ride_btn ("Create new ride…" — primary; the app bootstrap calls SetAffirmativeId so Enter creates)`

⚠ ux-polish: the no-ride prompt — shown by the app bootstrap after the console is wired whenever a store-backed launch resumed no ride and deferred no other window (resume_dlg's Open-library outcome sets library_deferred so the prompt never double-asks the same Create/Open choice). "Create new ride…" opens the ride-setup flow, "Open library…" the ride library; both chain through wx.CallAfter (the resume flow's modal-chaining rule). It replaced the retired `continue_or_new_dlg` — starting a stopped ride with data now continues directly, and "archive and start fresh" is gone (a fresh ride is File ▸ New Ride…). Two custom buttons, so no wxStdDialogButtonSizer at all — resume_dlg's own shape. The window lives in dialogs.xrc (§15b).

C · Riders, corrections & cards

Rider Editor`rider_editor_dlg`✕

| Plate | Name | Team |
|---|---|---|
| 123 | Sam Ellis | — |
| 77 | A. Roy | Trail Blazers |
| 78 | K. Singh | Trail Blazers |
| 212 | M. Chen | — |

`riders_list (wxDataViewCtrl · Team col hidden in solo-only)`

Rider

Plate
First name
Last name
Team— solo —Trail Blazers

AddSaveDelete

`plate_input (next free) · first_name_input · last_name_input · team_choice — "— solo —" first, then every existing team's display name ("New team…" is gone — teams are created only in the Teams Editor) · add_btn · save_btn · delete_btn`

Import CSV…Export CSV…

`import_btn · export_btn · wxID_CLOSE`
Close

⚠ code-side: list rows — the riders_list Name column renders the rider's full name (first + last, R-23); a solo entry's display name mirrors its rider's full name; team_choice content ("— solo —" first, then every existing team's display name — the retired "New team…" sentinel is gone (ux-polish): teams are created only in the Teams Editor, never prompted here); plate_input prefills the highest numeric plate + 1 (empty roster → 1); delete disabled once entry has data (post-start = DNF/void only, R-15); refused edits show on roster_infobar (wxInfoBar, code-side SetName, §15b); import_btn/export_btn run the same picker → preview/write flows as File ▸ Import/Export Riders CSV. Teams editable until start (relay) / during ride (pooled).

Import Riders — Preview`csv_preview_dlg`✕

riders.csv → **178 riders · 12 teams · 3 conflicts** `summary_lbl`

| Row | Problem |
|---|---|
| 41 | Duplicate plate 77 |
| 96 | Missing name |

`conflicts_list (wxDataViewCtrl)`

Nothing is written until you import. Re-import freely reshapes teams before start.

ImportCancel

`wxID_OK "Import" (disabled while conflicts > 0) · wxID_CANCEL`

⚠ code-side: summary_lbl text + conflicts_list rows; the wxID_OK gate; a refused import shows on csv_infobar (wxInfoBar, code-side SetName, §15b). Opened from File ▸ Import Riders CSV… or the editor's import_btn, after the OS-native picker.

Teams Editor`team_editor_dlg`✕

| Team | Riders | Logo |
|---|---|---|
| Trail Blazers | 3 | Card |
| Moss Ridge Riders | 2 | Image |

`teams_list (wxDataViewCtrl · Team | Riders | Logo — the Riders cell is the team's rider count; the Logo cell shows the logo's kind — "Card" for a card code, "Image" for a PNG (an image wins) — never the card glyph itself, ux-polish rework)`

 Only show one-rider teams `single_member_only_chk (R-78's one-rider filter)`

SaveRemoveClose

`save_btn · remove_btn · wxID_CLOSE — the left pane's bottom row (ux-polish: Save replaced the old Add-team slot on this side)`

Team

Name
Plate (relay)
Notes

`name_input · relay_plate_input (row hidden on a rider_pooled ride) · notes_input (wxTE_MULTILINE; a ≥ 3-text-line minimum height is applied code-side)`

Logo `logo_bmp` Pick card…Image…

`logo_bmp (wxStaticBitmap — a real card-asset or PNG bitmap preview sitting directly above the Members box, wx.NullBitmap when blank; renamed from the old logo_preview text control, ux-polish) · pick_card_btn · image_btn`

Members (read-only)

| A. Roy |
| K. Singh |

`members_list (wxDataViewCtrl · read-only — membership is managed in the Rider Editor; bounded height code-side so a long member list scrolls inside it while teams_list takes the extra dialog height)`

Add team

`add_btn — the right pane's bottom anchor (ux-polish): with no team selected the form above IS the Add form`

⚠ code-side (ux-polish rework): the dialog's two panes share the width (~50/50 — option 1 each), wide enough for three list columns + the record form (MIN_SIZE 940×560); rows for both lists; the Plate (relay) row's team_relay-only visibility — a rider_pooled team's plate is derived from its members (S1), never settable here; the Logo cell's kind text (Card/Image, image wins — an empty cell when neither); logo_bmp's bitmap — a card asset bitmap, the picked PNG's decode, or wx.NullBitmap; add_btn creates the team FROM THE FORM — name and notes read straight off it (no native name prompt): a blank name refuses ("enter a team name") and so does a duplicate name (trimmed, case-insensitive — `a team named "<name>" already exists`), the same guard applying to a rename on Save; a staged logo (Pick card cycles the seeded sequence / Image… loads a PNG — both shown in logo_bmp) is consumed by the next successful Add; a successful Add or Remove resets the blank Add form; selecting a row fills the form with that team's record and discards any staged logo. A team's logo card auto-assigns from the ride's seeded shoe seed (rng_seed → team_logo_seed) at creation — no two auto-assigned teams share. Add/Remove are DRAFT-only (refused via teams_infobar, an wxInfoBar built code-side with SetName like roster_infobar, once the ride has started); refused saves show there too. Teams here are *records*: Add team creates the roster's transient size-1 team (spec S2's floor is start-time), anchored by one rider named from the team's own name — rename/move that rider in the Rider Editor, which owns membership. Opened from Riders ▸ Teams Editor (mi_team_editor), a route enabled only for mixed rides (teams_allowed, R-11); the window lives in teams.xrc (§15b). The `single_member_only_chk` checkbox (R-78) filters `teams_list` to the one-rider teams.

Check for Rider Issues — `rider_issues_dlg`✕

- **Rider issues** — `issues_summary_lbl` (count line) · `issues_list (wxDataViewCtrl · Plate | Name | Issue)` — the read-only report `rivercrossing.rider_issues.rider_issues` finds.

Open Editor… Convert to Solo Close

`open_editor_btn · convert_solo_btn · wxID_CLOSE (default, §15b dialogs.py decisions)`

⚠ code-side (R-78): issues_list columns/rows; convert_solo_btn enabled only for a pooled DRAFT team-of-one — "Convert to Solo" extracts its lone rider to their own solo entry (extract_rider_to_solo); "Open Editor…" opens the teams editor for a team-of-one, the rider editor otherwise, then re-lists; a refused conversion shows on issues_infobar (an wxInfoBar built code-side with SetName). Opened from Riders ▸ Check for Rider Issues… (mi_check_rider_issues), a ride-open route; the window lives in riders.xrc (§15b).

Entry Detail — 77 Trail Blazers`entry_detail_dlg`✕

Plate:77 ▾
`plate_choice (wxChoice — the roster's entry plates, the shown entry selected; W11 F2b canvas addition: the six correction actions and the menu correction rows act on the shown entry, so the picker points the dialog at another entry without closing it. Loaded in code from the live roster; the no-store empty state leaves it empty and disabled)`

- **Team · 3 riders · 9 laps · 3:02:11** — A. Roy (77) · K. Singh (78) · L. Marchetti (79) `entry_header_lbl · members_lbl`
Cards held (9)

9♥
K♠
K♣
JK★
4♦…

`cards_list (wxDataViewCtrl · a DataViewBitmapRenderer column, not icon mode — that is a wxListCtrl feature and does not exist on DataView; AppendBitmapColumn's default renderer registers against wxBitmapBundle and silently drops a plain wx.Bitmap, so the column declares DataViewBitmapRenderer("wxBitmap") explicitly)`

| Lap | Time | Lap time | Rider | Card |
|---|---|---|---|---|
| 9 | 14:22:18 | 19:55 | 78 | K♣ |
| 8 | 14:02:23 | 21:40 | 77 | JK★ |

`laps_list (wxDataViewCtrl)`

Edit crossing…Deal card…Void card…Move rider…Mark DNF…Audit trailClose

`edit_crossing_btn · deal_card_btn · void_card_btn · move_rider_btn (pooled only) · dnf_btn · audit_btn · wxID_CLOSE`

Edit Crossing / Add Crossing at Time`edit_crossing_dlg`✕

Plate
Time
Reason

`plate_input · time_picker · reason_input · void_btn (edit mode only)`

Void crossing…OKCancel

⚠ one XRC dialog, two titles: Cards ▸ Edit / Add-at-Time set title + prefill in code; reason required, audit-logged.

Reassign Plate`reassign_dlg`✕

Crossing 14:21:59 · lap credited to **45** `crossing_lbl`

New plateReason

`new_plate_input · reason_input`

OKCancel

Deal Manual Card`manual_deal_dlg`✕

PlateReason

Deals the next card from the shoe — deterministic, audit-logged.

`plate_input · reason_input`

DealCancel

Mark DNF`dnf_confirm_dlg`✕

- **212 · M. Chen** — keeps laps and cards, ranked in the DNF block. Reversible. `entry_lbl`

Reason

`reason_input`

Mark DNFCancel

`wxID_OK "Mark DNF" · wxID_CANCEL (default)`

Void Card`void_card_confirm_dlg`✕

- **9♥ — 45 · J. Okafor** — removes the card from the entry's scored hand. Audit-logged. `card_lbl`

Reason

`reason_input`

Void cardCancel

`wxID_OK "Void card" · wxID_CANCEL (default + focused)`

⚠ E7 mock-first: the last §15 row with no frozen window, authored in E7 before wiring; names registered in spec.md §15b.

D · Results, library, audit

Results — GORBA EPIC 2026 (FINISHED 16:02:11)`results_frame`— ▢ ✕

Tie-break: ① Most laps ② Total time ③ High-card draw ▲▼`tiebreak_list (re-rank live)`Reopen ride…`reopen_btn`

| Place | Plate | Entry | Laps | Total | Best 5 | Hand |
|---|---|---|---|---|---|---|
| | | **Teams** | | | | |
| 1 | 77 | Trail Blazers | 9 | 5:44:02 | K♠ K♣ K♦ JK★ 9♥ | Four of a Kind — Kings |
| 2 | 56 | Fat Tire Four | 9 | 5:12:44 | Q♥ Q♣ Q♠ 9♦ 9♠ | Full House — Queens over Nines |
| | | **Solo** | | | | |
| 1 | 123 | Sam Ellis | 8 | 5:51:17 | Q♥ J♥ T♥ 9♥ 8♥ | Straight Flush — Queen high |
| 2 | 8 | R. Dubois | 7 | 5:38:44 | A♣ A♦ A♥ 4♦ 4♠ | Full House — Aces over Fours |

`standings_list (wxDataViewCtrl · full field; mixed rides render a Teams section then a Solo section, each numbered from 1 with its own DNF tail — a kind absent from the ride has no section; card bitmaps via imagelist)`
Publish options

 Show lap & total times
 Laps leaderboard
 Fastest-time leaderboard
 Full field
 All cards drawn

`show_times_chk (off default; hides Total col here too) · laps_board_chk · time_board_chk · full_field_chk · all_cards_chk`

Export HTML…Export PDF…Podium poster…Export CSV…

`export_html_btn · export_pdf_btn · poster_btn · export_csv_btn`

⚠ code-side: standings rows — on a mixed ride the presenter feeds (teams, solo) and a non-empty kind renders as a section header row ("Teams"/"Solo" in the Entry column, other cells blank) followed by its rows, each section numbered from 1 with its own DNF tail; a solo-only ride shows only the Solo section (Phase 3, R-65); "draw required" tie rows highlighted with a ⚠ badge column (in the Place cell — the canvas pins seven columns); stale-export flag banner (wxInfoBar stale_infobar — built in code, named with SetName()) after reopened corrections; tie-break reorder re-ranks live through the control's own ▲▼ arrows (seeded from the ride's stored order as plain labels, same as ride_setup; a New/Delete-edited row set falls back to the known-good order with a status notice — E6.4.1); show_times_chk hides the Total column here too (R-63 UI proof).

Ride Library`ride_library_dlg`✕

| Ride | Date | Status | Entries |
|---|---|---|---|
| GORBA EPIC 2026 | 2026-09-20 | RUNNING | 180 |
| Club poker night | 2026-06-11 | FINISHED | 24 |

`rides_list (wxDataViewCtrl)`

OpenNew…Duplicate…Delete…Close

`wxID_OPEN · wxID_NEW · duplicate_btn · wxID_DELETE (never on RUNNING) · wxID_CLOSE — only wxID_CLOSE is positioned by wxStdDialogButtonSizer; the rest share a sibling wxBoxSizer (Spec §15b)`

Delete Ride`delete_ride_dlg`✕

Deletes **"Club poker night"** and all its data. A backup is written first. Type the ride's name to confirm:

`message_lbl (the ride's name is interpolated — UX-DESKTOP §4 requires naming the object) · confirm_name_input`

DeleteCancel

`wxID_DELETE (enabled on exact match) · wxID_CANCEL (default)`

Audit Trail`audit_dlg`✕

All actionsCrossing editsCard deals/voidsMovesDNFShoe reshuffle

`audit_search (wxSearchCtrl) · action_choice`

| When | Who | Action | Entry | Reason |
|---|---|---|---|---|
| 14:23:02 | scorer | Void crossing | 45 | mis-key |
| 14:21:40 | scorer | Manual deal 7♦ | 45 | flag confirmed |

`audit_list (wxDataViewCtrl · newest first)`

Close

E · System & help

Settings`settings_dlg`✕

Appearance
 System
 Light
 Dark

`appearance_system_radio (default) · appearance_light_radio · appearance_dark_radio — the single theme surface since W13 (the View-menu theme trio left); all three live on both platforms: the 4.3.1 / wxWidgets 3.3.3 baseline supplies wx.App.SetAppearance, so Dark is never disabled and there is no capability hint`

 Sound on crossing (recorded / flagged / error cues)
 Hide times on the console (toggle any time, even mid-ride)

Back up now

`sound_chk · hide_times_chk · backup_now_btn`

`W13: the text-zoom choice (zoom_choice) left this dialog — View ▸ Zoom is the single zoom surface; the dialog's OK still carries zoom_percent through the settings file unchanged`

OKCancel

About RiverCrossing`about_dlg (Help ▸ wxID_ABOUT)`✕

`about_logo_bmp (the ride's logo; without one the embedded RiverCrossing logo — about.py's EMBEDDED_LOGO_SVG — so the About box never shows a blank bitmap, ux-polish)`

RiverCrossing 1.0.0 `version_lbl ("RiverCrossing <package version>", written by AboutDialog)`

Timing & poker-hand scoring for poker-run rides.

Mark Buckaway `author_lbl`
© 2026 Mark Buckaway `copyright_lbl`
Licensed under GPL-3.0-only `license_lbl`

Built for GORBA — [gorba.ca](https://gorba.ca) `gorba_link (wxHyperlinkCtrl)`

Close

⚠ ux-polish: fixed-size — about_dlg alone drops wxRESIZE_BORDER (wxDEFAULT_DIALOG_STYLE only) and AboutDialog pins ABOUT_SIZE (500×420) via SetSize + min/max hints, so its short fixed copy can never re-wrap from a user resize at any text zoom. The three attribution lines (author_lbl/copyright_lbl/license_lbl) are fixed XRC wording; the logo chain and version text are code-side.

Keyboard Shortcuts`shortcuts_dlg (Help ▸ mi_shortcuts)`✕

| Key | Action |
|---|---|
| Enter | Record crossing for typed plate |
| Ctrl+Z | Undo last crossing |
| F5 | Standings (Results window) |
| F1 | User guide |

`shortcuts_list (wxDataViewCtrl · read-only; rows filled in code from the accelerator table — cannot drift)`

Close

`wxID_CLOSE`

Evaluator Self-Test`selftest_dlg`✕

7,462 distinct ranks ........ PASS
Joker vector table (28) ..... PASS
Five-of-a-kind ordering ..... PASS
Whole-field 180×12 timing ... 0.31 s PASS
`selftest_output (read-only wxTextCtrl, monospace) · rerun_btn`

Run againClose
