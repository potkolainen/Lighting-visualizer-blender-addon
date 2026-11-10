"""Creating fixture objects in the scene and applying evaluated DMX state to them."""
from __future__ import annotations

import math
import re
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from ..core import model3ds, optics
from ..core.fixture import FixtureType

FIXTURE_COLLECTION = "Lighting Fixtures"
BODY_MATERIAL = "LV Fixture Body"
GOBO_NODE_PREFIX = "LV Gobo "
BLADE_PREFIX = "LV Blade "
PLANE_NODE = "LV Plane"
MODEL_EXTENSIONS = {".glb", ".gltf", ".fbx", ".obj", ".stl"}
PRISM_FACETS = 3
PRISM_OFFSET = 0.8   # facet tilt as a fraction of the beam angle (estimated)
LENS_NODE = "LV Lens Emission"
LENS_EMISSION_MAX = 1.0   # emission strength at full output


def get_collection(scene, name: str):
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in {c.name for c in scene.collection.children_recursive}:
        scene.collection.children.link(coll)
    return coll


def _body_material():
    mat = bpy.data.materials.get(BODY_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(BODY_MATERIAL)
        mat.diffuse_color = (0.05, 0.05, 0.055, 1.0)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.05, 0.05, 0.055, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def _mesh_object(name, coll, parent, build):
    bm = bmesh.new()
    build(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(_body_material())
    ob = bpy.data.objects.new(name, mesh)
    ob.parent = parent
    ob.visible_shadow = False
    coll.objects.link(ob)
    return ob


def _box(bm, size, center):
    res = bmesh.ops.create_cube(bm, size=1.0)
    for v in res["verts"]:
        v.co = Vector((v.co.x * size[0] + center[0], v.co.y * size[1] + center[1], v.co.z * size[2] + center[2]))


def _cylinder(bm, radius, depth, center):
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=radius, radius2=radius, depth=depth)
    for v in res["verts"]:
        v.co = v.co + Vector(center)


def _empty(name, coll, parent, location, display="PLAIN_AXES", size=0.1):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = display
    ob.empty_display_size = size
    ob.parent = parent
    ob.location = location
    coll.objects.link(ob)
    return ob


def _lens_object(name, coll, parent, location, radius):
    """Disc over the light-emitting area; its emission follows the fixture's output."""
    def build(bm):
        bmesh.ops.create_circle(bm, cap_ends=True, segments=32, radius=radius)
        bmesh.ops.reverse_faces(bm, faces=list(bm.faces))   # faces point along the beam (-Z)
    mat = bpy.data.materials.new(f"{name} Lens")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.name = LENS_NODE
    emission.inputs["Strength"].default_value = 0.0
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
    ob = _mesh_object(f"{name} Lens", coll, parent, build)
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    ob.location = (location[0], location[1], location[2] - 0.001)
    ob.visible_shadow = False   # must not shade the spot light sitting right behind it
    return ob


def import_model(path: str) -> list:
    """Import GLB/GLTF/FBX/OBJ/STL and return the newly created objects."""
    ext = Path(path).suffix.lower()
    before = set(bpy.data.objects.keys())
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    else:
        raise ValueError(f"Unsupported model format: {ext}")
    return [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]


def _reparent_keep_world(ob, parent):
    world = ob.matrix_world.copy()
    ob.parent = parent
    ob.matrix_parent_inverse = parent.matrix_world.inverted()
    ob.matrix_world = world


def _move_to_collection(ob, coll):
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)


def create_fixture(context, ftype: FixtureType, mode_name: str, universe: int, address: int,
                   name: str | None = None, location=(0.0, 0.0, 0.0)):
    scene = context.scene
    coll = get_collection(scene, FIXTURE_COLLECTION)
    mode = ftype.mode(mode_name)
    name = name or ftype.model or "Fixture"
    phys, geo = ftype.physical, ftype.geometry
    w, d, h = phys.get("width") or 0.25, phys.get("depth") or 0.25, phys.get("height") or 0.35
    has_pan = mode.channel("Pan") is not None or bool(geo.get("pan_range"))
    has_tilt = mode.channel("Tilt") is not None or bool(geo.get("tilt_range"))

    root = _empty(name, coll, None, location, "PLAIN_AXES", 0.2)
    parent = root
    pan = tilt = None
    if has_pan or has_tilt:
        _mesh_object(f"{name} Base", coll, root, lambda bm: _box(bm, (w * 0.7, d * 0.7, h * 0.15), (0, 0, -h * 0.075)))
        pan_pos = geo.get("pan_pivot") or (0.0, 0.0, 0.0)
        pan = _empty(f"{name} Pan", coll, root, pan_pos, "CIRCLE", 0.08)
        _mesh_object(f"{name} Yoke", coll, pan, lambda bm: (
            _box(bm, (0.03, d * 0.3, h * 0.55), (w * 0.5 + 0.02, 0, -h * 0.425)),
            _box(bm, (0.03, d * 0.3, h * 0.55), (-w * 0.5 - 0.02, 0, -h * 0.425))))
        tilt_pos = geo.get("tilt_pivot") or (0.0, 0.0, -h * 0.5)
        tilt = _empty(f"{name} Tilt", coll, pan, (tilt_pos[0] - pan_pos[0], tilt_pos[1] - pan_pos[1],
                                                  tilt_pos[2] - pan_pos[2]), "ARROWS", 0.08)
        _mesh_object(f"{name} Head", coll, tilt, lambda bm: _cylinder(bm, w * 0.42, h * 0.45, (0, 0, 0)))
        parent, lens = tilt, geo.get("lens_offset") or (0.0, 0.0, -h * 0.225)
    else:
        _mesh_object(f"{name} Body", coll, root, lambda bm: _cylinder(bm, w * 0.5, h, (0, 0, -h * 0.5)))
        lens = geo.get("lens_offset") or (0.0, 0.0, -h)

    light_data = bpy.data.lights.new(f"{name} Light", "SPOT")
    light_data.shadow_soft_size = 0.03
    light = bpy.data.objects.new(f"{name} Light", light_data)
    light.parent = parent
    light.location = lens
    coll.objects.link(light)
    lens_radius = geo.get("lens_radius") or w * (0.34 if (has_pan or has_tilt) else 0.4)
    lens_ob = _lens_object(name, coll, parent, lens, lens_radius)
    ies_path = bpy.path.abspath(ftype.optical.get("ies_file") or "")
    if ies_path and Path(ies_path).is_file():
        _attach_ies(light_data, ies_path)
    fx = root.lv
    fx.is_fixture = True
    fx.type_id, fx.type_json, fx.mode = ftype.id, ftype.to_json(), mode.name
    fx.universe, fx.address = universe, address
    fx.pan_obj, fx.tilt_obj, fx.light_obj, fx.lens_obj = pan, tilt, light, lens_ob
    fx.use_test = False

    model = geo.get("model_file")
    if geo.get("parts"):
        _attach_parts(root, pan, tilt, geo["parts"], coll, lens[2])
    elif model and Path(model).is_file():
        _attach_model(root, pan, tilt, geo, model, coll)
    return root


def _attach_ies(light, path: str):
    """Photometric distribution on the emission strength (Cycles light nodes)."""
    light.use_nodes = True
    nt = light.node_tree
    emission = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if emission is None:
        return
    ies = nt.nodes.new("ShaderNodeTexIES")
    ies.name = "LV IES"
    ies.mode = "EXTERNAL"
    ies.filepath = path
    nt.links.new(ies.outputs["Fac"], emission.inputs["Strength"])


def _primitive_mesh(shape: str, size, lens_z: float):
    """Real-size body from a GDTF primitive; size is (Length x, Width y, Height z) in metres."""
    length, width, height = size
    bm = bmesh.new()
    hang = -height / 2.0   # Base / Conventional hang down from the geometry origin
    if shape == "yoke":
        for side in (-1, 1):
            _box(bm, (0.02, width, height), (side * (length / 2.0 - 0.01), 0.0, hang))
    elif shape in ("box", "base"):
        _box(bm, size, (0.0, 0.0, hang if shape == "base" else 0.0))
    elif shape == "sphere":
        res = bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=0.5)
        for v in res["verts"]:
            v.co = Vector((v.co.x * length, v.co.y * width, v.co.z * height))
    else:   # cylinder, conventional, head: front face of a head sits at the lens
        res = bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.5, radius2=0.5, depth=1.0)
        centre = {"conventional": hang, "head": lens_z + height / 2.0}.get(shape, 0.0)
        for v in res["verts"]:
            v.co = Vector((v.co.x * length, v.co.y * width, v.co.z * height + centre))
    mesh = bpy.data.meshes.new(f"LV {shape}")
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(_body_material())
    return mesh


def _part_templates(part, lens_z: float = 0.0) -> list:
    """[(mesh data, matrix in the model file's own frame)] for one GDTF model file or primitive."""
    if part.get("primitive"):
        return [(_primitive_mesh(part["primitive"], part["size"], lens_z), Matrix.Identity(4))]
    path = part["file"]
    if part["ext"] == ".3ds":
        meshes = model3ds.read_3ds(Path(path).read_bytes())
        scale = model3ds.unit_scale(meshes, part["size"])
        out = []
        for name, verts, faces in meshes:
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata([(x * scale, y * scale, z * scale) for x, y, z in verts], [], [tuple(f) for f in faces])
            mesh.materials.append(_body_material())
            out.append((mesh, Matrix.Identity(4)))
        return out
    imported = import_model(path)
    bpy.context.view_layer.update()
    out = [(ob.data, ob.matrix_world.copy()) for ob in imported if ob.type == "MESH"]
    for ob in imported:
        bpy.data.objects.remove(ob, do_unlink=True)
    return out


def _attach_parts(root, pan, tilt, parts, coll, lens_z: float = 0.0):
    """GDTF models: base stays, the yoke follows pan, the head follows pan and tilt; each file is read once."""
    pan_origin = Vector(pan.location) if pan else Vector()
    targets = {"base": (root, Vector())}
    if pan:
        targets["yoke"] = (pan, pan_origin)
    if tilt:
        targets["head"] = (tilt, pan_origin + Vector(tilt.location))
    fallback = {"head": "yoke", "yoke": "base"}
    templates, made = {}, []
    for i, part in enumerate(parts):
        role = part["role"]
        while role not in targets:
            role = fallback[role]
        parent, origin = targets[role]
        key = part["file"] or (part["primitive"], tuple(part["size"]))
        if key not in templates:
            try:
                templates[key] = _part_templates(part, lens_z)
            except Exception as e:  # importer errors vary by format
                print(f"[lighting_visualizer] model {part['file']} failed: {e}")
                templates[key] = []
        rel = (Matrix.Translation(Vector(part["position"]) - origin)
               @ Matrix(part["rotation"]).transposed().to_4x4())
        for j, (mesh, local) in enumerate(templates[key]):
            ob = bpy.data.objects.new(f"{root.name} Part {i + 1}.{j + 1}", mesh)
            ob.visible_shadow = False   # the light sits inside the head and would be blocked by its own housing
            coll.objects.link(ob)
            ob.parent = parent
            ob.matrix_basis = rel @ local
            made.append(ob)
    if made:
        for ob in root.children_recursive:
            if ob.type == "MESH" and ob not in made and ob.name.split(" ")[-1] in ("Base", "Yoke", "Head", "Body"):
                ob.hide_viewport = ob.hide_render = True


def _attach_model(root, pan, tilt, geo, model_path, coll):
    """Replace the placeholder meshes with the imported model; object names in the geometry decide the pivot."""
    try:
        objs = import_model(model_path)
    except Exception as e:  # operator errors vary by importer
        print(f"[lighting_visualizer] model import failed: {e}")
        return
    roles = {}
    for key, parent in (("yoke_objects", pan), ("head_objects", tilt), ("base_objects", root)):
        for n in geo.get(key, []):
            roles[n] = parent
    for ob in objs:
        _move_to_collection(ob, coll)
        if ob.parent is None or ob.parent not in objs:
            _reparent_keep_world(ob, roles.get(re.sub(r"\.\d{3}$", "", ob.name)) or root)
    for ob in list(root.children_recursive):
        if ob.type == "MESH" and ob.name.split(" ")[-1] in ("Base", "Yoke", "Head", "Body") and ob not in objs:
            ob.hide_viewport = ob.hide_render = True


# ---------------------------------------------------------------------------------------------------------------
def apply_state(rt, now: float, exposure: float):
    """Write an evaluated FixtureState to the fixture's pivots and light; unchanged values are not rewritten."""
    st, fx = rt.state, rt.obj.lv
    factor = st.shutter_factor(now)
    rgb = st.color_at(now)
    key = (round(st.pan, 2), round(st.tilt, 2), round(st.intensity * factor, 4), tuple(round(c, 3) for c in rgb),
           round(st.beam_angle, 2), round(st.field_angle, 2), round(st.focus, 3), round(st.frost, 3),
           st.gobo_slot, round(rt.gobo_angle + st.gobo_index, 1), fx.intensity_scale, fx.beam_scale, exposure,
           fx.invert_pan, fx.invert_tilt, st.prism, round(rt.prism_angle, 1),
           tuple(tuple(round(v, 3) for v in b) for b in st.blades), round(st.shaper_rot, 1))
    if key == rt.last_key:
        return
    rt.last_key = key
    if fx.pan_obj:
        fx.pan_obj.rotation_euler = (0.0, 0.0, math.radians(-st.pan if fx.invert_pan else st.pan))
    if fx.tilt_obj:
        fx.tilt_obj.rotation_euler = (math.radians(-st.tilt if fx.invert_tilt else st.tilt), 0.0, 0.0)
    _write_lens(fx, st.intensity * factor, rgb)
    if not fx.light_obj or fx.light_obj.type != "LIGHT":
        return
    light = fx.light_obj.data
    beam = st.beam_angle * fx.beam_scale
    field = max(st.field_angle * fx.beam_scale, beam)
    candela = optics.intensity_candela(rt.ftype.optical, beam)
    watts = optics.blender_watts(candela) * fx.intensity_scale * exposure * st.intensity * factor
    facets = _prism_facets(fx.light_obj, create=st.prism)
    if st.prism:
        _write_light(rt, light, 0.0, st, rgb, beam, field)
        for i, f in enumerate(facets):
            f.hide_viewport = f.hide_render = False
            spin = math.radians(rt.prism_angle + i * 360.0 / PRISM_FACETS)
            tip = math.radians(PRISM_OFFSET * beam)
            f.rotation_euler = (Matrix.Rotation(spin, 3, "Z") @ Matrix.Rotation(tip, 3, "X")).to_euler()
            _write_light(rt, f.data, watts / PRISM_FACETS, st, rgb, beam, field, roll=math.degrees(spin))
    else:
        _write_light(rt, light, watts, st, rgb, beam, field)
        for f in facets:
            f.hide_viewport = f.hide_render = True


def _write_lens(fx, level, rgb):
    """Lens glows from 0 (off) to LENS_EMISSION_MAX (full output) in the fixture's colour."""
    lens = fx.lens_obj
    if lens is None or lens.active_material is None or lens.active_material.node_tree is None:
        return
    node = lens.active_material.node_tree.nodes.get(LENS_NODE)
    if node is None:
        return
    node.inputs["Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    node.inputs["Strength"].default_value = max(0.0, min(level, 1.0)) * LENS_EMISSION_MAX


def _write_light(rt, light, watts, st, rgb, beam, field, roll=0.0):
    light.energy = watts
    light.color = rgb
    light.spot_size = math.radians(min(max(field, 1.0), 180.0))
    light.spot_blend = optics.edge_blend(beam, field, st.frost, st.focus)
    _apply_gobo(rt, light, st, field, roll)
    _apply_blades(light, st, field, roll)


def _prism_facets(main, create: bool) -> list:
    """Extra spot lights that stand in for the prism's images; created on first use."""
    out = []
    for i in range(PRISM_FACETS):
        name = f"{main.name} Prism {i + 1}"
        ob = bpy.data.objects.get(name)
        if ob is None:
            if not create:
                return []
            ob = bpy.data.objects.new(name, main.data.copy())
            ob.parent = main.parent
            ob.location = main.location
            for c in main.users_collection:
                c.objects.link(ob)
        out.append(ob)
    return out


def _gobo_image(rt, st):
    for g in rt.obj.lv.gobos:
        if g.slot == st.gobo_slot and g.image:
            return g.image
    if st.gobo_image:
        path = st.gobo_image
        for img in bpy.data.images:
            if img.filepath and bpy.path.abspath(img.filepath) == path:
                return img
        try:
            return bpy.data.images.load(path, check_existing=True)
        except RuntimeError:
            return None
    return None


def _apply_gobo(rt, light, st, field_deg, roll=0.0):
    """Cycles light-node projection: direction -> plane -> image. EEVEE ignores light nodes."""
    image = _gobo_image(rt, st) if st.gobo_slot > 0 else None
    tree = light.node_tree if light.use_nodes else None
    mapping = tree.nodes.get(GOBO_NODE_PREFIX + "Mapping") if tree else None
    if mapping is None:
        if image is None:
            return
        light.use_nodes = True
        mapping = _build_gobo_nodes(light.node_tree)
    nodes = light.node_tree.nodes
    mix = nodes[GOBO_NODE_PREFIX + "Mix"]
    tex = nodes[GOBO_NODE_PREFIX + "Image"]
    mix.inputs["Fac"].default_value = 1.0 if image else 0.0
    if image and tex.image is not image:
        tex.image = image
    half = math.tan(math.radians(min(max(field_deg, 1.0), 170.0)) / 2.0)
    mapping.inputs["Scale"].default_value = (-0.5 / half, -0.5 / half, 1.0)
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rt.gobo_angle + st.gobo_index + roll))


def _light_output(tree):
    nodes, links = tree.nodes, tree.links
    out = next((n for n in nodes if n.type == "OUTPUT_LIGHT"), None) or nodes.new("ShaderNodeOutputLight")
    emission = next((n for n in nodes if n.type == "EMISSION"), None) or nodes.new("ShaderNodeEmission")
    if not any(l.to_node is out for l in links):
        links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return emission


def _plane_coords(tree):
    """Light direction -> position on the plane one metre ahead (x/z, y/z); shared by gobo and blades."""
    comb = tree.nodes.get(PLANE_NODE)
    if comb is not None:
        return comb
    nodes, links = tree.nodes, tree.links
    coord = nodes.new("ShaderNodeTexCoord")
    sep = nodes.new("ShaderNodeSeparateXYZ")
    dx = nodes.new("ShaderNodeMath")
    dx.operation = "DIVIDE"
    dy = nodes.new("ShaderNodeMath")
    dy.operation = "DIVIDE"
    comb = nodes.new("ShaderNodeCombineXYZ")
    comb.name = PLANE_NODE
    links.new(coord.outputs["Normal"], sep.inputs["Vector"])
    links.new(sep.outputs["X"], dx.inputs[0])
    links.new(sep.outputs["Z"], dx.inputs[1])
    links.new(sep.outputs["Y"], dy.inputs[0])
    links.new(sep.outputs["Z"], dy.inputs[1])
    links.new(dx.outputs[0], comb.inputs["X"])
    links.new(dy.outputs[0], comb.inputs["Y"])
    return comb


def _color_input(tree, emission):
    blades = tree.nodes.get(BLADE_PREFIX + "Mix")
    return blades.inputs["Color1"] if blades else emission.inputs["Color"]


def _build_gobo_nodes(tree):
    nodes, links = tree.nodes, tree.links
    emission = _light_output(tree)
    comb = _plane_coords(tree)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.name = GOBO_NODE_PREFIX + "Mapping"
    mapping.inputs["Location"].default_value = (0.5, 0.5, 0.0)
    tex = nodes.new("ShaderNodeTexImage")
    tex.name = GOBO_NODE_PREFIX + "Image"
    tex.extension = "CLIP"
    # Gobo files come in two styles: opaque black / white, or shapes on a transparent background (GDTF wheels).
    # Transparent areas let light through, so the image colour is only applied where it is opaque.
    cover = nodes.new("ShaderNodeMixRGB")
    cover.name = GOBO_NODE_PREFIX + "Alpha"
    cover.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    mix = nodes.new("ShaderNodeMixRGB")
    mix.name = GOBO_NODE_PREFIX + "Mix"
    mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(comb.outputs["Vector"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    links.new(tex.outputs["Alpha"], cover.inputs["Fac"])
    links.new(tex.outputs["Color"], cover.inputs["Color2"])
    links.new(cover.outputs["Color"], mix.inputs["Color2"])
    links.new(mix.outputs["Color"], _color_input(tree, emission))
    return mapping


def _apply_blades(light, st, field_deg, roll=0.0):
    """Framing shutters as a mask on the light colour (Cycles light nodes; EEVEE ignores them)."""
    tree = light.node_tree if light.use_nodes else None
    if tree is None or tree.nodes.get(BLADE_PREFIX + "Mix") is None:
        if not st.framed:
            return
        light.use_nodes = True
        _build_blade_nodes(light.node_tree)
    nodes = light.node_tree.nodes
    half = math.tan(math.radians(min(max(field_deg, 1.0), 170.0)) / 2.0)
    nodes[BLADE_PREFIX + "Field"].inputs["Scale"].default_value = -1.0 / half
    soft = 0.02 + abs(st.focus - 0.5) * 0.4 + st.frost * 0.3
    for i, (a, b, rot) in enumerate(st.blades, 1):
        angle = math.radians((i - 1) * 90.0 + rot + st.shaper_rot + roll)
        nodes[f"{BLADE_PREFIX}N{i}"].inputs[1].default_value = (math.cos(angle), math.sin(angle), 0.0)
        nodes[f"{BLADE_PREFIX}T{i}"].inputs[1].default_value = (-math.sin(angle), math.cos(angle), 0.0)
        nodes[f"{BLADE_PREFIX}S{i}"].inputs[1].default_value = b - a
        nodes[f"{BLADE_PREFIX}D{i}"].inputs[1].default_value = 1.0 - (a + b)
        nodes[f"{BLADE_PREFIX}P{i}"].inputs[1].default_value = -1.0 / soft


def _build_blade_nodes(tree):
    """Per blade: pass = clamp(0.5 - (p.n + k * p.t - d) / soft); the four passes multiply into the mask."""
    nodes, links = tree.nodes, tree.links
    emission = _light_output(tree)
    field = nodes.new("ShaderNodeVectorMath")
    field.operation = "SCALE"
    field.name = BLADE_PREFIX + "Field"
    links.new(_plane_coords(tree).outputs["Vector"], field.inputs[0])

    def math_node(name, op, clamp=False):
        node = nodes.new("ShaderNodeMath")
        node.operation, node.use_clamp, node.name = op, clamp, name
        return node
    mask = None
    for i in range(1, 5):
        dn = nodes.new("ShaderNodeVectorMath")
        dn.operation, dn.name = "DOT_PRODUCT", f"{BLADE_PREFIX}N{i}"
        dt = nodes.new("ShaderNodeVectorMath")
        dt.operation, dt.name = "DOT_PRODUCT", f"{BLADE_PREFIX}T{i}"
        along = math_node(f"{BLADE_PREFIX}S{i}", "MULTIPLY_ADD")
        edge = math_node(f"{BLADE_PREFIX}D{i}", "SUBTRACT")
        soft = math_node(f"{BLADE_PREFIX}P{i}", "MULTIPLY_ADD", clamp=True)
        soft.inputs[2].default_value = 0.5
        links.new(field.outputs["Vector"], dn.inputs[0])
        links.new(field.outputs["Vector"], dt.inputs[0])
        links.new(dt.outputs["Value"], along.inputs[0])
        links.new(dn.outputs["Value"], along.inputs[2])
        links.new(along.outputs[0], edge.inputs[0])
        links.new(edge.outputs[0], soft.inputs[0])
        if mask is None:
            mask = soft
        else:
            both = math_node(f"{BLADE_PREFIX}M{i}", "MULTIPLY")
            links.new(mask.outputs[0], both.inputs[0])
            links.new(soft.outputs[0], both.inputs[1])
            mask = both
    mix = nodes.new("ShaderNodeMixRGB")
    mix.name = BLADE_PREFIX + "Mix"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Fac"].default_value = 1.0
    mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    links.new(mask.outputs[0], mix.inputs["Color2"])
    gobo = nodes.get(GOBO_NODE_PREFIX + "Mix")
    if gobo is not None:
        links.new(gobo.outputs["Color"], mix.inputs["Color1"])
    links.new(mix.outputs["Color"], emission.inputs["Color"])


def fixture_matrix(rotation_rows, position) -> Matrix:
    """World matrix from MVR rows (images of the local axes) and a position in metres."""
    m = Matrix(rotation_rows).transposed().to_4x4()
    m.translation = Vector(position)
    return m
