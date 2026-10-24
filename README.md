# MemAct

Record a browser task. Save it as an editable, searchable procedure with instructions and browser replay.

MemAct runs locally on Linux. Its timeline is plain HTML, CSS and JavaScript. Python controls a separate Chromium window through Playwright. Each recording is one JSON file, including screenshots. No account, database, LLM, Node runtime or frontend build is required.

## Run on Linux

Requires Python 3.10 or newer, Python's venv/pip support, and a graphical Linux session for the recording browser.

```bash
git clone https://github.com/MemAct/MemAct.git
cd MemAct
./setup.sh
./run.sh
```

Open [127.0.0.1:8765](http://127.0.0.1:8765/). Stop the server with Ctrl+C.

The setup script installs Python packages into `.venv/` and downloads Playwright's Chromium. It does not install system packages or run sudo. On Fedora, a minimal Python installation may need `python3-pip`; Debian/Ubuntu may need `python3-venv` and `python3-pip`. To select a different interpreter, use `MEMACT_PYTHON=python3.12 ./setup.sh`.

Playwright officially supports selected Debian/Ubuntu versions. This project is also tested on this Fedora workstation. Other Linux distributions can require additional browser shared libraries; a successful Python install alone does not prove Chromium can launch. Keep browser sandboxing enabled. Do not run MemAct as root.

Optional arguments:

```bash
./run.sh --port 8766
./run.sh --data-dir /path/to/private/recordings
```

`--headless` is available for local automated tests. Normal use opens a visible browser.

## Use

1. Choose **New recording** below the sidebar search, or **Record** in the header, and enter a page URL and an optional name. Bare domains such as `google.com` use HTTPS automatically; localhost addresses use HTTP. **Use local test page** provides a safe mock workspace.
2. Work in the Chromium window, then choose **Stop recording** in MemAct. Pending edits are flushed before saving. The browser stays open so you can prepare a subset or a replacement.
3. Edit a step's label directly. Search matches labels, element names, page title, URL and nearby text. The sidebar search finds recording names and starting URLs.
4. Select steps and choose **Export**, **Replay**, **Delete**, or **Re-record**. Export provides plain English or portable JSON, preserving recorded order. **Import JSON** restores an exported recording. `examples/report.json` contains a real recording of the local mock page that you can import to try the timeline.
5. For **Re-record**, manually restore the browser to the state immediately before that step. Choose **Record next action**, then perform the replacement. Setup actions are excluded. Its ID and custom label are preserved; subsequent steps are unchanged and may need review.

Screenshots are best-effort views captured **after** each action. Their capture URL and timestamp are stored independently. A navigation can complete before its screenshot is taken. Missing screenshots are marked unavailable.

Right-click a recording or use its three-dot menu to rename or delete it. The same menu appears beside the open recording's name. Deleting a recording requires confirmation and removes its JSON file. The bottom sidebar arrow folds history into a narrow rail with Search, New recording and History controls, and remembers your preference locally.

## What is recorded

- Clicks, committed text edits (including empty fields), page-delivered special keys and modifier shortcuts.
- Checkbox/radio state and native dropdown selections.
- Full-page and same-document navigation, including observed navigation caused by an action.
- Iframe interactions, with a selector path through parent frames; cross-origin frames are handled by Playwright.
- Tabs and popups, with recorded page IDs and opener relationships.
- File-input selections and external file drops, as **names, sizes and MIME types only**.
- HTML element drag-and-drop, plus pointer drags such as sliders.

## Replay and confirmation

Replay resolves an exact role and accessible name first, then a unique CSS fallback. Ambiguous semantic matches may be narrowed by their recorded CSS selector. A changed element/input type or changed known accessible name is rejected. It does not click the first match, fall back to coordinates for missing click targets, or retry an action after it may have executed.

Submit/delete/pay/send controls are flagged as risky. Other interactions have unknown effects because page JavaScript can attach arbitrary behavior. Both risky and unknown actions require confirmation in the timeline, enforced by Python. Passive navigation/tab observations need no confirmation. Changing a label never changes safety classification.

Confirmation does not guarantee that a website will behave as expected. A successful click is not a business assertion. Cancellation stops future actions; it cannot undo ones already performed. Replay stops at the first failure.

Full sequences begin with their recorded navigation. Selected subsets use the prepared browser and do not secretly run omitted steps. You are responsible for prerequisites such as login and earlier edits. Missing tabs and mismatched URLs stop replay.

For file steps, select the original files in the replay confirmation. Names and sizes must match; this is not a content identity guarantee. File bytes exist in memory for the replay action and are not added to the recording. Up to 25 MB can be reselected per step. Clear-file-input actions do not need a file selection.

## Privacy

Password field values and password-target actions are excluded before data leaves the page. Fields marked with password autocomplete or `data-memact-private`/`data-memact-ignore` are excluded. A field that was a password remains private after a reveal toggle. Sensitive fields are hidden in screenshots, including frames.

Ordinary text fields, page titles, URLs, visible page text, filenames and screenshots can still contain private data. A site that copies a secret into an unrelated visible element cannot be reliably detected. Avoid recording secrets in page text, query strings, or ordinary inputs. Screenshots are not a guarantee of automatic personal-data redaction.

Recordings are private JSON files under `data/` by default. They are **not encrypted**. Exporting JSON or text can disclose their contents; inspect them before sharing. Browser login state is not persisted in a normal profile. Cookies, localStorage, network bodies, console logs, clipboard contents and uploaded file bytes are not collected.

The server binds only to loopback and uses a per-process API token, Host/Origin checks and no CORS. Website text is rendered as text, not HTML. Imported JSON is validated and cannot contain executable replay code or local screenshot paths.

## Practical boundaries

- This records web content, not the Linux desktop. Browser-chrome and OS-reserved shortcuts, native file-dialog navigation, password-manager UI, closed shadow roots and browser internal pages are outside page event capture.
- Standard HTML drag/drop, file-drop targets and a pointer slider are covered by fixtures. Canvas-specific gestures, cross-tab drags, and sites that reject synthetic drop events are not universally replayable. MemAct does not implement an operating-system drag recorder.
- Text edits replay their final value with `fill`, not their exact keystroke timings. Sites with per-key behavior, rich-text formatting, autocomplete composition, or drawing gestures may need a more specific recorder.
- Arbitrarily delayed scripts make navigation causality imperfect. The recorder associates nearby navigation with recent actions; unrelated or very delayed redirects may need manual review.
- CSS paths, frame embedding, accessible names and page URLs can change. Session-specific URLs or authentication can prevent a saved procedure from replaying later.
- Native JavaScript dialogs are dismissed and reported. Browser file downloads are reported but not stored. These are not full system/browser snapshots.
- Limits: 2,000 steps and 100 MB per recording, 30 files per selection, bounded event queue. Recording stops with a visible message if it cannot keep up.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests use temporary recordings and a local mock site. They cover event ordering, input/Enter submission, privacy, selectors, frames, tabs, file selection, external file drops, both drag strategies, confirmations, re-recording, persistence, API boundaries, search/edit/export, XSS handling and responsive visual constraints.

## Files

```text
server.py                  localhost API and static serving
browser.py                 recording, target resolution and guarded replay
capture.js                 injected web event listener
model.py                   JSON contract, validation and label templates
storage.py                 atomic JSON writes
web/                       plain timeline, supplied logos and local fonts
vendor/accessibility.js    prebuilt accessible-name/role library
tests/                     local mock/browser and model/server tests
RESEARCH.md                technical review and upstream reuse audit
THIRD_PARTY_NOTICES.md      upstream attribution and licenses
```

See [RESEARCH.md](RESEARCH.md) for design weaknesses and source references, and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the small adapted helper and vendored dependency.
