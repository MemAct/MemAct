# Third-party notices

## Adapted CSS selector helper

`capture.js` adapts the CSS path construction in `recorder/inject.js` from [HDRUK/web-application-tutorial-recorder](https://github.com/HDRUK/web-application-tutorial-recorder), commit `bfcb6d3b97feea8d465aa3e3f4aa1cc88409bf05`.

Copyright (c) 2026 HDR UK. MIT license: [licenses/HDRUK-MIT.txt](licenses/HDRUK-MIT.txt).

MemAct adds uniqueness checks, avoids coordinate fallback, and separates the selector from semantic role/name targeting. The upstream application is not bundled or executed.

## Accessible names and roles

`vendor/accessibility.js` bundles dom-accessibility-api 0.7.1 from [eps1lon/dom-accessibility-api](https://github.com/eps1lon/dom-accessibility-api).

Copyright (c) 2020 Sebastian Silbermann. MIT license: [licenses/dom-accessibility-api-MIT.txt](licenses/dom-accessibility-api-MIT.txt).

## Orbitron

Orbitron is served locally in `web/assets/orbitron.ttf`. Its SIL Open Font License and copyright information are included in [licenses/Orbitron-OFL.txt](licenses/Orbitron-OFL.txt).

## Runtime dependencies

Playwright (Apache-2.0) and aiohttp (Apache-2.0 AND MIT, per its installed package metadata) are installed separately through Python packaging. Their distributions carry their own license notices. No code is copied from the other reference applications listed in RESEARCH.md.

## DM Sans

DM Sans Regular, Medium, and Semibold are served locally from `web/assets/`. Source: Google Fonts' DM Sans distribution. The SIL Open Font License and copyright information are included in [licenses/DM-Sans-OFL.txt](licenses/DM-Sans-OFL.txt).
