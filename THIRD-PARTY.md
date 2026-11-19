# Third-party notices

This add-on **p5B** is distributed under **GPL-3.0-or-later** (see `LICENSE`).
The files listed below keep their own licences; nothing else in this repository is
third-party code.

## p5.js 1.11.3 and p5.js 2.4.0 — **LGPL-2.1** (not MIT)

Files: `host/libs/p5.min.js` (1.11.3), `host/libs/p5-2.min.js` (2.4.0)

p5.js is copyright (c) 2013-2025 the p5.js contributors and the Processing Foundation,
and is distributed under the **GNU Lesser General Public License, version 2.1
(LGPL-2.1)** — <https://github.com/processing/p5.js/blob/main/LICENSE>.

Both files are unmodified upstream builds. The full LGPL-2.1 text ships with this
add-on at [`host/libs/LICENSE-p5.txt`](host/libs/LICENSE-p5.txt).

> Note: earlier versions of this repository stated that p5.js was MIT licensed.
> That was wrong; it has always been LGPL-2.1.

### What LGPL-2.1 requires here

- The licence text travels with the library — done, see `host/libs/LICENSE-p5.txt`.
- Users are told which library is used and where the source lives — this file and
  the README.
- The library stays replaceable: both builds are plain files under `host/libs/`.
  Replace either file with your own p5.js build and the host will use it (the CDN
  copy is only a fallback when the local file is missing).

### Why GPL-3.0-or-later still works for the add-on

LGPL-2.1 §3 allows a work that uses the library to be distributed under the GPL.
The add-on as a whole is therefore GPL-3.0-or-later, while the two bundled p5.js
files remain LGPL-2.1. No p5.js source is modified, so nothing needs to be
published back — but the LGPL text and the source pointer must stay in the bundle.

## p5.js-compatibility 0.2.0 — LGPL-2.1 (not bundled)

When a sketch declares `// @p5 2`, the browser host loads
`p5.js-compatibility@0.2.0` from jsDelivr at runtime
(<https://github.com/processing/p5.js-compatibility>). Those files are fetched by
the user's browser and are **not** redistributed in this add-on.

That request only happens for sketches that ask for p5 2.x, and only when online
access is allowed both in Blender and in the browser.

## Blender

This add-on runs inside Blender and uses its Python API. Blender itself is
GPL-3.0-or-later; the add-on follows the same licence.
