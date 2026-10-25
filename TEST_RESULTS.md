# Local verification

Verified on 25 October 2026 on Fedora Linux 44, with Python 3.12.14, Playwright 1.64.1 and its bundled Chromium.

## Automated checks

```text
.venv/bin/python -m pytest -q
67 passed in 58.42s
```

After the final typography change, the desktop/mobile visual contract test was rerun:

```text
1 passed in 1.89s
```

After the sidebar button and scrolling changes, targeted UI checks passed:

```text
3 passed in 5.60s
```

These checks verify independent scrolling for 41 recordings and a 35-step timeline, consistent scrollbar styling, button placement and modal opening, desktop/mobile layout, and safe search/edit persistence.

The suite covers:

- Final-value text capture, clearing fields, flushing on stop and Enter submission exactly once.
- Native checkbox, dropdown, file-input and page shortcut capture/replay.
- Password exclusion before and after a reveal toggle, with private screenshot markers.
- Exact role/name targeting after an ID changes, duplicate-target scoping, CSS fallback, missing/ambiguous targets and changed accessible names.
- Click-driven and same-document navigation, cross-origin iframe interactions, popup actions and return to the opener.
- Re-selecting upload files without storing their bytes, external file drop, HTML5 drag/drop and pointer slider replay.
- Backend confirmation, stale confirmation rejection, cancellation before a destructive action, and stopping on unsupported native dialogs.
- Single-step replacement after manual page restoration, stable IDs and preserved custom labels.
- Atomic private JSON files, corrupt-file handling, selection order, export/import roundtrip and schema validation.
- Foreign-origin/Host/token rejection, safe rendering of HTML-like labels, search/edit persistence and desktop/mobile layout.

Python compilation, JavaScript syntax checks, shell syntax checks and `pip check` passed. `./run.sh --help` passed. A normal graphical Chromium recording was also completed on Fedora and saved as `examples/report.json`, containing six real steps and six screenshots of the local mock page.

## Manual browser checks

Recording actions and sidebar: all 8 server/UI tests passed. Coverage includes history right-click, both three-dot entry points, persisted rename, canceling deletion, deleting only the chosen recording, blocking deletion while recording, menu Escape handling, hover visibility, desktop/mobile icon-rail collapse, changing arrow direction, search focus and preference persistence. Matching card/control widths and heights also pass in both short and overflowing histories. Borders were softened and both sidebar states inspected in the browser.

URL-entry regression: 37 model and focused form tests passed. Bare domains normalize to HTTPS, loopback addresses to HTTP, unsafe schemes and malformed addresses are rejected, and errors appear inline without clearing the field. The form test stubs browser startup so it makes no request to Google.

Inspected both the empty state and populated timeline, including step selection, screenshot details, the supplied transparent wordmark, its alignment with the recording search field, the continuous #00011B background, DM Sans body typography, plain step numbering, revised empty-state copy the absence of a default footer message, the red Delete accent and icons in both search fields.

The local test page is the acceptance target. These checks do not establish universal compatibility with every website, browser-chrome/OS shortcut, arbitrary canvas gesture, or changing third-party UI. See README.md for the explicit boundaries.

Step deletion confirmation: the focused browser regression passed, covering singular/plural counts, Cancel, Escape, initial Cancel focus, deletion of only selected steps and absence of browser-native dialogs.

Export dialog: the focused browser regression passed, verifying Cancel/Escape, both downloaded file formats, filename extensions and export of only selected steps. Downloads were accepted in a separate test-only browser context.

Browser-close, title and replay updates: 28 browser/UI tests passed in 66.56 seconds. Checks cover saving queued steps when the last tab/browser closes, continuing with another tab open, starting a new recording afterward, menu-only title renaming, and confirming the opening navigation before creating a replay window. Live local replay also completed after Continue.

Step title changes: verified read-only labels, the toolbar Rename button enabled for exactly one selected step, dialog Cancel/Escape, saved rename persistence and safe label rendering. The focused regression passed; the other 9 server/UI checks passed in the accompanying run.

25 October 2026: reproduced stale selection highlighting in the live timeline, then verified that check/uncheck clears both checkbox state and row highlight. All 12 server/UI and focused rollback checks passed in 16.72s, including whole-recording Re-record confirmation/cancel, replacing steps under the same recording ID, new action capture and preserving prior steps on startup failure.

Popup close controls: the focused browser test passed in 2.54s. Every dialog has one accessible cross control, including screenshot preview. Closing export, rename, deletion, new recording and whole-recording re-record dialogs leaves data unchanged; closing replay confirmation cancels replay.
