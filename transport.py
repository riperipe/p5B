"""Frame transport + control channel for the p5 -> Blender bridge.

One port serves four things:

    GET /            the sketch host page (host/index.html)
    GET /sketch.js   current sketch source (fallback when WS is unavailable)
    GET /status      JSON stats
    WS  upgrade      pixels up (binary), sketch / state / cook requests down (JSON)

Two frame grammars are accepted on the wire.

v1 (still supported, what M0/M1 senders use):
    magic "P5B1", w, h, seq, len, payload(RGBA8)

v2 (planes + cook replies):
    magic "P5B2" 4 | w u32 | h u32 | seq u32 | req u32 | nplanes u16 | flags u16
    then nplanes * { name 4s | channels u8 | byte_len u32 }
    then the plane payloads concatenated in order
      req == 0  -> a streamed frame (goes to the live image)
      req >  0  -> the answer to cook request id `req`

The only things crossing the thread boundary are `queue` objects and plain
attributes. `bpy` is touched exclusively on Blender's main thread.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time

import numpy as np

MAGIC_V1 = b"P5B1"
MAGIC_V2 = b"P5B2"
HEADER_V1 = struct.Struct("<4sIIII")
HEADER_V2 = struct.Struct("<4sIIIIHH")
PLANE_HDR = struct.Struct("<4sBI")
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_BYTES = 64 * 1024 * 1024
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))


def _recv_exact(conn: socket.socket, n: int):
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        try:
            k = conn.recv_into(view[got:], n - got)
        except (ConnectionError, OSError):
            return None
        if k <= 0:
            return None
        got += k
    return bytes(buf)


def _ws_unmask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    arr = np.frombuffer(data, dtype=np.uint8)
    key = np.frombuffer((mask * (len(data) // 4 + 1))[: len(data)], dtype=np.uint8)
    return (arr ^ key).tobytes()


def ws_text(payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        head = bytes([0x81, n])
    elif n < 65536:
        head = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x81, 127]) + struct.pack(">Q", n)
    return head + payload


def parse_frame(data: bytes):
    """bytes -> (w, h, seq, req, {plane_name: payload}) or None."""
    if len(data) < 8:
        return None
    magic = data[:4]
    if magic == MAGIC_V1:
        if len(data) < HEADER_V1.size:
            return None
        _, w, h, seq, length = HEADER_V1.unpack_from(data, 0)
        payload = data[HEADER_V1.size : HEADER_V1.size + length]
        if length != w * h * 4 or len(payload) != length:
            return None
        return w, h, seq, 0, {"rgba": payload}
    if magic != MAGIC_V2:
        return None
    if len(data) < HEADER_V2.size:
        return None
    _, w, h, seq, req, nplanes, _flags = HEADER_V2.unpack_from(data, 0)
    off = HEADER_V2.size
    table = []
    for _ in range(nplanes):
        if off + PLANE_HDR.size > len(data):
            return None
        name, channels, blen = PLANE_HDR.unpack_from(data, off)
        off += PLANE_HDR.size
        table.append((name.rstrip(b"\x00").decode("latin-1"), channels, blen))
    planes = {}
    for name, channels, blen in table:
        if off + blen > len(data):
            return None
        planes[name] = data[off : off + blen]
        off += blen
    if "rgba" not in planes or len(planes["rgba"]) != w * h * 4:
        return None
    return w, h, seq, req, planes


class FrameServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9877, queue_size: int = 2):
        self.host = host
        self.port = port
        self.frames: "queue.Queue[tuple]" = queue.Queue(maxsize=queue_size)
        self.stats = {"frames": 0, "dropped": 0, "clients": 0, "bytes": 0,
                      "protocol": None, "cooks": 0}
        self._active = 0
        self._last_frame_ts = 0.0
        self.error = None

        # main-thread -> worker
        self.sketch_source = ""
        self.sketch_version = 0
        self._sketch_dirty = True
        self._outbox: "queue.Queue[str]" = queue.Queue(maxsize=64)
        self._state_holder = None
        self._state_event = threading.Event()

        # worker -> main-thread
        self.results: dict = {}          # cook id -> (w, h, planes)
        self._results_lock = threading.Lock()
        self._cook_id = 0

        self._send_lock = threading.Lock()
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        if self._sock is not None:
            self.stop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Windows: reuse-addr lets a stale listener keep the port and silently
        # steal new connections. Exclusive use makes a second bind fail loudly.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError as exc:
            self.error = f"bind {self.host}:{self.port} failed: {exc}"
            sock.close()
            return False
        sock.listen(4)
        sock.settimeout(0.3)
        self._sock = sock
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._accept_loop, name="p5bridge-listen", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected(self) -> bool:
        return self._active > 0

    @property
    def silent_for(self) -> float:
        import time as _t
        return 0.0 if not self._last_frame_ts else round(_t.time() - self._last_frame_ts, 2)

    # --------------------------------------------------- main-thread publishing
    def publish_sketch(self, source: str, version: int) -> None:
        self.sketch_source = source
        self.sketch_version = version
        self._sketch_dirty = True
        self._put({"type": "sketch", "version": version, "source": source})

    def publish_state(self, payload: str) -> None:
        self._state_holder = payload
        self._state_event.set()

    def request_cook(self, frame: int, t: float, state: dict | None = None,
                     width: int = 0, height: int = 0) -> int:
        self._cook_id += 1
        cid = self._cook_id
        self._put({"type": "cook", "id": cid, "frame": frame, "t": t,
                   "w": width, "h": height, "state": state or {}})
        self.stats["cooks"] += 1
        return cid

    def pop_result(self, cook_id: int):
        with self._results_lock:
            return self.results.pop(cook_id, None)

    def clear_results(self) -> None:
        with self._results_lock:
            self.results.clear()

    def _put(self, msg: dict) -> None:
        try:
            self._outbox.put_nowait(json.dumps(msg))
        except queue.Full:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                pass
            try:
                self._outbox.put_nowait(json.dumps(msg))
            except queue.Full:
                pass

    # ----------------------------------------------------------------- listening
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.stats["clients"] += 1
            threading.Thread(target=self._serve_thread, args=(conn,),
                             name="p5bridge-client", daemon=True).start()

    def _serve_thread(self, conn: socket.socket) -> None:
        self._active += 1
        try:
            self._serve(conn)
        finally:
            self._active -= 1
            try:
                conn.close()
            except OSError:
                pass

    def _serve(self, conn: socket.socket) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        head = _recv_exact(conn, 1)
        if head is None:
            return
        while len(head) < 4:
            more = _recv_exact(conn, 1)
            if more is None:
                return
            head += more
        if head[:3] == b"GET":
            request = self._read_headers(conn, head)
            if request is None:
                return
            text = request.decode("latin-1")
            if "upgrade: websocket" in text.lower():
                self._serve_websocket(conn, text)
            else:
                self._serve_http(conn, text)
        else:
            self._serve_raw(conn, head)

    @staticmethod
    def _read_headers(conn: socket.socket, head: bytes):
        data = bytearray(head)
        while b"\r\n\r\n" not in data:
            chunk = _recv_exact(conn, 1)
            if chunk is None:
                return None
            data += chunk
            if len(data) > 16 * 1024:
                return None
        return bytes(data)

    # --------------------------------------------------------------------- http
    def _serve_http(self, conn: socket.socket, request: str) -> None:
        self.stats["http_requests"] = self.stats.get("http_requests", 0) + 1
        try:
            path = request.split(" ")[1].split("?")[0]
        except IndexError:
            path = "/"
        if path in ("/", "/index.html"):
            self._send_file(conn, os.path.join(ADDON_DIR, "host", "index.html"), "text/html; charset=utf-8")
        elif path == "/sketch.js":
            self._send_http(conn, 200, "text/javascript; charset=utf-8", self.sketch_source.encode("utf-8"))
        elif path == "/status":
            body = json.dumps({"version": self.sketch_version, "connected": self.connected,
                               "frames": self.stats["frames"], "cooks": self.stats["cooks"]}).encode()
            self._send_http(conn, 200, "application/json", body)
        else:
            self._send_http(conn, 404, "text/plain", b"not found")

    def _send_file(self, conn, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            self._send_http(conn, 500, "text/plain", f"missing {exc}".encode())
            return
        self._send_http(conn, 200, content_type, body)

    @staticmethod
    def _send_http(conn, status: int, content_type: str, body: bytes) -> None:
        reason = {200: "OK", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            conn.sendall(head + body)
        except OSError:
            pass

    # ---------------------------------------------------------------------- raw
    def _serve_raw(self, conn: socket.socket, head: bytes) -> None:
        """Plain TCP carries v1 frames only (local helpers, synthetic senders)."""
        self.stats["protocol"] = "tcp"
        conn.settimeout(10.0)
        pending = head
        while not self._stop.is_set():
            rest = _recv_exact(conn, HEADER_V1.size - len(pending))
            if rest is None:
                return
            magic, w, h, seq, length = HEADER_V1.unpack(pending + rest)
            if magic != MAGIC_V1 or length != w * h * 4 or length > MAX_FRAME_BYTES:
                self.error = f"bad raw header magic={magic!r} {w}x{h} len={length}"
                return
            payload = _recv_exact(conn, length)
            if payload is None:
                return
            self._push(seq, w, h, {"rgba": payload})
            pending = b""

    # ---------------------------------------------------------------- websocket
    def _serve_websocket(self, conn: socket.socket, request: str) -> None:
        key = ""
        for line in request.split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if not key:
            return
        accept = base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
        try:
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
        except OSError:
            return
        self.stats["protocol"] = "websocket"
        self._sketch_dirty = True
        conn.settimeout(10.0)
        stop_evt = threading.Event()
        sender = threading.Thread(target=self._down_loop, args=(conn, stop_evt), daemon=True)
        sender.start()
        try:
            self._read_loop(conn)
        finally:
            stop_evt.set()

    def _down_loop(self, conn: socket.socket, stop_evt: threading.Event) -> None:
        if self._sketch_dirty:
            self._sketch_dirty = False
            self._send_json(conn, {"type": "hello", "version": self.sketch_version})
            self._send_json(conn, {"type": "sketch", "version": self.sketch_version,
                                   "source": self.sketch_source})
        while not stop_evt.is_set() and not self._stop.is_set():
            if self._state_event.wait(0.005):
                self._state_event.clear()
                state = self._state_holder
                if state:
                    self._send_text(conn, state)
            while True:
                try:
                    msg = self._outbox.get_nowait()
                except queue.Empty:
                    break
                self._send_text(conn, msg)

    def _send_json(self, conn, obj: dict) -> None:
        self._send_text(conn, json.dumps(obj))

    def _send_text(self, conn, text: str) -> None:
        frame = ws_text(text.encode("utf-8"))
        with self._send_lock:
            try:
                conn.sendall(frame)
            except OSError:
                pass

    def _read_loop(self, conn: socket.socket) -> None:
        msg = bytearray()   # per-connection reassembly buffer
        while not self._stop.is_set():
            first = _recv_exact(conn, 2)
            if first is None:
                return
            fin = bool(first[0] & 0x80)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                ext = _recv_exact(conn, 2)
                if ext is None:
                    return
                length = struct.unpack(">H", ext)[0]
            elif length == 127:
                ext = _recv_exact(conn, 8)
                if ext is None:
                    return
                length = struct.unpack(">Q", ext)[0]
            mask = b"\x00\x00\x00\x00"
            if masked:
                mask = _recv_exact(conn, 4)
                if mask is None:
                    return
            data = _recv_exact(conn, length) if length else b""
            if data is None:
                return
            if opcode == 0x8:
                return
            if opcode == 0x9:
                with self._send_lock:
                    try:
                        conn.sendall(bytes([0x8A, len(data)]) + data)
                    except OSError:
                        pass
                continue
            if opcode == 0x1:
                if masked:
                    data = _ws_unmask(data, mask)
                try:
                    msg = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if msg.get("type") == "want_sketch":
                    self._sketch_dirty = True
                    self._send_json(conn, {"type": "sketch", "version": self.sketch_version,
                                           "source": self.sketch_source})
                elif msg.get("type") == "ready":
                    self._state_event.set()
                continue
            if opcode not in (0x2, 0x0):
                continue
            if masked:
                data = _ws_unmask(data, mask)
            # Browsers split large binary messages into fragments (FIN=0 + continuations).
            # Assemble the whole message before parsing, otherwise every big frame
            # arrives truncated and reads as "bad ws frame".
            if opcode == 0x2:
                msg = bytearray()
            msg += data
            if len(msg) > MAX_FRAME_BYTES + 64:
                self.error = f"oversized ws message {len(msg)}"
                msg = bytearray()
                continue
            if not fin:
                continue
            payload = bytes(msg)
            msg = bytearray()
            frame = parse_frame(payload)
            if frame is None:
                self.error = f"bad ws frame ({len(payload)} bytes)"
                continue
            w, h, seq, req, planes = frame
            if req > 0:
                with self._results_lock:
                    self.results[req] = (w, h, planes)
            else:
                self._push(seq, w, h, planes)

    # -------------------------------------------------------------------- queue
    def _push(self, seq: int, width: int, height: int, planes: dict) -> None:
        self.stats["frames"] += 1
        self.stats["bytes"] += sum(len(v) for v in planes.values())
        self._last_frame_ts = time.time()
        try:
            self.frames.put_nowait((seq, width, height, planes))
        except queue.Full:
            self.stats["dropped"] += 1
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait((seq, width, height, planes))
            except queue.Full:
                pass
