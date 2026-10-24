# MemAct: implementation decisions

Researched on 24 October 2026, before application implementation. This document separates verified API behavior from the proposed first-release scope.

## Architecture

The user approved a Python localhost server controlling a separate Playwright-launched browser, with a plain HTML/JavaScript timeline communicating over HTTP. JSON is the source of truth. No framework, Node runtime, build step, database, hosted service, or LLM is needed to run MemAct. A small vendored browser library calculates accessible names. Orbitron is served locally.

Use Python's asynchronous Playwright API and aiohttp in one event loop. This avoids blocking the timeline while a browser is recording or waiting for replay confirmation. Poll a small state endpoint rather than adding WebSockets. Only bind to 127.0.0.1. Record and replay are mutually exclusive operations.

## What the original design needs to make explicit

1. **An action is not an event.** Typing emits many input and keyboard events. Coalesce typing into one `fill` per edit, flushing before a subsequent click, Enter, navigation, or stop. Preserve an empty string when a field is cleared. IME composition must finish before an edit is committed. Final-value filling does not reproduce per-key JavaScript behavior; these sites need future key-by-key support.
2. **Names are not just textContent.** Accessible names can come from aria-labelledby, aria-label, associated labels, alt text, and other semantics. Store visible text separately. Use dom-accessibility-api for event-time names, then Playwright's exact role/name locator for replay. The two implementations can differ at accessibility edge cases; use the mock suite to verify supported controls.
3. **A click can cause navigation.** Preserve the click and record its resulting URL as an observation, rather than blindly performing a second goto. Explicit address-bar navigation is a separate action. Redirects and history changes need observation too. There is no universal way to infer causality for arbitrarily delayed page scripts. Stop if a recorded navigation expectation does not arrive.
4. **Role/name is not always unique.** Never select `.first()` to hide ambiguity. A fallback must identify exactly one element. If role/name is ambiguous, a CSS candidate must also agree with that identity. Verify the target's tag and input type before acting. If an action times out, stop; do not try another selector after a click may already have taken effect.
5. **Screenshots are asynchronous.** An event listener cannot synchronously take a Playwright screenshot before a navigation. Store the original event's URL/title independently and label screenshots as best-effort observations captured afterward, including their actual URL/time. Capture failure must be visible, not replaced with an unrelated image. Inline base64 screenshots keep an exported recording in one JSON file.
6. **Labels are presentation, not instructions.** Keep action type, parameters, element identity, and confirmation information separate from the user-editable label. Changing a label must never change automation or bypass confirmation.
7. **Selected steps have prerequisites.** Skipping a login or an earlier form edit can make replay impossible. Do not secretly replay omitted steps. Let the user prepare the browser when replaying a subset and stop on page/target mismatch. Re-recording replaces one action after manual restoration, as explicitly approved by the user.
8. **A trace is not an assertion.** Successful Playwright calls do not prove a workflow succeeded. Assert recorded navigation where present, stop on errors, and describe replay as executing actions rather than verifying business outcomes.

## GitHub reuse audit

Repositories were shallow-cloned into the workspace's `work/references/` directory for inspection. No upstream install scripts were run. License files were inspected, not inferred from a repository description.

| Repository | Inspected commit | License | Decision |
| --- | --- | --- | --- |
| [HDRUK/web-application-tutorial-recorder](https://github.com/HDRUK/web-application-tutorial-recorder) | `bfcb6d3b97feea8d465aa3e3f4aa1cc88409bf05` | MIT | Adapt its short CSS path helper; keep its notice. Its recorder/player illustrate binding-based capture and action dispatch. Do not adopt encrypted password capture, coordinate fallback, fixed replay sleeps, or continue-on-error behavior. |
| [winst0niuss/ai-ready-pw-codegen](https://github.com/winst0niuss/ai-ready-pw-codegen) | `affbc0e853603ca1e49f9455981fbc1ee09607ff` | MIT | Schema reference only. Captures semantic targets, selector candidates and screenshots, but uses private `_enableRecorder`. Avoid dependence on private APIs and collection of DOM/network/console dumps. |
| [netsco/playwright-docs-recorder](https://github.com/netsco/playwright-docs-recorder) | `3436cb436f785f940b9463808844894f05265a3c` | MIT | Documentation/export reference only. A full fork adds Electron/Node and does not satisfy the Python core requirement. |
| [puppeteer/replay](https://github.com/puppeteer/replay) | Online schema inspected | Apache-2.0 | Useful action/navigation/selector schema reference. Not adopted as a runtime because it uses Puppeteer/Node. |
| [rrweb](https://github.com/rrweb-io/rrweb) | Online documentation inspected | MIT | Not adopted. DOM session reconstruction differs from performing actions against a live site. |

The MIT-licensed [dom-accessibility-api](https://github.com/eps1lon/dom-accessibility-api) 0.7.1 is vendored as a prebuilt browser script. It provides `computeAccessibleName` and `getRole`. Its license is shipped. The browser bundle is prepared once during development; users do not need npm or a build command.

## Privacy and local server boundaries

- Never serialize password values or password-target actions. Exclude fields with password autocomplete hints and remember elements that have ever been password fields, including after a show-password toggle.
- Apply filtering in the browser before data reaches Python; repeat checks in Python and replay. Do not collect global raw key streams, form dumps, cookies, localStorage, network payloads, or console logs.
- Mask sensitive inputs in screenshots, including formerly-password fields, and omit sensitive subtrees from nearby text. Screenshots, page titles, ordinary text fields and URLs can still contain personal data. Local storage is not encryption. State this plainly in the README.
- Full URLs may include secrets and may be necessary for exact navigation. Reject credentials in URL authority and non-HTTP(S) navigation. Users should not record pages whose query strings or visible text contain secrets. Do not claim arbitrary secret detection is possible.
- Treat recording data and website events as untrusted input. Validate action enums and payload sizes, never evaluate recorded code, and render labels/context via textContent rather than innerHTML.
- Validate the Host and Origin headers, require a per-process token for API reads/mutations, and do not enable CORS. A malicious website must not be able to control the local browser by submitting HTTP requests.
- Imported JSON needs schema validation and bounded size. Restrict screenshot data to known image MIME types. Do not let imported paths read files from disk or become output paths.
- Write JSON atomically, with private file permissions. Do not persist a normal browser profile or saved login credentials. A fresh context requires manual login where needed.
- Confirmation classification from labels cannot be guaranteed. The approved policy confirms detected risky actions and all unknown actions. Backend gates must enforce it; a JavaScript dialog alone is insufficient.

## First implementation order

1. `model.py`: versioned action format, templates, validation, plain-English export.
2. `storage.py`: atomic JSON persistence and recording list.
3. `capture.js`: event-time targets, edit coalescing, password filtering, navigation observations; include attributed selector helper.
4. `browser.py`: public Playwright API, serialized event persistence/screenshots, target resolution, replay confirmation and one-step replacement.
5. `server.py`: loopback-only endpoints and static files, Origin/Host/token checks.
6. `web/index.html`, `web/app.js`, `web/style.css`: search, editable timeline, select/export/replay/delete/re-record. No placeholder feature buttons.
7. `web/mock.html` and `tests/`: real browser checks against deterministic local interactions, plus model/server checks.
8. `README.md`, requirements and example recording: installation, usage, limitations and verified test results.

The user requested all of the broader web interactions today: frames, tabs/popups, file uploads, drag-and-drop, and keyboard shortcuts. The implementation includes these alongside clicks, text edits, checkbox/radio state, native dropdowns, and navigation. Browser/OS-reserved shortcuts and arbitrary desktop gestures remain outside a browser-page recorder. Native file selections and external file drops store metadata and request file reselection during replay; original file bytes are not persisted.

The user selected confirmation for detected risky and unknown actions. Submit/delete/pay/send controls are flagged risky. Other active interactions are unknown because arbitrary page handlers can have side effects, so those also require backend confirmation. Only passive observations bypass it.

Linux is the primary platform. Chromium launch was verified on Fedora 44. `setup.sh` and `run.sh` manage a local Python virtual environment without Node or a frontend build.

## Tests to prioritize

- Multiple buttons with the same role/name; role ambiguity must not click the wrong target.
- Stable semantic target after CSS/id change; CSS fallback when semantics are missing; duplicate/stale CSS; punctuation in IDs and names.
- aria-labelledby, aria-label, nested button spans/SVG, associated labels, unnamed elements.
- A fill followed immediately by Enter or a navigating click; final edit on stop; empty string; paste and composition.
- Link click and resulting navigation execute once; address-bar navigation; SPA history/hash; redirects.
- Delayed/disabled/covered/missing targets; stop on first replay error and never repeat an uncertain side effect.
- Checkbox and radio desired state, dropdown values, replay of renamed steps.
- Password typing, paste, show-password toggle, dynamic password fields, related screenshot masks, logs and exported JSON.
- All replay confirmations, cancel behavior, stale or repeated confirmation requests, mutations during replay.
- Replace exactly one step, preserve its ID/label, cancel re-record without changing the original, prepare the page without recording setup actions.
- Labels/nearby text containing HTML or script, invalid JSON/action types/URLs, cross-origin requests and hostile Host headers.
- Reload preserves edits/deletions; plain-English and JSON export follow original order of selected steps; screenshots survive JSON roundtrip.

## Primary technical sources

- [Playwright BrowserContext: add_init_script and expose_binding](https://playwright.dev/python/docs/api/class-browsercontext)
- [Playwright locators and strictness](https://playwright.dev/python/docs/locators)
- [Playwright text input and keyboard actions](https://playwright.dev/python/docs/input)
- [Playwright navigation and readiness](https://playwright.dev/python/docs/navigations)
- [Playwright screenshot API and masking](https://playwright.dev/python/docs/api/class-page#page-screenshot)
- [aiohttp server quickstart](https://docs.aiohttp.org/en/stable/web_quickstart.html)
- [dom-accessibility-api source and license](https://github.com/eps1lon/dom-accessibility-api)

## Visual constraints

Use the supplied transparent MemAct wordmark, a continuous #00011B background, locally served DM Sans body typography, and white text. Align the visible wordmark with the recording-search field; put its search icon on the right. Search and the action timeline are the main interface. No gradients, glow, glass, decorative blobs, marketing hero, repeated feature cards, status pills, eyebrow text, unnecessary motion, em dashes, or filler copy. Red is allowed for destructive controls such as Delete. Both search fields use a right-side search icon. Use other colors only when necessary for meaning. Preserve visible keyboard focus and readable contrast.


## Findings from implementation tests

- A Chromium popup can replace its initial document after init-script execution. The recorder now identifies the document on which listeners were installed and reinstalls only if it changed; DOMContentLoaded provides a public-API recovery hook. This is covered by popup-then-return-to-opener replay.
- Native dropdown selection and in-memory Playwright fixture assignment differ: some fixture APIs synthesize untrusted DOM change events. Recording tests use native keyboard selection and real temporary file paths so they exercise the user-event path. Synthetic events are excluded from capture.
- Enter in a textbox can synthesize a click on a different submit button. Record the key once and suppress that duplicate click so form submission happens once.
- Native dropdown keyboard selection is saved as its desired selected values, rather than replaying both the arrow key and its resulting change.
- A CSS fallback still needs identity checks. A matching ID whose accessible name changed from Export to Delete must stop, not silently click the new action.

The sidebar provides a New recording action below its search field. Long recording histories, timelines and inspectors scroll within their own regions. Scrollbars share one thin, neutral treatment throughout the app.
