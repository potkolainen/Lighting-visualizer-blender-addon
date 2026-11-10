"""Built-in generic fixture profiles, bundled GDTF Share fixtures and the user's personal library.

The generic profiles are not manufacturer data: every number is marked "estimated" and every channel layout is a generic
template. The bundled GDTF files (library/gdtf, listed in index.json) are real manufacturer / community GDTF files from
GDTF Share, parsed only when a fixture is first used.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import gdtf
from .fixture import (FixtureType, PHYSICAL_DEFAULTS, OPTICAL_DEFAULTS, GEOMETRY_DEFAULTS, mode_from_template,
                      slugify)

BUNDLED_DIR = Path(__file__).resolve().parents[1] / "library" / "gdtf"

_EST = {"physical": "estimated", "optical": "estimated", "dmx": "estimated"}
_NOTE = "Generic approximation, not manufacturer data. Edit in the Fixture Creator or import the manufacturer GDTF."


def _fx(fid, mfr, model, kind, category, beam, field_, modes, *, source="LED", lumens=None, zoom=None,
        size=(0.25, 0.25, 0.35), cct=5600.0, pan=0.0, tilt=0.0, note=_NOTE, desc=""):
    optical = {**OPTICAL_DEFAULTS, "source": source, "beam_angle": float(beam), "field_angle": float(field_),
               "lumens": lumens, "color_temp": cct}
    if zoom:
        optical["zoom_min"], optical["zoom_max"] = float(zoom[0]), float(zoom[1])
    return FixtureType(
        id=fid, manufacturer=mfr, model=model, kind=kind, category=category, description=desc,
        physical={**PHYSICAL_DEFAULTS, "width": size[0], "depth": size[1], "height": size[2]},
        optical=optical, geometry={**GEOMETRY_DEFAULTS, "pan_range": pan, "tilt_range": tilt},
        modes=[mode_from_template(n, t) for n, t in modes], provenance=dict(_EST), notes=note)


def _builtin() -> list:
    out = []
    mh = (("Standard", "Moving head spot 16bit"),)
    # Moving heads
    out.append(_fx("generic.mh_spot", "Generic", "Moving Head Spot", "MOVING_HEAD", "Moving Heads", 15, 25, mh,
                   source="Discharge", lumens=9000, zoom=(10, 40), size=(0.3, 0.3, 0.45), pan=540, tilt=270))
    out.append(_fx("generic.mh_wash", "Generic", "Moving Head Wash", "MOVING_HEAD", "Moving Heads", 20, 45,
                   (("Standard", "Moving head wash 16bit"),), lumens=8000, zoom=(12, 50),
                   size=(0.3, 0.3, 0.4), pan=540, tilt=270))
    out.append(_fx("generic.mh_beam", "Generic", "Moving Head Beam", "MOVING_HEAD", "Moving Heads", 2.5, 4,
                   (("Standard", "Moving head beam 16bit"),), source="Discharge", lumens=9000,
                   size=(0.3, 0.3, 0.45), pan=540, tilt=270))
    out.append(_fx("generic.mh_led", "Generic", "LED Moving Head", "MOVING_HEAD", "Moving Heads", 20, 35,
                   (("Standard", "Moving head spot 16bit"),), lumens=12000, zoom=(8, 45),
                   size=(0.35, 0.35, 0.5), pan=540, tilt=270))
    out.append(_fx("generic.mh_profile", "Generic", "Moving Head Profile", "MOVING_HEAD", "Moving Heads", 15, 28, mh,
                   source="LED", lumens=15000, zoom=(6, 50), size=(0.4, 0.4, 0.6), pan=540, tilt=270))
    out.append(_fx("martin.minimac", "Martin", "MiniMAC (generic placeholder)", "MOVING_HEAD", "Moving Heads", 15, 25,
                   mh, source="Discharge", lumens=5000, zoom=(8, 35), size=(0.3, 0.3, 0.45), pan=540, tilt=270,
                   note=_NOTE + " Specifications of the real MiniMAC are NOT included."))

    # Theatre profiles. ETC names the Source Four by its FIELD angle; everything else is estimated.
    for fa in (19, 26, 36, 50):
        beam = round(fa * 0.62, 1)
        out.append(_fx(f"generic.profile_{fa}", "Generic", f"{fa} Degree Profile", "PROFILE", "Theatre Profiles",
                       beam, fa, (("Dimmer 1ch", "Dimmer 1ch"),), source="Tungsten", cct=3200.0,
                       lumens=None, size=(0.25, 0.25, 0.4)))
        s4 = _fx(f"etc.source_four_{fa}", "ETC", f"Source Four {fa}\u00b0", "PROFILE", "Theatre Profiles",
                 beam, fa, (("Dimmer 1ch", "Dimmer 1ch"),), source="Tungsten", cct=3200.0, size=(0.25, 0.25, 0.4),
                 note=_NOTE + " Only the field angle in the model name is the manufacturer's designation.")
        s4.provenance["optical"] = "estimated"
        out.append(s4)
    out.append(_fx("generic.zoom_profile_15_30", "Generic", "Zoom Profile 15-30\u00b0", "PROFILE", "Theatre Profiles",
                   15, 28, (("Dimmer 1ch", "Dimmer 1ch"),), source="Tungsten", cct=3200.0, size=(0.3, 0.3, 0.45)))
    out.append(_fx("generic.zoom_profile_25_50", "Generic", "Zoom Profile 25-50\u00b0", "PROFILE", "Theatre Profiles",
                   25, 45, (("Dimmer 1ch", "Dimmer 1ch"),), source="Tungsten", cct=3200.0, size=(0.3, 0.3, 0.45)))

    # Conventional
    dim = (("Dimmer 1ch", "Dimmer 1ch"),)
    conv = (("Fresnel", "Fresnel", "Fresnel", 40, 70, (0.25, 0.25, 0.3)),
            ("pc", "PC Spotlight (Plano Convex)", "PC", 25, 45, (0.25, 0.25, 0.3)),
            ("par56", "PAR56", "PAR", 20, 40, (0.25, 0.25, 0.25)),
            ("par64", "PAR64", "PAR", 20, 40, (0.3, 0.3, 0.35)),
            ("cp60", "PAR64 CP60 (narrow)", "PAR", 12, 24, (0.3, 0.3, 0.35)),
            ("cp61", "PAR64 CP61 (medium)", "PAR", 20, 40, (0.3, 0.3, 0.35)),
            ("cp62", "PAR64 CP62 (wide)", "PAR", 30, 60, (0.3, 0.3, 0.35)),
            ("flood", "Floodlight", "Floods", 90, 120, (0.3, 0.2, 0.25)),
            ("cyc", "CYC Light", "Floods", 100, 140, (0.4, 0.25, 0.3)),
            ("strip", "Strip Light", "Floods", 100, 140, (1.0, 0.2, 0.2)),
            ("blinder", "Blinder", "Floods", 100, 130, (0.3, 0.2, 0.2)))
    for key, model, grp, beam, field_, size in conv:
        out.append(_fx(f"generic.{key}", "Generic", model, "CONVENTIONAL", "Conventional Theatre", beam, field_, dim,
                       source="Tungsten", cct=3200.0, size=size))
    out.append(_fx("generic.led_batten", "Generic", "Theatre LED Batten", "LED", "Conventional Theatre", 100, 140,
                   (("RGBW 4ch", "RGBW 4ch"), ("Dimmer + RGBW 5ch", "Dimmer + RGBW 5ch")), lumens=3000,
                   size=(1.0, 0.12, 0.12)))

    # Additional
    out.append(_fx("generic.led_par", "Generic", "LED PAR", "LED", "LED", 25, 45,
                   (("RGB 3ch", "RGB 3ch"), ("Dimmer + RGB 4ch", "Dimmer + RGB 4ch")), lumens=1500,
                   size=(0.2, 0.2, 0.2)))
    out.append(_fx("generic.rgbw_wash", "Generic", "RGBW Wash", "LED", "LED", 40, 70,
                   (("Dimmer + RGBW 5ch", "Dimmer + RGBW 5ch"),), lumens=4000, size=(0.25, 0.25, 0.25)))
    out.append(_fx("generic.led_profile", "Generic", "LED Profile", "LED", "LED", 20, 35,
                   (("Dimmer + RGBW 5ch", "Dimmer + RGBW 5ch"),), lumens=6000, zoom=(15, 45),
                   size=(0.3, 0.3, 0.4)))
    out.append(_fx("generic.followspot", "Generic", "Followspot", "PROFILE", "Special", 6, 10, dim,
                   source="Discharge", cct=5600.0, lumens=20000, size=(0.5, 0.5, 1.2)))
    out.append(_fx("generic.strobe", "Generic", "Strobe", "EFFECT", "Special", 100, 140,
                   (("Dimmer 1ch", "Dimmer 1ch"),), source="Discharge", cct=6000.0, size=(0.3, 0.1, 0.15)))
    out.append(_fx("generic.audience_blinder", "Generic", "Audience Blinder", "CONVENTIONAL", "Special", 100, 130,
                   dim, source="Tungsten", cct=3200.0, size=(0.4, 0.15, 0.2)))
    return out


class Library:
    def __init__(self, user_dir: str | Path | None = None, bundled_dir: str | Path | None = BUNDLED_DIR,
                 cache_dir: str | Path | None = None):
        self.user_dir = Path(user_dir) if user_dir else None
        self.bundled_dir = Path(bundled_dir) if bundled_dir else None
        self.cache_dir = Path(cache_dir) if cache_dir else None   # where bundled GDTF models / images are unpacked
        self._types: dict = {}
        self._bundled: dict = {}
        self._failed: set = set()
        self.reload()

    def reload(self):
        self._types = {t.id: t for t in _builtin()}
        self._user_ids = set()
        self._bundled, self._failed = {}, set()
        self._load_bundled_index()
        if self.user_dir and self.user_dir.is_dir():
            for f in sorted(self.user_dir.glob("*.json")):
                try:
                    t = FixtureType.from_dict(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, ValueError, KeyError):
                    continue
                self._types[t.id] = t
                self._user_ids.add(t.id)

    def _load_bundled_index(self):
        index = self.bundled_dir / "index.json" if self.bundled_dir else None
        if index is None or not index.is_file():
            return
        try:
            entries = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for fname, meta in entries.items():
            if not (self.bundled_dir / fname).is_file():
                continue
            fid = f"gdtf.share.{meta['rid']}"
            maker = meta.get("uploader") == "Manuf."
            who = "the manufacturer" if maker else "a community user"
            self._types[fid] = FixtureType(
                id=fid, manufacturer=meta.get("manufacturer", ""), model=meta.get("fixture", fname),
                category=meta.get("category") or ("GDTF (manufacturer)" if maker else "GDTF (community)"),
                provenance={k: "manufacturer" if maker else "community" for k in ("physical", "optical", "dmx")},
                description=f"GDTF Share revision {meta.get('revision', '')}, uploaded by {who}.",
                notes=f"GDTF file from GDTF Share, uploaded by {who}.")
            self._bundled[fid] = fname

    def _parse_bundled(self, fid: str):
        stub = self._types[fid]
        try:
            t = gdtf.import_gdtf(self.bundled_dir / self._bundled[fid], self.cache_dir)
        except Exception:  # a broken file must not take the whole library down
            self._failed.add(fid)
            return stub
        quality = self._types[fid].provenance["dmx"]
        t.id, t.manufacturer, t.model, t.category = fid, stub.manufacturer, stub.model, stub.category
        t.provenance = {k: quality for k in ("physical", "optical", "dmx")}
        t.notes = stub.notes
        self._types[fid] = t
        return t

    def get(self, fid: str):
        t = self._types.get(fid)
        if t is not None and not t.modes and fid in self._bundled and fid not in self._failed:
            t = self._parse_bundled(fid)
        return t

    def all(self) -> list:
        return sorted(self._types.values(), key=lambda t: (t.category or "zzz", t.label.lower()))

    def categories(self) -> list:
        return sorted({t.category or "User" for t in self._types.values()})

    def is_user(self, fid: str) -> bool:
        return fid in self._user_ids

    def save_user(self, ftype: FixtureType) -> Path:
        if self.user_dir is None:
            raise RuntimeError("No user library directory configured")
        if not ftype.id.startswith("user."):
            ftype.id = "user." + slugify(ftype.label)
        ftype.category = ftype.category or "User"
        self.user_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_dir / f"{slugify(ftype.id)}.json"
        path.write_text(json.dumps(ftype.to_dict(), indent=1), encoding="utf-8")
        self._types[ftype.id] = ftype
        self._user_ids.add(ftype.id)
        return path

    def delete_user(self, fid: str) -> bool:
        if fid not in self._user_ids or self.user_dir is None:
            return False
        (self.user_dir / f"{slugify(fid)}.json").unlink(missing_ok=True)
        self._types.pop(fid, None)
        self._user_ids.discard(fid)
        return True
