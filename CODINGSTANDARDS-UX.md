# UX & Interaction Design Standards

**Design every state and every path — not just the happy one.**

This document is the **platform-neutral core** standard for user experience and interaction
design. It applies to every user-facing surface in the workspace — the `webui` React app, the
`androiduser` and `androidadmin` Android apps, and the future iOS app. Read it before
designing, building, or reviewing any screen, component, page, flow, or mockup, **alongside
the platform supplement** for the target — `CODINGSTANDARDS-UX-WEB.md`,
`CODINGSTANDARDS-UX-ANDROID.md`, or `CODINGSTANDARDS-UX-IOS.md` — and the language standard.

It exists because polished visuals are not the same as a usable product, and because quickly
generated UI — including AI/LLM-generated UI — reliably builds only the **happy path**: what
the screen looks like when everything loads, every field is valid, and nothing fails. Real
users hit the other paths first. They open the app while data is still loading, with no
content yet, on a flaky network, after a server error, fat-fingering a touch target. When
those states are undesigned, the product feels broken, the user blames themselves, and they
leave. Good UX is also how a product earns trust. The rules below are the counter-pressure:
they force the loading, empty, error, and partial states into the design from the start.

## Core Philosophy

> "Good design is actually a lot harder to notice than poor design, in part because good
> designs fit our needs so well that the design is invisible." — Don Norman, *The Design of
> Everyday Things*
>
> "The system should always keep users informed about what is going on, through appropriate
> feedback within a reasonable amount of time." — Jakob Nielsen, Heuristic #1
>
> "Beautiful UI with bad UX will make people leave." — *Build for Good UX*, Part 1

**The contract:**

- **Every screen has four states — loading, success, error, and empty (plus partial).**
  Design all of them, not just success. The state the user sees first is often *not* success.
- **Always tell the user what is happening, why, and what to do next.** Visibility of system
  status and clear recovery are not optional polish; they are the product working.
- **Match the platform and the user's prior experience.** Don't reinvent standard controls
  (back, search, tab bar). Users spend most of their time in *other* apps and bring those
  expectations with them (Jakob's Law).
- **Accessibility (WCAG 2.2 AA) and honesty are requirements, not nice-to-haves.** A design
  that excludes assistive-technology users, or that manipulates users with deceptive patterns,
  is a defect — the same as a bug.

This core covers what is true on **every** platform. Platform-specific numbers and components
— touch-target sizes, navigation patterns, the focus/hover model, motion and accessibility
APIs — live in the matching platform supplement. Where a rule below says *"(see the platform
supplement),"* the concrete figure is there.

---

## The Rules

### 1. UI is not UX — design every path, not just the happy one

UI is what the product looks like; UX is whether a person can actually accomplish their goal —
including when something is slow, empty, or broken. A beautiful screen with unpredictable
behavior (a button that does nothing, an action with no feedback) creates friction, and
friction makes users blame themselves and leave.

- **ALWAYS** design the loading, success, error, empty, and partial states for every screen
  and section before calling it done.
  Why: quickly-built and AI-generated UI defaults to the happy path; the other states are what
  users hit first and what makes a product feel broken when missing. [Pt.1, Pt.2]
- **ALWAYS** make every interactive element's result predictable and discoverable without
  instructions.
  Why: if a user clicks and gets an unpredictable result or nothing at all, they lose trust in
  the whole product. [Pt.1; NN/g #6]
- **NEVER** ship attractive UI with ambiguous or unpredictable behavior.
  Why: friction → self-blame → churn; good UX is what builds trust in the product. [Pt.1]

### 2. Every screen has four states — build all four

Loading, success, error, and empty are not edge cases; they are the screen. A screen that only
renders the success state is unfinished.

- **ALWAYS** explicitly design loading, success, error, and empty for each screen *and* each
  independently-loading section within it.
  Why: each is a real state a real user will see. [Pt.2]
- **NEVER** leave a state undesigned or blank.
  Why: an undesigned state reads as "broken," even when the system is working correctly. [Pt.2, Pt.8]

### 3. Loading indicators — pick the right one for the wait

Each loader triggers a different expectation. Using the wrong one makes a working system feel
stuck. (Render mechanics are platform-specific; the *choice* is universal.)

- **ALWAYS** use a **skeleton screen** when a full page or large section is loading.
  Why: the user's brain processes the layout before the data arrives, so the wait feels
  shorter and the screen feels alive. [Pt.2, Pt.3]
- **ALWAYS** use a **progress bar** when the duration is knowable — uploads, downloads,
  installs, multi-step jobs.
  Why: people need a sense of how far along they are and how much longer to wait. [Pt.3]
- **ALWAYS** use an **inline spinner** for a small, contained action (a button just pressed,
  one region refreshing).
  Why: a localized "we're working on it" without taking over the screen. [Pt.3]
- **ALWAYS** use **optimistic UI** for actions that should feel instant (e.g., toggling a
  like), and roll back visibly if the server later rejects it.
  Why: the action feels immediate; the rare failure is corrected without making every success
  wait. [Pt.3]
- **NEVER** show a bare spinner for a long or known-duration operation (like a file upload).
  Why: a spinner with no progress reads as "stuck," so users assume it failed. [Pt.3]

### 4. Perceived performance & response time — feedback before the wait gets noticed

Three response-time limits govern how an interaction feels. Respect them and a slow system
still feels responsive; ignore them and a fast one feels broken.

- **ALWAYS** keep direct manipulations under ~0.1s where possible; no loader is needed for
  responses under ~1s.
  Why: 0.1s feels like direct cause-and-effect; up to 1s keeps the user's flow of thought
  uninterrupted. [NN/g Response Times]
- **ALWAYS** show feedback for any wait over ~1s, and for waits over ~10s show a percent-done
  indicator **and** a way to cancel.
  Why: 10s is the limit of sustained attention; beyond it users need progress and an exit.
  [NN/g Response Times; NN/g #1]
- **ALWAYS** treat ~400ms as the target ceiling for an interaction to feel instant.
  Why: the Doherty threshold — responses under ~400ms keep users engaged and productive.
  [Laws of UX]
- **NEVER** leave an action that takes longer than ~1s with no visible feedback.
  Why: with no signal, users assume the action failed and retry or abandon. [Pt.1; NN/g #1]

### 5. Error messages — what happened, why, and what to do next

A good error message does three things: it says **what** happened, **why**, and gives a
**clear next action** — in plain, human, blame-free language.

- **ALWAYS** state what happened, why it happened, and the specific next step.
  Why: a vague failure leaves the user unsure of reality — e.g., after paying, "did my payment
  go through or not?" [Pt.5; NN/g Error-Message Guidelines]
- **ALWAYS** write in plain, courteous, jargon-free language that never blames the user.
  Why: microcopy connects with people; technical noise and blame increase frustration and
  abandonment. [NN/g #9; Wix UX]
- **NEVER** dump raw backend, database, or stack-trace errors onto the screen.
  Why: a normal person can't parse them, and they leak internals — a security risk. [Pt.5]
- **NEVER** ship "Something went wrong" as the entire message.
  Why: it carries no information about what failed or what to do, which is its own bad
  experience. [Pt.5]
- **NEVER** fail silently — a tap that produces no message and no change.
  Why: the user can't tell whether it worked or broke; silent failure is the worst error
  state. [Pt.5]

### 6. Message & error placement — as close to the cause as possible

The closer a message sits to the thing it refers to, the better. Match the channel to the
stakes. (Platform components — toast vs. snackbar vs. sheet — are mapped in the supplements.)

- **ALWAYS** place an error inline, next to the field or control that caused it.
  Why: the user's eyes are already there; correction is immediate. [Pt.7]
- **ALWAYS** reserve transient, auto-dismissing messages (toasts/snackbars) for low-stakes,
  recoverable information.
  Why: anything that auto-dismisses can be missed if the user looks away. [Pt.7]
- **ALWAYS** reserve a blocking modal for cases where the user genuinely cannot continue — and
  always give a clear way forward.
  Why: blocking must be earned, and a block with no resolution path is a trap. [Pt.7; NN/g #3]
- **NEVER** use a transient toast for a critical error.
  Why: if the user misses it, they're left in a bad state. [Pt.7]
- **NEVER** block the user with a modal that offers no action to resolve or dismiss it.
  Why: it strands the user with no exit (violates user control & freedom). [Pt.7; NN/g #3]

### 7. Forms — reduce effort, validate early, never lose context

Nobody enjoys filling out forms. Every rule here removes friction or prevents a wasted submit.
(Platform keyboard/label mechanics are in the supplements.)

- **ALWAYS** give every field a persistent, visible label and clearly mark which fields are
  required.
  Why: a label that vanishes once the user types makes both filling and error-correction
  harder. [Pt.6; Baymard]
- **ALWAYS** validate inline, the moment a field loses focus — not only on submit.
  Why: submitting, waiting, then scrolling back up to fix one field is maximally frustrating.
  [Pt.6; Baymard]
- **ALWAYS** show a live character count when a field has a limit, and show password
  requirements with each one checked off as it's met.
  Why: it prevents users writing a paragraph only to delete half, or submitting a password
  that's then rejected. [Pt.6]
- **ALWAYS** prefill and autocomplete what you already know, and accept forgiving input
  formats (phone numbers with or without dashes/spaces), normalizing server-side.
  Why: less typing and less rejection for the user; formatting is the system's job, not
  theirs. [Pt.6; Baymard]
- **ALWAYS**, if you disable submit until the form is valid, make what's still missing
  obvious.
  Why: a greyed-out button with no explanation is *more* frustrating than no gating. [Pt.6]
- **NEVER** use placeholder text as a field's only label.
  Why: it disappears on input, breaks error-correction, and fails assistive technology.
  [Baymard]

### 8. Empty states — give the user a purpose and a next action

An empty state is often the first thing a new user sees. It should explain what the section is
for and offer a way to fill it — never a blank void.

- **ALWAYS** give every empty state a purpose: explain what it's for and provide a primary
  call-to-action.
  Why: a blank screen with no action leaves the user not knowing what to do. [Pt.8]
- **ALWAYS** guide first-time users toward their first unit of value (first project, file, or
  task), with light step-by-step encouragement.
  Why: reaching first value is what activates and retains new users. [Pt.8; Smashing; UserOnboard]
- **ALWAYS** make empty search results useful — acknowledge the missing term and offer a
  related query or next step.
  Why: "no results" is a dead end; a suggestion keeps the user moving. [Pt.8]
- **ALWAYS** treat a "goal" empty state (e.g., a cleared inbox) as an achievement to
  celebrate.
  Why: turning emptiness into a reward makes it something users look forward to. [Pt.8]
- **NEVER** present a blank screen with no explanation and no action.
  Why: it feels broken or abandoned and gives the user nowhere to go. [Pt.8]

### 9. Graceful degradation — each section owns its own data, loading, and failure

A page is assembled from many independent sources that load at different speeds. One slow or
failed source must not take down the whole page.

- **ALWAYS** make each section responsible for its own data, loading state, error state, and
  retry.
  Why: isolation means one failure degrades one section, not the entire page. [Pt.10]
- **ALWAYS** render what's available while the rest loads — show cached content immediately and
  swap in fresh data when it arrives.
  Why: the user can read and act right away instead of staring at a global spinner. [Pt.9, Pt.10]
- **ALWAYS** give a failed section its own error message and its own retry control.
  Why: localized recovery keeps the rest of the page fully usable. [Pt.10]
- **NEVER** throw a full-page error or block the entire screen because one section failed or is
  still loading.
  Why: it discards content the user could otherwise be using. [Pt.9, Pt.10]

### 10. Success states & system status — close the loop, keep status visible

The counterpart to good error handling is confirming success and keeping the user informed of
what the system is doing.

- **ALWAYS** confirm a completed action with clear, immediate feedback.
  Why: it closes the interaction loop so the user knows it worked and doesn't repeat it.
  [Pt.1; Apple HIG]
- **ALWAYS** keep the system's status visible while work is in progress or state has changed.
  Why: visibility of system status is the first usability heuristic for a reason — it's how the
  product communicates. [NN/g #1]
- **NEVER** leave a completed action ambiguous.
  Why: uncertainty makes users re-submit (e.g., double-charging) or distrust the product. [Pt.1]

### 11. Navigation & information architecture — conventions, consistency, exits

Users carry expectations from every other product they use. Honor those conventions, keep
navigation consistent, and always provide a way back or out. (Concrete patterns — tab bars,
bottom nav, browser back — are in the supplements.)

- **ALWAYS** follow established platform and industry conventions for standard controls (back,
  search, navigation).
  Why: Jakob's Law — users spend most of their time on *other* products and expect yours to
  work the same way. [NN/g #4; Laws of UX; HIG]
- **ALWAYS** provide a clearly marked exit — back, cancel, close, or undo — from any state.
  Why: users choose functions by mistake and need an obvious escape without a struggle (user
  control & freedom). [NN/g #3]
- **ALWAYS** prefer recognition over recall: keep options, actions, and information visible
  rather than forcing users to remember them.
  Why: minimizing memory load is a core heuristic of usable navigation. [NN/g #6]
- **NEVER** trap the user in a flow with no obvious way back or out.
  Why: a dead end with no exit is one of the fastest ways to lose a user. [NN/g #3]

### 12. Universal accessibility — WCAG 2.2 AA is the floor

Accessibility is a requirement on every platform. The principles below are universal; the
concrete numbers and platform APIs (focus indicators, screen-reader labels, text scaling) live
in the supplements.

- **ALWAYS** meet WCAG 2.2 Level AA: text contrast at least 4.5:1 (3:1 for large text ≥18pt or
  ≥14pt bold).
  Why: insufficient contrast excludes low-vision users and is the most common, most easily
  avoided accessibility failure. [WCAG 1.4.3]
- **ALWAYS** make every function operable by assistive technology and by keyboard/switch input,
  with meaningful labels and roles.
  Why: anything reachable only by precise pointer or sighted interaction locks out a real
  population of users. [WCAG 2.2; WCAG2Mobile]
- **ALWAYS** support the OS text-scaling / larger-text preference without breaking layout.
  Why: low-vision users rely on it; fixed-size text that clips or overlaps when scaled is
  unusable. [WCAG2Mobile]
- **NEVER** convey meaning by color alone.
  Why: color-blind users and many low-vision users won't perceive the distinction; pair color
  with text, icon, or shape. [WCAG 1.4.1]
- **NEVER** rely on motion or animation that the user can't reduce or that triggers on scroll.
  Why: large motion can cause nausea and vertigo for people with vestibular disorders; honor
  the reduced-motion preference. [WCAG 2.3.3]

### 13. Trust, microcopy & consent — be honest, be human, allow recovery

Respect the user's autonomy and attention. Transparent, human design builds the trust that
keeps users; deceptive design wins a click and loses a customer (and increasingly breaks the
law).

- **ALWAYS** make the user's intended action at least as easy as the business-preferred one.
  Why: asymmetry — making the desired-by-you path far easier than the desired-by-them path — is
  the defining trait of a deceptive pattern. [NN/g Deceptive Patterns]
- **ALWAYS** make pricing, subscriptions, and consequences transparent *before* the user
  commits, and make cancel/unsubscribe/opt-out as easy as opt-in.
  Why: hidden costs and roach-motel cancellation cause financial and privacy harm, erode trust,
  and carry legal exposure (the EU Digital Services Act bans many such patterns, fines up to 6%
  of global turnover). [NN/g Deceptive Patterns; Finance Watch]
- **ALWAYS** request a permission in context, right before the feature needs it, and explain
  why.
  Why: a cold permission prompt at launch with no context gets denied and burns trust; an
  in-context, justified ask is granted far more often. [Apple HIG; Material]
- **ALWAYS** confirm a destructive action, or (better) perform it immediately with a clear,
  time-boxed **undo**.
  Why: undo supports error recovery without nagging the user on every safe action. [NN/g #3; NN/g #5]
- **NEVER** use confirmshaming, bait-and-switch, forced continuity, repeated nagging, hidden
  costs, or pre-checked consent.
  Why: these manipulate rather than serve; they damage trust and expose the product legally.
  [NN/g Deceptive Patterns; CareerFoundry]
- **NEVER** put a destructive, irreversible action one tap away with no confirmation or undo.
  Why: a single mistaken tap should never cause unrecoverable loss (error prevention). [NN/g #5]

---

## Quick decision heuristics

Before you call a screen done, ask:

- **"Have I designed the loading, empty, error, and partial states — not just success?"** No →
  it isn't done (Rules 1, 2, 9).
- **"For every wait over ~1s, is there feedback? Over ~10s, progress + a cancel?"** No → add it
  (Rule 4).
- **"Does each error say what happened, why, and what to do next — in plain language?"** No →
  rewrite it (Rule 5).
- **"Is each message as close as possible to the thing it's about, at the right stakes?"** No →
  move it (Rule 6).
- **"Can the user always get back or out, using familiar controls?"** No → add the exit (Rule 11).
- **"Does this meet WCAG 2.2 AA, and is anything here manipulating the user?"** Fix contrast,
  labels, and any deceptive asymmetry first (Rules 12, 13).

---

## Review checklist

- [ ] Loading, success, error, empty, and partial states designed for every screen and section.
- [ ] The right loader for each wait (skeleton / progress bar / inline spinner / optimistic).
- [ ] Feedback for every wait > 1s; percent-done + cancel for > 10s; interactions target < 400ms.
- [ ] Every error states what happened, why, and the next action — plain, blame-free language.
- [ ] No raw backend/DB/stack errors, no bare "Something went wrong," no silent failures.
- [ ] Messages placed at the right stakes and as close to the cause as possible (inline first).
- [ ] Forms: visible labels, required marked, inline validation, char counts, prefill, forgiving formats.
- [ ] No placeholder-only labels.
- [ ] Every empty state has a purpose and a primary action; empty search results suggest a next step.
- [ ] Each section owns its data/loading/error/retry; cached-then-fresh; no full-page error on one failure.
- [ ] Completed actions are confirmed; system status stays visible.
- [ ] Standard controls follow platform/industry convention; a clear exit exists from every state.
- [ ] WCAG 2.2 AA met: contrast ≥ 4.5:1 (3:1 large), no color-only meaning, AT + keyboard operable, text scales.
- [ ] No deceptive patterns; permissions requested in context; destructive actions confirmable or undoable.
- [ ] The matching platform supplement (WEB / ANDROID / IOS) has been read and applied.

---

## When NOT to apply these rules

These rules serve the user; don't apply them mechanically against that goal. Do **not**:

- Add a loader, animation, or confirmation step where the action is genuinely instant and safe
  — unnecessary friction is its own bad UX (Rules 3, 4, 13).
- Block or interrupt for low-stakes information that an inline or transient message handles
  better (Rule 6).
- Sacrifice accessibility or honesty for minimalism or aesthetics — a cleaner-looking screen
  that excludes users or hides costs is a worse screen (Rules 12, 13).
- Override a platform's own established convention to satisfy a generic rule here — the platform
  supplement's specific guidance wins for that platform (Rule 11).

The test for every decision is the same: **does it help a real person — including on the slow,
empty, and broken paths — accomplish their goal honestly?** Keep what does; cut what doesn't.

---

## Sources

This standard is grounded in the following references (≥ 30 sources reviewed), plus the
*Build for Good UX* series (parts 1–10) that seeds it.

### Usability heuristics & laws of UX

- [10 Usability Heuristics for User Interface Design — Nielsen Norman Group](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [Visibility of System Status (Heuristic #1) — NN/g](https://www.nngroup.com/articles/visibility-system-status/)
- [Hick's Law — Laws of UX](https://lawsofux.com/hicks-law/)
- [The Laws of UX explained — UX Design Institute](https://www.uxdesigninstitute.com/blog/laws-of-ux/)
- [The 21 Main UX Laws Every Designer Must Follow — Maze](https://maze.co/collections/ux-ui-design/ux-laws/)

### Response time & perceived performance

- [Response Time Limits: 3 Important Limits (0.1s / 1s / 10s) — NN/g](https://www.nngroup.com/articles/response-times-3-important-limits/)
- [Response Time Limits (0.1s, 1s, 10s) — UX/UI Principles](https://uxuiprinciples.com/en/principles/response-time-limits)
- [Button States: Communicate Interaction — NN/g](https://www.nngroup.com/articles/button-states-communicate-interaction/)

### Error messages & microcopy

- [Error-Message Guidelines — NN/g](https://www.nngroup.com/articles/error-message-guidelines/)
- [When life gives you lemons, write better error messages — Wix UX](https://wix-ux.com/when-life-gives-you-lemons-write-better-error-messages-46c5223e1a2f)
- [Error Messages: Examples, Best Practices & Common Mistakes — CXL](https://cxl.com/blog/error-messages/)
- [Error Message UX, Handling & Feedback — Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-error-feedback)

### Forms

- [Form Design: Best Practices — Baymard Institute](https://baymard.com/learn/form-design)
- [Mobile Form Usability: Never Use Inline Labels — Baymard](https://baymard.com/blog/mobile-forms-avoid-inline-labels)
- [Mobile Checkout Usability — Baymard](https://baymard.com/blog/mobile-checkout)

### Empty states & onboarding

- [The Role of Empty States in User Onboarding — Smashing Magazine](https://www.smashingmagazine.com/2017/02/user-onboarding-empty-states-mobile-apps/)
- [Onboarding UX Patterns: Empty States — UserOnboard](https://www.useronboard.com/onboarding-ux-patterns/empty-states/)
- [Empty States: The Most Overlooked Aspect of UX — Toptal](https://www.toptal.com/designers/ux/empty-state-ux-design)

### Accessibility (universal)

- [Web Content Accessibility Guidelines (WCAG) 2.2 — W3C](https://www.w3.org/TR/WCAG22/)
- [How to Meet WCAG 2.2 (Quick Reference) — W3C WAI](https://www.w3.org/WAI/WCAG22/quickref/)
- [Understanding SC 1.4.3 Contrast (Minimum) — W3C WAI](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
- [Understanding SC 1.4.1 Use of Color — W3C WAI](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)
- [Understanding SC 2.3.3 Animation from Interactions — W3C WAI](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html)
- [Guidance on Applying WCAG 2.2 to Mobile Applications (WCAG2Mobile) — W3C](https://www.w3.org/TR/wcag2mobile-22/)

### Trust & deceptive patterns

- [Deceptive Patterns in UX: How to Recognize and Avoid Them — NN/g](https://www.nngroup.com/articles/deceptive-patterns/)
- [12 Dark Patterns in UX Design (And How To Avoid Them) — CareerFoundry](https://careerfoundry.com/en/blog/ux-design/dark-patterns-ux/)
- [Dark patterns explained: how to spot and avoid deceptive UX (DSA) — Finance Watch](https://www.finance-watch.org/blog/dark-patterns-explained-how-to-spot-and-avoid-deceptive-ux/)

### Platform conventions & foundations (cross-referenced; see supplements for specifics)

- [Human Interface Guidelines — Apple](https://developer.apple.com/design/human-interface-guidelines/)
- [Material Design 3 — Google](https://m3.material.io/)
- [How to Use "Tappability" Affordances — Interaction Design Foundation](https://www.interaction-design.org/literature/article/how-to-use-tappability-affordances)
- Don Norman, *The Design of Everyday Things*, Revised & Expanded Edition (Basic Books, 2013)

---

**Document Status:** Active
**Last Updated:** June 7, 2026
**Applies To:** All user-facing UI across webui, androiduser, androidadmin, and the future iOS app (read alongside the matching CODINGSTANDARDS-UX-WEB / -ANDROID / -IOS supplement and the language standard)
**Authority:** Synthesis of Nielsen's 10 Usability Heuristics, the Laws of UX, NN/g response-time and error-message research, Baymard form research, WCAG 2.2 AA, Norman's *The Design of Everyday Things*, and the *Build for Good UX* series (≥ 30 sources above)
