"""Photometric helpers. Every conversion here is a documented approximation, not a validated model."""
from __future__ import annotations

import math

# Blender spot lights: radiant intensity = P / (4*pi^2) W/sr, 683 lm/W. UNVALIDATED - compare with a light meter
# and use the per-fixture / scene calibration factors to correct it.
BLENDER_WATTS_PER_CANDELA = 4.0 * math.pi ** 2 / 683.0


def cone_solid_angle(full_angle_deg: float) -> float:
    return 2.0 * math.pi * (1.0 - math.cos(math.radians(max(full_angle_deg, 0.01)) / 2.0))


def candela_from_lumens(lumens: float, beam_angle_deg: float) -> float:
    """Average intensity of a flat-topped cone; real fixtures have a peaked (gaussian-like) distribution."""
    return lumens / cone_solid_angle(beam_angle_deg)


def lumens_from_candela(candela: float, beam_angle_deg: float) -> float:
    return candela * cone_solid_angle(beam_angle_deg)


def beam_diameter(angle_deg: float, distance: float) -> float:
    return 2.0 * distance * math.tan(math.radians(angle_deg) / 2.0)


def illuminance_lux(candela: float, distance: float) -> float:
    """On-axis illuminance on a surface perpendicular to the beam."""
    return candela / max(distance, 1e-6) ** 2


def intensity_candela(optical: dict, beam_angle: float) -> float:
    """On-axis intensity at full output for the given (zoomed) beam angle."""
    lumens, candela = optical.get("lumens"), optical.get("candela")
    nominal = optical.get("beam_angle") or beam_angle
    if lumens:
        return candela_from_lumens(lumens, beam_angle)
    if candela:
        return candela * (nominal / max(beam_angle, 0.01)) ** 2
    return candela_from_lumens(1000.0, beam_angle)  # unknown fixture: 1000 lm placeholder


def blender_watts(candela: float) -> float:
    return candela * BLENDER_WATTS_PER_CANDELA


def cct_to_rgb(kelvin: float) -> tuple:
    """Approximate blackbody colour (Tanner Helland fit), normalised so the largest component is 1."""
    t = min(max(kelvin, 1000.0), 40000.0) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    rgb = [min(max(c, 0.0), 255.0) / 255.0 for c in (r, g, b)]
    m = max(rgb)
    return tuple(c / m for c in rgb)


def xyy_to_rgb(x: float, y: float, Y: float = 1.0) -> tuple:
    """CIE xyY (GDTF colour) to linear sRGB, normalised to a maximum of 1."""
    if y <= 0:
        return (1.0, 1.0, 1.0)
    X, Z = Y * x / y, Y * (1 - x - y) / y
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    b = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    rgb = [max(c, 0.0) for c in (r, g, b)]
    m = max(rgb) or 1.0
    return tuple(c / m for c in rgb)


def iris_beam_angle(beam_angle: float, iris_open: float) -> float:
    return math.degrees(2.0 * math.atan(math.tan(math.radians(beam_angle) / 2.0) * max(iris_open, 0.01)))


def edge_blend(beam_angle: float, field_angle: float, frost: float, focus: float) -> float:
    """Blender spot_blend (0..1): soft edge from beam/field ratio, widened by frost and defocus (estimated model)."""
    base = 1.0 - min(beam_angle / max(field_angle, beam_angle, 0.01), 1.0)
    blend = base + (1.0 - base) * frost + abs(focus - 0.5) * 0.3
    return min(max(blend, 0.02), 1.0)


def calibration_report(optical: dict, distance: float, measured_diameter: float = 0.0, measured_lux: float = 0.0) -> dict:
    """Predicted vs measured beam diameter / illuminance and the correction factors."""
    beam = optical.get("beam_angle") or 25.0
    pred_d = beam_diameter(beam, distance)
    pred_lux = illuminance_lux(intensity_candela(optical, beam), distance)
    return {
        "predicted_diameter": pred_d, "predicted_lux": pred_lux,
        "beam_scale": (measured_diameter / pred_d) if measured_diameter > 0 else 1.0,
        "intensity_scale": (measured_lux / pred_lux) if measured_lux > 0 and pred_lux > 0 else 1.0,
    }
