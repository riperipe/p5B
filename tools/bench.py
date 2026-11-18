"""Throughput bench: blast pre-rendered frames as fast as possible.

    python tools/bench.py --width 1280 --height 720 --seconds 5
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

import numpy as np

HEADER = struct.Struct("<4sIIII")
MAGIC = b"P5B1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9877)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args()

    w, h = args.width, args.height
    # incompressible payload: worst case for the pipe
    cache = [np.random.randint(0, 256, (h, w, 4), dtype=np.uint8).tobytes() for _ in range(4)]
    sock = socket.create_connection(("127.0.0.1", args.port), timeout=5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    t0 = time.perf_counter()
    n = 0
    total = 0
    while time.perf_counter() - t0 < args.seconds:
        payload = cache[n % 4]
        sock.sendall(HEADER.pack(MAGIC, w, h, n, len(payload)) + payload)
        total += len(payload)
        n += 1
    dt = time.perf_counter() - t0
    sock.close()
    print(f"sent {n} frames in {dt:.2f}s -> {n/dt:.1f} fps, {total/dt/1e6:.1f} MB/s")


if __name__ == "__main__":
    main()
