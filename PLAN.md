# p5inBlender — 路线图

目标：把 2D 创意编程（p5.js / three.js / canvas）变成 Blender 里的一等公民：
在 Blender 里写代码、点运行、在 Image Editor 和任何 UV 映射的模型上看到它；
并且能逐帧确定性出片。

## 已完成

**M0 — 像素通路**（已验证）
- TCP + WebSocket 双协议，640x360@60fps 稳定（丢 1/720），720p 上限 127.6 fps
- 8-bit sRGB Image 字节直通（实测：写 0.5 → 渲回 0.5），色彩顺序坑已钉死
- UV 窗口平面 + 材质节点树（UV → Mapping → Image → Emission）

**M1 — 在 Blender 里写代码 + Empty 驱动**（已验证）
- `p5b_sketch.js` Text datablock，Text Editor 侧栏 Run 按钮
- sketch 热更新（v1 → v2 实测送达）
- 场景快照下行：`world_to_camera_view` 投影 + `p5b_track` 标记，点精确跟随
- `http://127.0.0.1:9877/` 直接吐宿主页面，无 CORS、无文件路径问题

## 已完成

**M2 — 拉取式 cook（出片的地基）** ✅
- [x] v2 帧协议：plane 表 + `req` 字段（0=流帧，>0=cook 应答）
- [x] 下行独立发送线程（不再借帧上行搭车）
- [x] `cook_frame(frame)`：请求 → 等待 → 写 Image，帧内容可复现
- [x] `P5B_mask` 图像（mask plane 落地，供材质当遮罩因子）
- [x] 动画渲染：`p5b.render_animation` 逐帧 cook + 渲染（1–6 帧实测，6 张全不同）
- [x] 多客户端服务器 + 空闲断连 + 端口残留清理 + WebSocket 分片组包

验收：同一帧连续 cook 两次，PNG 逐像素一致。**已通过**：
frame 60 两次 cook 的 md5 同为 `23a81e63d575`，frame 90 / 30 各自不同；
单次 cook 往返 10–52 ms；mask 平面（640x360, Non-Color, 0–0.996）已落盘为 `P5B_mask`。

## 计划

**M3 — 参数即通道**（COP 借鉴 #4） ✅ 主体完成
- [x] sketch 里用 `// @param name default min= max=` 声明参数
- [x] 插件解析 → 场景属性 `scene["p5b_params"]`，`Params` 面板自动出滑杆
- [x] 关键帧可打（实测可用路径 `["p5b_params"]["hue"]`）
- [x] 驱动器可用（实测 `driver_add('["p5b_params"]["speed"]')` + 表达式驱动）
- [x] 每帧随快照下发，sketch 通过 `env.params` 读取
- [ ] lil-gui 降级为浏览器内调试（尚未接入 lil-gui）

**M4 — 实例化**（COP 借鉴 #1、#3、#5）
- `P5B_Instance`：每个实例独立 image + 参数 + 分辨率覆盖
- 网络级默认值继承：resolution / pixel_scale / precision
- 一个实例可被多个平面共用；面板显示实例缩略图（替代 display flag）

**M5 — 二维/三维真正互通**（COP 借鉴 #6、#7）
- 上游纹理输入：p5 接收 Blender 纹理做反馈绘画
- 几何下行：顶点/边数据进 sketch，Blender 模型成为二维素材
- 多平面扩展：depth / id / normal

**M6 — 平台与分发**
- macOS 适配（自备 Chromium，不用 WKWebView）
- 打包：blender_manifest + 可选 Spout/Syphon 加速通道

## 明确不做

- 节点式合成 UI（Copernicus 的语义要学，交互形态不学）
- 把 JS 库"编译进着色器"（GLSL 里没有 JS，这条路不成立）
- 依赖系统 WebView（Windows/WebKit 双引擎会撕开行为差异）

## 已知硬约束

- `colorspace_settings` / `alpha_mode` 必须在写像素之前设置，否则缓冲被重置
- 图像缓冲 bottom-up，canvas top-down，写入前 `[::-1]`
- 跨平台像素级一致不承诺（GPU 光栅化差异），同平台内可复现
- 有状态的模拟（matter.js）要能 reset + 定步长重放才能进确定性出片
- DSH 沙箱禁止 Chromium 多进程命名管道，浏览器端验证需在普通终端跑
