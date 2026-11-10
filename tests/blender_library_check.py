"""Headless Blender check of the bundled GDTF library: categories, real models, pan / tilt, emissive lens, fog slider.

    flatpak run --filesystem=/mnt/Docs --command=blender org.blender.Blender -b --factory-startup \
        --python /mnt/Docs/<path to add-on>/tests/blender_library_check.py
"""
import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lighting_visualizer  # noqa: E402
from lighting_visualizer.bl import atmosphere, props, runtime  # noqa: E402

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


lighting_visualizer.register()
runtime.init_library()
scene = bpy.context.scene
lv = scene.lv

cats = {c[0] for c in props._enum_categories(lv, None)}
check({"Moving Heads", "Theatre Profiles", "Wireless / Battery", "Fresnel / PC"} <= cats, f"categories {sorted(cats)}")
lv.lib_category = "Wireless / Battery"
items = props._enum_types(lv, None)
check(len(items) >= 10 and all(i[1].startswith("Wireless / Battery") for i in items), f"category filter ({len(items)} items)")
lv.lib_category = "ALL"


def add(label_part, expect_parts):
    ftype = min((t for t in runtime.library.all() if label_part in t.label), key=lambda t: len(t.label))
    lv.fixture_type = ftype.id
    lv.add_count = 1
    check(bpy.ops.lv.add_fixture() == {"FINISHED"}, f"add {ftype.label}")
    root = bpy.context.active_object
    parts = [o for o in root.children_recursive if " Part " in o.name]
    check(len(parts) >= expect_parts, f"{ftype.label}: {len(parts)} model meshes")
    return root, parts


roots = {}
for name, minimum in (("Robin Pointe", 3), ("Mini-B", 3), ("MARVEL Spot", 1), ("ApeCoin", 1),
                      ("impression X4", 3)):
    root, parts = add(name, minimum)
    roots[name] = root
    fx = root.lv
    if fx.pan_obj:
        yoke = [p for p in parts if p.parent == fx.pan_obj]
        head = [p for p in parts if p.parent == fx.tilt_obj]
        check(bool(yoke) and bool(head), f"{name}: yoke follows pan, head follows tilt")
    sizes = [max(p.dimensions) for p in parts]
    check(all(0.005 < s < 1.5 for s in sizes), f"{name}: model sizes plausible {[round(s, 3) for s in sizes]}")
    low = min(v.co.z for p in parts for v in p.data.vertices) if parts else 0
    print(f"   {name}: lowest model vertex (local) z={low:.3f}")

# emissive lens: off at 0, full at 255, coloured
_coin = [p for p in roots["ApeCoin"].children_recursive if " Part " in p.name][0]
check(abs(_coin.dimensions.x - 0.06) < 0.002 and abs(_coin.dimensions.z - 0.026) < 0.002,
      f"ApeCoin is coin sized {tuple(round(v, 3) for v in _coin.dimensions)}")
lv.fixture_type = "generic.led_par"
lv.fixture_mode = "Dimmer + RGB 4ch"
lv.add_universe, lv.add_address = 1, 1
bpy.ops.lv.add_fixture()
par = bpy.context.active_object
par.lv.use_test = True
node = par.lv.lens_obj.active_material.node_tree.nodes["LV Lens Emission"]
values = list(par.lv.test_values)
values[0:4] = [0, 255, 0, 0]
par.lv.test_values = values
runtime.request_rebuild()
runtime.tick()
check(node.inputs["Strength"].default_value == 0.0, "lens is off at dimmer 0")
values[0:4] = [255, 255, 0, 0]
par.lv.test_values = values
runtime.tick()
col = node.inputs["Color"].default_value
check(abs(node.inputs["Strength"].default_value - 1.0) < 1e-6 and col[0] == 1.0 and col[1] == 0.0,
      f"lens full and red at dimmer 255 (strength {node.inputs['Strength'].default_value:.2f})")
values[0] = 128
par.lv.test_values = values
runtime.tick()
check(0.4 < node.inputs["Strength"].default_value < 0.6, f"lens half at dimmer 128 ({node.inputs['Strength'].default_value:.2f})")

# a pan / tilt fixture moves its model parts
for name in ("Robin Pointe", "Mini-B"):
    root = roots[name]
    head = next(p for p in root.children_recursive if " Part " in p.name and p.parent == root.lv.tilt_obj)
    bpy.context.view_layer.update()
    before = head.matrix_world.copy()
    root.lv.pan_obj.rotation_euler = (0.0, 0.0, math.radians(30))   # DMX 0 already sits at -270 deg = +90 deg
    bpy.context.view_layer.update()
    moved = sum(abs(a - b) for ra, rb in zip(head.matrix_world, before) for a, b in zip(ra, rb))
    check(moved > 1e-3, f"{name}: head part turns with the pan pivot")

# fog slider
for level in (0.0, 0.5, 1.0):
    lv.haze_level = level
    box = bpy.data.objects.get("LV Haze Volume")
    mul = box.active_material.node_tree.nodes["LV Haze Multiply"] if box else None
    if level == 0.0:
        check(mul is None or mul.inputs[1].default_value == 0.0, "fog 0: multiply value is 0")
        continue
    d = mul.inputs[0].default_value * mul.inputs[1].default_value
    print(f"fog {level:.1f} -> density {d:.4f}")
check(atmosphere.haze_density(0.0) == 0.0 and abs(atmosphere.haze_density(1.0) - 0.2) < 1e-9, "fog slider range")

print("FAILURES:", failures)
sys.exit(1 if failures else 0)
