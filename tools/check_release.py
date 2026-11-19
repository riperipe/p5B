"""发布前自检：版本一致、许可正确、没有占位链接、必需文件齐全。

    python tools/check_release.py

退出码 0 = 可以发；1 = 有问题（会列出）。CI 也可以直接跑。
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = ["__init__.py", "blender_manifest.toml", "i18n.py", "transport.py",
            "live_image.py", "window.py", "host/index.html", "host/libs/p5.min.js",
            "host/libs/p5-2.min.js", "host/libs/LICENSE-p5.txt", "LICENSE",
            "THIRD-PARTY.md", "README.md", "README.zh.md"]
PLACEHOLDERS = ("example.invalid", "YOURNAME", "your-username", "TODO", "FIXME")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    problems = []

    # 1) 版本号四处一致：bl_info / manifest / 最新 zip 名 / README 里的示例包名
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
        for name in ("README.md", "README.zh.md"):
            text = read(name)
            if "p5b-<version>.zip" not in text and "p5b-<版本号>.zip" not in text:
                if want not in text:
                    problems.append(f"{name} 里没有写下载包名（应为 p5b-<version>.zip 或 {want}）")

    # 2) 许可：插件本体 GPL-3.0-or-later，内置 p5.js 是 LGPL-2.1（不是 MIT）
    lic = read("LICENSE")
    if "GNU GENERAL PUBLIC LICENSE" not in lic or "Version 3" not in lic:
        problems.append("LICENSE 不是 GPLv3 全文")
    if "GPL-3.0-or-later" not in manifest:
        problems.append("manifest 的 license 字段不是 GPL-3.0-or-later")

    third = read("THIRD-PARTY.md") if os.path.exists(os.path.join(ROOT, "THIRD-PARTY.md")) else ""
    for token in ("p5.js 1.11.3", "p5.js 2.4.0", "LGPL-2.1", "Processing Foundation"):
        if token not in third:
            problems.append(f"THIRD-PARTY.md 缺少 {token}")
    for wrong in ("distributed under the MIT licence", "and GNU Foundation"):
        if wrong in third:
            problems.append(f"THIRD-PARTY.md 仍有错误表述：{wrong}")
    p5lic = os.path.join(ROOT, "host", "libs", "LICENSE-p5.txt")
    if not os.path.exists(p5lic):
        problems.append("缺少 host/libs/LICENSE-p5.txt")
    else:
        with open(p5lic, encoding="utf-8", errors="replace") as fh:
            p5text = fh.read()
        if "LESSER GENERAL PUBLIC LICENSE" not in p5text:
            problems.append("host/libs/LICENSE-p5.txt 不是 LGPL 全文")
        if re.search(r"under the MIT licen[cs]e", p5text, re.I):
            problems.append("host/libs/LICENSE-p5.txt 仍写着 MIT")

    # 3) 占位链接
    for name in ("blender_manifest.toml", "README.md", "README.zh.md"):
        text = read(name) if os.path.exists(os.path.join(ROOT, name)) else ""
        for token in PLACEHOLDERS:
            if token in text:
                problems.append(f"{name} 里还有占位内容：{token}")

    # 4) 扩展权限声明（network 是必须的；files 因为会写 sketch / 临时 profile）
    m_perm = re.search(r"^\[permissions\](.*?)(^\[|\Z)", manifest, re.M | re.S)
    if not m_perm:
        problems.append("manifest 缺少 [permissions] 段")
    else:
        block = m_perm.group(1)
        for key in ("network", "files"):
            if not re.search(rf"^{key}\s*=", block, re.M):
                problems.append(f"manifest 的 [permissions] 里缺少 {key}")
        for line in block.strip().splitlines():
            if "=" in line:
                value = line.split("=", 1)[1].strip().strip('"')
                if len(value) > 64:
                    problems.append(f"权限说明超过 64 字：{line.strip()}")
                if value.endswith("."):
                    problems.append(f"权限说明不能以句号结尾：{line.strip()}")

    # 5) 中文 README 不能留着旧版本的坑
    if os.path.exists(os.path.join(ROOT, "README.zh.md")):
        zh = read("README.zh.md")
        for stale in ("0.3.0.zip", "scripts/addons", "p5B.cook_frame", "Install from Disk"):
            if stale in zh:
                problems.append(f"README.zh.md 仍有过期内容：{stale}")

    # 6) 必需文件
    for name in REQUIRED:
        if not os.path.exists(os.path.join(ROOT, name)):
            problems.append(f"缺少 {name}")

    if problems:
        print("release check FAILED")
        for p in problems:
            print("  -", p)
        return 1
    print(f"release check OK  (version {info_ver})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
