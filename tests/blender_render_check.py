"""Cycles render check of framing blades and GDTF gobo images (top view of a lit floor).

    flatpak run --filesystem=/mnt/Docs --command=blender org.blender.Blender -b --factory-startup \
        --python /mnt/Docs/<path to add-on>/tests/blender_render_check.py
"""
import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lighting_visualizer  # noqa: E402
from lighting_visualizer.bl import build, runtime  # noqa: E402
from lighting_visualizer.core import evaluate as ev  # noqa: E402

failures = []
EXPOSURE = 1.0
OUT = ROOT / "tests" / "_probe"
OUT.mkdir(exist_ok=True)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


lighting_visualizer.register()
runtime.init_library()
scene = bpy.context.scene
for default in bpy.data.objects:
    default.hide_render = True   # the factory scene has its own light, which would hide the effects
lv = scene.lv
lv.quality = "HIGH"
scene.cycles.samples = 24
scene.cycles.device = "CPU"
scene.render.resolution_x = scene.render.resolution_y = 96
scene.world = bpy.data.worlds.new("dark")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)

bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, -6))
mat = bpy.data.materials.new("floor")
mat.use_nodes = True
bpy.context.object.data.materials.append(mat)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(cam)
cam.location = (0, 0, -3.5)   # inside the beam, looking down at the lit spot
cam.data.lens = 24
scene.camera = cam


def fixture(label_part):
    ftype = next(t for t in runtime.library.all() if label_part in t.label)
    lv.fixture_type = ftype.id
    lv.add_count = 1
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.lv.add_fixture()
    runtime.request_rebuild()
    runtime.tick()
    return runtime.fixtures[bpy.context.active_object.name]


def drive(rt, **channels):
    mode = rt.ftype.mode(rt.mode_name)
    data = bytearray(512)
    for c in mode.channels:
        for i, off in enumerate(c.offsets):
            data[off - 1] = (c.default >> (8 * (len(c.offsets) - 1 - i))) & 0xFF
    for attr in ("Pan", "Tilt"):
        axis = mode.channel(attr)
        if axis is not None:
            data[axis.offsets[0] - 1] = 128   # beam straight down
    shutter = mode.channel("Shutter")
    if shutter is not None:
        opened = next((f.dmx_from for f in shutter.functions if f.name.lower().startswith("open")), None)
        if opened is not None:
            data[shutter.offsets[0] - 1] = opened
    for attr, value in channels.items():
        ch = mode.channel(attr)
        data[ch.offsets[0] - 1] = value
    rt.state = ev.evaluate(rt.ftype, mode, bytes(data), 1)
    rt.last_key = ()
    build.apply_state(rt, 0.0, EXPOSURE)
    return rt.state


def render(name):
    scene.render.filepath = str(OUT / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(scene.render.filepath, check_existing=False)
    w, h = img.size
    px = list(img.pixels)
    bpy.data.images.remove(img)
    lum = [px[(y * w + x) * 4] for y in range(h) for x in range(w)]
    return lum, w, h


def halves(lum, w, h):
    left = sum(lum[y * w + x] for y in range(h) for x in range(w // 2))
    right = sum(lum[y * w + x] for y in range(h) for x in range(w // 2, w))
    top = sum(lum[y * w + x] for y in range(h // 2) for x in range(w))
    bottom = sum(lum[y * w + x] for y in range(h // 2, h) for x in range(w))
    return left, right, top, bottom


# --- framing blades ---------------------------------------------------------------------------------------------
rt = fixture("Robin T1")
open_state = drive(rt, Dimmer=255)
check(not open_state.framed, "blades out by default")
print("   open state:", open_state.dark_reason() or "lit", "energy", rt.obj.lv.light_obj.data.energy)
bpy.context.view_layer.update()
_l = rt.obj.lv.light_obj
print("   pan/tilt", open_state.pan, open_state.tilt, "light at", tuple(round(v, 2) for v in _l.matrix_world.translation),
      "aims", tuple(round(v, 2) for v in (_l.matrix_world.to_quaternion() @ __import__("mathutils").Vector((0, 0, -1)))),
      "spot", round(math.degrees(_l.data.spot_size), 1), "type", _l.data.type)
lum_open, w, h = render("blades_open")
total_open = sum(lum_open)
check(total_open > 1.0, f"beam lights the floor ({total_open:.1f})")

for blade in range(1, 5):
    drive(rt, Dimmer=255, **{f"Blade{blade}A": 140})
    assert rt.state.framed
    lum, w, h = render(f"blade{blade}")
    left, right, top, bottom = halves(lum, w, h)
    print(f"   blade {blade}: total {sum(lum) / total_open:.2f} of open; left/right {left:.1f}/{right:.1f} top/bottom {top:.1f}/{bottom:.1f}")
    check(sum(lum) < total_open * 0.8, f"blade {blade} cuts the beam ({sum(lum) / total_open:.2f} of open)")

drive(rt, Dimmer=255, Blade1A=255, Blade2A=255, Blade3A=255, Blade4A=255)
lum, w, h = render("blades_closed")
check(sum(lum) < total_open * 0.05, f"all blades in: nearly dark ({sum(lum) / total_open:.3f})")

# --- GDTF gobo image ----------------------------------------------------------------------------------------------
rt2 = fixture("Robin Pointe")
rt2.obj.location = (0, 0, 0)
mode = rt2.ftype.mode(rt2.mode_name)
gobo_values = []
for f in mode.channel("Gobo1").functions:
    if f.slot is not None and f.slot > 0 and ev._slot_image(rt2.ftype, mode.channel("Gobo1"), f):
        gobo_values.append((f.dmx_from, f.slot))
check(bool(gobo_values), f"Pointe has gobo slots with images ({len(gobo_values)})")
# hide the first fixture so the gobo is judged alone
for o in bpy.data.objects:
    if o.lv.is_fixture and o.name != rt2.obj.name:
        o.hide_render = True
        for c in o.children_recursive:
            c.hide_render = True
drive(rt2, Dimmer=255)
lum_open2, w, h = render("gobo_open")
dmx, slot = gobo_values[0]
drive(rt2, Dimmer=255, Gobo1=dmx)
check(rt2.state.gobo_slot == slot and Path(rt2.state.gobo_image).is_file(), f"gobo slot {slot} image {Path(rt2.state.gobo_image).name}")
lum_g, w, h = render("gobo_slot")
diff = sum(abs(a - b) for a, b in zip(lum_open2, lum_g)) / max(sum(lum_open2), 1e-6)
check(diff > 0.05, f"gobo changes the pattern (difference {diff:.2f})")

print("FAILURES:", failures)
sys.exit(1 if failures else 0)
