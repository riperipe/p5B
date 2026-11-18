# p5inBlender — 路线图

把 2D 创意编程（p5.js）变成 Blender 里的一等公民：在 Blender 里写代码、点运行、
在 Image Editor 和任何 UV 映射的模型上看到它，并且能逐帧确定性出片。

## 已完成

**M0 — 像素通路** ✅
- TCP + WebSocket 双协议；640x360@60fps 稳定；720p 上限 ~128 fps
- 8-bit sRGB 字节直通（实测 0.5 → 0.5）；色彩/顺序坑已钉死
- UV 窗口平面 + 材质节点树（UV → Mapping → Image → Emission）

**M1 — 在 Blender 里写代码** ✅
- Text datablock 写 sketch，点 Run 热更新
- 场景快照下行：`world_to_camera_view` 投影 + `p5b_track` 标记
- 宿主页面由插件自己吐（`http://127.0.0.1:<port>/`），无 CORS/路径问题

**M2 — 拉取式 cook（确定性出片）** ✅
- v2 帧协议：plane 表 + `req`（0=流帧，>0=cook 应答）
- `cook_frame(frame)`：请求 → 等待 → 写 Image；同帧重复结果一致
- `p5b.render_animation`：逐帧 cook + 渲染
- 多平面（mask → `P5B_mask`）

**M3 — 参数即通道** ⏸ 暂时移除
- 做过一版（`@param` → 场景属性 → 可 K 帧/驱动），面板交互不顺，按用户要求先从 UI 移除
- 代码已删除，等主链路稳定后再回来

**M3.5 — 真 p5.js** ✅
- 宿主加载真 p5.js（本地 `host/p5.min.js` → CDN → 明确提示）
- sketch 用实例模式 `function sketch(p)`，数据走 `p.p5inblender.state`
- 面板可查 `client_p5`

**M4 — 多实例** ✅ 基本完成
- [x] `P5B_Instance`：每个实例独立 text / image / material / plane / 端口
- [x] 多实例同时运行（每实例一个后台浏览器页面），面板列出端口、fps、连接状态
- [x] 点名字切换活动实例；Start / Stop / Remove 按实例操作
- [x] 实测：两个实例 59.9 / 61.5 fps，各自画面 md5 不同（互不干扰）
- [x] 每实例分辨率：`width`/`height`，sketch 里 `p.createCanvas(d.w, d.h)`；实测 320×180 与 640×360 并存（51.6 / 55.5 / 55.2 fps）
- [x] 一个实例的材质可绑到任意网格（`p5b.bind_plane`）
- [ ] pixel_scale 交互降采样、实例缩略图

## 计划

**M5 — 二维/三维真正互通**

**M5.1 上游纹理（Blender → sketch）** ✅
- [x] 实例记录带 `texture`（任意 Image datablock）；`Canvas` 面板可搜索选择
- [x] `Feedback` 按钮：把实例自己的画布当输入（自反馈绘画）
- [x] 下行二进制纹理通道（`P5BT` 头 + RGBA8，~10 Hz，取最新丢旧）
- [x] sketch 里读 `p.p5inblender.texture`（一个 canvas 元素）
- [x] 实测：667 次纹理发送 / 全数 ack；条纹测试图出现在画布上（mean 0.014 → 0.58）

**M5.2 几何下行**（计划）
- 顶点/边数据进 sketch，Blender 模型成为二维素材
- 多平面扩展：depth / id / normal

**M5.3 编辑体验** ✅
- [x] `Edit Sketch` 按钮（三处入口），自动定位到当前实例的文本
- [x] `Auto Run on Edit`：改完 ~0.3 秒自动推送（实测版本 1→2，画面 md5 变化）
- [x] `Sketch` 面板显示当前 `text:` 名称

**M6 — 平台与分发**
- macOS 适配（自备 Chromium，不用 WKWebView）
- 打包：blender_manifest + 可选 Spout/Syphon 加速通道

## 明确不做

- 节点式合成 UI（Copernicus 的语义要学，交互形态不学）
- 依赖系统 WebView（Windows/WebKit 双引擎会撕开行为差异）

## 已知硬约束

- `colorspace_settings` / `alpha_mode` 必须在写像素之前设置，否则缓冲被重置
- 图像缓冲 bottom-up，canvas top-down，写入前 `[::-1]`
- 浏览器会把大帧拆成 WS 分片，必须按 FIN 组包再解析
- 浏览器被完全遮挡时 `requestAnimationFrame` 会暂停（页面有定时器兜底）
- Blender 的 undo 会让 Python 手里指向 Image 的引用失效（每次写入前重新解析）
- 有状态的模拟要能 reset + 定步长重放，才能进确定性出片
- 跨平台像素级一致不承诺；同平台内可复现
