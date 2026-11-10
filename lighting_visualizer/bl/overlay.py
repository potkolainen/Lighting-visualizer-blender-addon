"""Viewport overlay: draws each lamp's beam as a cone so DMX changes are visible in any shading mode."""
import math
import time

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import runtime

_handle = None
SEGMENTS = 24
SPOKES = 6


def beam_lines(matrix, beam_deg: float, length: float) -> list:
    """Line-segment end points (pairs) of a cone from the light's origin along its -Z axis."""
    apex = matrix.translation.copy()
    axis = (matrix.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    helper = Vector((0.0, 0.0, 1.0)) if abs(axis.z) < 0.99 else Vector((1.0, 0.0, 0.0))
    u = axis.cross(helper).normalized()
    v = axis.cross(u)
    radius = length * math.tan(math.radians(min(max(beam_deg, 0.5), 170.0)) / 2.0)
    centre = apex + axis * length
    ring = [centre + (u * math.cos(2 * math.pi * k / SEGMENTS) + v * math.sin(2 * math.pi * k / SEGMENTS)) * radius
            for k in range(SEGMENTS)]
    pts = []
    for k in range(SEGMENTS):
        pts += [ring[k], ring[(k + 1) % SEGMENTS]]
    for k in range(0, SEGMENTS, SEGMENTS // SPOKES):
        pts += [apex, ring[k]]
    return pts


def _color(st, factor: float, now: float):
    rgb = st.color_at(now)
    top = max(rgb) or 1.0
    return (rgb[0] / top, rgb[1] / top, rgb[2] / top, min(max(st.intensity * factor, 0.0), 1.0) * 0.85 + 0.1)


def _draw():
    scene = bpy.context.scene
    if scene is None or not scene.lv.show_beams or not runtime.fixtures:
        return
    try:
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    except ValueError:
        shader = gpu.shader.from_builtin("3D_UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(2.0)
    gpu.state.depth_test_set("NONE")
    now = time.monotonic()
    for rt in list(runtime.fixtures.values()):
        try:
            st, light = rt.state, rt.obj.lv.light_obj
            if st is None or light is None or st.dark_reason():
                continue
            factor = st.shutter_factor(now)
            if factor <= 0.0:
                continue
            pts = beam_lines(light.matrix_world, st.beam_angle * rt.obj.lv.beam_scale, scene.lv.beam_length)
            batch = batch_for_shader(shader, "LINES", {"pos": [tuple(p) for p in pts]})
            shader.bind()
            shader.uniform_float("color", _color(st, factor, now))
            batch.draw(shader)
        except ReferenceError:
            continue
    gpu.state.blend_set("NONE")
    gpu.state.line_width_set(1.0)


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
