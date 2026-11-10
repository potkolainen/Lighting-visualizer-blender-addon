import re
import tempfile
import uuid
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix

from ..core import gdtf, mvr, optics, qlc
from ..core.fixture import slugify
from . import atmosphere, build, runtime

MVR_COLLECTION = "MVR Scene"


def fixture_root(ob):
    while ob is not None:
        if ob.lv.is_fixture:
            return ob
        ob = ob.parent
    return None


def natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def selected_fixtures(context) -> list:
    roots = {fixture_root(o) for o in context.selected_objects}
    return sorted((r for r in roots if r is not None), key=lambda o: natural_key(o.name))


def active_fixture(context):
    return fixture_root(context.active_object)


def _select_only(context, objects):
    for o in context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    if objects:
        context.view_layer.objects.active = objects[-1]


class LV_OT_add_fixture(Operator):
    bl_idname = "lv.add_fixture"
    bl_label = "Add Fixture"
    bl_description = "Add the selected library fixture at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        lv = context.scene.lv
        ftype = runtime.library.get(lv.fixture_type)
        if ftype is None or not ftype.modes:
            self.report({"ERROR"}, "Choose a fixture from the library first")
            return {"CANCELLED"}
        mode = ftype.mode(lv.fixture_mode)
        footprint = max(mode.footprint, 1)
        universe, address = lv.add_universe, lv.add_address
        origin = context.scene.cursor.location
        new = []
        for i in range(lv.add_count):
            if address + footprint - 1 > 512:
                universe, address = universe + 1, 1
            name = f"{ftype.model} {i + 1}" if lv.add_count > 1 else ftype.model
            root = build.create_fixture(context, ftype, mode.name, universe, address, name,
                                        (origin.x + i * 1.0, origin.y, origin.z))
            new.append(root)
            address += footprint
        lv.add_universe, lv.add_address = universe, min(address, 512)
        _select_only(context, new)
        runtime.request_rebuild()
        return {"FINISHED"}


class LV_OT_set_mode(Operator):
    bl_idname = "lv.set_mode"
    bl_label = "Change DMX Mode"
    bl_options = {"REGISTER", "UNDO"}

    def _items(self, context):
        root = active_fixture(context)
        ft = runtime.resolve_type(root) if root else None
        LV_OT_set_mode._cache = [(m.name, f"{m.name} ({m.footprint} ch)", "") for m in ft.modes] if ft else []
        return LV_OT_set_mode._cache or [("NONE", "-", "")]

    mode: EnumProperty(name="Mode", items=_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        root = active_fixture(context)
        if root is None or self.mode == "NONE":
            return {"CANCELLED"}
        root.lv.mode = self.mode
        runtime.request_rebuild()
        return {"FINISHED"}


class LV_OT_refresh_interfaces(Operator):
    bl_idname = "lv.refresh_interfaces"
    bl_label = "Refresh Adapters"

    def execute(self, context):
        runtime.refresh_interfaces()
        return {"FINISHED"}


class LV_OT_map_add(Operator):
    bl_idname = "lv.map_add"
    bl_label = "Add Universe Mapping"

    def execute(self, context):
        lv = context.scene.lv
        lv.mapping.add()
        lv.mapping_index = len(lv.mapping) - 1
        return {"FINISHED"}


class LV_OT_map_remove(Operator):
    bl_idname = "lv.map_remove"
    bl_label = "Remove Universe Mapping"

    def execute(self, context):
        lv = context.scene.lv
        if 0 <= lv.mapping_index < len(lv.mapping):
            lv.mapping.remove(lv.mapping_index)
            lv.mapping_index = max(lv.mapping_index - 1, 0)
        return {"FINISHED"}


class LV_OT_pick_fixture(Operator):
    bl_idname = "lv.pick_fixture"
    bl_label = "Select Fixture"
    bl_description = "Select this fixture"

    name: StringProperty()

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if ob is None:
            return {"CANCELLED"}
        _select_only(context, [ob])
        return {"FINISHED"}


class LV_OT_test_off(Operator):
    bl_idname = "lv.test_off"
    bl_label = "Use DMX Input"
    bl_description = "Switch every lamp out of test mode so it follows the network again"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for ob in context.scene.objects:
            if ob.lv.is_fixture and ob.lv.use_test:
                ob.lv.use_test = False
        runtime.request_refresh()
        return {"FINISHED"}


class LV_OT_update_definitions(Operator):
    bl_idname = "lv.update_definitions"
    bl_label = "Update Definitions"
    bl_description = ("Reload the channel definitions of the lamps from the fixture library (e.g. after a newer add-on "
                      "version); address, universe and mode are kept")
    bl_options = {"REGISTER", "UNDO"}

    every_fixture: bpy.props.BoolProperty(name="All Fixtures", default=True)

    def execute(self, context):
        roots = ([o for o in context.scene.objects if o.lv.is_fixture] if self.every_fixture
                 else selected_fixtures(context))
        updated = skipped = 0
        for root in roots:
            ftype = runtime.library.get(root.lv.type_id)
            if ftype is None or not ftype.modes:
                skipped += 1
                continue
            root.lv.type_json = ftype.to_json()
            if root.lv.mode not in {m.name for m in ftype.modes}:
                root.lv.mode = ftype.modes[0].name
            updated += 1
        runtime.request_rebuild()
        self.report({"INFO"}, f"Updated {updated} lamp(s)" + (f", {skipped} not in the library" if skipped else ""))
        return {"FINISHED"}


class LV_OT_auto_patch(Operator):
    bl_idname = "lv.auto_patch"
    bl_label = "Auto Patch"
    bl_description = "Patch fixtures one after another (by name), starting at the universe / address below"
    bl_options = {"REGISTER", "UNDO"}

    every_fixture: bpy.props.BoolProperty(name="All Fixtures", default=False)

    def execute(self, context):
        lv = context.scene.lv
        universe, address = lv.patch_universe, lv.patch_address
        roots = (sorted((o for o in context.scene.objects if o.lv.is_fixture), key=lambda o: natural_key(o.name))
                 if self.every_fixture else selected_fixtures(context))
        for root in roots:
            ft = runtime.resolve_type(root)
            mode = ft.mode(root.lv.mode) if ft else None
            footprint = max(mode.footprint, 1) if mode else 1
            if address + footprint - 1 > 512:
                universe, address = universe + 1, 1
            root.lv.universe, root.lv.address = universe, address
            address += footprint
        lv.patch_universe, lv.patch_address = universe, min(address, 512)
        return {"FINISHED"}


class LV_OT_select_fixtures(Operator):
    bl_idname = "lv.select_fixtures"
    bl_label = "Select Fixtures"
    bl_options = {"REGISTER", "UNDO"}

    by_universe: IntProperty(default=0, description="0 = all fixtures")

    def execute(self, context):
        found = [o for o in context.scene.objects if o.lv.is_fixture
                 and (self.by_universe == 0 or o.lv.universe == self.by_universe)]
        _select_only(context, found)
        return {"FINISHED"}


class LV_OT_import_gdtf(Operator, ImportHelper):
    bl_idname = "lv.import_gdtf"
    bl_label = "Import GDTF Fixture"
    bl_description = "Import a GDTF file into the personal fixture library"
    filter_glob: StringProperty(default="*.gdtf", options={"HIDDEN"})

    def execute(self, context):
        try:
            ftype = gdtf.import_gdtf(self.filepath, runtime.user_dir("gdtf"))
            runtime.library.save_user(ftype)
        except Exception as e:
            self.report({"ERROR"}, f"GDTF import failed: {e}")
            return {"CANCELLED"}
        lv = context.scene.lv
        lv.lib_search = ""
        try:
            lv.fixture_type = ftype.id
        except TypeError:
            pass
        self.report({"INFO"}, f"Imported {ftype.label} ({len(ftype.modes)} modes)")
        return {"FINISHED"}


def _import_qxf_files(operator, context, paths) -> int:
    lv, first, failed = context.scene.lv, None, 0
    for path in paths:
        try:
            ftype = qlc.import_qxf(path)
            runtime.library.save_user(ftype)
            first = first or ftype
        except Exception as e:
            failed += 1
            operator.report({"WARNING"}, f"{Path(path).name}: {e}")
    if first is not None:
        lv.lib_search = ""
        try:
            lv.fixture_type = first.id
        except TypeError:
            pass
    return failed


class LV_OT_import_qlc(Operator, ImportHelper):
    bl_idname = "lv.import_qlc"
    bl_label = "Import QLC+ Fixture Files"
    bl_description = "Import QLC+ fixture definitions (.qxf) into the personal fixture library"
    filter_glob: StringProperty(default="*.qxf", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def execute(self, context):
        paths = [Path(self.directory) / f.name for f in self.files] or [Path(self.filepath)]
        failed = _import_qxf_files(self, context, paths)
        self.report({"INFO"}, f"Imported {len(paths) - failed} QLC+ fixture(s)")
        return {"FINISHED"} if failed < len(paths) else {"CANCELLED"}


class LV_OT_search_qlc(Operator):
    bl_idname = "lv.search_qlc"
    bl_label = "Import Matching QLC+ Fixtures"
    bl_description = "Find fixtures in the QLC+ library by name and import all matches (up to 40)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        lv = context.scene.lv
        folder = bpy.path.abspath(lv.qlc_dir) if lv.qlc_dir else ""
        dirs = [Path(folder)] if folder else qlc.default_fixture_dirs()
        if not any(d.is_dir() for d in dirs):
            self.report({"ERROR"}, "QLC+ fixtures folder not found - set it in the Folder field")
            return {"CANCELLED"}
        found = qlc.search_fixtures(lv.qlc_query, dirs, limit=40)
        if not found:
            self.report({"WARNING"}, f"No QLC+ fixture matches '{lv.qlc_query}'")
            return {"CANCELLED"}
        failed = _import_qxf_files(self, context, found)
        self.report({"INFO"}, f"Imported {len(found) - failed} QLC+ fixture(s)")
        return {"FINISHED"}


class LV_OT_import_mvr(Operator, ImportHelper):
    bl_idname = "lv.import_mvr"
    bl_label = "Import MVR"
    bl_description = "Import fixtures, trusses, objects and DMX patch from an MVR file"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: StringProperty(default="*.mvr", options={"HIDDEN"})

    def execute(self, context):
        path = Path(self.filepath)
        try:
            scene = mvr.import_mvr(path, runtime.user_dir("mvr") / slugify(path.stem))
        except Exception as e:
            self.report({"ERROR"}, f"MVR import failed: {e}")
            return {"CANCELLED"}
        types, missing, unpatched, skipped = {}, 0, 0, 0
        fallback = runtime.library.get("generic.led_par")
        objs_coll = build.get_collection(context.scene, MVR_COLLECTION)
        for item in scene.items:
            if item.kind == "fixture":
                ftype = types.get(item.gdtf_spec)
                if ftype is None:
                    spec = Path(scene.extract_dir) / item.gdtf_spec
                    try:
                        ftype = gdtf.import_gdtf(spec, runtime.user_dir("gdtf"))
                        runtime.library.save_user(ftype)
                    except Exception:
                        ftype, missing = fallback, missing + 1
                    types[item.gdtf_spec] = ftype
                unpatched += item.universe == 0
                root = build.create_fixture(context, ftype, item.gdtf_mode, max(item.universe, 1),
                                            max(item.address, 1), item.name)
                root.matrix_world = build.fixture_matrix(item.rotation, item.position)
                root.lv.fixture_id = item.fixture_id
                root["mvr_uuid"], root["lv_group"], root["lv_layer"] = item.uuid, item.group, item.layer
            else:
                empty = bpy.data.objects.new(item.name, None)
                empty.empty_display_type, empty.empty_display_size = "CUBE", 0.25
                objs_coll.objects.link(empty)
                empty.matrix_world = build.fixture_matrix(item.rotation, item.position)
                empty["lv_group"], empty["lv_layer"], empty["mvr_uuid"] = item.group, item.layer, item.uuid
                for fname in item.geometry_files:
                    model = Path(scene.extract_dir) / fname
                    if model.suffix.lower() not in build.MODEL_EXTENSIONS or not model.is_file():
                        skipped += 1
                        continue
                    try:
                        for ob in build.import_model(str(model)):
                            if ob.parent is None:
                                ob.parent = empty
                            build._move_to_collection(ob, objs_coll)
                    except Exception:
                        skipped += 1
        runtime.request_rebuild()
        msg = f"Imported {len(scene.fixtures)} fixtures, {len(scene.items) - len(scene.fixtures)} other objects"
        if missing or unpatched or skipped:
            msg += f" ({missing} without GDTF file, {unpatched} unpatched, {skipped} geometries skipped)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}


def _world_rows(matrix):
    """MVR rotation rows (images of the local axes, scale removed) and position of a Blender world matrix."""
    rot = matrix.to_3x3().normalized()
    return tuple(tuple(rot.col[i]) for i in range(3)), tuple(matrix.translation)


def _mvr_uuid(ob) -> str:
    if not ob.get("mvr_uuid"):
        ob["mvr_uuid"] = str(uuid.uuid4())
    return ob["mvr_uuid"]


def _export_glb(context, ob, dest: Path, apply_modifiers: bool) -> bool:
    """Write one object as GLB at its local origin (scale baked in, modifiers applied on request)."""
    scene, view_layer = context.scene, context.view_layer
    if apply_modifiers or ob.type != "MESH":
        mesh = bpy.data.meshes.new_from_object(ob.evaluated_get(context.evaluated_depsgraph_get()))
    else:
        mesh = ob.data.copy()
    if mesh is None:
        return False
    sx, sy, sz = ob.matrix_world.to_scale()
    mesh.transform(Matrix.Diagonal((sx, sy, sz, 1.0)))
    tmp = bpy.data.objects.new(ob.name, mesh)
    coll = bpy.data.collections.new("LV MVR Export")
    scene.collection.children.link(coll)
    coll.objects.link(tmp)
    selected, active = [o for o in view_layer.objects if o.select_get()], view_layer.objects.active
    try:
        for o in view_layer.objects:
            o.select_set(False)
        tmp.select_set(True)
        view_layer.objects.active = tmp
        bpy.ops.export_scene.gltf(filepath=str(dest), export_format="GLB", use_selected=True, export_apply=False)
        return dest.is_file()
    except Exception as e:
        print(f"[lighting_visualizer] glTF export of {ob.name} failed: {e}")
        return False
    finally:
        for o in view_layer.objects:
            o.select_set(o in selected)
        view_layer.objects.active = active
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(coll)


class LV_OT_export_mvr(Operator, ExportHelper):
    bl_idname = "lv.export_mvr"
    bl_label = "Export MVR"
    bl_description = "Export fixtures with their DMX patch, and optionally scene meshes, to an MVR file"
    filename_ext = ".mvr"
    filter_glob: StringProperty(default="*.mvr", options={"HIDDEN"})
    selected_only: BoolProperty(name="Selected Only", default=False)
    export_meshes: BoolProperty(name="Export Meshes", default=True,
                                description="Also write mesh objects that are not fixtures as MVR scene objects (glTF)")
    apply_modifiers: BoolProperty(name="Apply Modifiers", default=True,
                                  description="Apply all modifiers (as evaluated in the viewport) to exported meshes")

    def execute(self, context):
        scene = context.scene
        pool = context.selected_objects if self.selected_only else list(scene.objects)
        fixtures = sorted({fixture_root(o) for o in pool} - {None}, key=lambda o: natural_key(o.name))
        items, files, specs, generated, skipped = [], {}, {}, 0, 0
        for root in fixtures:
            ftype = runtime.resolve_type(root)
            if ftype is None or not ftype.modes:
                skipped += 1
                continue
            if ftype.id not in specs:
                name, n = slugify(ftype.label) + ".gdtf", 1
                while name in files:
                    n += 1
                    name = f"{slugify(ftype.label)}_{n}.gdtf"
                src = ftype.geometry.get("gdtf_file")
                if src and Path(src).is_file():
                    files[name] = Path(src)
                else:
                    files[name] = gdtf.export_gdtf(ftype)
                    generated += 1
                specs[ftype.id] = name
            rows, pos = _world_rows(root.matrix_world)
            items.append(mvr.MvrItem(
                kind="fixture", name=root.name, uuid=_mvr_uuid(root), layer=root.get("lv_layer", ""),
                group=root.get("lv_group", ""), position=pos, rotation=rows, gdtf_spec=specs[ftype.id],
                gdtf_mode=root.lv.mode, fixture_id=root.lv.fixture_id, universe=root.lv.universe,
                address=root.lv.address))
        objects = 0
        if self.export_meshes:
            with tempfile.TemporaryDirectory() as tmp:
                for ob in pool:
                    if ob.type not in GEOMETRY_TYPES or fixture_root(ob) is not None or not ob.visible_get():
                        continue
                    ident = _mvr_uuid(ob)
                    fname = f"{slugify(ob.name)}_{ident[:8]}.glb"
                    if not _export_glb(context, ob, Path(tmp) / fname, self.apply_modifiers):
                        skipped += 1
                        continue
                    files[fname] = (Path(tmp) / fname).read_bytes()
                    rows, pos = _world_rows(ob.matrix_world)
                    items.append(mvr.MvrItem(kind="object", name=ob.name, uuid=ident, layer=ob.get("lv_layer", ""),
                                             group=ob.get("lv_group", ""), position=pos, rotation=rows,
                                             geometry_files=[fname]))
                    objects += 1
        if not items:
            self.report({"ERROR"}, "Nothing to export")
            return {"CANCELLED"}
        try:
            mvr.export_mvr(self.filepath, items, files)
        except OSError as e:
            self.report({"ERROR"}, f"MVR export failed: {e}")
            return {"CANCELLED"}
        msg = f"Exported {len(items) - objects} fixtures, {objects} objects"
        if generated or skipped:
            msg += f" ({generated} GDTF files generated from the library, {skipped} skipped)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class LV_OT_delete_user_fixture(Operator):
    bl_idname = "lv.delete_user_fixture"
    bl_label = "Delete From Personal Library"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        fid = context.scene.lv.fixture_type
        if not runtime.library.delete_user(fid):
            self.report({"WARNING"}, "Only personal library fixtures can be deleted")
            return {"CANCELLED"}
        return {"FINISHED"}


class LV_OT_fit_haze(Operator):
    bl_idname = "lv.fit_haze"
    bl_label = "Fit Fog to Scene"
    bl_description = "Resize the fog box to surround everything in the scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if atmosphere.fit_haze(context.scene) is None:
            self.report({"INFO"}, "Raise the Fog slider first")
            return {"CANCELLED"}
        return {"FINISHED"}


class LV_OT_apply_calibration(Operator):
    bl_idname = "lv.apply_calibration"
    bl_label = "Apply Calibration"
    bl_description = "Store correction factors on the active fixture from the measured values"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = active_fixture(context)
        ft = runtime.resolve_type(root) if root else None
        if ft is None:
            return {"CANCELLED"}
        lv = context.scene.lv
        rep = optics.calibration_report(ft.optical, lv.cal_distance, lv.cal_diameter, lv.cal_lux)
        root.lv.beam_scale = min(max(rep["beam_scale"], 0.1), 10.0)
        root.lv.intensity_scale = rep["intensity_scale"]
        return {"FINISHED"}


class LV_OT_import_gobo(Operator, ImportHelper):
    bl_idname = "lv.import_gobo"
    bl_label = "Import Gobo Image"
    bl_description = "Assign an image to a gobo slot of the active fixture (projected with Cycles)"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp;*.exr", options={"HIDDEN"})
    slot: IntProperty(name="Gobo Slot", min=1, default=1, description="Slot number as selected by the gobo channel")

    def execute(self, context):
        root = active_fixture(context)
        if root is None:
            self.report({"ERROR"}, "Select a fixture first")
            return {"CANCELLED"}
        image = bpy.data.images.load(self.filepath, check_existing=True)
        entry = next((g for g in root.lv.gobos if g.slot == self.slot), None) or root.lv.gobos.add()
        entry.slot, entry.image = self.slot, image
        runtime.request_refresh()
        return {"FINISHED"}


classes = (
    LV_OT_add_fixture, LV_OT_set_mode, LV_OT_pick_fixture, LV_OT_test_off, LV_OT_update_definitions, LV_OT_refresh_interfaces, LV_OT_map_add,
    LV_OT_map_remove, LV_OT_auto_patch, LV_OT_select_fixtures, LV_OT_import_gdtf, LV_OT_import_qlc,
    LV_OT_search_qlc, LV_OT_import_mvr, LV_OT_export_mvr,
    LV_OT_delete_user_fixture, LV_OT_fit_haze, LV_OT_apply_calibration, LV_OT_import_gobo,
)
