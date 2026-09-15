# RiverCrossing — XRC Window Designs

RiverCrossing — XRC window designs (implementation truth · retires the Industry hi-fi mockups)

Native wxWidgets controls only, sizer-based, Windows look shown (identical structure on macOS). Every interactive control carries its `xrc_name` annotation — these are the XRC `name` attributes, canonical and frozen; standard buttons use stock IDs (wxID_OK, wxID_CANCEL, wxID_CLOSE, wxID_DELETE, wxID_EXIT, wxID_ABOUT). Naming rules + window↔file map: Spec §15b.

- **Canvas caveats (HTML approximation):** browser form controls stand in for wx natives; exact spacing/fonts come from sizers + system fonts, not these pixels; sizes below are minimums expressed in dialog units at build time. **Global code-side items (not expressible in XRC):** ① DataView columns + row data + per-row attributes (bold flagged rows, red suits) — appended in code, the attributes through a `DataViewIndexListModel` subclass overriding `GetAttrByRow`, since no setter exists; ② card imagelist population (53 card bitmaps @1x/2x); ③ wxInfoBar **construction** + message text + Show/Hide calls, and the console's two custom gauges — the `RaceClock` dials (`elapsed_clock`/`remaining_clock`) and the `StopLight` (`ride_status_light`) — built code-side into main.xrc's `elapsed_clock_panel`/`remaining_clock_panel`/`ride_status_panel` placeholder slots and named with `SetName()`, because XRC cannot author a `wx.Control` subclass (ux-polish); ④ splitter sash position restore from settings; ⑤ menu enable/disable per ride state (§15); ⑥ theme: `wx.App.SetAppearance` on the 4.3.1 / wxWidgets 3.3.3 baseline — all three appearance radios live on both platforms (measured: macOS applies at runtime to existing windows; `Appearance::System` pins the NSAppearance current at the call instead of restoring follow-the-system, so the app re-applies System on `wx.EVT_SYS_COLOUR_CHANGED`, best-effort; MSW on 3.3.3 returns `CannotChange` once a top-level window exists, so a Windows theme change takes effect at next launch and the status bar says so); ⑦ window minimum sizes via `SetMinSize()` (XRC has no window-level minsize); ⑧ the zoom menu-item default `mi_zoom_100` (`<checked>` is a no-op on radio items; W13 removed the View-menu theme trio — the Settings appearance radios are the single theme surface, so the old `mi_theme_system` default went with them). Everything else drawn here is declared in XRC.

- **Three classes cannot be authored in XRC** (measured on 4.3.1 / wxWidgets 3.3.3 — full detail in Spec §15b): **wxInfoBar** yields a generic `wx.Control` and drops its `name`, so all ten code-side info bars (`stale_infobar` in results_dlg, `setup_infobar` in ride_setup_dlg, `roster_infobar`/`csv_infobar` in the rider editor, `add_rider_infobar` in add_rider_dlg (W7), `issues_infobar` in rider_issues_dlg, `teams_infobar` in team_editor_dlg, `add_team_infobar` in add_team_dlg (W8), `crossing_detail_infobar` in crossing_detail_dlg (J2) and `sim_infobar` in simulation_dlg — §15b) are built in code and named with `SetName()` · **wxDataViewListCtrl**'s handler hard-forces the name `dataviewCtrl`, so every list control below is a **wxDataViewCtrl**, whose name is honoured · **wxMenuBar** drops its name too: `main_menubar` loads via `XmlResource.LoadMenuBar()` and never resolves through `FindWindowByName`. Custom `wx.Control` subclasses are equally un-authorable in XRC: the console's `RaceClock` dials and `StopLight` (views/gauges.py) are built into main.xrc's placeholder panels and named with `SetName()` — the InfoBar rule extended (ux-polish). Also measured: `wxStdDialogButtonSizer` positions only OK/Yes/Save/Apply/No/Cancel/Close/Help, so `wxID_OPEN`, `wxID_NEW`, `wxID_DELETE` and the custom buttons annotated below live in a sibling `wxBoxSizer`; a bare `&` in a label is a mnemonic and is stripped on macOS — author `&&` and read labels with `GetLabelText()`.

A · Main frame

RiverCrossing — GORBA EPIC & MTB Festival 2026`main_frame`— ▢ ✕

FileRideRidersCardsResultsViewHelp`main_menubar (the resource id LoadMenuBar() loads by — wxMenuBar drops its name, so it never resolves through FindWindowByName) · items mi_* — see §15b; per-ride-state enabling per §15 — `mi_new_ride` (New Ride…) is enabled only while no ride is open, `mi_edit_ride` (Edit Ride…) only while one is`

⚠ The console carries no `wxInfoBar` in any state (G4): the resume prompt is the modal `resume_dlg` (section E), and the REOPENED corrections notice is the status bar's first field — nothing sits above the ride-info block.

◉ ● ◉ — the lit lamp leads the header row, inside the fixed-width "Status" group box left of the ride groups
`ride_status_panel · ride_status_light (StopLight · three stacked circles — green live RUNNING · amber REOPENED/stopped RUNNING · red DRAFT/FINISHED · built code-side into ride_status_panel and named with SetName(); colour is never the sole channel — the status label spells the state. ride_status_panel is the "Status" wxStaticBoxSizer's own column, far-left in the header, and `main_frame._pin_stop_light` floors the lamp at its own `DoGetBestSize()` while `_pin_status_label_width` floors `ride_status_lbl` at the widest state word, so the fixed-width box never resizes on a state change)`
RUNNING `ride_status_lbl`

▣ Ride — Name / Date / Venue, each read-out labelled · ▣ Details — Organizer / Scorer / Lap length km
`ride_name_value · ride_date_value · ride_venue_value (the "Ride" wxStaticBoxSizer group) · ride_organizer_value · ride_scorer_value · ride_lap_km_value (the "Details" wxStaticBoxSizer group) — read-only wxTextCtrl, <size>240,-1</size>, each preceded by its own unnamed label; the two native group boxes split C1's single "Ride" group into the ride's identity rows and its organizer rows, and each group's label is <label> text only, never a frozen name, so no constant in ui/ids.py changed. ⬛ `ride_logo_bmp` (wxStaticBitmap — the ride's own logo, fitted to 64×64 and hidden when the ride has none) closes the block, to the right of both groups, with the Lap box and the clock panels to its right`

▣ (untitled) — 01, caption "Current Lap" below
`current_lap_lbl (wxStaticText — the ride's current lap: the highest lap number recorded, 00 before any crossing; always two digits (capped at main_frame.MAX_CURRENT_LAP), light-green clock-sized type authored in main.xrc's <fg>/<font>, its width pinned to the two-digit extent code-side by main_frame._pin_current_lap_width. The untitled wxStaticBoxSizer (its label is empty — the caption names the box) sits between the ride-info block and the clock panels; its "Current Lap" caption is unnamed <label> text)`

Elapsed — (dial) — 4:22:41      Remaining — (dial) — 1:37:19

`elapsed_clock_panel · remaining_clock_panel (XRC placeholder panels — ux-polish): each clock column reads caption ("Elapsed"/"Remaining"), then the RaceClock dial (elapsed_clock/remaining_clock — code-side SetName(), views/gauges.py), then the numeric readout (clock_elapsed_lbl/clock_remaining_lbl). The elapsed dial fills toward 1.0 and the remaining dial drains toward 0.0 as the ride runs — both fractions over the planned duration, clamped, zeroed in DRAFT`

Start ride   Stop ride…

`start_btn · stop_btn — round green GO / red STOP wxBitmapButtons (ux-polish): the SVG glyphs (gauges.py go_bundle/stop_bundle) are applied code-side, and the labels are re-applied there too ("Start ride"/"Stop ride…"), because the XRC bitmap-button handler ignores <label> — the buttons stay text-named for assistive tech. C2 removed the Arm checkbox (`arm_stop_chk`) and its 10 s auto-clear: the engine's own state, mirrored on the buttons by the presenter's `refresh_console_gates` (the mi_start_ride/mi_stop_ride rules), is the single gate — start_btn is enabled in DRAFT, REOPENED and stopped-RUNNING (continue-after-stop is the resume mechanism), disabled in live RUNNING and FINISHED; stop_btn is enabled only in a live RUNNING ride (not stopped); a riderless-roster stop/start refusal surfaces as a native warning`

Record crossing `(the entry row is framed by a native wxStaticBoxSizer so the operator can always find it — Phase 8)`

Plate`plate_input (focused · larger type via <font><sysfont>wxSYS_DEFAULT_GUI_FONT</sysfont><relativesize>1.5</relativesize></font> — relative so the 90–150% zoom still applies, never an absolute point size · wider DIP <size> · <hint> "Rider plate")`
Record (Enter)`record_btn`
✓ 123 · Sam Ellis · Lap 4 · 22:41 · dealt 9♥`last_crossing_lbl — W9: the dealt card's suit always renders as a glyph (the code is real, held or not) and a held crossing appends " (held)"`
Undo last (Ctrl+Z)`undo_btn — gated RUNNING with ≥ 1 crossing (W5, the mi_undo_crossing rule mirrored on the button; REOPENED corrections are the dialogs' own job)`

Search`crossings_search (wxSearchCtrl — Phase 4: narrows the feed to the rows whose cells carry the typed text; cleared/blank shows every crossing, mirroring the rider editor's own search)`

| Time | Plate | Name | Team | Card | Lap | Lap time | Total |
|---|---|---|---|---|---|---|---|
| 4:20:52 | 8 | R. Dubois | solo | 4♦ | 7 | 21:17 | 2:58:03 |
| 4:21:30 | 212 | M. Chen | solo | JK★ | 5 | 24:02 | 2:10:44 |
| 4:21:59 | 45 | J. Okafor | solo | 9♦ | 6 | 07:12 ⚑ | 2:44:30 |
| 4:22:18 | 77 | P. Nkosi | Trail Blazers (T) | K♠ | 9 | 19:55 | 3:02:11 |
| 4:22:41 | 123 | Sam Ellis | solo | 9♥ | 4 | 22:41 | 1:31:04 |

`crossings_list (wxDataViewCtrl · every crossing of the ride — no cap, the list scrolls (Phase 4 retired R-32's 30-row window) — every column sortable and resizable through CrossingsFeedModel.Compare (both flags spelled out, since an explicit flags= replaces AppendTextColumn's own default; the platform's own header arrow), opening on the Time column ascending, so the first row is the ride's first crossing (elapsed 0) and the list reads as the ride clock. Columns in the W9 order Time · Plate · Name · Team · Card · Lap · Lap time · Total with one pinned width per column (80, 50, 150, 130, 60, 50, 80, 80 — DataView has no autosize-to-content, so the widths are data), the Total and Lap time columns R-37's two independent show settings each hide. The Time cell is the *elapsed* reading since the gun (h:mm:ss from 0), never the wall-clock crossing instant, and Total is the entry's own running sum; a DNF rider's Name cell carries a " DNF" suffix. The Team cell reads `solo` for a solo entry — never blank, and never the rider's own name repeated from the Name column (blank only for a crossing whose plate no longer resolves) — the same word the rider lists show. The Card column always renders a real dealt code: a held crossing's row carries the held card's own code — never a placeholder — and the row renders bold, the flagged channel (R-34)`

Crossings
1 124

Cards dealt
1 092

On course
42

Shoe
41/108

Riders
180

Teams
24

`crossings_count_lbl · cards_count_lbl · on_course_lbl · shoe_lbl · riders_count_lbl · teams_count_lbl — the six chips: the four live-timing counts, then the two W12 registration chips (Riders/Teams) appended after them. The roster counts are static per ride; the Teams chip (caption and value) is hidden code-side in solo-only rides (R-11)`
Needs Review   Riders — `review_notebook (wxNotebook · two tabs — ux-polish; the notebookpage node's own name is dropped by the XRC handler, so the names live on the page children below)`

Plate | Lap | Lap time | Issue   `flagged_list (wxDataViewCtrl — the R-34 flag rows, four sortable columns)`
Review… `review_btn — and the Cards ▸ Review Held Cards route both land on this page (SetSelection(0) + focus)`

Plate | Name | Team   `console_riders_list (wxDataViewCtrl — the live roster; a solo rider's Team cell is `solo` (never the em dash — the word every rider list shows, from `rider_columns.SOLO_TEAM_TEXT`); rows refreshed on the 1 s tick; the five shared CONSOLE_RIDER_COLUMNS — Plate | Name | Team | Sex | Cards — are natively sortable and resizable (DATAVIEW_COL_SORTABLE | DATAVIEW_COL_RESIZABLE, the platform's own header arrow; the sort is re-applied after every tick rebuild through _apply_sort, which clears the column's sort key first (UnsetAsSortKey()) before SetSortOrder() + Resort() — a same-direction SetSortOrder() is a measured no-op on macOS, so without the clear the rebuilt model reverts to the source's order and the operator's sort silently reverts on the next tick) with the Name column at 160 px and the rest at the platform's 80 DIP default) — activating a rider row (double-click or Enter) opens rider_editor_dlg pre-selected at that rider's plate (the app wires the view's set_on_open_rider seam)`

epic-2026.prdb

Saved 14:22:41

Shoe cycle 1 · seed 8843

`main_statusbar`

⚠ code-side: feed columns/rows + flagged-row attrs (a DataViewIndexListModel subclass overriding GetAttrByRow — there is no setter); card column bitmaps from imagelist; InfoBar construction + text + show/hide; sash position (main_splitter); state variants — DRAFT: clock 0:00:00, start_btn enabled, plate_input disabled with "start the ride to record" hint (record_btn tracks plate_input's enablement in every state) · FINISHED: entry row hidden, no banner — the lamp goes red and `ride_status_lbl` reads FINISHED; the status bar (`main_statusbar`) reads "Ride is finished. Results are available"; results open from the Results menu (Results ▸ Standings, F5) · REOPENED: corrections banner, entry disabled, edited rows highlighted, Start enabled (continue riding — C2), and the clock frozen at the recorded finish (C3) as FINISHED's is. ux-polish header: the two `RaceClock` dials (`elapsed_clock`/`remaining_clock`) are inserted code-side between each clock column's caption and numeric readout in `elapsed_clock_panel`/`remaining_clock_panel` (fractions over planned duration — elapsed fills toward 1.0, remaining drains toward 0.0, both 0.0 in DRAFT), `ride_status_panel` is the fixed-width "Status" group box's own column, far-left in the header — the `StopLight` (`ride_status_light`, pinned at its own `DoGetBestSize()` by `main_frame._pin_stop_light` so the leading column cannot squeeze it away) beside `ride_status_lbl` (floored at the widest state word by `_pin_status_label_width`) — green live RUNNING · amber REOPENED/stopped RUNNING · red DRAFT/FINISHED, `console.stop_light_mode`, and colour is never the sole channel; the two ride groups (Ride: Name/Date/Venue, Details: Organizer/Scorer/Lap length km) follow it, with `ride_logo_bmp` to their right, then the Lap box (`current_lap_lbl`, its two-digit width pinned by `main_frame._pin_current_lap_width`) and the clock panels — and `start_btn`/`stop_btn` are `wxBitmapButton`s carrying gauges.py's round green GO / red STOP SVG glyphs with "Start ride"/"Stop ride…" re-applied in code (the XRC handler ignores bitmap-button labels). Review sidebar: a two-tab `review_notebook` wxNotebook — "Needs Review" (index 0: `flagged_list` Plate | Lap | Lap time + `review_btn`) and "Riders" (`console_riders_list` Plate | Name | Team over the live roster, refreshed on the 1 s tick) — and the rider-row activation seam (double-click or Enter) opens `rider_editor_dlg` pre-selected at that rider's plate. The two time-column settings (show_total_times_chk/show_lap_time_chk, mirrored by the two View-menu check items) hide the Total and Lap time columns independently; the clock stays; clock_elapsed_lbl/clock_remaining_lbl reserve a fixed minimum width and re-layout on update so long elapsed/remaining text never overlaps the Start/Stop controls (R-55). W6 clock freeze: while the ride is stopped, the elapsed/remaining labels and the gauge dials freeze at a captured value (the first refresh after the stop captures it); Continue unfreezes and re-renders the live wall-clock elapsed immediately — the engine keeps counting underneath, the freeze is display-only. C3 extends the freeze to a closed ride: a FINISHED or REOPENED console renders `RideEngine.closed_elapsed()` — the recorded finish instant — never the live clock. W5 console gates, C2-extended (the engine is the single source of truth): start_btn mirrors the mi_start_ride rule (DRAFT, REOPENED, or stopped-RUNNING for continue), stop_btn mirrors the mi_stop_ride rule (a live RUNNING ride only — the Arm checkbox is gone), undo_btn mirrors mi_undo_crossing (RUNNING with ≥ 1 crossing). Min frame 1100×780, fits 1366×768 — declared as <size> and re-applied with SetMinSize(); Spec §13 states the same figure (W9 raised the canvas's earlier 1100×700 floor). The feed/list splitter's sash default is 850 px (`DEFAULT_SASH`, W9), applied when no saved position exists.

B · Ride setup & lifecycle dialogs

Ride Setup`ride_setup_dlg`✕

Name
DatePlanned start
VenueLap length km
OrganizerScorer
Duration (H:MM)Min lap (M:SS)

Hold short-lap cards for review (default)
Always deal cards

Logo

`name_input · date_picker · start_time_picker · venue_input · lap_km_spin · organizer_input · scorer_input · duration_input · min_lap_input · hold_short_radio · always_deal_radio — the W4 short-lap card policy pair sits between the timed-fields grid and the Entries box: Duration and Min lap are text fields that parse "H:MM"/"M:SS" (unparseable or non-positive values refuse on OK, W4), and hold_short_radio ("Hold short-lap cards for review") / always_deal_radio ("Always deal cards") read straight into RideConfig.hold_short_laps — hold for review is the checked default (W4; the 1.0.17 follow-up flipped it from always-deal), so a fresh ride holds a short-lap card until the operator confirms or voids it (R-34). The standalone Logo row (a `logo_picker` wxFilePickerCtrl with a path box) is retired: `logo_preview_bmp · logo_status_lbl · logo_browse_btn` now live in the Cards box beside `tiebreak_list` — a 240×240 wxStaticBitmap preview, the status label (default "NO LOGO"), and a "Browse…" button that picks a PNG, resizes it to fit the 256×256 standard logo size, previews it and stages the resized file as the ride's `logo_png` BLOB source`
Entries
 Solo riders only
 Solo + teams (default)

Max riders per team (2–10)

 Rider plates — pooled (default): each rider draws per lap, uncapped; team hand from pooled cards
 Team plate — relay: one plate per team, one rider on course (EPIC)

`solo_radio · mixed_radio — teams is the default: mixed_radio carries the XRC <value>1</value> (ux-polish), so "Solo + teams (default)" is what a fresh setup shows; solo-only is the opt-out · team_size_spin · pooled_radio · relay_radio (enabled only when mixed_radio)`

Cards

Decks Jokers 1 ▾ Per deck Total (checked)
 Card cap Disabled ▾

Tie-break order ① High-card draw ② Most laps ③ Total time ▲▼

`decks_spin · jokers_spin (a wxSpinCtrl over 0..10, opening on 1 — `ride.DEFAULT_JOKERS_PER_DECK`/`ride.MAX_JOKERS_PER_DECK`, recorded so the control and the domain cannot drift) · jokers_per_deck_radio / jokers_total_radio (the jokers mode pair — "Per deck" re-deals that many jokers every cycle, "Total" (checked, `ride.DEFAULT_JOKERS_MODE`) spends them once across the ride; `ride.JOKERS_MODES` fixes the two spellings and the ride persists `ride.jokers_mode`) · cap_choice (a wxChoice whose "Disabled" first item is the default no-cap selection, then one item per cap 5..20; the view reads the item's text, so the numbers are domain values, not indices) · tiebreak_list (wxEditableListBox — exactly the three rows above, three lines tall at <size>120,120</size> with the width sized to the longest row "High-card draw" plus its reorder arrows; the XRC declares no <style>, so it shows the arrows alone and no New/Edit/Delete buttons)`

OKCancel

`wxID_OK "Save" (D2: one label for both modes — New Ride commits a new ride, Edit Ride saves an existing one) · wxID_CANCEL (wxStdDialogButtonSizer)`

⚠ code-side: entry/plate-model group locks after start (relay) or stays editable (pooled, R-17); tiebreak_list reorder persisted (its ①②③ numbering here is illustration, not row text); the stored tie-break order's default is high-card-first (`ride.DEFAULT_TIEBREAK_ORDER`) and applies only to a FINISHED ride's results — an unresolved pair is then flagged "draw required" (`standings.HIGH_CARD_DRAW` stops the sort), while a not-yet-finished ride ignores the stored order and auto-ranks most laps then total time (`standings.LIVE_TIEBREAK_ORDER`, applied by `EngineDataSource.standings`); the logo's Browse button resizes the picked PNG to fit 256×256 (`LOGO_STANDARD_SIZE`) and stages that resized file, so the ride row's `logo_png` BLOB is always the standard size; decks_spin's value — the XRC declares none and the presenter supplies **8** (Spec §4; settled by the E3.5 ride-setup work — the canvas's 2 was a mock artifact); lap_km_spin has the same no-declared-value shape and the presenter pushes the canvas's **8.0** on load (`ride.DEFAULT_LAP_KM`, W4 — a fresh dialog would otherwise sit at 0.0 and refuse every submit). ux-polish: the minimum-setup rule — OK (and a DRAFT ride's Start, ride.py's own start gate) refuses a config with a blank name/venue/organizer/scorer or a non-positive lap length (`ride.setup_minimum_violations`), joining every reason into one `setup_infobar` refusal (the code-side wxInfoBar, §15b) and leaving the dialog open; the ride's date, planned start and logo are not part of the minimum rule — a logo is fully optional and the date/time pickers always hold a value, while duration/min-lap keep their own parse-and-positivity validation (W4: a blank or unparseable "H:MM"/"M:SS" field surfaces its message on the dialog instead of failing silently). The short-lap policy radios persist as `RideConfig.hold_short_laps` — the store's first additive migration (v2, W4), never a setup refusal. All fields plain XRC.

Set Start Time`set_start_dlg`✕

Started at

`start_date_picker · start_time_picker`

Lap-1 times recompute from this moment.

OKCancel

~~Stop Ride?~~ `stop_confirm_dlg` ✕ — **RETIRED (W5)**

⚠ Retirement: the Stop flow now asks through the native confirm (`ui.std_dialogs.show_confirm`, R-85) — the same frozen copy below and the same Stop ride / Cancel buttons (Cancel default + focused), shown by the presenter's `on_stop_requested` and driven by both the console Stop button and Ride ▸ Stop Ride…, so row and button cannot drift. The window left dialogs.xrc and the frozen-name registry in W5.

The clock stops for everyone. Riders still on course keep their laps; no cards are dealt after stop. You can continue the ride later without losing anything.

Stop rideCancel

~~Finish Ride?~~ `finish_confirm_dlg` ✕ — **RETIRED (H2)**

⚠ Retirement: the finish question now asks through the native danger confirm (`ui.std_dialogs.show_danger`, R-85) — the retired window's own line, kept verbatim in `views.dialogs.finish_ride_message`: "Locks entry and computes final standings (evaluator self-test must be green). You can reopen later for corrections." — with Finish ride / Cancel (Cancel default + focused). Ride ▸ Finish Ride… shows it; from REOPENED the same confirm is retitled "Finish again?" with the primary button "Finish again" (`finish_again_labels`). On OK the status lamp goes red and the status bar reads "Ride is finished. Results are available"; the finish-time auto-export of HTML and PDF into the per-user `exports` folder was removed, so the operator exports on demand from the Results menu (Results ▸ Export HTML/PDF/Podium poster/CSV) or the results dialog's own export buttons (E2). The window left dialogs.xrc and the frozen-name registry in H2.

~~Duplicate Ride~~ `duplicate_ride_dlg` ✕ — **RETIRED (H2)**

⚠ Retirement: Duplicate Ride… now asks through the native non-destructive confirm (`ui.std_dialogs.show_prompt`, R-85) — the retired window's two lines, kept verbatim in `views.dialogs.duplicate_ride_message` and merged into one native message body: `Duplicate "<ride name>" as a new DRAFT ride?` + "Copies the ride's setup and full rider list — no timing data." — with Duplicate / Cancel (OK default, so a reflex Enter is safe). The window left dialogs.xrc and the frozen-name registry in H2.

~~Reopen Ride~~ `reopen_ride_dlg` ✕ — **RETIRED (H2)**

⚠ Retirement: Ride ▸ Reopen Ride now asks through the native non-destructive confirm (`ui.std_dialogs.show_prompt`, R-85) — the retired window's question and its note, kept in `views.dialogs.reopen_ride_message` and merged into one native message body: `Reopen "<ride name>" for corrections?` + "Standings recompute on export." — with Reopen / Cancel (OK default, so a reflex Enter is safe). The window left dialogs.xrc and the frozen-name registry in H2.

Resume Ride`resume_dlg`✕

"GORBA EPIC 2026" is still running — it kept timing on the wall clock. (Wording swaps for crash: "The app closed unexpectedly…" — session_state)

Continue rideOpen library

`message_lbl (ride name + quit/crash wording interpolated) · continue_btn (wxID_OK, default) · library_btn`

Ride Is Running`exit_running_dlg`✕

Quitting won't stop the ride — it keeps timing on the wall clock, and you'll be asked to continue when you reopen.

CancelFinish ride first…Quit — keep ride running

`wxID_CANCEL (default + focused) · finish_first_btn · wxID_OK "Quit — keep ride running"`

⚠ three buttons per Spec §15 and R-51, ordered per §13 Ghost · Secondary · Primary; the canvas drew two, and the requirements are the acceptance authority — missing functionality, not styling. finish_first_btn sits in a sibling wxBoxSizer because wxStdDialogButtonSizer positions only the stock ids it recognises.

~~Quit RiverCrossing?~~ `exit_confirm_dlg` ✕ — **RETIRED (H2)**

⚠ Retirement: the no-ride-running quit question now asks through the native confirm (`ui.std_dialogs.show_confirm`, R-85) — the frozen copy moved into `rivercrossing.ui.quit_flow` (`EXIT_CONFIRM_TITLE` "Quit RiverCrossing?", `EXIT_CONFIRM_MESSAGE` "Are you sure you want to quit? No ride is running.", `EXIT_CONFIRM_OK_LABEL` "Quit", `EXIT_CONFIRM_CANCEL_LABEL` "Cancel"), with Cancel the default and the initially-focused button, so a reflex Enter is safe. `quit_flow.dialog_for_status` now returns `EXIT_RUNNING_DLG` for a running ride and `None` otherwise — the XRC path is only the running one. On macOS the window ✕ hides the app (Dock click reopens it), so only ⌘Q / app-menu Quit / File ▸ Exit reach a quit dialog there; on Windows ✕ runs the same confirm flow. The window left dialogs.xrc and the frozen-name registry in H2.

~~No Ride Open~~ `no_ride_dlg` ✕ — **RETIRED (W15)**

⚠ Retirement: the launch's No Ride Open surface is gone. W15 replaced the window with a native info dialog (`app._show_no_ride_info` → `ui.std_dialogs.show_info`, R-85) — title "No Ride Open", the copy "No ride is loaded. Create a new one or load an existing one." (the retired window's own static line read "No ride is open.") — and that alert was itself removed in the 2026-09-13 launch cleanup (R-80 retired): a store-backed launch that resumes no ride and opens no ride now shows nothing at all, leaving the console standing with no prompt. The file menus are the create/open surface — New Ride… and Ride Library — since the ux-polish launch rework, so the window's two buttons had nothing left to do. The post-Show launch flow shows only `resume_dlg`; a launch that already deferred the same choice to resume_dlg's Open library never double-prompted. The window left dialogs.xrc and the frozen-name registry in W15 (`message_lbl` stays — it is §15b's shared name, still carried by `resume_dlg`, `exit_running_dlg` and `delete_ride_dlg`).

C · Riders, corrections & cards

Rider Editor`rider_editor_dlg`✕

Search`rider_search (wxSearchCtrl — W7: narrows riders_list by name or plate; cleared/blank shows the full list)`

| Plate | Name | Team |
|---|---|---|
| 123 | Sam Ellis | solo |
| 77 | A. Roy | Trail Blazers |
| 78 | K. Singh | Trail Blazers |
| 212 | M. Chen | solo |

`riders_list (wxDataViewCtrl · the list pane takes 3/5 of the dialog width and the form pane 2/5 — sizer options 3:2, W7 rework — Team col hidden in solo-only · a solo rider's Team cell is the word "solo" (W7), never the em dash · the shared Plate | Name | Team | Sex columns are natively sortable and resizable (DATAVIEW_COL_SORTABLE | DATAVIEW_COL_RESIZABLE with RiderRowListModel.Compare — the platform's own header arrow replaces W7's ▲/▼ title suffix) with the Name column opening at 160 px and the rest at the platform's 80 DIP default; the presenter holds no sort state and the view re-applies the operator's chosen sort after each show_riders rebuild (_apply_sort), so the rows stay ordered and a selection tracks the model row index through the sort — on macOS a same-direction SetSortOrder() is a measured no-op, so the tick-rebuilt console list clears the key (UnsetAsSortKey()) before re-applying (see console_riders_list)`

Add rider…DeleteClose

`add_btn ("Add rider…" — 1.0.13: the list pane's bottom row, moved here from the form pane; it opens the dedicated add_rider_dlg form, blank, and is the editor's only add route) · delete_btn · wxID_CLOSE — the three anchor the list pane's bottom, equal-sized (W7); the stock-dialog sizer cannot stretch stock buttons, so Close is a plain stock-id button inside that row's wxBoxSizer (dialogs.wire_close_button still finds it by name and wires Escape); Delete asks the native warning confirm (ui.std_dialogs.show_confirm, B3) before deleting`

Rider — PlateFirst nameLast nameTeam— solo —Trail Blazers

Edit Rider…

`plate_input · first_name_input · last_name_input (all wxTE_READONLY — 1.0.12 B1: the box is a read-only display of the selected record, never typed into, so the plate lock and the dirty-form save gate both left with the editable form) · team_choice (a wxChoice, selectable but never free-typed — "— solo —" first, then every existing team's display name; "New team…" is gone — teams are created only in the Teams Editor) · edit_btn ("Edit Rider…" — 1.0.12 B2: opens add_rider_dlg on the selected record, and a row double-click or Enter is the same route; 1.0.13: it alone anchors the form pane's bottom-right, acting on the record shown above it, so it carries a plain custom id — never wxID_OK — and wx never auto-closes on a click)`

⚠ code-side (1.0.12 B1/B3): the dialog draws at 1280×560 — the code-side SetMinSize floor applies width AND height (riders.xrc's header records the XRC-can't-minsize rule); list rows — the Name column renders the rider's full name (first + last, R-23), a solo entry's display name mirrors its rider's full name; team_choice content; the record form is a read-only display (plate_input/first_name_input/last_name_input carry wxTE_READONLY in the XRC), so the W7 plate lock and the dirty-form save gate are both gone — the record is edited in add_rider_dlg; delete_btn disabled until a row is selected and, once the entry has data, forever — post-start is DNF/void only (R-15) — and it asks the native warning confirm (ui.std_dialogs.show_confirm, B3) first; edit_btn (or a row double-click or Enter) opens add_rider_dlg on the selected record and is enabled only while a row is selected; refused edits show on roster_infobar (wxInfoBar, code-side SetName, §15b). With a store-backed ride open, committed changes are written back to the store when the modal ends on either open path (menu route and the console Riders-tab activation seam — app._persist_rider_editor_changes), so edits survive a relaunch; a refused write is a status notice. Teams editable until start (relay) / during ride (pooled). Import/Export Riders CSV live on the File menu only — the editor's own import_btn/export_btn retired in W7. Both routes it opens are the one add_rider_dlg window (riders.xrc): run_add_rider_flow opens it blank with the plate prefilled to next free, run_edit_rider_flow preloaded and retitled "Edit Rider", and both commit on its single "Save" button.

Add Rider / Edit Rider`add_rider_dlg`✕

Rider — PlateFirst nameLast nameTeam— solo —Trail Blazers

AddCancel

`plate_input · first_name_input · last_name_input · team_choice · wxID_OK "Save" (1.0.12 B2: one label for both modes, the view flooring the button's width in code — XRC has no window minsize) · wxID_CANCEL (wxStdDialogButtonSizer) — the W7 dialog, one window in two modes since 1.0.12: the Rider Editor's "Add rider…" (add_btn → run_add_rider_flow) opens it with the plate prefilled to next free and the form blank, and "Edit Rider…" (edit_btn, or a row double-click/Enter → run_edit_rider_flow) opens it on the selected rider with the record preloaded and the title re-applied as "Edit Rider". OK commits through AddRiderPresenter or EditRiderPresenter (a blank name, duplicate plate or full team refuses on add_rider_infobar and leaves the dialog open — never a silent non-close), and only a committed add or edit closes it. Same field names as rider_editor_dlg — §15b permits names to repeat across windows, but the read-only styles do not: this is the dialog where the operator types · the dialog lives in riders.xrc`

Import Riders — Preview`csv_preview_dlg`✕

riders.csv → **178 riders · 12 teams · 3 conflicts** `summary_lbl`

| Row | Problem |
|---|---|
| 41 | Duplicate plate 77 |
| 96 | Missing name |

`conflicts_list (wxDataViewCtrl)`

Nothing is written until you import. Re-import freely reshapes teams before start.

☑ Map unknown sex to Male `map_unknown_sex_chk`
☑ Convert teams of 1 to solo `convert_teams_of_one_chk`

ImportCancel

`map_unknown_sex_chk "Map unknown sex to Male" · convert_teams_of_one_chk "Convert teams of 1 to solo" · wxID_OK "Import" (disabled while conflicts > 0) · wxID_CANCEL`

⚠ code-side: summary_lbl text + conflicts_list rows; the wxID_OK gate; a refused import shows on csv_infobar (wxInfoBar, code-side SetName, §15b). map_unknown_sex_chk (W10) maps blank and unrecognized sex cells to "M" for the preview and commit — an explicit operator opt-in, not a silent guess. convert_teams_of_one_chk (R-21) imports each one-rider team row as a solo entry instead of leaving it a warned team-of-one — DRAFT-only (off DRAFT the ride's structure lock leaves the row on the existing team-of-one path) and defined on both plate models (pooled: the lone rider keeps their own plate as a solo entry; relay: the new solo entry carries the team's single plate). It too is an explicit, unchecked-by-default opt-in. Opened from File ▸ Import Riders CSV… after the OS-native picker (the editor's own import_btn retired with W7's rework — the File menu is the one CSV path).

Teams Editor`team_editor_dlg`✕

| Team | Riders |
|---|---|
| Trail Blazers | 3 |
| Moss Ridge Riders | 2 |

`teams_list (wxDataViewCtrl · Team | Riders — the Riders cell is the team's rider count. Phase 3 dropped the Logo column and made both columns natively sortable through the model's Compare; W10 makes them resizable (DATAVIEW_COL_SORTABLE | DATAVIEW_COL_RESIZABLE) and defaults the Team column to 160 px; the editor then selects by the row's display name, never a positional index, since sorting moves rows under the selection)`

 Only show one-rider teams `single_member_only_chk (R-78's one-rider filter)`

Edit…RemoveClose

`edit_btn · remove_btn · wxID_CLOSE — the left pane's bottom row (Phase 3: edit_btn "Edit…" replaced the old Save slot — the record's own Add/Edit Team dialog owns the edit, and edit_btn is enabled only while a team is selected); Remove asks the native warning confirm (ui.std_dialogs.show_confirm) before deleting. The three sizer items share option 1, so the stock Close stretches to their size instead of rendering smaller`

Team

Name
Plate (relay)
Notes

`name_input · relay_plate_input (row hidden on a rider_pooled ride — a rider_pooled team's plate is derived from its members, never settable here) · notes_input (wxTE_MULTILINE|wxTE_READONLY — the whole form is a read-only display of the selected team, Phase 3; the Notes box's ≥ 3-text-line minimum height is applied code-side)`

Members (read-only)

| A. Roy |
| K. Singh |

`members_list (wxDataViewCtrl · read-only — membership is managed in the Rider Editor; bounded height code-side so a long member list scrolls inside it while teams_list takes the extra dialog height)`

Add team…

`add_btn ("Add team…" — opens the dedicated add_team_dlg window blank; the in-form Add and its staged-logo state are retired with it) · edit_btn in the left pane opens that same window on the selected team, and a row double-click or Enter is the same route (Phase 3 — the pane's own form above is the read-only display)`

⚠ code-side (Phase 3 rework + W8): the dialog's two panes share the width (~50/50 — option 1 each), wide enough for two list columns + the read-only record form (MIN_SIZE 940×560); rows for both lists; the Plate (relay) row's team_relay-only visibility — a rider_pooled team's plate is derived from its members (S1), never settable here; edit_btn is enabled only while a team is selected (the record's own Add/Edit Team dialog owns the edit, so this editor carries no Save and no dirty gate); a blank or duplicate team name (trimmed, case-insensitive — `a team named "<name>" already exists`) refuses on Add/Save in that dialog. A team's logo card auto-assigns from the ride's seeded shoe seed (rng_seed → team_logo_seed) at creation, and each Pick card… draws a fresh random card that is neither assigned nor already staged — no two teams share. Add/Remove are DRAFT-only (refused via teams_infobar, an wxInfoBar built code-side with SetName like roster_infobar, once the ride has started); refused saves show there too. W8 teams are *records with zero riders*: Add team creates an empty TEAM entry — nothing invents a rider or a plate (R-81's amendment). The empty entry's plate follows the ride's plate model: a team_relay team carries its explicit relay plate (next free when left blank) with no riders; a rider_pooled team claims the next free plate provisionally, and the first rider to join through the Rider Editor replaces the claim with their own plate. With a store-backed ride open, committed changes are written back when the modal ends (app._persist_team_editor_changes — the mirror of the rider editor's hook). Opened from Riders ▸ Teams Editor (mi_team_editor), a route enabled only for mixed rides (teams_allowed, R-11); the window lives in teams.xrc (§15b). The `single_member_only_chk` checkbox (R-78) filters `teams_list` to the one-rider teams.

Add Team / Edit Team`add_team_dlg`✕

Team — NamePlate (relay)Notes

Logo `logo_bmp` Pick card…

AddCancel

`name_input · relay_plate_input (row hidden on a rider_pooled ride) · notes_input · logo_bmp (wxStaticBitmap — the staged card preview, wx.NullBitmap when blank; bounded by SetMaxSize(LOGO_PREVIEW_BOX) so no bitmap pushes the button row off the dialog) · pick_card_btn · wxID_OK "Add" (the edit route relabels it "Save" code-side, set_mode) · wxID_CANCEL (wxStdDialogButtonSizer) — the W8 dialog, one window in two modes since Phase 3: the Teams Editor's "Add team…" (add_btn → run_add_team_flow) opens it blank, and edit_btn (or a row double-click) opens it on the selected team with its record preloaded, the caption and OK label set per mode (Add Team/Add, Edit Team/Save). Add or Save commits through AddTeamPresenter — a blank or duplicate team name refuses on add_team_infobar and leaves the dialog open; the Add commit creates a zero-rider TEAM entry (W8/R-81) taking the staged card logo when one was picked, and each Pick card… draws a fresh random card that is neither assigned nor already staged (the logo image is retired, Phase 3). Same field names as team_editor_dlg — §15b permits names to repeat across windows · the window lives in teams.xrc`

Check for Rider Issues — `rider_issues_dlg`✕

- **Card check** — `card_check_lbl (wxStaticText — the card-sufficiency advisory above the summary: "Shoe holds N cards · estimated M crossings — NOT ENOUGH / far too many (2×+) / OK" from `ride.check_card_sufficiency`; hidden when the estimate is unavailable, so no stale verdict lingers)`
- **Rider issues** — `issues_summary_lbl` (count line) · `issues_list (wxDataViewCtrl · Plate | Name | Issue)` — the report `rivercrossing.rider_issues.rider_issues` finds.

Open Editor… Convert to Solo Assign Plate Renumber Close

`open_editor_btn · convert_solo_btn · assign_plate_btn ("Assign Plate") · renumber_btn ("Renumber") · wxID_CLOSE (default, §15b dialogs.py decisions)`

⚠ code-side (R-78): issues_list columns/rows; the card-sufficiency line (`card_check_lbl`) sits above the summary — `run_rider_issues_flow` renders `ride.check_card_sufficiency(config, roster, avg_speed_kmh)` when it is handed both the live ride config and the stored average rider speed, and either missing hides the line (§4); the report covers the duplicate-team-name (hard) and near-duplicate-team-name (⚠ warning) kinds beside team-of-one/missing-name/missing-number/duplicate-name/duplicate-number; convert_solo_btn enabled only for a pooled DRAFT team-of-one — "Convert to Solo" extracts its lone rider to their own solo entry (extract_rider_to_solo); assign_plate_btn is enabled only for a missing-number issue (gives that rider the next free plate) and renumber_btn only for a duplicate-number issue (renumbers the later claimant), both DRAFT-only and writing through the roster's shared change_plate dispatch so each plate shape picks its model-correct primitive; "Open Editor…" opens the teams editor preselected on a team-of-one's team by name (select_team_by_name), the rider editor preselected on the issue's plate (select_rider_by_plate) otherwise, then re-lists; refusals show on issues_infobar (an wxInfoBar built code-side with SetName). The view reconciles the list selection after every render so the fix buttons derive from the post-render row, and the flow reports a change from the roster's audit-log delta, so a nested editor's edit counts too. Opened from Riders ▸ Check for Rider Issues… (mi_check_rider_issues), a ride-open route; the window lives in riders.xrc (§15b).

Cannot Start Ride`start_blocked_dlg`✕

| Issue |
|---|
| organizer is required |
| scorer is required |
| roster has no riders |

`start_blocked_list (wxDataViewCtrl · one column "Issue" — one blocking reason per row, in the order the engine reported them)`

OK

`wxID_OK "OK" (wxStdDialogButtonSizer)`

⚠ code-side (Phase 5): the console's blocked-start report. `on_start` routes a `StartBlockedError` here (`view.show_start_blocked(exc.reasons)`) instead of the W5 one-line native warning: one row per blocking issue, in the order the engine reported them — a setup violation verbatim (`ride.setup_minimum_violations`), a roster violation prefixed with its plate (`Roster.validate_for_start`, R-12's team floor), or "roster has no riders" — with a single OK to dismiss. The ride stays un-started either way. The view appends the one `Issue` column in code (`START_BLOCKED_COLUMN_LABELS`) and opens the dialog at twice its fitted width and height (`main_frame._start_blocked_size`), since XRC declares no window-level minsize and the single column would otherwise clip its text. The window lives in riders.xrc (§15b).

~~Entry Detail — 77 Trail Blazers~~ `entry_detail_dlg` ✕ — **RETIRED (scoring-and-corrections)**

⚠ Retirement: the Riders ▸ Entry Detail… row (`mi_entry_detail`) and its whole window left the app — `detail.xrc` is deleted and its controls retired with it: `plate_choice`, `entry_header_lbl`, `members_lbl`, `cards_list`, `laps_list`, `move_rider_btn`, `deal_card_btn`, `dnf_btn`, `edit_crossing_btn`, `audit_btn` (the shared `void_card_btn` moved to Crossing Detail instead). Its corrections now live on the console's per-row Crossing Detail window (F), which grew `edit_time_btn`/`void_card_btn`; the per-row plate reassign runs through the `crossing_number_dlg` prompt (§9). Historical canvas copy follows.

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

Add Crossing at Time / Edit Time`edit_crossing_dlg`✕

Plate
Time
Reason

`plate_input · time_picker · reason_input · void_btn (hidden, with plate_input read-only, for Crossing Detail's Edit Time)`

Void crossing…OKCancel

⚠ one XRC dialog, two reachable titles: Cards ▸ Add Crossing at Time… sets "Add Crossing at Time", and Crossing Detail's Edit Time… sets "Edit Time", both in code with the prefill; reason required, audit-logged. The retired Cards ▸ Edit Crossing… row set "Edit Crossing" — the dialog's own `adding=False` default, which no route reaches now (1.0.17 follow-up). Crossing Detail's Edit Time opens it with a read-only plate and no `void_btn`, while the Add Crossing path keeps `void_btn` and an editable plate.

~~Reassign Plate~~ ✕ — **RETIRED (scoring-and-corrections):** the Cards ▸ Reassign Plate… route left with the retired Entry Detail window; Crossing Detail reassigns a plate through `crossing_number_dlg` + `reassign_crossing`.

Deal Bonus Card`manual_deal_dlg`✕

PlateReason

Deals the next card from the shoe — deterministic, audit-logged.

`plate_input · reason_input`

DealCancel

Mark DNF`dnf_confirm_dlg`✕

Rider plate

- **212 · M. Chen** — excluded from the results; a team keeps its other riders. Reversible. `entry_lbl`

Reason

`plate_input (the "Rider plate" target typed in) · reason_input`

Mark DNFCancel

`wxID_OK "Mark DNF" · wxID_CANCEL (default)`

⚠ code-side (per-rider DNF): the typed number resolves through the roster — a pooled team member's own number marks that rider alone (their cards forfeit from the team hand) and a team is out only when every rider is; a DNF subject is excluded from the results outright (no DNF block), while every lap and card stays in the record. With a target already known the runner overwrites `entry_lbl`'s consequence copy with the naming sentence (`dialogs.dnf_message`).

Void Card`void_card_confirm_dlg`✕

Card`card_lbl` — the dealt card's glyph (`9♥`)
Entry`entry_lbl` — the entry the card was dealt to (`45 · J. Okafor`)

Removes the card from the entry's scored hand. Audit-logged.

Reason

`reason_input`

Void cardCancel

`wxID_OK "Void card" (disabled until the Reason box is non-empty) · wxID_CANCEL (default + focused)`

⚠ E7 mock-first: the last §15 row with no frozen window, authored in E7 before wiring; names registered in spec.md §15b.

D · Results, library, audit

Results — GORBA EPIC 2026 (FINISHED 16:02:11)`results_dlg`✕

Teams Solo`results_notebook (wxNotebook — a MIXED ride's two standings pages; the XRC notebookpage nodes drop their own names, so the page names live on the two lists below. A SOLO-only ride hides the notebook and shows the standalone standings_list in its place)`

| Place | Plate | Entry | Laps | Best 5 | Hand |
|---|---|---|---|---|---|
| 1 | 77 | Trail Blazers | 9 | K♠ K♣ K♦ JK★ 9♥ | Four of a Kind — Kings |
| 2 | 56 | Fat Tire Four | 9 | Q♥ Q♣ Q♠ 9♦ 9♠ | Full House — Queens over Nines |

`teams_standings_list (wxDataViewCtrl — the Teams page)`

| Place | Plate | Entry | Laps | Best 5 | Hand |
|---|---|---|---|---|---|
| 1 | 123 | Sam Ellis | 8 | Q♥ J♥ 10♥ 9♥ 8♥ | Straight Flush — Queen high |
| 2 | 8 | R. Dubois | 7 | A♣ A♦ A♥ 4♦ 4♠ | Full House — Aces over Fours |

`solo_standings_list (wxDataViewCtrl — the Solo page, the notebook's second tab) · standings_list (wxDataViewCtrl — a SOLO-only ride's one list, standing in the notebook's place; the view shows the notebook or this list, never both. All three carry the same six columns — G6's Place · Plate · Entry · Laps · Best 5 · Hand, each pinned to its own width (60 / 50 / 160 / 50 / 160 / 210, the Team list's Hand taking 260 when its Plate column is hidden) — and rank their own kind from 1, DNF entrants excluded outright — a kind absent from the ride simply has no rows. The Teams list hides its Plate column on a `rider_pooled` ride (a pooled team's plate derives from its members, so the column would only repeat one); it stays visible under `team_relay` and on the solo list. The three lists' columns are natively sortable and resizable (DATAVIEW_COL_SORTABLE | DATAVIEW_COL_RESIZABLE — both bits spelled out, since an explicit flags= replaces AppendTextColumn's own default; the platform's own header arrow); Place and Laps sort on their integer values, never as strings)`
Export HTML…Export PDF…Podium poster…Export CSV…Close

`export_html_btn · export_pdf_btn · poster_btn · export_csv_btn — all four are disabled until the ride is FINISHED, the same gate the four Results-menu export rows carry (one handler serves the menu row and the button) · wxID_CLOSE (the four custom ids keep this row a plain sibling wxBoxSizer — wxStdDialogButtonSizer positions only the stock Close)`

⚠ code-side: standings rows — the presenter feeds each list its own kind (the notebook's two pages on a MIXED ride, the lone standings_list on a SOLO one, Phase 3, R-65); "draw required" tie rows highlighted with a ⚠ badge in the Place cell (the six pinned columns), the badge explained on activation — double-click or Enter opens an OK-only `std_dialogs.show_info` alert carrying the row's own tie note ("draw required" plus the venue-draw sentence), since a DataViewCtrl has no per-row tooltip and hover is keyboard-unreachable; stale-export flag banner (wxInfoBar stale_infobar — built in code, named with SetName()) after reopened corrections. G6 moved the five publish options out of this dialog onto checkable Results-menu items (each persisting in `AppSettings.publish_*`), so the dialog carries no checkbox and the show-times setting no longer hides any column here. The window is a modal wxDialog opened through dialogs.run_dialog — a transient over the console, so its title bar carries the ✕ alone (no ▢), and the tie-break panel and Reopen ride button left with the frame: tie-break order is ride_setup_dlg's own tiebreak_list and nothing else, and reopening a ride is the menu's Ride ▸ Reopen Ride (mi_reopen_ride). Standings is always available — the Results ▸ Standings row (F5) is never gated on a ride open; with none open the dialog renders its empty state. The Teams list hides its Plate column under `rider_pooled` (a pooled team's plate is derived from its members, so the column would only repeat one) and keeps it under `team_relay`, where the plate is the entry's identity; the solo list keeps it always. It floors at 740 px wide (MIN_SIZE, code-side: the six pinned columns total 690 px plus the list's scrollbar and the notebook/sizer borders — the publish-checkbox row that used to set the 755 px floor is gone), with each of the three standings lists (`standings_list`/`teams_standings_list`/`solo_standings_list`) floored at 198 px high — a 28 px header plus 10 × 17 px rows (measured on wxPython 4.3.1), so ten standings rows are visible. The window lives in results.xrc (§15b).

Ride Library`ride_library_dlg`✕

| Ride | Date | Status | Entries |
|---|---|---|---|
| GORBA EPIC 2026 | 2026-09-20 | RUNNING | 180 |
| Club poker night | 2026-06-11 | FINISHED | 24 |

`rides_list (wxDataViewCtrl · the four columns carry fixed default widths — Ride 240, Date 110, Status 110, Entries 70 — and stay natively sortable and user-resizable (DATAVIEW_COL_SORTABLE | DATAVIEW_COL_RESIZABLE, sorted through RidesListModel.Compare); the earlier elastic Ride column is retired — it stretched the name to every spare pixel and pushed Entries out of the dialog`

OpenDuplicate…Delete…Close

`wxID_OPEN · duplicate_btn · wxID_DELETE (never on RUNNING) · wxID_CLOSE — only wxID_CLOSE is positioned by wxStdDialogButtonSizer; the rest share a sibling wxBoxSizer (Spec §15b). wxID_NEW is removed (File ▸ New Ride… owns the setup flow); the dialog floors at 610×220 (MIN_SIZE, code-side — Phase 6 widened it from 560 so the four pinned column widths plus the button row fit), and every row comes from the store, so the Status column shows each ride's persisted status (draft/running/finished/reopened) with no replay.`

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

`audit_list (wxDataViewCtrl · newest first · fixed opening column widths When 90 · Who 120 · Action 180 · Entry 120 · Reason 330, every column still user-resizable)`

Close

⚠ code-side (Phase 6): the dialog floors at 1000×600 (`MIN_SIZE` — about 2× its measured content at the pinned widths above), applied with `SetMinSize` + `Fit` and clamped to the display's work area, since XRC has no window-level minsize (audit.xrc declares no <size>).

E · System & help

Settings`settings_dlg`✕

Appearance
 System
 Light
 Dark

`appearance_system_radio (default) · appearance_light_radio · appearance_dark_radio — the single theme surface since W13 (the View-menu theme trio left); all three live on both platforms: the 4.3.1 / wxWidgets 3.3.3 baseline supplies wx.App.SetAppearance, so Dark is never disabled and there is no capability hint`

 Sound on crossing (recorded / flagged / error cues)
 Show Total Times on Crossings Panel
 Show Lap Time in Crossings Panel
 Verbose logging (write a diagnostic log for support)
 Avg Lap Time (kmh) 12.0

`sound_chk · show_total_times_chk · show_lap_time_chk · verbose_log_chk (F1 — a stored setting rendered at open and collected on OK, like sound_chk; on by default, it feeds ui.logging.Logging, the opt-out NDJSON trace rivercrossing-<timestamp>.log — one file per launch, pruned to the last 20, and the app's one log since the separate plain-text crash log retired in A2) · avg_speed_spin (a decimal wxSpinCtrlDouble, captioned "Avg Lap Time (kmh)" — the authored caption, even though the value is the average rider speed in km/h — <min>1</min>, one decimal, <inc>0.1</inc>, opening on 12.0 — the card-sufficiency estimate's speed, §4; the stored value's own 1 km/h floor lives in the presenter). The two time-column checkboxes are stored settings like sound_chk and each mirror its own View-menu item (R-37): show_total_times_chk opens on the presenter's default (Total hidden) and show_lap_time_chk on its default (lap time shown); neither declares <checked>. The "Back up now" button left this dialog in Phase 1: the manual backup keeps exactly one surface, File ▸ Back Up Database… (`mi_backup_now`, R-54), so the dialog's only buttons are the stock pair`

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
| F2 | Edit crossing (open detail) |
| Delete | Delete selected crossing |
| Ctrl+D | Delete selected crossing |
| Ctrl+E | Edit crossing plate |

`shortcuts_list (wxDataViewCtrl · read-only; rows filled in code from the accelerator table — cannot drift)`

Close

`wxID_CLOSE`

⚠ code-side (Phase 6): the dialog pins its two columns to SHORTCUT_COLUMN_WIDTHS (120/320 px) — an unpinned DataView column keeps the platform's 80 DIP default and clips the Action text on first open — and floors itself at SHORTCUTS_MIN_SIZE (480×300) with SetMinSize + Fit() in code, since XRC declares no window-level minsize.

Evaluator Self-Test`selftest_dlg`✕

7,462 distinct ranks ........ PASS
Joker vector table (28) ..... PASS
Five-of-a-kind ordering ..... PASS
Whole-field 180×12 timing ... 0.31 s PASS
`selftest_output (read-only wxTextCtrl, monospace) · rerun_btn`

Run againClose

F · Crossing detail

Crossing Detail`crossing_detail_dlg`✕ — the nine values are read-only wxTextCtrl entry boxes (G2), never labels, in one "Details" wxStaticBoxSizer holding four columns in a row: caption, value, caption, value

Rider`crossing_rider_lbl` — the rider whose plate was typed (`Crossing.rider_plate` resolved through the roster; a `team_relay` crossing falls back to the entry's display name)
Team`crossing_team_lbl` — the team's `display_name`, or `solo` for a solo entry (never blank — the word the crossings feed's Team cell shows)
Plate`crossing_plate_lbl` — the typed rider's own plate
Lap #`crossing_lap_lbl` — the crossing's per-entry lap number (`Crossing.seq`)
Crossing time`crossing_time_lbl` — `crossed_at`, local 24-hour `HH:MM:SS`
Lap time`crossing_lap_time_lbl` — spec §6's derived lap time (`m:ss`, `h:mm:ss` past an hour — the feed's own format)
Total time`crossing_total_lbl` — the entry's running total (`h:mm:ss`)
Card`crossing_card_lbl` — the dealt card's glyph (`format_card`); a held crossing shows the real held code, as the feed row does (R-34)
Held / flagged`crossing_held_lbl` — "Held — short lap awaiting review" (R-34), "Credited", or "Voided" (`RideEngine.held_card_for` / `credited_cards`)

Edit PlateEdit Time…Void Card…DeleteOK

`edit_btn ("Edit Plate" — crossing mode opens the crossing_number_dlg Save/Cancel Plate prompt and re-renders the detail in place; miss mode opens the same prompt blank and stores/shows the typed plate) · edit_time_btn ("Edit Time…" — opens edit_crossing_dlg in edit mode through run_edit_crossing and commits RideEngine.edit_crossing; the retired Reassign Plate…/Void Card… menu rows' real home, Phase 2) · void_card_btn ("Void Card…" — opens void_card_confirm_dlg through run_void_card and voids the crossing's own **credited** card; a held card stays the review surface's) · delete_btn ("Delete" — any crossing of a RUNNING/REOPENED ride: the newest runs undo_last, any other void_crossing, both after the danger confirm) · wxID_OK (default, the only stock button — the window carries no wxID_CANCEL, so CrossingDetailView points Escape at OK with SetEscapeId; only wxID_OK, and delete_btn on a successful delete, close the window — edit_btn, edit_time_btn and void_card_btn re-render the detail in place) — edit_btn, edit_time_btn, void_card_btn and delete_btn sit in a sibling wxBoxSizer beside the stock sizer, since wxStdDialogButtonSizer positions only OK/Yes/Save/Apply/No/Cancel/Close/Help`

⚠ code-side (J2, the crossing-detail workstream): opened by double-clicking a row of the console's `crossings_list` — `MainFrame.set_on_open_crossing` fires that row's model index, and `app._feed_row_target` resolves that index against the presenter's own rendered rows (`rendered_feed_rows()`, the search filter included) back to the live `Crossing` or pending miss — Phase 4 retired the 30-row cap, so any row of the ride resolves, and only a stale activation after the feed shrank names nothing. Every value box is written by `views/crossing_detail.py`'s `CrossingDetailView` from the pure `build_fields(crossing, roster, engine)` view-model — the nine `crossing_*_lbl` read-only entry boxes are the window's value fields (the Team box reads `solo` for a solo entry, `_team_name`'s own rule, matching the feed's Team cell); the captions are fixed XRC copy and carry no frozen name. `edit_btn` reads "Edit Plate" and opens the §9 `crossing_number_dlg` Save/Cancel Plate prompt (`run_plate_dialog`), pre-filled with the current plate; Save hands the typed plate to `RideEngine.reassign_crossing(ordinal, new_plate, reason="crossing detail edit")` — addressed by the crossing's **ride-wide ordinal** in `engine.crossings`, deliberately not `Crossing.seq`, which is the per-entry lap number (the engine's own E7.1.1 note) — and re-renders the detail dialog in place from the updated crossing, so the window stays open (Cancel does nothing); miss mode's Edit opens the same prompt blank and stores/shows the typed plate. `delete_btn` deletes any crossing of a live ride — enabled whenever the ride is RUNNING or REOPENED — and which engine command runs depends on *which* crossing it is: the newest runs `RideEngine.undo_last()` (the console's own Undo, its card restituted) and any other runs `RideEngine.void_crossing()` with `reason="crossing detail delete"` (card voided, the entry's later laps renumbered); either way the native `std_dialogs.show_danger` confirm runs first, names the crossing, and words the question to match the command. A refusal (blank or unknown plate, a ride that is not RUNNING/REOPENED) shows on the code-side `crossing_detail_infobar` (an `wxInfoBar` built with `SetName()`, the InfoBar rule) and leaves the dialog open; every commit travels through the engine, whose `on_event` sink persists it, and the console re-renders the feed on its 1 s tick. A pending miss opens the same window in **miss mode** (`MissDetailView`, K2): `build_miss_fields` renders the feed row's own placeholders — Plate and Rider `-`, Team `missed`, the signal instant as the crossing time, blank lap/lap time/total/card and "Not yet scored" for the card state — and Edit opens the blank Plate prompt whose Save assigns the typed plate through `RideEngine.assign_plate_to_miss(miss_seq, plate, reason="miss detail edit")`, which records the crossing and deals its card at the miss's own instant; Delete stays disabled (a miss is not `engine.crossings[-1]`). The window lives in dialogs.xrc (§15b).

Plate`crossing_number_dlg (wxDialog)`

Plate`number_input (wxTextCtrl · <size>50,-1</size> — the four-digit plate field, seeded with the crossing's current plate, focused and selected on open)`

SaveCancel

`number_input · wxID_OK ("Save", default) · wxID_CANCEL — the stock std sizer and stock ids (§13, UX-DESKTOP §3); the window's "Plate" caption is an unnamed <label>, never a frozen name`

⚠ code-side (§9): the crossing-mode Edit prompt. `views/crossing_detail.py`'s `run_plate_dialog` shows this over the crossing detail window, seeds and selects `number_input`, and returns Save's trimmed text — nothing here resolves the plate; `RideEngine.reassign_crossing` owns the blank/unknown-plate refusal, so the prompt never duplicates the roster's own rules. Cancel (and Escape, which the stock `wxID_CANCEL` already routes) returns None. The window lives in dialogs.xrc (§15b).

G · Rider Simulator

Rider Simulator`simulation_dlg`✕

Number of riders 175
Number of teams 40
Solo riders 15
Number of laps 1
Minutes between first rider 45
Short-lap riders 1
Lapped riders Disabled
Team riders stop after 4 laps Disabled

Generate RidersCheckGOCancel

`riders_spin · teams_spin · solo_spin · laps_spin · interval_spin · short_lap_choice · lapped_choice · team_stop_choice — the five count fields (wxSpinCtrl: riders 1–1000 @ 175, teams 1–100 @ 40, solo 0–1000 @ 15, laps 1–1000 @ 1, interval 1–240 minutes @ 45) — each a stored setting (`sim_riders`/`sim_teams`/`sim_solo`/`sim_laps`/`sim_interval` in settings.json): the dialog seeds them from the stored values on open and the app persists them on close; solo is auto-computed from riders and teams (`resolve_solo` — teams fill first, the remainder rides solo) and the interval opens on the speed-derived default — one lap at the ride's average speed plus a five-minute buffer, `default_interval_minutes(lap_km, avg_speed_kmh)` seeding `round(lap_km / avg_speed_kmh × 60) + 5` minutes (the demo 8 km / 12 km/h ride opens on 40 + 5 = 45) · short_lap_choice ("Short-lap riders"), lapped_choice ("Lapped riders") and team_stop_choice ("Team riders stop after 4 laps") — the G9 behaviour dropdowns, each a wxChoice whose item 0 is "Disabled" and items 1..10 are the counts themselves, so the selected index is the count; the authored defaults are one short-lap rider and the other two Disabled, and each is a stored setting (`sim_short_laps`/`sim_lapped`/`sim_team_stop` in settings.json) seeded back on open and persisted on close · gen_riders_btn ("Generate Riders") · check_btn ("Check" — shows the riders/teams/solo relationship in a native OK-only `std_dialogs.show_info`, or `show_warning` naming how to fix it when the counts cannot work: the operator asked a question, so the answer is a system modal, never a transient InfoBar cue) · go_btn ("GO" — the dialog's default button, so Enter runs the race) · wxID_CANCEL — the three custom ids (gen_riders_btn, check_btn, go_btn) keep this row a plain sibling wxBoxSizer, since wxStdDialogButtonSizer positions only the stock Cancel (and leaves wx its own Escape)`

⚠ team riders must be between 4 and 20, got 3`sim_infobar (wxInfoBar — built in code and named with SetName(); the dialog reserves no slot, so the bar is inserted at sizer index 0 above the generator grid; XRC cannot author one; hidden by default)`

⚠ code-side (Rider Simulator): the field generator and the scripted race. "Generate Riders" is the dialog's one generator (the Generate Teams button retired): it creates the requested `TEAM-0001`-style empty teams first when the roster holds none, then `FIRSTNAME-0001`/`LASTNAME-0001` placeholders off one shared counter (unique by construction) — the solo entries first, so their plates sit below every team rider's, then the team riders round-robin, each with a random M/F sex, an auto-assigned plate on a pooled ride and none on a relay one. A MIXED ride's "Generate Riders" reads all three count fields; a SOLO-only ride has no teams to create, so teams_spin/solo_spin leave the dialog and the button generates solo entries only. A successful generation closes the dialog; on a refused count the presenter's own message lands on `sim_infobar` and the dialog stays open for a correction. A roster that already holds entries was loaded from a ride, so every generator control is disabled — generating would collide with the real riders — while the lap and interval fields stay live. "GO" validates the race settings (laps ≥ 1, interval ≥ 1 minute; a refusal lands on `sim_infobar` too), then starts the DRAFT ride and records deterministic lap times through the console's own `RideEngine.record_crossing(plate, at=…)` seam: the entry order is shuffled once from the run's seed and every lap reuses it, with one crossing per entry per lap — a relay team under its own plate, a pooled team under the one rider on course that lap (rotating round-robin), so a 4-rider team's lap count equals a solo's — lap 1 at the ride's start (elapsed 0), riders spread across whole minutes 0 … interval − 1, and at least a minute before the next lap opens at `actual_start + (L − 1) * interval`; the run calls `RideEngine.stop()` when it ends, leaving the ride stopped-RUNNING. The three G9 behaviour dropdowns bend that script, each selecting the leading entries of the run's one shuffled order so a seed still reproduces the whole run: short-lap riders cross impossibly fast (each lap at the entry's own previous recorded instant plus `min_lap_s − 30 s`, clamped at one second), so the engine flags their every lap and, under the hold policy, holds their cards; lapped riders sit out the first wave only and finish one lap short; and team riders stop after lap 4 — they record their first four laps and never cross again (out of the race, never a DNF), a pooled team whose rotation slot lands on a stopped rider sending the next active rider so the team still laps once per wave, while a relay team crosses under its own entry plate and the behaviour is a no-op there. One seed reproduces a whole run, and every mutation goes through the shipped `Roster`/`RideEngine` methods, so a simulated ride is the same ride the console records. Opened from File ▸ Simulation… (`mi_simulation`), a route enabled only when a ride is open and in DRAFT — a generated field only makes sense while the roster is still open for edits; it floors at 425 px wide (MIN_WIDTH, code-side: the generator row is wider than the label/field grid, and below it the Generate and GO buttons would be squeezed); the window lives in simulation.xrc (§15b).

Simulating…`sim_running_dlg`✕

Simulated 240 of 1 800`sim_status_lbl`

▰▰▰▰░░░░░░░░`progress_gauge (wxGauge · wxGA_HORIZONTAL · 0–100 percent complete)`

Cancel`cancel_btn`

⚠ code-side: `sim_running_dlg` is the modal progress window GO opens — the status line reads "Simulated <n> of <t> crossings" and the gauge holds `100 * completed // total`, both refreshed after every recorded crossing. The race runs on the main thread inside this dialog's own modal loop, with `wx.Yield()` after each crossing so the gauge repaints and a Cancel click is dispatched without waiting for the race to end; Cancel (or Escape, which the view points at it with SetEscapeId, since cancel_btn carries a custom id) ends the run early. The window lives in simulation.xrc (§15b).
