"""GDTF (General Device Type Format) importer and minimal exporter. A .gdtf file is a zip with description.xml plus models and wheel images."""
from __future__ import annotations

import io
import re
import shutil
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import optics
from .fixture import (ChannelFunction, DmxChannel, DmxMode, FixtureType, GEOMETRY_DEFAULTS, OPTICAL_DEFAULTS,
                      PHYSICAL_DEFAULTS, slugify)

_SIMPLE = {
    "Dimmer": "Dimmer", "Pan": "Pan", "Tilt": "Tilt", "Zoom": "Zoom", "Iris": "Iris",
    "ColorAdd_R": "Red", "ColorAdd_G": "Green", "ColorAdd_B": "Blue", "ColorAdd_W": "White",
    "ColorRGB_Red": "Red", "ColorRGB_Green": "Green", "ColorRGB_Blue": "Blue",
    "ColorSub_C": "Cyan", "ColorSub_M": "Magenta", "ColorSub_Y": "Yellow",
    "CTO": "CTO", "CTC": "CTO", "StrobeFrequency": "Strobe", "ShaperRot": "ShaperRot",
}
_PATTERNS = (
    (r"^Blade(\d)([AB])$", lambda m: f"Blade{m.group(1)}{m.group(2)}"),
    (r"^Blade(\d)Rot$", lambda m: f"Blade{m.group(1)}Rot"),
    (r"^Color(\d+)$", lambda m: "ColorWheel" if m.group(1) == "1" else "Control"),
    (r"^Gobo(\d+)$", lambda m: f"Gobo{m.group(1)}"),
    (r"^Gobo(\d+)SelectSpin$", lambda m: f"Gobo{m.group(1)}"),
    (r"^Gobo(\d+)Pos$", lambda m: f"Gobo{m.group(1)}Pos"),
    (r"^Gobo(\d+)PosRotate$", lambda m: f"Gobo{m.group(1)}Rot"),
    (r"^Prism(\d+)$", lambda m: f"Prism{m.group(1)}"),
    (r"^Prism(\d+)PosRotate$", lambda m: f"Prism{m.group(1)}Rot"),
    (r"^Focus(\d+)$", lambda m: "Focus"),
    (r"^Frost(\d+)$", lambda m: "Frost"),
    (r"^Shutter(\d+)$", lambda m: "Shutter"),
    (r"^Shutter(\d+)Strobe\w*$", lambda m: "Strobe"),
    (r"^AnimationWheel(\d+)$", lambda m: "AnimationWheel"),
)


def map_attribute(gdtf_name: str) -> str:
    if gdtf_name in _SIMPLE:
        return _SIMPLE[gdtf_name]
    for pattern, fn in _PATTERNS:
        m = re.match(pattern, gdtf_name)
        if m:
            return fn(m)
    return "Control"


def _split_dmx(text: str, default_bytes: int = 1):
    """GDTF DMX values look like "32768/2" (value/bytes). Returns (value, bytes)."""
    if not text or text.lower() == "none":
        return 0, default_bytes
    value, _, nb = text.partition("/")
    try:
        return int(value), int(nb) if nb else default_bytes
    except ValueError:
        return 0, default_bytes


def _coarse(text: str) -> int:
    value, nb = _split_dmx(text)
    return min(value >> (8 * (nb - 1)), 255)


def _float(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def import_gdtf(path: str | Path, extract_dir: str | Path | None = None) -> FixtureType:
    """Parse a .gdtf archive. Wheel images (and 3D models) are extracted to `extract_dir` when given."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("description.xml"))
        _strip_ns(root)
        ft = root.find("FixtureType")
        if ft is None:
            raise ValueError("description.xml has no FixtureType")
        names = set(z.namelist())
        out_dir = Path(extract_dir) / slugify(ft.get("Name", path.stem)) if extract_dir else None
        extracted = {}

        def extract(member: str) -> str:
            if out_dir is None or member not in names:
                return ""
            if member not in extracted:
                dest = out_dir / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(member))
                extracted[member] = str(dest)
            return extracted[member]

        def find_media(subdir: str, name: str, exts) -> str:
            for e in exts:
                for cand in (f"{subdir}/{name}{e}", f"{subdir}/{name}"):
                    if cand in names:
                        return cand
            return ""

        wheels = _parse_wheels(ft, lambda n: extract(find_media("wheels", n, (".png", ".svg"))))
        optical = _parse_optical(ft)
        physical = _parse_physical(ft)
        geometry = dict(GEOMETRY_DEFAULTS)
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            source = out_dir / "source.gdtf"   # kept so MVR export can embed the original file
            if path.resolve() != source.resolve():
                shutil.copyfile(path, source)
            geometry["gdtf_file"] = str(source)

        modes = []
        for m in ft.iter("DMXMode"):
            modes.append(_parse_mode(m, wheels, geometry))
        if not modes:
            raise ValueError("GDTF contains no DMX modes")
        _apply_geometry_pivots(ft, geometry)
        beam = next(ft.iter("Beam"), None)
        if beam is not None and _float(beam.get("BeamRadius")):
            geometry["lens_radius"] = _float(beam.get("BeamRadius"))
        if out_dir:
            lower = {n.lower(): n for n in names}
            geometry["parts"] = _model_parts(ft, lambda m: _resolve_model(m, lower, extract))

    name = ft.get("Name", path.stem)
    return FixtureType(
        id="gdtf." + slugify(f"{ft.get('Manufacturer', '')}_{name}"),
        manufacturer=ft.get("Manufacturer", ""), model=name, kind="OTHER", category="GDTF",
        description=ft.get("Description", ft.get("LongName", "")),
        physical=physical, optical=optical, geometry=geometry, modes=modes, wheels=wheels,
        provenance={"physical": "manufacturer", "optical": "manufacturer", "dmx": "manufacturer"},
        notes="Imported from GDTF. Pivots come from the geometry tree; 3D models are not mapped, a placeholder body is used.")


_ROW = re.compile(r"\{([^}]*)\}")
_IDENT = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _parse_position(text):
    """"{ux,uy,uz,0}{vx,..}{wx,..}{ox,oy,oz,1}" -> (3x3 rows, origin in metres); identity when malformed.

    GDTF 1.2 files write the matrix row-major with the origin in the last column instead; that form is converted."""
    try:
        rows = [[float(x) for x in r.split(",")] for r in _ROW.findall(text or "")]
    except ValueError:
        return _IDENT, (0.0, 0.0, 0.0)
    if len(rows) != 4 or any(len(r) < 4 for r in rows):
        return _IDENT, (0.0, 0.0, 0.0)
    if not any(rows[3][:3]) and any(r[3] for r in rows[:3]):
        rot = tuple(tuple(rows[j][i] for j in range(3)) for i in range(3))
        return rot, (rows[0][3], rows[1][3], rows[2][3])
    return tuple(tuple(r[:3]) for r in rows[:3]), tuple(rows[3][:3])


MAX_PARTS = 300


def _resolve_model(model_el, lower_names: dict, extract):
    """(extracted path, extension) of a Model's glTF / 3DS file, or None for primitives and missing files."""
    file = model_el.get("File", "")
    if not file:
        return None
    for folder, exts in (("models/gltf", (".glb", ".gltf")), ("models/3ds", (".3ds",))):
        for e in exts:
            member = lower_names.get(f"{folder}/{file}{e}".lower()) or lower_names.get(f"{folder}/{file}".lower())
            if member:
                path = extract(member)
                if path:
                    return path, Path(member).suffix.lower()
    return None


def _primitive_shape(model_el, size) -> str:
    """Shape name for a GDTF primitive Model (box, base, cylinder, conventional, head, sphere, yoke), else "".

    Cube / Cylinder / Sphere are centred on the geometry origin; Base and Conventional hang down from it.
    Cables and the 1 m placeholder size are skipped."""
    kind = (model_el.get("PrimitiveType") or "").lower()
    if not all(s > 0 for s in size) or size == [1.0, 1.0, 1.0]:
        return ""
    for prefix, shape in (("cube", "box"), ("base", "base"), ("cylinder", "cylinder"), ("conventional", "conventional"),
                          ("head", "head"), ("sphere", "sphere"), ("yoke", "yoke")):
        if kind.startswith(prefix):
            return shape
    return ""


def _model_parts(ft, resolve) -> list:
    """Meshes of the geometry tree as [{file, ext, role, rotation, position, size, name}] in fixture coordinates.

    role is base / yoke / head: the pan axis geometry and everything below it moves with pan, the tilt axis with tilt."""
    models = {m.get("Name"): m for m in ft.iter("Model")}
    axes = {}
    for ch in ft.iter("DMXChannel"):
        logical = ch.find("LogicalChannel")
        attr = logical.get("Attribute") if logical is not None else None
        if attr in ("Pan", "Tilt"):
            axes.setdefault(attr, ch.get("Geometry"))
    parts = []

    def walk(el, parent_rot, parent_pos, role):
        for child in el:
            if not isinstance(child.tag, str) or child.tag in ("Model", "Wheel"):
                continue
            rot, pos = _parse_position(child.get("Position"))
            world_pos = tuple(sum(pos[k] * parent_rot[k][j] for k in range(3)) + parent_pos[j] for j in range(3))
            world_rot = tuple(tuple(sum(rot[i][k] * parent_rot[k][j] for k in range(3)) for j in range(3))
                              for i in range(3))
            name = child.get("Name")
            child_role = role
            if name and name == axes.get("Pan"):
                child_role = "yoke"
            if name and name == axes.get("Tilt"):
                child_role = "head"
            model = models.get(child.get("Model"))
            if child.tag in ("Geometry", "Axis") and model is not None and len(parts) < MAX_PARTS:
                size = [_float(model.get(k), 0.0) for k in ("Length", "Width", "Height")]
                found = resolve(model)
                shape = _primitive_shape(model, size) if not found else ""
                if found or shape:
                    parts.append({"file": found[0] if found else "", "ext": found[1] if found else "",
                                  "primitive": shape, "role": child_role, "name": name or "",
                                  "rotation": [list(r) for r in world_rot], "position": list(world_pos),
                                  "size": size})
            walk(child, world_rot, world_pos, child_role)

    geoms = ft.find("Geometries")
    if geoms is not None:
        walk(geoms, _IDENT, (0.0, 0.0, 0.0), "base")
    return parts


def _geometry_positions(ft):
    """{geometry name: absolute origin} plus the first Beam's absolute origin."""
    positions, beam = {}, [None]

    def walk(el, parent_rot, parent_pos):
        for child in el:
            if not isinstance(child.tag, str) or child.tag in ("Model", "Wheel"):
                continue
            rot, pos = _parse_position(child.get("Position"))
            world_pos = tuple(sum(pos[k] * parent_rot[k][j] for k in range(3)) + parent_pos[j] for j in range(3))
            world_rot = tuple(tuple(sum(rot[i][k] * parent_rot[k][j] for k in range(3)) for j in range(3))
                              for i in range(3))
            if child.get("Name"):
                positions[child.get("Name")] = world_pos
            if child.tag == "Beam" and beam[0] is None:
                beam[0] = world_pos
            walk(child, world_rot, world_pos)

    geoms = ft.find("Geometries")
    if geoms is not None:
        walk(geoms, _IDENT, (0.0, 0.0, 0.0))
    return positions, beam[0]


def _apply_geometry_pivots(ft, geometry: dict):
    positions, beam = _geometry_positions(ft)
    axis = {}
    for ch in ft.iter("DMXChannel"):
        logical = ch.find("LogicalChannel")
        attr = logical.get("Attribute") if logical is not None else None
        if attr in ("Pan", "Tilt") and ch.get("Geometry") in positions:
            axis.setdefault(attr, positions[ch.get("Geometry")])
    pan, tilt = axis.get("Pan"), axis.get("Tilt")
    if pan is None and tilt is None:
        if beam is not None:
            geometry["lens_offset"] = list(beam)
        return
    pan = pan or tilt
    tilt = tilt or pan
    geometry["pan_pivot"], geometry["tilt_pivot"] = list(pan), list(tilt)
    if beam is not None:
        geometry["lens_offset"] = [b - t for b, t in zip(beam, tilt)]


def _parse_physical(ft) -> dict:
    phys = dict(PHYSICAL_DEFAULTS)
    for tag, key in (("Weight", "weight"), ("PowerConsumption", "power")):
        el = next(ft.iter(tag), None)
        if el is not None:
            phys[key] = _float(el.get("Value"), None)
    for model in ft.iter("Model"):
        if model.get("PrimitiveType") in (None, "Undefined") and _float(model.get("Width")):
            phys["width"], phys["depth"] = _float(model.get("Width")), _float(model.get("Length"), phys["depth"])
            phys["height"] = _float(model.get("Height"), phys["height"])
            return phys
    for model in ft.iter("Model"):   # primitives only: the first real shape gives the size
        size = [_float(model.get(k), 0.0) for k in ("Length", "Width", "Height")]
        if _primitive_shape(model, size) and not (model.get("PrimitiveType") or "").lower().startswith("pigtail"):
            phys["depth"], phys["width"], phys["height"] = size
            break
    return phys


def _parse_optical(ft) -> dict:
    opt = dict(OPTICAL_DEFAULTS)
    beam = next(ft.iter("Beam"), None)
    if beam is not None:
        opt["beam_angle"] = _float(beam.get("BeamAngle"), opt["beam_angle"])
        opt["field_angle"] = _float(beam.get("FieldAngle"), opt["field_angle"])
        opt["lumens"] = _float(beam.get("LuminousFlux"), None)
        opt["color_temp"] = _float(beam.get("ColorTemperature"), opt["color_temp"])
        opt["source"] = beam.get("LampType", opt["source"])
    return opt


def _parse_wheels(ft, resolve_image) -> dict:
    wheels = {}
    for w in ft.iter("Wheel"):
        slots = []
        for s in w.findall("Slot"):
            color = tuple(_float(v, 0.0) for v in (s.get("Color") or "").split(",")) if s.get("Color") else None
            rgb = optics.xyy_to_rgb(*color) if color and len(color) == 3 else None
            media = s.get("MediaFileName", "")
            slots.append({"name": s.get("Name", ""), "color": list(rgb) if rgb else None,
                          "image": resolve_image(media) if media else ""})
        wheels[w.get("Name", "")] = slots
    return wheels


def _parse_mode(mode_el, wheels, geometry) -> DmxMode:
    channels = []
    for ch in mode_el.iter("DMXChannel"):
        offset = ch.get("Offset", "")
        if not offset or offset.lower() == "none":
            continue  # virtual / unpatched channel
        offsets = [int(o) for o in offset.split(",") if o.strip().isdigit()]
        if not offsets:
            continue
        logical = ch.find("LogicalChannel")
        attr_name = logical.get("Attribute", "") if logical is not None else ""
        attribute = map_attribute(attr_name)
        dvalue, dbytes = _split_dmx(ch.get("Default", ""), len(offsets))
        default = round(dvalue * (256 ** len(offsets) - 1) / (256 ** dbytes - 1))
        functions, wheel_name = [], ""
        if logical is not None:
            for cf in logical.findall("ChannelFunction"):
                wheel_name = wheel_name or cf.get("Wheel", "")
                sets = cf.findall("ChannelSet")
                cf_from = _coarse(cf.get("DMXFrom", "0/1"))
                if not sets:
                    functions.append(ChannelFunction(cf.get("Name", ""), cf_from, 255,
                                                     _float(cf.get("PhysicalFrom")), _float(cf.get("PhysicalTo"))))
                    continue
                for s in sets:
                    idx = s.get("WheelSlotIndex")
                    functions.append(ChannelFunction(
                        s.get("Name", cf.get("Name", "")), _coarse(s.get("DMXFrom", "0/1")), 255,
                        _float(s.get("PhysicalFrom")), _float(s.get("PhysicalTo")),
                        slot=int(idx) - 1 if idx and idx.isdigit() and int(idx) > 0 else None))
        functions.sort(key=lambda f: f.dmx_from)
        for i, f in enumerate(functions):  # GDTF only gives range starts
            f.dmx_to = functions[i + 1].dmx_from - 1 if i + 1 < len(functions) else 255
        if attribute in ("Pan", "Tilt") and functions and None not in (functions[0].phys_from, functions[0].phys_to):
            span = abs(functions[0].phys_to - functions[0].phys_from)
            if span > 0:
                geometry["pan_range" if attribute == "Pan" else "tilt_range"] = span
        channels.append(DmxChannel(attribute=attribute, name=attr_name or attribute, offsets=offsets,
                                   default=default, functions=functions, wheel=wheel_name))
    return DmxMode(name=mode_el.get("Name", "Mode"), channels=channels)


# ---- export (minimal GDTF for fixtures that have no original file) ---------------------------------------------
_EXPORT_ATTR = {
    "Red": "ColorAdd_R", "Green": "ColorAdd_G", "Blue": "ColorAdd_B", "White": "ColorAdd_W",
    "Cyan": "ColorSub_C", "Magenta": "ColorSub_M", "Yellow": "ColorSub_Y", "Strobe": "StrobeFrequency",
    "ColorWheel": "Color1", "Shutter": "Shutter1", "Focus": "Focus1", "Frost": "Frost1",
    "AnimationWheel": "AnimationWheel1", "Gobo1Rot": "Gobo1PosRotate", "Gobo2Rot": "Gobo2PosRotate",
    "Prism1Rot": "Prism1PosRotate", "Prism2Rot": "Prism2PosRotate",
}
_IDENT_POS = "{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}"


def _node_name(text: str, fallback: str = "Item") -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text or "").strip("_") or fallback


def _position(offset) -> str:
    x, y, z = (offset or (0.0, 0.0, 0.0))
    return "{1,0,0,0}{0,1,0,0}{0,0,1,0}{%g,%g,%g,1}" % (x, y, z)


def _rel(a, b):
    return [p - q for p, q in zip(a or (0.0, 0.0, 0.0), b or (0.0, 0.0, 0.0))]


def export_gdtf(ftype: FixtureType) -> bytes:
    """Build a minimal .gdtf archive (geometry, beam data, DMX modes with channel functions) from a FixtureType.
    Wheels, 3D models and thumbnails are not written."""
    geo, optical, physical = ftype.geometry, ftype.optical, ftype.physical
    name = _node_name(ftype.model or ftype.id, "Fixture")
    root = ET.Element("GDTF", DataVersion="1.1")
    ft = ET.SubElement(root, "FixtureType", Name=name, ShortName=name[:16], LongName=ftype.label,
                       Manufacturer=ftype.manufacturer or "Unknown", Description=ftype.description or "",
                       FixtureTypeID=str(uuid.uuid5(uuid.NAMESPACE_URL, "lighting_visualizer:gdtf:" + ftype.id)).upper(),
                       Thumbnail="", RefFT="", CanHaveChildren="Yes")

    attributes = {}
    for mode in ftype.modes:
        for c in mode.channels:
            attributes.setdefault(_EXPORT_ATTR.get(c.attribute) or _node_name(c.attribute if c.attribute != "Control"
                                                                              else c.name, "Control"), None)
    defs = ET.SubElement(ft, "AttributeDefinitions")
    group = ET.SubElement(ET.SubElement(defs, "FeatureGroups"), "FeatureGroup", Name="Control", Pretty="Control")
    ET.SubElement(group, "Feature", Name="Control")
    attr_el = ET.SubElement(defs, "Attributes")
    for a in attributes:
        ET.SubElement(attr_el, "Attribute", Name=a, Pretty=a, Feature="Control.Control")

    props = {}
    if physical.get("weight") is not None:
        props["Weight"] = physical["weight"]
    if physical.get("power") is not None:
        props["PowerConsumption"] = physical["power"]
    if props:
        el = ET.SubElement(ET.SubElement(ft, "PhysicalDescriptions"), "Properties")
        for tag, value in props.items():
            ET.SubElement(el, tag, Value=f"{value:g}")

    moving = bool(geo.get("pan_range") or geo.get("tilt_range")) or any(
        m.channel("Pan") or m.channel("Tilt") for m in ftype.modes)
    body = ET.SubElement(ET.SubElement(ft, "Geometries"), "Geometry", Name="Body", Position=_IDENT_POS)
    pan_pivot = geo.get("pan_pivot") or (0.0, 0.0, 0.0)
    tilt_pivot = geo.get("tilt_pivot") or pan_pivot
    lens = geo.get("lens_offset") or (0.0, 0.0, -(physical.get("height") or 0.3))
    parent, pan_geo, tilt_geo = body, "Body", "Body"
    if moving:
        parent = ET.SubElement(parent, "Axis", Name="Yoke", Position=_position(pan_pivot))
        pan_geo = tilt_geo = "Yoke"
        parent = ET.SubElement(parent, "Axis", Name="Head", Position=_position(_rel(tilt_pivot, pan_pivot)))
        tilt_geo = "Head"
    source = str(optical.get("source", ""))
    ET.SubElement(parent, "Beam", Name="Beam", Position=_position(lens), LampType="LED" if "LED" in source.upper() else "Discharge",
                  PowerConsumption=f"{physical.get('power') or 0:g}", LuminousFlux=f"{optical.get('lumens') or 0:g}",
                  ColorTemperature=f"{optical.get('color_temp') or 6000:g}", BeamAngle=f"{optical.get('beam_angle') or 25:g}",
                  FieldAngle=f"{optical.get('field_angle') or 40:g}", BeamRadius="0.05", BeamType="Spot",
                  ColorRenderingIndex="100")

    modes_el = ET.SubElement(ft, "DMXModes")
    for mode in ftype.modes:
        channels_el = ET.SubElement(ET.SubElement(modes_el, "DMXMode", Name=mode.name, Geometry="Body"), "DMXChannels")
        for c in mode.channels:
            attr = _EXPORT_ATTR.get(c.attribute) or _node_name(c.attribute if c.attribute != "Control" else c.name, "Control")
            n = max(len(c.offsets), 1)
            scale = 256 ** (n - 1)
            geometry = {"Pan": pan_geo, "Tilt": tilt_geo}.get(c.attribute, "Body")
            ch = ET.SubElement(channels_el, "DMXChannel", Offset=",".join(str(o) for o in c.offsets), Geometry=geometry,
                               Default=f"{c.default}/{n}")
            logical = ET.SubElement(ch, "LogicalChannel", Attribute=attr)
            funcs = c.functions or [ChannelFunction(attr, 0, 255)]
            used = set()
            for i, f in enumerate(funcs):
                fname = _node_name(f.name, attr)
                if fname in used:
                    fname = f"{fname}_{i + 1}"
                used.add(fname)
                cf = ET.SubElement(logical, "ChannelFunction", Name=fname, Attribute=attr, DMXFrom=f"{f.dmx_from * scale}/{n}")
                if f.phys_from is not None and f.phys_to is not None:
                    cf.set("PhysicalFrom", f"{f.phys_from:g}")
                    cf.set("PhysicalTo", f"{f.phys_to:g}")
            if c.attribute in ("Pan", "Tilt") and geo.get("pan_range" if c.attribute == "Pan" else "tilt_range") \
                    and len(funcs) == 1 and funcs[0].phys_from is None:
                half = geo["pan_range" if c.attribute == "Pan" else "tilt_range"] / 2
                logical[0].set("PhysicalFrom", f"{-half:g}")
                logical[0].set("PhysicalTo", f"{half:g}")

    ET.indent(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("description.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return buf.getvalue()
