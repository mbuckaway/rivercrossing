# Coding Standards — UX, Native Desktop Supplement

**Read alongside** `CODINGSTANDARDS-UX.md` (the platform-neutral core) and
`CODINGSTANDARDS-PYTHON.md`. This file supplies the concrete platform numbers and APIs the core
standard defers to with *"(see the platform supplement)"*.

**Applies to:** native desktop UI built with **wxPython on Windows 10/11 and macOS 13+**.

**Why this file exists:** `CODINGSTANDARDS-UX.md` requires a matching platform supplement and its review
checklist has a mandatory line for it, but it only names WEB / ANDROID / IOS — none of which covers a
native desktop app. Without this file the core standard's UX gate is unsatisfiable here.

**Authorities.** Apple *Human Interface Guidelines* (macOS) · Microsoft *Windows application design*
(Fluent) · **WCAG 2.2 Level AA** for contrast, keyboard operability and text scaling. Where the two
platform guidelines disagree, follow the host platform at runtime — never invent a third look.

---

## 1 · Native means native

- **ALWAYS** use standard platform controls, unmodified. wxWidgets renders real native widgets; that is
  the point of choosing it.
- **NEVER** restyle a standard control — no custom colours, fonts, borders or owner-drawn chrome on
  buttons, text fields, checkboxes, radio buttons, choices, list or tree controls.
- **NEVER** position controls absolutely. Every window is sizer-based so it resizes, honours system font
  size changes, and adapts to platform metrics.
- **NEVER** hard-code padding or font sizes to match a mockup pixel-for-pixel. Mockups are structural
  references; spacing comes from sizers and system metrics.
- Custom drawing is permitted only where no native control exists, and each instance must be justified
  in review.

## 2 · Keyboard is the primary input

This application is operated at speed by a scorer who should not have to look down.

- **ALWAYS** make every command reachable by keyboard. Anything achievable with the mouse has a keyboard
  path (WCAG 2.2 AA, operable).
- **ALWAYS** give menu items a mnemonic (`&File`) and put accelerators in **one single-source accelerator
  table** — the shortcuts dialog is generated from it, so the two cannot drift.
- **ALWAYS** use platform-conventional accelerators and let wx translate them: write `Ctrl+` and wx maps
  it to `Cmd` on macOS.
- **ALWAYS** define a deliberate tab order that follows reading order, and set a default button so
  `Enter` does the obvious thing.
- **ALWAYS** return focus to the control that opened a window when it closes.
- **NEVER** steal focus from a text-entry field while the user may be mid-keystroke. Buffer input
  instead.
- **NEVER** bind a destructive action to a bare, unmodified key.

## 3 · Dialogs

Extends core Rule 6. The functional harness asserts these per dialog.

- **ALWAYS** let `Esc` cancel, and make cancel non-destructive — never a shortcut to the harmful path.
- **ALWAYS** let `Enter` activate the marked default button.
- **ALWAYS** trap `Tab` inside a modal dialog.
- Initial focus: confirmation dialogs → the primary button; **destructive** confirmations → **Cancel**,
  so a reflex `Enter` is safe; form dialogs → the first input field.
- **ALWAYS** use the platform's standard button sizer (`wxStdDialogButtonSizer`) and stock IDs
  (`wxID_OK`, `wxID_CANCEL`, `wxID_CLOSE`, `wxID_DELETE`, …) so button order and labels are correct on
  each OS automatically. macOS and Windows order buttons differently; the sizer handles it.
- **ALWAYS** use the OS-native file and save pickers. Never build a custom file browser.
- **NEVER** use a modal dialog for information the status bar or an inline info bar can carry.
- **NEVER** show a modal with no way forward.

## 4 · Destructive actions

Extends core Rule 13.

- **ALWAYS** confirm a destructive action, or perform it immediately with a clear, time-boxed undo.
- **ALWAYS** name the object in the confirmation ("Delete *Club poker night*?"), never just "Are you
  sure?".
- **ALWAYS** make the confirmation's default button the safe one.
- For irreversible destruction of user data, require a **typed confirmation** of the object's name and
  keep the destructive button disabled until it matches exactly.
- **NEVER** put an irreversible action one keystroke away with no confirmation and no undo.

## 5 · Menus and platform relocation

- **ALWAYS** author one menu tree for both platforms and let wx relocate the platform-owned items:
  About, Preferences/Settings and Quit move into the macOS application menu automatically when given
  their stock IDs (`wxID_ABOUT`, `wxID_PREFERENCES`, `wxID_EXIT`). Assert the relocation in tests.
- **ALWAYS** disable — never hide — a command that is unavailable in the current state, so the UI does
  not reflow and users can still discover it.
- **ALWAYS** end a menu item that opens a dialog with an ellipsis (`Settings…`).

## 6 · Windows, sizing and DPI

- **ALWAYS** set a sensible minimum size and verify the window fits a **1366×768** display — the floor
  for a field laptop.
- **ALWAYS** persist and restore window geometry and splitter sash positions, and validate restored
  coordinates against the *current* display arrangement before applying them.
- **ALWAYS** be per-monitor DPI aware on Windows; test on a mixed-DPI setup where possible.
- **NEVER** assume a fixed window size or a single display.

## 7 · Accessibility floor (WCAG 2.2 AA)

- **Contrast:** text at least **4.5:1**, large text (≥18pt, or ≥14pt bold) at least **3:1**. Using
  unmodified native controls satisfies this by default — every custom-drawn surface must be measured.
- **ALWAYS** give every control a stable name. In this codebase the frozen XRC `name` attribute serves
  both the functional harness and the accessibility layer, so naming is not optional.
- **ALWAYS** label every input with a real, persistent label control — never placeholder text alone.
- **ALWAYS** honour the OS text-scaling preference and support the in-app zoom range without clipping or
  overlapping. Sizer-based layout is what makes this work.
- **ALWAYS** respect the OS high-contrast and reduced-motion settings.
- **NEVER** convey meaning by colour alone. A flagged row gets a glyph or text marker as well as weight
  or colour.
- **NEVER** rely on hover to reveal essential information — it is unreachable by keyboard.

## 8 · Feedback and latency

Extends core Rules 3, 4 and 10. Desktop users expect direct manipulation to feel instant.

- Keystroke-driven acknowledgement: **< 100 ms perceived**. Commit to the UI first, persist off the UI
  thread.
- **NEVER** block the UI thread on I/O — no synchronous disk, database or network work in an event
  handler. The primary entry field must never stall.
- Operations over ~1 s show progress; over ~10 s show a determinate progress indicator **and** a way to
  cancel.
- **ALWAYS** confirm completion somewhere persistent — status bar or info bar — not only via a transient
  cue.
- Audio cues are an accompaniment, never the sole channel: every cue has a visible counterpart, and the
  cues are toggleable.

## 9 · State, errors and empty views

- **ALWAYS** render every distinct application state explicitly, including the disabled/pre-start state,
  with a one-line hint explaining what to do next.
- **ALWAYS** show errors inline, next to the control that caused them, and validate a field when it
  loses focus rather than only on submit.
- **ALWAYS** write error text as: what happened · why · the specific next step.
- **NEVER** leave an empty list as a blank panel — give it a one-line hint.
- **NEVER** surface a raw traceback, database error or internal identifier in the UI.

---

## Review checklist (desktop)

- [ ] All layout is sizer-based; no absolute positioning; no restyled standard controls.
- [ ] Every command is keyboard-reachable; accelerators come from the single accelerator table.
- [ ] Menu mnemonics present; stock IDs used so macOS relocation happens automatically.
- [ ] `Esc` cancels · `Enter` activates the default · `Tab` is trapped · focus returns to the opener.
- [ ] Destructive confirmations default to the safe button; irreversible ones require typed confirmation.
- [ ] Minimum window size set and verified to fit 1366×768; geometry and sash positions persist and are
      validated on restore.
- [ ] Contrast ≥ 4.5:1 (≥ 3:1 large); no meaning carried by colour alone.
- [ ] Every control has a stable name serving both tests and assistive technology.
- [ ] OS text scaling and the in-app zoom range work without clipping.
- [ ] No I/O on the UI thread; keystroke feedback under 100 ms.
- [ ] Every state — including empty and pre-start — renders a hint rather than a blank panel.
- [ ] `CODINGSTANDARDS-UX.md` (core) has been read and applied.
