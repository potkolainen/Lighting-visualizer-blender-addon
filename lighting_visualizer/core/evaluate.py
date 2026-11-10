"""Turns raw DMX values into a physical fixture state. Pure Python; knows nothing about networks or Blender."""
from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, field

from . import optics
from .fixture import FixtureType, DmxMode

DEFAULT_STROBE_HZ = (0.5, 20.0)        # estimated when the fixture defines no physical range
DEFAULT_GOBO_ROT_DPS = 360.0
DEFAULT_CTO_KELVIN = 3200.0
IRIS_MIN_OPEN = 0.1                    # estimated: fully closed iris still passes 10 % of the diameter
BLADE_ROT_RANGE = 45.0                 # estimated degrees either side when the fixture gives no physical range


@dataclass
class FixtureState:
    pan: float = 0.0                   # degrees from centre
    tilt: float = 0.0
    intensity: float = 1.0             # 0..1 dimmer
    rgb: tuple = (1.0, 1.0, 1.0)
    cct: float | None = None           # source colour temperature, K
    beam_angle: float = 25.0           # after zoom and iris
    field_angle: float = 40.0
    focus: float = 0.5
    frost: float = 0.0
    iris: float = 1.0                  # open fraction
    shutter_closed: bool = False
    strobe_hz: float = 0.0
    gobo_slot: int = 0                 # 0 = open
    gobo_image: str = ""
    gobo_rot_dps: float = 0.0
    gobo_index: float = 0.0            # degrees
    prism: bool = False
    prism_rot_dps: float = 0.0
    color_cycle_hz: float = 0.0        # colour wheel scrolling (rainbow): hue cycles per second, signed
    blades: tuple = ((0.0, 0.0, 0.0),) * 4   # framing blades: (insertion A, insertion B, rotation deg), 0 = out
    shaper_rot: float = 0.0            # degrees, turns the whole blade assembly
    values: dict = field(default_factory=dict)   # attribute -> normalized 0..1 (for the inspector)

    def shutter_factor(self, t: float) -> float:
        if self.shutter_closed:
            return 0.0
        if self.strobe_hz > 0.0:
            return 1.0 if (t * self.strobe_hz) % 1.0 < 0.5 else 0.0
        return 1.0

    @property
    def framed(self) -> bool:
        return any(a > 0.001 or b > 0.001 for a, b, _r in self.blades)

    @property
    def animated(self) -> bool:
        return (self.strobe_hz > 0.0 or self.gobo_rot_dps != 0.0 or self.prism_rot_dps != 0.0
                or self.color_cycle_hz != 0.0)

    def color_at(self, t: float) -> tuple:
        """Output colour at time t; differs from `rgb` only while the colour wheel scrolls."""
        if not self.color_cycle_hz:
            return self.rgb
        hue = colorsys.hsv_to_rgb((t * self.color_cycle_hz) % 1.0, 1.0, 1.0)
        return tuple(c * h for c, h in zip(self.rgb, hue))

    def dark_reason(self) -> str:
        """Why the fixture emits nothing right now, or "" when it is lit."""
        if self.shutter_closed:
            return "shutter closed"
        if self.intensity <= 0.001:
            return "dimmer at 0"
        if max(self.rgb) <= 0.001:
            return "colour channels at 0"
        return ""


def _signed_speed(n: float, max_dps: float) -> float:
    """0.5 = stop, below = counter-clockwise, above = clockwise, with a small dead band."""
    d = (n - 0.5) * 2.0
    return 0.0 if abs(d) < 0.02 else d * max_dps


_RAMP_UP = re.compile(r"slow\s*>\s*fast|min\s*>\s*max|0\s*<\s*100|0\s*>\s*100")
_RAMP_DOWN = re.compile(r"fast\s*>\s*slow|max\s*>\s*min")
_STOP = ("no rotation", "stop", "no function")


def _ramp(f, c: int) -> float:
    return (c - f.dmx_from) / max(f.dmx_to - f.dmx_from, 1)


def _function_rotation(ch, c: int):
    """Signed deg/s from the function names ("CW (slow > fast)", "No rotation", ...); None if not described."""
    f = ch.function_at(c)
    if f is None:
        return None
    label = f.name.lower()
    if any(s in label for s in _STOP):
        return 0.0
    sign = -1.0 if re.search(r"\bccw\b|counter|anti", label) else 1.0 if re.search(r"\bcw\b|clockwise", label) else 0.0
    if sign == 0.0:
        return None
    t = _ramp(f, c)
    if _RAMP_DOWN.search(label):
        t = 1.0 - t
    elif not _RAMP_UP.search(label):
        t = 0.5
    return sign * (0.05 + 0.95 * t) * DEFAULT_GOBO_ROT_DPS


def _rotation(ch, c: int, n: float) -> float:
    if any(re.search(r"\bc?cw\b|clockwise|no rotation", f.name.lower()) for f in ch.functions):
        return _function_rotation(ch, c) or 0.0
    return _signed_speed(n, DEFAULT_GOBO_ROT_DPS)


def _iris_closure(ch, c: int, n: float) -> float:
    """0 = open .. 1 = fully closed."""
    ramps = [f for f in ch.functions if re.search(r"open\s*>\s*clos|clos\w*\s*>\s*open", f.name.lower())]
    if not ramps:
        return n
    f = ch.function_at(c)
    if f is None:
        return 0.0
    label = f.name.lower()
    if re.search(r"open\s*>\s*clos", label):
        return _ramp(f, c)
    if re.search(r"clos\w*\s*>\s*open", label):
        return 1.0 - _ramp(f, c)
    return 1.0 if label.strip().startswith("closed") else 0.0


def _frost_amount(ch, c: int, n: float) -> float:
    if not any("frost" in f.name.lower() for f in ch.functions):
        return n
    f = ch.function_at(c)
    return _ramp(f, c) if f is not None and "frost" in f.name.lower() else 0.0


_AMBER = (1.0, 0.55, 0.0)
_LIME = (0.7, 1.0, 0.0)


def evaluate(ftype: FixtureType, mode: DmxMode, data, address: int = 1) -> FixtureState:
    """`data` is a 512-slot universe buffer, `address` the fixture's 1-based start address."""
    opt = ftype.optical
    st = FixtureState(beam_angle=opt.get("beam_angle") or 25.0, field_angle=opt.get("field_angle") or 40.0)
    raw, coarse = {}, {}
    for ch in mode.channels:
        r = ch.raw(data, address)
        raw[ch.attribute] = (ch, r)
        coarse[ch.attribute] = ch.coarse(r)
        st.values[ch.attribute] = ch.normalized(r)

    def n(attr, default=0.0):
        return st.values.get(attr, default)

    geo = ftype.geometry
    if "Pan" in st.values:
        st.pan = (n("Pan") - 0.5) * (geo.get("pan_range") or 540.0)
    if "Tilt" in st.values:
        st.tilt = (n("Tilt") - 0.5) * (geo.get("tilt_range") or 270.0)

    # Intensity. A fixture without a dimmer channel is driven by its colour channels instead.
    st.intensity = n("Dimmer") if "Dimmer" in st.values else 1.0

    # Colour: additive emitters, then subtractive CMY, colour wheel and CTO.
    rgb = [1.0, 1.0, 1.0]
    if any(a in st.values for a in ("Red", "Green", "Blue", "White", "Amber", "Lime")):
        w = n("White")
        a, li = n("Amber"), n("Lime")
        rgb = [min(n("Red") + w + a * _AMBER[0] + li * _LIME[0], 1.0),
               min(n("Green") + w + a * _AMBER[1] + li * _LIME[1], 1.0),
               min(n("Blue") + w + a * _AMBER[2] + li * _LIME[2], 1.0)]
    if any(a in st.values for a in ("Cyan", "Magenta", "Yellow")):
        rgb = [rgb[0] * (1 - n("Cyan")), rgb[1] * (1 - n("Magenta")), rgb[2] * (1 - n("Yellow"))]
    if "ColorWheel" in raw:
        ch, _ = raw["ColorWheel"]
        f = ch.function_at(coarse["ColorWheel"])
        slot_color = _slot_color(ftype, ch, f)
        if slot_color:
            rgb = [c * s for c, s in zip(rgb, slot_color)]
        elif f is not None:
            rot = _function_rotation(ch, coarse["ColorWheel"])
            if rot:
                st.color_cycle_hz = rot / DEFAULT_GOBO_ROT_DPS
    if "CTO" in st.values and n("CTO") > 0.0:
        warm = optics.cct_to_rgb(DEFAULT_CTO_KELVIN)
        src = optics.cct_to_rgb(opt.get("color_temp") or 5600.0)
        amount = n("CTO")
        rgb = [min(c * (1 - amount + amount * w / max(s, 1e-6)), 1.0) for c, w, s in zip(rgb, warm, src)]
    st.rgb = tuple(rgb)
    st.cct = opt.get("color_temp")

    # Optics.
    zmin, zmax = opt.get("zoom_min"), opt.get("zoom_max")
    if "Zoom" in st.values and zmin and zmax:
        ch, _ = raw["Zoom"]
        f = ch.function_at(coarse["Zoom"])
        if f is not None and f.phys_from is not None and f.phys_to is not None:
            st.beam_angle = f.physical(coarse["Zoom"], zmin, zmax)
        else:
            st.beam_angle = zmin + (zmax - zmin) * n("Zoom")
        nominal_beam = opt.get("beam_angle") or st.beam_angle
        st.field_angle = st.beam_angle * (opt.get("field_angle") or nominal_beam) / nominal_beam
    if "Iris" in st.values:
        ch, _ = raw["Iris"]
        st.iris = 1.0 - _iris_closure(ch, coarse["Iris"], n("Iris")) * (1.0 - IRIS_MIN_OPEN)
        before = st.beam_angle
        st.beam_angle = optics.iris_beam_angle(before, st.iris)
        st.field_angle *= st.beam_angle / before
    if "Focus" in st.values:
        ch, r = raw["Focus"]
        st.focus = 0.5 + n("Focus") - ch.normalized(ch.default)   # blur follows the distance from the home value
    if "Frost" in st.values:
        ch, _ = raw["Frost"]
        st.frost = _frost_amount(ch, coarse["Frost"], n("Frost"))

    # Shutter / strobe.
    if "Shutter" in raw:
        ch, _ = raw["Shutter"]
        f = ch.function_at(coarse["Shutter"])
        label = f.name.lower() if f else ""
        if label.startswith("open"):
            pass
        elif "close" in label or "blackout" in label:
            st.shutter_closed = True
        elif "strobe" in label:
            lo, hi = DEFAULT_STROBE_HZ
            st.strobe_hz = f.physical(coarse["Shutter"], lo, hi)
    if "Strobe" in raw:
        ch, _ = raw["Strobe"]
        c = coarse["Strobe"]
        f = ch.function_at(c)
        label = f.name.lower() if f else ""
        if c > 0 and "open" not in label and "no " not in label:
            lo, hi = DEFAULT_STROBE_HZ
            st.strobe_hz = f.physical(c, lo, hi) if f else lo + (hi - lo) * c / 255.0

    # Gobo wheel 1 (further wheels are exposed through `values` only).
    if "Gobo1" in raw:
        ch, _ = raw["Gobo1"]
        f = ch.function_at(coarse["Gobo1"])
        if f is not None and f.slot is not None:
            st.gobo_slot = f.slot
            st.gobo_image = _slot_image(ftype, ch, f)
    if "Gobo1Rot" in st.values:
        ch, _ = raw["Gobo1Rot"]
        st.gobo_rot_dps = _rotation(ch, coarse["Gobo1Rot"], n("Gobo1Rot"))
    if "Gobo1Pos" in st.values:
        st.gobo_index = n("Gobo1Pos") * 360.0
    if "Prism1" in raw:
        ch, _ = raw["Prism1"]
        f = ch.function_at(coarse["Prism1"])
        off = ("open", "none", "off", "no prism")
        st.prism = (not any(w in f.name.lower() for w in off)) if f else coarse["Prism1"] > 0
    if "Prism1Rot" in st.values and st.prism:
        ch, _ = raw["Prism1Rot"]
        st.prism_rot_dps = _rotation(ch, coarse["Prism1Rot"], n("Prism1Rot"))
    # Framing blades (4 shutters; B is the second end of a blade that can tilt, otherwise it follows A).
    if any(a.startswith(("Blade", "Shaper")) for a in st.values):
        def angle(attr):
            if attr not in raw:
                return 0.0
            ch, _ = raw[attr]
            f = ch.function_at(coarse[attr])
            return f.physical(coarse[attr], -BLADE_ROT_RANGE, BLADE_ROT_RANGE) if f else (n(attr) - 0.5) * 2 * BLADE_ROT_RANGE
        st.blades = tuple((n(f"Blade{i}A"), n(f"Blade{i}B", n(f"Blade{i}A")), angle(f"Blade{i}Rot")) for i in range(1, 5))
        st.shaper_rot = angle("ShaperRot")
    return st


def _slot_entry(ftype: FixtureType, ch, f):
    slots = ftype.wheels.get(ch.wheel) if ch.wheel else None
    if slots and f is not None and f.slot is not None and 0 <= f.slot < len(slots):
        return slots[f.slot]
    return None


def _slot_color(ftype, ch, f):
    if f is None:
        return None
    if f.color:
        return f.color
    entry = _slot_entry(ftype, ch, f)
    return tuple(entry["color"]) if entry and entry.get("color") else None


def _slot_image(ftype, ch, f) -> str:
    if f.image:
        return f.image
    entry = _slot_entry(ftype, ch, f)
    return entry.get("image", "") if entry else ""
