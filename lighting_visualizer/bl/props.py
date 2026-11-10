import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       IntVectorProperty, PointerProperty, StringProperty)
from bpy.types import PropertyGroup

from . import runtime

_type_items: list = []
_mode_items: list = []
_category_items: list = []


def _touch(self, context):
    runtime.request_refresh()


def _enum_categories(self, context):
    _category_items[:] = [("ALL", "All categories", "")] + [(c, c, "") for c in runtime.library.categories()]
    return _category_items


def _enum_types(self, context):
    q = self.lib_search.lower().strip()
    cat = self.lib_category
    _type_items[:] = [(t.id, f"{t.category} / {t.label}" + (" [community]" if t.provenance.get("dmx") == "community" else ""),
                       t.description or t.notes)
                      for t in runtime.library.all()
                      if (cat == "ALL" or t.category == cat) and q in f"{t.category} {t.label}".lower()]
    return _type_items or [("NONE", "No match", "")]


def _enum_modes(self, context):
    t = runtime.library.get(self.fixture_type)
    _mode_items[:] = [(m.name, f"{m.name} ({m.footprint} ch)", "") for m in t.modes] if t else []
    return _mode_items or [("NONE", "-", "")]


def _type_update(self, context):
    t = runtime.library.get(self.fixture_type)
    if t and t.modes:
        self.fixture_mode = t.modes[0].name


def _enum_interfaces(self, context):
    return runtime.interfaces


def _use_test_update(self, context):
    if self.use_test:
        ob = self.id_data
        ft = runtime.resolve_type(ob)
        mode = ft.mode(self.mode) if ft else None
        if mode:
            defaults = [0] * 32
            for c in mode.channels:
                for i, off in enumerate(c.offsets):
                    if off <= 32:
                        defaults[off - 1] = (c.default >> (8 * (len(c.offsets) - 1 - i))) & 0xFF
            self.test_values = defaults
    runtime.request_refresh()


def _quality_update(self, context):
    from . import atmosphere
    atmosphere.apply_quality(context.scene)


def _haze_update(self, context):
    from . import atmosphere
    atmosphere.apply_haze(context.scene)


class LV_UniverseMap(PropertyGroup):
    protocol: EnumProperty(name="Protocol", default="any", items=(
        ("any", "Any", "sACN and Art-Net"), ("sacn", "sACN", ""), ("artnet", "Art-Net", "")))
    source: IntProperty(name="Incoming", description="Universe number as sent by the console (Art-Net: port-address)",
                        min=0, max=63999, default=1)
    target: IntProperty(name="Internal", description="Universe fixtures are patched to", min=1, max=63999, default=1)


class LV_GoboAssign(PropertyGroup):
    slot: IntProperty(name="Slot", min=1, default=1, update=_touch)
    image: PointerProperty(name="Image", type=bpy.types.Image, update=_touch)


class LV_Fixture(PropertyGroup):
    is_fixture: BoolProperty()
    type_id: StringProperty()
    type_json: StringProperty(description="Fixture definition stored inside the .blend")
    mode: StringProperty(update=_touch)
    universe: IntProperty(name="Universe", min=1, max=63999, default=1, update=_touch)
    address: IntProperty(name="Address", min=1, max=512, default=1, update=_touch)
    fixture_id: StringProperty(name="Fixture ID")
    pan_obj: PointerProperty(type=bpy.types.Object)
    tilt_obj: PointerProperty(type=bpy.types.Object)
    light_obj: PointerProperty(type=bpy.types.Object)
    lens_obj: PointerProperty(type=bpy.types.Object)
    invert_pan: BoolProperty(name="Invert Pan", update=_touch)
    invert_tilt: BoolProperty(name="Invert Tilt", update=_touch)
    intensity_scale: FloatProperty(name="Intensity Calibration", min=0.0, soft_max=10.0, default=1.0, update=_touch,
                                   description="Multiplier found by comparing with a light meter")
    beam_scale: FloatProperty(name="Beam Calibration", min=0.1, soft_max=5.0, default=1.0, update=_touch,
                              description="Multiplier on beam / field angle found by measuring the real beam")
    use_test: BoolProperty(name="Test Mode", description="Drive this fixture from the sliders instead of the network",
                           update=_use_test_update)
    test_values: IntVectorProperty(size=32, min=0, max=255, update=_touch)
    gobos: CollectionProperty(type=LV_GoboAssign)


class LV_Scene(PropertyGroup):
    # library / add
    lib_search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"})
    lib_category: EnumProperty(name="Category", items=_enum_categories)
    fixture_type: EnumProperty(name="Fixture", items=_enum_types, update=_type_update)
    fixture_mode: EnumProperty(name="Mode", items=_enum_modes)
    add_universe: IntProperty(name="Universe", min=1, max=63999, default=1)
    add_address: IntProperty(name="Address", min=1, max=512, default=1)
    add_count: IntProperty(name="Count", min=1, max=500, default=1)
    qlc_query: StringProperty(name="Search", description="Words from the QLC+ fixture name, e.g. 'mac 2000 profile'")
    qlc_dir: StringProperty(name="Folder", subtype="DIR_PATH",
                            description="QLC+ fixtures folder; empty = look in the usual install locations")
    # network
    listen: BoolProperty(name="Listen for DMX", default=True,
                         description="Receive sACN / Art-Net continuously; settings changes apply automatically")
    protocol: EnumProperty(name="Protocol", default="auto", items=(
        ("auto", "Auto", "Listen for sACN and Art-Net"), ("sacn", "sACN", "ANSI E1.31 only"),
        ("artnet", "Art-Net", "Art-Net only")))
    interface: EnumProperty(name="Adapter", items=_enum_interfaces,
                            description="Adapter used for sACN multicast subscriptions")
    multicast: BoolProperty(name="sACN Multicast", default=True)
    universes: StringProperty(name="Universes", default="",
                              description="Only accept these universes, e.g. 1-4,10. Empty = every universe the lamps use")
    artnet_offset: IntProperty(name="Art-Net Offset", default=1, min=0, max=64,
                               description="Added to the Art-Net port-address; grandMA universe 1 is Art-Net universe 0")
    loss_policy: EnumProperty(name="On Signal Loss", default="hold", items=(
        ("hold", "Hold Last Look", ""), ("blackout", "Blackout", "")))
    refresh_hz: IntProperty(name="Refresh Rate", min=5, max=60, default=30, description="Scene update rate in Hz")
    mapping: CollectionProperty(type=LV_UniverseMap)
    mapping_index: IntProperty()
    monitor_universe: IntProperty(name="Universe", min=1, max=63999, default=1)
    monitor_page: IntProperty(name="Page", min=0, max=7, default=0)
    # rendering / atmosphere
    show_beams: BoolProperty(name="Show Beams", default=True,
                             description="Draw each lamp's beam in the viewport (visible in every shading mode)")
    beam_length: FloatProperty(name="Beam Length", unit="LENGTH", min=0.5, soft_max=30.0, default=8.0)
    quality: EnumProperty(name="Mode", default="REALTIME", update=_quality_update, items=(
        ("REALTIME", "Real-Time", "EEVEE, simplified volumetrics, no gobo projection"),
        ("HIGH", "High Quality", "Cycles, full volumetrics and gobo projection")))
    haze_level: FloatProperty(name="Fog", min=0.0, max=1.0, default=0.0, subtype="FACTOR", update=_haze_update,
                              description="0 = clear air, 1 = dense fog; the scale is exponential so light haze is easy to set")
    haze_anisotropy: FloatProperty(name="Anisotropy", min=-0.9, max=0.9, default=0.3, update=_haze_update)
    exposure: FloatProperty(name="Fixture Output Scale", min=0.0, soft_max=10.0, default=1.0, update=_touch,
                            description="Global multiplier on all fixture output (calibration)")
    # patching
    patch_universe: IntProperty(name="Universe", min=1, max=63999, default=1)
    patch_address: IntProperty(name="Address", min=1, max=512, default=1)
    # calibration
    cal_distance: FloatProperty(name="Distance", unit="LENGTH", min=0.1, default=10.0)
    cal_diameter: FloatProperty(name="Measured Beam Diameter", unit="LENGTH", min=0.0, default=0.0)
    cal_lux: FloatProperty(name="Measured Illuminance (lx)", min=0.0, default=0.0)


classes = (LV_UniverseMap, LV_GoboAssign, LV_Fixture, LV_Scene)


def register_types():
    bpy.types.Object.lv = PointerProperty(type=LV_Fixture)
    bpy.types.Scene.lv = PointerProperty(type=LV_Scene)


def unregister_types():
    del bpy.types.Object.lv
    del bpy.types.Scene.lv
