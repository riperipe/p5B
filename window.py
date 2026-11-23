"""UV-window rig: a mesh whose UV layout is the frame through which the
canvas is seen. The canvas lives in UV 0..1, so moving/rotating/scaling the
island (or a Mapping node) pans the 2D world without touching the sketch.

Default material (v0.16):

    UV Map ─ Mapping ─ Image Texture ─┬─ Color ───────────── Emission ─┐
                                      │                               Mix ─ Output
                                      └─ Alpha ─ ColorRamp(alpha) ───┘   ▲
                                                                  Transparent

* Alpha 已经接好：Image.Alpha → ColorRamp（默认 0→0 / 1→1，直通）→ Mix Shader 的系数，
  所以画布的透明区域在 Blender 里也是透明的（EEVEE 用 BLENDED 混合）。
* 颜色那条路上放了一个 **P5B Color Ramp**（未连线、已摆好位置），想做套色/色调映射时
  把 Image.Color 拖到它、再把它接到 Emission 即可。
"""

from __future__ import annotations

import bpy

MAT_NAME = "P5B_Canvas"
UV_NAME = "UVMap"        # Blender 自带 UV 图层的默认名字（仅在必须新建时使用）
RIG_VERSION = 2  # 结构版本：老材质会被自动升级


def create_window_plane(name: str = "P5B_Window", size: float = 2.0, location=(0.0, 0.0, 0.0)):
    me = bpy.data.meshes.new(name + "Mesh")
    s = size * 0.5
    me.from_pydata([(-s, -s, 0.0), (s, -s, 0.0), (s, s, 0.0), (-s, s, 0.0)], [], [(0, 1, 2, 3)])
    me.update()
    uvl = me.uv_layers.new(name=UV_NAME)
    corners = {0: (0.0, 0.0), 1: (1.0, 0.0), 2: (1.0, 1.0), 3: (0.0, 1.0)}
    for loop in me.loops:
        uvl.data[loop.index].uv = corners[loop.vertex_index]
    ob = bpy.data.objects.new(name, me)
    ob.location = location
    bpy.context.scene.collection.objects.link(ob)
    return ob


def ensure_uv(obj) -> str:
    """返回物体已有的 UV 图层名（活动的那张）；不新建。

    窗户材质直接吃模型自带的 UV：共享 UV、UV 编辑、多 UV 图层都按 Blender 的
    活动图层走。返回空字符串表示这个网格没有 UV，此时 UVMap 节点留空，
    Blender 会退回活动 UV（没有就采不到像素）。
    """
    me = getattr(obj, "data", None)
    layers = getattr(me, "uv_layers", None)
    if not layers:
        return ""
    active = layers.active
    return active.name if active is not None else layers[0].name


def _apply_blend_mode(mat) -> None:
    """让 alpha 真的透明：EEVEE Next 用 surface_render_method，旧版用 blend_method。"""
    for attr, value in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        if hasattr(mat, attr):
            try:
                setattr(mat, attr, value)
                return
            except Exception:
                continue


def fit_rect(img_w, img_h):
    """How much of the square canvas the image covers, uniformly scaled (contain fit).

    640x360 on a 2x2 square -> (1.0, 0.5625): full width, vertical letterbox.
    """
    if not img_w or not img_h:
        return 1.0, 1.0
    aspect = float(img_w) / float(img_h)
    if aspect >= 1.0:
        return 1.0, 1.0 / aspect
    return aspect, 1.0


def apply_aspect(mat, image=None) -> None:
    """Uniform-scale the image inside the square canvas: no stretching, bars instead."""
    nt = mat.node_tree
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
    img = image if image is not None else (tex.image if tex else None)
    if img is None:
        return
    sx, sy = fit_rect(img.size[0], img.size[1])
    mapping = nt.nodes.get("P5B Mapping") or next((n for n in nt.nodes if n.type == "MAPPING"), None)
    if mapping is not None:
        mapping.inputs["Scale"].default_value = (1.0 / sx, 1.0 / sy, 1.0)
        mapping.inputs["Location"].default_value = (0.5 - 0.5 / sx, 0.5 - 0.5 / sy, 0.0)
    if tex is not None:
        tex.extension = "CLIP"  # 画布外不重复，露底色/透明


def build_material(image, name: str = MAT_NAME, force: bool = False) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree

    # 已经有 ColorRamp 的材质视为"用户改过"，只更新图像，不覆盖节点（force 除外）
    if not force and any(n.type == "VALTORGB" for n in nt.nodes):
        tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
        if tex is not None:
            tex.image = image
        _apply_blend_mode(mat)
        apply_aspect(mat, image)
        return mat

    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.name = out.label = "P5B Output"
    out.location = (760, 0)

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = bsdf.label = "P5B Surface"
    bsdf.location = (440, 0)
    # 印刷/屏幕感：不反光
    for sock, value in (("Roughness", 0.9), ("Metallic", 0.0), ("IOR", 1.45)):
        if sock in bsdf.inputs:
            bsdf.inputs[sock].default_value = value
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.15
    elif "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = 0.15
    # 自发光：场景没灯也能看清画布（同时保留 Principled 的 alpha/混合能力）
    if "Emission Strength" in bsdf.inputs:
        bsdf.inputs["Emission Strength"].default_value = 1.0

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.name = tex.label = "P5B Image"
    tex.location = (-420, 0)

    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.name = mapping.label = "P5B Mapping"
    mapping.location = (-680, 0)

    # 自带 UV：Texture Coordinate 的 UV 输出直接取网格的活动 UV 图层，
    # 不新建、不覆盖用户已有的 UV（多 UV 时跟随 Blender 的活动图层）
    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.name = coord.label = "P5B UV"
    coord.location = (-920, 0)

    # 颜色：默认直通（ColorRamp 会把 RGB 变成灰度，别默认插在链路上）
    # 需要套色/色调映射时，把 Image.Color 拖到 P5B Color Ramp、再接到 Base Color
    cramp = nt.nodes.new("ShaderNodeValToRGB")
    cramp.name = cramp.label = "P5B Color Ramp"
    cramp.location = (60, 320)
    elements = cramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = (0.02, 0.03, 0.08, 1.0)
    elements[1].position = 1.0
    elements[1].color = (1.0, 0.86, 0.45, 1.0)
    mid = elements.new(0.5)
    mid.color = (0.95, 0.35, 0.25, 1.0)

    # Alpha：单独一条 ramp，默认直通；拉色标就能改透明度曲线/硬边
    aramp = nt.nodes.new("ShaderNodeValToRGB")
    aramp.name = aramp.label = "P5B Alpha Ramp"
    aramp.location = (60, -220)
    ael = aramp.color_ramp.elements
    ael[0].position = 0.0
    ael[0].color = (0.0, 0.0, 0.0, 1.0)
    ael[1].position = 1.0
    ael[1].color = (1.0, 1.0, 1.0, 1.0)

    nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if "Emission Color" in bsdf.inputs:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
    nt.links.new(tex.outputs["Alpha"], aramp.inputs["Fac"])
    nt.links.new(aramp.outputs["Color"], bsdf.inputs["Alpha"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex.image = image
    _apply_blend_mode(mat)
    mat.use_backface_culling = False
    apply_aspect(mat, image)
    return mat


def bind_window(obj, image, material_name: str = MAT_NAME):
    mat = build_material(image, material_name)
    legacy = next((n for n in mat.node_tree.nodes if n.type == "UVMAP"), None)
    if legacy is not None:
        legacy.uv_map = ensure_uv(obj)
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat
    return mat
