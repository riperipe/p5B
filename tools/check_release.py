"""发布前自检：版本一致、没有占位链接、必需文件齐全。

    python tools/check_release.py

退出码 0 = 可以发；1 = 有问题（会列出）。CI 也可以直接跑。
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["__init__.py", "blender_manifest.toml", "i18n.py", "transport.py",
            "live_image.py", "window.py", "host/index.html", "LICENSE",
            "THIRD-PARTY.md", "README.md"]
PLACEHOLDERS = ("example.invalid", "YOURNAME", "your-username", "TODO", "FIXME")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    problems = []

    # 1) 版本号三处一致：bl_info / manifest / 最新 zip 名
    init = read("__init__.py")
    m_info = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', init)
    manifest = read("blender_manifest.toml")
    m_man = re.search(r'^version\s*=\s*"([\d.]+)"', manifest, re.M)
    if not m_info or not m_man:
        problems.append("找不到版本号（bl_info 或 manifest）")
        info_ver = man_ver = None
    else:
        info_ver = ".".join(m_info.groups())
        man_ver = m_man.group(1)
        if info_ver != man_ver:
            problems.append(f"版本不一致：bl_info={info_ver} manifest={man_ver}")
    zips = sorted(f for f in os.listdir(ROOT) if f.endswith(".zip"))
    if info_ver:
        want = f"p5b-{info_ver}.zip"
        if zips and want not in zips:
            problems.append(f"zip 名字和版本对不上：有 {zips}，应为 {want}")

    # 2) 许可证
    lic = read("LICENSE")
    if "GNU GENERAL PUBLIC LICENSE" not in lic or "Version 3" not in lic:
        problems.append("LICENSE 不是 GPLv3 全文")
    if "GPL-3.0-or-later" not in manifest:
        problems.append("manifest 的 license 字段不是 GPL-3.0-or-later")

    # 3) 占位链接
    for name in ("blender_manifest.toml", "README.md", "README.zh.md"):
        text = read(name) if os.path.exists(os.path.join(ROOT, name)) else ""
        for token in PLACEHOLDERS:
            if token in text:
                problems.append(f"{name} 里还有占位内容：{token}")

    # 4) 必需文件 / 不该进包的东西
    for name in REQUIRED:
        if not os.path.exists(os.path.join(ROOT, name)):
            problems.append(f"缺少 {name}")
    for junk in ("__pycache__", ".git"):
        if os.path.exists(os.path.join(ROOT, junk)):
            pass  # 目录存在没关系，打包时要排除

    # 5) 第三方声明里要提到内置 p5
    third = read("THIRD-PARTY.md") if os.path.exists(os.path.join(ROOT, "THIRD-PARTY.md")) else ""
    for token in ("p5.js 1.11.3", "p5.js 2.4.0", "MIT"):
        if token not in third:
            problems.append(f"THIRD-PARTY.md 缺少 {token}")

    if problems:
        print("release check FAILED")
        for p in problems:
            print("  -", p)
        return 1
    print(f"release check OK  (version {info_ver})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
