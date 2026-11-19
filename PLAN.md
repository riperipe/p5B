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

**M5.2 几何下行（Blender 模型 → sketch 线框）** ✅
- [x] `Tracking` 面板 `Send Geometry` 标记网格（`p5b_geo`）
- [x] Blender 侧把顶点投影成 2D 屏幕坐标，连同边索引一起下发（≤2000 顶点 / 4000 边）
- [x] 10 Hz 缓存下发，参与 state 与 cook
- [x] sketch 里 `state.geo[] = { name, verts:[[x,y]...], edges:[[a,b]...] }`
- [x] 实测：42 顶点 / 120 边的二十面体球，画布上出现绿色线框（81473 个绿色像素）

**M5.3 编辑体验** ✅
- [x] `Edit Sketch` 按钮（三处入口），自动定位到当前实例的文本
- [x] `Auto Run on Edit`：改完 ~0.3 秒自动推送（实测版本 1→2，画面 md5 变化）
- [x] `Sketch` 面板显示当前 `text:` 名称

**M5.4 内置 demo** ✅
- [x] `tools/demo_sketch.js` 同时演示 跟随点 / 几何线框 / 上游纹理
- [x] `Reset to Demo` 一键写回；新建实例默认就是这份
- [x] 实测：球体线框 + 脉冲光环 + 拖尾 + HUD，57.8 fps

**M5.5 外部库与全局模式** ✅
- [x] `// @lib <url>` 按需加载 p5 生态库（可多行、支持本地 `/libs/`）
- [x] `// @mode global`：p5 + sketch 放进同源 iframe，老 sketch 免改造
- [x] 实测 p5.riso：网点 + 线框双色印，56.9 fps（缓存网点前只有 8 fps）

**M5.6 WEBGL 与排错** ✅
- [x] WEBGL 画布经 2D 暂存画布回读（直接 `getContext('2d')` 会返回 null）
- [x] 全局模式自动识别（没有 `function sketch(` 但有 `function setup(` 就走 iframe）
- [x] iframe 里的 `onerror` / `console.error` 回传到 Blender 面板
- [x] `Sketch` 面板可下拉切换实例用哪份 Text datablock
- [x] 实测：800×800 WEBGL glitch shader sketch 正常出图（41.9 fps）

**M5.7 修：切换 sketch 后画面不更新** ✅
- 症状：换一份代码跑，Blender 里画面完全不变，看起来像"跑不动"。
- 原因：重载时 `destroySketch()` 没清掉 `iframeCanvas`，旧 iframe 的最后一帧
  继续被当成当前画面发回 Blender；新 sketch 其实已经在跑（只是没被看到）。
- 修法：`destroySketch()` 里 `iframeCanvas = null`。

**M5.8 修：点 Run 报 KeyError: 'server'** ✅
- 多实例重构时删掉了 `_state["server"]`，但 `run_sketch` / `open_host` / `render_animation`
  三个算子还留着旧写法。
- 修法：全部改成走 `active_record()` + `_runtime()`；同时让 `Run` 在没有画布客户端时
  自动拉起后台 host（点 Run 就该看到东西）。

**M5.9 追踪诊断：物体在画面外看不出来** ✅
- 症状：ConnectedParticles 这类 sketch 显示"tracked · idle"，一个粒子都没有。
- 原因：相机被挪走后被跟踪物体投影到屏幕外（`visible: false`），sketch 按"鼠标离开画布"
  处理；另外窗户平面被误跟踪，污染了 objects 列表。
- 修法：`Tracking` 面板显示 `tracked N · on screen M`，全在画面外时红字提示。

**M6 — 平台与分发**（macOS 暂缓，用户明确说不急）
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
