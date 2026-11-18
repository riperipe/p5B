"""Test sender: streams a synthetic animated canvas to the Blender receiver.

Run with Blender's bundled interpreter (numpy available, no extra deps):

    & "D:\\Anzhuang\\Blender Foundation\\Blender 5.1\\5.1\\python\\bin\\python.exe" tools\\sender.py --seconds 10
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import time

import numpy as np

HEADER = struct.Struct("<4sIIII")
MAGIC = b"P5B1"


def render_frame(w: int, h: int, t: float) -> bytes:
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u = x / w
    v = y / h
    img = np.empty((h, w, 4), dtype=np.uint8)
    # background: vertical gradient with a slow hue shift
    hue = 0.55 + 0.05 * math.sin(t * 0.6)
    img[..., 0] = np.clip(40 + 90 * u + 20 * np.sin(t + v * 3), 0, 255).astype(np.uint8)
    img[..., 1] = np.clip(30 + 70 * v + 25 * np.sin(t * 1.3 + u * 2), 0, 255).astype(np.uint8)
    img[..., 2] = np.clip(110 + 90 * hue + 30 * np.sin(t * 0.9 + u + v), 0, 255).astype(np.uint8)
    # orbiting disc
    cx, cy = 0.5 + 0.32 * math.cos(t * 1.7), 0.5 + 0.32 * math.sin(t * 1.7)
    r = 0.09
    d = np.sqrt((u - cx) ** 2 + (v - cy) ** 2 * (w / h) ** 2)
    disc = d < r
    img[disc, 0] = 255
    img[disc, 1] = 220
    img[disc, 2] = 120
    # rotating bar
    ang = t * 2.0
    dx, dy = u - 0.5, (v - 0.5)
    proj = dx * math.cos(ang) + dy * math.sin(ang)
    perp = -dx * math.sin(ang) + dy * math.cos(ang)
    bar = (np.abs(proj) < 0.012) & (np.abs(perp) < 0.22)
    img[bar, 0] = 90
    img[bar, 1] = 240
    img[bar, 2] = 255
    img[..., 3] = 255
    return img.tobytes()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"connected to {args.host}:{args.port}  {args.width}x{args.height}")

    interval = 1.0 / args.fps
    t0 = time.perf_counter()
    n = 0
    sent_bytes = 0
    last = t0
    try:
        while True:
            t = time.perf_counter() - t0
            if t > args.seconds:
                break
            payload = render_frame(args.width, args.height, t)
            sock.sendall(HEADER.pack(MAGIC, args.width, args.height, n, len(payload)) + payload)
            n += 1
            sent_bytes += len(payload)
            if n % 30 == 0:
                now = time.perf_counter()
                print(f"  sent {n} frames  {n / (now - t0):.1f} fps  {(sent_bytes / 1e6) / (now - t0):.1f} MB/s")
            wait = t0 + n * interval - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
    finally:
        sock.close()
    dt = time.perf_counter() - t0
    print(f"done: {n} frames in {dt:.2f}s  ->  {n / dt:.1f} fps  {(sent_bytes / 1e6) / dt:.1f} MB/s")


if __name__ == "__main__":
    main()
