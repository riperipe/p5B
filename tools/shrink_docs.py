# -*- coding: utf-8 -*-
"""Convert the noisy glitch screenshot to JPEG (PNG was 1.8 MB for one README image)."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
src = ROOT / "docs" / "demo_glitch.png"
dst = ROOT / "docs" / "demo_glitch.jpg"
if src.exists():
    im = Image.open(src).convert("RGB")
    im.save(dst, "JPEG", quality=88, optimize=True, progressive=True)
    print(f"{src.name} {src.stat().st_size/1024:.0f}KB -> {dst.name} {dst.stat().st_size/1024:.0f}KB")
    src.unlink()
else:
    print("demo_glitch.png already gone")

# keep the remaining PNGs modest
for p in sorted((ROOT / "docs").glob("*.png")):
    im = Image.open(p)
    if im.width > 1000:
        im = im.resize((1000, round(im.height * 1000 / im.width)), Image.LANCZOS)
        im.convert("RGB").save(p, "PNG", optimize=True)
    print(p.name, f"{p.stat().st_size/1024:.0f}KB")
