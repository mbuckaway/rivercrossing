---
title: RiverCrossing — User Guide
lang: en
render: python tools/gen_userguide.py --write
---

# RiverCrossing — User Guide

Timing and poker-hand scoring for poker-run rides. The app opens this page in your browser from **Help ▸ User Guide** or **F1**. The chapters follow race day: set up, run, finish, publish.

<nav class="toc">
  <strong>Contents</strong>
  <ol>
    <li><a href="#getting-started">Getting started</a></li>
    <li><a href="#setting-up-a-ride">Setting up a ride</a></li>
    <li><a href="#riders-entries">Riders &amp; entries</a></li>
    <li><a href="#running-the-ride">Running the ride</a></li>
    <li><a href="#fixing-mistakes">Fixing mistakes</a></li>
    <li><a href="#stopping-quitting-recovery">Stopping, quitting &amp; recovery</a></li>
    <li><a href="#finishing-standings">Finishing &amp; standings</a></li>
    <li><a href="#publishing-results">Publishing results</a></li>
    <li><a href="#poker-the-run-way">How scoring works</a></li>
    <li><a href="#card-shoe">The card shoe</a></li>
    <li><a href="#ranking-the-field">Ranking the field</a></li>
    <li><a href="#hand-rankings">Hand rankings</a></li>
    <li><a href="#scoring-references">Scoring references</a></li>
    <li><a href="#evaluator-self-test">Evaluator self-test</a></li>
    <li><a href="#troubleshooting-faq">Troubleshooting &amp; FAQ</a></li>
    <li><a href="#appendix-a-shortcuts">Appendix A — Keyboard shortcuts</a></li>
    <li><a href="#appendix-b-csv-reference">Appendix B — CSV reference</a></li>
  </ol>
</nav>

## Getting started {: #getting-started }

RiverCrossing is a keyboard-first desktop app for Windows and macOS. The operator types a rider's plate and presses <kbd>Enter</kbd> at each lap crossing. The app records the lap, deals a card from a seeded virtual shoe, and at the finish scores every entry's best 5-card poker hand.

### About RiverCrossing {: #about }

**Help ▸ About RiverCrossing** shows the product name and installed version, the author line, "© 2026 Mark Buckaway", and the GPL-3.0-only licence. The version comes from the installed package, so it always matches the build you run.

### Installing

- **Windows:** run the installer and follow the prompts. Windows may show a SmartScreen "More info → Run anyway" step on the unsigned build.
- **macOS:** drag the app into Applications, or open the DMG and copy it, then launch. Gatekeeper may ask you to confirm opening an unsigned app once.

### First launch and the ride library

The app opens on the timing console. When no ride is open, the menus are how you get started. **Ride ▸ New Ride…** opens the ride setup window. **File ▸ Ride Library** lists every ride in the one database. From the library you can open a ride, duplicate one (copies setup plus riders, no timing data), or delete one (you must type the ride's name to confirm, and a backup is written first). A running ride is never deletable.

### Settings {: #settings }

**File ▸ Settings** (or the app menu on macOS) controls the operator-comfort layer:

- **Appearance** — System (follow the OS), Light, or Dark. All three work on both platforms. On Windows a change applies at the next launch. macOS applies it live.
- **Sound on crossing** — three cues: a click when a crossing records, a two-tone alert when a short-lap crossing is held for review, and a buzz for a refused plate or a miss.
- **Show Total Times on Crossings Panel** — off by default. Turns the feed's Total column on or off mid-ride. Times are still recorded underneath.
- **Show Lap Time in Crossings Panel** — on by default. Turns the feed's Lap time column on or off mid-ride. The two time columns are independent.
- **Verbose logging** — on by default. Writes a diagnostic log for support. Each launch gets its own file, `rivercrossing-<date-time>.log`, in your per-user configuration folder (`~/Library/Application Support/RiverCrossing` on macOS, under `%LOCALAPPDATA%` on Windows). It records the launch, the ride loaded, and any crash as one JSON record per line. While it is on, it also records the action trail of menu picks, dialogs opened, and button events. Turning it off stops the trail at once. The newest 20 session files are kept.
- **Avg Lap Time (kmh)** — the rider field's average speed, a decimal, 12.0 by default. It sets the simulator's default minutes between laps.

Appearance and text zoom each live on one surface. The theme radios are in Settings only. Text zoom lives on the View menu's Zoom 90%–150% rows only. The two time-column settings each keep two controls, their Settings checkbox and their View-menu item, and each pair stays in sync.

### Where your data lives

All rides live in one SQLite database, `rides.db`, in your user data folder. Every crossing commits as it happens, so a crash loses at most a keystroke. Backups are written on open and hourly while the app is running, into a `rides.db.backups/` sibling folder, pruned to the newest 20.

## Setting up a ride {: #setting-up-a-ride }

**Ride ▸ New Ride…** opens the setup window for a new ride. **Ride ▸ Edit Ride…** reopens the same window preloaded with the open ride's settings. New Ride… is enabled only while no ride is open. Edit Ride… is enabled only while one is. Existing rides open from the ride library.

The setup window has these fields:

- **Name, Date, Planned start, Venue** — the identity shown on the console and in published results.
- **Lap length km, Organizer, Scorer** — shown on the console and in exports.
- **Duration (H:MM)** and **Min lap (M:SS)** — the riding window and the shortest lap that is not flagged.
- **Logo** — an image file, embedded in exports. Optional.
- **Short-lap policy** — **Hold short-lap cards for review** (the default) or **Always deal cards**. A lap faster than the minimum is flagged either way. The policy decides what happens to its card.
- **Entries** — **Solo riders only** or **Solo + teams (default)** with a **Max riders per team** (2–10, default 4).
- **Plate model** — **Rider plates — pooled (default)**: every rider has their own plate and draws one card per lap, uncapped, and the team's hand scores from the pooled cards. **Team plate — relay**: one plate per team, one rider on course at a time.
- **Cards** — **Decks** (default 8), **Jokers** (0–10, default 1, wild), and the jokers mode **Per deck** or **Total** (default). **Card cap** sets how many of an entry's cards score.
- **Tie-break order** — the ordered list that resolves identical best hands. Use the Up and Down buttons to reorder. It opens on high-card draw, then most laps, then shortest total time.

The ride needs a **name, venue, organizer, scorer**, and a **lap length** before the dialog accepts the setup. It lists what is missing if you save without them. The ride cannot start until those are set and the roster has at least one rider. Duration and minimum lap time must be valid H:MM and M:SS values.

Once the ride starts, its format locks. Entry mode, plate model, the shoe, the card cap, and the tie-break order are frozen. On a rider-pooled ride, Max riders per team stays editable. The name, date, start, venue, organizer, and scorer stay editable in a running ride.

### Filling a field with the simulator {: #simulator }

**File ▸ Simulation…** fills the open draft ride with a placeholder field and rehearses a race on it. This is how you demo the app or practise the console before the real riders arrive.

- **Number of riders** (default 175) and **Number of teams** (default 40) set the field size. **Solo riders** fills itself in from those two, and **Check** explains the relationship in an OK-only dialog. With the defaults, 175 riders fill 40 teams to 160 team riders and leave 15 solo.
- **Number of laps** sets how many laps the run rehearses.
- **Minutes between first rider** opens on one lap at your **Avg Lap Time (kmh)** plus a five-minute buffer, so 8 km at 12 km/h gives 45 minutes. The field stays editable.
- **Generate Riders** writes the placeholder riders and teams into the roster. **GO** then runs the race: it back-dates the start, records crossings at whole minutes within the interval, and leaves the ride stopped when it finishes. One seed reproduces the same run every time.
- **New Ride** is the one live button when no ride is open: it routes to the ride setup window.

Three dropdowns bend the rehearsal so you can see the console's review surfaces without waiting for a real problem. Each offers **Disabled** and **1–10**, and each count takes the leading riders of the run's own crossing order:

- **Short-lap riders** (1 by default) cross impossibly fast, so each of their laps is flagged and, under the hold policy, its card is held.
- **Lapped riders** (Disabled by default) sit out the first wave only, then lap normally and finish one lap behind.
- **Team riders stop after 4 laps** (Disabled by default) record their first four laps and then never cross again. They are not marked DNF. On a pooled ride the team still completes every lap; the rotation slot passes to the next active rider. On a relay ride the team crosses under its own plate, so this dropdown changes nothing.

The simulator fields and counts are remembered, so they come back the next time you open the window.

## Riders & entries {: #riders-entries }

**Riders ▸ Rider Editor** manages the roster: plates, names, and teams. Riders need a first name and a last name in the editor. The CSV import also accepts a single name.

- **Add rider…** opens the Add Rider form: plate, first name, last name, team, and sex (blank, M, or F). A solo rider's entry is listed under that full name. A new rider onto an existing team is added by picking the team from the list. Plates are unique across the ride, and a new entry's plate is prefilled with the next free number.
- **Edit Rider…** changes the selected rider. **Delete** is available while the ride is a draft. Once an entry has recorded data, it is never removed. Mark it DNF or void its crossings instead.
- **Teams Editor** (**Riders ▸ Teams Editor**, mixed rides only) edits a team's name, notes, relay plate, and logo. The logo is a playing card picked with **Pick card…**. **Add team…** creates a team from the form, and a blank or duplicate name is refused. Membership is read-only here; the Rider Editor owns who is on which team. The **Only show one-rider teams** checkbox filters the list.
- **Check for Rider Issues…** (**Riders ▸ Check for Rider Issues…**) lists roster problems before the start. It shows the card-sufficiency line (does the shoe hold enough cards for the field?) and each issue, with buttons to open the editor, convert a one-rider team to solo, assign a plate, or renumber plates.
- **Mark DNF…** (**Riders ▸ Mark DNF…**) takes a rider's plate (or a whole entry's plate) and marks that rider out. Every lap and card they recorded is kept, and a pooled team member can be marked without ending the team. The rider's cards are forfeit from the team's hand. A team drops only when every one of its riders is out, and DNF riders are excluded from the results.
- **Import / Export CSV** (**File ▸ Import Riders CSV… / Export Riders CSV…**) brings a roster in from a file or writes one out. One header-mapped format works for every ride. See Appendix B.

## Running the ride {: #running-the-ride }

The console is keyboard-first. The operator types a plate and presses <kbd>Enter</kbd>. The top of the window holds the ride header, and the lower part holds the crossings feed and the review sidebar.

**The header:**

- **Status box** — the stop light and the status word together: green running, amber stopped or reopened, red draft or finished.
- **Ride box** — Name, Date, Venue. **Details box** — Organizer, Scorer, Lap length km. The ride's **logo** sits to the right.
- **Current Lap** — a light-green two-digit count that rises as riders cross.
- **Elapsed** and **Remaining** — analog dials with numeric readouts. Elapsed counts from the start; Remaining counts down to the planned finish.
- **Start ride** and **Stop ride…** — the round start and stop buttons at the right, a green play glyph and a red square.

**The working surface:**

- **Record crossing row** — the Plate field, the **Record (Enter)** button, the last-crossing readout, and the **Undo last (Ctrl+Z)** button.
- **Crossings feed** — every crossing the ride records, newest first. The whole ride scrolls. The **Search** box above narrows the list as you type. Columns sort on a header click and resize from their edges. The feed opens sorted by Time, newest first. A DNF rider's row carries a "DNF" marker beside the name.
- **Review sidebar** — six counter chips and a notebook:
  - **Crossings** — crossings recorded. **Cards dealt** — cards credited (recorded minus held). **On course** — entries out on the loop. **Shoe** — cards remaining out of the total. **Riders** and **Teams** — the registered totals.
  - **Riders** tab — the whole roster. Double-click a rider (or select one and press Enter) to open the Rider Editor on that rider.
  - **Needs Review** tab — crossings that want a decision, with the reason in the Issue column and the card's state in the Card column. **Show Held Cards Only** lists only unresolved items. **Review…** opens the right dialog for the selected row.

**Start, stop, and the clock:**

- **Start** — press the Start button (or **Ride ▸ Start Ride**). A ride with no riders, or a setup missing a required field, cannot start; the app says what is missing. A stopped ride resumes with the same button and keeps all data.
- **Stop** — press the Stop button (or **Ride ▸ Stop Ride…**) and confirm. Stopping locks the entry field and freezes the clock display at the stop instant. The ride clock keeps counting on wall time underneath, so nothing is lost. Start resumes whenever you are ready.
- **Set Start Time…** (**Ride ▸ Set Start Time…**) back-dates the start and recomputes lap-1 times when you missed the gun.
- **Undo** — <kbd>Ctrl+Z</kbd> (or the Undo button) removes the last crossing and returns its card.

**Short laps and duplicates:**

- A lap under the ride's minimum lap time is flagged and listed on the Needs Review tab. The Card column shows **Held** (waiting), **Credited**, or **Void**.
- A **duplicate crossing** (the same entry recorded twice at the identical instant) also appears on the Needs Review tab. It can arise from a mis-key or a correction.
- A short lap on a team entry reads **Team overlap** in the Issue column and is resolved the same way as a short lap.

## Fixing mistakes {: #fixing-mistakes }

Every correction is a command with a reason, and every one is written to the audit log. Nothing is ever deleted; it is voided or edited.

- **Undo last crossing** — the fastest fix for a mistyped plate.
- **Edit a crossing** — change its time, or void it. Later lap times for the entry recompute.
- **Add a crossing at time** (**Cards ▸ Add Crossing at Time…**) — a rider missed at the line. Give the plate and the exact time.
- **Reassign a plate** — move a crossing to the plate it belonged to. The card moves with it.
- **Deal a bonus card** (**Cards ▸ Deal Bonus Card…**) — deal the next card from the shoe straight into an entry's hand, with a reason. A manual deal is a deliberate credit.
- **Void a card** — remove a card from an entry's scored hand. The card does not return to the shoe.
- **Mark DNF** — see Riders & entries.
- **Audit trail** (**Ride ▸ Audit Trail…**) — every action, newest first, with a plate search and an action filter. Each row shows when, what, which entry, and the reason.

### Crossing detail {: #entry-detail }

The **Crossing Detail** window opens from a feed row or a Needs Review row. It shows one crossing's rider, team, plate, lap number, crossing time, lap time, total time, card, and status. Its buttons edit the plate, edit the time, void the card, or delete the crossing. Deleting the newest crossing undoes it and returns its card; deleting any other voids it and renumbers the entry's later laps.

### Clearing the Needs Review tab

Select a row on the Needs Review tab and press **Review…**, or double-click it. The app asks the right question for that row:

- **Short lap** — the card is **Held**: dealt but not credited. **Review…** opens a dialog with **Confirm card**, **Void card**, and **Cancel**. Confirm releases the card to the hand. Void keeps it out for good. Cancel leaves it held. Either way the crossing stays in the ride, and the decision is audit-logged.
- **Credited or Void** — a card you already decided. **Review…** opens **Return to Held** so you can put it back for review.
- **Duplicate crossing** — two laps instead of one. **Review…** opens the Crossing Detail. Press **Delete** and confirm to remove one of the pair. Only you saw which keystroke was the mistake.
- **Team overlap** — the same decision as a short lap.

## Stopping, quitting & recovery {: #stopping-quitting-recovery }

- **Stop** — press the Stop button (or **Ride ▸ Stop Ride…**) and confirm the native prompt. Stopping locks the entry field and freezes the clock display. Start resumes whenever you are ready.
- **Quit with a ride running** — the exit dialog offers Cancel, **Finish ride first…**, or **Quit — keep ride running**. Quitting keeps the ride live on the wall clock. The next launch asks you to continue it, and the resume dialog says whether the last exit was a clean quit or a crash.
- **Clear Ride…** (**Ride ▸ Clear Ride…**) unloads the open ride from memory after a confirmed danger prompt. It does not delete the ride from the database.
- **Crash recovery** — every crossing commits as it happens, so a crash loses at most a keystroke. The clock is wall time, so elapsed time is never lost.
- **Backups** — written on open and hourly while the app is running. **File ▸ Back Up Database…** writes one on demand.

## Finishing & standings {: #finishing-standings }

- **Finish ride** — **Ride ▸ Finish Ride…** locks entry, runs the hand evaluator's self-test (a red self-test blocks finishing), and computes the final standings. It publishes nothing by itself. Generate the results yourself from the Results menu.
- **Finish gate** — the evaluator self-test runs fresh on every finish. A red result blocks it. DNF riders are excluded outright, and held cards never count until you confirm them.
- **Reopen for corrections** — **Ride ▸ Reopen Ride** reopens a finished ride for corrections. Entry is locked, edits are highlighted in the crossings list, and Finish again re-locks and re-ranks. After corrections, the Standings dialog flags the published results as stale until you export again.
- **Continue a reopened ride** — Start stays live while a ride is reopened. Pressing the Start button continues it: entry unlocks, the clock goes live again from the original start, and the recorded finish is discarded.

### The Standings window {: #results-window }

**Results ▸ Standings** (<kbd>F5</kbd>) shows the full field with place, plate, entry, laps, best-5 cards, and the hand name. A ⚠ badge beside a tied place gives the tie's explanation on a double-click. On a mixed ride the standings sit on two notebook pages, **Teams** and **Solo**, each numbered from 1. On a rider-pooled ride the Teams tab drops its Plate column, because a pooled team's plate comes from its members. The dialog is modal, so close it to get back to the console.

## Publishing results {: #publishing-results }

- **Export HTML…** — one self-contained file you can post anywhere. On a mixed ride it renders per-kind sections: top 3 teams then top 3 solo riders on the podium, Top teams and Top solo riders (five each), Most laps split teams and solo (five each), and the Full field with a compact plate-less Teams subsection followed by the Solo riders with plates. A solo ride keeps the single-kind page.
- **Export PDF…** — the same sections as the HTML export, as a printable report.
- **Podium Poster PDF…** — a single celebratory page for the prize table: top 3 teams plus top 3 solo riders on a mixed ride, or the top 5 solo riders on a solo ride.
- **Podium Poster HTML…** — the same poster as a self-contained page.
- **Export Standings CSV…** — place, plate, entry, type, sex, laps, hand, and times when shown.
- **Preview HTML in Browser** / **Preview Podium Poster HTML in Browser** / **Preview PDF in Browser** — open this session's last export of that format in your browser. Each needs a finished ride and an export made this session. After a restart the preview is disabled until the next export.
- **Publish Options** — five checkable rows on the Results menu that shape every export: **Show lap & total times**, **Laps leaderboard**, **Fastest-time leaderboard**, **Full field**, and **All cards drawn**. With show-times off, the exports embed no time data, and the fastest-time leaderboard is cleared and disabled.

## How scoring works {: #poker-the-run-way }

It is not a race. It is a poker run. Each completed lap deals one card. At the finish, every entry's **best 5-card hand from all cards drawn** is scored. Jokers are wild: a joker plays as any card you need, but only when it actually improves the hand. A spare joker that does not improve it is left unused.

### How a card is selected on a crossing

One accepted crossing deals exactly one card. The card comes from the front of the shoe's remaining list. A refused entry — not running, stopped, or an unknown plate — deals nothing. Undo Last Crossing returns the card to the front of the shoe, so the next deal re-deals it. Voiding a crossing or card removes the card from the system; it does not go back into the shoe. Reassigning a crossing moves its card with it; no new card is dealt.

A short lap still records. Under the default hold policy, its card is dealt into a held state and only credited on Confirm. Under always-deal, it is credited like any other lap.

### How the best five are selected

The app scores the best 5-card hand from the entry's credited cards. For five or more cards, it builds one candidate per reachable hand shape directly from the rank and suit counts. A joker plays only where it improves the hand. When a joker does not help, it stays unplayed, and a natural hand beats an equal hand that used a joker.

The card cap limits how many of an entry's cards score. Disabled (the default) scores every credited card. A number from 5 to 20 scores the best five of the first N credited cards. Laps and times are unaffected: laps past N still count, and their cards are still dealt.

### Fewer than five cards

An entry holding fewer than five cards still ranks. Its cards score as the best partial hand they make. A partial hand can never be a flush or a straight, because both need all five cards. A missing kicker always ranks below a present one, however good the rest of the hand is. An entry with no cards at all has no hand name.

### How hands are compared

The app compares two hands by three things, in order: the hand class, then the kickers, then the number of jokers played. A higher class wins. Equal classes compare their kickers. Equal class and kickers go to the hand that used fewer jokers, so a natural hand beats a wild one and one joker beats two. This is part of hand strength, so it is settled before the ride's laps and total-time tie-breaks.

## The card shoe {: #card-shoe }

The shoe is the single shuffled list of cards the ride builds once and draws from, in order, for its whole life.

### How the shoe is created

When the ride is created, the app draws a random **seed** from your operating system's secure random source and stores it with the ride. The shoe is that seed's shuffled list of decks and jokers.

The default shoe is **8 decks of 52 natural cards plus 1 joker**. The jokers mode decides how that joker works:

- **Per deck** — every shuffle re-deals 1 joker per deck (8 jokers), for 424 cards a shuffle.
- **Total** (the default) — the ride gets 1 joker once, for 417 cards in the first shuffle. After that joker is dealt, the shoe deals naturals only.

The shuffle is an unbiased Fisher–Yates shuffle seeded from that random seed. The order is random and unpredictable to riders, but the seed is stored, so replaying the ride from the database reproduces exactly the cards everyone drew. That is the crash-and-restart guarantee.

### How cards are dealt

The next card is always the front of the remaining list. Every crossing takes the next card, so the dealt order is exactly the shuffled list, first to last. If the shoe empties, it reshuffles automatically under a new seed and keeps dealing. Undo puts a card back at the front. Voiding does not. Reassigning moves the card with the crossing.

The shoe's size is part of the ride's format. You can change it while the ride is a draft. Once the ride starts, those fields are disabled in **Ride ▸ Edit Ride…**, so the live shoe and the recorded ride never disagree. The shoe closes on Finish and reopens on Reopen.

## Ranking the field {: #ranking-the-field }

### While the ride is running

While a ride is not finished, the standings board ranks by **most laps, then shortest total time**. A hand tie never shows as an unresolved draw on the live board. This live order lets the operator see progress without waiting for the finished result.

### When the ride is finished

At the finish, the app ranks every active entry by its best hand, strongest first. Entries with hands that are equal in class, kickers, and joker count tie, and the ride's **tie-break order** resolves the tie. That order is set in the Ride Setup window and locked once the ride starts. It opens on:

1. **High-card draw** at the venue — resolves nothing in the app, so an unresolved pair is flagged "draw required" for the venue to settle.
2. **Most laps** — more laps wins.
3. **Shortest total time** — the faster entry wins.

The app never guesses a tie. When the criteria leave two entries unresolved, they share a place and are flagged. A two-way draw reads 1, 2, 2, 4, because the run after a draw starts one place past it.

DNF entries are excluded entirely: not placed, not listed, not exported. On a mixed ride, teams and solo riders are ranked separately, each numbered from 1, because a team's pooled cards would dominate most solo hands.

## Hand rankings {: #hand-rankings }

This list is the exact order the app uses, strongest first. A hand lower on the list beats every hand below it.

1. **Five of a kind** — needs a joker, or five identical physical cards from a multi-deck shoe.
2. **Royal flush** — A-K-Q-J-10 of one suit.
3. **Straight flush** — five in a row of one suit.
4. **Four of a kind**.
5. **Full house** — three of a kind plus a pair.
6. **Flush** — five of one suit.
7. **Straight** — five in a row. The wheel, A-2-3-4-5, counts and plays as a five-high straight, never ace-high.
8. **Three of a kind**.
9. **Two pair**.
10. **One pair**.
11. **High card**.

Duplicates across one entry are legal. A multi-deck shoe can deal the same card twice, so 9H 9H is a pair of nines. Two entries can also hold the same card code, because the shoe is shuffled, not dealt from a single deck.

## Scoring references {: #scoring-references }

These sources describe the rules the app follows. They corroborate the hand rankings and the shuffle.

- [List of poker hands](https://en.wikipedia.org/wiki/List_of_poker_hands) — the hand-ranking order.
- [Five of a kind](https://en.wikipedia.org/wiki/Five_of_a_kind) — five of a kind with jokers wild.
- [Wild card (cards)](https://en.wikipedia.org/wiki/Wild_card_(cards)) — jokers as wild cards.
- [Fisher–Yates shuffle](https://en.wikipedia.org/wiki/Fisher%E2%80%93Yates_shuffle) — the shuffle the shoe uses.
- [PokerHandEvaluator (phevaluator)](https://github.com/HenryRLee/PokerHandEvaluator) — the natural-card evaluator the app wraps.

## Evaluator self-test {: #evaluator-self-test }

The hand evaluator is the one part of the app nobody can check by eye mid-race, so it checks itself. The app runs the evaluator self-test silently at every launch. **Help ▸ Run Evaluator Self-test** opens the same suite in a window, with its PASS/FAIL lines and a **Run again** button. Every check scores live against the evaluator you are running.

The six checks, in order:

1. **7,462 distinct ranks** — the packaged rank table sorts to exactly the 7,462 natural 5-card ranks, with no gap and no repeat.
2. **Joker vector table (28)** — the 28 hand-authored wild-card vectors each still evaluate to their expected hand class and kickers.
3. **Five-of-a-kind ordering** — five of a kind outranks a royal flush, and a natural five of a kind outranks a wild one.
4. **Whole-field 180×12 timing** — a seeded 180-entry field of 12-card hands scores inside its budget.
5. **compare() total order** — hand comparison is still a strict total order, so the standings sort by the hands, not by crossing order.
6. **best_hand() joker bound** — a best hand never plays more than five jokers, even when the pool holds six or seven.

A red self-test blocks finishing a ride. A red one at launch opens the window so you can read it before carrying on. A green launch run stays silent. The self-test never changes anything it reads.

## Troubleshooting & FAQ {: #troubleshooting-faq }

- **Missed the start** — use **Ride ▸ Set Start Time…** to back-date it. Lap-1 times recompute.
- **Typed the wrong plate** — undo the crossing, or edit or reassign it and record a reason.
- **A rider crossed twice** — void the extra crossing. Its card voids with it.
- **The shoe ran out** — it reshuffles automatically. Deals continue.
- **Moving the database to another machine** — copy `rides.db`, and its `rides.db.backups/` folder if you want the history. The app opens it in place on the new machine.
- **Restoring a backup** — replace `rides.db` with the backup file, then relaunch.
- **Theme change on Windows** — a dark or light switch applies at the next launch. The status bar says so.

## Appendix A — Keyboard shortcuts {: #appendix-a-shortcuts }

The same shortcuts appear in **Help ▸ Keyboard Shortcuts**, generated from the accelerator table so they never drift. On macOS, <kbd>Ctrl</kbd> reads as <kbd>⌘</kbd>.

| Key | Action |
|---|---|
| <kbd>Enter</kbd> | Record crossing for typed plate |
| <kbd>Ctrl+Z</kbd> | Undo last crossing |
| <kbd>F5</kbd> | Standings window |
| <kbd>F1</kbd> | User guide (this page) |
| <kbd>F2</kbd> | Edit crossing (open detail) |
| <kbd>Delete</kbd> | Delete selected crossing |
| <kbd>Ctrl+D</kbd> | Delete selected crossing |
| <kbd>Ctrl+E</kbd> | Edit crossing plate |

## Appendix B — CSV reference {: #appendix-b-csv-reference }

Every roster import and export uses one header-mapped format, with one row per rider. The app reads the header row and maps each column to a field by name, not by position, so you can export from a registration system, add or reorder columns, and it still imports.

### The canonical columns

| Canonical header | What it holds | Notes |
|---|---|---|
| `FIRSTNAME` | Rider's first name | At least one of FIRSTNAME or LASTNAME must appear in the header and in each data row. |
| `LASTNAME` | Rider's last name | Optional. A rider with only one name leaves it blank. |
| `TYPE` | `solo` or `team` | Blank means solo, unless the row names a team. |
| `TEAMNAME` | The team's name | Blank for solo rows. Rows sharing a team name form one team. Matching ignores case and extra spaces. |
| `NUMBER` | The rider's or team's plate | Optional. When blank, the app assigns the next free plate. |
| `NOTES` | A note on the rider or team | Optional. Team notes from member rows join into one team note. |
| `SEX` | The rider's sex, `M` or `F` | Blank means unknown. Any other value is a conflict unless you tick **Map unknown sex to Male**. |

Any column whose heading matches nothing is ignored. The app's own export always writes the canonical headers in this order. Rows with no first or last name are skipped, unless such a row still names a plate, team, or type, in which case it is reported as a missing name.

### Example

```text
FIRSTNAME,LASTNAME,TYPE,TEAMNAME,NUMBER,NOTES
Luca,Ferrari,solo,,7,
Dev,Patel,team,Moss Ridge Riders,,shifts at the hour
Jo,Lindqvist,team,Moss Ridge Riders,,shifts at the hour
Casey,Muir,team,Moss Ridge Riders,,
Sam,Ellis,solo,,,
```

### How plates are assigned

- Blank numbers get sequential free plates, in file order.
- On a rider-pooled ride, each row's number is that rider's own plate, and a team adopts its lowest-numbered member's plate.
- On a team-relay ride, a team rides one plate, so all of that team's rows must carry the same number, or all be blank.

### Importing

The import preview reports how many riders, teams, and conflicts the file contains, and lists every problem row before anything is written. Nothing changes until you confirm. Match-and-merge works by plate: an existing plate updates that entry in place, a new plate is added. Once a ride has started, most structure changes are refused. New plates and name fixes are accepted, and a new plate can still join an existing team.

Two options change how the file is read, and ticking either re-runs the preview:

- **Map unknown sex to Male** — every rider without a recognised `M` or `F` imports as male.
- **Convert teams of 1 to solo** — each one-rider team imports as that rider's solo entry. The conversion is draft-only.

### Conflict messages

- **Missing or malformed header** — no column could be read as a first or last name.
- **Missing name** — a row with no name that still names a plate, team, or type.
- **Duplicate plate within the file** — two rows claim the same plate.
- **Unknown entry type** — a TYPE value that is neither `solo` nor `team`.
- **Team too small or too large** — fewer than 2 riders, or more than the ride's max.
- **Structural change while running** — for example solo to team on a started ride.
- **Team rows carry different plates** — on a relay ride, a team's rows must name the same plate.

A finished-ride export adds four columns after the roster columns: `laps`, `cards`, `best_hand`, and `total_time`.
