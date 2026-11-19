# p5inBlender

Write p5.js sketches **inside Blender** and use the live canvas as a texture on any
mesh, mapped through UVs. The 2D canvas is the "window"; UV islands decide where
that window opens on your model.

[中文说明](README.zh.md)

![glitch sketch running on p5 2.4](docs/demo_glitch.png)

## What it does

- A real browser runs your sketch (p5.js 1.x or 2.x) — Blender receives the pixels
  every frame over a local socket.
- The frames land in an `Image` datablock, so the Image Editor, EEVEE, Cycles and
  the viewport all show the canvas live.
- A ready-made UV window plane and material come with it; bind the material to any
  mesh to project the canvas through its UVs.
- Bidirectional: JavaScipt can read Blender state (tracked objects, geometry,
  an upstream texture) and Blender writes the canvas back to an image.

## Install

1. Download `p5inBlender-<version>.zip` from Releases (or build it from this repo).
2. Blender → `Edit → Preferences → Get Extensions → Install from Disk…` (for
   Blender 4.2+) or `Add-ons → Install…` and pick the zip.
3. Enable **p5inBlender**, then open the 3D View sidebar (`N`) → `p5inBlender`.

## Quick start

1. `Sketch` panel → **Start** (this opens a hidden browser, starts the receiver and
   pushes the current sketch).
2. `Sketch` panel → **Edit**: a Text datablock opens in the Text Editor; write
   JavaScript there. *Auto Run on Edit* pushes it about 0.3 s after you stop typing.
3. Put `https://github.com/...`-style libraries at the top of the sketch:

```js
// @lib https://cdn.jsdelivr.net/gh/antiboredom/p5.riso@master/lib/p5.riso.js
// @mode global      // classic global-mode sketch (setup/draw at top level)
// @p5 2             // run on p5 2.x + p5.js-compatibility add-ons
```

4. `Canvas` panel → **Window Plane** creates the 2×2 canvas and its material;
   **Bind Mesh** applies that material to whatever is selected.

## The canvas coordinate system

The canvas is a **2×2 square lying flat on XY** (exactly like `Add → Mesh → Plane`).
Everything is expressed relative to it — the camera does not matter:

```
u = local x / 2 + 0.5        v = 0.5 − local y / 2
visible = u, v ∈ [0, 1]      depth = height above the canvas (along its normal)
```

The sketch image is letterboxed inside the square (uniform scale, never stretched),
so `u, v` are coordinates **inside the image**, not inside the square.

```js
function sketch(p) {
  p.draw = function () {
    const d = p.p5inblender;          // { state, t, frame, w, h, hud, texture }
    const st = d.state;               // { objects[], geo[], canvas, frame, t, hud }
    st.objects.forEach(o => p.circle(o.x * p.width, o.y * p.height, 12));
  };
}
```

Track an empty with `Tracking → Track Selected` to feed `state.objects`; mark a
mesh with `Send Geometry` to get its wireframe projected onto the canvas.

## Material

New canvases get a Principled material with:

- `Image → Base Color` **and** `Emission Color` (visible without lights),
- `Image.Alpha → P5B Alpha Ramp → Alpha` (blended, so transparent sketches stay
  transparent in Blender),
- a detached `P5B Color Ramp` you can wire in for duotone/palette looks.

`Canvas → Reset Material` rebuilds it in place. Hand-edited materials are never
overwritten automatically.

## Panels

| Panel | Contents |
|---|---|
| **Sketch** (first) | status line, Start/Stop, Run/Edit, sketch picker, Follow Edited Text, Edit Externally, Reset Demo |
| **Canvas** | Show, Window Plane, Bind Mesh, Reset Material, Standardize Canvas, Show Live Info, Texture + “Use Own Canvas as Texture”, host Open/Background/Close, port/size |
| **Tracking** | Track Selected, Send Geometry, tracked/on-screen counts |
| **Render** | Cook, Cook + Render, Render Animation (deterministic per-frame pulls) |
| **Instances** | several independent canvases, each with its own text/image/material/port |

## Working in an external editor

Blender's text editor only has Python syntax rules, so JavaScript stays plain.
`Sketch → Edit Externally` saves the sketch to `//p5inblender_sketches/`, opens it in
your OS editor (VS Code gives real JS highlighting + the `p5.vscode` API
completion), and syncs your saves back into Blender within ~0.3 s.

## p5 versions

- Default: **p5 1.11.3**, vendored in `host/libs/p5.min.js` (works offline).
- `// @p5 2`: runs on **p5 2.4.0** (also vendored) inside a same-origin iframe and
  loads the [p5.js-compatibility](https://github.com/processing/p5.js-compatibility)
  add-ons (`preload`, `shapes`, `data`, `events`) from a CDN, so 1.x-era code keeps
  working.

## Troubleshooting

| Symptom | Check |
|---|---|
| Nothing updates | `Sketch` panel: is the picker pointing at the file you edit? The status line shows `vN` — it must increase. |
| Sketch never appears | `Canvas → Show` to open the live image; check the status line for `sketch:` errors. |
| Picture flickers | Several browser pages are connected; the newest healthy client owns the canvas, close the extra tabs (`stale` counter tells you). |
| Choppy updates | `旧连接帧/stale` growing means a throttled background tab is competing. |
| Panel shows `panel error:` | That line is the actual exception — report it. |
| Slow first update | The sketch is reloaded in the browser on every run; heavy `setup()` (shader compile, big graphics) costs time. |

## Development

```
p5inBlender/
├── __init__.py        add-on entry: operators, panels, pump, cook
├── transport.py       one port: HTTP host page + WebSocket frames + raw TCP v1
├── live_image.py      Image datablock writes (sRGB byte passthrough)
├── window.py          UV window plane + default material
├── i18n.py            English/Chinese UI strings
├── host/              browser host (index.html + vendored p5 1.x / 2.x)
└── tools/             synthetic sender, bench, demo sketches
```

Measured on Blender 5.1 / Windows: 640×360 at ~60 fps, 720p ceiling ~127 fps,
cook round-trip 10–52 ms, image upload 0.69 ms (640×360).

Licence: MIT (the vendored p5.js builds keep their own MIT licence).
