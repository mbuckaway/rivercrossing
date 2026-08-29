# RiverCrossing — XRC Window Designs

*RiverCrossing — XRC window designs (implementation truth · retires the Industry hi-fi mockups)  Native wxWidgets controls only, sizer-based, Windows look shown (identical structure on macOS). Every interactive control carries its `xrc_name` annotation — these are the XRC `name` attributes, canonical and frozen; standard buttons use stock IDs (wxID_OK, wxID_CANCEL, wxID_CLOSE, wxID_DELETE, wxID_EXIT, wxID_ABOUT). Naming rules + window↔file map: Spec §15b.  - Canvas caveats (HTML approximation): browser form controls stand in for wx natives; exact spacing/fonts come from sizers + system fonts, not these pixels; sizes below are minimums expressed in dialog units at build time. Global code-side items (not expressible in XRC): ① DataView columns + row data + per-row attributes (bold flagged rows, red suits) — appended in code, the attributes through a DataViewIndexListModel subclass overriding GetAttrByRow; ② card imagelist population (53 card bitmaps @1x/2x); ③ wxInfoBar construction + message text + Show/Hide calls; ④ splitter sash position restore from settings; ⑤ menu enable/disable per ride state (§15); ⑥ theme: wx.App.SetAppearance on the 4.3.1 / wxWidgets 3.3.3 baseline — all three appearance radios live on both platforms (measured: macOS applies at runtime to existing windows; Appearance::System pins the NSAppearance current at the call instead of restoring follow-the-system, so the app re-applies System on wx.EVT_SYS_COLOUR_CHANGED, best-effort; MSW on 3.3.3 returns CannotChange once a top-level window exists, so a Windows theme change takes effect at next launch and the status bar says so); ⑦ window minimum sizes via SetMinSize(); ⑧ radio menu-item defaults mi_theme_system and mi_zoom_100. Three classes cannot be authored in XRC at all — wxInfoBar and wxMenuBar drop their name attribute, wxDataViewListCtrl's handler forces the name dataviewCtrl, so every list below is a wxDataViewCtrl (Spec §15b). Everything else drawn here is declared in XRC.  A · Main frame  RiverCrossing — GORBA EPIC & MTB Festival 2026`main_frame`— ▢ ✕  FileRideRidersCardsResultsViewHelp`main_menubar (the resource id LoadMenuBar() loads by — not FindWindowByName) · items mi_ — see §15b`  ⓘ This ride was running when the app closed. Continue timing on wall clock?Continue rideOpen library`resume_infobar · reopened_infobar (wxInfoBar — built in code and named with SetName(); XRC cannot author one; hidden by default)`  GORBA EPIC & MTB Festival 2026 `ride_name_lbl`  RUNNING `ride_status_lbl`  4:22:41 `clock_elapsed_lbl` 1:37:19 to close `clock_remaining_lbl`  Start ride  ArmStop ride…  `start_btn · arm_stop_chk · stop_btn`  Record crossing (the entry row is framed by a native wxStaticBoxSizer so the operator can always find it — Phase 8)  Plate`plate_input (focused · larger type via <font><sysfont>wxSYS_DEFAULT_GUI_FONT</sysfont><relativesize>1.5</relativesize></font> — relative so the 90–150% zoom still applies, never an absolute point size · wider DIP <size> · <hint> "Plate number")` Record (Enter)`record_btn` ✓ 123 · Sam Ellis · Lap 4 · 22:41 · dealt 9♥`last_crossing_lbl` Undo last (Ctrl+Z)`undo_btn`  | Time | Plate | Entry | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 14:22:41 | 123 | Sam Ellis | 4 | 22:41 | 1:31:04 | 9♥ | | 14:22:18 | 77 | Trail Blazers (T) | 9 | 19:55 | 3:02:11 | K♠ | | 14:21:59 | 45 | J. Okafor | 6 | 07:12 ⚑ | 2:44:30 | held | | 14:21:30 | 212 | M. Chen | 5 | 24:02 | 2:10:44 | JK★ | | 14:20:52 | 8 | R. Dubois | 7 | 21:17 | 2:58:03 | 4♦ |  `crossings_list (wxDataViewCtrl · newest first · last 30)`  Crossings 1 124  Cards dealt 1 092  On course 42  Shoe 41/108  `crossings_count_lbl · cards_count_lbl · on_course_lbl · shoe_lbl` Needs review (1)  ⚑ 45 · lap 6 · 07:12 < min 12:00  `flagged_list (wxDataViewCtrl) · review_btn`  epic-2026.prdb  Saved 14:22:41  Shoe cycle 1 · seed 8843  `main_statusbar`  ⚠ code-side: feed columns/rows + flagged-row attrs (a DataViewIndexListModel subclass overriding GetAttrByRow — there is no setter); card column bitmaps from imagelist; InfoBar construction + text + show/hide; sash position (main_splitter); state variants — DRAFT: clock 0:00:00, start_btn enabled, plate_input disabled with "start the ride to record" hint (record_btn tracks plate_input's enablement in every state) · FINISHED: entry row hidden, result banner InfoBar (finished_infobar) with Reopen/Results buttons · REOPENED: corrections banner, entry disabled, edited rows highlighted. Hide-times setting removes Lap time/Total columns + times in last_crossing_lbl; clock stays. Min frame 1100×700, fits 1366×768 — declared as <size> and re-applied with SetMinSize(); Spec §13 now states the same figure.  B · Ride setup & lifecycle dialogs  Ride Setup`ride_setup_dlg`✕  Name DatePlanned start VenueLap length km OrganizerScorer Duration h:mMin lap m:s Logo  `name_input · date_picker · start_time_picker · venue_input · lap_km_spin · organizer_input · scorer_input · duration_input · min_lap_input · logo_picker` Entries  Solo riders only (default)  Solo + teams  Max riders per team (2–10)   Rider plates — pooled (default): each rider draws per lap, uncapped; team hand from pooled cards  Team plate — relay: one plate per team, one rider on course (EPIC)  `solo_radio · mixed_radio · team_size_spin · pooled_radio · relay_radio (enabled only when mixed_radio)`  Cards  Decks Jokers/deck:  0 2 4  Card cap  Tie-break order ① Most laps ② Total time ③ High-card draw ▲▼  `decks_spin · jokers_0_radio · jokers_2_radio (default) · jokers_4_radio · cap_chk · cap_spin · tiebreak_list (wxEditableListBox reorder arrows)`  OKCancel  `wxID_OK · wxID_CANCEL (wxStdDialogButtonSizer)`  ⚠ code-side: entry/plate-model group locks after start (relay) or stays editable (pooled, R-17); tiebreak_list reorder persisted (its ①②③ numbering here is illustration, not row text); decks_spin's value — the XRC declares none and the presenter supplies **8** (Spec §4; settled by the E3.5 ride-setup work — the canvas's 2 was a mock artifact). All fields plain XRC.  Set Start Time`set_start_dlg`✕  Started at  `start_date_picker · start_time_picker`  Lap-1 times recompute from this moment.  OKCancel  Stop Ride?`stop_confirm_dlg`✕  The clock stops for everyone. Riders still on course keep their laps; no cards are dealt after stop. You can continue the ride later without losing anything.  Stop rideCancel  `wxID_OK "Stop ride" · wxID_CANCEL (default + focused)`  Finish Ride?`finish_confirm_dlg`✕  Locks entry and computes final standings (evaluator self-test must be green). You can reopen later for corrections.  Finish rideCancel  `wxID_OK "Finish ride" · wxID_CANCEL (default)`  Ride Already Has Data`continue_or_new_dlg`✕  This ride was stopped with 1 124 crossings recorded. Continue it (keeps start time and all data) or archive and start fresh?  Continue rideArchive & start newCancel  `message_lbl (the crossing count is interpolated) · continue_btn (wxID_OK, default) · archive_new_btn · wxID_CANCEL`  Resume Ride`resume_dlg`✕  "GORBA EPIC 2026" is still running — it kept timing on the wall clock. (Wording swaps for crash: "The app closed unexpectedly…" — session_state)  Continue rideOpen library  `message_lbl (ride name + quit/crash wording interpolated) · continue_btn (wxID_OK, default) · library_btn`  Ride Is Running`exit_running_dlg`✕  Quitting won't stop the ride — it keeps timing on the wall clock, and you'll be asked to continue when you reopen.  CancelFinish ride first…Quit — keep ride running  `wxID_CANCEL (default + focused) · finish_first_btn · wxID_OK "Quit — keep ride running"` — three buttons per Spec §15 and R-51, ordered per §13 Ghost · Secondary · Primary; the canvas drew two.  Quit RiverCrossing?`exit_confirm_dlg`✕  Are you sure you want to quit? No ride is running.  CancelQuit  `wxID_CANCEL (default + focused) · wxID_OK "Quit"` — added in EPIC 1 Phase 8: §15 and R-51 originally said "otherwise quits"; amended so the app never exits without confirmation (destructive-confirm per §13/R-76). On macOS the window ✕ hides the app (Dock click reopens it); on Windows ✕ runs the same confirm flow.  C · Riders, corrections & cards  Rider Editor`rider_editor_dlg`✕  | Plate | Name | Team | |---|---|---| | 123 | Sam Ellis | — | | 77 | A. Roy | Trail Blazers | | 78 | K. Singh | Trail Blazers | | 212 | M. Chen | — |  `riders_list (wxDataViewCtrl · Team col hidden in solo-only)`  Rider  Plate Name Team— solo —Trail BlazersNew team…  AddSaveDelete  `plate_input (next free) · name_input · team_choice · add_btn · save_btn · delete_btn`  Import CSV…Export CSV…  `import_btn · export_btn · wxID_CLOSE` Close  ⚠ code-side: list rows; team_choice content ("— solo —" · teams · "New team…", which prompts for a name with the native text-entry dialog); plate_input prefills the highest numeric plate + 1 (empty roster → 1); delete disabled once entry has data (post-start = DNF/void only, R-15); refused edits show on roster_infobar (wxInfoBar, code-side SetName, §15b); import_btn/export_btn run the same picker → preview/write flows as File ▸ Import/Export Riders CSV. Teams editable until start (relay) / during ride (pooled).  Import Riders — Preview`csv_preview_dlg`✕  riders.csv → 178 riders · 12 teams · 3 conflicts `summary_lbl`  | Row | Problem | |---|---| | 41 | Duplicate plate 77 | | 96 | Missing name |  `conflicts_list (wxDataViewCtrl)`  Nothing is written until you import. Re-import freely reshapes teams before start.  ImportCancel  `wxID_OK "Import" (disabled while conflicts > 0) · wxID_CANCEL`  ⚠ code-side: summary_lbl text + conflicts_list rows; the wxID_OK gate; a refused import shows on csv_infobar (wxInfoBar, code-side SetName, §15b). Opened from File ▸ Import Riders CSV… or the editor's import_btn, after the OS-native picker.  Entry Detail — 77 Trail Blazers`entry_detail_dlg`✕  - Team · 3 riders · 9 laps · 3:02:11 — A. Roy (77) · K. Singh (78) · L. Marchetti (79) `entry_header_lbl · members_lbl` Cards held (9)  9♥ K♠ K♣ JK★ 4♦…  `cards_list (wxDataViewCtrl · a DataViewBitmapRenderer column, not icon mode — that is a wxListCtrl feature and does not exist on DataView; AppendBitmapColumn's default renderer registers against wxBitmapBundle and silently drops a plain wx.Bitmap, so the column declares DataViewBitmapRenderer("wxBitmap") explicitly)`  | Lap | Time | Lap time | Rider | Card | |---|---|---|---|---| | 9 | 14:22:18 | 19:55 | 78 | K♣ | | 8 | 14:02:23 | 21:40 | 77 | JK★ |  `laps_list (wxDataViewCtrl)`  Edit crossing…Deal card…Void card…Move rider…Mark DNF…Audit trailClose  `edit_crossing_btn · deal_card_btn · void_card_btn · move_rider_btn (pooled only) · dnf_btn · audit_btn · wxID_CLOSE`  Edit Crossing / Add Crossing at Time`edit_crossing_dlg`✕  Plate Time Reason  `plate_input · time_picker · reason_input · void_btn (edit mode only)`  Void crossing…OKCancel  ⚠ one XRC dialog, two titles: Cards ▸ Edit / Add-at-Time set title + prefill in code; reason required, audit-logged.  Reassign Plate`reassign_dlg`✕  Crossing 14:21:59 · lap credited to 45 `crossing_lbl`  New plateReason  `new_plate_input · reason_input`  OKCancel  Deal Manual Card`manual_deal_dlg`✕  PlateReason  Deals the next card from the shoe — deterministic, audit-logged.  `plate_input · reason_input`  DealCancel  Mark DNF`dnf_confirm_dlg`✕  - 212 · M. Chen — keeps laps and cards, ranked in the DNF block. Reversible. `entry_lbl`  Reason  `reason_input`  Mark DNFCancel  `wxID_OK "Mark DNF" · wxID_CANCEL (default)`  D · Results, library, audit  Results — GORBA EPIC 2026 (FINISHED 16:02:11)`results_frame`— ▢ ✕  Tie-break: ① Most laps ② Total time ③ High-card draw ▲▼`tiebreak_list (re-rank live)`Reopen ride…`reopen_btn`  | Place | Plate | Entry | Laps | Total | Best 5 | Hand | |---|---|---|---|---|---|---| | 1 | 77 | Trail Blazers | 9 | 5:44:02 | K♠ K♣ K♦ JK★ 9♥ | Four of a Kind — Kings | | 2 | 123 | Sam Ellis | 8 | 5:51:17 | Q♥ J♥ T♥ 9♥ 8♥ | Straight flush, queen-high | | 3 | 8 | R. Dubois | 7 | 5:38:44 | A♣ A♦ A♥ 4♦ 4♠ | Full house, aces over fours |  `standings_list (wxDataViewCtrl · full field; DNF block last; card bitmaps via imagelist)` Publish options   Show lap & total times  Laps leaderboard  Fastest-time leaderboard  Full field  All cards drawn  `show_times_chk (off default; hides Total col here too) · laps_board_chk · time_board_chk · full_field_chk · all_cards_chk`  Export HTML…Export PDF…Podium poster…Export CSV…  `export_html_btn · export_pdf_btn · poster_btn · export_csv_btn`  ⚠ code-side: standings rows; "draw required" tie rows highlighted with a ⚠ badge column; stale-export flag banner (wxInfoBar stale_infobar — built in code, named with SetName()) after reopened corrections.  Ride Library`ride_library_dlg`✕  | Ride | Date | Status | Entries | |---|---|---|---| | GORBA EPIC 2026 | 2026-09-20 | RUNNING | 180 | | Club poker night | 2026-06-11 | FINISHED | 24 |  `rides_list (wxDataViewCtrl)`  OpenNew…Duplicate…Delete…Close  `wxID_OPEN · wxID_NEW · duplicate_btn · wxID_DELETE (never on RUNNING) · wxID_CLOSE — only wxID_CLOSE is positioned by wxStdDialogButtonSizer; the rest share a sibling wxBoxSizer (Spec §15b)`  Delete Ride`delete_ride_dlg`✕  Deletes "Club poker night" and all its data. A backup is written first. Type the ride's name to confirm:  `message_lbl (the ride's name is interpolated — UX-DESKTOP §4 requires naming the object) · confirm_name_input`  DeleteCancel  `wxID_DELETE (enabled on exact match) · wxID_CANCEL (default)`  Audit Trail`audit_dlg`✕  All actionsCrossing editsCard deals/voidsMovesDNFShoe reshuffle  `audit_search (wxSearchCtrl) · action_choice`  | When | Who | Action | Entry | Reason | |---|---|---|---|---| | 14:23:02 | scorer | Void crossing | 45 | mis-key | | 14:21:40 | scorer | Manual deal 7♦ | 45 | flag confirmed |  `audit_list (wxDataViewCtrl · newest first)`  Close  E · System & help  Settings`settings_dlg`✕  Appearance  System  Light  Dark  `appearance_system_radio (default) · appearance_light_radio · appearance_dark_radio — all three live on both platforms: the 4.3.1 / wxWidgets 3.3.3 baseline supplies wx.App.SetAppearance, so Dark is never disabled and there is no capability hint`   Sound on crossing (recorded / flagged / error cues)  Hide times on the console (toggle any time, even mid-ride)  Text zoom Back up now  `sound_chk · hide_times_chk · zoom_choice · backup_now_btn`  OKCancel  About RiverCrossing`about_dlg (Help ▸ wxID_ABOUT)`✕  `about_logo_bmp (ride logo, falls back to app icon)`  RiverCrossing 1.0.0 `version_lbl`  Timing & poker-hand scoring for poker-run rides. Built for GORBA — [gorba.ca](https://gorba.ca) `gorba_link (wxHyperlinkCtrl)`  Close  Keyboard Shortcuts`shortcuts_dlg (Help ▸ mi_shortcuts)`✕  | Key | Action | |---|---| | Enter | Record crossing for typed plate | | Ctrl+Z | Undo last crossing | | F5 | Standings (Results window) | | F1 | User guide |  `shortcuts_list (wxDataViewCtrl · read-only; rows filled in code from the accelerator table — cannot drift)`  Close  `wxID_CLOSE`  Evaluator Self-Test`selftest_dlg`✕  7,462 distinct ranks ........ PASS Joker vector table (28) ..... PASS Five-of-a-kind ordering ..... PASS Whole-field 180×12 timing ... 0.31 s PASS `selftest_output (read-only wxTextCtrl, monospace) · rerun_btn`*

RiverCrossing — XRC window designs (implementation truth · retires the Industry hi-fi mockups)

Native wxWidgets controls only, sizer-based, Windows look shown (identical structure on macOS). Every interactive control carries its `xrc_name` annotation — these are the XRC `name` attributes, canonical and frozen; standard buttons use stock IDs (wxID_OK, wxID_CANCEL, wxID_CLOSE, wxID_DELETE, wxID_EXIT, wxID_ABOUT). Naming rules + window↔file map: Spec §15b.

- **Canvas caveats (HTML approximation):** browser form controls stand in for wx natives; exact spacing/fonts come from sizers + system fonts, not these pixels; sizes below are minimums expressed in dialog units at build time. **Global code-side items (not expressible in XRC):** ① DataView columns + row data + per-row attributes (bold flagged rows, red suits) — appended in code, the attributes through a `DataViewIndexListModel` subclass overriding `GetAttrByRow`, since no setter exists; ② card imagelist population (53 card bitmaps @1x/2x); ③ wxInfoBar **construction** + message text + Show/Hide calls; ④ splitter sash position restore from settings; ⑤ menu enable/disable per ride state (§15); ⑥ theme: `wx.App.SetAppearance` on the 4.3.1 / wxWidgets 3.3.3 baseline — all three appearance radios live on both platforms (measured: macOS applies at runtime to existing windows; `Appearance::System` pins the NSAppearance current at the call instead of restoring follow-the-system, so the app re-applies System on `wx.EVT_SYS_COLOUR_CHANGED`, best-effort; MSW on 3.3.3 returns `CannotChange` once a top-level window exists, so a Windows theme change takes effect at next launch and the status bar says so); ⑦ window minimum sizes via `SetMinSize()` (XRC has no window-level minsize); ⑧ radio menu-item defaults `mi_theme_system` and `mi_zoom_100` (`<checked>` is a no-op on radio items). Everything else drawn here is declared in XRC.

- **Three classes cannot be authored in XRC** (measured on 4.3.1 / wxWidgets 3.3.3 — full detail in Spec §15b): **wxInfoBar** yields a generic `wx.Control` and drops its `name`, so the four info bars are built in code and named with `SetName()` · **wxDataViewListCtrl**'s handler hard-forces the name `dataviewCtrl`, so every list control below is a **wxDataViewCtrl**, whose name is honoured · **wxMenuBar** drops its name too: `main_menubar` loads via `XmlResource.LoadMenuBar()` and never resolves through `FindWindowByName`. Also measured: `wxStdDialogButtonSizer` positions only OK/Yes/Save/Apply/No/Cancel/Close/Help, so `wxID_OPEN`, `wxID_NEW`, `wxID_DELETE` and the custom buttons annotated below live in a sibling `wxBoxSizer`; a bare `&` in a label is a mnemonic and is stripped on macOS — author `&&` and read labels with `GetLabelText()`.

A · Main frame

RiverCrossing — GORBA EPIC & MTB Festival 2026`main_frame`— ▢ ✕

FileRideRidersCardsResultsViewHelp`main_menubar (the resource id LoadMenuBar() loads by — wxMenuBar drops its name, so it never resolves through FindWindowByName) · items mi_* — see §15b`

ⓘ This ride was running when the app closed. Continue timing on wall clock?Continue rideOpen library`resume_infobar · reopened_infobar (wxInfoBar — built in code and named with SetName(); XRC cannot author one; hidden by default)`

GORBA EPIC & MTB Festival 2026 `ride_name_lbl`

RUNNING `ride_status_lbl`

4:22:41 `clock_elapsed_lbl`
1:37:19 to close `clock_remaining_lbl`

Start ride

ArmStop ride…

`start_btn · arm_stop_chk · stop_btn`

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
Needs review (1)

⚑ 45 · lap 6 · 07:12 < min 12:00

`flagged_list (wxDataViewCtrl) · review_btn`

epic-2026.prdb

Saved 14:22:41

Shoe cycle 1 · seed 8843

`main_statusbar`

⚠ code-side: feed columns/rows + flagged-row attrs (a DataViewIndexListModel subclass overriding GetAttrByRow — there is no setter); card column bitmaps from imagelist; InfoBar construction + text + show/hide; sash position (main_splitter); state variants — DRAFT: clock 0:00:00, start_btn enabled, plate_input disabled with "start the ride to record" hint (record_btn tracks plate_input's enablement in every state) · FINISHED: entry row hidden, result banner InfoBar (finished_infobar) with Reopen/Results buttons · REOPENED: corrections banner, entry disabled, edited rows highlighted. Hide-times setting removes Lap time/Total columns + times in last_crossing_lbl; clock stays. Min frame 1100×700, fits 1366×768 — declared as <size> and re-applied with SetMinSize(); Spec §13 now states the same figure.

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
 Solo riders only (default)
 Solo + teams

Max riders per team (2–10)

 Rider plates — pooled (default): each rider draws per lap, uncapped; team hand from pooled cards
 Team plate — relay: one plate per team, one rider on course (EPIC)

`solo_radio · mixed_radio · team_size_spin · pooled_radio · relay_radio (enabled only when mixed_radio)`

Cards

Decks Jokers/deck:
 0 2 4
 Card cap

Tie-break order ① Most laps ② Total time ③ High-card draw ▲▼

`decks_spin · jokers_0_radio · jokers_2_radio (default) · jokers_4_radio · cap_chk · cap_spin · tiebreak_list (wxEditableListBox reorder arrows)`

OKCancel

`wxID_OK · wxID_CANCEL (wxStdDialogButtonSizer)`

⚠ code-side: entry/plate-model group locks after start (relay) or stays editable (pooled, R-17); tiebreak_list reorder persisted (its ①②③ numbering here is illustration, not row text); decks_spin's value — the XRC declares none and the presenter supplies **8** (Spec §4; settled by the E3.5 ride-setup work — the canvas's 2 was a mock artifact). All fields plain XRC.

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

Ride Already Has Data`continue_or_new_dlg`✕

This ride was stopped with 1 124 crossings recorded. Continue it (keeps start time and all data) or archive and start fresh?

Continue rideArchive & start newCancel

`message_lbl (the crossing count is interpolated) · continue_btn (wxID_OK, default) · archive_new_btn · wxID_CANCEL`

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
Name
Team— solo —Trail BlazersNew team…

AddSaveDelete

`plate_input (next free) · name_input · team_choice · add_btn · save_btn · delete_btn`

Import CSV…Export CSV…

`import_btn · export_btn · wxID_CLOSE`
Close

⚠ code-side: list rows; team_choice content ("— solo —" · teams · "New team…", which prompts for a name with the native text-entry dialog); plate_input prefills the highest numeric plate + 1 (empty roster → 1); delete disabled once entry has data (post-start = DNF/void only, R-15); refused edits show on roster_infobar (wxInfoBar, code-side SetName, §15b); import_btn/export_btn run the same picker → preview/write flows as File ▸ Import/Export Riders CSV. Teams editable until start (relay) / during ride (pooled).

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

Entry Detail — 77 Trail Blazers`entry_detail_dlg`✕

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

D · Results, library, audit

Results — GORBA EPIC 2026 (FINISHED 16:02:11)`results_frame`— ▢ ✕

Tie-break: ① Most laps ② Total time ③ High-card draw ▲▼`tiebreak_list (re-rank live)`Reopen ride…`reopen_btn`

| Place | Plate | Entry | Laps | Total | Best 5 | Hand |
|---|---|---|---|---|---|---|
| 1 | 77 | Trail Blazers | 9 | 5:44:02 | K♠ K♣ K♦ JK★ 9♥ | Four of a Kind — Kings |
| 2 | 123 | Sam Ellis | 8 | 5:51:17 | Q♥ J♥ T♥ 9♥ 8♥ | Straight Flush — Queen high |
| 3 | 8 | R. Dubois | 7 | 5:38:44 | A♣ A♦ A♥ 4♦ 4♠ | Full House — Aces over Fours |

`standings_list (wxDataViewCtrl · full field; DNF block last; card bitmaps via imagelist)`
Publish options

 Show lap & total times
 Laps leaderboard
 Fastest-time leaderboard
 Full field
 All cards drawn

`show_times_chk (off default; hides Total col here too) · laps_board_chk · time_board_chk · full_field_chk · all_cards_chk`

Export HTML…Export PDF…Podium poster…Export CSV…

`export_html_btn · export_pdf_btn · poster_btn · export_csv_btn`

⚠ code-side: standings rows; "draw required" tie rows highlighted with a ⚠ badge column (in the Place cell — the canvas pins seven columns); stale-export flag banner (wxInfoBar stale_infobar — built in code, named with SetName()) after reopened corrections; tie-break reorder re-ranks live through the control's own ▲▼ arrows (seeded from the ride's stored order as plain labels, same as ride_setup; a New/Delete-edited row set falls back to the known-good order with a status notice — E6.4.1); show_times_chk hides the Total column here too (R-63 UI proof).

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

`appearance_system_radio (default) · appearance_light_radio · appearance_dark_radio — all three live on both platforms: the 4.3.1 / wxWidgets 3.3.3 baseline supplies wx.App.SetAppearance, so Dark is never disabled and there is no capability hint`

 Sound on crossing (recorded / flagged / error cues)
 Hide times on the console (toggle any time, even mid-ride)

Text zoom Back up now

`sound_chk · hide_times_chk · zoom_choice · backup_now_btn`

OKCancel

About RiverCrossing`about_dlg (Help ▸ wxID_ABOUT)`✕

`about_logo_bmp (ride logo, falls back to app icon)`

RiverCrossing 1.0.0 `version_lbl`

Timing & poker-hand scoring for poker-run rides.
Built for GORBA — [gorba.ca](https://gorba.ca) `gorba_link (wxHyperlinkCtrl)`

Close

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
