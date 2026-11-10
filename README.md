# Lighting Visualizer for Blender

Stage lighting in Blender, driven live from a lighting console. Patch real fixtures, send sACN or Art-Net from
grandMA2 / grandMA3 (or QLC+, or any other DMX source) and watch the virtual rig respond: pan and tilt move the actual
fixture model, the beam follows zoom, iris, frost, gobos and framing blades, and the lens glows with the output.

Works as a Blender extension (Blender 4.2 or newer, developed and tested on Blender 5.2 LTS).

> **Status: 0.2.0, early.** The simulation is physically motivated but **not validated against measurements**.
> Generic fixtures use estimated numbers and say so. Use the Calibration panel to compare with a light meter.

## Features

**Live DMX input**
- sACN (E1.31) and Art-Net, multicast / unicast / broadcast, several universes at once
- Universe mapping, sACN priority handling, sequence checks, sync packets, hold-last-look or blackout on signal loss
- Starts by itself and follows the universes your lamps are patched to
- Receives from Blender's timer with non-blocking sockets: no threads, so it cannot crash Blender

**Real fixtures**
- 83 real GDTF files from GDTF Share, bundled and ready to use (list in [docs/fixtures.md](docs/fixtures.md)):
  Robe, Martin, Clay Paky, Ayrton, GLP, Chauvet, ETC, Robert Juliat, Selecon, Astera, Ape Labs and more
- Their real DMX modes, optics and physical data, and their real 3D models: base, yoke and head follow pan and tilt.
  Fixtures without a mesh get real-size shapes from the GDTF geometry
- Browse by category (Moving Heads, Theatre Profiles, Fresnel / PC, PAR / LED PAR, LED Wash / Bar / Strip,
  Strobe / Blinder / Effect, Wireless / Battery, Other Theatre) and search; community uploads are marked
- Import your own GDTF, QLC+ (`.qxf`) or MVR files, export MVR, build custom fixtures in the Fixture Creator
  (modes, channels, ranges, your own 3D model, IES photometry) and keep them in a personal library
- Generic profiles (PAR, Fresnel, profiles, moving heads, LED fixtures) for quick layouts, all marked *estimated*

**Fixture behaviour**
- Pan / tilt, dimmer, RGB / RGBW / CMY, colour wheel, CTO, zoom, focus, iris, frost, shutter and strobe
- Gobos with real wheel images, gobo rotation and indexing, prism
- Framing blades (shutters) with rotation, for the fixtures that have blade channels
- The lens is emissive: 0 when the lamp is dark, 1 at full output, in the lamp's colour
- Fog slider (0 = clear air, 1 = dense fog; it creates the fog box by itself); Real-Time (EEVEE) and High Quality (Cycles) modes

Gobos and framing blades are rendered with Cycles light nodes, so they show in High Quality mode.

## Install

1. Download `lighting_visualizer-0.2.0.zip` from the [Releases](../../releases) page, or build it yourself:
   `python3 build.py` (creates `lighting_visualizer.zip`).
2. Blender: *Edit > Preferences > Get Extensions > Install from Disk* and pick the zip.
3. Open the 3D viewport sidebar (N) and the **Lighting** tab.

Network access is only used to receive DMX from your console. Files are used to import / export fixtures and to store
your personal fixture library.

## Quick start

1. **Add Lamps**: choose a category, search, pick a fixture and mode, set the universe and address, press *Add Fixture*.
   *Count* patches several lamps one after another.
2. Send DMX from your console to the computer (see below). The status line in **Lighting Rig** turns to
   *Receiving* and every lamp shows what it is doing.
3. No console? `python3 tools/send_test.py sacn --universe 1` sends test data, or use *Test Mode* on a lamp to drive it
   with sliders.
4. Use *High Quality* mode and the Fog slider to see beams in the air, or render with Cycles.

### Console setup (grandMA2 / grandMA3)

Output **sACN** (preferred) or Art-Net to the computer's IP address or to multicast. A dedicated wired network is
recommended. sACN universe numbers are used as they are; Art-Net port-address 0 is universe 1 (adjustable with
*Art-Net Offset*).

QLC+ works the same way: *Inputs / Outputs*, enable *E1.31* or *ArtNet* output, and use the same fixture, mode and
address in both programs.

## Accuracy and limits

- Not validated. Do not treat results as photometric data. The Blender watt conversion is an estimate; calibrate with
  real measurements (Calibration panel).
- Generic fixtures are estimated. GDTF fixtures use whatever their uploader entered; files uploaded by community users
  have not been checked by the manufacturer.
- Gobo projection and framing blades need Cycles. Prism is three facet lights with estimated spacing.
- Gobo wheel transitions, gobo shake, animation wheel and full GDTF export are not implemented. MVR export has not been
  tested against other software.
- Beam points down (-Z) at pan = tilt = 0.

## Development

```
python3 -m pytest tests                                    core: packets, DMX engine, receiver, fixtures, GDTF, MVR
blender -b --factory-startup --python tests/blender_smoke.py          add-on inside Blender
blender -b --factory-startup --python tests/blender_library_check.py  bundled fixtures, models, lens, fog
blender -b --factory-startup --python tests/blender_render_check.py   Cycles: framing blades and a gobo
```

(Use absolute paths for the scripts, and `flatpak run --filesystem=<dir> --command=blender org.blender.Blender` for the
Flatpak build.)

| Folder | What is in it |
|---|---|
| `lighting_visualizer/core` | Pure Python: fixtures, DMX evaluation, GDTF / MVR / QLC+, optics, network packets and engine (no `bpy`) |
| `lighting_visualizer/bl` | Blender side: panels, operators, fixture building, atmosphere, runtime timer |
| `lighting_visualizer/library/gdtf` | Bundled GDTF files and `index.json` |
| `tools/gdtf_share.py` | Downloads fixtures from GDTF Share (needs your own GDTF Share account) |
| `tools/send_test.py` | Sends test sACN / Art-Net |

## Credits and licences

- This add-on: GPL-3.0-or-later, see [LICENSE](LICENSE).
- **Powered by GDTF.** [GDTF](https://gdtf.eu) and [GDTF Share](https://gdtf-share.com). The bundled fixture files were
  downloaded through the GDTF Share API and remain the work of their uploaders (manufacturers and community users);
  see [NOTICE.md](NOTICE.md).
- [psutil](https://github.com/giampaolo/psutil) (BSD-3-Clause) is bundled as wheels to list network adapters.

## Support

Bug reports and ideas: [Issues](../../issues).
