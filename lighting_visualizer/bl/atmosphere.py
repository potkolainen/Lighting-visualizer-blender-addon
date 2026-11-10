"""Venue fog and the real-time / high-quality render switch."""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector

HAZE_OBJECT = "LV Haze Volume"
HAZE_MATERIAL = "LV Haze"
HAZE_NODE = "LV Haze"   # the scatter node; an old world node of the same name is removed when found
HAZE_MULTIPLY = "LV Haze Multiply"
HAZE_MAX_DENSITY = 0.2   # scatter density at slider = 1 (thick fog)
HAZE_CURVE = 5.0
HAZE_MARGIN = 2.0
HAZE_MIN_SIZE = (12.0, 12.0, 6.0)
SMOKE_COLLECTION = "Lighting Atmosphere"


def _set(obj, name, value):
    try:
        setattr(obj, name, value)
    except (AttributeError, TypeError, ValueError):
        pass  # setting missing in this Blender version


def haze_curve(level: float) -> float:
    """Slider 0..1 to the multiply value 0..1; exponential, so steps in light haze are easy to make."""
    level = min(max(level, 0.0), 1.0)
    return (math.exp(HAZE_CURVE * level) - 1.0) / (math.exp(HAZE_CURVE) - 1.0)


def haze_density(level: float) -> float:
    """Volume density for a slider position: 0 is clear air, 1 is dense fog."""
    return HAZE_MAX_DENSITY * haze_curve(level)


def scene_bounds(scene):
    """(min, max) world corners around everything in the scene except the atmosphere objects, or None."""
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    found = False
    for ob in scene.objects:
        if ob.name.startswith(HAZE_OBJECT) or ob.type == "CAMERA":
            continue
        points = ([ob.matrix_world @ Vector(c) for c in ob.bound_box] if ob.type == "MESH"
                  else [ob.matrix_world.translation])
        for p in points:
            lo, hi, found = Vector(map(min, lo, p)), Vector(map(max, hi, p)), True
    return (lo, hi) if found else None


def fit_haze(scene):
    """Size and place the haze box around the scene (with a margin); the user may rescale it afterwards."""
    ob = bpy.data.objects.get(HAZE_OBJECT)
    if ob is None:
        return None
    bounds = scene_bounds(scene)
    lo, hi = bounds if bounds else (Vector((-6.0, -6.0, 0.0)), Vector((6.0, 6.0, 6.0)))
    lo, hi = lo - Vector((HAZE_MARGIN,) * 3), hi + Vector((HAZE_MARGIN,) * 3)
    size = Vector(max(h - l, m) for l, h, m in zip(lo, hi, HAZE_MIN_SIZE))
    ob.location = (lo + hi) / 2.0
    ob.scale = size
    return ob


def _haze_object(scene):
    from .build import get_collection
    ob = bpy.data.objects.get(HAZE_OBJECT)
    if ob is not None:
        return ob
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    mesh = bpy.data.meshes.new(HAZE_OBJECT)
    bm.to_mesh(mesh)
    bm.free()
    mat = bpy.data.materials.new(HAZE_MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    scatter = nt.nodes.new("ShaderNodeVolumeScatter")
    scatter.name = HAZE_NODE
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(scatter.outputs["Volume"], out.inputs["Volume"])
    _ensure_multiply(mat)
    mesh.materials.append(mat)
    ob = bpy.data.objects.new(HAZE_OBJECT, mesh)
    ob.display_type = "WIRE"
    ob.visible_shadow = False
    get_collection(scene, SMOKE_COLLECTION).objects.link(ob)
    fit_haze(scene)
    return ob


def _ensure_multiply(mat):
    """Density = max density x slider value; the slider value alone switches the fog off (0) in every engine."""
    nt = mat.node_tree
    mul = nt.nodes.get(HAZE_MULTIPLY)
    if mul is None:
        mul = nt.nodes.new("ShaderNodeMath")
        mul.name = mul.label = HAZE_MULTIPLY
        mul.operation = "MULTIPLY"
        mul.inputs[0].default_value = HAZE_MAX_DENSITY
        nt.links.new(mul.outputs["Value"], nt.nodes[HAZE_NODE].inputs["Density"])
    return mul


def apply_haze(scene):
    """Venue haze: a scattering volume in a box around the venue, so the sky outside it is never dimmed."""
    lv = scene.lv
    world = scene.world
    if world is not None and world.node_tree is not None:
        old = world.node_tree.nodes.get(HAZE_NODE)
        if old is not None:
            world.node_tree.nodes.remove(old)
    ob = bpy.data.objects.get(HAZE_OBJECT)
    if ob is None:
        if lv.haze_level <= 0.0:
            return
        ob = _haze_object(scene)
    mat = ob.active_material
    _ensure_multiply(mat).inputs[1].default_value = haze_curve(lv.haze_level)
    mat.node_tree.nodes[HAZE_NODE].inputs["Anisotropy"].default_value = lv.haze_anisotropy


def apply_quality(scene):
    if scene.lv.quality == "REALTIME":
        for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = engine
                break
            except TypeError:
                continue
        _set(scene.eevee, "volumetric_tile_size", "8")
        _set(scene.eevee, "volumetric_samples", 32)
        _set(scene.eevee, "use_volumetric_shadows", False)
    else:
        scene.render.engine = "CYCLES"
        _set(scene.cycles, "samples", 256)
        _set(scene.cycles, "volume_bounces", 2)
        _set(scene.cycles, "volume_step_rate", 1.0)
        _set(scene.eevee, "use_volumetric_shadows", True)
        _set(scene.eevee, "volumetric_tile_size", "2")
