# Ligting-visualizer-blender-addon
Lighting fixtures, sACN / Art-Net input, GDTF and MVR


Install
  python3 build.py            -> lighting_visualizer.zip
  Blender: Edit > Preferences > Get Extensions > Install from Disk > lighting_visualizer.zip
  Sidebar (N) in the 3D viewport > "Lighting".

Fixture library
  Add Lamps lists generic profiles (estimated data) and ~75 real GDTF files from GDTF Share (library/gdtf, with index.json),
  labelled "GDTF (manufacturer)" or "GDTF (community)" by uploader. Their DMX modes, optics and 3D models (glTF, 3DS) are
  used: base, yoke and head follow pan / tilt. The lens glows with the lamp's output and colour.
  Refresh or extend the files: GDTF_USER=... GDTF_PASS=... python3 tools/gdtf_share.py fetch LIST.json tools/gdtf_wanted.txt
  Powered by GDTF (https://gdtf-share.com).

Tests
  python3 -m pytest tests                      core: packets, DMX engine, receiver (loopback), fixtures, GDTF, MVR
  blender -b --factory-startup --python tests/blender_smoke.py     add-on inside Blender (writes test fixtures to the
                                                                   user library of that Blender; delete afterwards)
  python3 tools/send_test.py sacn --universe 1     send test DMX without a console
  blender -b --factory-startup --python tests/render_check.py -- out.png [prism]   Cycles render of gobo / prism

Console setup (grandMA2 / grandMA3)
  Output sACN (preferred) or Art-Net on the DMX protocol settings to the computer's IP or multicast. Dedicated wired
  network recommended. The add-on listens by itself (checkbox "Listen" in Lighting Rig); no start button. It follows
  the universes the lamps are patched to. DMX Input > Settings: protocol, adapter (multicast), universe filter.
  sACN universe numbers are used as-is; Art-Net port-address 0 is internal universe 1 (Art-Net Offset = 1).
  Universe Mapping remaps incoming numbers. Same priority sources are merged highest-takes-precedence per slot.

Testing with QLC+
  QLC+ > Inputs/Outputs: pick universe 1, enable Output on "E1.31" (sACN) or "ArtNet", Configure it. Same PC:
  unicast to 127.0.0.1; other PC: its IP. Art-Net universe 0 / sACN universe 1 are both internal universe 1.
  Blender > Lighting > Lighting Rig: the status line turns to "Receiving ..." and each lamp shows what it does.
  QLC+ > Fixtures: add the same fixture and mode at the same universe/address you give it in Blender.
  Fixture Library > QLC+ Fixtures: type e.g. "mac 2000 profile" > Import Matching. This reads QLC+'s own .qxf files
  (/usr/share/qlcplus/fixtures, ~/.qlcplus/fixtures, Windows/macOS install folders, or set Folder), so the DMX
  modes match QLC+ exactly. Data is community-made: channel ranges (shutter, strobe, iris, frost, gobo, rotation,
  zoom), pan / tilt range, beam angle, weight and power are used; luminous flux is guessed from power when missing.
  SVG gobo images cannot be loaded by Blender, PNG ones are; assign your own images in the Gobos panel.

Lighting Rig panel
  One row per lamp: universe, address (edit directly), live summary (dimmer %, pan / tilt, "dark: shutter closed",
  "no data on universe N"). Add Lamps > Count = 20 patches 20 lamps one after another. Patch All / Patch Selected
  renumber from a start address (natural name order). Fixture Inspector shows what each DMX channel value means.

  Beams are drawn as cones in the viewport (Show Beams) so changes are visible in Solid mode too; real light
  and haze need Material Preview / Rendered. A lamp left in Test Mode ignores DMX; the rig warns and offers "Use DMX".

  Generic moving heads use a typical DMX chart (shutter 0-7 closed / 8-15 open / 16-131 strobe, 8 colour slots + rainbow,
  7 gobos + shake + scroll, prism on at 128+). After updating the add-on press "Update Lamp Definitions" so existing
  lamps pick up changed definitions. Inspector channel numbers are absolute (address + offset).

Fixtures
  Fixture Library: generic profiles (all marked "estimated") + personal library + GDTF / QLC+ import. MVR import places
  fixtures with patch, trusses / objects as empties (glTF geometry is imported). Fixture Inspector shows DMX and the
  calculated state; Test Mode drives a fixture with sliders. Fixture Creator edits modes / channels / ranges, imports
  GLB / GLTF / FBX / OBJ / STL and saves to the personal library. Beam points down (-Z) at pan = tilt = 0 (DMX 50 %).

MVR export (Fixture Library > Export MVR)
  Writes fixtures (world transform, patch, fixture ID, GDTF file) and, with Export Meshes, every visible non-fixture
  mesh / curve object as a glTF scene object. Apply Modifiers applies all modifiers as shown in the viewport to the
  exported meshes; object scale is baked into the geometry. Selected Only limits the export to the selection.
  Fixtures imported from GDTF / MVR embed their original .gdtf; others get a minimal generated .gdtf (geometry,
  beam data, DMX modes / channels; no wheels or 3D model). Layers and groups come from the custom properties
  lv_layer / lv_group (set on MVR import). Not tested against other software yet.
