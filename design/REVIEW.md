# Final cross-document audit — RiverCrossing design package

Programmatic cross-checks over all documents, plus a manual read of the menu system and defaults. Every finding below is already fixed in both `docs-html/` and `docs-md/`.

## Checks that passed
| Check | Result |
| --- | --- |
| R-id integrity | 49 requirements defined; **zero** orphan citations across spec, plan, briefs, skeletons, XRC canvas. |
| Window inventory | 23 windows in the XRC canvas; every doc that states a count says 23. |
| Menu system | `spec.md` §15 route map = 38 rows; every row has a target and an "enabled when" state; §15b registry names 40 `mi_*` items covering all of them; canvas menubar shows the same seven menus. |
| XRC naming | 84 distinct snake_case control/window names, all suffix-conventional; no duplicates within a window. |
| Task coverage | Plan and briefs now agree on all 86 numbered tasks (see F-2). |
| Stack baseline | wxPython 4.2.5 / wx 3.2 consistently stated as the floor in all six docs; 4.3 only ever as the upgrade path. |
| Export defaults | `ExportOptions` defaults identical in skeletons, briefs, results-window mockup and both golden pages (times off, laps board on, time board off, full field on, all cards on, lap 8 km). |
| Radio defaults | Every radio group in the canvas has a stated default: solo-only entries, rider-plates-pooled, 2 jokers/deck, tie-break ① laps, appearance System, zoom 100%. |
| Asset naming | `-2x` suffix (not `@2x`) stated in briefs and README, matching the shipped files. |

## Findings and resolutions
| # | Finding | Resolution |
| --- | --- | --- |
| F-1 | `requirements.md` §2 listed R-18 between R-14 and R-16 — ids did not ascend, so a reader scanning for R-16/R-17 could miss them. | Row reordered; R-13 → R-18 now ascend. |
| F-2 | Plan task **E4.2.4** (unknown-plate rejection cue) had no owning brief — its behavior was tested inside brief E4.2.1 but the id appeared nowhere, so an agent working brief-by-brief would have left it unclaimed. | Brief retitled **E4.2.1 + E4.2.4**; the cue is now explicitly owned. |
| F-3 | `spec.md` §15b (XRC appendix) rendered outside the document card, reading as an orphan block. | Spliced into the document after the §15 menu map. |
| F-4 | `spec.md` §7 and §8 were dense paragraphs mixing binding rules with rationale. | Rewritten as bullets (CSV: format, pooled row form, import semantics, teams-from-file, RUNNING lock, export columns; HTML: static markup, embedded record, build-time CSS, no-invented-data, widget verdict, templates, render method, tests). |
| F-5 | The spec's companions footer predated half the doc set. | Now links requirements, XRC windows, skeletons, plan, briefs, both golden samples, and marks the hi-fi doc retired. |
| F-6 | Brief E1.4.1 cited "§15 table (43 rows)"; the table holds 38. | Corrected, with the per-menu breakdown (File 8 · Ride 7 · Riders 4 · Cards 7 · Results 7 · View 1 · Help 4) so the coverage-walk test has an exact expected count. |
| F-7 | Markdown conversion: cross-document links pointed at `.dc.html` files that do not exist beside the markdown. | All links remapped to the sibling `.md` files; template and export links repointed to `../templates/` and `../exports/`. |
| F-8 | Bundled HTML copies referenced assets at their old project-root paths. | Repointed to `../exports/`, `../templates/`, `../assets/sounds/` inside `docs-html/`. |

## Standing caveat (by design, not a defect)
The golden results pages were hand-assembled from the real payloads, so the first render from the shipped Jinja2 templates may differ in loop indentation. Task **E6.2.2** therefore regenerates the goldens once from the real renderer, verifies value-parity against these fixtures, and freezes the bytes from then on.
