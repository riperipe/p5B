# p5inBlender — 把 2D 画布当作实时纹理喂进 Blender
#
# 结构：
#   transport.py   端口上同时提供 HTTP（宿主页面）+ WebSocket（像素上行 / 状态下行）
#   live_image.py  Image datablock 写入（已实测色彩空间规则，见文件头）
#   window.py      UV 窗口平面 + 材质节点树（UV -> Mapping -> Image -> Emission）
#   host/index.html  浏览器端宿主：接收 sketch 源码与场景快照，逐帧回传像素
#
# 工作流：
#   1. 点 "New Sketch" 生成 Text datablock p5b_sketch.js，并切出一个 Text Editor
#   2. 在 Blender 里改 JS，点 "Run"
#   3. 浏览器打开 http://127.0.0.1:9877/ ，Image Editor 与贴了材质的模型立刻出画面

import json
import os
import queue
import subprocess
import sys
import tempfile
import time

import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

from . import live_image, transport, window

bl_info = {
    "name": "p5inBlender",
    "author": "DeepseekWorkzone",
    "version": (0, 20, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > p5inBlender",
    "description": "Live 2D canvas as a UV-mapped texture, coded in Blender's text editor",
    "category": "Material",
}

DEFAULT_PORT = 9877
SKETCH_NAME = "p5b_sketch.js"
CHROME_REL = (
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> str | None:
    """Locate a Chromium-family browser. Blender's own Python has no web engine;
    this is the runtime the sketch actually executes in."""
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LocalAppData", "")]
        for root in roots:
            if not root:
                continue
            for rel in CHROME_REL:
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    return path
        return None
    if sys.platform == "darwin":
        for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                     "/Applications/Chromium.app/Contents/MacOS/Chromium"):
            if os.path.isfile(path):
                return path
        return None
    from shutil import which
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        path = which(name)
        if path:
            return path
    return None


def launch_host(mode: str = "window", port: int = DEFAULT_PORT):
    """Start the sketch host. mode: 'window' = app window, 'headless' = no window."""
    st = _state
    hosts = st.setdefault("hosts", [])
    for entry in hosts:
        if entry["proc"].poll() is None and entry["port"] == port:
            return entry["proc"]
    exe = find_browser()
    if exe is None:
        st["error"] = "no Chromium-family browser found (Chrome/Edge/Chromium)"
        return None
    profile = os.path.join(tempfile.gettempdir(), f"p5bridge-profile-{port}")
    url = f"http://127.0.0.1:{port}/"
    args = [
        exe,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--mute-audio",
        # 这几条是"浏览器被 Blender 遮住就停止出帧"的解药
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-features=CalculateNativeWinOcclusion",
    ]
    if mode == "headless":
        args += ["--headless=new", "--window-size=680,460", url]
    else:
        args += [f"--app={url}", "--window-size=680,460"]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(args, creationflags=creationflags,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        st["error"] = f"launch failed: {exc}"
        return None
    hosts.append({"proc": proc, "port": port, "mode": mode})
    st["browser_proc"] = proc
    st["browser_mode"] = mode
    return proc


def close_host() -> bool:
    st = _state
    hosts = st.get("hosts") or []
    st["hosts"] = []
    st["browser_proc"] = None
    st["browser_mode"] = ""
    closed = False
    for entry in hosts:
        proc = entry["proc"]
        if proc.poll() is not None:
            continue
        closed = True
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return closed


def host_status() -> dict:
    st = _state
    hosts = [h for h in (st.get("hosts") or []) if h["proc"].poll() is None]
    last = hosts[-1] if hosts else None
    return {"alive": bool(hosts),
            "count": len(hosts),
            "mode": last["mode"] if last else "",
            "pid": last["proc"].pid if last else None,
            "ports": [h["port"] for h in hosts]}

TEMPLATE = r"""// p5b_sketch.js —— p5inBlender demo
//
// 这一份同时演示三件事：
//   1. 被跟踪物体（Tracking → Track Selected）的屏幕坐标，画成带拖尾的光点
//   2. 模型线框（Tracking → Send Geometry），Blender 投影好直接画线
//   3. 上游纹理（Canvas → Texture / Feedback），当背景画，做自反馈
//
// 改完不用点 Run（Auto Run on Edit 开着时约 0.3 秒自动生效）。

let trails = {};

function sketch(p) {
  p.setup = function () {
    const d = p.p5inblender || {};
    p.createCanvas(d.w || 640, d.h || 360);
    p.frameRate(60);
    p.pixelDensity(1);
  };

  p.draw = function () {
    const D = p.p5inblender || {};
    const state = D.state || { objects: [], geo: [] };
    const objs = state.objects || [];
    const geo = state.geo || [];
    const tex = D.texture;

    // 1) 背景：有上游纹理就铺纹理（自反馈），否则用半透明覆盖做拖尾
    if (tex) {
      p.push();
      p.translate(p.width / 2, p.height / 2);
      p.rotate(0.0012 * (p.frameCount % 2000));
      p.scale(0.9975);
      p.drawingContext.globalAlpha = 0.96;
      p.drawingContext.drawImage(tex, -p.width / 2, -p.height / 2, p.width, p.height);
      p.drawingContext.globalAlpha = 1;
      p.pop();
    } else {
      p.noStroke();
      p.fill(6, 8, 14, 38);
      p.rect(0, 0, p.width, p.height);
    }

    // 2) 网格底纹（每帧重画，避免拖尾糊成一片）
    p.stroke(120, 200, 255, 12);
    p.strokeWeight(1);
    for (let x = 0; x < p.width; x += 48) p.line(x, 0, x, p.height);
    for (let y = 0; y < p.height; y += 48) p.line(0, y, p.width, y);

    // 3) 几何线框：Blender 已经投影到 0..1，这里只乘尺寸
    geo.forEach(function (g) {
      p.noFill();
      p.strokeWeight(1.2);
      p.stroke(0, 255, 190, 90);
      p.beginShape(p.LINES);
      (g.edges || []).forEach(function (e) {
        const va = (g.verts || [])[e[0]];
        const vb = (g.verts || [])[e[1]];
        if (!va || !vb) return;
        p.vertex(va[0] * p.width, va[1] * p.height);
        p.vertex(vb[0] * p.width, vb[1] * p.height);
      });
      p.endShape();

      p.noStroke();
      p.fill(0, 255, 210, 110);
      (g.verts || []).forEach(function (v) {
        p.circle(v[0] * p.width, v[1] * p.height, 2.5);
      });
    });

    // 4) 被跟踪物体：拖尾 + 脉冲光环 + 标签
    objs.forEach(function (o, i) {
      if (o.visible === false) return;
      const x = o.x * p.width;
      const y = o.y * p.height;
      const tr = trails[i] || (trails[i] = []);
      tr.push([x, y]);
      if (tr.length > 48) tr.shift();

      p.noFill();
      p.stroke(255, 170, 60, 70);
      p.strokeWeight(2);
      p.beginShape();
      for (let k = 0; k < tr.length; k++) p.vertex(tr[k][0], tr[k][1]);
      p.endShape();

      const pulse = 1 + 0.14 * Math.sin((D.t || 0) * 6 + i);
      p.stroke(255, 200, 90, 210);
      p.strokeWeight(1.5);
      p.circle(x, y, 34 * pulse);
      p.stroke(255, 200, 90, 80);
      p.circle(x, y, 58 * pulse);

      p.noStroke();
      p.fill(255, 255, 240);
      p.circle(x, y, 8);

      p.fill(255, 230, 200, 200);
      p.textSize(11);
      p.text(o.name, x + 22, y - 16);
    });

    // 5) HUD（N 面板 Canvas → Show Live Info 控制，读 p5inblender.hud）
    if (D.hud !== false) {
      p.noStroke();
      p.fill(200, 230, 255, 190);
      p.textSize(11);
      p.text('frame ' + (state.frame || 0) +
             '   t=' + (D.t || 0).toFixed(2) + 's' +
             '   tracked ' + objs.length +
             '   geo ' + geo.length +
             '   ' + p.width + 'x' + p.height +
             (tex ? '   tex' : ''), 12, p.height - 12);
    }
  };
}
"""

_state = {
    "instances": {},      # name -> runtime dict (server, image, counters)
    "active": "",
    "fps": 0.0,
    "state": None,
    "error": "",
    "browser_proc": None,
    "browser_mode": "",
}


# --------------------------------------------------------------------- helpers
_ICON_CACHE = None


def _icon(name: str) -> str:
    """返回一个真实存在的图标名；名字写错也不会让面板 draw 抛异常（面板会闪烁）。"""
    global _ICON_CACHE
    if _ICON_CACHE is None:
        try:
            enum = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
            _ICON_CACHE = {i.identifier for i in enum}
        except Exception:
            _ICON_CACHE = set()
    return name if (not _ICON_CACHE or name in _ICON_CACHE) else "NONE"


def _tag_redraw():
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            screen = win.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type in {"IMAGE_EDITOR", "VIEW_3D", "TEXT_EDITOR", "PROPERTIES"}:
                    area.tag_redraw()


def canvas_frame(plane, img_size=None):
    """画布坐标系：以画布平面自身为准（和相机无关）。

    画布是 XY 平面上的 2x2 方片（同 Add > Mesh > Plane）。图像按原比例居中放进
    这块方片（letterbox，不拉伸），所以物体坐标 = 图像内的归一化坐标。
    """
    if plane is None:
        return None
    mw = plane.matrix_world
    inv = mw.inverted()
    corners = [Vector(c) for c in plane.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    sx = (max(xs) - min(xs)) or 2.0
    sy = (max(ys) - min(ys)) or 2.0
    cx = (max(xs) + min(xs)) * 0.5
    cy = (max(ys) + min(ys)) * 0.5
    fx, fy = window.fit_rect(*(img_size or (0, 0)))
    return {"inv": inv, "sx": sx, "sy": sy, "cx": cx, "cy": cy,
            "fx": fx, "fy": fy, "name": plane.name}


def project_to_canvas(point, frame):
    """世界坐标 → 画布 uv（u 向右、v 向下，和图像坐标一致）+ 离画布高度。"""
    if frame is None:
        return 0.0, 0.0, 0.0
    local = frame["inv"] @ Vector(point)
    u = (local.x - frame["cx"]) / frame["sx"] + 0.5
    v = 0.5 - (local.y - frame["cy"]) / frame["sy"]
    # 方片坐标 → 图像坐标（图像只占方片中间一条）
    u = (u - 0.5) / frame["fx"] + 0.5
    v = (v - 0.5) / frame["fy"] + 0.5
    return u, v, local.z


def canvas_plane_of(rec):
    if rec is None or not rec.plane:
        return None
    return bpy.data.objects.get(rec.plane)


def live_size_of(rec):
    rt = _runtime(rec.name) if rec is not None else None
    if rt is None or rt["live"] is None:
        return None
    return (int(rt["live"].width) or 1, int(rt["live"].height) or 1)


def build_state(scene, plane=None, img_size=None) -> dict:
    """What the sketch gets to see: tracked objects in canvas space."""
    frame = canvas_frame(plane, img_size)
    objects = []
    if frame is not None:
        for ob in scene.objects:
            if not ob.get("p5b_track"):
                continue
            co = ob.matrix_world.translation
            u, v, height = project_to_canvas(co, frame)
            objects.append({
                "name": ob.name,
                "x": round(float(u), 5),
                "y": round(float(v), 5),
                "depth": round(float(height), 4),  # 高出画布多少（画布法线方向）
                "visible": bool(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0),
            })
    return {
        "type": "state",
        "frame": scene.frame_current,
        "t": round(scene.frame_current / max(scene.render.fps, 1), 4),
        "hud": bool(getattr(scene, "p5b_show_hud", True)),
        "canvas": ({"name": frame["name"], "w": round(frame["sx"], 4), "h": round(frame["sy"], 4)}
                   if frame else None),
        "objects": objects,
    }


# ------------------------------------------------------------------ instances
def instance_records():
    return bpy.context.scene.p5b_instances


def instance_record(name: str):
    for rec in instance_records():
        if rec.name == name:
            return rec
    return None


def active_record():
    sc = bpy.context.scene
    if not len(sc.p5b_instances):
        return None
    return sc.p5b_instances[min(max(sc.p5b_active, 0), len(sc.p5b_instances) - 1)]


def _runtime(name: str):
    return _state["instances"].get(name)


def _new_runtime(name: str) -> dict:
    return {"name": name, "server": None, "live": None, "frames": 0, "fps": 0.0,
            "t0": 0.0, "n": 0, "last_seq": -1, "last_size": (0, 0),
            "sketch_version": 0, "error": "", "aspect_size": None, "geo": []}


def _ensure_pump():
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.005)


def create_instance(name: str, port: int | None = None, with_plane: bool = True):
    """Create an independent canvas: own text datablock, image, material, plane."""
    sc = bpy.context.scene
    rec = sc.p5b_instances.add()
    rec.name = name
    base = int(getattr(sc, "p5b_port", DEFAULT_PORT))
    rec.port = int(port) if port else base + max(len(sc.p5b_instances) - 1, 0)
    rec.text = f"p5b_sketch_{name}.js"
    rec.image = f"P5B_live_{name}"
    rec.material = f"P5B_Canvas_{name}"
    rec.plane = ""
    rec.width = int(getattr(sc, "p5b_width", 640))
    rec.height = int(getattr(sc, "p5b_height", 360))
    text = bpy.data.texts.get(rec.text) or bpy.data.texts.new(rec.text)
    if not text.as_string().strip():
        text.from_string(TEMPLATE)
    if with_plane:
        idx = max(len(sc.p5b_instances) - 1, 0)
        plane = window.create_window_plane(name=f"P5B_Window_{name}", size=2.0,
                                           location=(2.5 * idx, 0.0, 0.0))
        rec.plane = plane.name
    return rec


def instance_start(rec, width: int | None = None, height: int | None = None) -> bool:
    sc = bpy.context.scene
    st = _state
    rt = _runtime(rec.name)
    if rt is not None and rt["server"] is not None and rt["server"].running:
        return True
    release_stale_port(int(rec.port))
    srv = transport.FrameServer(port=int(rec.port))
    if not srv.start():
        st["error"] = srv.error or "server failed to start"
        return False
    rt = _new_runtime(rec.name)
    rt["server"] = srv
    rt["live"] = live_image.LiveImage(rec.image or f"P5B_live_{rec.name}",
                                      int(width or rec.width or sc.p5b_width),
                                      int(height or rec.height or sc.p5b_height))
    text = bpy.data.texts.get(rec.text)
    if text is not None:
        rt["sketch_version"] += 1
        srv.publish_sketch(text.as_string(), rt["sketch_version"])
    plane = bpy.data.objects.get(rec.plane) if rec.plane else None
    if plane is not None:
        window.bind_window(plane, rt["live"].image, rec.material or f"P5B_Canvas_{rec.name}")
    st["instances"][rec.name] = rt
    st["error"] = ""
    _ensure_pump()
    return True


def instance_stop(rec) -> None:
    rt = _state["instances"].pop(rec.name, None)
    if rt is not None and rt["server"] is not None:
        rt["server"].stop()


def instance_push(rec) -> int:
    rt = _runtime(rec.name)
    if rt is None:
        return 0
    text = bpy.data.texts.get(rec.text)
    if text is None:
        _state["error"] = f"sketch text '{rec.text}' not found"
        return 0
    rt["sketch_version"] += 1
    if rt["server"] is not None:
        rt["server"].publish_sketch(text.as_string(), rt["sketch_version"])
    return rt["sketch_version"]


def build_geometry(scene, plane=None, img_size=None) -> list:
    """Meshes marked with p5b_geo, projected onto the canvas plane.

    Canvas-space like everything else: the canvas is the frame, the camera is
    irrelevant. A mesh above the canvas reads like a shadow/projection on it.
    """
    frame = canvas_frame(plane, img_size)
    if frame is None:
        return []
    out = []
    for ob in scene.objects:
        if ob.type != "MESH" or not ob.get("p5b_geo"):
            continue
        me = ob.data
        mw = ob.matrix_world
        verts = []
        for v in me.vertices:
            u, vv, _ = project_to_canvas(mw @ v.co, frame)
            verts.append([round(float(u), 4), round(float(vv), 4)])
            if len(verts) >= 2000:
                break
        edges = [[int(e.vertices[0]), int(e.vertices[1])] for e in me.edges][:4000]
        out.append({"name": ob.name, "verts": verts, "edges": edges})
    return out


def image_to_rgba8(img):
    """Blender image -> top-down RGBA8 bytes (what ImageData wants)."""
    w, h = img.size
    if not w or not h:
        return None
    src = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(src)
    arr = (np.clip(src, 0.0, 1.0) * 255.0).astype(np.uint8).reshape(h, w, 4)
    return w, h, np.ascontiguousarray(arr[::-1]).tobytes()


def _pump():
    st = _state
    scene = bpy.context.scene
    now = time.perf_counter()
    pushed_total = 0

    for rt in list(st["instances"].values()):
        srv = rt["server"]
        if srv is None:
            continue
        pushed = 0
        while pushed < 3:
            try:
                seq, w, h, planes = srv.frames.get_nowait()
            except queue.Empty:
                break
            try:
                if rt["live"] is None:
                    break
                rt["live"].push_planes(planes, w, h)
            except Exception as exc:
                rt["error"] = f"push failed: {exc}"
                break
            rt["last_seq"] = seq
            rt["last_size"] = (w, h)
            pushed += 1

        # optional upstream texture (feedback painting): a Blender image -> browser
        rec = instance_record(rt["name"])
        if rec is not None and rec.texture and srv.connected:
            if now - rt.get("tex_t", 0.0) >= 0.1:
                img = bpy.data.images.get(rec.texture)
                if img is not None:
                    packed = image_to_rgba8(img)
                    if packed is not None:
                        srv.publish_texture(*packed)
                        rt["tex_t"] = now

        # auto-run: push a sketch again once its text stops changing
        if rec is not None and getattr(scene, "p5b_auto_run", True):
            if now - rt.get("ar_t", 0.0) >= 0.12:
                rt["ar_t"] = now
                text = bpy.data.texts.get(rec.text)
                if text is not None:
                    # 外部编辑器改了磁盘文件？读回来再推送
                    if text.filepath:
                        try:
                            mtime = os.path.getmtime(bpy.path.abspath(text.filepath))
                        except OSError:
                            mtime = 0.0
                        if mtime and mtime > rt.get("file_mtime", 0.0):
                            rt["file_mtime"] = mtime
                            try:
                                with open(bpy.path.abspath(text.filepath), encoding="utf-8") as fh:
                                    text.from_string(fh.read())
                            except OSError:
                                pass
                            instance_push(rec)
                            rt["src_hash"] = hash(text.as_string())
                            continue
                    digest = hash(text.as_string())
                    if rt.get("src_hash") is None:
                        rt["src_hash"] = digest
                    elif digest != rt["src_hash"]:
                        if rt.get("pending_hash") == digest:
                            rt["src_hash"] = digest
                            instance_push(rec)
                        else:
                            rt["pending_hash"] = digest
        if pushed:
            rt["frames"] += pushed
            rt["n"] += pushed
            if rt["t0"] == 0.0:
                rt["t0"] = now
            dt = now - rt["t0"]
            if dt >= 0.5:
                rt["fps"] = rt["n"] / dt
                rt["n"] = 0
                rt["t0"] = now
            pushed_total += pushed

    if pushed_total:
        _tag_redraw()

    try:
        rebuild_geo = (now - st.get("geo_t", 0.0) >= 0.2)
        if rebuild_geo:
            st["geo_t"] = now
        for rt in st["instances"].values():
            srv = rt["server"]
            if srv is None:
                continue
            rec = instance_record(rt["name"])
            plane = canvas_plane_of(rec)
            img_size = live_size_of(rec) if rec is not None else None
            # 图像尺寸变了（sketch 自己 resizeCanvas）：重算 letterbox
            if rec is not None and img_size and rt.get("aspect_size") != img_size:
                rt["aspect_size"] = img_size
                mat = bpy.data.materials.get(rec.material or "")
                if mat is not None and rt["live"] is not None:
                    window.apply_aspect(mat, rt["live"].image)
            state = build_state(scene, plane, img_size)
            if rebuild_geo:
                rt["geo"] = build_geometry(scene, plane, img_size)
            state["geo"] = rt.get("geo", [])
            if rec is not None and rec.name == _state.get("active"):
                st["state"] = state
            w = int((rec.width if rec else 0) or scene.p5b_width)
            h = int((rec.height if rec else 0) or scene.p5b_height)
            srv.publish_state(json.dumps({**state, "w": w, "h": h}))
    except Exception as exc:
        st["error"] = f"state failed: {exc}"

    if not st["instances"]:
        return None
    return 0.0 if pushed_total else 0.004


def release_stale_port(port: int) -> int:
    """Close sockets in this process still bound to `port`.

    Reloading the addon during development can orphan a listener: its accept
    thread keeps the socket object alive, and every later bind silently fails
    or, worse, a duplicate listener steals connections.
    """
    import gc
    import socket as _socket

    closed = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, _socket.socket):
                addr = obj.getsockname()
                if isinstance(addr, tuple) and len(addr) >= 2 and addr[1] == port:
                    obj.close()
                    closed += 1
        except Exception:
            pass
    return closed


def ensure_default_instance():
    """The demo scene ships with P5B_live / p5b_sketch.js already; adopt them."""
    sc = bpy.context.scene
    if len(sc.p5b_instances):
        return active_record()
    rec = sc.p5b_instances.add()
    rec.name = "canvas"
    rec.port = int(getattr(sc, "p5b_port", DEFAULT_PORT))
    rec.text = SKETCH_NAME
    rec.image = "P5B_live"
    rec.material = "P5B_Canvas"
    rec.plane = "P5B_Window"
    if bpy.data.texts.get(rec.text) is None:
        bpy.data.texts.new(rec.text).from_string(TEMPLATE)
    return rec


def start_bridge(port: int = DEFAULT_PORT, width: int = 640, height: int = 360, host: str = "127.0.0.1"):
    """Start the active instance (creating a default record if needed)."""
    rec = ensure_default_instance()
    if rec is None:
        _state["error"] = "no instance"
        return False
    ok = instance_start(rec, width, height)
    if ok:
        _state["active"] = rec.name
    return ok


def stop_bridge():
    for rt in list(_state["instances"].values()):
        if rt["server"] is not None:
            rt["server"].stop()
    _state["instances"].clear()
    close_host()
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)


def push_sketch() -> int:
    rec = active_record()
    if rec is None:
        return 0
    return instance_push(rec)


def stats_snapshot():
    st = _state
    rec = active_record()
    rt = _runtime(rec.name) if rec is not None else None
    srv = rt["server"] if rt else None
    rows = []
    for r in instance_records():
        rrt = _runtime(r.name)
        rsrv = rrt["server"] if rrt else None
        rows.append({
            "name": r.name,
            "port": int(r.port),
            "running": bool(rsrv and rsrv.running),
            "connected": bool(rsrv and rsrv.connected),
            "fps": round(rrt["fps"], 1) if rrt else 0.0,
        })
    return {
        "running": bool(srv and srv.running),
        "connected": bool(srv and srv.connected),
        "protocol": (srv.stats["protocol"] if srv else None),
        "port": (int(rec.port) if rec is not None else None),
        "frames": (rt["frames"] if rt else 0),
        "fps": round(rt["fps"], 2) if rt else 0.0,
        "dropped": (srv.stats["dropped"] if srv else 0),
        "cooks": (srv.stats["cooks"] if srv else 0),
        "silent_for": (srv.silent_for if srv else 0.0),
        "clients": (srv.stats["clients"] if srv else 0),
        "host": host_status(),
        "sketch_version": (rt["sketch_version"] if rt else 0),
        "tracked": len((st["state"] or {}).get("objects", [])),
        "visible": sum(1 for o in (st["state"] or {}).get("objects", []) if o.get("visible", True)),
        "client_p5": (srv.stats.get("client_p5") if srv else None),
        "textures": (srv.stats.get("textures", 0) if srv else 0),
        "tex_acks": (srv.stats.get("tex_acks", 0) if srv else 0),
        "instance": (rec.name if rec is not None else None),
        "instances": rows,
        "error": st["error"] or (srv.error if srv else ""),
    }


def cook_frame(frame: int | None = None, timeout: float = 5.0) -> int:
    """Ask the active sketch for one specific frame and wait for it (blocking).

    This is the pull-based half of the model: the browser renders exactly on
    request, so a frame is a pure function of (sketch version, frame, state)
    and repeating it gives the same pixels. Returns the frame cooked, or -1.
    """
    st = _state
    rec = active_record()
    rt = _runtime(rec.name) if rec is not None else None
    if rt is None or rt["server"] is None or not rt["server"].connected or rt["live"] is None:
        return -1
    srv = rt["server"]
    scene = bpy.context.scene
    target = scene.frame_current if frame is None else int(frame)
    scene.frame_set(target)
    w = int((rec.width if rec else 0) or scene.p5b_width)
    h = int((rec.height if rec else 0) or scene.p5b_height)
    state = build_state(scene, canvas_plane_of(rec), live_size_of(rec))
    state["geo"] = build_geometry(scene, canvas_plane_of(rec), live_size_of(rec))
    state["w"] = w
    state["h"] = h
    fps = max(scene.render.fps, 1)
    srv.clear_results()
    cid = srv.request_cook(target, target / fps, state, w, h)
    deadline = time.perf_counter() + timeout
    result = None
    while time.perf_counter() < deadline:
        result = srv.pop_result(cid)
        if result is not None:
            break
        time.sleep(0.002)
    if result is None:
        st["error"] = f"cook timeout for frame {target}"
        return -1
    w, h, planes = result
    rt["live"].push_planes(planes, w, h)
    st["state"] = state
    _tag_redraw()
    return target

# --------------------------------------------------------------------- operators
class P5B_OT_start(bpy.types.Operator):
    bl_idname = "p5b.start"
    bl_label = "Start"
    bl_description = "Start the receiver, push the sketch and run the canvas in the background"

    def execute(self, context):
        sc = context.scene
        port = int(sc.p5b_port)
        if not start_bridge(port, int(sc.p5b_width), int(sc.p5b_height)):
            self.report({"ERROR"}, _state["error"] or "failed to start")
            return {"CANCELLED"}
        push_sketch()
        if not host_status()["alive"]:
            launch_host("headless", port)
        self.report({"INFO"}, f"p5inBlender running in background on 127.0.0.1:{port}")
        return {"FINISHED"}


class P5B_OT_stop(bpy.types.Operator):
    bl_idname = "p5b.stop"
    bl_label = "Stop Receiver"

    def execute(self, context):
        stop_bridge()
        return {"FINISHED"}


class P5B_OT_new_sketch(bpy.types.Operator):
    bl_idname = "p5b.new_sketch"
    bl_label = "Edit Sketch"
    bl_description = "Open this instance's sketch text in a Text Editor (creating it if needed)"

    def execute(self, context):
        sc = context.scene
        ensure_default_instance()
        rec = active_record()
        if rec is None:
            self.report({"ERROR"}, "no instance")
            return {"CANCELLED"}
        text = bpy.data.texts.get(rec.text)
        if text is None:
            text = bpy.data.texts.new(rec.text)
            text.from_string(TEMPLATE)
        # show it in an existing text editor, or turn a node editor into one
        shown = False
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == "TEXT_EDITOR":
                        area.spaces.active.text = text
                        area.spaces.active.show_syntax_highlight = True
                        shown = True
        if not shown:
            for wm in bpy.data.window_managers:
                for win in wm.windows:
                    candidates = [a for a in win.screen.areas if a.type == "NODE_EDITOR"]
                    if candidates:
                        area = max(candidates, key=lambda a: a.width)
                        area.type = "TEXT_EDITOR"
                        area.spaces.active.text = text
                        shown = True
        self.report({"INFO"}, f"editing {rec.name} → {rec.text}")
        return {"FINISHED"}


class P5B_OT_reset_sketch(bpy.types.Operator):
    bl_idname = "p5b.reset_sketch"
    bl_label = "Reset to Demo"
    bl_description = "Overwrite this instance's sketch with the built-in demo and run it"

    def execute(self, context):
        rec = active_record()
        if rec is None:
            self.report({"ERROR"}, "no instance")
            return {"CANCELLED"}
        text = bpy.data.texts.get(rec.text) or bpy.data.texts.new(rec.text)
        text.from_string(TEMPLATE)
        instance_push(rec)
        self.report({"INFO"}, f"reset {rec.text}")
        return {"FINISHED"}


def _write_authoring_bundle(folder: str) -> None:
    """给外置编辑器准备 JS 语言支持（插件自带，不覆盖用户已有配置）。"""
    vscode = os.path.join(folder, ".vscode")
    os.makedirs(vscode, exist_ok=True)
    settings = os.path.join(vscode, "settings.json")
    if not os.path.exists(settings):
        with open(settings, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "{\n"
                '  // p5inBlender: JS 编辑体验\n'
                '  "files.associations": { "*.js": "javascript" },\n'
                '  "editor.tabSize": 2,\n'
                '  "editor.quickSuggestions": { "other": true, "comments": false, "strings": false },\n'
                '  "editor.formatOnSave": false,\n'
                '  "javascript.suggest.autoImports": true\n'
                "}\n"
            )
    jsconfig = os.path.join(folder, "jsconfig.json")
    if not os.path.exists(jsconfig):
        with open(jsconfig, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "{\n"
                '  "compilerOptions": {\n'
                '    "target": "ES2020",\n'
                '    "lib": ["DOM", "ES2020"],\n'
                '    "checkJs": false\n'
                "  },\n"
                '  "include": ["*.js"]\n'
                "}\n"
            )
    readme = os.path.join(folder, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                "p5inBlender 外置编辑目录\n"
                "=========================\n\n"
                "这些 .js 文件就是 Blender Text datablock 的镜像：\n"
                "  - 在编辑器里改并保存 → 插件 0.3 秒内读回 Blender 并推给画布\n"
                "  - Blender 里改了再点 Edit Externally 会重新导出\n\n"
                "VS Code 建议装扩展：\n"
                "  - ESLint（可选）\n"
                "  - p5.vscode（自动补全 p5 API，作者 Sam Lavigne）\n\n"
                "注意：Blender 内置文本编辑器只有 Python 着色规则，想要真正的 JS 高亮就用这里。\n"
            )


class P5B_OT_external_edit(bpy.types.Operator):
    bl_idname = "p5b.external_edit"
    bl_label = "Edit Externally"
    bl_description = "Save the sketch to disk, open it in your OS editor (real JS highlighting), auto-sync on save"

    def execute(self, context):
        rec = active_record()
        if rec is None:
            self.report({"ERROR"}, "no instance")
            return {"CANCELLED"}
        text = bpy.data.texts.get(rec.text)
        if text is None:
            self.report({"ERROR"}, f"text '{rec.text}' not found")
            return {"CANCELLED"}
        if text.filepath:
            path = bpy.path.abspath(text.filepath)
        else:
            base = bpy.path.abspath("//") if bpy.data.filepath else tempfile.gettempdir()
            folder = os.path.join(base, "p5inblender_sketches")
            os.makedirs(folder, exist_ok=True)
            name = rec.text if rec.text.lower().endswith((".js", ".py", ".txt")) else rec.text + ".js"
            path = os.path.join(folder, name)
            text.filepath = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.as_string())
        _write_authoring_bundle(os.path.dirname(path))
        try:
            _state["instances"].get(rec.name, {})["file_mtime"] = os.path.getmtime(path)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self.report({"ERROR"}, f"could not open editor: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"editing {path} — save the file to sync")
        return {"FINISHED"}


class P5B_OT_run_sketch(bpy.types.Operator):
    bl_idname = "p5b.run_sketch"
    bl_label = "Run"
    bl_description = "Push the sketch to the browser and make sure the receiver is running"

    def execute(self, context):
        sc = context.scene
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["server"] is None or not rt["server"].running:
            if not start_bridge(0, int(sc.p5b_width), int(sc.p5b_height)):
                self.report({"ERROR"}, _state["error"] or "failed to start receiver")
                return {"CANCELLED"}
            rec = active_record()
            rt = _runtime(rec.name) if rec is not None else None
        ver = push_sketch()
        if ver == 0:
            self.report({"ERROR"}, _state["error"] or "no sketch text")
            return {"CANCELLED"}
        # 没有画布客户端就顺手开一个后台的（用户点 Run 的预期是"立刻看到"）
        if not host_status()["alive"] and rec is not None:
            launch_host("headless", int(rec.port))
        connected = bool(rt and rt["server"] and rt["server"].connected)
        self.report({"INFO"}, f"sketch v{ver} on {(rec.name if rec else '?')} "
                              f"({'live' if connected else 'waiting for browser'})")
        return {"FINISHED"}


class P5B_OT_create_window(bpy.types.Operator):
    bl_idname = "p5b.create_window"
    bl_label = "Create UV Window"
    bl_description = "Create a UV plane whose texture shows the live canvas"

    def execute(self, context):
        st = _state
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["live"] is None:
            if not start_bridge(0, int(context.scene.p5b_width), int(context.scene.p5b_height)):
                self.report({"ERROR"}, st["error"])
                return {"CANCELLED"}
            rt = _runtime(active_record().name)
        ob = window.create_window_plane(size=2.0)
        window.bind_window(ob, rt["live"].image, rec.material or "P5B_Canvas")
        rec.plane = ob.name
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {"FINISHED"}


class P5B_OT_standardize_plane(bpy.types.Operator):
    bl_idname = "p5b.standardize_plane"
    bl_label = "Standardize Canvas"
    bl_description = "Put the canvas back to the standard: 2x2 square, flat on XY at the origin, no rotation"

    def execute(self, context):
        rec = active_record()
        plane = canvas_plane_of(rec) if rec is not None else None
        if plane is None:
            self.report({"ERROR"}, "no canvas plane for this instance")
            return {"CANCELLED"}
        plane.location = (0.0, 0.0, 0.0)
        plane.rotation_euler = (0.0, 0.0, 0.0)
        plane.scale = (1.0, 1.0, 1.0)
        # 网格本身也拉回 2x2（原位顶点，顶点数据不动 uv）
        me = plane.data
        if len(me.vertices) >= 4:
            plane.data.vertices.foreach_set("co", [
                -1.0, -1.0, 0.0,
                 1.0, -1.0, 0.0,
                 1.0,  1.0, 0.0,
                -1.0,  1.0, 0.0,
            ])
            me.update()
        self.report({"INFO"}, f"{plane.name} is 2x2 on XY at the origin")
        return {"FINISHED"}


class P5B_OT_show_canvas(bpy.types.Operator):
    bl_idname = "p5b.show_canvas"
    bl_label = "Show Live Canvas"

    def execute(self, context):
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["live"] is None:
            self.report({"ERROR"}, "start the receiver first")
            return {"CANCELLED"}
        area = context.area
        if area is None:
            self.report({"ERROR"}, "no active area")
            return {"CANCELLED"}
        area.type = "IMAGE_EDITOR"
        space = area.spaces.active
        space.image = rt["live"].image
        space.ui_mode = "VIEW"
        return {"FINISHED"}


class P5B_OT_reset_material(bpy.types.Operator):
    bl_idname = "p5b.reset_material"
    bl_label = "Reset Material"
    bl_description = "Rebuild the canvas material with the default node setup (overwrites manual edits)"

    def execute(self, context):
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["live"] is None:
            self.report({"ERROR"}, "start the instance first")
            return {"CANCELLED"}
        name = (rec.material or f"P5B_Canvas_{rec.name}")
        # 就地重建节点（不删数据块，避免把正在用它的物体留空槽）
        mat = window.build_material(rt["live"].image, name, force=True)
        plane = bpy.data.objects.get(rec.plane) if rec.plane else None
        if plane is not None:
            if not plane.data.materials:
                plane.data.materials.append(mat)
            else:
                plane.data.materials[0] = mat
        self.report({"INFO"}, f"rebuilt {mat.name}")
        return {"FINISHED"}


class P5B_OT_feedback(bpy.types.Operator):
    bl_idname = "p5b.feedback"
    bl_label = "Use Own Canvas"
    bl_description = "Feed the active instance's canvas back to its own sketch (feedback painting)"

    def execute(self, context):
        rec = active_record()
        if rec is None:
            self.report({"ERROR"}, "no instance")
            return {"CANCELLED"}
        rec.texture = rec.image
        self.report({"INFO"}, f"{rec.texture} -> {rec.name}")
        return {"FINISHED"}


class P5B_OT_bind_plane(bpy.types.Operator):
    bl_idname = "p5b.bind_plane"
    bl_label = "Bind Selected to Instance"
    bl_description = "Give the selected meshes the active instance's canvas material"

    def execute(self, context):
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["live"] is None:
            self.report({"ERROR"}, "start the instance first")
            return {"CANCELLED"}
        targets = [o for o in context.selected_objects if o.type == "MESH"]
        if not targets:
            self.report({"ERROR"}, "select a mesh object")
            return {"CANCELLED"}
        mat = window.build_material(rt["live"].image, rec.material or f"P5B_Canvas_{rec.name}")
        for ob in targets:
            data = ob.data
            if not data.uv_layers:
                data.uv_layers.new(name=window.UV_NAME)
            if not data.materials:
                data.materials.append(mat)
            else:
                data.materials[0] = mat
        self.report({"INFO"}, f"bound {len(targets)} mesh(es) to {rec.name}")
        return {"FINISHED"}


class P5B_OT_mark_geo(bpy.types.Operator):
    bl_idname = "p5b.mark_geo"
    bl_label = "Send Geometry"
    bl_description = "Send the selected meshes' projected wireframe to the sketch"

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if ob.type == "MESH":
                ob["p5b_geo"] = True
                n += 1
        self.report({"INFO"}, f"{n} mesh(es) marked")
        return {"FINISHED"}


class P5B_OT_unmark_geo(bpy.types.Operator):
    bl_idname = "p5b.unmark_geo"
    bl_label = "Stop Geometry"

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if "p5b_geo" in ob:
                del ob["p5b_geo"]
                n += 1
        self.report({"INFO"}, f"{n} mesh(es) unmarked")
        return {"FINISHED"}


class P5B_OT_track(bpy.types.Operator):
    bl_idname = "p5b.track"
    bl_label = "Track Selected"
    bl_description = "Send the selected objects' screen position to the sketch every frame"

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            ob["p5b_track"] = True
            n += 1
        self.report({"INFO"}, f"tracking {n} object(s)")
        return {"FINISHED"}


class P5B_OT_untrack(bpy.types.Operator):
    bl_idname = "p5b.untrack"
    bl_label = "Untrack Selected"

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if "p5b_track" in ob:
                del ob["p5b_track"]
                n += 1
        self.report({"INFO"}, f"untracked {n} object(s)")
        return {"FINISHED"}


class P5B_OT_open_host(bpy.types.Operator):
    bl_idname = "p5b.open_host"
    bl_label = "Open Canvas"
    bl_description = "Start the sketch host browser (window or background headless)"

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=(
            ("window", "Window", "A small app-mode browser window you can interact with"),
            ("headless", "Background", "No window at all; runs silently and streams frames"),
        ),
        default="window",
    )

    def execute(self, context):
        sc = context.scene
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        if rt is None or rt["server"] is None or not rt["server"].running:
            if not start_bridge(0, int(sc.p5b_width), int(sc.p5b_height)):
                self.report({"ERROR"}, _state["error"])
                return {"CANCELLED"}
        rec = active_record()
        port = int(rec.port) if rec is not None else int(sc.p5b_port)
        proc = launch_host(self.mode, port)
        if proc is None:
            self.report({"ERROR"}, _state["error"])
            return {"CANCELLED"}
        self.report({"INFO"}, f"host started ({self.mode}, pid {proc.pid})")
        return {"FINISHED"}


class P5B_OT_close_host(bpy.types.Operator):
    bl_idname = "p5b.close_host"
    bl_label = "Close Canvas"
    bl_description = "Stop the sketch host browser"

    def execute(self, context):
        closed = close_host()
        self.report({"INFO"}, "host closed" if closed else "no host running")
        return {"FINISHED"}


class P5B_OT_render_animation(bpy.types.Operator):
    bl_idname = "p5b.render_animation"
    bl_label = "Render Animation"
    bl_description = "Cook every frame from the sketch, then render it (deterministic sequence)"

    def execute(self, context):
        sc = context.scene
        rec = active_record()
        rt = _runtime(rec.name) if rec is not None else None
        srv = rt["server"] if rt else None
        if srv is None or not srv.connected:
            self.report({"ERROR"}, "host not connected — start the receiver and the canvas first")
            return {"CANCELLED"}
        start, end, step = sc.frame_start, sc.frame_end, max(sc.frame_step, 1)
        frames = list(range(start, end + 1, step))
        if bpy.data.filepath:
            out_dir = bpy.path.abspath("//p5b_anim")
        else:
            out_dir = os.path.join(tempfile.gettempdir(), "p5bridge_anim")
        os.makedirs(out_dir, exist_ok=True)
        wm = context.window_manager
        wm.progress_begin(0, len(frames))
        done = 0
        try:
            for i, frame in enumerate(frames):
                if cook_frame(frame) < 0:
                    self.report({"ERROR"}, f"cook failed at frame {frame}: {_state['error']}")
                    return {"CANCELLED"}
                sc.render.filepath = os.path.join(out_dir, f"{frame:04d}.png")
                bpy.ops.render.render(write_still=True)
                wm.progress_update(i + 1)
                done += 1
        finally:
            wm.progress_end()
        self.report({"INFO"}, f"rendered {done} frames -> {out_dir}")
        return {"FINISHED"}


class P5B_OT_cook_still(bpy.types.Operator):
    bl_idname = "p5b.cook_still"
    bl_label = "Cook Frame"
    bl_description = "Ask the sketch for this exact frame, then render it (deterministic)"

    def execute(self, context):
        sc = context.scene
        frame = cook_frame(sc.frame_current)
        if frame < 0:
            self.report({"ERROR"}, _state["error"] or "cook failed")
            return {"CANCELLED"}
        self.report({"INFO"}, f"cooked frame {frame}")
        return {"FINISHED"}


class P5B_OT_cook_render(bpy.types.Operator):
    bl_idname = "p5b.cook_render"
    bl_label = "Cook + Render"
    bl_description = "Cook this frame then write a still next to the blend file"

    def execute(self, context):
        sc = context.scene
        if cook_frame(sc.frame_current) < 0:
            self.report({"ERROR"}, _state["error"] or "cook failed")
            return {"CANCELLED"}
        sc.render.filepath = bpy.path.abspath("//p5b_frame_")
        bpy.ops.render.render(write_still=True)
        self.report({"INFO"}, "rendered via cook")
        return {"FINISHED"}


class P5B_Instance(bpy.types.PropertyGroup):
    """Persistent record of one canvas: where its sketch, image and plane live."""

    port: bpy.props.IntProperty(name="Port", default=DEFAULT_PORT, min=1024, max=65535)
    width: bpy.props.IntProperty(name="Width", default=0, min=0, max=4096)
    height: bpy.props.IntProperty(name="Height", default=0, min=0, max=4096)
    text: bpy.props.StringProperty(name="Sketch", default="")
    image: bpy.props.StringProperty(name="Image", default="")
    material: bpy.props.StringProperty(name="Material", default="")
    plane: bpy.props.StringProperty(name="Plane", default="")
    texture: bpy.props.StringProperty(name="Texture", default="")


class P5B_OT_instance_new(bpy.types.Operator):
    bl_idname = "p5b.instance_new"
    bl_label = "New Instance"
    bl_description = "Create an independent canvas (own sketch, image, material, plane)"

    def execute(self, context):
        sc = context.scene
        base = f"canvas{len(sc.p5b_instances) + 1}"
        name, i = base, 2
        while instance_record(name) is not None:
            name = f"{base}{i}"
            i += 1
        rec = create_instance(name)
        ok = instance_start(rec)
        sc.p5b_active = len(sc.p5b_instances) - 1
        _state["active"] = rec.name
        if ok:
            self.report({"INFO"}, f"{name} on port {int(rec.port)}")
        else:
            self.report({"ERROR"}, _state["error"] or "start failed")
        return {"FINISHED"} if ok else {"CANCELLED"}


class P5B_OT_instance_activate(bpy.types.Operator):
    bl_idname = "p5b.instance_activate"
    bl_label = "Activate Instance"

    index: bpy.props.IntProperty()

    def execute(self, context):
        sc = context.scene
        if 0 <= self.index < len(sc.p5b_instances):
            sc.p5b_active = self.index
            _state["active"] = sc.p5b_instances[self.index].name
        return {"FINISHED"}


class P5B_OT_instance_start(bpy.types.Operator):
    bl_idname = "p5b.instance_start"
    bl_label = "Start Instance"

    name: bpy.props.StringProperty()

    def execute(self, context):
        rec = instance_record(self.name) or active_record()
        if rec is None:
            return {"CANCELLED"}
        ok = instance_start(rec)
        if ok:
            _state["active"] = rec.name
        self.report({"INFO"} if ok else {"ERROR"}, f"{rec.name} : {int(rec.port)}")
        return {"FINISHED"} if ok else {"CANCELLED"}


class P5B_OT_instance_stop(bpy.types.Operator):
    bl_idname = "p5b.instance_stop"
    bl_label = "Stop Instance"

    name: bpy.props.StringProperty()

    def execute(self, context):
        rec = instance_record(self.name) or active_record()
        if rec is not None:
            instance_stop(rec)
        return {"FINISHED"}


class P5B_OT_instance_remove(bpy.types.Operator):
    bl_idname = "p5b.instance_remove"
    bl_label = "Remove Instance"

    name: bpy.props.StringProperty()

    def execute(self, context):
        sc = context.scene
        rec = instance_record(self.name) or active_record()
        if rec is None:
            return {"CANCELLED"}
        instance_stop(rec)
        target = rec.name
        for i in range(len(sc.p5b_instances)):
            if sc.p5b_instances[i].name == target:
                sc.p5b_instances.remove(i)
                break
        sc.p5b_active = max(0, min(sc.p5b_active, len(sc.p5b_instances) - 1))
        return {"FINISHED"}


# -------------------------------------------------------------------------- UI
def _draw_status_and_start(layout):
    st = stats_snapshot()
    if st["connected"] and st["fps"] > 0.5:
        text, icon = f"live — {st['fps']} fps", "CHECKMARK"
    elif st["connected"]:
        text, icon = "connected, waiting for frames", "TIME"
    elif st["running"]:
        text, icon = "listening, no canvas", "SHADING_WIRE"
    else:
        text, icon = "stopped", "PAUSE"

    col = layout.column(align=True)
    col.label(text=text, icon=icon)
    if st["error"]:
        col.label(text=st["error"][:64], icon=_icon("ERROR"))

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("p5b.start", text="Start", icon=_icon("PLAY"))
    row.operator("p5b.stop", text="Stop", icon=_icon("SNAP_FACE"))


class P5B_MT_menu(bpy.types.Menu):
    """Always-visible entry point: 3D View header → p5inBlender."""

    bl_idname = "P5B_MT_menu"
    bl_label = "p5inBlender"

    def draw(self, context):
        layout = self.layout
        st = stats_snapshot()
        hs = st["host"]
        layout.label(text=f"{'live' if st['connected'] else 'stopped'} · {st['fps']} fps · host {hs['mode'] or 'off'}")
        layout.separator()
        layout.operator("p5b.start", text="Start (background)", icon=_icon("PLAY"))
        layout.operator("p5b.stop", text="Stop", icon=_icon("SNAP_FACE"))
        layout.separator()
        layout.operator("p5b.new_sketch", text="Edit Sketch", icon=_icon("TEXT"))
        layout.operator("p5b.run_sketch", text="Run Sketch", icon=_icon("SCRIPTPLUGINS"))
        layout.separator()
        layout.operator("p5b.show_canvas", text="Show Live Canvas", icon=_icon("IMAGE_DATA"))


class P5B_PT_panel(bpy.types.Panel):
    bl_label = "p5inBlender"
    bl_idname = "P5B_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        _draw_status_and_start(self.layout)


class P5B_PT_canvas(bpy.types.Panel):
    bl_label = "Canvas"
    bl_idname = "P5B_PT_canvas"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        sc = context.scene
        st = stats_snapshot()
        hs = st["host"]
        layout = self.layout

        row = layout.row(align=True)
        row.operator("p5b.show_canvas", text="Show", icon=_icon("IMAGE_DATA"))
        row.operator("p5b.create_window", text="Window Plane", icon=_icon("MESH_PLANE"))
        row = layout.row(align=True)
        row.operator("p5b.bind_plane", text="Bind Mesh", icon=_icon("LINKED"))
        row.operator("p5b.reset_material", text="Reset Material", icon=_icon("FILE_REFRESH"))
        layout.operator("p5b.standardize_plane", text="Standardize Canvas (2x2 on XY)", icon=_icon("MESH_GRID"))
        layout.prop(context.scene, "p5b_show_hud", text="Show Live Info")
        rec = active_record()
        if rec is not None:
            layout.prop_search(rec, "texture", bpy.data, "images", text="Texture")
            row = layout.row(align=True)
            row.operator("p5b.feedback", text="Feedback", icon=_icon("LOOP_BACK"))

        row = layout.row(align=True)
        op = row.operator("p5b.open_host", text="Open", icon=_icon("WINDOW"))
        op.mode = "window"
        op = row.operator("p5b.open_host", text="Background", icon=_icon("HIDE_ON"))
        op.mode = "headless"
        row.operator("p5b.close_host", text="", icon=_icon("X"))
        layout.label(text=f"host: {hs['mode'] or 'off'}" + (f" (pid {hs['pid']})" if hs["pid"] else ""))

        row = layout.row(align=True)
        row.prop(sc, "p5b_port", text="")
        row.prop(sc, "p5b_width", text="W")
        row.prop(sc, "p5b_height", text="H")
        layout.label(text=f"127.0.0.1:{st['port'] or sc.p5b_port}", icon=_icon("URL"))


class P5B_PT_sketch(bpy.types.Panel):
    bl_label = "Sketch"
    bl_idname = "P5B_PT_sketch"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        st = stats_snapshot()
        rec = active_record()
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("p5b.run_sketch", text="Run", icon=_icon("PLAY"))
        row.operator("p5b.new_sketch", text="Edit", icon=_icon("TEXT"))
        if rec is not None:
            layout.prop_search(rec, "text", bpy.data, "texts", text="Sketch")
            if bpy.data.texts.get(rec.text) is None:
                layout.label(text=f"missing text: {rec.text}", icon=_icon("ERROR"))
        row = layout.row(align=True)
        row.operator("p5b.external_edit", text="Edit Externally", icon=_icon("TEXT"))
        row.operator("p5b.reset_sketch", text="Reset Demo", icon=_icon("FILE_REFRESH"))
        layout.prop(context.scene, "p5b_auto_run", text="Auto Run on Edit")
        layout.label(text=f"v{st['sketch_version']} · {st['frames']} frames · {st['dropped']} dropped")


class P5B_PT_tracking(bpy.types.Panel):
    bl_label = "Tracking"
    bl_idname = "P5B_PT_tracking"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        st = stats_snapshot()
        layout = self.layout
        row = layout.row(align=True)
        row.operator("p5b.track", text="Track Selected", icon=_icon("TRACKER"))
        row.operator("p5b.untrack", text="", icon=_icon("CANCEL"))
        row = layout.row(align=True)
        row.operator("p5b.mark_geo", text="Send Geometry", icon=_icon("MESH_DATA"))
        row.operator("p5b.unmark_geo", text="", icon=_icon("X"))
        objs = (st.get("state") or {}).get("objects") or []
        if st["tracked"]:
            layout.label(text=f"tracked {st['tracked']} · on screen {st['visible']}")
            if st["visible"] == 0:
                layout.label(text="tracked objects are off-screen (check the camera)", icon=_icon("ERROR"))
        else:
            layout.label(text="no tracked object")
        layout.label(text=f"{st['tracked']} object(s) sent to the sketch")


class P5B_PT_render(bpy.types.Panel):
    bl_label = "Render"
    bl_idname = "P5B_PT_render"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = stats_snapshot()
        layout = self.layout
        row = layout.row(align=True)
        row.operator("p5b.cook_still", text="Cook", icon=_icon("IMAGE_REFERENCE"))
        row.operator("p5b.cook_render", text="Cook + Render", icon=_icon("RENDER_STILL"))
        layout.operator("p5b.render_animation", icon=_icon("RENDER_ANIMATION"))
        layout.label(text=f"{st['cooks']} cook request(s)")


class P5B_PT_instances(bpy.types.Panel):
    bl_label = "Instances"
    bl_idname = "P5B_PT_instances"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        sc = context.scene
        st = stats_snapshot()
        layout = self.layout
        layout.operator("p5b.instance_new", text="New Instance", icon=_icon("ADD"))
        if not st["instances"]:
            layout.label(text="no instance yet — press Start")
        for i, row in enumerate(st["instances"]):
            line = layout.row(align=True)
            op = line.operator("p5b.instance_activate", text=row["name"], emboss=False,
                               icon=_icon("RADIOBUT_ON") if i == sc.p5b_active else "RADIOBUT_OFF")
            op.index = i
            line.label(text=f"{row['port']} · " + (f"{row['fps']}fps" if row["running"] else "off"))
            op = line.operator("p5b.instance_start", text="", icon=_icon("PLAY"))
            op.name = row["name"]
            op = line.operator("p5b.instance_stop", text="", icon=_icon("SNAP_FACE"))
            op.name = row["name"]
            op = line.operator("p5b.instance_remove", text="", icon=_icon("X"))
            op.name = row["name"]


class P5B_PT_text_sidebar(bpy.types.Panel):
    """Run button right next to the code."""

    bl_label = "p5inBlender"
    bl_idname = "P5B_PT_text_sidebar"
    bl_space_type = "TEXT_EDITOR"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        st = stats_snapshot()
        layout = self.layout
        layout.operator("p5b.run_sketch", text="Run", icon=_icon("PLAY"))
        layout.label(text=f"{'live' if st['connected'] else 'off'} · v{st['sketch_version']} · {st['fps']} fps")


classes = (
    P5B_Instance,
    P5B_OT_start, P5B_OT_stop, P5B_OT_new_sketch, P5B_OT_run_sketch, P5B_OT_reset_sketch,
    P5B_OT_external_edit,
    P5B_OT_open_host, P5B_OT_close_host,
    P5B_OT_create_window, P5B_OT_show_canvas, P5B_OT_track, P5B_OT_untrack,
    P5B_OT_bind_plane, P5B_OT_feedback, P5B_OT_mark_geo, P5B_OT_unmark_geo, P5B_OT_reset_material,
    P5B_OT_standardize_plane,
    P5B_OT_cook_still, P5B_OT_cook_render, P5B_OT_render_animation,
    P5B_OT_instance_new, P5B_OT_instance_activate, P5B_OT_instance_start,
    P5B_OT_instance_stop, P5B_OT_instance_remove,
    P5B_PT_panel, P5B_PT_canvas, P5B_PT_sketch, P5B_PT_tracking, P5B_PT_render,
    P5B_PT_instances, P5B_PT_text_sidebar, P5B_MT_menu,
)


def _on_load_post(*_args):
    """Default off: loading a file never leaves a listener or a browser behind."""
    stop_bridge()


def _draw_header_menu(self, context):
    self.layout.menu("P5B_MT_menu", text="p5inBlender")


def _guard_panel_draws(classes):
    """面板 draw 里任何异常都不该让整个侧栏闪烁/空白：捕获后画成一行红字。"""
    def wrap(fn):
        def safe(self, context):
            try:
                return fn(self, context)
            except Exception as exc:  # noqa: BLE001
                try:
                    self.layout.label(text=("panel error: " + str(exc))[:90], icon=_icon("ERROR"))
                except Exception:
                    pass
                return None
        return safe

    for cls in classes:
        fn = cls.__dict__.get("draw")
        if fn is None or not getattr(cls, "bl_space_type", None) and not getattr(cls, "bl_idname", "").startswith("P5B_MT"):
            continue
        if getattr(fn, "__p5b_guarded", False):
            continue
        wrapped = wrap(fn)
        wrapped.__p5b_guarded = True
        cls.draw = wrapped


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    _guard_panel_draws(classes)
    for prop, kwargs in (
        ("p5b_port", dict(name="Port", default=DEFAULT_PORT, min=1024, max=65535)),
        ("p5b_width", dict(name="W", default=640, min=16, max=4096)),
        ("p5b_height", dict(name="H", default=360, min=16, max=4096)),
    ):
        if not hasattr(bpy.types.Scene, prop):
            setattr(bpy.types.Scene, prop, bpy.props.IntProperty(**kwargs))
    if not hasattr(bpy.types.Scene, "p5b_instances"):
        bpy.types.Scene.p5b_instances = bpy.props.CollectionProperty(type=P5B_Instance)
    if not hasattr(bpy.types.Scene, "p5b_active"):
        bpy.types.Scene.p5b_active = bpy.props.IntProperty(name="Active", default=0)
    if not hasattr(bpy.types.Scene, "p5b_auto_run"):
        bpy.types.Scene.p5b_auto_run = bpy.props.BoolProperty(name="Auto Run", default=True)
    if not hasattr(bpy.types.Scene, "p5b_show_hud"):
        bpy.types.Scene.p5b_show_hud = bpy.props.BoolProperty(
            name="Show Live Info", default=True,
            description="Show the live readout (fps/objects/state) in the canvas; "
                        "sketches read it as p5inblender.hud")
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    try:
        bpy.types.VIEW3D_MT_editor_menus.remove(_draw_header_menu)
    except Exception:
        pass
    bpy.types.VIEW3D_MT_editor_menus.append(_draw_header_menu)


def unregister():
    stop_bridge()
    try:
        bpy.types.VIEW3D_MT_editor_menus.remove(_draw_header_menu)
    except Exception:
        pass
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    for prop in ("p5b_port", "p5b_width", "p5b_height", "p5b_instances", "p5b_active",
                 "p5b_auto_run", "p5b_show_hud"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
