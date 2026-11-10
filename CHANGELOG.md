# Changelog

## 0.2.0

- 83 real GDTF fixtures bundled (manufacturer and community uploads, labelled), with category filter and search
- Real 3D models from the GDTF files (glTF and 3DS): base, yoke and head follow pan / tilt; real-size shapes from the
  GDTF geometry where a file has no mesh
- Gobo images from GDTF wheels (transparent areas let light through)
- Framing blades and shaper rotation
- Emissive lens that follows dimmer and colour
- Fog slider (0 = clear air, 1 = dense fog) using a fog box instead of a world volume; Fit Fog to Scene
- GDTF 1.2 position matrices
- Receiver no longer uses threads (non-blocking sockets read from Blender's timer)
- psutil bundled as wheels
- Bug tracker and website links in the manifest

## 0.1.0

- First release: sACN / Art-Net input, generic fixture library, GDTF / MVR / QLC+ import, Fixture Creator,
  gobos, prism, haze, calibration
