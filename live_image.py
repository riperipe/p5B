"""Live Image datablock fed by canvas frames.

Measured in Blender 5.1.1 / EEVEE+Cycles with Standard view transform:

    write 0.5 into an 8-bit image, colorspace 'sRGB'  ->  renders back 0.5   (128/255)
    write 0.5 into an 8-bit image, colorspace 'Non-Color' -> renders 0.7379  (188/255)
    write 0.5 into a float image,  colorspace 'sRGB'  ->  renders 0.7379  (188/255)

So: an 8-bit image tagged 'sRGB' is a *byte passthrough*. Canvas bytes
(0..255) / 255 land in the file and come out of the shader unchanged.
Do not "helpfully" linearise them; do not use float buffers.

Second trap: `colorspace_settings` must be set **before** pixels are written.
Assigning it afterwards reloads the buffer and silently wipes the data.
"""

from __future__ import annotations

import bpy
import numpy as np


def frame_to_float_rgba(payload: bytes, width: int, height: int) -> np.ndarray:
    """canvas RGBA8 (top-down) -> Blender float RGBA (bottom-up), sRGB-encoded."""
    a = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 4)
    return np.ascontiguousarray(a[::-1], dtype=np.float32).ravel() / 255.0


class LiveImage:
    def __init__(self, name: str = "P5B_live", width: int = 640, height: int = 360):
        self.name = name
        self.width = 0
        self.height = 0
        self.image = None
        self.frames = 0
        self._configure(width, height)

    def _configure(self, width: int, height: int) -> None:
        img = bpy.data.images.get(self.name)
        if img is None:
            img = bpy.data.images.new(self.name, width=width, height=height, alpha=True, float_buffer=False)
        # settings BEFORE pixels: changing colorspace resets the buffer
        img.colorspace_settings.name = "sRGB"
        img.alpha_mode = "STRAIGHT"
        img.use_fake_user = True
        img.generated_color = (0.0, 0.0, 0.0, 1.0)
        if tuple(img.size) != (width, height):
            img.scale(width, height)
        self.image = img
        self.width, self.height = width, height

    def push(self, payload: bytes, width: int, height: int) -> None:
        self.push_planes({"rgba": payload}, width, height)

    def _valid_image(self):
        """Blender's undo can invalidate a Python-held datablock reference.

        A stale reference raises ReferenceError and every later write fails with
        "StructRNA of type Image has been removed", which looks exactly like the
        canvas freezing. Re-resolve by name instead of trusting the cached one.
        """
        img = self.image
        if img is None:
            return None
        try:
            name = img.name
        except (ReferenceError, RuntimeError):
            return None
        return img if bpy.data.images.get(name) is img else None

    def push_planes(self, planes: dict, width: int, height: int) -> None:
        if (width, height) != (self.width, self.height):
            self._configure(width, height)
        rgba = planes.get("rgba")
        if rgba is None:
            return
        if self._valid_image() is None:
            self._configure(width, height)
        self.image.pixels.foreach_set(frame_to_float_rgba(rgba, width, height))
        self.image.update()
        mask = planes.get("mask")
        if mask is not None:
            self._push_mask(mask, width, height)
        self.frames += 1

    def _push_mask(self, payload: bytes, width: int, height: int) -> None:
        """Optional single-channel plane -> P5B_mask, usable as a shader factor."""
        img = bpy.data.images.get("P5B_mask")
        if img is None:
            img = bpy.data.images.new("P5B_mask", width=width, height=height, alpha=False, float_buffer=False)
            img.colorspace_settings.name = "Non-Color"
            img.use_fake_user = True
        if tuple(img.size) != (width, height):
            img.scale(width, height)
        src = np.frombuffer(payload, dtype=np.uint8).reshape(height, width)[::-1]
        rgba = np.empty((height, width, 4), dtype=np.float32)
        v = src.astype(np.float32) / 255.0
        rgba[..., 0] = v
        rgba[..., 1] = v
        rgba[..., 2] = v
        rgba[..., 3] = 1.0
        img.pixels.foreach_set(np.ascontiguousarray(rgba).ravel())
        img.update()
