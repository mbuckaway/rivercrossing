# Windows Debugging Pass — Session Summary and Hand-off

**Written:** 2026-08-30 · **Branch:** `topic/windows-debugging` · **PR:** #14
**Purpose:** Root-cause and fix the Windows CI reds and the "crashes when it should not"
reporting, and verify the whole suite + build on a real Windows 11 desktop.

---

## Status at a glance

| Item | State |
|---|---|
| Static gates (ruff, mypy, import-linter, ids_drift, css_drift) | **GREEN** locally and on CI (both OSes) |
| Unit + property + simulations | **GREEN** — 2150 passed / 0 failed, 98.3 % coverage locally; CI windows unit green |
| Functional suite | **GREEN ×3 locally** (runs 2, 3, 5, each converged via the fresh-process rerun wrapper); **GREEN on windows-latest CI** (first documented Windows functional green since the EPIC-3 deprioritization) |
| Bundle + smoke | **GREEN locally and on CI** — PyInstaller onedir builds, bundle smoke 55 passed (incl. the exe-launch test), exe launches clean |
| winsetup / winsetup_smoke | **GREEN locally and on windows-latest CI** — installer compiles (34 MB), all 7 smoke tests pass: silent install → Start-menu shortcut → HKCU uninstall entry → installed-app launch → silent uninstall removes all traces (E9.1.2) |

## 1 · Root causes fixed (all test-first pairs on this branch)

1. **Static gate, both OSes — ruff format drift.** EPIC 6 left `tests/unit/test_functional_rerun.py`
   unformatted (`1 file would be reformatted` in every recent master run).
2. **Windows static + unit — `tools/gen_css.py` WinError 193.** npm's extensionless
   `tailwindcss` shim is not executable on Windows; the generator resolves the
   `tailwindcss.cmd` sibling, and the real-CLI test skips when node is not on PATH.
3. **Windows unit — CRLF corruption of byte-frozen artifacts.** git `autocrlf=true` rewrote the
   vendored Tailwind templates/artifacts, the frozen HTML/JSON export goldens, the
   `design/exports` samples and the CSV fixtures to CRLF on checkout, breaking the byte-compare
   honesty tests. `.gitattributes` pins LF for every byte-frozen artifact (the `vectors/*.csv`
   rule extended), and the golden generators/tests write bytes rather than text.
4. **Windows unit — PDF goldens not cross-OS byte-deterministic (R-62/D14).** python.org
   Windows builds link zlib-ng; macOS uses the platform zlib; their deflate bytes differ for
   identical input. The report/poster now store all streams uncompressed (fpdf2 hardcodes
   compression for font data; the buffer-derived `/ID` is suppressed; the classic xref table is
   rebuilt). Goldens regenerated; files ~10× larger — determinism over size (spec §8b
   write-back). A new "Windows CLI resolution" test block pins the `.cmd` seam.
5. **Crash-recovery — `Store.open` transient WAL I/O.** Reopening a DB whose writer was
   hard-killed (`TerminateProcess`) can hit a one-shot `disk I/O error` on the first PRAGMA
   while the -wal lock settles; the app's relaunch (R-52) failed. `open()` retries the
   connect/pragma/migrate/session sequence up to 5 times (~0.75 s worst case) for transient
   SQLite conditions and surfaces persistent errors on the first attempt. This also tamed a
   ~1-in-3 flake in `tests/simulations/test_store_crash_consistency.py` on Windows (15/15 and
   10/10 loops clean after the fix).

## 2 · Verification numbers

- Local (Windows 11 desktop, branch head): unit 2150 passed / 5 skipped / 0 failed (98.3 %);
  functional green ×3 — each pass converged via `tools/functional_rerun.py` after the documented
  upstream wx/SIP churn stall (fresh-process reruns absorb it by design, Addendum 2).
- CI (PR #14, run 33324682158): static + unit green on both OSes; **functional windows
  green** (pass 1 stall on `test_mini_acceptance.py` → rerun 1 clean, exit 0);
  **stage 5 windows green** — PyInstaller bundle, exe-launch smoke, NSIS compile, and the
  silent install → launch → uninstall suite all passed on windows-latest.

## 3 · Environment notes / open items

- **Local AV (resolved):** Trend Micro Titanium quarantined the unsigned PyInstaller exe on
  every build (evidence in `C:\ProgramData\Trend Micro\AMSP\quarantine`, `TSC_GENCLEAN`
  records matching build times/sizes). The user disabled/configured Trend Micro (2026-08-30),
  after which the full local stage-5 chain — bundle, exe-launch smoke, NSIS compile, and the
  E9.1.2 silent install → launch → uninstall suite — passed end to end. CI was never affected
  (no Trend Micro on hosted runners).
- **Windows Update rebooted the machine mid-pass (TrustedInstaller, planned upgrade)** and the
  forced restart left a corrupt `dist` exe once; a clean rebuild after the reboot was fine.
- **macOS functional on hosted runners remains red** from the documented upstream wx/SIP
  wrapper-cache corruption (Phoenix #2931 / sip#113) — pre-existing on master, out of scope
  for this Windows pass. The macOS functional job was red on master's latest run too.
- **Residual upstream SIP churn** on Windows remains (pass-1 stalls on `test_rider_editor.py`,
  `test_csv_preview.py`, `test_mini_acceptance.py`, `test_view_support.py`); the fresh-process
  rerun wrapper (budget 4) converges each time. No in-process repair exists (Addendum 2).
