"""GUI check of the beam overlay: opens Blender with a window, lights two lamps, saves a screenshot and quits.

    flatpak run --filesystem=/mnt/Docs/Code org.blender.Blender --python tests/gui_overlay_check.py -- /path/out.png
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp/lv_overlay.png"

import lighting_visualizer  # noqa: E402
from lighting_visualizer.bl import runtime  # noqa: E402

lighting_visualizer.register()
runtime.init_library()
scene = bpy.context.scene
scene.lv.listen = False
for name in ("Cube", "Light", "Camera"):
    if name in bpy.data.objects:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

scene.lv.fixture_type = "generic.mh_spot"
scene.cursor.location = (-2, 0, 5)
bpy.ops.lv.add_fixture()
a = bpy.context.active_object
scene.cursor.location = (2, 0, 5)
scene.lv.add_address = 101
bpy.ops.lv.add_fixture()
b = bpy.context.active_object
for ob, dimmer, rgbpan in ((a, 255, 128), (b, 120, 200)):
    ob.lv.use_test = True
    mode = runtime.resolve_type(ob).modes[0]
    vals = list(ob.lv.test_values)
    vals[mode.channel("Dimmer").offsets[0] - 1] = dimmer
    vals[mode.channel("Pan").offsets[0] - 1] = rgbpan
    vals[mode.channel("Tilt").offsets[0] - 1] = 200
    ob.lv.test_values = vals
runtime.request_rebuild()


def finish():
    runtime.tick()
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
                    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
                    bpy.ops.screen.screenshot(filepath=OUT, check_existing=False)
                print("OVERLAY_SCREENSHOT", OUT)
                bpy.ops.wm.quit_blender()
                return None
    return 0.5


bpy.app.timers.register(finish, first_interval=3.0)
