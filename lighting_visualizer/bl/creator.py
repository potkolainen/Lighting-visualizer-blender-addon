"""Fixture Creator: edit a fixture definition, attach custom 3D geometry and save it to the personal library."""
import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ImportHelper

from ..core.fixture import (ATTRIBUTE_IDS, ATTRIBUTES, KINDS, MODE_TEMPLATES, ChannelFunction, DmxChannel, DmxMode,
                            FixtureType, GEOMETRY_DEFAULTS, mode_from_template)
from . import build, runtime
from .operators import active_fixture

_SUFFIX = re.compile(r"\.\d{3}$")
SOURCES = (("LED", "LED", ""), ("Halogen", "Halogen", ""), ("Tungsten", "Tungsten", ""),
           ("Discharge", "Discharge", ""), ("Other", "Other", ""))


class LV_CreatorFunction(PropertyGroup):
    name: StringProperty(name="Function", default="Function")
    dmx_from: IntProperty(name="From", min=0, max=255, default=0)
    dmx_to: IntProperty(name="To", min=0, max=255, default=255)
    use_phys: BoolProperty(name="Physical Range", description="Map the range to physical values (deg, Hz, ...)")
    phys_from: FloatProperty(name="Physical From")
    phys_to: FloatProperty(name="Physical To")
    slot: IntProperty(name="Wheel Slot", description="0-based wheel slot selected by this range, -1 = none",
                      min=-1, default=-1)
    use_color: BoolProperty(name="Wheel Colour", description="This range is a colour filter (colour wheel slot)")
    color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0))


class LV_CreatorChannel(PropertyGroup):
    name: StringProperty(name="Name", default="Channel")
    attribute: EnumProperty(name="Function", items=[(a, l, "") for a, l in ATTRIBUTES], default="Dimmer")
    offset: IntProperty(name="DMX Offset", min=1, max=512, default=1, description="Channel number inside the fixture")
    fine: BoolProperty(name="16-bit", description="Uses the next channel as fine byte")
    default: IntProperty(name="Default", min=0, max=65535, default=0, description="Home value (0-255, or 0-65535 for 16-bit)")
    functions: CollectionProperty(type=LV_CreatorFunction)
    function_index: IntProperty()


class LV_CreatorMode(PropertyGroup):
    name: StringProperty(name="Mode", default="Mode 1")
    channels: CollectionProperty(type=LV_CreatorChannel)
    channel_index: IntProperty()


class LV_Creator(PropertyGroup):
    manufacturer: StringProperty(name="Manufacturer")
    model: StringProperty(name="Model", default="My Fixture")
    kind: EnumProperty(name="Type", items=[(k, l, "") for k, l in KINDS], default="OTHER")
    description: StringProperty(name="Description")
    width: FloatProperty(name="Width", unit="LENGTH", min=0.01, default=0.25)
    depth: FloatProperty(name="Depth", unit="LENGTH", min=0.01, default=0.25)
    height: FloatProperty(name="Height", unit="LENGTH", min=0.01, default=0.35)
    weight: FloatProperty(name="Weight (kg)", min=0.0)
    power: FloatProperty(name="Power (W)", min=0.0)
    source: EnumProperty(name="Light Source", items=SOURCES, default="LED")
    lumens: FloatProperty(name="Luminous Flux (lm)", min=0.0)
    candela: FloatProperty(name="Intensity (cd)", min=0.0)
    beam_angle: FloatProperty(name="Beam Angle", min=0.1, max=180.0, default=25.0)
    field_angle: FloatProperty(name="Field Angle", min=0.1, max=180.0, default=40.0)
    zoom_min: FloatProperty(name="Zoom Min Beam", min=0.0, max=180.0)
    zoom_max: FloatProperty(name="Zoom Max Beam", min=0.0, max=180.0)
    color_temp: FloatProperty(name="Color Temp (K)", min=1000.0, max=20000.0, default=5600.0)
    cri: FloatProperty(name="CRI", min=0.0, max=100.0)
    lens: StringProperty(name="Lens")
    ies_file: StringProperty(name="IES File", subtype="FILE_PATH")
    pan_range: FloatProperty(name="Pan Range", min=0.0, max=1440.0, description="Mechanical limit in degrees, 0 = fixed")
    tilt_range: FloatProperty(name="Tilt Range", min=0.0, max=720.0)
    model_file: StringProperty(name="Model File", subtype="FILE_PATH")
    pan_pivot: FloatVectorProperty(name="Pan Pivot", size=3, unit="LENGTH")
    tilt_pivot: FloatVectorProperty(name="Tilt Pivot", size=3, unit="LENGTH")
    lens_offset: FloatVectorProperty(name="Lens Position", size=3, unit="LENGTH")
    use_pivots: BoolProperty(name="Use Custom Pivots", description="Use the pivots below instead of the dimensions")
    base_objects: StringProperty(name="Base Parts")
    yoke_objects: StringProperty(name="Yoke Parts")
    head_objects: StringProperty(name="Head Parts")
    modes: CollectionProperty(type=LV_CreatorMode)
    mode_index: IntProperty()
    template: EnumProperty(name="Template", items=[(k, k, "") for k in MODE_TEMPLATES])


# ---- conversion ------------------------------------------------------------------------------------------------
def _opt(v):
    return float(v) if v else None


def _names(text: str) -> list:
    return [_SUFFIX.sub("", n.strip()) for n in text.split(",") if n.strip()]


def build_type(c: LV_Creator) -> FixtureType:
    geometry = {**GEOMETRY_DEFAULTS, "pan_range": c.pan_range, "tilt_range": c.tilt_range, "model_file": c.model_file,
                "base_objects": _names(c.base_objects), "yoke_objects": _names(c.yoke_objects),
                "head_objects": _names(c.head_objects)}
    if c.use_pivots:
        geometry.update(pan_pivot=list(c.pan_pivot), tilt_pivot=list(c.tilt_pivot), lens_offset=list(c.lens_offset))
    modes = []
    for m in c.modes:
        chans = []
        for ch in m.channels:
            funcs = [ChannelFunction(
                f.name, f.dmx_from, max(f.dmx_to, f.dmx_from), f.phys_from if f.use_phys else None,
                f.phys_to if f.use_phys else None, f.slot if f.slot >= 0 else None,
                tuple(f.color) if f.use_color else None) for f in ch.functions]
            offsets = [ch.offset, ch.offset + 1] if ch.fine else [ch.offset]
            chans.append(DmxChannel(ch.attribute, ch.name, offsets, ch.default, funcs))
        modes.append(DmxMode(m.name, chans))
    return FixtureType(
        id="", manufacturer=c.manufacturer, model=c.model, kind=c.kind, category="User", description=c.description,
        physical={"width": c.width, "depth": c.depth, "height": c.height, "weight": _opt(c.weight),
                  "power": _opt(c.power)},
        optical={"source": c.source, "lumens": _opt(c.lumens), "candela": _opt(c.candela),
                 "beam_angle": c.beam_angle, "field_angle": max(c.field_angle, c.beam_angle),
                 "zoom_min": _opt(c.zoom_min), "zoom_max": _opt(c.zoom_max), "color_temp": c.color_temp,
                 "cri": _opt(c.cri), "lens": c.lens, "distribution": "ies" if c.ies_file else "gaussian",
                 "ies_file": c.ies_file},
        geometry=geometry, modes=modes, provenance={"physical": "user", "optical": "user", "dmx": "user"})


def load_type(c: LV_Creator, t: FixtureType):
    c.manufacturer, c.model, c.kind, c.description = t.manufacturer, t.model, t.kind, t.description
    p, o, g = t.physical, t.optical, t.geometry
    c.width, c.depth, c.height = p.get("width") or 0.25, p.get("depth") or 0.25, p.get("height") or 0.35
    c.weight, c.power = p.get("weight") or 0.0, p.get("power") or 0.0
    c.source = o.get("source") if o.get("source") in {s[0] for s in SOURCES} else "Other"
    c.lumens, c.candela = o.get("lumens") or 0.0, o.get("candela") or 0.0
    c.beam_angle, c.field_angle = o.get("beam_angle") or 25.0, o.get("field_angle") or 40.0
    c.zoom_min, c.zoom_max = o.get("zoom_min") or 0.0, o.get("zoom_max") or 0.0
    c.color_temp, c.cri = o.get("color_temp") or 5600.0, o.get("cri") or 0.0
    c.lens, c.ies_file = o.get("lens", ""), o.get("ies_file", "")
    c.pan_range, c.tilt_range, c.model_file = g.get("pan_range", 0.0), g.get("tilt_range", 0.0), g.get("model_file", "")
    c.use_pivots = bool(g.get("pan_pivot"))
    if c.use_pivots:
        c.pan_pivot, c.tilt_pivot = g["pan_pivot"], g.get("tilt_pivot") or (0, 0, 0)
        c.lens_offset = g.get("lens_offset") or (0, 0, 0)
    c.base_objects, c.yoke_objects, c.head_objects = (", ".join(g.get(k, [])) for k in
                                                      ("base_objects", "yoke_objects", "head_objects"))
    c.modes.clear()
    for m in t.modes:
        cm = c.modes.add()
        cm.name = m.name
        for ch in m.channels:
            cc = cm.channels.add()
            cc.name, cc.offset = ch.name, ch.offsets[0]
            cc.attribute = ch.attribute if ch.attribute in ATTRIBUTE_IDS else "Control"
            cc.fine, cc.default = len(ch.offsets) > 1, min(ch.default, 65535)
            for f in ch.functions:
                cf = cc.functions.add()
                cf.name, cf.dmx_from, cf.dmx_to = f.name, f.dmx_from, f.dmx_to
                cf.use_phys = f.phys_from is not None and f.phys_to is not None
                cf.phys_from, cf.phys_to = f.phys_from or 0.0, f.phys_to or 0.0
                cf.slot = -1 if f.slot is None else f.slot
                cf.use_color = bool(f.color)
                if f.color:
                    cf.color = f.color
    c.mode_index = 0


def _overlaps(mode: LV_CreatorMode) -> list:
    used, clashes = {}, []
    for ch in mode.channels:
        for off in ([ch.offset, ch.offset + 1] if ch.fine else [ch.offset]):
            if off in used:
                clashes.append(f"{mode.name}: channel {off} used by '{used[off]}' and '{ch.name}'")
            used[off] = ch.name
    return clashes


# ---- operators -------------------------------------------------------------------------------------------------
class LV_OT_creator_edit(Operator):
    bl_idname = "lv.creator_edit"
    bl_label = "Edit"
    bl_options = {"INTERNAL"}

    action: EnumProperty(items=[(a, a, "") for a in (
        "ADD_MODE", "REMOVE_MODE", "ADD_CHANNEL", "REMOVE_CHANNEL", "ADD_FUNCTION", "REMOVE_FUNCTION")])

    def execute(self, context):
        c = context.scene.lv_creator
        mode = c.modes[c.mode_index] if 0 <= c.mode_index < len(c.modes) else None
        chan = mode.channels[mode.channel_index] if mode and 0 <= mode.channel_index < len(mode.channels) else None
        a = self.action
        if a == "ADD_MODE":
            c.modes.add().name = f"Mode {len(c.modes)}"
            c.mode_index = len(c.modes) - 1
        elif a == "REMOVE_MODE" and mode:
            c.modes.remove(c.mode_index)
            c.mode_index = max(c.mode_index - 1, 0)
        elif a == "ADD_CHANNEL" and mode:
            nxt = max((x.offset + (2 if x.fine else 1) for x in mode.channels), default=1)
            ch = mode.channels.add()
            ch.offset = min(nxt, 512)
            mode.channel_index = len(mode.channels) - 1
        elif a == "REMOVE_CHANNEL" and chan:
            mode.channels.remove(mode.channel_index)
            mode.channel_index = max(mode.channel_index - 1, 0)
        elif a == "ADD_FUNCTION" and chan:
            chan.functions.add()
            chan.function_index = len(chan.functions) - 1
        elif a == "REMOVE_FUNCTION" and chan and 0 <= chan.function_index < len(chan.functions):
            chan.functions.remove(chan.function_index)
            chan.function_index = max(chan.function_index - 1, 0)
        return {"FINISHED"}


class LV_OT_creator_template(Operator):
    bl_idname = "lv.creator_template"
    bl_label = "Add Mode From Template"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        c = context.scene.lv_creator
        t = mode_from_template(c.template, c.template)
        m = c.modes.add()
        m.name = t.name
        for ch in t.channels:
            cc = m.channels.add()
            cc.name, cc.attribute, cc.offset = ch.name, ch.attribute, ch.offsets[0]
            cc.fine, cc.default = len(ch.offsets) > 1, ch.default
            for f in ch.functions:
                cf = cc.functions.add()
                cf.name, cf.dmx_from, cf.dmx_to = f.name, f.dmx_from, f.dmx_to
                cf.slot = -1 if f.slot is None else f.slot
                cf.use_phys = f.phys_from is not None and f.phys_to is not None
                cf.phys_from, cf.phys_to = f.phys_from or 0.0, f.phys_to or 0.0
                cf.use_color = bool(f.color)
                if f.color:
                    cf.color = f.color
        c.mode_index = len(c.modes) - 1
        return {"FINISHED"}


class LV_OT_creator_load(Operator):
    bl_idname = "lv.creator_load"
    bl_label = "Load Selected Library Fixture"
    bl_description = "Copy the library fixture chosen in the Library panel into the creator"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        t = runtime.library.get(context.scene.lv.fixture_type)
        if t is None:
            return {"CANCELLED"}
        load_type(context.scene.lv_creator, t)
        return {"FINISHED"}


class LV_OT_creator_save(Operator):
    bl_idname = "lv.creator_save"
    bl_label = "Save to Personal Library"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        c = context.scene.lv_creator
        if not c.model.strip():
            self.report({"ERROR"}, "Enter a model name")
            return {"CANCELLED"}
        if not c.modes:
            self.report({"ERROR"}, "Add at least one DMX mode")
            return {"CANCELLED"}
        clashes = [x for m in c.modes for x in _overlaps(m)]
        if clashes:
            self.report({"ERROR"}, clashes[0])
            return {"CANCELLED"}
        ftype = build_type(c)
        runtime.library.save_user(ftype)
        lv = context.scene.lv
        lv.lib_search = ""
        try:
            lv.fixture_type = ftype.id
        except TypeError:
            pass
        self.report({"INFO"}, f"Saved '{ftype.label}' to the personal library")
        return {"FINISHED"}


class LV_OT_import_model(Operator, ImportHelper):
    bl_idname = "lv.import_model"
    bl_label = "Import Model Into Fixture"
    bl_description = "Import GLB / GLTF / FBX / OBJ / STL geometry and parent it to the active fixture"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: StringProperty(default="*.glb;*.gltf;*.fbx;*.obj;*.stl", options={"HIDDEN"})

    def execute(self, context):
        root = active_fixture(context)
        if root is None:
            self.report({"ERROR"}, "Select a fixture first")
            return {"CANCELLED"}
        try:
            objs = build.import_model(self.filepath)
        except Exception as e:
            self.report({"ERROR"}, f"Import failed: {e}")
            return {"CANCELLED"}
        coll = build.get_collection(context.scene, build.FIXTURE_COLLECTION)
        for ob in objs:
            build._move_to_collection(ob, coll)
            if ob.parent is None or ob.parent not in objs:
                build._reparent_keep_world(ob, root)
        context.scene.lv_creator.model_file = self.filepath
        self.report({"INFO"}, f"Imported {len(objs)} objects; assign them to Base / Yoke / Head")
        return {"FINISHED"}


class LV_OT_assign_part(Operator):
    bl_idname = "lv.assign_part"
    bl_label = "Assign Selected Objects"
    bl_description = "Parent the selected objects to the active fixture's base, pan (yoke) or tilt (head) axis"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(items=(("BASE", "Base", "Stays fixed"), ("YOKE", "Yoke", "Rotates with pan"),
                              ("HEAD", "Head", "Rotates with pan and tilt")))

    def execute(self, context):
        root = active_fixture(context)
        if root is None:
            return {"CANCELLED"}
        fx = root.lv
        target = {"BASE": root, "YOKE": fx.pan_obj, "HEAD": fx.tilt_obj}[self.role]
        if target is None:
            self.report({"ERROR"}, "This fixture has no such axis")
            return {"CANCELLED"}
        skip = {root, fx.pan_obj, fx.tilt_obj, fx.light_obj, fx.lens_obj}
        for ob in context.selected_objects:
            if ob not in skip:
                build._reparent_keep_world(ob, target)
        return {"FINISHED"}


class LV_OT_creator_capture(Operator):
    bl_idname = "lv.creator_capture"
    bl_label = "Capture Geometry From Fixture"
    bl_description = "Store pivots, lens position and part names of the active fixture in the creator"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_fixture(context)
        if root is None:
            return {"CANCELLED"}
        fx, c = root.lv, context.scene.lv_creator
        inv = root.matrix_world.inverted()
        if fx.pan_obj:
            c.pan_pivot = inv @ fx.pan_obj.matrix_world.translation
        if fx.tilt_obj:
            c.tilt_pivot = inv @ fx.tilt_obj.matrix_world.translation
        if fx.light_obj:
            head = fx.tilt_obj or root
            c.lens_offset = head.matrix_world.inverted() @ fx.light_obj.matrix_world.translation
        c.use_pivots = True

        def parts(parent):
            return ", ".join(_SUFFIX.sub("", o.name) for o in parent.children
                             if o.type != "LIGHT" and o not in (fx.pan_obj, fx.tilt_obj, fx.lens_obj)) if parent else ""
        c.base_objects, c.yoke_objects, c.head_objects = parts(root), parts(fx.pan_obj), parts(fx.tilt_obj)
        return {"FINISHED"}


# ---- UI --------------------------------------------------------------------------------------------------------
class LV_UL_creator_modes(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False)
        layout.label(text=f"{len(item.channels)} ch")


class LV_UL_creator_channels(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=str(item.offset))
        layout.prop(item, "name", text="", emboss=False)
        layout.label(text=item.attribute + (" 16b" if item.fine else ""))


class LV_UL_creator_functions(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False)
        layout.label(text=f"{item.dmx_from}-{item.dmx_to}")


class _CreatorPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lighting"


class LV_PT_creator(_CreatorPanel, Panel):
    bl_label = "Fixture Creator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("lv.creator_load", icon="IMPORT")
        col.operator("lv.creator_save", icon="FILE_TICK")


class LV_PT_creator_general(_CreatorPanel, Panel):
    bl_label = "General"
    bl_parent_id = "LV_PT_creator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        c = context.scene.lv_creator
        col = self.layout.column(align=True)
        for p in ("manufacturer", "model", "kind", "description"):
            col.prop(c, p)
        col = self.layout.column(align=True)
        for p in ("width", "depth", "height", "weight", "power"):
            col.prop(c, p)


class LV_PT_creator_optical(_CreatorPanel, Panel):
    bl_label = "Optics"
    bl_parent_id = "LV_PT_creator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        c = context.scene.lv_creator
        col = self.layout.column(align=True)
        for p in ("source", "lumens", "candela", "beam_angle", "field_angle", "zoom_min", "zoom_max",
                  "color_temp", "cri", "lens", "ies_file"):
            col.prop(c, p)
        self.layout.label(text="Values are saved as 'user entered' data.", icon="INFO")


class LV_PT_creator_model(_CreatorPanel, Panel):
    bl_label = "3D Model & Axes"
    bl_parent_id = "LV_PT_creator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        c = context.scene.lv_creator
        lay = self.layout
        col = lay.column(align=True)
        col.prop(c, "pan_range")
        col.prop(c, "tilt_range")
        lay.label(text="1. Save, then add the fixture to the scene")
        lay.operator("lv.import_model", icon="IMPORT")
        row = lay.row(align=True)
        for role in ("BASE", "YOKE", "HEAD"):
            row.operator("lv.assign_part", text=role.title()).role = role
        lay.label(text="Move the Pan / Tilt empties and the light to the real pivots")
        lay.operator("lv.creator_capture", icon="EMPTY_AXIS")
        col = lay.column(align=True)
        col.prop(c, "model_file")
        col.prop(c, "use_pivots")
        sub = col.column(align=True)
        sub.enabled = c.use_pivots
        for p in ("pan_pivot", "tilt_pivot", "lens_offset"):
            sub.prop(c, p)
        for p in ("base_objects", "yoke_objects", "head_objects"):
            col.prop(c, p)


class LV_PT_creator_dmx(_CreatorPanel, Panel):
    bl_label = "DMX Modes & Channels"
    bl_parent_id = "LV_PT_creator"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        c = context.scene.lv_creator
        lay = self.layout
        row = lay.row()
        row.template_list("LV_UL_creator_modes", "", c, "modes", c, "mode_index", rows=3)
        col = row.column(align=True)
        col.operator("lv.creator_edit", icon="ADD", text="").action = "ADD_MODE"
        col.operator("lv.creator_edit", icon="REMOVE", text="").action = "REMOVE_MODE"
        row = lay.row(align=True)
        row.prop(c, "template", text="")
        row.operator("lv.creator_template", text="Add", icon="PRESET")
        if not 0 <= c.mode_index < len(c.modes):
            return
        mode = c.modes[c.mode_index]
        lay.label(text="Channels")
        row = lay.row()
        row.template_list("LV_UL_creator_channels", "", mode, "channels", mode, "channel_index", rows=6)
        col = row.column(align=True)
        col.operator("lv.creator_edit", icon="ADD", text="").action = "ADD_CHANNEL"
        col.operator("lv.creator_edit", icon="REMOVE", text="").action = "REMOVE_CHANNEL"
        if not 0 <= mode.channel_index < len(mode.channels):
            return
        ch = mode.channels[mode.channel_index]
        col = lay.column(align=True)
        for p in ("name", "attribute", "offset", "fine", "default"):
            col.prop(ch, p)
        lay.label(text="Ranges / functions")
        row = lay.row()
        row.template_list("LV_UL_creator_functions", "", ch, "functions", ch, "function_index", rows=3)
        col = row.column(align=True)
        col.operator("lv.creator_edit", icon="ADD", text="").action = "ADD_FUNCTION"
        col.operator("lv.creator_edit", icon="REMOVE", text="").action = "REMOVE_FUNCTION"
        if 0 <= ch.function_index < len(ch.functions):
            f = ch.functions[ch.function_index]
            col = lay.column(align=True)
            for p in ("name", "dmx_from", "dmx_to", "slot", "use_phys", "use_color"):
                col.prop(f, p)
            if f.use_color:
                col.prop(f, "color")
            if f.use_phys:
                col.prop(f, "phys_from")
                col.prop(f, "phys_to")


classes = (
    LV_CreatorFunction, LV_CreatorChannel, LV_CreatorMode, LV_Creator,
    LV_OT_creator_edit, LV_OT_creator_template, LV_OT_creator_load, LV_OT_creator_save, LV_OT_import_model,
    LV_OT_assign_part, LV_OT_creator_capture,
    LV_UL_creator_modes, LV_UL_creator_channels, LV_UL_creator_functions,
    LV_PT_creator, LV_PT_creator_general, LV_PT_creator_optical, LV_PT_creator_model, LV_PT_creator_dmx,
)


def register_types():
    bpy.types.Scene.lv_creator = PointerProperty(type=LV_Creator)


def unregister_types():
    del bpy.types.Scene.lv_creator
