import textwrap

from bpy.types import Panel, UIList

from ..core import optics
from . import runtime
from .operators import active_fixture, natural_key


class _Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lighting"


class LV_UL_mapping(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "protocol", text="")
        row.prop(item, "source", text="")
        row.label(text="", icon="FORWARD")
        row.prop(item, "target", text="")


def _status_line(lv):
    """(text, icon) for the one-line listening status."""
    eng, rx = runtime.engine, runtime.receiver
    if not lv.listen:
        return "Not listening (switched off)", "RADIOBUT_OFF"
    if not rx.running:
        return (rx.errors[0] if rx.errors else "Starting..."), ("ERROR" if rx.errors else "TIME")
    status = eng.status()
    ports = "/".join(str(p) for p in rx.ports.values())
    if status == "idle":
        return f"Listening on UDP {ports} - waiting for data", "TIME"
    if status == "lost":
        return "Signal lost", "ERROR"
    return f"Receiving {eng.packet_rate():.0f} packets/s from {eng.last_source_ip}", "CHECKMARK"


def _lamps(scene) -> list:
    return sorted((o for o in scene.objects if o.lv.is_fixture),
                  key=lambda o: (o.lv.universe, o.lv.address, natural_key(o.name)))


class LV_PT_rig(_Panel, Panel):
    bl_label = "Lighting Rig"

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        eng = runtime.engine
        text, icon = _status_line(lv)
        row = lay.row()
        row.label(text=text, icon=icon)
        row.prop(lv, "listen", text="Listen")
        for proto, uni in eng.ignored_universes():
            lay.label(text=f"Ignored {proto} universe {uni} (see DMX Input > Settings > Universes)", icon="ERROR")

        lamps = _lamps(context.scene)
        testing = sum(1 for o in lamps if o.lv.use_test)
        if testing:
            row = lay.row()
            row.alert = True
            row.label(text=f"{testing} lamp(s) in TEST MODE - ignoring DMX", icon="ERROR")
            row.operator("lv.test_off", text="Use DMX")
        row = lay.row(align=True)
        row.prop(lv, "show_beams", text="Show Beams", toggle=True)
        row.prop(lv, "beam_length", text="Length")
        active = set(eng.active_universes())
        patched = {o.lv.universe for o in lamps}
        if lamps and active and not active & patched:
            lay.label(text=f"Data on universe {', '.join(map(str, sorted(active)))} but no lamp is patched there",
                      icon="ERROR")
        if not lamps:
            lay.label(text="No lamps yet - add some under Add Lamps", icon="INFO")
        spans = [(o.lv.universe, o.lv.address, o.lv.address + _footprint(o) - 1, o) for o in lamps]
        col = lay.column(align=True)
        for ob in lamps[:100]:
            fx = ob.lv
            box = col.box()
            row = box.row(align=True)
            row.operator("lv.pick_fixture", text=ob.name, depress=ob.select_get()).name = ob.name
            row.prop(fx, "universe", text="U")
            row.prop(fx, "address", text="")
            rt = runtime.fixtures.get(ob.name)
            summary, sicon = runtime.lamp_summary(rt) if rt else ("not ready", "ERROR")
            sub = box.row()
            sub.scale_y = 0.75
            sub.label(text=summary, icon=sicon)
            mine = (fx.universe, fx.address, fx.address + _footprint(ob) - 1)
            clash = next((o.name for u, lo, hi, o in spans
                          if o is not ob and u == mine[0] and lo <= mine[2] and mine[1] <= hi), None)
            if clash:
                sub.label(text=f"overlaps {clash}", icon="ERROR")
        if len(lamps) > 100:
            lay.label(text=f"... {len(lamps) - 100} more lamps not listed")

        lay.separator()
        row = lay.row(align=True)
        row.prop(lv, "patch_universe", text="U")
        row.prop(lv, "patch_address", text="Start")
        row = lay.row(align=True)
        op = row.operator("lv.auto_patch", text="Patch All", icon="SORTSIZE")
        op.every_fixture = True
        op = row.operator("lv.auto_patch", text="Patch Selected")
        op.every_fixture = False
        row = lay.row(align=True)
        row.operator("lv.update_definitions", text="Update Lamp Definitions", icon="FILE_REFRESH").every_fixture = True


def _footprint(ob) -> int:
    rt = runtime.fixtures.get(ob.name)
    mode = rt.ftype.mode(rt.mode_name) if rt else None
    return max(mode.footprint, 1) if mode else 1


class LV_PT_library(_Panel, Panel):
    bl_label = "Add Lamps"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        lay.prop(lv, "lib_category", text="")
        lay.prop(lv, "lib_search", icon="VIEWZOOM")
        lay.prop(lv, "fixture_type", text="")
        ft = runtime.library.get(lv.fixture_type)
        if ft:
            lay.prop(lv, "fixture_mode", text="Mode")
            box = lay.box()
            col = box.column(align=True)
            o = ft.optical
            col.label(text=f"{ft.kind.replace('_', ' ').title()}  |  {o.get('source', '')}")
            col.label(text=f"Beam {o.get('beam_angle')}\u00b0  Field {o.get('field_angle')}\u00b0")
            prov = ft.provenance.get("optical", "estimated")
            icon = {"manufacturer": "CHECKMARK", "community": "INFO"}.get(prov, "ERROR")
            col.label(text=f"Optical data: {prov}", icon=icon)
            for line in textwrap.wrap(ft.notes, 48)[:6]:
                col.label(text=line)
        col = lay.column(align=True)
        row = col.row(align=True)
        row.prop(lv, "add_universe")
        row.prop(lv, "add_address")
        col.prop(lv, "add_count")
        lay.operator("lv.add_fixture", icon="LIGHT_SPOT")
        row = lay.row(align=True)
        row.operator("lv.import_gdtf", icon="IMPORT")
        row.operator("lv.import_mvr", icon="IMPORT")
        lay.operator("lv.export_mvr", icon="EXPORT")
        if runtime.library.is_user(lv.fixture_type):
            lay.operator("lv.delete_user_fixture", icon="TRASH")


class LV_PT_qlc(_Panel, Panel):
    bl_label = "QLC+ Fixtures"
    bl_parent_id = "LV_PT_library"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        lay.label(text="Use the same definitions as QLC+")
        lay.prop(lv, "qlc_query", icon="VIEWZOOM")
        lay.prop(lv, "qlc_dir")
        lay.operator("lv.search_qlc", icon="IMPORT")
        lay.operator("lv.import_qlc", text="Import .qxf Files...", icon="FILE_FOLDER")


class LV_PT_network(_Panel, Panel):
    bl_label = "DMX Input (sACN / Art-Net)"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        eng, rx = runtime.engine, runtime.receiver
        text, icon = _status_line(lv)
        box = lay.box()
        box.label(text=text, icon=icon)
        if rx.running:
            age = eng.seconds_since_last_packet()
            box.label(text="Listening on UDP " + ", ".join(f"{p} {n}" for p, n in rx.ports.items()))
            box.label(text=f"Last packet: {'never' if age is None else f'{age:.1f} s ago'}   "
                           f"Total {eng.total_packets}   Ignored {eng.rejected_packets}")
            box.label(text=f"Universes with data: {', '.join(map(str, eng.active_universes())) or '-'}")
            if rx.joined:
                box.label(text=f"sACN multicast groups joined: {len(rx.joined)}")
            for s in eng.sources():
                box.label(text=f"{s.name} [{s.protocol} {s.ip}] priority {s.priority}", icon="DOT")
                if s.seq_lost or s.seq_discarded:
                    box.label(text=f"   sequence: {s.seq_lost} lost, {s.seq_discarded} out of order")
        for e in rx.errors[-3:]:
            box.label(text=e, icon="ERROR")


class LV_PT_net_settings(_Panel, Panel):
    bl_label = "Settings"
    bl_parent_id = "LV_PT_network"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        col = lay.column(align=True)
        col.prop(lv, "protocol")
        row = col.row(align=True)
        row.prop(lv, "interface", text="Adapter")
        row.operator("lv.refresh_interfaces", text="", icon="FILE_REFRESH")
        col.prop(lv, "multicast")
        col.prop(lv, "universes")
        col.prop(lv, "artnet_offset")
        col = lay.column(align=True)
        col.prop(lv, "loss_policy")
        col.prop(lv, "refresh_hz")


class LV_PT_mapping(_Panel, Panel):
    bl_label = "Universe Mapping"
    bl_parent_id = "LV_PT_network"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lv = context.scene.lv
        row = self.layout.row()
        row.template_list("LV_UL_mapping", "", lv, "mapping", lv, "mapping_index", rows=3)
        col = row.column(align=True)
        col.operator("lv.map_add", icon="ADD", text="")
        col.operator("lv.map_remove", icon="REMOVE", text="")


class LV_PT_monitor(_Panel, Panel):
    bl_label = "DMX Monitor"
    bl_parent_id = "LV_PT_network"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        row = lay.row(align=True)
        row.prop(lv, "monitor_universe")
        row.prop(lv, "monitor_page")
        data = runtime.engine.universe_data(lv.monitor_universe)
        start = lv.monitor_page * 64
        col = lay.column(align=True)
        for r in range(8):
            base = start + r * 8
            vals = " ".join(f"{v:3d}" for v in data[base:base + 8])
            col.label(text=f"{base + 1:3d}: {vals}")


def _channels(address: int, ch) -> str:
    """Absolute DMX channel(s) of a fixture channel, e.g. "38" or "38+39" for 16-bit."""
    return "+".join(str(address + o - 1) for o in ch.offsets)


def _describe(ch, coarse: int, n: float, st) -> str:
    """What a channel value means for the lamp, e.g. pan degrees or the active range name."""
    a = ch.attribute
    if st is not None:
        if a == "Pan":
            return f"{st.pan:.1f}\u00b0"
        if a == "Tilt":
            return f"{st.tilt:.1f}\u00b0"
        if a == "Dimmer":
            return f"{st.intensity * 100:.0f}%"
        if a == "Zoom":
            return f"beam {st.beam_angle:.1f}\u00b0"
        if a == "Iris":
            return f"{st.iris * 100:.0f}% open"
        if a == "Frost":
            return f"{st.frost * 100:.0f}%"
        if a == "Shutter":
            return "closed" if st.shutter_closed else (f"strobe {st.strobe_hz:.1f} Hz" if st.strobe_hz else "open")
        if a == "Strobe":
            return f"{st.strobe_hz:.1f} Hz" if st.strobe_hz else "off"
    f = ch.function_at(coarse)
    return f.name if f is not None and f.name else f"{n * 100:.0f}%"


class LV_PT_inspector(_Panel, Panel):
    bl_label = "Fixture Inspector"

    @classmethod
    def poll(cls, context):
        return active_fixture(context) is not None

    def draw(self, context):
        lay = self.layout
        root = active_fixture(context)
        fx = root.lv
        rt = runtime.fixtures.get(root.name)
        ft = rt.ftype if rt else runtime.resolve_type(root)
        lay.label(text=root.name, icon="LIGHT_SPOT")
        if ft is None:
            lay.label(text="Fixture definition missing", icon="ERROR")
            return
        mode = ft.mode(fx.mode)
        lay.label(text=f"{ft.label}  -  {mode.name}")
        lay.label(text=f"Channels {fx.address}-{fx.address + mode.footprint - 1} of universe {fx.universe}")
        row = lay.row(align=True)
        row.prop(fx, "universe")
        row.prop(fx, "address")
        if fx.address + mode.footprint - 1 > 512:
            lay.label(text="Footprint exceeds universe", icon="ERROR")
        row = lay.row(align=True)
        row.operator("lv.set_mode", text="Change Mode", icon="PRESET")
        row.prop(fx, "fixture_id", text="ID")
        row = lay.row(align=True)
        row.prop(fx, "invert_pan")
        row.prop(fx, "invert_tilt")
        lay.label(text="Data: " + ", ".join(f"{k} {v}" for k, v in ft.provenance.items()))

        lay.prop(fx, "use_test", toggle=True, icon="PLAY")
        st = rt.state if rt else None
        box = lay.box()
        if fx.use_test:
            col = box.column(align=True)
            for ch in mode.channels:
                if ch.offsets[0] <= 32:
                    row = col.row(align=True)
                    row.label(text=f"{_channels(fx.address, ch)} {ch.attribute}")
                    row.prop(fx, "test_values", index=ch.offsets[0] - 1, text="", slider=True)
        else:
            data = runtime.engine.universe_data(fx.universe)
            col = box.column(align=True)
            for ch in mode.channels:
                raw = ch.raw(data, fx.address)
                c = ch.coarse(raw)
                meaning = textwrap.shorten(_describe(ch, c, ch.normalized(raw), st), 30, placeholder="...")
                col.label(text=f"{_channels(fx.address, ch)}  {ch.attribute}  {c}  \u2192  {meaning}")
        if st is not None:
            box = lay.box()
            col = box.column(align=True)
            reason = st.dark_reason()
            col.label(text=f"Dark: {reason}" if reason else "Emitting", icon="LIGHT_DATA" if reason else "LIGHT_SPOT")
            col.label(text=f"Pan {st.pan:.1f}\u00b0  Tilt {st.tilt:.1f}\u00b0")
            col.label(text=f"Dimmer {st.intensity * 100:.0f}%  RGB {st.rgb[0]:.2f} {st.rgb[1]:.2f} {st.rgb[2]:.2f}")
            col.label(text=f"Beam {st.beam_angle:.1f}\u00b0  Field {st.field_angle:.1f}\u00b0  Iris {st.iris * 100:.0f}%")
            col.label(text=f"Focus {st.focus:.2f}  Frost {st.frost:.2f}")
            col.label(text=f"Shutter {'closed' if st.shutter_closed else 'open'}  Strobe {st.strobe_hz:.1f} Hz")
            col.label(text=f"Gobo slot {st.gobo_slot}  rot {st.gobo_rot_dps:.0f}\u00b0/s  Prism {'on' if st.prism else 'off'}")


class LV_PT_calibration(_Panel, Panel):
    bl_label = "Calibration"
    bl_parent_id = "LV_PT_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        root = active_fixture(context)
        ft = runtime.resolve_type(root)
        col = lay.column(align=True)
        col.prop(lv, "cal_distance")
        col.prop(lv, "cal_diameter")
        col.prop(lv, "cal_lux")
        if ft:
            rep = optics.calibration_report(ft.optical, lv.cal_distance, lv.cal_diameter, lv.cal_lux)
            box = lay.box()
            box.label(text=f"Model: beam \u00d8 {rep['predicted_diameter']:.2f} m, {rep['predicted_lux']:.0f} lx")
            box.label(text=f"Factors: beam x{rep['beam_scale']:.2f}, intensity x{rep['intensity_scale']:.2f}")
        lay.operator("lv.apply_calibration", icon="CHECKMARK")
        col = lay.column(align=True)
        col.prop(root.lv, "beam_scale")
        col.prop(root.lv, "intensity_scale")
        lay.prop(lv, "exposure")
        lay.label(text="Light output conversion is not photometrically validated.", icon="INFO")


class LV_PT_gobos(_Panel, Panel):
    bl_label = "Gobos"
    bl_parent_id = "LV_PT_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay = self.layout
        fx = active_fixture(context).lv
        for g in fx.gobos:
            row = lay.row(align=True)
            row.prop(g, "slot")
            row.prop(g, "image", text="")
        lay.operator("lv.import_gobo", icon="IMAGE_DATA")
        lay.label(text="Projection needs Cycles (High Quality).", icon="INFO")


class LV_PT_atmosphere(_Panel, Panel):
    bl_label = "Atmosphere & Rendering"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay, lv = self.layout, context.scene.lv
        lay.prop(lv, "quality", expand=True)
        col = lay.column(align=True)
        col.prop(lv, "haze_level", slider=True)
        col.prop(lv, "haze_anisotropy")
        lay.operator("lv.fit_haze", icon="FULLSCREEN_ENTER")
        lay.prop(lv, "exposure")


classes = (LV_UL_mapping, LV_PT_rig, LV_PT_library, LV_PT_qlc, LV_PT_network, LV_PT_net_settings, LV_PT_mapping, LV_PT_monitor, LV_PT_inspector,
           LV_PT_calibration, LV_PT_gobos, LV_PT_atmosphere)
