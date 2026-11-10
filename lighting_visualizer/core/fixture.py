"""Universal fixture data structure shared by the library, creator, GDTF/MVR importers and the DMX engine."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Where a number came from; the UI shows this next to every optical value.
PROVENANCE = ("manufacturer", "community", "user", "estimated")

ATTRIBUTES = (
    ("Dimmer", "Dimmer"), ("Pan", "Pan"), ("Tilt", "Tilt"),
    ("Red", "Red"), ("Green", "Green"), ("Blue", "Blue"), ("White", "White"), ("Amber", "Amber"), ("Lime", "Lime"),
    ("Cyan", "Cyan"), ("Magenta", "Magenta"), ("Yellow", "Yellow"),
    ("CTO", "CTO / CTB"), ("ColorWheel", "Color Wheel"),
    ("Zoom", "Zoom"), ("Focus", "Focus"), ("Iris", "Iris"), ("Frost", "Frost"),
    ("Shutter", "Shutter"), ("Strobe", "Strobe"),
    ("Gobo1", "Gobo Wheel 1"), ("Gobo1Rot", "Gobo 1 Rotation"), ("Gobo1Pos", "Gobo 1 Index"),
    ("Gobo2", "Gobo Wheel 2"), ("Gobo2Rot", "Gobo 2 Rotation"), ("Gobo2Pos", "Gobo 2 Index"),
    ("Prism1", "Prism"), ("Prism1Rot", "Prism Rotation"),
    ("AnimationWheel", "Animation Wheel"), ("Control", "Control / Other"),
)
ATTRIBUTE_IDS = frozenset(a for a, _ in ATTRIBUTES)

KINDS = (
    ("MOVING_HEAD", "Moving Head"), ("PROFILE", "Profile"), ("CONVENTIONAL", "Conventional"),
    ("LED", "LED"), ("EFFECT", "Effect"), ("OTHER", "Other"),
)

OPTICAL_DEFAULTS = {
    "source": "LED",          # LED / Halogen / Discharge / Tungsten / Other
    "lumens": None,           # total luminous flux at full output
    "candela": None,          # on-axis intensity at the nominal beam angle
    "beam_angle": 25.0,       # degrees, full angle (50 % of peak)
    "field_angle": 40.0,      # degrees, full angle (10 % of peak)
    "zoom_min": None,         # narrowest / widest beam angle for zoom fixtures
    "zoom_max": None,
    "color_temp": 5600.0,     # K
    "cri": None,
    "lens": "",
    "distribution": "gaussian",  # gaussian / flat / ies
    "ies_file": "",
}

PHYSICAL_DEFAULTS = {"width": 0.2, "depth": 0.2, "height": 0.3, "weight": None, "power": None}

GEOMETRY_DEFAULTS = {
    "pan_range": 0.0,         # mechanical range in degrees, 0 = fixed
    "tilt_range": 0.0,
    "lens_offset": None,      # [x, y, z] metres from the tilt pivot, None = derived from dimensions
    "model_file": "",
    "base_objects": [], "yoke_objects": [], "head_objects": [],
    "pan_pivot": None, "tilt_pivot": None,
}


@dataclass
class ChannelFunction:
    name: str = ""
    dmx_from: int = 0         # always on the coarse byte, 0..255
    dmx_to: int = 255
    phys_from: float | None = None
    phys_to: float | None = None
    slot: int | None = None   # 0-based position in the wheel
    color: tuple | None = None  # linear-ish rgb 0..1 for colour-wheel slots
    image: str = ""

    def contains(self, coarse: int) -> bool:
        return self.dmx_from <= coarse <= self.dmx_to

    def physical(self, coarse: int, default_lo: float, default_hi: float) -> float:
        lo = default_lo if self.phys_from is None else self.phys_from
        hi = default_hi if self.phys_to is None else self.phys_to
        span = max(self.dmx_to - self.dmx_from, 1)
        return lo + (hi - lo) * (coarse - self.dmx_from) / span


@dataclass
class DmxChannel:
    attribute: str
    name: str = ""
    offsets: list = field(default_factory=lambda: [1])  # 1-based, coarse first
    default: int = 0                                     # raw value at the channel's own resolution
    functions: list = field(default_factory=list)
    wheel: str = ""

    @property
    def nbytes(self) -> int:
        return max(1, len(self.offsets))

    @property
    def max_raw(self) -> int:
        return 256 ** self.nbytes - 1

    def raw(self, data, base: int) -> int:
        """Raw channel value from a universe buffer; `base` is the fixture's 1-based start address."""
        value = 0
        for off in self.offsets:
            idx = base - 1 + off - 1
            if not 0 <= idx < len(data):
                return self.default
            value = (value << 8) | data[idx]
        return value

    def coarse(self, raw: int) -> int:
        return raw >> (8 * (self.nbytes - 1))

    def normalized(self, raw: int) -> float:
        return raw / self.max_raw

    def function_at(self, coarse: int):
        for f in self.functions:
            if f.contains(coarse):
                return f
        return None


@dataclass
class DmxMode:
    name: str
    channels: list = field(default_factory=list)

    @property
    def footprint(self) -> int:
        return max((o for c in self.channels for o in c.offsets), default=0)

    def channel(self, attribute: str):
        for c in self.channels:
            if c.attribute == attribute:
                return c
        return None


@dataclass
class FixtureType:
    id: str
    manufacturer: str = ""
    model: str = ""
    kind: str = "OTHER"
    category: str = ""
    description: str = ""
    physical: dict = field(default_factory=lambda: dict(PHYSICAL_DEFAULTS))
    optical: dict = field(default_factory=lambda: dict(OPTICAL_DEFAULTS))
    geometry: dict = field(default_factory=lambda: dict(GEOMETRY_DEFAULTS))
    modes: list = field(default_factory=list)
    wheels: dict = field(default_factory=dict)       # wheel name -> [{"name", "color", "image"}]
    provenance: dict = field(default_factory=dict)   # "optical"/"physical"/"dmx" -> PROVENANCE value
    notes: str = ""

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model}".strip() or self.id

    def mode(self, name: str | int | None = None):
        if not self.modes:
            return None
        if isinstance(name, int):
            return self.modes[min(max(name, 0), len(self.modes) - 1)]
        for m in self.modes:
            if m.name == name:
                return m
        return self.modes[0]

    def has_attribute(self, attribute: str, mode=None) -> bool:
        modes = [mode] if mode is not None else self.modes
        return any(m.channel(attribute) is not None for m in modes)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "manufacturer": self.manufacturer, "model": self.model, "kind": self.kind,
            "category": self.category, "description": self.description,
            "physical": self.physical, "optical": self.optical, "geometry": self.geometry,
            "modes": [{"name": m.name, "channels": [{
                "attribute": c.attribute, "name": c.name, "offsets": list(c.offsets), "default": c.default,
                "wheel": c.wheel,
                "functions": [{"name": f.name, "dmx_from": f.dmx_from, "dmx_to": f.dmx_to,
                               "phys_from": f.phys_from, "phys_to": f.phys_to, "slot": f.slot,
                               "color": list(f.color) if f.color else None, "image": f.image}
                              for f in c.functions],
            } for c in m.channels]} for m in self.modes],
            "wheels": self.wheels, "provenance": self.provenance, "notes": self.notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "FixtureType":
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls, d: dict) -> "FixtureType":
        modes = []
        for m in d.get("modes", []):
            chans = []
            for c in m.get("channels", []):
                funcs = [ChannelFunction(
                    name=f.get("name", ""), dmx_from=int(f.get("dmx_from", 0)), dmx_to=int(f.get("dmx_to", 255)),
                    phys_from=f.get("phys_from"), phys_to=f.get("phys_to"), slot=f.get("slot"),
                    color=tuple(f["color"]) if f.get("color") else None, image=f.get("image", ""))
                    for f in c.get("functions", [])]
                chans.append(DmxChannel(
                    attribute=c.get("attribute", "Control"), name=c.get("name", ""),
                    offsets=[int(o) for o in c.get("offsets", [1])], default=int(c.get("default", 0)),
                    functions=funcs, wheel=c.get("wheel", "")))
            modes.append(DmxMode(name=m.get("name", "Mode"), channels=chans))
        return cls(
            id=d["id"], manufacturer=d.get("manufacturer", ""), model=d.get("model", ""),
            kind=d.get("kind", "OTHER"), category=d.get("category", ""), description=d.get("description", ""),
            physical={**PHYSICAL_DEFAULTS, **d.get("physical", {})},
            optical={**OPTICAL_DEFAULTS, **d.get("optical", {})},
            geometry={**GEOMETRY_DEFAULTS, **d.get("geometry", {})},
            modes=modes, wheels=d.get("wheels", {}), provenance=d.get("provenance", {}), notes=d.get("notes", ""))


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "fixture"


def channel(attribute: str, offset, default: int = 0, name: str = "", functions=None, wheel: str = "") -> DmxChannel:
    """Shorthand: `offset` may be an int or a (coarse, fine) tuple."""
    offs = [offset] if isinstance(offset, int) else list(offset)
    return DmxChannel(attribute=attribute, name=name or attribute, offsets=offs, default=default,
                      functions=functions or [], wheel=wheel)


def _lay_out(specs) -> list:
    """Assign consecutive offsets to (attribute, bytes, default, functions) specs."""
    out, pos = [], 1
    for attribute, nb, default, funcs in specs:
        out.append(channel(attribute, tuple(range(pos, pos + nb)), default, functions=funcs))
        pos += nb
    return out


# Typical moving-head DMX ranges (Robe / Martin style charts); real fixtures differ, so import the real definition
# (GDTF / QLC+) when you have it.
def _shutter():
    F = ChannelFunction
    return [F("Closed", 0, 7), F("Open", 8, 15), F("Strobe - slow > fast", 16, 131), F("Open", 132, 255)]


_WHEEL_COLORS = (
    ("Red", (1.0, 0.0, 0.0)), ("Orange", (1.0, 0.25, 0.0)), ("Yellow", (1.0, 0.8, 0.0)),
    ("Green", (0.0, 0.8, 0.05)), ("Light blue", (0.0, 0.45, 1.0)), ("Blue", (0.0, 0.03, 1.0)),
    ("Magenta", (1.0, 0.0, 0.45)), ("UV / purple", (0.35, 0.0, 1.0)),
)


def _color_wheel():
    F = ChannelFunction
    out = [F("Open / white", 0, 7)]
    for i, (name, rgb) in enumerate(_WHEEL_COLORS):
        out.append(F(name, 8 + 8 * i, 15 + 8 * i, color=rgb))
    out += [F("Open / white", 72, 127), F("Rainbow CW (slow > fast)", 128, 191),
            F("Rainbow CCW (slow > fast)", 192, 255)]
    return out


def _gobo_wheel(count: int = 7):
    F = ChannelFunction
    out = [F("Open", 0, 7, slot=0)]
    out += [F(f"Gobo {n}", 8 * n, 8 * n + 7, slot=n) for n in range(1, count + 1)]
    out += [F(f"Gobo {n} shake", 64 + 8 * (n - 1), 71 + 8 * (n - 1), slot=n) for n in range(1, count + 1)]
    out += [F("Wheel scroll CW (slow > fast)", 128, 191), F("Wheel scroll CCW (slow > fast)", 192, 255)]
    return out


def _prism():
    F = ChannelFunction
    return [F("Open (no prism)", 0, 127), F("Prism 3-facet", 128, 255)]


# Generic channel layouts used for the built-in profiles and as creator templates.
MODE_TEMPLATES = {
    "Dimmer 1ch": lambda: [("Dimmer", 1, 0, None)],
    "RGB 3ch": lambda: [("Red", 1, 0, None), ("Green", 1, 0, None), ("Blue", 1, 0, None)],
    "Dimmer + RGB 4ch": lambda: [("Dimmer", 1, 0, None), ("Red", 1, 255, None), ("Green", 1, 255, None),
                                 ("Blue", 1, 255, None)],
    "RGBW 4ch": lambda: [("Red", 1, 0, None), ("Green", 1, 0, None), ("Blue", 1, 0, None), ("White", 1, 0, None)],
    "Dimmer + RGBW 5ch": lambda: [("Dimmer", 1, 0, None), ("Red", 1, 255, None), ("Green", 1, 255, None),
                                  ("Blue", 1, 255, None), ("White", 1, 0, None)],
    "Moving head spot 16bit": lambda: [
        ("Pan", 2, 32768, None), ("Tilt", 2, 32768, None), ("Dimmer", 1, 0, None),
        ("Shutter", 1, 8, _shutter()), ("Zoom", 1, 0, None), ("Focus", 1, 128, None),
        ("Iris", 1, 0, None), ("Frost", 1, 0, None), ("ColorWheel", 1, 0, _color_wheel()), ("CTO", 1, 0, None),
        ("Gobo1", 1, 0, _gobo_wheel()), ("Gobo1Rot", 1, 128, None), ("Prism1", 1, 0, _prism()),
        ("Prism1Rot", 1, 128, None)],
    "Moving head wash 16bit": lambda: [
        ("Pan", 2, 32768, None), ("Tilt", 2, 32768, None), ("Dimmer", 1, 0, None),
        ("Shutter", 1, 8, _shutter()), ("Red", 1, 0, None), ("Green", 1, 0, None), ("Blue", 1, 0, None),
        ("White", 1, 0, None), ("Zoom", 1, 0, None), ("CTO", 1, 0, None)],
    "Moving head beam 16bit": lambda: [
        ("Pan", 2, 32768, None), ("Tilt", 2, 32768, None), ("Dimmer", 1, 0, None),
        ("Shutter", 1, 8, _shutter()), ("ColorWheel", 1, 0, _color_wheel()), ("Gobo1", 1, 0, _gobo_wheel()),
        ("Prism1", 1, 0, _prism()), ("Frost", 1, 0, None)],
}


def mode_from_template(name: str, template: str) -> DmxMode:
    return DmxMode(name=name, channels=_lay_out(MODE_TEMPLATES[template]()))
