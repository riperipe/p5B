"""UI 文案的英文/中文切换。

中文是源码里的原文，英文放在下面的表里；`t()` 根据 Blender 的界面语言自动选。
（Blender 自带的界面翻译会翻掉标准词，例如 Show → 显示，但自定义文案不会被翻，
所以这里自己来。）
"""

from __future__ import annotations

import bpy

# 中文原文 -> English
EN = {
    # 面板 / 按钮
    "自身画布作纹理": "Use Own Canvas as Texture",
    "纹理": "Texture",
    "跟随编辑的文本": "Follow Edited Text",
    "目标纹理：{name}": "Target texture: {name}",
    "目标纹理：{name}（未启动）": "Target texture: {name} (not started)",
    "（无）": "(none)",
    "编辑中：{name}（不在运行）": "Editing: {name} (not running)",
    "缺失文本：{name}": "Missing text: {name}",
    "旧连接帧 {n}": "stale {n}",
    "帧": "frames",
    "丢": "dropped",
    # 状态行
    "正在运行 — {fps} fps": "live — {fps} fps",
    "已连接，等待画面": "connected, waiting for frames",
    "正在监听，没有画布": "listening, no canvas",
    "已停止": "stopped",
    "实时信息": "Show Live Info",
    "启动": "Start",
    "停止": "Stop",
    "在 Blender 里编辑": "Edit in Blender",
    "外部编辑器": "External Editor",
    "重置为示例": "Reset Demo",

    # 提示 / 报错
    "没有实例": "No instance",
    "请先启动实例": "Start the instance first",
    "实例 {name} 的画布图像不存在（{image}）": "Instance {name} has no canvas image ({image})",
    "实例 {name} 的纹理 ← {image}": "Texture of {name} ← {image}",
    "文本 '{name}' 找不到": "text '{name}' not found",
    "面板错误：{msg}": "panel error: {msg}",
    # 描述（bl_description）
    "把当前实例的画布图像设为它的上游纹理，sketch 每 0.1 秒能读到上一帧"
    "（反馈举例：拖影、万花筒）。不是撤销/返回":
        "Feed this instance's canvas back into its own sketch as an upstream texture; "
        "the sketch can read the previous frame every 0.1 s (feedback examples: trails, "
        "kaleidoscope). This is NOT undo/redo",
    "在文本编辑器里改哪份 sketch，就自动让实例跑哪份（避免改了别的文件没反应）":
        "Run whichever sketch you are editing in the Text Editor "
        "(avoids editing a file that is not the one being run)",
    # 外置编辑目录说明
    "p5B 外置编辑目录": "p5B external editing folder",
    "这些 .js 文件就是 Blender Text datablock 的镜像：":
        "These .js files mirror the Blender Text datablocks:",
    "  - 在编辑器里改并保存 → 插件 0.3 秒内读回 Blender 并推给画布\n":
        "  - edit and save here -> the add-on reads it back within 0.3 s and pushes it to the canvas\n",
    "  - Blender 里改了再点 Edit Externally 会重新导出\n\n":
        "  - editing in Blender and pressing Edit Externally re-exports the file\n\n",
    "VS Code 建议装扩展：\n": "Recommended VS Code extensions:\n",
    "  - p5.vscode（自动补全 p5 API，作者 Sam Lavigne）\n\n":
        "  - p5.vscode (p5 API autocomplete, by Sam Lavigne)\n\n",
    "  - ESLint（可选）\n": "  - ESLint (optional)\n",
    "注意：Blender 内置文本编辑器只有 Python 着色规则，想要真正的 JS 高亮就用这里。\n":
        "Note: Blender's text editor only knows Python syntax rules; use an external "
        "editor if you want real JS highlighting.\n",
}


def language() -> str:
    """'en' / 'zh'，跟随 Blender 界面语言。"""
    try:
        lang = bpy.context.preferences.view.language
    except Exception:
        lang = "en_US"
    return "zh" if str(lang).lower().startswith("zh") else "en"


def t(text: str, **kw) -> str:
    """按界面语言返回文案；带 {name} 这类占位符的传关键字参数。"""
    out = text if language() == "zh" else EN.get(text, text)
    if kw:
        try:
            out = out.format(**kw)
        except (KeyError, IndexError):
            pass
    return out
