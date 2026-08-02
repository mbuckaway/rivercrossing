# RiverCrossing — Hi-Fi UI Designs (RETIRED)

*- RETIRED (July 24 2026) — superseded as implementation reference by the native [XRC window designs](xrc-windows.md) (wxWidgets-native controls, canonical snake_case XRC names, Spec §15b). These Industry-styled mockups remain for flow/content history only — do not implement visuals or control names from them.  [8](#t8)Console states — before the start and after the finish (completes [1a](#1a); spec §13)  [8a](#8a)DRAFT — ride loaded, not started  RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sun Sept 20 2026 · Guelph Lake · 8 km loop  GORBA EPIC & MTB Festival  Planned start  10:00  6 h ride · window 10:00–16:00  DRAFT — not started  Set start time…  Start ride  Rider plate  —  Start the ride to record crossings  Roster is loaded and editable until the start — 180 entries · 262 riders (F3 to review). Start with the button at the gun, or record it late with Set start time.  Crossings  No crossings yet. The first rider across the line appears here with their lap and card.  Ride counters  0  crossings  0  cards dealt  0  on course  180  entries ready  Shoe — 8 decks · 2 jokers each  sealed432 cards  Shuffled with this ride's seed — first card deals on the first crossing  Pre-start checks  ✓ 180 entries, plates unique ✓ Evaluator self-test passed ✓ Backup written 09:41 — CSV re-import still allowed  ● rides.db ready (WAL) — every crossing will commit as it happens Ctrl+R starts the ride · F3 rider editor  [8b](#8b)FINISHED — ride over, results live  RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sun Sept 20 2026 · Guelph Lake · 8 km loop  GORBA EPIC & MTB Festival  Final time  6:00:00  10:00:12 → 16:00:12  ■ FINISHED 16:00:12  Reopen ride… Results (F5)  Ride finished — standings computed  Best hand: #88 Moss Ridge Riders — Four of a Kind, Nines · 1 tie resolved by laps · 0 held cards  Generate HTML Export PDF…  Crossings — final, read-only 1,124 recorded · corrections require Reopen  | Time | # | Entry / rider on course | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 15:58:41 | 88 | Moss Ridge Riders · Ben Alton | 11 | 29:17 | 5:58:29 | K ♥ | | 15:57:02 | 7 | Luca Ferrari · solo | 10 | 31:44 | 5:56:50 | 4 ♣ | | 15:55:24 | 61 | Marc Tremblay · solo | 10 | 30:58 | 5:55:12 | 2 ♠ | | 15:54:47 | 127 | Dirt Dynamos · Priya Nair | 10 | 32:40 | 5:54:35 | 9 ♥ | | 15:53:10 | 150 | Singletrack Sisters · Mia Chen | 9 | 33:05 | 5:52:58 | 6 ♦ | | 15:51:33 | 102 | Lake Effect · Nina Kovac | 9 | 34:21 | 5:51:21 | J ♣ |  Final counters  1,124  crossings  1,092  cards dealt  177  with a hand  3  DNF, listed  Most laps — final  #88Moss Ridge Riders11  #7Luca Ferrari10  #61Marc Tremblay10  #127Dirt Dynamos10  Published  ✓ epic-2026-results.html — 16:07 — PDF not yet exported  ● Ride archived in rides.db — reopen any time for corrections F5 standings · entry field disabled while finished  [8c](#8c)REOPENED — corrections mode (from [8b](#8b) ▸ Reopen ride…)  RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sun Sept 20 2026 · Guelph Lake · 8 km loop  GORBA EPIC & MTB Festival  Final time — unchanged  6:00:00  window closed 16:00:12  ◆ REOPENED 16:22 — corrections only  View audit trail… Finish again  Corrections mode — the clock is closed, the record is open  Live plate entry is off. Add a missed crossing with an explicit time, fix or void existing ones, deal or void cards — standings recompute after every change. Nothing finalizes until Finish again.  Add crossing at time… Edit crossing…Deal / void card…  Crossings — editable, changes highlighted 2 corrections this session, both audit-logged  | Time | # | Entry / rider on course | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 15:59:50 ADDED 16:24 | 33 | Peter Kim · solo | 8 | 36:12 | 5:59:38 | 8 ♠ | | 15:58:41 | 88 | Moss Ridge Riders · Ben Alton | 11 | 29:17 | 5:58:29 | K ♥ | | 15:57:02 | 7 | Luca Ferrari · solo | 10 | 31:44 | 5:56:50 | VOIDED 16:23 — double entry | | 15:55:24 | 61 | Marc Tremblay · solo | 10 | 30:58 | 5:55:12 | 2 ♠ | | 15:54:47 | 127 | Dirt Dynamos · Priya Nair | 10 | 32:40 | 5:54:35 | 9 ♥ |  ⚠ Published results are stale  epic-2026-results.html was generated at 16:07 — before this session's corrections (16:23, 16:24). Standings may have shifted.  Regenerate after finishing  This session  16:23 — voided #7 lap 10 double entry 16:24 — added #33 crossing 15:59:50 missed at the line Every change carries who · when · why.  ● Corrections committed as they happen — Finish again recomputes and re-locks Standings preview: #88 still P1 · tie P3/P4 unchanged  The one console, four states: [8a](#8a) DRAFT → [1a](#1a)/[2a](#2a) RUNNING → [8b](#8b) FINISHED ⇄ [8c](#8c) REOPENED. Try next: "start-of-day checklist dialog".  [7](#t7)Pooled rider plates + hidden-times console — external requirements adopted (extends [1c](#1c), [1d](#1d), [2a](#2a))  [7a](#7a)Ride setup — the Plate model choice (mixed rides only)  Entries — as it now appears in Ride setup  Entries  Solo only Solo + teams  Max riders per team  Plate model  Rider plates — pooled (default): every rider has their own plate and draws one card per lap, uncapped; the team's hand scores from the pooled cards Team plate — relay: one plate per team, one rider on course at a time (EPIC)  Pooled rides stay editable while running — riders can move between teams and their plate, crossings and cards move with them (audit-logged). CSV switches to one row per rider: plate, name, team_name.  [7b](#7b)Rider editor — pooled ride, grouped by team, editable mid-ride  Riders & teams — Club Poker Night (rider plates, pooled) — ● running –▢×  | Plate | Rider | Team | Cards |  | |---|---|---|---|---| | TEAM · DIRT DYNAMOS — 8 cards pooled |  |  |  |  | | 12 | Sarah Okafor | Dirt Dynamos | 3 | Move to team… | | 14 | Priya Nair | Dirt Dynamos | 3 | Move to team… | | 15 | Tom Hale | moved from Chain Gang · 11:24 | 2 | Move to team… | | TEAM · CHAIN GANG — 5 cards pooled |  |  |  |  | | 21 | Liz Warner | Chain Gang | 3 | Move to team… | | 22 | Omar Haddad | Chain Gang | 2 | Move to team… | | SOLO ENTRIES |  |  |  |  | | 31 | Marc Tremblay | — solo | 4 | Move to team… |  38 riders · 11 teams + 9 solos Moves are allowed while running on pooled rides — plate, crossings and cards travel with the rider; every move is audit-logged  [7c](#7c)Console with times hidden — Settings toggle, live mid-ride (compare [2a](#2a))  RiverCrossing — Arkell Fall Poker Run — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sat Oct 17 2026 · Arkell Spring Grounds · 6.5 km loop · solo entries · times hidden  Arkell Fall Poker Run  Riding window closes in  2:47:20  ● RUNNING  Arm Stop ride…  Rider plate — Enter records crossing  23  ↩ record · Esc clear · ⌘Z undo last  Last crossing  #23 · Hana Yoshida  Lap 3 · card 3 of 3  7 ♥  7 ♥  card dealt 7♥ — 3rd card shoe: 131 left  Undo last Edit crossing…  Crossings — latest first times hidden (Settings) — still recorded underneath  | # | Rider | Lap | Cards | Card dealt | |---|---|---|---|---| | 23 | Hana Yoshida | 3 | 3 | 7 ♥ | | 4 | Owen Clark | 3 | 3 | J ♠ | | 31 | Marc Tremblay | 3 | 3 | ★ JOKER | | 17 | Gita Rao | 2 | 2 | 2 ♣ | | 52 | Ana Souza | 3 | 3 | 10 ♦ | | 8 | Peter Kim | 2 | 2 | K ♣ | | 45 | Liz Warner | 3 | 3 | A ♥ | | 29 | Sam Ellis | 3 | 3 | 5 ♠ |  Ride counters  87  crossings  87  cards dealt  28  on course now  64/64  riders active  Shoe — 4 decks · 2 jokers each  dealt 87216 total  131 remaining · reshuffles automatically at 0  Most laps — unofficial  #31Marc Tremblay3  #45Liz Warner3  #23Hana Yoshida3  #29Sam Ellis3  #52Ana Souza3  ● Autosaved — every crossing committed to rides.db (WAL) Times hidden by Settings — lap & total columns return the moment it's toggled off  Plate model defaults to Rider plates — pooled; EPIC-style rides switch to Team relay at setup. Pooled mode + mid-ride moves + the all-cards export flag come from the organizer's prior-script requirements. Try next: "pooled-mode entry detail" · "all-cards row in the results page sample".  [6](#t6)User Guide outline — the bundled HTML doc behind Help ▸ User Guide (F1)  [6a](#6a)Guide structure — 10 chapters + 2 appendices, task-ordered like race day  docs/user-guide.html · bundled with the app · v1.0  RiverCrossing — User Guide  Opens in the browser from Help ▸ User Guide (F1). Chapters follow the day: set up → run → finish → publish.  1Getting started  Installing on Windows & macOS · first launch and the ride library · where rides.db lives · automatic backups · light & dark themes  2Setting up a ride  Name, date, organizer, scorer, logo · schedule & minimum lap time · solo-only vs solo + teams and max riders per team · the shoe: decks & jokers · card cap · tie-break order  3Riders & entries  The rider editor · plates · adding solos and teams · CSV import & export, column reference · the conflict report · reshaping teams up to the start · what locks when the ride starts  4Running the ride  Starting — the button, or Set Start Time if the gun was missed · recording crossings (plate + Enter) · the sounds · undo · how cards are dealt · short-lap review & held cards · relay teams in practice  5Fixing mistakes  Undo last crossing · edit a crossing · reassign a plate · void & manual cards · mark DNF · the audit log — why nothing is ever deleted  6Stopping, quitting & recovery  The Arm + Stop + confirm sequence · quitting with a ride running · resuming on reopen · crash recovery · why the wall clock means you can't lose time  7Finishing & standings  Finish ride · pre-publish checks · how ties are broken (and changing the rule after the finish) · reopening for corrections  8Publishing results  HTML export — options, the times toggle, posting the single file anywhere · PDF export & the podium poster · printing  9Poker, the run way  Hand rankings, best → worst (five of a kind → high card) · jokers are wild · best 5 of all your cards · what beats what, with examples · duplicates across entries  10Troubleshooting & FAQ  Missed the start · typed the wrong plate · rider crossed twice · shoe ran out · moving the database to another machine · restoring a backup  AAppendix A — Keyboard shortcuts  The full table, Windows and macOS columns  BAppendix B — CSV reference  Column tables for solo-only and mixed rides · example files · every conflict message and its fix  Format: one HTML file on the app's own tokens (light/dark aware), anchors per section so dialogs can deep-link Help buttons — e.g. the import report links straight to Appendix B. Screenshots come from the functional-test harness, so they regenerate with every release and never go stale.  Chapters mirror race day; every dialog's Help lands on its anchor. Try next: "write chapter 4 in full" · "quick-start one-pager for volunteers".  [5](#t5)PDF results layout — what rivercrossing.pdfexport (fpdf2) renders · spec §8b, same data as the HTML page  [5a](#5a)Page 1 — cover, podium, top ten (Letter, times shown)  Official results · poker run  GORBA EPIC & MTB Festival 2026  180 · 1,124 · 1,092  entries · laps · cards dealt  Sunday September 20, 2026 · Guelph Lake MTB Trails · 8 km closed loop · riding 10:00–16:00 Best 5-card hand from all cards held · jokers wild  Best hands — top 3  1  #88 Moss Ridge Riders  TEAM ×4 · 11 laps · 5:52:41  9♠9♦9♣★K♥  FOUR OF A KIND — NINES  2  #7 Luca Ferrari  SOLO · 10 laps · 5:41:03  A♠A♦★4♣4♥  FULL HOUSE — ACES / FOURS  3  #127 Dirt Dynamos  TEAM ×3 · 10 laps · 5:48:19  Q♠Q♦★9♥9♣  FULL HOUSE — QUEENS / NINES  Top ten  | P | # | Entry | Laps | Total | Best 5 cards | Hand | |---|---|---|---|---|---|---| | 1 | 88 | Moss Ridge Riders | 11 | 5:52:41 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND — NINES | | 2 | 7 | Luca Ferrari | 10 | 5:41:03 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE — ACES / FOURS | | 3 | 127 | Dirt Dynamos tie → laps | 10 | 5:48:19 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE — QUEENS / NINES | | 4 | 56 | Fat Tire Four | 9 | 5:12:44 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE — QUEENS / NINES | | 5 | 61 | Marc Tremblay | 10 | 5:44:56 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH — K HIGH | | 6 | 150 | Singletrack Sisters | 9 | 5:03:27 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT — 9 HIGH | | 7 | 143 | Chain Gang | 8 | 4:41:12 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND — SEVENS | | 8 | 12 | Ana Souza | 9 | 5:21:38 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR — KINGS & FIVES | | 9 | 102 | Lake Effect | 9 | 5:17:05 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR — TENS & FOURS | | 10 | 19 | Owen Clark | 8 | 4:52:50 | A♣ A♠ K♥ J♣ 8♦ | PAIR — ACES |  P3/P4 held identical hands — resolved by tie-break rule ① most laps (10 v 9). It's not a race: laps and times are unofficial. ★ = joker, shown as played.  Organizer: GORBA — J. Marsden · Scorer: D. Whitfield Page 1 of 6 · generated 16:07, Sept 20 2026 · RiverCrossing  [5b](#5b)Page 2 — optional boards, full field begins  GORBA EPIC & MTB Festival 2026 — Official results Sunday September 20, 2026  Most laps  | 1 | #88 | Moss Ridge Riders | 11 | 5:52:41 | |---|---|---|---|---| | 2 | #7 | Luca Ferrari | 10 | 5:41:03 | | 3 | #61 | Marc Tremblay | 10 | 5:44:56 | | 4 | #127 | Dirt Dynamos | 10 | 5:48:19 | | 5 | #12 | Ana Souza | 9 | 5:21:38 |  Unofficial — 8 km per lap.  Fastest — laps then time  | 1 | #88 | Moss Ridge Riders | 11 laps | 5:52:41 | avg 32:03 | |---|---|---|---|---|---| | 2 | #7 | Luca Ferrari | 10 laps | 5:41:03 | avg 34:06 | | 3 | #61 | Marc Tremblay | 10 laps | 5:44:56 | avg 34:29 | | 4 | #127 | Dirt Dynamos | 10 laps | 5:48:19 | avg 34:50 | | 5 | #102 | Lake Effect | 9 laps | 5:17:05 | avg 35:14 |  Most laps, shortest elapsed to last crossing.  Full field — ordered by hand  | P | # | Entry | Type | Laps | Total | Best lap | Best 5 cards | Hand | |---|---|---|---|---|---|---|---|---| | 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 5:52:41 | 27:59 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND | | 2 | 7 | Luca Ferrari | Solo | 10 | 5:41:03 | 28:30 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE | | 3 | 127 | Dirt Dynamos | Team ×3 | 10 | 5:48:19 | 30:52 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE | | 4 | 56 | Fat Tire Four | Team ×4 | 9 | 5:12:44 | 29:41 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE | | 5 | 61 | Marc Tremblay | Solo | 10 | 5:44:56 | 29:12 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH | | 6 | 150 | Singletrack Sisters | Team ×4 | 9 | 5:03:27 | 29:52 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT | | 7 | 143 | Chain Gang | Team ×2 | 8 | 4:41:12 | 30:06 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND | | 8 | 12 | Ana Souza | Solo | 9 | 5:21:38 | 31:44 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR | | 9 | 102 | Lake Effect | Team ×3 | 9 | 5:17:05 | 28:47 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR | | 10 | 19 | Owen Clark | Solo | 8 | 4:52:50 | 32:05 | A♣ A♠ K♥ J♣ 8♦ | PAIR | | 11 | 33 | Peter Kim | Solo | 7 | 4:31:19 | 33:58 | K♣ K♦ Q♠ 9♥ 6♣ | PAIR | | 12 | 168 | Grinder Bros | Team ×2 | 8 | 4:47:33 | 31:20 | J♥ J♦ A♠ 8♣ 3♠ | PAIR | | 13 | 71 | Gita Rao | Solo | 7 | 4:22:08 | 34:12 | 8♠ 8♥ K♣ 10♦ 4♥ | PAIR | | 14 | 94 | Ted Novak DNF | Solo | 4 | 2:37:43 | 31:07 | A♦ Q♣ 9♠ 5♥ | HIGH CARD |  Rows 15–180 continue on pages 3–6 at this density (≈ 44 rows per page). With times hidden, the Total and Best lap columns are omitted and the table re-widens.  Organizer: GORBA — J. Marsden · Scorer: D. Whitfield Page 2 of 6 · generated 16:07, Sept 20 2026 · RiverCrossing  [5d](#5d)One-page podium poster — for the prize table & clubhouse wall  Sunday September 20, 2026 Guelph Lake MTB Trails · 8 km loop  Best poker hands  GORBA EPIC & MTB Festival 2026  1  #88 Moss Ridge Riders  Team of 4 — Dev Patel · Jo Lindqvist · Casey Muir · Ben Alton · 11 laps  FOUR OF A KIND — NINES  9 ♠  9 ♠  9 ♦  9 ♦  9 ♣  9 ♣  ★JOKER  K ♥  K ♥  2  #7 Luca Ferrari  Solo · 10 laps  FULL HOUSE — ACES / FOURS  A♠ A♦ ★ 4♣ 4♥  3  #127 Dirt Dynamos  Team of 3 · 10 laps  FULL HOUSE — QUEENS / NINES  Q♠ Q♦ ★ 9♥ 9♣  It's not a race, it's a poker run — 180 entries · 1,124 laps · 1,092 cards dealt · ★ joker as played Generated 16:07 · RiverCrossing  [5e](#5e)Times hidden — same page 2 with the "Show lap & total times" flag off  GORBA EPIC & MTB Festival 2026 — Official results Sunday September 20, 2026  Most laps  | 1 | #88 | Moss Ridge Riders | 11 | |---|---|---|---| | 2 | #7 | Luca Ferrari | 10 | | 3 | #61 | Marc Tremblay | 10 | | 4 | #127 | Dirt Dynamos | 10 | | 5 | #12 | Ana Souza | 9 |  Unofficial — 8 km per lap. Order by laps, ties listed together.  Times not published  This export was generated with times hidden — it's a poker run, not a race. Lap and total time columns are omitted and the "Fastest" leaderboard cannot render without them, so it is left out automatically. Laps and hands are unaffected.  Full field — ordered by hand  | P | # | Entry | Type | Laps | Best 5 cards | Hand | |---|---|---|---|---|---|---| | 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND | | 2 | 7 | Luca Ferrari | Solo | 10 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE | | 3 | 127 | Dirt Dynamos | Team ×3 | 10 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE | | 4 | 56 | Fat Tire Four | Team ×4 | 9 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE | | 5 | 61 | Marc Tremblay | Solo | 10 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH | | 6 | 150 | Singletrack Sisters | Team ×4 | 9 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT | | 7 | 143 | Chain Gang | Team ×2 | 8 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND | | 8 | 12 | Ana Souza | Solo | 9 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR | | 9 | 102 | Lake Effect | Team ×3 | 9 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR | | 10 | 19 | Owen Clark | Solo | 8 | A♣ A♠ K♥ J♣ 8♦ | PAIR | | 11 | 33 | Peter Kim | Solo | 7 | K♣ K♦ Q♠ 9♥ 6♣ | PAIR | | 12 | 168 | Grinder Bros | Team ×2 | 8 | J♥ J♦ A♠ 8♣ 3♠ | PAIR | | 13 | 71 | Gita Rao | Solo | 7 | 8♠ 8♥ K♣ 10♦ 4♥ | PAIR | | 14 | 94 | Ted Novak DNF | Solo | 4 | A♦ Q♣ 9♠ 5♥ | HIGH CARD |  Rows 15–180 continue on the following pages. The wider Hand column absorbs the removed time columns.  Organizer: GORBA — J. Marsden · Scorer: D. Whitfield Page 2 of 5 · generated 16:07, Sept 20 2026 · RiverCrossing  [5f](#5f)All cards drawn — full-field fragment with the flag on (both exports)  Full field — ordered by hand · includes every card drawn (organizer option)  | P | # | Entry | Type | Laps | Best 5 cards | Hand | |---|---|---|---|---|---|---| | 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND | |  |  | All 11 cards, in draw order: 9♠ 9♦ 9♣ ★ K♥ 2♣ 5♦ 7♥ J♣ 3♠ 10♦ |  |  |  |  | | 2 | 7 | Luca Ferrari | Solo | 10 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE | |  |  | All 10 cards, in draw order: A♠ A♦ ★ 4♣ 4♥ 8♠ 2♦ 9♥ Q♣ 6♠ |  |  |  |  | | 3 | 127 | Dirt Dynamos | Team ×3 | 10 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE | |  |  | All 10 cards, in draw order: Q♠ Q♦ ★ 9♥ 9♣ 4♣ 7♦ 2♥ 10♠ J♦ |  |  |  |  |  Rendering rule, both exports: one muted sub-row per entry, cards in draw order; on rider-pooled rides each card carries the drawing rider's initials ("9♠ SO"). Density drops to ≈ 22 entries per PDF page with the flag on; the HTML sample ([light](../exports/epic-2026-results.html) · [dark](../exports/epic-2026-results-no-times.html)) has it switched on live.  [5c](#5c)Render notes for rivercrossing.pdfexport  - Geometry. Letter 8.5×11in (A4 selectable), 0.58in margins; footer rule + "Page n of N" on every page; page-2+ header is the one-line running title. Fonts. fpdf2 embeds TTFs: Barlow + Barlow Condensed (headings), and suit glyphs ♠♥♦♣★ from a bundled symbol-capable face (e.g. DejaVu Sans) — no system-font dependence, identical output on both platforms. Color. The Industry tokens as RGB: ink #1D1F20, steel #416180 for ♥♦ and hand names, deep steel #1D2D3D for the P1 plate — no red, prints clean in grayscale. Flags. Same export options as HTML: hide-times drops Total/Best-lap columns; boards render only if selected; corner registration marks drawn as two 11pt hairlines per corner. Determinism. Fixed creation metadata + embedded fonts ⇒ byte-stable output for the §11 golden-file test.  Pages mirror the HTML export ([1f](#1f) options apply to both); [5d](#5d) is a third export flag ("Podium poster") emitting a single celebratory page. Try next: "grayscale print check" · "poster with door-prize draw numbers".  [4](#t4)Exit & resume — quitting can never lose a running ride (pairs with crash recovery [1h](#1h))  [4a](#4a)Exit caught while running · resume on next open  Quit — the ride is still running  GORBA EPIC & MTB Festival is running: 2:41:07 elapsed, 214 crossings saved. The clock is wall time — quitting loses nothing, and the ride continues on its own. When you reopen, you'll be asked to pick it back up.  The database records this as a clean quit with the ride running, so reopening knows exactly where you left off.  CancelFinish ride first…Quit — keep ride running  Welcome back — the ride is still running  You quit at 12:41:22 with GORBA EPIC running. The clock never stopped — elapsed is now 2:47:35, all 214 crossings intact. Continue where you left off?  Ride libraryContinue ride  Same resume dialog either way in — only the copy differs: a clean quit reads "You quit at…", a crash ([1h](#1h)) reads "closed unexpectedly at…". The database tells them apart by session bookkeeping, not guesswork.  Flow: × or File ▸ Exit while RUNNING → exit dialog → quit keeps the ride live (wall clock) → next launch always opens the resume dialog. Try next: "auto-resume without asking, with an undo bar" · "show the Finish-first path".  [3](#t3)Menu audit — every menu item's screen or dialog now exists (completes [2c](#2c); File/Export & Back Up use OS-native pickers)  [3a](#3a)Settings — File ▸ Settings…  Settings –▢×  Appearance  System Light Dark  Mirrors View ▸ Theme — one setting, two places  Console  Operator name (written to the audit log)  ✓Sound on recorded crossing Hide times on the console — toggleable while a ride runs; times are still recorded  Database  C:\Users\scorer\PokerRunTracker\rides.dbChange…Back up now  ✓Automatic backups — on open and hourly while running, keep last 20  Close  [3b](#3b)Add / edit entry + Mark DNF — Riders menu  Add entry  Plate  Entry name  Type  Solo Team  Riders on team  Riders — one field per seat  Opens with Solo selected and focus in the Plate field (shown here in its Team state). Plate prefilled with the next free number; rider fields match the team size — 2 up to this ride's max (setup allows up to 10). Solo-only rides show plate + name only. Locked once the ride starts.  CancelAdd entry  Mark #45 DNF?  Liz Warner keeps her 3 laps and 3 cards — listed and marked DNF 11:20. Reversible from the editor.  CancelMark DNF  [3c](#3c)Corrections — Cards ▸ Edit crossing · Reassign plate · Deal manual card  Edit crossing — #127 lap 4  Crossed at  Card  Void this crossing (card voids with it)  Reason — required, goes to the audit log  Later lap times for this entry recompute automatically.  CancelSave  Reassign crossing — 12:37:55  Recorded for #94 Ted Novak (flagged: short lap).  Move to plateBecomes #49 Gita Rao — lap 5 · lap time 35:40 · card moves with it, flag clears  ReasonCancelReassign  Deal manual card  Entry  Next card from the shoe (auditable) Specific card: Q ♠  Reason — required  CancelDeal card  [3d](#3d)Ride control confirms — Ride ▸ Stop · Set start time · Finish · Duplicate (File)  Stop the ride?  Reached by arming the checkbox beside Stop first — two deliberate steps, so it can't be hit by accident. Locks the entry field; the clock is wall time, so stopping loses nothing. Start again any time — you'll be asked to continue.  CancelStop ride  Set start time  Missed the gun? Back-date the start; lap-1 times recompute.  Actual start  CancelSet start  Finish the ride?  Ends at 16:00:00, runs the hand algorithm and opens Results. 1 held card needs review first. Reopen is always available.  CancelFinish ride  Duplicate ride  Copies setup and the full rider list — no timing data.  New ride name  CancelDuplicate  Reopen Ride uses the same confirm pattern ("Reopen for corrections? Standings recompute on export"), as does Delete ride — destructive form: type the ride's name to enable the button, focus starts on Cancel, a backup is written first. Undo Last Crossing acts directly — status-bar notice, no dialog.  [3e](#3e)CSV import report — File ▸ Import Riders CSV… (after the OS file picker)  Import epic-roster-final.csv  12  new entries  165  updated in place  3  conflicts — skipped  | Row | Plate | Problem | |---|---|---| | 41 | 88 | Duplicate plate within the file (rows 41 and 96) | | 77 | 143 | type=team3 but only 2 rider columns filled | | 102 | 61 | Structural change (solo → team2) — ride is RUNNING; name fixes only |  Nothing has been written yet. Import applies the 177 clean rows; conflicts are saved to a report you can fix and re-import — repeat freely until the ride starts.  CancelSave conflict report…Import 177 rows  [3f](#3f)Help — Keyboard shortcuts · Evaluator self-test · About  Keyboard shortcuts  Record crossingEnter Refocus plate entrySpace Undo last crossingCtrl+Z Clear entryEsc Rider editorF3 Entry detailF4 StandingsF5 Start / stop rideCtrl+R / Ctrl+. Import CSVCtrl+I SettingsCtrl+,  macOS: Ctrl reads as ⌘.  Close  Hand evaluator self-test  PASSED — 0.41 s  ✓ 7,462 natural ranks verified against the local table ✓ 214 joker vectors (incl. five of a kind, wheel) ✓ Determinism: seed replay reproduces the EPIC 2025 deal ✓ Cap-X and tie-break fixtures  Runs automatically at every launch; this menu item re-runs it on demand.  Close  RiverCrossing  Version 1.0.0 (build 2026-07-23) Python 3.14 · wxPython 4.2.2 · SQLite 3.46 Database: rides.db · 4 rides  Poker-run timing & scoring, built for the [GORBA EPIC](https://gorba.ca/events/gorba-epic/)  Licenses…Close  Every 2c menu item now lands somewhere: see the menu → screen map in the spec. OS-native dialogs (file pickers for Export CSV, Back Up, Generate HTML) are deliberately not custom. Try next: "toast/status-bar notice designs" · "User Guide outline".  [2](#t2)Solo-only mode — the default; team UI disappears (riffs on [1a](#1a) + [1d](#1d))  [2a](#2a)Main timing console — solo-only ride, light  RiverCrossing — Arkell Fall Poker Run — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sat Oct 17 2026 · Arkell Spring Grounds · 6.5 km loop · solo entries  Arkell Fall Poker Run  Elapsed  1:12:40  Remaining  2:47:20  ● RUNNING — started 10:30:05  ✓Arm Stop ride…  Rider plate — Enter records crossing  23  ↩ record · Esc clear · ⌘Z undo last  Last crossing — 11:42:45  #23 · Hana Yoshida  Lap 3 · lap time 24:12 · total 1:12:40  7 ♥  7 ♥  card dealt 7♥ — 3rd card shoe: 131 left  Undo last Edit crossing…  Crossings — latest first showing 9 of 87 · scroll for more  | Time | # | Rider | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 11:42:45 | 23 | Hana Yoshida | 3 | 24:12 | 1:12:40 | 7 ♥ | | 11:42:19 | 4 | Owen Clark | 3 | 25:01 | 1:12:14 | J ♠ | | 11:41:50 | 31 | Marc Tremblay | 3 | 23:40 | 1:11:45 | ★ JOKER | | 11:41:12 | 17 | Gita Rao | 2 | 36:28 | 1:11:07 | 2 ♣ | | 11:40:41 | 52 | Ana Souza | 3 | 24:55 | 1:10:36 | 10 ♦ | | 11:40:02 | 8 | Peter Kim | 2 | 35:12 | 1:09:57 | K ♣ | | 11:39:33 | 45 | Liz Warner | 3 | 23:58 | 1:09:28 | A ♥ | | 11:38:57 | 29 | Sam Ellis | 3 | 24:36 | 1:08:52 | 5 ♠ | | 11:38:20 | 11 | Dev Patel | 2 | 34:47 | 1:08:15 | Q ♦ |  Ride counters  87  crossings  87  cards dealt  28  on course now  64/64  riders active  Shoe — 4 decks · 2 jokers each  dealt 87216 total  131 remaining · reshuffles automatically at 0  Most laps — unofficial  #31Marc Tremblay3  #45Liz Warner3  #23Hana Yoshida3  #29Sam Ellis3  #52Ana Souza3  ● Autosaved 11:42:45 — every crossing committed to rides.db (WAL) Solo-only ride: no team columns, no rider-on-course tracking · Space refocuses plate entry  [2b](#2b)Rider editor — solo-only ride (compare [1d](#1d))  Riders — Arkell Fall Poker Run –▢×  Search name or plate…  Import CSV  Export CSV  Add rider  | Plate | Rider | Status |  | |---|---|---|---| | 4 | Owen Clark | Active |  | | 8 | Peter Kim | Active |  | | 11 | Dev Patel | Active |  | | 23 | Hana Yoshida | Active |  | | 17 | Gita Rao | Active |  | | 29 | Sam Ellis | Active |  | | 31 | Marc Tremblay | Active |  | | 45 | Liz Warner | DNF 11:20 |  | | 52 | Ana Souza | Active |  |  64 riders · next free plate 65 CSV: plate, name, notes — type & rider_1…4 columns are ignored on solo-only rides (reported, not fatal)  [2c](#2c)Menu system — identical structure on Windows & macOS  RiverCrossing –▢×  FileRideRidersCardsResultsViewHelp  Start RideCtrl+R  Stop Ride…Ctrl+.  Set Start Time…Ctrl+T  Finish Ride…Ctrl+F  Reopen Ride  Ride Setup…Ctrl+E  client area — console below the bar  Open state: accent highlight, shortcuts right-aligned, unavailable items at 45% — Start is disabled while running, Reopen until finished.  Full menu map — one structure, both platforms  File  New Ride… Ctrl+N Ride Library Ctrl+O Duplicate Ride… ——— Import Riders CSV… Ctrl+I Export Riders CSV… ——— Back Up Database… ——— Settings… Ctrl+, Exit Alt+F4  Ride  Start Ride Ctrl+R Stop Ride… Ctrl+. Set Start Time… Ctrl+T Finish Ride… Ctrl+F Reopen Ride ——— Audit Trail… Ride Setup… Ctrl+E  Riders  Rider Editor F3 Add Rider/Entry… Ctrl+Shift+A Mark DNF… Entry Detail… F4  Cards  Undo Last Crossing Ctrl+Z Add Crossing at Time… Edit Crossing… Reassign Plate… ——— Deal Manual Card… Void Card… ——— Review Held Cards (1)  Results  Standings F5 Generate HTML… Export PDF… Preview in Browser ——— Tie-break Order…  View  Theme: System ● Theme: Light ○ Theme: Dark ○ ——— Bigger Text Ctrl+= Smaller Text Ctrl+- Reset Zoom Ctrl+0  Help  User Guide F1 Keyboard Shortcuts Run Evaluator Self-test About RiverCrossing  Same tree on macOS — wx relocates the standard items per platform convention (About / Settings / Quit move to the app menu, Ctrl becomes ⌘, Exit/Alt+F4 disappears). Every item routes through the same tested command layer as its console button.  Differences from [1a](#1a): "Entry / rider on course" collapses to "Rider", the last-crossing panel drops the team line, counters say "riders" not "entries", and the review panel only appears when something needs it. Try next: "kiosk-size entry field" · "solo-only rider editor".  [1](#t1)RiverCrossing — desktop app, all windows (Industry system, light + dark)  [1i](#1i)Assumptions & design system notes  Grounded in the event  Modelled on the GORBA EPIC (Sun Sept 20 2026, Guelph Lake): a 6-hour poker run on an 8 km closed loop, riding 10:00–16:00. Entries are solo riders or relay teams (2 up to a per-ride max — default 4, limit 10) with one rider on course at a time; every completed lap deals one card to the entry; best 5-card poker hand wins. It is officially not a race — laps + time leaderboards are optional toggles, never the headline.  Decisions reflected in these designs  Keyboard-first console (plate # + Enter, undo one keystroke away) · solo-only by default, teams opt-in at setup · virtual shoe (n×54-card decks, jokers wild, dealt on crossing) · every correction is a logged, reversible edit · core logic in pure-Python modules built test-first (TDD), UI driven by a functional test harness · SQLite autosave on every event, so kill / relaunch resumes the clock from wall time · tie-break order is a ride setting, re-selectable after the finish.  Companions: [engineering spec (DB · poker algorithm · state machine)](spec.md) · [results HTML sample (Tailwind 4, data embedded)](../exports/epic-2026-results.html)  Dark mode = token remap  Same components, one palette swap — in wx it is a theme table, here literally the same markup with CSS variables re-pointed.  bg #F2F2F3 text #1D1F20 accent #5980A6  bg #1B1D1F text #E9EAEB accent #8FB8E0  Tonal ramps invert (100↔900) so tags, tints and hovers carry over unchanged. Suits: ♥♦ take the steel accent, ♠♣ take the text color — no red in this system.  [1a](#1a)Main timing console — light · the operator lives here  RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sun Sept 20 2026 · Guelph Lake · 8 km loop  GORBA EPIC & MTB Festival  Elapsed  2:41:07  Remaining  3:18:53  ● RUNNING — started 10:00:12  Arm Stop ride…  Rider plate — Enter records crossing  127  ↩ record · Esc clear · ⌘Z undo last  Last crossing — 12:41:07  #127 · Sarah Okafor — Dirt Dynamos · team of 3  Lap 4 · lap time 31:22 · total 2:40:55  Q ♠  Q ♠  card dealt Q♠ — 4th card shoe: 223 left  Undo last Edit crossing…  Crossings — latest first showing 13 of 214 · scroll for more  | Time | # | Entry / rider on course | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 12:41:07 | 127 | Dirt Dynamos · Sarah Okafor | 4 | 31:22 | 2:40:55 | Q ♠ | | 12:40:44 | 61 | Marc Tremblay · solo | 5 | 29:48 | 2:40:32 | 9 ♦ | | 12:40:12 | 88 | Moss Ridge Riders · Dev Patel | 6 | 27:59 | 2:40:00 | ★ JOKER | | 12:39:51 | 12 | Ana Souza · solo | 4 | 33:15 | 2:39:39 | 4 ♣ | | 12:39:20 | 143 | Chain Gang · Liz Warner | 5 | 30:06 | 2:39:08 | K ♥ | | 12:38:58 | 33 | Peter Kim · solo | 3 | 44:02 | 2:38:46 | 7 ♠ | | 12:38:31 | 102 | Lake Effect · Josh Bright | 5 | 28:47 | 2:38:19 | A ♦ | | 12:37:55 | 94 | Ted Novak · solo | 4 | 12:44 ⚠ | 2:37:43 | HELD — short lap | | 12:37:28 | 56 | Fat Tire Four · Sam Ellis | 5 | 31:11 | 2:37:16 | 3 ♥ | | 12:36:59 | 19 | Owen Clark · solo | 4 | 32:05 | 2:36:47 | J ♠ | | 12:36:30 | 150 | Singletrack Sisters · Mia Chen | 5 | 29:52 | 2:36:18 | 8 ♦ | | 12:36:02 | 7 | Luca Ferrari · solo | 5 | 28:30 | 2:35:50 | Q ♦ | | 12:35:41 | 168 | Grinder Bros · Alex Roy | 4 | 34:19 | 2:35:29 | 6 ♣ |  Ride counters  214  crossings  209  cards dealt  41  on course now  178/180  entries active  Shoe — 8 decks · 2 jokers each  dealt 209432 total  223 remaining · reshuffles automatically at 0  Most laps — unofficial  #88Moss Ridge Riders6  #7Luca Ferrari5  #102Lake Effect5  #150Singletrack Sisters5  #61Marc Tremblay5  ⚠ Needs review (1)  #94 lap 4 took 12:44 — under the 18:00 minimum. Card held until confirmed.  Confirm + dealVoid  ● Autosaved 12:41:22 — every crossing committed to rides.db (WAL) Space refocuses plate entry · F3 rider editor · F5 results  [1b](#1b)Main timing console — dark · same tokens, remapped  RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db –▢×  FileRideRidersCardsResultsViewHelp  Sun Sept 20 2026 · Guelph Lake · 8 km loop  GORBA EPIC & MTB Festival  Elapsed  2:41:07  Remaining  3:18:53  ● RUNNING — started 10:00:12  Arm Stop ride…  Rider plate — Enter records crossing  61  ↩ record · Esc clear · ⌘Z undo last  Last crossing — 12:41:07  #127 · Sarah Okafor — Dirt Dynamos · team of 3  Lap 4 · lap time 31:22 · total 2:40:55  Q ♠  Q ♠  card dealt Q♠ — 4th card shoe: 223 left  Undo last Edit crossing…  Crossings — latest first showing 8 of 214 · scroll for more  | Time | # | Entry / rider on course | Lap | Lap time | Total | Card | |---|---|---|---|---|---|---| | 12:41:07 | 127 | Dirt Dynamos · Sarah Okafor | 4 | 31:22 | 2:40:55 | Q ♠ | | 12:40:44 | 61 | Marc Tremblay · solo | 5 | 29:48 | 2:40:32 | 9 ♦ | | 12:40:12 | 88 | Moss Ridge Riders · Dev Patel | 6 | 27:59 | 2:40:00 | ★ JOKER | | 12:39:51 | 12 | Ana Souza · solo | 4 | 33:15 | 2:39:39 | 4 ♣ | | 12:39:20 | 143 | Chain Gang · Liz Warner | 5 | 30:06 | 2:39:08 | K ♥ | | 12:38:58 | 33 | Peter Kim · solo | 3 | 44:02 | 2:38:46 | 7 ♠ | | 12:38:31 | 102 | Lake Effect · Josh Bright | 5 | 28:47 | 2:38:19 | A ♦ | | 12:37:28 | 56 | Fat Tire Four · Sam Ellis | 5 | 31:11 | 2:37:16 | 3 ♥ |  Ride counters  214  crossings  209  cards dealt  41  on course now  178/180  entries active  Shoe — 8 decks · 2 jokers each  dealt 209432 total  223 remaining · reshuffles automatically at 0  ⚠ Needs review (1)  #94 lap 4 took 12:44 — under the 18:00 minimum. Card held until confirmed.  Confirm + dealVoid  ● Autosaved 12:41:22 — every crossing committed to rides.db (WAL) Space refocuses plate entry · F3 rider editor · F5 results  [1c](#1c)Ride setup  Ride setup –▢×  Ride nameEvent date  Venue / course  Organizer  Scorer  Organizer logo gorba-logo.png · replace…  Schedule & timing  Entries  Solo only Solo + teams  Max riders per team  New rides default to Solo only — team fields stay hidden everywhere. Max riders per team: 2–10, default 4 (the EPIC uses 4).  Planned start  Duration  Lap length  Minimum lap time  Crossings faster than the minimum are flagged and their card is held for review. Start can also be set after the fact from the console if the gun is missed.  The shoe  Decks shuffled together  Jokers per deck (wild)  0 2 4  8 × 54 = 432 cards for 180 entries. Duplicates across entries are expected; the shoe reshuffles if it empties.  Cards per entry  One card per completed lap — no cap Cap at 10 cards; laps still count past the cap  Tie-break order⋮⋮1Most laps completed  ⋮⋮2Shortest total time  ⋮⋮3High-card draw at venue  Drag to reorder. Applies only between identical best hands — and can be changed again on the results screen before export.  Cancel Create ride  [1d](#1d)Rider editor + CSV  Riders & entries — GORBA EPIC & MTB Festival 2026 –▢×  Search name, team or plate…  All 180 Solo 98 Teams 82  Import CSV  Export CSV  Add entry  | Plate | Entry | Type | Riders | Status |  | |---|---|---|---|---|---| | 7 | Luca Ferrari | SOLO | — | Active |  | | 12 | Ana Souza | SOLO | — | Active |  | | 127 | Dirt Dynamos | TEAM ×3 | Sarah Okafor · Priya Nair · Tom Hale | Active |  | | 33 | Peter Kim | SOLO | — | Active |  | | 56 | Fat Tire Four | TEAM ×4 | Sam Ellis · Kat Diaz · Rob Finch · Amir Wahid | Active |  | | 61 | Marc Tremblay | SOLO | — | Active |  | | 88 | Moss Ridge Riders | TEAM ×4 | Dev Patel · Jo Lindqvist · Casey Muir · Ben Alton | Active |  | | 94 | Ted Novak | SOLO | — | DNF 12:58 |  | | 102 | Lake Effect | TEAM ×3 | Josh Bright · Nina Kovac · Will Trent | Active |  | | 143 | Chain Gang | TEAM ×2 | Liz Warner · Omar Haddad | Active |  |  180 entries · 262 riders · next free plate 181 · delete is enabled only before the start — after it, entries are DNF'd or voided, never removed CSV: plate, entry_name, type (solo | teamN ≤ ride max), rider_1…rider_N — import matches on plate, conflicts listed before writing; re-import reshapes teams freely until the ride starts. Solo-only rides hide Type & Riders columns and the Solo/Teams filter.  [1e](#1e)Entry detail — cards, hand, laps  Entry #127 — Dirt Dynamos –▢×  #127 · Dirt DynamosTEAM ×3  Sarah Okafor (2 laps) · Priya Nair (2) · Tom Hale (2)  Add card… Edit a crossing… Mark DNF  6  laps  6  cards held  3:44:10  total time  37:21  avg lap  Laps — as recorded at 13:44:22  | Lap | Rider on course | Crossed at | Lap time | Card dealt |  | |---|---|---|---|---|---| | 1 | Sarah Okafor | 10:31:40 | 31:28 | Q ♦ |  | | 2 | Priya Nair | 11:04:55 | 33:15 | 4 ♣ |  | | 3 | Tom Hale | 12:09:45 | 1:04:50 incl. pit break | 9 ♣ |  | | 4 | Sarah Okafor | 12:41:07 | 31:22 | Q ♠ |  | | 5 | Priya Nair | 13:13:30 | 32:23 | 9 ♥ |  | | 6 | Tom Hale | 13:44:22 | 30:52 | ★ JOKER |  |  Every edit here is written to the audit log with who/when/why — nothing is deleted, only voided. Cards held — 6  Q ♠  Q ♠  Q ♦  Q ♦  ★JOKER  9 ♥  9 ♥  9 ♣  9 ♣  4 ♣  4 ♣  Steel border = plays in the best hand · faded = unused  Best hand — best 5 of 6  Full house Queens over nines  Joker plays as Q — Q♠ Q♦ ★ 9♥ 9♣ Rank 146 of 7,462 possible hands Currently 3rd of 180 entries  [1f](#1f)Finish & results export  Results — GORBA EPIC & MTB Festival 2026 –▢×  ■ FINISHED 16:00:00 6:00:00 elapsed · 1,124 crossings · 1,092 cards dealt · 177 entries with a hand Reopen ride…  Standings — best poker hand  | P | # | Entry | Laps | Best 5 cards | Hand | |---|---|---|---|---|---| | 1 | 88 | Moss Ridge Riders | 11 | 9♠9♦9♣★K♥ | FOUR OF A KIND — NINES | | 2 | 7 | Luca Ferrari | 10 | A♠A♦★4♣4♥ | FULL HOUSE — ACES / FOURS | | 3 | 127 | Dirt Dynamos | 10 | Q♠Q♦★9♥9♣ | FULL HOUSE — QUEENS / NINES TIE → laps 10 v 9 | | 4 | 56 | Fat Tire Four | 9 | Q♥Q♣Q♠9♦9♠ | FULL HOUSE — QUEENS / NINES | | 5 | 61 | Marc Tremblay | 10 | K♦J♦8♦6♦2♦ | FLUSH — K HIGH | | 6 | 150 | Singletrack Sisters | 9 | 9♣8♦7♠6♥5♣ | STRAIGHT — 9 HIGH | | 7 | 143 | Chain Gang | 8 | 7♠7♥7♣K♦J♠ | THREE OF A KIND — SEVENS | | 8 | 12 | Ana Souza | 9 | K♠K♥5♣5♦A♥ | TWO PAIR — KINGS & FIVES | | 9 | 102 | Lake Effect | 9 | 10♠10♥4♣4♠Q♦ | TWO PAIR — TENS & FOURS | | 10 | 19 | Owen Clark | 8 | A♣A♠K♥J♣8♦ | PAIR — ACES |  177 more rows below — full field, every entry listed with laps, cards and hand. ★ = joker, shown as played.  Publish results — HTML  Show lap & total times — off by default; this setting alone decides, the published page has no toggle ✓Laps leaderboard Fastest total time leaderboard ✓Full field listing ✓GORBA logo Podium poster PDF (one page, top 3) All cards drawn per entry (not just best 5)  Tie-break between identical hands  ① Laps ② Time ③ Draw  Changing this re-runs standings instantly — 1 tie currently resolved by laps.  Generate HTML  Export PDF…Preview in browser  epic-2026-results.html · self-contained · 96 KB · data embedded as JSON — post anywhere epic-2026-results.pdf · Letter/A4 · same sections & toggles Pre-publish checks  ✓ 0 crossings awaiting review ✓ 2 held cards resolved (1 dealt, 1 voided) ✓ 3 DNF entries included, marked ✓ Hand evaluator self-test passed (7,462 ranks)  [1g](#1g)Ride library  RiverCrossing — ride library –▢×  Rides  One database, every event — rides.db  Back up database…  New ride  | Ride | Date | Organizer | Entries | Status |  | |---|---|---|---|---|---| | GORBA EPIC & MTB Festival 2026 | Sun Sept 20 2026 | GORBA | 180 | ● RUNNING 2:41:07 | Resume | | Arkell Fall Poker Run | Sat Oct 17 2026 | GORBA | 64 | DRAFT | OpenDuplicateDelete… | | GORBA EPIC & MTB Festival 2025 | Sun Sept 21 2025 | GORBA | 171 | FINISHED | OpenDuplicateDelete… | | Club Poker Night — Winter Loop | Sat Jan 24 2026 | GORBA | 38 | FINISHED | OpenDuplicateDelete… |  ~/PokerRunTracker/rides.db · 4 rides · 2.1 MB Duplicate copies riders & setup, no timing data · Delete asks you to type the ride's name, writes a backup first, and is never offered on a running ride  [1h](#1h)Safety dialogs — stop / resume / short lap  Start pressed — this ride already has data  GORBA EPIC was stopped at 12:41:22 with 214 crossings recorded. Continuing keeps the original 10:00:12 start — the clock never stopped, so no time or data is lost.  Starting over archives this ride's data first — it is never deleted.  Start a new ride…Continue ride  Recovered — GORBA EPIC & MTB Festival  RiverCrossing closed unexpectedly at 12:41:22 while this ride was running. Every crossing was already saved. The ride clock runs on wall time — elapsed is now 2:47:35 and counting.  Ride libraryResume ride  #94 Ted Novak — lap faster than minimum  Lap 4 recorded at 12:44 against an 18:00 minimum. Usually a double-entry or a mistyped plate. The crossing is kept either way; only the card waits.  Void crossingReassign plate…Confirm — deal card*

- **RETIRED (July 24 2026)** — superseded as implementation reference by the native [XRC window designs](xrc-windows.md) (wxWidgets-native controls, canonical snake_case XRC names, Spec §15b). These Industry-styled mockups remain for flow/content history only — do not implement visuals or control names from them.

[8](#t8)Console states — before the start and after the finish (completes [1a](#1a); spec §13)

[8a](#8a)DRAFT — ride loaded, not started

RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

*Sun Sept 20 2026 · Guelph Lake · 8 km loop

GORBA EPIC & MTB Festival

Planned start

10:00

6 h ride · window 10:00–16:00

DRAFT — not started

Set start time…

Start ride*

Rider plate

—

Start the ride to record crossings

Roster is loaded and editable until the start — **180 entries · 262 riders** (F3 to review).
Start with the button at the gun, or record it late with Set start time.

Crossings

No crossings yet.
The first rider across the line appears here with their lap and card.

Ride counters

0

crossings

0

cards dealt

0

on course

180

entries ready

Shoe — 8 decks · 2 jokers each

sealed432 cards

Shuffled with this ride's seed — first card deals on the first crossing

Pre-start checks

✓ 180 entries, plates unique
✓ Evaluator self-test passed
✓ Backup written 09:41
— CSV re-import still allowed

● rides.db ready (WAL) — every crossing will commit as it happens
Ctrl+R starts the ride · F3 rider editor

[8b](#8b)FINISHED — ride over, results live

RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

*Sun Sept 20 2026 · Guelph Lake · 8 km loop

GORBA EPIC & MTB Festival

Final time

6:00:00

10:00:12 → 16:00:12

■ FINISHED 16:00:12

Reopen ride…
Results (F5)*

Ride finished — standings computed

Best hand: #88 Moss Ridge Riders — Four of a Kind, Nines · 1 tie resolved by laps · 0 held cards

Generate HTML
Export PDF…

Crossings — final, read-only
1,124 recorded · corrections require Reopen

| Time | # | Entry / rider on course | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 15:58:41 | 88 | Moss Ridge Riders · Ben Alton | 11 | 29:17 | 5:58:29 | K ♥ |
| 15:57:02 | 7 | Luca Ferrari · solo | 10 | 31:44 | 5:56:50 | 4 ♣ |
| 15:55:24 | 61 | Marc Tremblay · solo | 10 | 30:58 | 5:55:12 | 2 ♠ |
| 15:54:47 | 127 | Dirt Dynamos · Priya Nair | 10 | 32:40 | 5:54:35 | 9 ♥ |
| 15:53:10 | 150 | Singletrack Sisters · Mia Chen | 9 | 33:05 | 5:52:58 | 6 ♦ |
| 15:51:33 | 102 | Lake Effect · Nina Kovac | 9 | 34:21 | 5:51:21 | J ♣ |

Final counters

1,124

crossings

1,092

cards dealt

177

with a hand

3

DNF, listed

Most laps — final

#88Moss Ridge Riders**11**

#7Luca Ferrari**10**

#61Marc Tremblay**10**

#127Dirt Dynamos**10**

Published

✓ epic-2026-results.html — 16:07
— PDF not yet exported

● Ride archived in rides.db — reopen any time for corrections
F5 standings · entry field disabled while finished

[8c](#8c)REOPENED — corrections mode (from [8b](#8b) ▸ Reopen ride…)

RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

*Sun Sept 20 2026 · Guelph Lake · 8 km loop

GORBA EPIC & MTB Festival

Final time — unchanged

6:00:00

window closed 16:00:12

◆ REOPENED 16:22 — corrections only

View audit trail…
Finish again*

Corrections mode — the clock is closed, the record is open

Live plate entry is off. Add a missed crossing **with an explicit time**, fix or void existing ones, deal or void cards — standings recompute after every change. Nothing finalizes until **Finish again**.

Add crossing at time…
Edit crossing…Deal / void card…

Crossings — editable, changes highlighted
2 corrections this session, both audit-logged

| Time | # | Entry / rider on course | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 15:59:50 ADDED 16:24 | 33 | Peter Kim · solo | 8 | 36:12 | 5:59:38 | 8 ♠ |
| 15:58:41 | 88 | Moss Ridge Riders · Ben Alton | 11 | 29:17 | 5:58:29 | K ♥ |
| 15:57:02 | 7 | Luca Ferrari · solo | 10 | 31:44 | 5:56:50 | VOIDED 16:23 — double entry |
| 15:55:24 | 61 | Marc Tremblay · solo | 10 | 30:58 | 5:55:12 | 2 ♠ |
| 15:54:47 | 127 | Dirt Dynamos · Priya Nair | 10 | 32:40 | 5:54:35 | 9 ♥ |

⚠ Published results are stale

epic-2026-results.html was generated at **16:07** — before this session's corrections (16:23, 16:24). Standings may have shifted.

Regenerate after finishing

This session

16:23 — voided #7 lap 10 double entry
16:24 — added #33 crossing 15:59:50 missed at the line
Every change carries who · when · why.

● Corrections committed as they happen — Finish again recomputes and re-locks
Standings preview: #88 still P1 · tie P3/P4 unchanged

The one console, four states: [8a](#8a) DRAFT → [1a](#1a)/[2a](#2a) RUNNING → [8b](#8b) FINISHED ⇄ [8c](#8c) REOPENED. Try next: "start-of-day checklist dialog".

[7](#t7)Pooled rider plates + hidden-times console — external requirements adopted (extends [1c](#1c), [1d](#1d), [2a](#2a))

[7a](#7a)Ride setup — the Plate model choice (mixed rides only)

Entries — as it now appears in Ride setup

Entries

*Solo only
Solo + teams

Max riders per team

Plate model

Rider plates — pooled (default): every rider has their own plate and draws one card per lap, uncapped; the team's hand scores from the pooled cards
Team plate — relay: one plate per team, one rider on course at a time (EPIC)

Pooled rides stay editable while running — riders can move between teams and their plate, crossings and cards move with them (audit-logged). CSV switches to one row per rider: plate, name, team_name.

[7b](#7b)Rider editor — pooled ride, grouped by team, editable mid-ride

Riders & teams — Club Poker Night (rider plates, pooled) — ● running
–▢×

| Plate | Rider | Team | Cards |  |
|---|---|---|---|---|
| TEAM · DIRT DYNAMOS — 8 cards pooled |  |  |  |  |
| 12 | Sarah Okafor | Dirt Dynamos | 3 | Move to team… |
| 14 | Priya Nair | Dirt Dynamos | 3 | Move to team… |
| 15 | Tom Hale | moved from Chain Gang · 11:24 | 2 | Move to team… |
| TEAM · CHAIN GANG — 5 cards pooled |  |  |  |  |
| 21 | Liz Warner | Chain Gang | 3 | Move to team… |
| 22 | Omar Haddad | Chain Gang | 2 | Move to team… |
| SOLO ENTRIES |  |  |  |  |
| 31 | Marc Tremblay | — solo | 4 | Move to team… |

38 riders · 11 teams + 9 solos
Moves are allowed while running on pooled rides — plate, crossings and cards travel with the rider; every move is audit-logged

[7c](#7c)Console with times hidden — Settings toggle, live mid-ride (compare [2a](#2a))

RiverCrossing — Arkell Fall Poker Run — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

Sat Oct 17 2026 · Arkell Spring Grounds · 6.5 km loop · solo entries · times hidden

Arkell Fall Poker Run

Riding window closes in

2:47:20

● RUNNING

Arm
Stop ride…*

Rider plate — Enter records crossing

23

↩ record · Esc clear · ⌘Z undo last

Last crossing

#23 · Hana Yoshida

Lap **3** · card **3 of 3**

7
♥

7
♥

card dealt
7♥ — 3rd card
shoe: 131 left

Undo last
Edit crossing…

Crossings — latest first
times hidden (Settings) — still recorded underneath

| # | Rider | Lap | Cards | Card dealt |
|---|---|---|---|---|
| 23 | Hana Yoshida | 3 | 3 | 7 ♥ |
| 4 | Owen Clark | 3 | 3 | J ♠ |
| 31 | Marc Tremblay | 3 | 3 | ★ JOKER |
| 17 | Gita Rao | 2 | 2 | 2 ♣ |
| 52 | Ana Souza | 3 | 3 | 10 ♦ |
| 8 | Peter Kim | 2 | 2 | K ♣ |
| 45 | Liz Warner | 3 | 3 | A ♥ |
| 29 | Sam Ellis | 3 | 3 | 5 ♠ |

Ride counters

87

crossings

87

cards dealt

28

on course now

64/64

riders active

Shoe — 4 decks · 2 jokers each

dealt 87216 total

131 remaining · reshuffles automatically at 0

Most laps — unofficial

#31Marc Tremblay**3**

#45Liz Warner**3**

#23Hana Yoshida**3**

#29Sam Ellis**3**

#52Ana Souza**3**

● Autosaved — every crossing committed to rides.db (WAL)
Times hidden by Settings — lap & total columns return the moment it's toggled off

Plate model defaults to Rider plates — pooled; EPIC-style rides switch to Team relay at setup. Pooled mode + mid-ride moves + the all-cards export flag come from the organizer's prior-script requirements. Try next: "pooled-mode entry detail" · "all-cards row in the results page sample".

[6](#t6)User Guide outline — the bundled HTML doc behind Help ▸ User Guide (F1)

[6a](#6a)Guide structure — 10 chapters + 2 appendices, task-ordered like race day

*docs/user-guide.html · bundled with the app · v1.0

RiverCrossing — User Guide

Opens in the browser from Help ▸ User Guide (F1).
Chapters follow the day: set up → run → finish → publish.

1**Getting started**

Installing on Windows & macOS · first launch and the ride library · where rides.db lives · automatic backups · light & dark themes

2**Setting up a ride**

Name, date, organizer, scorer, logo · schedule & minimum lap time · solo-only vs solo + teams and max riders per team · the shoe: decks & jokers · card cap · tie-break order

3**Riders & entries**

The rider editor · plates · adding solos and teams · CSV import & export, column reference · the conflict report · reshaping teams up to the start · what locks when the ride starts

4**Running the ride**

Starting — the button, or Set Start Time if the gun was missed · recording crossings (plate + Enter) · the sounds · undo · how cards are dealt · short-lap review & held cards · relay teams in practice

5**Fixing mistakes**

Undo last crossing · edit a crossing · reassign a plate · void & manual cards · mark DNF · the audit log — why nothing is ever deleted

6**Stopping, quitting & recovery**

The Arm + Stop + confirm sequence · quitting with a ride running · resuming on reopen · crash recovery · why the wall clock means you can't lose time

7**Finishing & standings**

Finish ride · pre-publish checks · how ties are broken (and changing the rule after the finish) · reopening for corrections

8**Publishing results**

HTML export — options, the times toggle, posting the single file anywhere · PDF export & the podium poster · printing

9**Poker, the run way**

Hand rankings, best → worst (five of a kind → high card) · jokers are wild · best 5 of all your cards · what beats what, with examples · duplicates across entries

10**Troubleshooting & FAQ**

Missed the start · typed the wrong plate · rider crossed twice · shoe ran out · moving the database to another machine · restoring a backup

A**Appendix A — Keyboard shortcuts**

The full table, Windows and macOS columns

B**Appendix B — CSV reference**

Column tables for solo-only and mixed rides · example files · every conflict message and its fix

Format: one HTML file on the app's own tokens (light/dark aware), anchors per section so dialogs can deep-link Help buttons — e.g. the import report links straight to Appendix B. Screenshots come from the functional-test harness, so they regenerate with every release and never go stale.

Chapters mirror race day; every dialog's Help lands on its anchor. Try next: "write chapter 4 in full" · "quick-start one-pager for volunteers".

[5](#t5)PDF results layout — what rivercrossing.pdfexport (fpdf2) renders · spec §8b, same data as the HTML page

[5a](#5a)Page 1 — cover, podium, top ten (Letter, times shown)

Official results · poker run

GORBA EPIC & MTB Festival 2026

180 · 1,124 · 1,092

entries · laps · cards dealt

Sunday September 20, 2026 · Guelph Lake MTB Trails · 8 km closed loop · riding 10:00–16:00
Best 5-card hand from all cards held · jokers wild

Best hands — top 3*

1

#88 Moss Ridge Riders

TEAM ×4 · 11 laps · 5:52:41

9♠9♦9♣★K♥

FOUR OF A KIND — NINES

2

#7 Luca Ferrari

SOLO · 10 laps · 5:41:03

A♠A♦★4♣4♥

FULL HOUSE — ACES / FOURS

3

#127 Dirt Dynamos

TEAM ×3 · 10 laps · 5:48:19

Q♠Q♦★9♥9♣

FULL HOUSE — QUEENS / NINES

Top ten

| P | # | Entry | Laps | Total | Best 5 cards | Hand |
|---|---|---|---|---|---|---|
| 1 | 88 | Moss Ridge Riders | 11 | 5:52:41 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND — NINES |
| 2 | 7 | Luca Ferrari | 10 | 5:41:03 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE — ACES / FOURS |
| 3 | 127 | Dirt Dynamos tie → laps | 10 | 5:48:19 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE — QUEENS / NINES |
| 4 | 56 | Fat Tire Four | 9 | 5:12:44 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE — QUEENS / NINES |
| 5 | 61 | Marc Tremblay | 10 | 5:44:56 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH — K HIGH |
| 6 | 150 | Singletrack Sisters | 9 | 5:03:27 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT — 9 HIGH |
| 7 | 143 | Chain Gang | 8 | 4:41:12 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND — SEVENS |
| 8 | 12 | Ana Souza | 9 | 5:21:38 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR — KINGS & FIVES |
| 9 | 102 | Lake Effect | 9 | 5:17:05 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR — TENS & FOURS |
| 10 | 19 | Owen Clark | 8 | 4:52:50 | A♣ A♠ K♥ J♣ 8♦ | PAIR — ACES |

P3/P4 held identical hands — resolved by tie-break rule ① most laps (10 v 9). It's not a race: laps and times are unofficial. ★ = joker, shown as played.

Organizer: GORBA — J. Marsden · Scorer: D. Whitfield
Page 1 of 6 · generated 16:07, Sept 20 2026 · RiverCrossing

[5b](#5b)Page 2 — optional boards, full field begins

GORBA EPIC & MTB Festival 2026 — Official results
Sunday September 20, 2026

Most laps

| 1 | #88 | Moss Ridge Riders | 11 | 5:52:41 |
|---|---|---|---|---|
| 2 | #7 | Luca Ferrari | 10 | 5:41:03 |
| 3 | #61 | Marc Tremblay | 10 | 5:44:56 |
| 4 | #127 | Dirt Dynamos | 10 | 5:48:19 |
| 5 | #12 | Ana Souza | 9 | 5:21:38 |

Unofficial — 8 km per lap.

Fastest — laps then time

| 1 | #88 | Moss Ridge Riders | 11 laps | 5:52:41 | avg 32:03 |
|---|---|---|---|---|---|
| 2 | #7 | Luca Ferrari | 10 laps | 5:41:03 | avg 34:06 |
| 3 | #61 | Marc Tremblay | 10 laps | 5:44:56 | avg 34:29 |
| 4 | #127 | Dirt Dynamos | 10 laps | 5:48:19 | avg 34:50 |
| 5 | #102 | Lake Effect | 9 laps | 5:17:05 | avg 35:14 |

Most laps, shortest elapsed to last crossing.

Full field — ordered by hand

| P | # | Entry | Type | Laps | Total | Best lap | Best 5 cards | Hand |
|---|---|---|---|---|---|---|---|---|
| 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 5:52:41 | 27:59 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND |
| 2 | 7 | Luca Ferrari | Solo | 10 | 5:41:03 | 28:30 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE |
| 3 | 127 | Dirt Dynamos | Team ×3 | 10 | 5:48:19 | 30:52 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE |
| 4 | 56 | Fat Tire Four | Team ×4 | 9 | 5:12:44 | 29:41 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE |
| 5 | 61 | Marc Tremblay | Solo | 10 | 5:44:56 | 29:12 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH |
| 6 | 150 | Singletrack Sisters | Team ×4 | 9 | 5:03:27 | 29:52 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT |
| 7 | 143 | Chain Gang | Team ×2 | 8 | 4:41:12 | 30:06 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND |
| 8 | 12 | Ana Souza | Solo | 9 | 5:21:38 | 31:44 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR |
| 9 | 102 | Lake Effect | Team ×3 | 9 | 5:17:05 | 28:47 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR |
| 10 | 19 | Owen Clark | Solo | 8 | 4:52:50 | 32:05 | A♣ A♠ K♥ J♣ 8♦ | PAIR |
| 11 | 33 | Peter Kim | Solo | 7 | 4:31:19 | 33:58 | K♣ K♦ Q♠ 9♥ 6♣ | PAIR |
| 12 | 168 | Grinder Bros | Team ×2 | 8 | 4:47:33 | 31:20 | J♥ J♦ A♠ 8♣ 3♠ | PAIR |
| 13 | 71 | Gita Rao | Solo | 7 | 4:22:08 | 34:12 | 8♠ 8♥ K♣ 10♦ 4♥ | PAIR |
| 14 | 94 | Ted Novak DNF | Solo | 4 | 2:37:43 | 31:07 | A♦ Q♣ 9♠ 5♥ | HIGH CARD |

Rows 15–180 continue on pages 3–6 at this density (≈ 44 rows per page). With times hidden, the Total and Best lap columns are omitted and the table re-widens.

Organizer: GORBA — J. Marsden · Scorer: D. Whitfield
Page 2 of 6 · generated 16:07, Sept 20 2026 · RiverCrossing

[5d](#5d)One-page podium poster — for the prize table & clubhouse wall

*Sunday September 20, 2026
Guelph Lake MTB Trails · 8 km loop

Best poker hands

GORBA EPIC & MTB Festival 2026*

1

#88 Moss Ridge Riders

Team of 4 — Dev Patel · Jo Lindqvist · Casey Muir · Ben Alton · 11 laps

FOUR OF A KIND — NINES

9
♠

9
♠

9
♦

9
♦

9
♣

9
♣

★JOKER

K
♥

K
♥

2

#7 Luca Ferrari

Solo · 10 laps

FULL HOUSE — ACES / FOURS

A♠
A♦
★
4♣
4♥

3

#127 Dirt Dynamos

Team of 3 · 10 laps

FULL HOUSE — QUEENS / NINES

Q♠
Q♦
★
9♥
9♣

It's not a race, it's a poker run — 180 entries · 1,124 laps · 1,092 cards dealt · ★ joker as played
Generated 16:07 · RiverCrossing

[5e](#5e)Times hidden — same page 2 with the "Show lap & total times" flag off

GORBA EPIC & MTB Festival 2026 — Official results
Sunday September 20, 2026

Most laps

| 1 | #88 | Moss Ridge Riders | 11 |
|---|---|---|---|
| 2 | #7 | Luca Ferrari | 10 |
| 3 | #61 | Marc Tremblay | 10 |
| 4 | #127 | Dirt Dynamos | 10 |
| 5 | #12 | Ana Souza | 9 |

Unofficial — 8 km per lap. Order by laps, ties listed together.

Times not published

This export was generated with times hidden — it's a poker run, not a race. Lap and total time columns are omitted and the "Fastest" leaderboard cannot render without them, so it is left out automatically. Laps and hands are unaffected.

Full field — ordered by hand

| P | # | Entry | Type | Laps | Best 5 cards | Hand |
|---|---|---|---|---|---|---|
| 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND |
| 2 | 7 | Luca Ferrari | Solo | 10 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE |
| 3 | 127 | Dirt Dynamos | Team ×3 | 10 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE |
| 4 | 56 | Fat Tire Four | Team ×4 | 9 | Q♥ Q♣ Q♠ 9♦ 9♠ | FULL HOUSE |
| 5 | 61 | Marc Tremblay | Solo | 10 | K♦ J♦ 8♦ 6♦ 2♦ | FLUSH |
| 6 | 150 | Singletrack Sisters | Team ×4 | 9 | 9♣ 8♦ 7♠ 6♥ 5♣ | STRAIGHT |
| 7 | 143 | Chain Gang | Team ×2 | 8 | 7♠ 7♥ 7♣ K♦ J♠ | THREE OF A KIND |
| 8 | 12 | Ana Souza | Solo | 9 | K♠ K♥ 5♣ 5♦ A♥ | TWO PAIR |
| 9 | 102 | Lake Effect | Team ×3 | 9 | 10♠ 10♥ 4♣ 4♠ Q♦ | TWO PAIR |
| 10 | 19 | Owen Clark | Solo | 8 | A♣ A♠ K♥ J♣ 8♦ | PAIR |
| 11 | 33 | Peter Kim | Solo | 7 | K♣ K♦ Q♠ 9♥ 6♣ | PAIR |
| 12 | 168 | Grinder Bros | Team ×2 | 8 | J♥ J♦ A♠ 8♣ 3♠ | PAIR |
| 13 | 71 | Gita Rao | Solo | 7 | 8♠ 8♥ K♣ 10♦ 4♥ | PAIR |
| 14 | 94 | Ted Novak DNF | Solo | 4 | A♦ Q♣ 9♠ 5♥ | HIGH CARD |

Rows 15–180 continue on the following pages. The wider Hand column absorbs the removed time columns.

Organizer: GORBA — J. Marsden · Scorer: D. Whitfield
Page 2 of 5 · generated 16:07, Sept 20 2026 · RiverCrossing

[5f](#5f)All cards drawn — full-field fragment with the flag on (both exports)

Full field — ordered by hand · includes every card drawn (organizer option)

| P | # | Entry | Type | Laps | Best 5 cards | Hand |
|---|---|---|---|---|---|---|
| 1 | 88 | Moss Ridge Riders | Team ×4 | 11 | 9♠ 9♦ 9♣ ★ K♥ | FOUR OF A KIND |
|  |  | All 11 cards, in draw order: 9♠ 9♦ 9♣ ★ K♥ 2♣ 5♦ 7♥ J♣ 3♠ 10♦ |  |  |  |  |
| 2 | 7 | Luca Ferrari | Solo | 10 | A♠ A♦ ★ 4♣ 4♥ | FULL HOUSE |
|  |  | All 10 cards, in draw order: A♠ A♦ ★ 4♣ 4♥ 8♠ 2♦ 9♥ Q♣ 6♠ |  |  |  |  |
| 3 | 127 | Dirt Dynamos | Team ×3 | 10 | Q♠ Q♦ ★ 9♥ 9♣ | FULL HOUSE |
|  |  | All 10 cards, in draw order: Q♠ Q♦ ★ 9♥ 9♣ 4♣ 7♦ 2♥ 10♠ J♦ |  |  |  |  |

Rendering rule, both exports: one muted sub-row per entry, cards in draw order; on rider-pooled rides each card carries the drawing rider's initials ("9♠ SO"). Density drops to ≈ 22 entries per PDF page with the flag on; the HTML sample ([light](../exports/epic-2026-results.html) · [dark](../exports/epic-2026-results-no-times.html)) has it switched on live.

[5c](#5c)Render notes for rivercrossing.pdfexport

- **Geometry.** Letter 8.5×11in (A4 selectable), 0.58in margins; footer rule + "Page n of N" on every page; page-2+ header is the one-line running title. **Fonts.** fpdf2 embeds TTFs: Barlow + Barlow Condensed (headings), and suit glyphs ♠♥♦♣★ from a bundled symbol-capable face (e.g. DejaVu Sans) — no system-font dependence, identical output on both platforms. **Color.** The Industry tokens as RGB: ink #1D1F20, steel #416180 for ♥♦ and hand names, deep steel #1D2D3D for the P1 plate — no red, prints clean in grayscale. **Flags.** Same export options as HTML: hide-times drops Total/Best-lap columns; boards render only if selected; corner registration marks drawn as two 11pt hairlines per corner. **Determinism.** Fixed creation metadata + embedded fonts ⇒ byte-stable output for the §11 golden-file test.

Pages mirror the HTML export ([1f](#1f) options apply to both); [5d](#5d) is a third export flag ("Podium poster") emitting a single celebratory page. Try next: "grayscale print check" · "poster with door-prize draw numbers".

[4](#t4)Exit & resume — quitting can never lose a running ride (pairs with crash recovery [1h](#1h))

[4a](#4a)Exit caught while running · resume on next open

Quit — the ride is still running

GORBA EPIC & MTB Festival is running: **2:41:07** elapsed, **214 crossings** saved. The clock is wall time — quitting loses nothing, and the ride **continues on its own**. When you reopen, you'll be asked to pick it back up.

The database records this as a clean quit with the ride running, so reopening knows exactly where you left off.

CancelFinish ride first…Quit — keep ride running

Welcome back — the ride is still running

You quit at **12:41:22** with GORBA EPIC running. The clock never stopped — elapsed is now **2:47:35**, all 214 crossings intact. Continue where you left off?

Ride libraryContinue ride

Same resume dialog either way in — only the copy differs: a clean quit reads "You quit at…", a crash ([1h](#1h)) reads "closed unexpectedly at…". The database tells them apart by session bookkeeping, not guesswork.

Flow: × or File ▸ Exit while RUNNING → exit dialog → quit keeps the ride live (wall clock) → next launch always opens the resume dialog. Try next: "auto-resume without asking, with an undo bar" · "show the Finish-first path".

[3](#t3)Menu audit — every menu item's screen or dialog now exists (completes [2c](#2c); File/Export & Back Up use OS-native pickers)

[3a](#3a)Settings — File ▸ Settings…

Settings
–▢×

Appearance

*System
Light
Dark

Mirrors View ▸ Theme — one setting, two places

Console

Operator name (written to the audit log)

✓Sound on recorded crossing
Hide times on the console — toggleable while a ride runs; times are still recorded

Database

C:\Users\scorer\PokerRunTracker\rides.dbChange…Back up now

✓Automatic backups — on open and hourly while running, keep last 20

Close

[3b](#3b)Add / edit entry + Mark DNF — Riders menu

Add entry

Plate

Entry name

Type

Solo
Team

Riders on team

Riders — one field per seat

Opens with **Solo** selected and focus in the Plate field (shown here in its Team state). Plate prefilled with the next free number; rider fields match the team size — 2 up to this ride's max (setup allows up to 10). Solo-only rides show plate + name only. Locked once the ride starts.

CancelAdd entry

Mark #45 DNF?

Liz Warner keeps her 3 laps and 3 cards — listed and marked **DNF 11:20**. Reversible from the editor.

CancelMark DNF

[3c](#3c)Corrections — Cards ▸ Edit crossing · Reassign plate · Deal manual card

Edit crossing — #127 lap 4

Crossed at

Card

Void this crossing (card voids with it)

Reason — required, goes to the audit log

Later lap times for this entry recompute automatically.

CancelSave

Reassign crossing — 12:37:55

Recorded for **#94 Ted Novak** (flagged: short lap).

Move to plate*Becomes **#49 Gita Rao — lap 5** · lap time 35:40 · card moves with it, flag clears

Reason*CancelReassign

Deal manual card

Entry

Next card from the shoe (auditable)
Specific card: Q ♠

Reason — required

CancelDeal card

[3d](#3d)Ride control confirms — Ride ▸ Stop · Set start time · Finish · Duplicate (File)

Stop the ride?

Reached by arming the checkbox beside Stop first — two deliberate steps, so it can't be hit by accident. Locks the entry field; the clock is wall time, so stopping loses nothing. Start again any time — you'll be asked to continue.

CancelStop ride

Set start time

Missed the gun? Back-date the start; lap-1 times recompute.

Actual start

CancelSet start

Finish the ride?

Ends at **16:00:00**, runs the hand algorithm and opens Results. 1 held card needs review first. Reopen is always available.

CancelFinish ride

Duplicate ride

Copies setup and the full rider list — no timing data.

New ride name

CancelDuplicate

Reopen Ride uses the same confirm pattern ("Reopen for corrections? Standings recompute on export"), as does Delete ride — destructive form: type the ride's name to enable the button, focus starts on Cancel, a backup is written first. Undo Last Crossing acts directly — status-bar notice, no dialog.

[3e](#3e)CSV import report — File ▸ Import Riders CSV… (after the OS file picker)

Import epic-roster-final.csv

12

new entries

165

updated in place

3

conflicts — skipped

| Row | Plate | Problem |
|---|---|---|
| 41 | 88 | Duplicate plate within the file (rows 41 and 96) |
| 77 | 143 | type=team3 but only 2 rider columns filled |
| 102 | 61 | Structural change (solo → team2) — ride is RUNNING; name fixes only |

Nothing has been written yet. Import applies the 177 clean rows; conflicts are saved to a report you can fix and re-import — repeat freely until the ride starts.

CancelSave conflict report…Import 177 rows

[3f](#3f)Help — Keyboard shortcuts · Evaluator self-test · About

Keyboard shortcuts

Record crossingEnter
Refocus plate entrySpace
Undo last crossingCtrl+Z
Clear entryEsc
Rider editorF3
Entry detailF4
StandingsF5
Start / stop rideCtrl+R / Ctrl+.
Import CSVCtrl+I
SettingsCtrl+,

macOS: Ctrl reads as ⌘.

Close

Hand evaluator self-test*

PASSED — 0.41 s

✓ 7,462 natural ranks verified against the local table
✓ 214 joker vectors (incl. five of a kind, wheel)
✓ Determinism: seed replay reproduces the EPIC 2025 deal
✓ Cap-X and tie-break fixtures

Runs automatically at every launch; this menu item re-runs it on demand.

Close

*RiverCrossing

Version 1.0.0 (build 2026-07-23)
Python 3.14 · wxPython 4.2.2 · SQLite 3.46
Database: rides.db · 4 rides

Poker-run timing & scoring, built for the [GORBA EPIC](https://gorba.ca/events/gorba-epic/)

Licenses…Close

Every 2c menu item now lands somewhere: see the menu → screen map in the spec. OS-native dialogs (file pickers for Export CSV, Back Up, Generate HTML) are deliberately not custom. Try next: "toast/status-bar notice designs" · "User Guide outline".

[2](#t2)Solo-only mode — the default; team UI disappears (riffs on [1a](#1a) + [1d](#1d))

[2a](#2a)Main timing console — solo-only ride, light

RiverCrossing — Arkell Fall Poker Run — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

Sat Oct 17 2026 · Arkell Spring Grounds · 6.5 km loop · solo entries

Arkell Fall Poker Run

Elapsed

1:12:40

Remaining

2:47:20

● RUNNING — started 10:30:05

✓Arm
Stop ride…*

Rider plate — Enter records crossing

23

↩ record · Esc clear · ⌘Z undo last

Last crossing — 11:42:45

#23 · Hana Yoshida

Lap **3** · lap time **24:12** · total **1:12:40**

7
♥

7
♥

card dealt
7♥ — 3rd card
shoe: 131 left

Undo last
Edit crossing…

Crossings — latest first
showing 9 of 87 · scroll for more

| Time | # | Rider | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 11:42:45 | 23 | Hana Yoshida | 3 | 24:12 | 1:12:40 | 7 ♥ |
| 11:42:19 | 4 | Owen Clark | 3 | 25:01 | 1:12:14 | J ♠ |
| 11:41:50 | 31 | Marc Tremblay | 3 | 23:40 | 1:11:45 | ★ JOKER |
| 11:41:12 | 17 | Gita Rao | 2 | 36:28 | 1:11:07 | 2 ♣ |
| 11:40:41 | 52 | Ana Souza | 3 | 24:55 | 1:10:36 | 10 ♦ |
| 11:40:02 | 8 | Peter Kim | 2 | 35:12 | 1:09:57 | K ♣ |
| 11:39:33 | 45 | Liz Warner | 3 | 23:58 | 1:09:28 | A ♥ |
| 11:38:57 | 29 | Sam Ellis | 3 | 24:36 | 1:08:52 | 5 ♠ |
| 11:38:20 | 11 | Dev Patel | 2 | 34:47 | 1:08:15 | Q ♦ |

Ride counters

87

crossings

87

cards dealt

28

on course now

64/64

riders active

Shoe — 4 decks · 2 jokers each

dealt 87216 total

131 remaining · reshuffles automatically at 0

Most laps — unofficial

#31Marc Tremblay**3**

#45Liz Warner**3**

#23Hana Yoshida**3**

#29Sam Ellis**3**

#52Ana Souza**3**

● Autosaved 11:42:45 — every crossing committed to rides.db (WAL)
Solo-only ride: no team columns, no rider-on-course tracking · Space refocuses plate entry

[2b](#2b)Rider editor — solo-only ride (compare [1d](#1d))

Riders — Arkell Fall Poker Run
–▢×

Search name or plate…

Import CSV

Export CSV

Add rider

| Plate | Rider | Status |  |
|---|---|---|---|
| 4 | Owen Clark | Active |  |
| 8 | Peter Kim | Active |  |
| 11 | Dev Patel | Active |  |
| 23 | Hana Yoshida | Active |  |
| 17 | Gita Rao | Active |  |
| 29 | Sam Ellis | Active |  |
| 31 | Marc Tremblay | Active |  |
| 45 | Liz Warner | DNF 11:20 |  |
| 52 | Ana Souza | Active |  |

64 riders · next free plate **65**
CSV: plate, name, notes — type & rider_1…4 columns are ignored on solo-only rides (reported, not fatal)

[2c](#2c)Menu system — identical structure on Windows & macOS

RiverCrossing
–▢×

FileRideRidersCardsResultsViewHelp

Start RideCtrl+R

Stop Ride…Ctrl+.

Set Start Time…Ctrl+T

Finish Ride…Ctrl+F

Reopen Ride

Ride Setup…Ctrl+E

client area — console below the bar

Open state: accent highlight, shortcuts right-aligned, unavailable items at 45% — Start is disabled while running, Reopen until finished.

Full menu map — one structure, both platforms

File

New Ride… Ctrl+N
Ride Library Ctrl+O
Duplicate Ride…
———
Import Riders CSV… Ctrl+I
Export Riders CSV…
———
Back Up Database…
———
Settings… Ctrl+,
Exit Alt+F4

Ride

Start Ride Ctrl+R
Stop Ride… Ctrl+.
Set Start Time… Ctrl+T
Finish Ride… Ctrl+F
Reopen Ride
———
Audit Trail…
Ride Setup… Ctrl+E

Riders

Rider Editor F3
Add Rider/Entry… Ctrl+Shift+A
Mark DNF…
Entry Detail… F4

Cards

Undo Last Crossing Ctrl+Z
Add Crossing at Time…
Edit Crossing…
Reassign Plate…
———
Deal Manual Card…
Void Card…
———
Review Held Cards (1)

Results

Standings F5
Generate HTML…
Export PDF…
Preview in Browser
———
Tie-break Order…

View

Theme: System ●
Theme: Light ○
Theme: Dark ○
———
Bigger Text Ctrl+=
Smaller Text Ctrl+-
Reset Zoom Ctrl+0

Help

User Guide F1
Keyboard Shortcuts
Run Evaluator Self-test
About RiverCrossing

Same tree on macOS — wx relocates the standard items per platform convention (About / Settings / Quit move to the app menu, Ctrl becomes ⌘, Exit/Alt+F4 disappears). Every item routes through the same tested command layer as its console button.

Differences from [1a](#1a): "Entry / rider on course" collapses to "Rider", the last-crossing panel drops the team line, counters say "riders" not "entries", and the review panel only appears when something needs it. Try next: "kiosk-size entry field" · "solo-only rider editor".

[1](#t1)RiverCrossing — desktop app, all windows (Industry system, light + dark)

[1i](#1i)Assumptions & design system notes

Grounded in the event

Modelled on the **GORBA EPIC** (Sun Sept 20 2026, Guelph Lake): a 6-hour poker run on an 8 km closed loop, riding 10:00–16:00. Entries are solo riders or relay teams (2 up to a per-ride max — default 4, limit 10) with one rider on course at a time; every completed lap deals one card to the entry; best 5-card poker hand wins. It is officially *not a race* — laps + time leaderboards are optional toggles, never the headline.

Decisions reflected in these designs

Keyboard-first console (plate # + Enter, undo one keystroke away) · solo-only by default, teams opt-in at setup · virtual shoe (n×54-card decks, jokers wild, dealt on crossing) · every correction is a logged, reversible edit · core logic in pure-Python modules built test-first (TDD), UI driven by a functional test harness · SQLite autosave on every event, so kill / relaunch resumes the clock from wall time · tie-break order is a ride setting, re-selectable after the finish.

Companions: [engineering spec (DB · poker algorithm · state machine)](spec.md) · [results HTML sample (Tailwind 4, data embedded)](../exports/epic-2026-results.html)

Dark mode = token remap

Same components, one palette swap — in wx it is a theme table, here literally the same markup with CSS variables re-pointed.

bg #F2F2F3
text #1D1F20
accent #5980A6

bg #1B1D1F
text #E9EAEB
accent #8FB8E0

Tonal ramps invert (100↔900) so tags, tints and hovers carry over unchanged. Suits: ♥♦ take the steel accent, ♠♣ take the text color — no red in this system.

[1a](#1a)Main timing console — light · the operator lives here

RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

*Sun Sept 20 2026 · Guelph Lake · 8 km loop

GORBA EPIC & MTB Festival

Elapsed

2:41:07

Remaining

3:18:53

● RUNNING — started 10:00:12

Arm
Stop ride…*

Rider plate — Enter records crossing

127

↩ record · Esc clear · ⌘Z undo last

Last crossing — 12:41:07

#127 · Sarah Okafor — Dirt Dynamos · team of 3

Lap **4** · lap time **31:22** · total **2:40:55**

Q
♠

Q
♠

card dealt
Q♠ — 4th card
shoe: 223 left

Undo last
Edit crossing…

Crossings — latest first
showing 13 of 214 · scroll for more

| Time | # | Entry / rider on course | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 12:41:07 | 127 | Dirt Dynamos · Sarah Okafor | 4 | 31:22 | 2:40:55 | Q ♠ |
| 12:40:44 | 61 | Marc Tremblay · solo | 5 | 29:48 | 2:40:32 | 9 ♦ |
| 12:40:12 | 88 | Moss Ridge Riders · Dev Patel | 6 | 27:59 | 2:40:00 | ★ JOKER |
| 12:39:51 | 12 | Ana Souza · solo | 4 | 33:15 | 2:39:39 | 4 ♣ |
| 12:39:20 | 143 | Chain Gang · Liz Warner | 5 | 30:06 | 2:39:08 | K ♥ |
| 12:38:58 | 33 | Peter Kim · solo | 3 | 44:02 | 2:38:46 | 7 ♠ |
| 12:38:31 | 102 | Lake Effect · Josh Bright | 5 | 28:47 | 2:38:19 | A ♦ |
| 12:37:55 | 94 | Ted Novak · solo | 4 | 12:44 ⚠ | 2:37:43 | HELD — short lap |
| 12:37:28 | 56 | Fat Tire Four · Sam Ellis | 5 | 31:11 | 2:37:16 | 3 ♥ |
| 12:36:59 | 19 | Owen Clark · solo | 4 | 32:05 | 2:36:47 | J ♠ |
| 12:36:30 | 150 | Singletrack Sisters · Mia Chen | 5 | 29:52 | 2:36:18 | 8 ♦ |
| 12:36:02 | 7 | Luca Ferrari · solo | 5 | 28:30 | 2:35:50 | Q ♦ |
| 12:35:41 | 168 | Grinder Bros · Alex Roy | 4 | 34:19 | 2:35:29 | 6 ♣ |

Ride counters

214

crossings

209

cards dealt

41

on course now

178/180

entries active

Shoe — 8 decks · 2 jokers each

dealt 209432 total

223 remaining · reshuffles automatically at 0

Most laps — unofficial

#88Moss Ridge Riders**6**

#7Luca Ferrari**5**

#102Lake Effect**5**

#150Singletrack Sisters**5**

#61Marc Tremblay**5**

⚠ Needs review (1)

#94 lap 4 took **12:44** — under the 18:00 minimum. Card held until confirmed.

Confirm + dealVoid

● Autosaved 12:41:22 — every crossing committed to rides.db (WAL)
Space refocuses plate entry · F3 rider editor · F5 results

[1b](#1b)Main timing console — dark · same tokens, remapped

RiverCrossing — GORBA EPIC & MTB Festival 2026 — rides.db
–▢×

FileRideRidersCardsResultsViewHelp

*Sun Sept 20 2026 · Guelph Lake · 8 km loop

GORBA EPIC & MTB Festival

Elapsed

2:41:07

Remaining

3:18:53

● RUNNING — started 10:00:12

Arm
Stop ride…*

Rider plate — Enter records crossing

61

↩ record · Esc clear · ⌘Z undo last

Last crossing — 12:41:07

#127 · Sarah Okafor — Dirt Dynamos · team of 3

Lap **4** · lap time **31:22** · total **2:40:55**

Q
♠

Q
♠

card dealt
Q♠ — 4th card
shoe: 223 left

Undo last
Edit crossing…

Crossings — latest first
showing 8 of 214 · scroll for more

| Time | # | Entry / rider on course | Lap | Lap time | Total | Card |
|---|---|---|---|---|---|---|
| 12:41:07 | 127 | Dirt Dynamos · Sarah Okafor | 4 | 31:22 | 2:40:55 | Q ♠ |
| 12:40:44 | 61 | Marc Tremblay · solo | 5 | 29:48 | 2:40:32 | 9 ♦ |
| 12:40:12 | 88 | Moss Ridge Riders · Dev Patel | 6 | 27:59 | 2:40:00 | ★ JOKER |
| 12:39:51 | 12 | Ana Souza · solo | 4 | 33:15 | 2:39:39 | 4 ♣ |
| 12:39:20 | 143 | Chain Gang · Liz Warner | 5 | 30:06 | 2:39:08 | K ♥ |
| 12:38:58 | 33 | Peter Kim · solo | 3 | 44:02 | 2:38:46 | 7 ♠ |
| 12:38:31 | 102 | Lake Effect · Josh Bright | 5 | 28:47 | 2:38:19 | A ♦ |
| 12:37:28 | 56 | Fat Tire Four · Sam Ellis | 5 | 31:11 | 2:37:16 | 3 ♥ |

Ride counters

214

crossings

209

cards dealt

41

on course now

178/180

entries active

Shoe — 8 decks · 2 jokers each

dealt 209432 total

223 remaining · reshuffles automatically at 0

⚠ Needs review (1)

#94 lap 4 took **12:44** — under the 18:00 minimum. Card held until confirmed.

Confirm + dealVoid

● Autosaved 12:41:22 — every crossing committed to rides.db (WAL)
Space refocuses plate entry · F3 rider editor · F5 results

[1c](#1c)Ride setup

Ride setup
–▢×

Ride name*Event date

Venue / course

Organizer

Scorer

Organizer logo*
*gorba-logo.png · replace…

Schedule & timing

Entries

Solo only
Solo + teams

Max riders per team

New rides default to **Solo only** — team fields stay hidden everywhere. Max riders per team: 2–10, default 4 (the EPIC uses 4).

Planned start

Duration

Lap length

Minimum lap time

Crossings faster than the minimum are flagged and their card is held for review. Start can also be set after the fact from the console if the gun is missed.

The shoe

Decks shuffled together

Jokers per deck (wild)

0
2
4

8 × 54 = 432 cards for 180 entries. Duplicates across entries are expected; the shoe reshuffles if it empties.

Cards per entry

One card per completed lap — no cap
Cap at 10 cards; laps still count past the cap

Tie-break order*⋮⋮**1**Most laps completed

⋮⋮**2**Shortest total time

⋮⋮**3**High-card draw at venue

Drag to reorder. Applies only between identical best hands — and can be changed again on the results screen before export.

Cancel
Create ride

[1d](#1d)Rider editor + CSV

Riders & entries — GORBA EPIC & MTB Festival 2026
–▢×

Search name, team or plate…

*All 180
Solo 98
Teams 82

Import CSV

Export CSV

Add entry

| Plate | Entry | Type | Riders | Status |  |
|---|---|---|---|---|---|
| 7 | Luca Ferrari | SOLO | — | Active |  |
| 12 | Ana Souza | SOLO | — | Active |  |
| 127 | Dirt Dynamos | TEAM ×3 | Sarah Okafor · Priya Nair · Tom Hale | Active |  |
| 33 | Peter Kim | SOLO | — | Active |  |
| 56 | Fat Tire Four | TEAM ×4 | Sam Ellis · Kat Diaz · Rob Finch · Amir Wahid | Active |  |
| 61 | Marc Tremblay | SOLO | — | Active |  |
| 88 | Moss Ridge Riders | TEAM ×4 | Dev Patel · Jo Lindqvist · Casey Muir · Ben Alton | Active |  |
| 94 | Ted Novak | SOLO | — | DNF 12:58 |  |
| 102 | Lake Effect | TEAM ×3 | Josh Bright · Nina Kovac · Will Trent | Active |  |
| 143 | Chain Gang | TEAM ×2 | Liz Warner · Omar Haddad | Active |  |

180 entries · 262 riders · next free plate **181** · delete is enabled only before the start — after it, entries are DNF'd or voided, never removed
CSV: plate, entry_name, type (solo | teamN ≤ ride max), rider_1…rider_N — import matches on plate, conflicts listed before writing; re-import reshapes teams freely until the ride starts. Solo-only rides hide Type & Riders columns and the Solo/Teams filter.

[1e](#1e)Entry detail — cards, hand, laps

Entry #127 — Dirt Dynamos
–▢×

#127 · Dirt DynamosTEAM ×3

Sarah Okafor (2 laps) · Priya Nair (2) · Tom Hale (2)

Add card…
Edit a crossing…
Mark DNF

6

laps

6

cards held

3:44:10

total time

37:21

avg lap

Laps — as recorded at 13:44:22

| Lap | Rider on course | Crossed at | Lap time | Card dealt |  |
|---|---|---|---|---|---|
| 1 | Sarah Okafor | 10:31:40 | 31:28 | Q ♦ |  |
| 2 | Priya Nair | 11:04:55 | 33:15 | 4 ♣ |  |
| 3 | Tom Hale | 12:09:45 | 1:04:50 incl. pit break | 9 ♣ |  |
| 4 | Sarah Okafor | 12:41:07 | 31:22 | Q ♠ |  |
| 5 | Priya Nair | 13:13:30 | 32:23 | 9 ♥ |  |
| 6 | Tom Hale | 13:44:22 | 30:52 | ★ JOKER |  |

Every edit here is written to the audit log with who/when/why — nothing is deleted, only voided.*
Cards held — 6

Q
♠

Q
♠

Q
♦

Q
♦

★JOKER

9
♥

9
♥

9
♣

9
♣

4
♣

4
♣

Steel border = plays in the best hand · faded = unused

Best hand — best 5 of 6

Full house
Queens over nines

Joker plays as Q — Q♠ Q♦ ★ 9♥ 9♣
Rank 146 of 7,462 possible hands
Currently **3rd** of 180 entries

[1f](#1f)Finish & results export

Results — GORBA EPIC & MTB Festival 2026
–▢×

■ FINISHED 16:00:00
6:00:00 elapsed · 1,124 crossings · 1,092 cards dealt · 177 entries with a hand
Reopen ride…

Standings — best poker hand

| P | # | Entry | Laps | Best 5 cards | Hand |
|---|---|---|---|---|---|
| 1 | 88 | Moss Ridge Riders | 11 | 9♠9♦9♣★K♥ | FOUR OF A KIND — NINES |
| 2 | 7 | Luca Ferrari | 10 | A♠A♦★4♣4♥ | FULL HOUSE — ACES / FOURS |
| 3 | 127 | Dirt Dynamos | 10 | Q♠Q♦★9♥9♣ | FULL HOUSE — QUEENS / NINES TIE → laps 10 v 9 |
| 4 | 56 | Fat Tire Four | 9 | Q♥Q♣Q♠9♦9♠ | FULL HOUSE — QUEENS / NINES |
| 5 | 61 | Marc Tremblay | 10 | K♦J♦8♦6♦2♦ | FLUSH — K HIGH |
| 6 | 150 | Singletrack Sisters | 9 | 9♣8♦7♠6♥5♣ | STRAIGHT — 9 HIGH |
| 7 | 143 | Chain Gang | 8 | 7♠7♥7♣K♦J♠ | THREE OF A KIND — SEVENS |
| 8 | 12 | Ana Souza | 9 | K♠K♥5♣5♦A♥ | TWO PAIR — KINGS & FIVES |
| 9 | 102 | Lake Effect | 9 | 10♠10♥4♣4♠Q♦ | TWO PAIR — TENS & FOURS |
| 10 | 19 | Owen Clark | 8 | A♣A♠K♥J♣8♦ | PAIR — ACES |

177 more rows below — full field, every entry listed with laps, cards and hand. ★ = joker, shown as played.

Publish results — HTML

Show lap & total times — off by default; this setting alone decides, the published page has no toggle
✓Laps leaderboard
Fastest total time leaderboard
✓Full field listing
✓GORBA logo
Podium poster PDF (one page, top 3)
All cards drawn per entry (not just best 5)

Tie-break between identical hands

*① Laps
② Time
③ Draw

Changing this re-runs standings instantly — 1 tie currently resolved by laps.

Generate HTML

Export PDF…Preview in browser

epic-2026-results.html · self-contained · 96 KB · data embedded as JSON — post anywhere
epic-2026-results.pdf · Letter/A4 · same sections & toggles*
Pre-publish checks

✓ 0 crossings awaiting review
✓ 2 held cards resolved (1 dealt, 1 voided)
✓ 3 DNF entries included, marked
✓ Hand evaluator self-test passed (7,462 ranks)

[1g](#1g)Ride library

RiverCrossing — ride library
–▢×

Rides

One database, every event — rides.db

Back up database…

New ride

| Ride | Date | Organizer | Entries | Status |  |
|---|---|---|---|---|---|
| GORBA EPIC & MTB Festival 2026 | Sun Sept 20 2026 | GORBA | 180 | ● RUNNING 2:41:07 | Resume |
| Arkell Fall Poker Run | Sat Oct 17 2026 | GORBA | 64 | DRAFT | OpenDuplicateDelete… |
| GORBA EPIC & MTB Festival 2025 | Sun Sept 21 2025 | GORBA | 171 | FINISHED | OpenDuplicateDelete… |
| Club Poker Night — Winter Loop | Sat Jan 24 2026 | GORBA | 38 | FINISHED | OpenDuplicateDelete… |

~/PokerRunTracker/rides.db · 4 rides · 2.1 MB
Duplicate copies riders & setup, no timing data · Delete asks you to type the ride's name, writes a backup first, and is never offered on a running ride

[1h](#1h)Safety dialogs — stop / resume / short lap

Start pressed — this ride already has data

GORBA EPIC was stopped at 12:41:22 with **214 crossings** recorded. Continuing keeps the original 10:00:12 start — the clock never stopped, so no time or data is lost.

Starting over archives this ride's data first — it is never deleted.

Start a new ride…Continue ride

Recovered — GORBA EPIC & MTB Festival

RiverCrossing closed unexpectedly at 12:41:22 while this ride was running. Every crossing was already saved. The ride clock runs on wall time — elapsed is now **2:47:35** and counting.

Ride libraryResume ride

#94 Ted Novak — lap faster than minimum

Lap 4 recorded at **12:44** against an 18:00 minimum. Usually a double-entry or a mistyped plate. The crossing is kept either way; only the card waits.

Void crossingReassign plate…Confirm — deal card

Try next: "swap the console rail for a laps big-board" · "show the results HTML page" · "make the entry field full-width kiosk size"
