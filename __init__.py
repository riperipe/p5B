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
import re
import subprocess
import sys
import tempfile
import time

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

from . import live_image, transport, window

bl_info = {
    "name": "p5inBlender",
    "author": "DeepseekWorkzone",
    "version": (0, 4, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > p5inBlender",
    "description": "Live 2D canvas as a UV-mapped texture, coded in Blender's text editor",
    "category": "Material",
}

DEFAULT_PORT = 9877
SKETCH_NAME = "p5b_sketch.js"
PARAM_ATTR = "p5b_params"
PARAM_LINE = re.compile(r"^\s*//\s*@param\s+([A-Za-z_]\w*)\s+(-?[\d.]+)(.*)$")


def parse_params(source: str) -> dict:
    """Read `// @param name default [min=..] [max=..]` lines from the sketch.

    Declaring the parameters inside the code keeps one source of truth: the
    sketch says what it needs, Blender builds the sliders from that.
    """
    out = {}
    for line in source.splitlines():
        match = PARAM_LINE.match(line)
        if not match:
            continue
        name, default, rest = match.group(1), float(match.group(2)), match.group(3)
        lo = re.search(r"min=(-?[\d.]+)", rest)
        hi = re.search(r"max=(-?[\d.]+)", rest)
        out[name] = {
            "default": default,
            "min": float(lo.group(1)) if lo else 0.0,
            "max": float(hi.group(1)) if hi else max(1.0, default * 2.0),
        }
    return out


def sketch_params_schema() -> dict:
    text = bpy.data.texts.get(SKETCH_NAME)
    return parse_params(text.as_string()) if text else {}


def sync_params(scene) -> dict:
    """Create missing properties from the schema; keep the values the user set."""
    schema = sketch_params_schema()
    if PARAM_ATTR not in scene:
        scene[PARAM_ATTR] = {}
    group = scene[PARAM_ATTR]
    for name, spec in schema.items():
        if name not in group:
            group[name] = spec["default"]
    scene[PARAM_ATTR + "_meta"] = {k: {"min": v["min"], "max": v["max"]} for k, v in schema.items()}
    return schema


def param_snapshot(scene) -> dict:
    group = scene.get(PARAM_ATTR)
    if group is None:
        return {}
    out = {}
    for key in group.keys():
        try:
            out[key] = round(float(group[key]), 6)
        except (TypeError, ValueError):
            continue
    return out


def keyframe_param(scene, name: str):
    for path in (f'{PARAM_ATTR}["{name}"]', f'["{PARAM_ATTR}"]["{name}"]'):
        try:
            if scene.keyframe_insert(data_path=path, frame=scene.frame_current):
                return path
        except Exception:
            continue
    return None


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
    proc = st.get("browser_proc")
    if proc is not None and proc.poll() is None:
        return proc
    exe = find_browser()
    if exe is None:
        st["error"] = "no Chromium-family browser found (Chrome/Edge/Chromium)"
        return None
    profile = os.path.join(tempfile.gettempdir(), "p5bridge-profile")
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
    st["browser_proc"] = proc
    st["browser_mode"] = mode
    return proc


def close_host() -> bool:
    st = _state
    proc = st.get("browser_proc")
    st["browser_proc"] = None
    st["browser_mode"] = ""
    if proc is None or proc.poll() is not None:
        return False
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
    return True


def host_status() -> dict:
    st = _state
    proc = st.get("browser_proc")
    alive = bool(proc is not None and proc.poll() is None)
    return {"alive": alive, "mode": st.get("browser_mode", "") if alive else "",
            "pid": proc.pid if alive else None}

TEMPLATE = r"""// p5b_sketch.js —— 在 Blender 里改，点 Run 生效
//
// 宿主每帧调用：
//   setup(env)  一次
//   draw(env)   每帧；画完把画布发回 Blender
//
// env = { ctx, w, h, t, frame, state }
//   state.objects[] = { name, x, y, depth, visible }   // x/y 已归一化到 0..1
//
// 要试"点跟着空物体"：给物体加 p5b_track 标记（面板 Track Selected），
// 然后移动/打关键帧，下面的圆点就会跟着走。
//
// 参数：下面几行会被插件解析成 Blender 属性（Params 面板），
// 可以被关键帧 / 驱动器驱动，每帧随快照下发到 env.params。

// @param hue 0.55 min=0 max=1
// @param speed 1.0 min=0 max=4
// @param dot 14 min=2 max=60

let trails = [];

function setup(env) {
  trails = (env.state.objects || []).map(() => []);
}

function draw(env) {
  const { ctx, w, h, t, state } = env;
  const p = env.params || {};
  const hue = p.hue == null ? 0.55 : p.hue;
  const trailLen = 10 + 60 * (p.speed == null ? 1 : p.speed);
  const dotR = p.dot == null ? 14 : p.dot;

  // 半透明覆盖 = 拖尾
  ctx.fillStyle = "rgba(8, 10, 18, 0.16)";
  ctx.fillRect(0, 0, w, h);

  const objs = state.objects || [];
  objs.forEach((o, i) => {
    if (o.visible === false) return;
    const x = o.x * w, y = o.y * h;
    const tr = trails[i] || (trails[i] = []);
    tr.push([x, y]);
    if (tr.length > trailLen) tr.shift();

    ctx.strokeStyle = `hsla(${(1 - hue) * 240}, 90%, 65%, 0.6)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    tr.forEach(([px, py], k) => (k ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.stroke();

    const r = dotR;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4);
    g.addColorStop(0, "rgba(255, 240, 200, 1)");
    g.addColorStop(0.4, "rgba(255, 170, 60, 0.75)");
    g.addColorStop(1, "rgba(255, 120, 0, 0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(x, y, r * 2.4, 0, Math.PI * 2); ctx.fill();

    ctx.fillStyle = "#fff";
    ctx.font = "13px ui-monospace, monospace";
    ctx.fillText(o.name, x + 18, y - 14);
  });

  ctx.fillStyle = "rgba(200, 230, 255, 0.75)";
  ctx.font = "12px ui-monospace, monospace";
  ctx.fillText(`frame ${state.frame}  t=${t.toFixed(2)}  tracked=${objs.length}` +
               `  hue=${hue.toFixed(2)}  speed=${(p.speed == null ? 1 : p.speed).toFixed(2)}`, 14, h - 14);
}
"""

_state = {
    "server": None,
    "live": None,
    "fps": 0.0,
    "frames": 0,
    "t0": 0.0,
    "n": 0,
    "last_seq": -1,
    "last_size": (0, 0),
    "error": "",
    "sketch_version": 0,
    "state": None,
    "browser_proc": None,
    "browser_mode": "",
}


# --------------------------------------------------------------------- helpers
def _tag_redraw():
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            screen = win.screen
            if screen is None:
                continue
            for area in screen.areas:
                if area.type in {"IMAGE_EDITOR", "VIEW_3D", "TEXT_EDITOR", "PROPERTIES"}:
                    area.tag_redraw()


def build_state(scene) -> dict:
    """What the sketch gets to see: tracked objects projected to screen space."""
    cam = scene.camera
    objects = []
    if cam is not None:
        for ob in scene.objects:
            if not ob.get("p5b_track"):
                continue
            co = ob.matrix_world.translation
            v = world_to_camera_view(scene, cam, Vector(co))
            objects.append({
                "name": ob.name,
                "x": round(float(v.x), 5),
                "y": round(1.0 - float(v.y), 5),  # canvas y grows downward
                "depth": round(float(v.z), 4),
                "visible": bool(v.z > 0.0 and -0.25 <= v.x <= 1.25 and -0.25 <= v.y <= 1.25),
            })
    return {
        "type": "state",
        "frame": scene.frame_current,
        "t": round(scene.frame_current / max(scene.render.fps, 1), 4),
        "objects": objects,
        "params": param_snapshot(scene),
    }


def _pump():
    st = _state
    srv = st["server"]
    if srv is None:
        return None
    pushed = 0
    while pushed < 3:
        try:
            seq, w, h, planes = srv.frames.get_nowait()
        except queue.Empty:
            break
        try:
            st["live"].push_planes(planes, w, h)
        except Exception as exc:
            st["error"] = f"push failed: {exc}"
            break
        st["last_seq"] = seq
        st["last_size"] = (w, h)
        pushed += 1
    if pushed:
        st["frames"] += pushed
        st["n"] += pushed
        now = time.perf_counter()
        if st["t0"] == 0.0:
            st["t0"] = now
        dt = now - st["t0"]
        if dt >= 0.5:
            st["fps"] = st["n"] / dt
            st["n"] = 0
            st["t0"] = now
        _tag_redraw()
    # scene snapshot for the sketch (cheap: a handful of objects)
    try:
        state = build_state(bpy.context.scene)
        st["state"] = state
        srv.publish_state(json.dumps(state))
    except Exception as exc:
        st["error"] = f"state failed: {exc}"
    return 0.0 if pushed else 0.004


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


def start_bridge(port: int = DEFAULT_PORT, width: int = 640, height: int = 360, host: str = "127.0.0.1"):
    st = _state
    if st["server"] is not None and st["server"].running:
        return True
    release_stale_port(port)
    srv = transport.FrameServer(host=host, port=port)
    if not srv.start():
        st["error"] = srv.error or "server failed to start"
        return False
    st["server"] = srv
    st["live"] = live_image.LiveImage("P5B_live", width, height)
    st["frames"] = 0
    st["n"] = 0
    st["t0"] = 0.0
    st["error"] = ""
    # publish whatever sketch already exists so a fresh browser gets it on connect
    text = bpy.data.texts.get(SKETCH_NAME)
    if text is not None:
        st["sketch_version"] += 1
        srv.publish_sketch(text.as_string(), st["sketch_version"])
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.005)
    return True


def stop_bridge():
    st = _state
    close_host()
    if st["server"] is not None:
        st["server"].stop()
        st["server"] = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    st["fps"] = 0.0


def push_sketch() -> int:
    st = _state
    text = bpy.data.texts.get(SKETCH_NAME)
    if text is None:
        return 0
    st["sketch_version"] += 1
    if st["server"] is not None:
        st["server"].publish_sketch(text.as_string(), st["sketch_version"])
    return st["sketch_version"]


def stats_snapshot():
    st = _state
    srv = st["server"]
    return {
        "running": bool(srv and srv.running),
        "connected": bool(srv and srv.connected),
        "protocol": (srv.stats["protocol"] if srv else None),
        "port": (srv.port if srv else None),
        "frames": st["frames"],
        "fps": round(st["fps"], 2),
        "dropped": (srv.stats["dropped"] if srv else 0),
        "cooks": (srv.stats["cooks"] if srv else 0),
        "silent_for": (srv.silent_for if srv else 0.0),
        "clients": (srv.stats["clients"] if srv else 0),
        "host": host_status(),
        "sketch_version": st["sketch_version"],
        "tracked": len((st["state"] or {}).get("objects", [])),
        "error": st["error"] or (srv.error if srv else ""),
    }


def cook_frame(frame: int | None = None, timeout: float = 5.0) -> int:
    """Ask the sketch for one specific frame and wait for it (blocking, main thread).

    This is the pull-based half of the model: the browser renders exactly on
    request, so a frame is a pure function of (sketch version, frame, state)
    and repeating it gives the same pixels. Returns the frame cooked, or -1.
    """
    st = _state
    srv = st["server"]
    if srv is None or not srv.connected or st["live"] is None:
        return -1
    scene = bpy.context.scene
    target = scene.frame_current if frame is None else int(frame)
    scene.frame_set(target)
    state = build_state(scene)
    fps = max(scene.render.fps, 1)
    srv.clear_results()
    cid = srv.request_cook(target, target / fps, state,
                           int(scene.p5b_width), int(scene.p5b_height))
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
    st["live"].push_planes(planes, w, h)
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
    bl_label = "New Sketch"
    bl_description = "Create the sketch text datablock and open a text editor for it"

    def execute(self, context):
        text = bpy.data.texts.get(SKETCH_NAME)
        if text is None:
            text = bpy.data.texts.new(SKETCH_NAME)
            text.from_string(TEMPLATE)
        # find a text editor to show it in
        shown = False
        for wm in bpy.data.window_managers:
            for win in wm.windows:
                for area in win.screen.areas:
                    if area.type == "TEXT_EDITOR":
                        area.spaces.active.text = text
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
        self.report({"INFO"}, f"{SKETCH_NAME} ready")
        return {"FINISHED"}


class P5B_OT_run_sketch(bpy.types.Operator):
    bl_idname = "p5b.run_sketch"
    bl_label = "Run"
    bl_description = "Push the sketch to the browser and make sure the receiver is running"

    def execute(self, context):
        sc = context.scene
        if _state["server"] is None:
            if not start_bridge(int(sc.p5b_port), int(sc.p5b_width), int(sc.p5b_height)):
                self.report({"ERROR"}, _state["error"])
                return {"CANCELLED"}
        sync_params(sc)
        ver = push_sketch()
        if ver == 0:
            self.report({"ERROR"}, f"no text named {SKETCH_NAME}")
            return {"CANCELLED"}
        connected = bool(_state["server"] and _state["server"].connected)
        self.report({"INFO"}, f"sketch v{ver} pushed ({'live' if connected else 'waiting for browser'})")
        return {"FINISHED"}


class P5B_OT_create_window(bpy.types.Operator):
    bl_idname = "p5b.create_window"
    bl_label = "Create UV Window"
    bl_description = "Create a UV plane whose texture shows the live canvas"

    def execute(self, context):
        st = _state
        if st["live"] is None:
            if not start_bridge(int(context.scene.p5b_port), int(context.scene.p5b_width), int(context.scene.p5b_height)):
                self.report({"ERROR"}, st["error"])
                return {"CANCELLED"}
        ob = window.create_window_plane()
        window.bind_window(ob, st["live"].image)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {"FINISHED"}


class P5B_OT_show_canvas(bpy.types.Operator):
    bl_idname = "p5b.show_canvas"
    bl_label = "Show Live Canvas"

    def execute(self, context):
        st = _state
        if st["live"] is None:
            self.report({"ERROR"}, "start the receiver first")
            return {"CANCELLED"}
        area = context.area
        if area is None:
            self.report({"ERROR"}, "no active area")
            return {"CANCELLED"}
        area.type = "IMAGE_EDITOR"
        space = area.spaces.active
        space.image = st["live"].image
        space.ui_mode = "VIEW"
        return {"FINISHED"}


class P5B_OT_sync_params(bpy.types.Operator):
    bl_idname = "p5b.sync_params"
    bl_label = "Sync Params"
    bl_description = "Rebuild scene properties from the // @param lines in the sketch"

    def execute(self, context):
        schema = sync_params(context.scene)
        self.report({"INFO"}, f"{len(schema)} parameter(s) synced")
        return {"FINISHED"}


class P5B_OT_key_param(bpy.types.Operator):
    bl_idname = "p5b.key_param"
    bl_label = "Key Parameter"
    bl_description = "Insert a keyframe for this parameter at the current frame"

    name: bpy.props.StringProperty()

    def execute(self, context):
        sc = context.scene
        path = keyframe_param(sc, self.name)
        if not path:
            self.report({"ERROR"}, f"could not keyframe {self.name}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"keyed {self.name} at frame {sc.frame_current}")
        return {"FINISHED"}


class P5B_OT_keyframe_params(bpy.types.Operator):
    bl_idname = "p5b.keyframe_params"
    bl_label = "Keyframe Params"
    bl_description = "Insert a keyframe for every sketch parameter at the current frame"

    def execute(self, context):
        sc = context.scene
        done = 0
        for name in sketch_params_schema():
            if keyframe_param(sc, name):
                done += 1
        self.report({"INFO"}, f"{done} keyframe(s) inserted")
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
        if _state["server"] is None:
            if not start_bridge(int(sc.p5b_port), int(sc.p5b_width), int(sc.p5b_height)):
                self.report({"ERROR"}, _state["error"])
                return {"CANCELLED"}
        proc = launch_host(self.mode, int(sc.p5b_port))
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
        srv = _state["server"]
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


# -------------------------------------------------------------------------- UI
def _draw_status_and_start(layout):
    st = stats_snapshot()
    if st["connected"] and st["fps"] > 0.5:
        text, icon = f"live — {st['fps']} fps", "CHECKMARK"
    elif st["connected"]:
        text, icon = "connected, waiting for frames", "TIME"
    elif st["running"]:
        text, icon = "listening, no canvas", "WIRE"
    else:
        text, icon = "stopped", "PAUSE"

    col = layout.column(align=True)
    col.label(text=text, icon=icon)
    if st["error"]:
        col.label(text=st["error"][:64], icon="ERROR")

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator("p5b.start", text="Start", icon="PLAY")
    row.operator("p5b.stop", text="Stop", icon="SNAP_FACE")


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
        layout.operator("p5b.start", text="Start (background)", icon="PLAY")
        layout.operator("p5b.stop", text="Stop", icon="SNAP_FACE")
        layout.separator()
        op = layout.operator("p5b.open_host", text="Open Canvas Window", icon="WINDOW")
        op.mode = "window"
        layout.operator("p5b.close_host", text="Close Canvas", icon="X")
        layout.separator()
        layout.operator("p5b.run_sketch", text="Run Sketch", icon="SCRIPTPLUGINS")
        layout.operator("p5b.new_sketch", text="New Sketch", icon="TEXT")
        layout.separator()
        layout.operator("p5b.show_canvas", text="Show Live Canvas", icon="IMAGE_DATA")
        layout.operator("p5b.create_window", text="Create UV Window", icon="MESH_PLANE")


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
        row.operator("p5b.show_canvas", text="Show", icon="IMAGE_DATA")
        row.operator("p5b.create_window", text="Window Plane", icon="MESH_PLANE")

        row = layout.row(align=True)
        op = row.operator("p5b.open_host", text="Open", icon="WINDOW")
        op.mode = "window"
        op = row.operator("p5b.open_host", text="Background", icon="HIDE_ON")
        op.mode = "headless"
        row.operator("p5b.close_host", text="", icon="X")
        layout.label(text=f"host: {hs['mode'] or 'off'}" + (f" (pid {hs['pid']})" if hs["pid"] else ""))

        row = layout.row(align=True)
        row.prop(sc, "p5b_port", text="")
        row.prop(sc, "p5b_width", text="W")
        row.prop(sc, "p5b_height", text="H")
        layout.label(text=f"127.0.0.1:{st['port'] or sc.p5b_port}", icon="URL")


class P5B_PT_sketch(bpy.types.Panel):
    bl_label = "Sketch"
    bl_idname = "P5B_PT_sketch"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"

    def draw(self, context):
        st = stats_snapshot()
        layout = self.layout
        row = layout.row(align=True)
        row.operator("p5b.run_sketch", text="Run", icon="PLAY")
        row.operator("p5b.new_sketch", text="New", icon="TEXT")
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
        row.operator("p5b.track", text="Track Selected", icon="TRACKER")
        row.operator("p5b.untrack", text="", icon="CANCEL")
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
        row.operator("p5b.cook_still", text="Cook", icon="IMAGE_REFERENCE")
        row.operator("p5b.cook_render", text="Cook + Render", icon="RENDER_STILL")
        layout.operator("p5b.render_animation", icon="RENDER_ANIMATION")
        layout.label(text=f"{st['cooks']} cook request(s)")


class P5B_PT_params(bpy.types.Panel):
    bl_label = "Params"
    bl_idname = "P5B_PT_params"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "p5inBlender"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        sc = context.scene
        layout = self.layout
        schema = sketch_params_schema()
        if not schema:
            layout.label(text="no // @param lines in sketch")
        group = sc.get(PARAM_ATTR) if PARAM_ATTR in sc else None
        for name in schema:
            row = layout.row(align=True)
            if group is not None and name in group:
                try:
                    row.prop(sc, f'{PARAM_ATTR}["{name}"]', text=name)
                except Exception:
                    row.label(text=f"{name} · {group[name]:.3f}")
            else:
                row.label(text=f"{name} · not synced")
            op = row.operator("p5b.key_param", text="", icon="KEY_HLT")
            op.name = name
        row = layout.row(align=True)
        row.operator("p5b.sync_params", text="Sync", icon="FILE_REFRESH")
        row.operator("p5b.keyframe_params", text="Key All", icon="KEY_HLT")


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
        row = layout.row(align=True)
        row.operator("p5b.run_sketch", text="Run", icon="PLAY")
        row.operator("p5b.new_sketch", text="New", icon="TEXT")
        layout.label(text=f"{'live' if st['connected'] else 'off'} · v{st['sketch_version']} · {st['fps']} fps")


classes = (
    P5B_OT_start, P5B_OT_stop, P5B_OT_new_sketch, P5B_OT_run_sketch,
    P5B_OT_open_host, P5B_OT_close_host,
    P5B_OT_create_window, P5B_OT_show_canvas, P5B_OT_track, P5B_OT_untrack,
    P5B_OT_cook_still, P5B_OT_cook_render, P5B_OT_render_animation,
    P5B_OT_sync_params, P5B_OT_keyframe_params, P5B_OT_key_param,
    P5B_PT_panel, P5B_PT_canvas, P5B_PT_sketch, P5B_PT_tracking, P5B_PT_render, P5B_PT_params,
    P5B_PT_text_sidebar, P5B_MT_menu,
)


def _on_load_post(*_args):
    """Default off: loading a file never leaves a listener or a browser behind."""
    stop_bridge()


def _draw_header_menu(self, context):
    self.layout.menu("P5B_MT_menu", text="p5inBlender")


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    for prop, kwargs in (
        ("p5b_port", dict(name="Port", default=DEFAULT_PORT, min=1024, max=65535)),
        ("p5b_width", dict(name="W", default=640, min=16, max=4096)),
        ("p5b_height", dict(name="H", default=360, min=16, max=4096)),
    ):
        if not hasattr(bpy.types.Scene, prop):
            setattr(bpy.types.Scene, prop, bpy.props.IntProperty(**kwargs))
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
    for prop in ("p5b_port", "p5b_width", "p5b_height"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
