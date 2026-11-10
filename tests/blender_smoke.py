"""Headless Blender smoke test.

    flatpak run --filesystem=/mnt/Docs/Code org.blender.Blender -b --factory-startup --python tests/blender_smoke.py
    (or: blender -b --factory-startup --python tests/blender_smoke.py)
"""
import math
import socket
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import lighting_visualizer  # noqa: E402
from lighting_visualizer.bl import runtime, build, creator  # noqa: E402
from lighting_visualizer.core.net import packets  # noqa: E402
import sample_files  # noqa: E402

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def ctx_op(op, **kw):
    return op(**kw)


lighting_visualizer.register()
runtime.init_library()
runtime.refresh_interfaces()
scene = bpy.context.scene
lv = scene.lv

# --- add a moving head and a PAR --------------------------------------------------------------------------------
lv.fixture_type = "generic.mh_spot"
lv.add_universe, lv.add_address, lv.add_count = 1, 1, 1
check(bpy.ops.lv.add_fixture() == {"FINISHED"}, "add moving head")
mh = bpy.context.active_object
lv.fixture_type = "generic.led_par"
lv.fixture_mode = "Dimmer + RGB 4ch"
lv.add_universe, lv.add_address = 1, 101
check(bpy.ops.lv.add_fixture() == {"FINISHED"}, "add LED PAR")
par = bpy.context.active_object
check(mh.lv.is_fixture and mh.lv.pan_obj and mh.lv.tilt_obj and mh.lv.light_obj, "moving head hierarchy")
check(par.lv.pan_obj is None and par.lv.light_obj is not None, "PAR is fixed")
check(par.lv.address == 101 and par.lv.mode == "Dimmer + RGB 4ch", "PAR patch")

# --- automatic listening: loopback -> scene ----------------------------------------------------------------------
check(lv.listen, "listening is on by default")
lv.multicast = False
runtime.SACN_PORT = runtime.ARTNET_PORT = 0          # ephemeral ports so the test never clashes with a real console
runtime.request_rebuild()
runtime.tick()
check(runtime.receiver.running and not runtime.receiver.errors, "receiver starts by itself")
data = bytearray(512)
data[0:4] = bytes([255, 255, 200, 0])   # pan coarse/fine = 255,255 -> max
data[2:4] = bytes([255, 255])           # tilt max
data[4] = 255                           # dimmer
data[5] = 255                           # shutter open
data[100:104] = bytes([255, 255, 0, 0])  # PAR: dimmer full, red
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.sendto(packets.build_sacn(1, bytes(data), cid=b"C" * 16), ("127.0.0.1", runtime.receiver.ports["sacn"]))
end = time.time() + 2
while time.time() < end and runtime.engine.total_packets == 0:
    runtime.receiver.poll()
    time.sleep(0.01)
runtime.request_rebuild()
runtime.tick()
st = runtime.fixtures[mh.name].state
check(abs(st.pan - 270) < 1 and abs(st.tilt - 135) < 1, f"pan/tilt from DMX ({st.pan:.1f}, {st.tilt:.1f})")
check(abs(math.degrees(mh.lv.pan_obj.rotation_euler.z) - 270) < 1, "pan pivot rotated")
check(abs(math.degrees(mh.lv.tilt_obj.rotation_euler.x) - 135) < 1, "tilt pivot rotated")
light = par.lv.light_obj.data
check(light.energy > 0 and light.color[0] == 1.0 and light.color[1] == 0.0, f"PAR light energy={light.energy:.1f} red")
check(mh.lv.light_obj.data.energy > 0, "moving head emits")
check(runtime.lamp_summary(runtime.fixtures[par.name])[0].startswith("100%"), "lamp summary says what it does")

# changing a lamp's address takes effect immediately
par.lv.address = 105
runtime.tick()
check(runtime.fixtures[par.name].state.intensity == 0.0, "address change re-reads the data")
par.lv.address = 101
runtime.tick()
check(runtime.fixtures[par.name].state.intensity == 1.0, "address change back")

# a universe that no lamp uses is reported, not silently dropped
lv.universes = "7"
runtime.tick()
tx.sendto(packets.build_sacn(1, bytes(data), sequence=3, cid=b"C" * 16), ("127.0.0.1", runtime.receiver.ports["sacn"]))
time.sleep(0.2)
runtime.receiver.poll()
check(("sacn", 1) in runtime.engine.ignored_universes(), "ignored universe is reported")
lv.universes = ""
runtime.tick()

# blackout via dimmer 0
data[4] = 0
tx.sendto(packets.build_sacn(1, bytes(data), sequence=5, cid=b"C" * 16), ("127.0.0.1", runtime.receiver.ports["sacn"]))
time.sleep(0.2)
runtime.tick()
check(mh.lv.light_obj.data.energy == 0.0, "dimmer 0 -> light off")
check("dimmer at 0" in runtime.fixtures[mh.name].state.dark_reason(), "dark reason explained")

# switching listening off and on again
lv.listen = False
runtime.tick()
check(not runtime.receiver.running, "listen off stops the receiver")
lv.listen = True
runtime._net["retry_at"] = 0.0
runtime.tick()
check(runtime.receiver.running, "listen on restarts it")

# twenty lamps patched one after another, driven over Art-Net
lv.fixture_type = "generic.led_par"
lv.fixture_mode = "Dimmer + RGB 4ch"
lv.add_universe, lv.add_address, lv.add_count = 2, 1, 20
check(bpy.ops.lv.add_fixture() == {"FINISHED"}, "add 20 lamps")
twenty = [o for o in bpy.data.objects if o.lv.is_fixture and o.name.startswith("LED PAR ")]
check(len(twenty) == 20 and sorted((o.lv.universe, o.lv.address) for o in twenty) == [(2, 1 + 4 * i) for i in range(20)],
      "20 lamps get consecutive addresses")
frame = bytearray(512)
for i in range(20):
    frame[4 * i] = 255 if i % 2 == 0 else 0
    frame[4 * i + 1] = 255
runtime.request_rebuild()
runtime.tick()
ports = runtime.receiver.ports
tx.sendto(packets.build_artnet(1, bytes(frame), sequence=1), ("127.0.0.1", ports["artnet"]))   # Art-Net 1 = universe 2
time.sleep(0.2)
runtime.tick()
lit = sum(1 for o in twenty if runtime.fixtures[o.name].state.intensity == 1.0)
check(lit == 10, f"20 lamps follow Art-Net data ({lit} lit)")
for o in twenty:
    bpy.data.objects.remove(o.lv.light_obj, do_unlink=True)
    bpy.data.objects.remove(o, do_unlink=True)
runtime.request_rebuild()
runtime.tick()
runtime.receiver.stop()
lv.listen = False

# --- test mode --------------------------------------------------------------------------------------------------
mh.lv.use_test = True
runtime.tick()
check(runtime.fixtures[mh.name].state is not None, "test mode evaluates")
check(bpy.ops.lv.test_off() == {"FINISHED"} and not mh.lv.use_test, "Use DMX switches test mode off")
import json  # noqa: E402
old_def = json.loads(mh.lv.type_json)
for m in old_def["modes"]:
    for c in m["channels"]:
        if c["attribute"] == "ColorWheel":
            c["functions"] = []
mh.lv.type_json = json.dumps(old_def)
check(not runtime.resolve_type(mh).modes[0].channel("ColorWheel").functions, "old definition has no wheel")
bpy.ops.object.select_all(action="DESELECT")
check(bpy.ops.lv.update_definitions(every_fixture=True) == {"FINISHED"}, "update definitions operator")
check(len(runtime.resolve_type(mh).modes[0].channel("ColorWheel").functions) > 5, "lamp definition refreshed from library")
from lighting_visualizer.bl import overlay  # noqa: E402
from mathutils import Matrix  # noqa: E402
pts = overlay.beam_lines(Matrix.Translation((1, 2, 6)), 20.0, 5.0)
ring = [p for p in pts if abs(p.z - 1.0) < 1e-6]
check(len(pts) == 2 * overlay.SEGMENTS + 2 * overlay.SPOKES and ring, "beam overlay geometry")
check(abs(math.hypot(ring[0].x - 1, ring[0].y - 2) - 5 * math.tan(math.radians(10))) < 1e-6, "beam cone radius follows angle")

# --- operators --------------------------------------------------------------------------------------------------
bpy.ops.object.select_all(action="DESELECT")
mh.select_set(True), par.select_set(True)
lv.patch_universe, lv.patch_address = 2, 1
check(bpy.ops.lv.auto_patch() == {"FINISHED"}, "auto patch")
check({(mh.lv.universe, mh.lv.address), (par.lv.universe, par.lv.address)} == {(2, 1), (2, 5)},
      f"patch result {(mh.lv.universe, mh.lv.address)} {(par.lv.universe, par.lv.address)}")
lv.haze_level = 0.5
check(bpy.data.objects.get("LV Haze Volume") is not None, "fog slider creates the fog box")
lv.haze_level = 0.25
check(sum(1 for o in bpy.data.objects if o.name.startswith("LV Haze Volume")) == 1, "moving the slider keeps one fog box")
lv.quality = "HIGH"
check(scene.render.engine == "CYCLES", "quality high -> Cycles")
lv.quality = "REALTIME"
check(scene.render.engine.startswith("BLENDER_EEVEE"), f"quality realtime -> {scene.render.engine}")

# --- gobo nodes -------------------------------------------------------------------------------------------------
img = bpy.data.images.new("gobo", 8, 8)
entry = par.lv.gobos.add()
entry.slot, entry.image = 1, img
from lighting_visualizer.core.fixture import ChannelFunction, DmxChannel, DmxMode, FixtureType  # noqa: E402
gt = FixtureType.from_json(runtime.library.get("generic.mh_spot").to_json())
gt.id = "gobo.test"
ch = gt.modes[0].channel("Gobo1")
ch.functions = [ChannelFunction("Open", 0, 9, slot=0), ChannelFunction("Star", 10, 255, slot=1)]
runtime.library.save_user(gt)
lv.fixture_type = runtime.library.all()[0].id
lv.lib_search = ""
lv.fixture_type = [t for t in runtime.library.all() if t.model == gt.model and runtime.library.is_user(t.id)][0].id
bpy.ops.lv.add_fixture()
g = bpy.context.active_object
g.lv.gobos.add().slot = 1
g.lv.gobos[0].image = img
g.lv.use_test = True
vals = list(g.lv.test_values)
off = gt.modes[0].channel("Gobo1").offsets[0] - 1
vals[off] = 100
vals[gt.modes[0].channel("Dimmer").offsets[0] - 1] = 255
g.lv.test_values = vals
runtime.request_rebuild()
runtime.tick()
check(runtime.fixtures[g.name].state.gobo_slot == 1, "gobo slot 1 selected")
check(g.lv.light_obj.data.node_tree.nodes.get("LV Gobo Mapping") is not None, "gobo node tree built")

# --- prism facets ---------------------------------------------------------------------------------------------
vals[gt.modes[0].channel("Prism1").offsets[0] - 1] = 255
g.lv.test_values = vals
runtime.tick()
facets = [bpy.data.objects.get(f"{g.lv.light_obj.name} Prism {i}") for i in (1, 2, 3)]
check(all(facets) and not any(f.hide_render for f in facets), "prism creates three visible facet lights")
check(g.lv.light_obj.data.energy == 0.0 and facets[0].data.energy > 0, "prism moves output to the facets")
check(facets[0].data.node_tree.nodes.get("LV Gobo Mapping") is not None, "facets keep the gobo projection")
vals[gt.modes[0].channel("Prism1").offsets[0] - 1] = 0
g.lv.test_values = vals
runtime.tick()
check(all(f.hide_render for f in facets) and g.lv.light_obj.data.energy > 0, "prism off restores single beam")

# --- IES ------------------------------------------------------------------------------------------------------
ies_dir = Path(tempfile.mkdtemp())
(ies_dir / "t.ies").write_text("IESNA:LM-63-1995\n[TEST] x\nTILT=NONE\n1 1000 1 3 1 1 2 0.1 0.1 0\n1 1 10\n0 45 90\n0\n1000 500 0\n")
it = FixtureType.from_json(runtime.library.get("generic.led_par").to_json())
it.id, it.model = "ies.test", "IES PAR"
it.optical["ies_file"] = str(ies_dir / "t.ies")
runtime.library.save_user(it)
lv.lib_search = ""
lv.fixture_type = "user.generic_ies_par"
bpy.ops.lv.add_fixture()
ies_fx = bpy.context.active_object
check(ies_fx.lv.light_obj.data.node_tree.nodes.get("LV IES") is not None, "IES node attached")

# --- creator ----------------------------------------------------------------------------------------------------
c = scene.lv_creator
c.manufacturer, c.model = "Test", "Creator Fixture"
bpy.ops.lv.creator_edit(action="ADD_MODE")
bpy.ops.lv.creator_edit(action="ADD_CHANNEL")
bpy.ops.lv.creator_edit(action="ADD_CHANNEL")
c.modes[0].channels[1].attribute = "Red"
c.modes[0].channels[1].fine = True
check(bpy.ops.lv.creator_save() == {"FINISHED"}, "creator save")
saved = runtime.library.get("user.test_creator_fixture")
check(saved is not None and saved.modes[0].channels[1].offsets == [2, 3], "creator type stored with 16-bit channel")
c.modes[0].channels[0].offset = 2
try:
    bpy.ops.lv.creator_save()
    check(False, "creator rejects overlapping channels")
except RuntimeError as e:
    check("channel 2" in str(e), "creator rejects overlapping channels")
check(bpy.ops.lv.creator_template() == {"FINISHED"}, "creator template")
lv.fixture_type = "user.test_creator_fixture"
check(bpy.ops.lv.creator_load() == {"FINISHED"} and c.model == "Creator Fixture", "creator load")

# --- GDTF + MVR import ------------------------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp())
gdtf_file = sample_files.make_gdtf(tmp)
check(bpy.ops.lv.import_gdtf(filepath=str(gdtf_file)) == {"FINISHED"}, "GDTF import operator")
mvr_file = tmp / "plot.mvr"
with zipfile.ZipFile(mvr_file, "w") as z:
    z.writestr("GeneralSceneDescription.xml", sample_files.MVR_XML)
    z.write(gdtf_file, "t.gdtf")
before = len([o for o in bpy.data.objects if o.lv.is_fixture])
check(bpy.ops.lv.import_mvr(filepath=str(mvr_file)) == {"FINISHED"}, "MVR import operator")
mvr_fx = [o for o in bpy.data.objects if o.lv.is_fixture and o.name.startswith("Spot 1")]
check(len(mvr_fx) == 1 and (mvr_fx[0].lv.universe, mvr_fx[0].lv.address) == (3, 1), "MVR fixture patched 3.1")
if mvr_fx:
    pan_o, tilt_o = mvr_fx[0].lv.pan_obj, mvr_fx[0].lv.tilt_obj
    check(pan_o and tilt_o and abs(pan_o.location.z + 0.1) < 1e-4 and abs(tilt_o.location.z + 0.3) < 1e-4,
          "GDTF pivots placed from geometry tree")
    check(abs(mvr_fx[0].matrix_world.translation.z - 5.0) < 1e-4 and mvr_fx[0].lv.type_id.startswith("user.acme"),
          "MVR fixture position and GDTF type")

# --- QLC+ import ------------------------------------------------------------------------------------------------
qxf = sample_files.make_qxf(tmp)
check(bpy.ops.lv.import_qlc(filepath=str(qxf), directory=str(qxf.parent), files=[{"name": qxf.name}]) == {"FINISHED"},
      "QLC+ file import operator")
qt = runtime.library.get("user.acme_spot_2000")
check(qt is not None and qt.mode("Full").footprint == 12, "QLC+ fixture stored in library")
lv.qlc_query, lv.qlc_dir = "acme spot", str(qxf.parents[1])
check(bpy.ops.lv.search_qlc() == {"FINISHED"}, "QLC+ search import operator")
lv.add_universe, lv.add_address = 1, 201
check(bpy.ops.lv.add_fixture() == {"FINISHED"}, "add QLC+ fixture")
qfx = bpy.context.active_object
qfx.lv.use_test = True
qvals = list(qfx.lv.test_values)
qmode = qt.mode("Full")
qvals[qmode.channel("Dimmer").offsets[0] - 1] = 255
qfx.lv.test_values = qvals
runtime.request_rebuild()
runtime.tick()
check(qfx.lv.light_obj.data.energy > 0, "QLC+ fixture defaults open the shutter and emit")
check(abs(math.degrees(qfx.lv.pan_obj.rotation_euler.z)) < 1.0, "QLC+ fixture pan defaults to centre")

# --- panels draw against a validating fake layout -----------------------------------------------------------------
ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
problems = []


class FakeOpProps:
    def __init__(self, idname):
        mod, name = idname.split(".")
        object.__setattr__(self, "_props", set(getattr(getattr(bpy.ops, mod), name).get_rna_type().properties.keys()))

    def __setattr__(self, k, v):
        if k not in self._props:
            problems.append(f"operator property {k}")


class FakeLayout:
    def __init__(self):
        self.enabled = self.active = True
        self.alignment = ""
        self.scale_y = 1.0

    def _icon(self, kw):
        if kw.get("icon", "NONE") not in ICONS:
            problems.append(f"icon {kw['icon']}")

    def prop(self, data, name, **kw):
        if name not in data.bl_rna.properties:
            problems.append(f"prop {type(data).__name__}.{name}")
        self._icon(kw)

    def operator(self, idname, **kw):
        self._icon(kw)
        return FakeOpProps(idname)

    def label(self, **kw):
        self._icon(kw)

    def template_list(self, cls, lid, data, prop, adata, aprop, **kw):
        for d, p in ((data, prop), (adata, aprop)):
            if p not in d.bl_rna.properties:
                problems.append(f"template_list {p}")
        if not hasattr(bpy.types, cls):
            problems.append(f"UIList {cls}")

    def row(self, **kw):
        return FakeLayout()

    column = box = split = row

    def separator(self, **kw):
        pass


class FakePanel:
    def __init__(self):
        self.layout = FakeLayout()


from lighting_visualizer.bl import ui as lv_ui  # noqa: E402
pan_ch = runtime.resolve_type(mh).modes[0].channel("Pan")
check(lv_ui._channels(38, pan_ch) == "38+39", "inspector shows absolute DMX channels")
mh.lv.use_test = False
bpy.ops.object.select_all(action="DESELECT")
mh.select_set(True)
bpy.context.view_layer.objects.active = mh
for cls in list(lv_ui.classes) + list(creator.classes):
    if issubclass(cls, bpy.types.Panel):
        if "poll" in cls.__dict__ and not cls.poll(bpy.context):
            continue
        try:
            cls.draw(FakePanel(), bpy.context)
        except Exception as e:
            problems.append(f"{cls.__name__}: {type(e).__name__}: {e}")
check(not problems, f"panels draw cleanly {sorted(set(problems))}")

# --- save / reload keeps fixtures -------------------------------------------------------------------------------
blend = tmp / "x.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
bpy.ops.wm.open_mainfile(filepath=str(blend))
runtime.request_rebuild()
runtime.tick()
check(len(runtime.fixtures) >= 3, f"fixtures survive reload ({len(runtime.fixtures)})")

lighting_visualizer.unregister()
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
