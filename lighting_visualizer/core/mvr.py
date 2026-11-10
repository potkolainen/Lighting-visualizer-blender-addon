"""MVR (My Virtual Rig) import / export: fixtures, trusses, scene objects, groups and DMX patch (GeneralSceneDescription.xml)."""
from __future__ import annotations

import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_IDENT = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_VEC = re.compile(r"\{([^}]*)\}")


@dataclass
class MvrItem:
    kind: str                    # "fixture" | "truss" | "object"
    name: str
    uuid: str = ""
    layer: str = ""
    group: str = ""              # "/"-joined group path
    position: tuple = (0.0, 0.0, 0.0)   # metres, Z up
    rotation: tuple = _IDENT     # columns of the world rotation matrix are the local axes; stored as rows u, v, w
    geometry_files: list = field(default_factory=list)
    gdtf_spec: str = ""
    gdtf_mode: str = ""
    fixture_id: str = ""
    universe: int = 0            # 1-based, 0 = unpatched
    address: int = 0
    unit_number: str = ""


@dataclass
class MvrScene:
    items: list = field(default_factory=list)
    extract_dir: str = ""

    @property
    def fixtures(self) -> list:
        return [i for i in self.items if i.kind == "fixture"]


def parse_matrix(text: str):
    """"{ux,uy,uz}{vx,vy,vz}{wx,wy,wz}{ox,oy,oz}" -> (rows u,v,w, origin in metres)."""
    vecs = [tuple(float(x) for x in m.split(",")) for m in _VEC.findall(text or "")]
    if len(vecs) != 4 or any(len(v) != 3 for v in vecs):
        return _IDENT, (0.0, 0.0, 0.0)
    return tuple(vecs[:3]), tuple(c / 1000.0 for c in vecs[3])  # MVR origins are in millimetres


def _compose(parent, local):
    """World transform of `local` inside `parent`; each is (rows u,v,w, origin). Row i is the image of axis i."""
    (pr, pt), (lr, lt) = parent, local
    rot = tuple(tuple(sum(row[k] * pr[k][j] for k in range(3)) for j in range(3)) for row in lr)
    pos = tuple(sum(lt[k] * pr[k][j] for k in range(3)) + pt[j] for j in range(3))
    return rot, pos


def parse_address(text: str):
    """Absolute address ("1025") or "universe.channel" -> (universe 1-based, channel 1..512)."""
    text = (text or "").strip()
    if not text:
        return 0, 0
    if "." in text:
        u, c = text.split(".", 1)
        return int(u), int(c)
    n = int(text)
    return (n - 1) // 512 + 1, (n - 1) % 512 + 1


def import_mvr(path: str | Path, extract_dir: str | Path | None = None) -> MvrScene:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("GeneralSceneDescription.xml"))
        for el in root.iter():
            if isinstance(el.tag, str) and "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
        if extract_dir:
            out = Path(extract_dir)
            out.mkdir(parents=True, exist_ok=True)
            for name in z.namelist():
                if name != "GeneralSceneDescription.xml" and not name.endswith("/") and ".." not in Path(name).parts:
                    dest = out / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))
    scene = MvrScene(extract_dir=str(extract_dir or ""))
    for layer in root.iter("Layer"):
        ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0)
        lm = layer.find("Matrix")
        base = parse_matrix(lm.text) if lm is not None else ident
        _walk(layer.find("ChildList"), base, layer.get("name", ""), "", scene)
    return scene


def _walk(child_list, parent, layer: str, group: str, scene: MvrScene):
    if child_list is None:
        return
    for el in child_list:
        tag = el.tag
        if tag not in ("Fixture", "Truss", "SceneObject", "GroupObject", "FocusPoint", "Support", "Projector"):
            continue
        m = el.find("Matrix")
        world = _compose(parent, parse_matrix(m.text) if m is not None else (_IDENT, (0.0, 0.0, 0.0)))
        name = el.get("name", tag)
        if tag == "GroupObject":
            _walk(el.find("ChildList"), world, layer, f"{group}/{name}" if group else name, scene)
            continue
        item = MvrItem(kind={"Fixture": "fixture", "Truss": "truss"}.get(tag, "object"), name=name,
                       uuid=el.get("uuid", ""), layer=layer, group=group, position=world[1], rotation=world[0])
        item.geometry_files = [g.get("fileName", "") for g in el.findall("Geometries/Geometry3D") if g.get("fileName")]
        if tag == "Fixture":
            item.gdtf_spec = (el.findtext("GDTFSpec") or "").strip()
            item.gdtf_mode = (el.findtext("GDTFMode") or "").strip()
            item.fixture_id = (el.findtext("FixtureID") or "").strip()
            item.unit_number = (el.findtext("UnitNumber") or "").strip()
            addr = el.find("Addresses/Address")
            if addr is not None:
                try:
                    item.universe, item.address = parse_address(addr.text)
                except ValueError:
                    pass
        scene.items.append(item)
        if el.find("ChildList") is not None:
            _walk(el.find("ChildList"), world, layer, group, scene)


# ---- export ----------------------------------------------------------------------------------------------------
def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def format_matrix(rotation, position) -> str:
    """Inverse of parse_matrix: rows u, v, w and an origin in metres -> MVR matrix string (origin in millimetres)."""
    rows = "".join("{" + ",".join(_fmt(c) for c in row) + "}" for row in rotation)
    return rows + "{" + ",".join(_fmt(c * 1000.0) for c in position) + "}"


def _stable_uuid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "lighting_visualizer:" + key))


def export_mvr(path: str | Path, items, files: dict | None = None) -> None:
    """Write an MVR archive. `items` are MvrItem with world transforms; `files` maps archive names (GDTF and 3D files)
    to bytes or a file path. Items are grouped by `layer` and the "/"-joined `group`; group transforms are identity."""
    root = ET.Element("GeneralSceneDescription", verMajor="1", verMinor="6")
    ET.SubElement(root, "UserData")
    layers = ET.SubElement(ET.SubElement(root, "Scene"), "Layers")
    child_lists: dict = {}
    ident = format_matrix(_IDENT, (0.0, 0.0, 0.0))

    def child_list(layer_name: str, group: str):
        key = (layer_name, group)
        if key in child_lists:
            return child_lists[key]
        if not group:
            layer = ET.SubElement(layers, "Layer", name=layer_name, uuid=_stable_uuid("layer:" + layer_name))
            ET.SubElement(layer, "Matrix").text = ident
            lst = ET.SubElement(layer, "ChildList")
        else:
            parent_group, _, name = group.rpartition("/")
            grp = ET.SubElement(child_list(layer_name, parent_group), "GroupObject", name=name,
                                uuid=_stable_uuid(f"group:{layer_name}:{group}"))
            ET.SubElement(grp, "Matrix").text = ident
            lst = ET.SubElement(grp, "ChildList")
        child_lists[key] = lst
        return lst

    for item in items:
        group = "/".join(p for p in item.group.split("/") if p)
        tag = {"fixture": "Fixture", "truss": "Truss"}.get(item.kind, "SceneObject")
        el = ET.SubElement(child_list(item.layer or "Layer 1", group), tag, name=item.name,
                           uuid=item.uuid or str(uuid.uuid4()))
        ET.SubElement(el, "Matrix").text = format_matrix(item.rotation, item.position)
        if tag == "Fixture":
            ET.SubElement(el, "GDTFSpec").text = item.gdtf_spec
            ET.SubElement(el, "GDTFMode").text = item.gdtf_mode
            if item.universe > 0 and item.address > 0:
                addrs = ET.SubElement(el, "Addresses")
                ET.SubElement(addrs, "Address", {"break": "0"}).text = str((item.universe - 1) * 512 + item.address)
            if item.fixture_id:
                ET.SubElement(el, "FixtureID").text = item.fixture_id
            if item.unit_number:
                ET.SubElement(el, "UnitNumber").text = item.unit_number
        elif item.geometry_files:
            geoms = ET.SubElement(el, "Geometries")
            for name in item.geometry_files:
                ET.SubElement(geoms, "Geometry3D", fileName=name)

    ET.indent(root)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("GeneralSceneDescription.xml", xml)
        for name, data in (files or {}).items():
            if isinstance(data, (str, Path)):
                z.write(data, name)
            else:
                z.writestr(name, data)
