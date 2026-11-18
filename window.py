"""UV-window rig: a mesh whose UV layout is the frame through which the
canvas is seen. The canvas lives in UV 0..1, so moving/rotating/scaling the
island (or a Mapping node) pans the 2D world without touching the sketch.
"""

from __future__ import annotations

import bpy

MAT_NAME = "P5B_Canvas"
UV_NAME = "P5B_UV"


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
    me = obj.data
    if not me.uv_layers:
        me.uv_layers.new(name=UV_NAME)
    return me.uv_layers.active.name


def build_material(image, name: str = MAT_NAME) -> bpy.types.Material:
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
    if tex is None:
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (320, 0)
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.location = (60, 0)
        emit.inputs["Strength"].default_value = 1.0
        # Mapping sits before the texture so crop/pan/zoom is animatable
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.location = (-760, 0)
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.location = (-980, 0)
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (-520, 0)
        nt.links.new(uvn.outputs["UV"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    tex.image = image
    return mat


def bind_window(obj, image, material_name: str = MAT_NAME):
    uv = ensure_uv(obj)
    mat = build_material(image, material_name)
    uvn = next((n for n in mat.node_tree.nodes if n.type == "UVMAP"), None)
    if uvn is not None:
        uvn.uv_map = uv
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat
    return mat
