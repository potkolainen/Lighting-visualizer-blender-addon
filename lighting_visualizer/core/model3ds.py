"""Minimal 3D Studio (.3ds) reader: triangle meshes only, enough for the fixture models inside GDTF files."""
from __future__ import annotations

import struct

_MAIN, _EDITOR, _OBJECT, _TRIMESH, _VERTICES, _FACES = 0x4D4D, 0x3D3D, 0x4000, 0x4100, 0x4110, 0x4120


def unit_scale(meshes: list, size) -> float:
    """Metres per file unit: 3DS has no unit, so compare the mesh with the GDTF Model's declared size."""
    declared = max([s for s in size if s] or [0.0])
    coords = [v for _n, verts, _f in meshes for v in verts]
    if not coords or declared <= 0:
        return 1.0
    extent = max(max(c[i] for c in coords) - min(c[i] for c in coords) for i in range(3))
    for scale in (1.0, 0.001, 0.01, 0.0254):
        if extent * scale and 0.5 < extent * scale / declared < 2.0:
            return scale
    return 1.0


def read_3ds(data: bytes) -> list:
    """[(name, [(x, y, z)], [(a, b, c)])] for every mesh object in the file."""
    meshes = []

    def chunks(start, end):
        pos = start
        while pos + 6 <= end:
            cid, length = struct.unpack_from("<HI", data, pos)
            if length < 6:
                return
            yield cid, pos + 6, min(pos + length, end)
            pos += length

    def read_mesh(name, start, end):
        verts, faces = [], []
        for cid, body, stop in chunks(start, end):
            if cid == _VERTICES:
                n = struct.unpack_from("<H", data, body)[0]
                verts = [struct.unpack_from("<3f", data, body + 2 + 12 * i) for i in range(n)]
            elif cid == _FACES:
                n = struct.unpack_from("<H", data, body)[0]
                faces = [struct.unpack_from("<3H", data, body + 2 + 8 * i) for i in range(n)]
        if verts and faces:
            meshes.append((name, verts, faces))

    def read_object(start, end):
        nul = data.index(b"\x00", start, end)
        name = data[start:nul].decode("latin-1")
        for cid, body, stop in chunks(nul + 1, end):
            if cid == _TRIMESH:
                read_mesh(name, body, stop)

    if len(data) < 6 or struct.unpack_from("<H", data, 0)[0] != _MAIN:
        raise ValueError("not a 3DS file")
    for cid, body, stop in chunks(6, len(data)):
        if cid == _EDITOR:
            for cid2, body2, stop2 in chunks(body, stop):
                if cid2 == _OBJECT:
                    read_object(body2, stop2)
    return meshes
