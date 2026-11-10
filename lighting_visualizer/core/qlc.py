"""QLC+ fixture definition (.qxf) importer, so the visualizer can use the same fixture library as QLC+."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .evaluate import DEFAULT_STROBE_HZ
from .fixture import (ChannelFunction, DmxChannel, DmxMode, FixtureType, GEOMETRY_DEFAULTS, OPTICAL_DEFAULTS,
                      PHYSICAL_DEFAULTS, slugify)

LUMENS_PER_WATT = {"LED": 20.0, "Discharge": 12.0, "Tungsten": 12.0, "Other": 12.0}   # estimate of output per input W
FIELD_TO_BEAM = 1.5
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}   # Blender cannot load the SVG gobos

_PRESET_ATTR = {
    "PositionPan": "Pan", "PositionTilt": "Tilt",
    "IntensityMasterDimmer": "Dimmer", "IntensityDimmer": "Dimmer",
    "IntensityRed": "Red", "IntensityGreen": "Green", "IntensityBlue": "Blue", "IntensityWhite": "White",
    "IntensityCyan": "Cyan", "IntensityMagenta": "Magenta", "IntensityYellow": "Yellow",
    "IntensityAmber": "Amber", "IntensityLime": "Lime",
    "ColorMacro": "ColorWheel", "ColorWheel": "ColorWheel", "ColorDoubleMacro": "ColorWheel",
    "ColorCTOMixer": "CTO", "ColorCTCMixer": "CTO", "ColorCTBMixer": "CTO",
    "ShutterStrobeSlowFast": "Strobe", "ShutterStrobeFastSlow": "Strobe",
    "ShutterIrisMinToMax": "Iris", "ShutterIrisMaxToMin": "Iris",
    "BeamZoomSmallBig": "Zoom", "BeamZoomBigSmall": "Zoom",
    "BeamFocusNearFar": "Focus", "BeamFocusFarNear": "Focus",
    "PrismRotationSlowFast": "Prism1Rot", "PrismRotationFastSlow": "Prism1Rot",
}
_COLOUR_ATTR = {"red": "Red", "green": "Green", "blue": "Blue", "white": "White", "cyan": "Cyan",
                "magenta": "Magenta", "yellow": "Yellow", "amber": "Amber", "lime": "Lime"}
_GOBO_PRESETS = {"GoboMacro", "GoboShakeMacro"}
_ROTATION_NAMES = {
    "RotationClockwiseSlowToFast": "CW (slow > fast)", "RotationClockwiseFastToSlow": "CW (fast > slow)",
    "RotationCounterClockwiseSlowToFast": "CCW (slow > fast)",
    "RotationCounterClockwiseFastToSlow": "CCW (fast > slow)",
    "RotationClockwise": "CW", "RotationCounterClockwise": "CCW", "RotationStop": "No rotation",
}


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _num(text, default=0.0) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _hex_color(text):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (text or "").strip())
    if not m:
        return None
    h = m.group(1)
    return tuple(_srgb_to_linear(int(h[i:i + 2], 16) / 255.0) for i in (0, 2, 4))


_COLOR_NAMES = (
    ("light blue", (0.0, 0.45, 1.0)), ("dark blue", (0.0, 0.01, 0.8)), ("deep blue", (0.0, 0.01, 0.8)),
    ("blue", (0.0, 0.03, 1.0)), ("red", (1.0, 0.0, 0.0)), ("orange", (1.0, 0.25, 0.0)), ("amber", (1.0, 0.45, 0.0)),
    ("yellow", (1.0, 0.8, 0.0)), ("lime", (0.6, 1.0, 0.0)), ("green", (0.0, 0.8, 0.05)), ("cyan", (0.0, 0.8, 1.0)),
    ("magenta", (1.0, 0.0, 0.45)), ("pink", (1.0, 0.3, 0.5)), ("purple", (0.35, 0.0, 1.0)),
    ("violet", (0.35, 0.0, 1.0)), ("lavender", (0.5, 0.35, 1.0)), ("uv", (0.35, 0.0, 1.0)),
    ("indigo", (0.15, 0.0, 0.9)), ("turquoise", (0.0, 0.8, 0.6)), ("teal", (0.0, 0.7, 0.55)),
)


def _wheel_color(cap):
    """Colour of a wheel slot: hex value when the file has one, otherwise guessed from the slot's name."""
    first = _hex_color(cap["res1"])
    second = _hex_color(cap["res2"]) if cap["preset"] == "ColorDoubleMacro" else None
    if first and second:
        return tuple((a + b) / 2 for a, b in zip(first, second))
    if first:
        return first
    text = cap["name"].lower()
    if re.search(r">|rotation|rainbow|random|scroll", text):
        return None
    for word, rgb in _COLOR_NAMES:
        if re.search(rf"\b{word}\b", text):
            return rgb
    return None


def _clean(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---- channel classification -----------------------------------------------------------------------------------
def _stem(name: str) -> str:
    base = re.split(r"[,\-/]", name, maxsplit=1)[0].lower()
    return re.sub(r"\b(fine|lsb|16[- ]?bit|position)\b", "", base).strip()


class _Chan:
    """One <Channel> definition with everything the mode loop needs."""

    def __init__(self, el):
        self.name = el.get("Name", "")
        self.preset = el.get("Preset", "")
        group_el = el.find("Group")
        self.group = _clean(group_el.text) if group_el is not None else ""
        self.byte = int(group_el.get("Byte", "0")) if group_el is not None else 0
        self.colour = _clean(el.findtext("Colour"))
        self.default = _num(el.get("Default"), -1.0)
        self.caps = [{
            "min": int(_num(c.get("Min"))), "max": int(_num(c.get("Max"), 255)), "preset": c.get("Preset", ""),
            "name": _clean(c.text), "res1": c.get("Res1", c.get("Color", "")), "res2": c.get("Res2", ""),
        } for c in el.findall("Capability")]
        self.is_fine = self.preset.endswith("Fine") or self.byte == 1
        coarse_preset = self.preset[:-4] if self.preset.endswith("Fine") else self.preset
        self.key = coarse_preset if coarse_preset in _PRESET_ATTR else f"{self.group}|{_stem(self.name)}"

    def has_cap(self, *words) -> bool:
        return any(w in c["name"].lower() for c in self.caps for w in words)


def _gobo_wheel(name: str) -> int:
    m = re.search(r"(?:gobo|wheel)\D{0,8}(\d)", name.lower())
    return int(m.group(1)) if m else 1


def _classify(ch: _Chan) -> str:
    if ch.preset in _PRESET_ATTR:
        return _PRESET_ATTR[ch.preset]
    if ch.preset.endswith("Fine") and ch.preset[:-4] in _PRESET_ATTR:
        return _PRESET_ATTR[ch.preset[:-4]]
    name, group = ch.name.lower(), ch.group
    if group in ("Speed", "Maintenance", "Nothing"):
        return "Control"
    if re.search(r"\bct[ocb]\b|colou?r temp", name):
        return "CTO"
    for word, attr in (("iris", "Iris"), ("frost", "Frost"), ("zoom", "Zoom"), ("focus", "Focus")):
        if word in name:
            return attr
    if "prism" in name or group == "Prism":
        return "Prism1Rot" if re.search(r"rot|spin|velocity", name) else "Prism1"
    if group in ("Intensity", "Colour") and ch.colour.lower() in _COLOUR_ATTR:
        return _COLOUR_ATTR[ch.colour.lower()]
    if group == "Pan":
        return "Pan"
    if group == "Tilt":
        return "Tilt"
    if group == "Intensity":
        return "Dimmer"
    if group == "Colour":
        return "ColorWheel"
    if group == "Gobo":
        wheel = _gobo_wheel(ch.name)
        if wheel > 2:
            return "Control"
        has_macros = any(c["preset"] in _GOBO_PRESETS for c in ch.caps)
        rotates = re.search(r"rot|velocity|position|index|spin", name) and not has_macros
        return f"Gobo{wheel}Rot" if rotates else f"Gobo{wheel}"
    if group == "Shutter" or "shutter" in name or "strobe" in name:
        closes = ch.has_cap("close", "blackout") or any(c["preset"] == "ShutterClose" for c in ch.caps)
        return "Shutter" if closes or "shutter" in name else "Strobe"
    if group == "Effect" and ch.has_cap("frost"):
        return "Frost"
    return "Control"


# ---- functions ------------------------------------------------------------------------------------------------
def _find_gobo(rel: str, gobo_dirs) -> str:
    if not rel or Path(rel).suffix.lower() not in IMAGE_EXTENSIONS:
        return ""
    for d in gobo_dirs:
        p = Path(d) / rel
        if p.is_file():
            return str(p)
    return ""


def _shutter_kind(cap) -> str:
    preset, text = cap["preset"], cap["name"].lower()
    if preset == "ShutterClose":
        return "Closed"
    if preset.startswith("Strobe"):
        return "Strobe"
    if preset:
        return "Open"
    if re.search(r"clos|blackout", text) and not re.search(r"pulse|open", text):
        return "Closed"
    return "Strobe" if re.search(r"strobe|flash", text) else "Open"


def _build_functions(attr: str, ch: _Chan, gobo_dirs, zoom_range) -> list:
    out, slot = [], 0
    wheel_has_macros = any(c["preset"] in _GOBO_PRESETS or c["res1"] for c in ch.caps)
    for i, cap in enumerate(ch.caps):
        lo, hi = max(0, min(cap["min"], 255)), max(0, min(cap["max"], 255))
        if cap["preset"] == "Alias":
            continue
        f = ChannelFunction(cap["name"], lo, max(hi, lo))
        if attr in ("Shutter", "Strobe"):
            kind = _shutter_kind(cap)
            f.name = f"{kind} - {cap['name']}"
            if kind == "Strobe":
                lo_hz, hi_hz = DEFAULT_STROBE_HZ
                if "FastToSlow" in cap["preset"]:
                    f.phys_from, f.phys_to = hi_hz, lo_hz
                elif cap["preset"] in ("StrobeFreqRange", "StrobeFrequency") and _num(cap["res1"], -1) >= 0:
                    f.phys_from = _num(cap["res1"])
                    f.phys_to = _num(cap["res2"], f.phys_from) if cap["res2"] else f.phys_from
        elif attr == "ColorWheel":
            f.color = _wheel_color(cap)
        elif re.fullmatch(r"Gobo\d", attr):
            if cap["preset"] in _GOBO_PRESETS or cap["res1"]:
                if "open" in cap["res1"].lower() or cap["name"].lower().startswith("open"):
                    f.slot = 0
                else:
                    slot += 1
                    f.slot = slot
                f.image = _find_gobo(cap["res1"], gobo_dirs)
            elif not wheel_has_macros:
                f.slot = i   # plain list of named positions
        elif attr.endswith("Rot"):
            f.name = _ROTATION_NAMES.get(cap["preset"], cap["name"])
        elif attr == "Prism1":
            f.name = {"PrismEffectOn": "Prism on", "PrismEffectOff": "Prism off"}.get(cap["preset"], cap["name"])
        out.append(f)
    if attr == "Zoom" and zoom_range and ch.preset in ("BeamZoomSmallBig", "BeamZoomBigSmall"):
        lo_deg, hi_deg = zoom_range
        small_first = ch.preset == "BeamZoomSmallBig"
        out = [ChannelFunction("Zoom", 0, 255, lo_deg if small_first else hi_deg, hi_deg if small_first else lo_deg)]
    elif attr == "Iris" and ch.preset in ("ShutterIrisMinToMax", "ShutterIrisMaxToMin"):
        name = "Closed > Open" if ch.preset == "ShutterIrisMinToMax" else "Open > Closed"
        out = [ChannelFunction(name, 0, 255)]
    elif attr in ("Strobe",) and not out and ch.preset in ("ShutterStrobeSlowFast", "ShutterStrobeFastSlow"):
        lo_hz, hi_hz = DEFAULT_STROBE_HZ
        out = [ChannelFunction("Strobe", 1, 255, *((lo_hz, hi_hz) if ch.preset.endswith("SlowFast")
                                                    else (hi_hz, lo_hz)))]
    return out


def _default_raw(attr: str, nbytes: int, funcs, declared: float) -> int:
    top = 256 ** nbytes - 1
    if declared >= 0:
        return min(int(declared) << (8 * (nbytes - 1)), top)
    if attr in ("Pan", "Tilt"):
        return (top + 1) // 2
    first = {"Shutter": "open", "Gobo1Rot": "no rotation", "Gobo2Rot": "no rotation", "Prism1Rot": "no rotation"}
    word = first.get(attr)
    if word:
        for f in funcs:
            if f.name.lower().startswith(word) or word in f.name.lower():
                return f.dmx_from << (8 * (nbytes - 1))
    return 0


# ---- fixture ----------------------------------------------------------------------------------------------------
def _source(bulb_type: str) -> str:
    t = bulb_type.lower()
    if "led" in t:
        return "LED"
    if re.search(r"hmi|msd|msr|discharge|metal|mh|hid", t):
        return "Discharge"
    if re.search(r"halogen|tungsten|incandescent|hpl|lamp|bulb", t):
        return "Tungsten"
    return "Other"


def _kind(qtype: str) -> str:
    t = qtype.lower()
    if "moving" in t or "scanner" in t:
        return "MOVING_HEAD"
    if "color changer" in t or "colour changer" in t or "led bar" in t:
        return "LED"
    if "dimmer" in t:
        return "CONVENTIONAL"
    if any(w in t for w in ("strobe", "effect", "laser", "flower")):
        return "EFFECT"
    return "OTHER"


def gobo_dirs_for(path: Path) -> list:
    """QLC+ keeps gobo images in a `gobos` folder next to `fixtures`."""
    return [p / "gobos" for p in list(path.parents)[:3] if (p / "gobos").is_dir()]


def import_qxf(path: str | Path, gobo_dirs=None) -> FixtureType:
    path = Path(path)
    root = ET.parse(path).getroot()
    _strip_ns(root)
    gobo_dirs = gobo_dirs if gobo_dirs is not None else gobo_dirs_for(path)
    manufacturer, model = _clean(root.findtext("Manufacturer")), _clean(root.findtext("Model"))
    chans = {c.get("Name", ""): _Chan(c) for c in root.findall("Channel")}

    phys_el = next((m.find("Physical") for m in root.findall("Mode") if m.find("Physical") is not None),
                   root.find("Physical"))
    lens = phys_el.find("Lens") if phys_el is not None else None
    dmin, dmax = (_num(lens.get("DegreesMin")), _num(lens.get("DegreesMax"))) if lens is not None else (0.0, 0.0)
    zoom_range = (dmin, dmax) if 0 < dmin < dmax else None

    modes = []
    for mode_el in root.findall("Mode"):
        channels, by_key = [], {}
        for ref in sorted(mode_el.findall("Channel"), key=lambda e: int(_num(e.get("Number")))):
            ch = chans.get(_clean(ref.text)) or chans.get(ref.text or "")
            if ch is None:
                continue
            offset = int(_num(ref.get("Number"))) + 1
            if ch.is_fine:
                if ch.key in by_key:
                    by_key[ch.key].offsets.append(offset)
                continue   # a fine byte without a coarse channel carries no usable value
            attr = _classify(ch)
            funcs = _build_functions(attr, ch, gobo_dirs, zoom_range)
            dmx = DmxChannel(attribute=attr, name=ch.name, offsets=[offset], functions=funcs)
            dmx.default = _default_raw(attr, 1, funcs, ch.default)
            channels.append(dmx)
            by_key[ch.key] = dmx
        for dmx in channels:   # 16-bit defaults: re-scale to the final resolution
            if dmx.nbytes > 1:
                dmx.default = _default_raw(dmx.attribute, dmx.nbytes, dmx.functions, -1.0)
        modes.append(DmxMode(name=mode_el.get("Name", "Mode"), channels=channels))
    if not modes:
        raise ValueError(f"{path.name}: no modes")

    physical, optical, geometry = dict(PHYSICAL_DEFAULTS), dict(OPTICAL_DEFAULTS), dict(GEOMETRY_DEFAULTS)
    notes = ["Imported from a QLC+ fixture definition (community data, not verified by the manufacturer)."]
    lumens_known = False
    if phys_el is not None:
        bulb, dims = phys_el.find("Bulb"), phys_el.find("Dimensions")
        focus, tech = phys_el.find("Focus"), phys_el.find("Technical")
        power = _num(tech.get("PowerConsumption")) if tech is not None else 0.0
        btype = bulb.get("Type", "") if bulb is not None else ""
        optical["source"] = _source(btype)
        if bulb is not None and _num(bulb.get("ColourTemperature")) > 0:
            optical["color_temp"] = _num(bulb.get("ColourTemperature"))
        lumens = _num(bulb.get("Lumens")) if bulb is not None else 0.0
        if lumens > 0:
            optical["lumens"], lumens_known = lumens, True
        elif power > 0:
            optical["lumens"] = round(power * LUMENS_PER_WATT[optical["source"]])
            notes.append(f"Luminous flux is a guess ({optical['lumens']:.0f} lm from {power:.0f} W); enter the "
                         "real value in the Fixture Creator or use Calibration.")
        if dmin > 0:
            optical["beam_angle"] = (dmin + dmax) / 2 if zoom_range else dmin
            optical["field_angle"] = optical["beam_angle"] * FIELD_TO_BEAM
            if zoom_range:
                optical["zoom_min"], optical["zoom_max"] = dmin, dmax
            notes.append("Field angle is estimated from the beam angle.")
        if dims is not None:
            for key, attr in (("width", "Width"), ("depth", "Depth"), ("height", "Height")):
                if _num(dims.get(attr)) > 0:
                    physical[key] = _num(dims.get(attr)) / 1000.0
            physical["weight"] = _num(dims.get("Weight")) or None
        physical["power"] = power or None
        if focus is not None:
            geometry["pan_range"], geometry["tilt_range"] = _num(focus.get("PanMax")), _num(focus.get("TiltMax"))
    provenance = {"physical": "community", "dmx": "community",
                  "optical": "community" if lumens_known and dmin > 0 else "estimated"}
    if provenance["optical"] == "estimated":
        notes.append("Some optical values are estimated, so optical data is marked 'estimated'.")
    return FixtureType(
        id="qlc." + slugify(f"{manufacturer} {model}"), manufacturer=manufacturer, model=model,
        kind=_kind(_clean(root.findtext("Type"))), category=f"QLC+ {manufacturer}".strip(),
        physical=physical, optical=optical, geometry=geometry, modes=modes, provenance=provenance,
        notes=" ".join(notes))


# ---- library search ---------------------------------------------------------------------------------------------
def default_fixture_dirs() -> list:
    home = Path.home()
    candidates = [
        Path("/usr/share/qlcplus/fixtures"), Path("/usr/local/share/qlcplus/fixtures"), home / ".qlcplus" / "fixtures",
        home / ".var/app/org.qlcplus.QLCplus/.qlcplus/fixtures", home / "QLC+" / "Fixtures",
        home / "Documents" / "QLC+" / "Fixtures", Path("C:/QLC+/Fixtures"), Path("C:/Program Files/QLC+/Fixtures"),
        Path("C:/Program Files (x86)/QLC+/Fixtures"), Path("/Applications/QLC+.app/Contents/Resources/Fixtures"),
        home / "Library/Application Support/QLC+/Fixtures",
    ]
    return [p for p in candidates if p.is_dir()]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def search_fixtures(query: str, dirs, limit: int = 50) -> list:
    """Paths of .qxf files whose manufacturer/model (taken from the file name) contain every query word."""
    words = [_norm(w) for w in query.split() if _norm(w)]
    if not words:
        return []
    found = []
    for d in dirs:
        for p in sorted(Path(d).rglob("*.qxf")):
            stem = _norm(p.stem)
            if all(w in stem for w in words):
                found.append(p)
                if len(found) >= limit:
                    return found
    return found
