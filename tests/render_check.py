"""Renders a gobo / beam check with Cycles (CPU). Output: /tmp/lv_render.png (use --filesystem for flatpak).

    blender -b --factory-startup --python tests/render_check.py -- /tmp/out.png [prism]
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lighting_visualizer  # noqa: E402
from lighting_visualizer.bl import build, runtime  # noqa: E402
from lighting_visualizer.core.fixture import ChannelFunction, FixtureType  # noqa: E402

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp/lv_render.png"

lighting_visualizer.register()
runtime.init_library()
scene = bpy.context.scene
lv = scene.lv

# floor
for _n in ("Cube", "Light", "Camera"):
    if _n in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[_n], do_unlink=True)
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))

# asymmetric gobo ("F" shape) so orientation and mirroring are visible
N = 64
img = bpy.data.images.new("gobo_F", N, N)
px = [0.0, 0.0, 0.0, 1.0] * (N * N)
def fill(x0, x1, y0, y1):
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * N + x) * 4
            px[i:i + 4] = [1.0, 1.0, 1.0, 1.0]
fill(18, 26, 12, 52)      # stem
fill(18, 46, 44, 52)      # top bar
fill(18, 38, 28, 35)      # middle bar
img.pixels = px

ft = FixtureType.from_json(runtime.library.get("generic.mh_spot").to_json())
ft.id, ft.model = "render.check", "Check"
ft.modes[0].channel("Gobo1").functions = [ChannelFunction("Open", 0, 9, slot=0), ChannelFunction("F", 10, 255, slot=1)]
runtime.library.save_user(ft)

lv.fixture_type = ft.id
lv.lib_search = ""
lv.fixture_type = ft.id
bpy.context.scene.cursor.location = (0, 0, 6)
bpy.ops.lv.add_fixture()
fx = bpy.context.active_object
fx.lv.gobos.add().slot = 1
fx.lv.gobos[0].image = img
fx.lv.use_test = True
mode = ft.modes[0]
vals = list(fx.lv.test_values)
vals[mode.channel("Dimmer").offsets[0] - 1] = 255
vals[mode.channel("Gobo1").offsets[0] - 1] = 100
vals[mode.channel("Zoom").offsets[0] - 1] = 255
if "prism" in sys.argv:
    vals[mode.channel("Prism1").offsets[0] - 1] = 255
fx.lv.test_values = vals
runtime.request_rebuild()
runtime.tick()
print("state:", runtime.fixtures[fx.name].state)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
cam.location = (0, -9, 9)
cam.rotation_euler = (0.9, 0, 0)
scene.camera = cam
scene.world = bpy.data.worlds.new("w")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0

lv.quality = "HIGH"
scene.cycles.device = "CPU"
scene.cycles.samples = 48
scene.render.resolution_x, scene.render.resolution_y = 640, 360
scene.render.filepath = OUT
scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = 0.0
bpy.ops.render.render(write_still=True)
print("rendered", OUT)
lighting_visualizer.unregister()
