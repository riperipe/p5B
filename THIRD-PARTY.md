# Third-party notices

This add-on bundles the following third-party code. Their licences apply to those
files; everything else is GPL-3.0-or-later (see LICENSE).

## p5.js 1.11.3 and p5.js 2.4.0 — MIT

Files: `host/libs/p5.min.js`, `host/libs/p5-2.min.js`

p5.js is (c) the p5.js contributors and the Processing Foundation, distributed
under the MIT licence. The full MIT text, as shipped by the project:

```
Copyright (c) 2013-2025 p5.js contributors and Processing Foundation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

MIT is compatible with GPL-3.0, so the bundle as a whole is distributed under
GPL-3.0-or-later.

## p5.js-compatibility 0.2.0 — LGPL-2.1

Not bundled. When a sketch declares `// @p5 2`, the browser host loads
`p5.js-compatibility` from jsDelivr at runtime
(https://github.com/processing/p5.js-compatibility). It is fetched by the user's
browser, not redistributed in this add-on.

## Blender

This add-on runs inside Blender and uses its Python API. Blender itself is
GPL-3.0-or-later; the add-on follows the same licence.
