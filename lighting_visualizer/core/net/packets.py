"""sACN (ANSI E1.31) and Art-Net packet encoding / decoding. No sockets here."""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

SACN_PORT = 5568
ARTNET_PORT = 6454

_ACN_ID = b"ASC-E1.17\x00\x00\x00"
_ARTNET_ID = b"Art-Net\x00"
_VECTOR_ROOT_DATA = 4
_VECTOR_ROOT_EXTENDED = 8
_VECTOR_FRAMING_DATA = 2
_VECTOR_FRAMING_SYNC = 1
_OPT_PREVIEW, _OPT_TERMINATED = 0x80, 0x40
OP_ARTDMX = 0x5000
OP_ARTSYNC = 0x5200


@dataclass
class DmxPacket:
    protocol: str            # "sacn" | "artnet"
    universe: int            # as transmitted (sACN 1-based, Art-Net 15-bit port-address)
    data: bytes
    sequence: int = 0
    priority: int = 100
    source_name: str = ""
    source_id: str = ""      # CID for sACN, empty for Art-Net (the engine uses the sender IP)
    preview: bool = False
    terminated: bool = False
    sync_address: int = 0


@dataclass
class SyncPacket:
    protocol: str
    sync_address: int = 0
    sequence: int = 0
    source_id: str = ""


def sacn_multicast_group(universe: int) -> str:
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def _flags_length(length: int) -> bytes:
    return struct.pack(">H", 0x7000 | length)


def build_sacn(universe: int, data: bytes, sequence: int = 0, priority: int = 100, source_name: str = "LV test",
               cid: bytes | None = None, sync_address: int = 0, preview: bool = False, terminated: bool = False) -> bytes:
    cid = cid or uuid.uuid4().bytes
    dmp = bytes([0x02, 0xA1]) + struct.pack(">HHH", 0, 1, len(data) + 1) + b"\x00" + data
    framing = (struct.pack(">I", _VECTOR_FRAMING_DATA) + source_name.encode()[:63].ljust(64, b"\x00")
               + struct.pack(">BHB", priority, sync_address, sequence & 0xFF)
               + bytes([(_OPT_PREVIEW if preview else 0) | (_OPT_TERMINATED if terminated else 0)])
               + struct.pack(">H", universe) + _flags_length(len(dmp) + 2) + dmp)
    root_body = struct.pack(">I", _VECTOR_ROOT_DATA) + cid + _flags_length(len(framing) + 2) + framing
    return (struct.pack(">HH", 0x0010, 0) + _ACN_ID + _flags_length(len(root_body) + 2) + root_body)


def build_sacn_sync(sync_address: int, sequence: int = 0, cid: bytes | None = None) -> bytes:
    cid = cid or uuid.uuid4().bytes
    framing = struct.pack(">I", _VECTOR_FRAMING_SYNC) + struct.pack(">BHH", sequence & 0xFF, sync_address, 0)
    root_body = struct.pack(">I", _VECTOR_ROOT_EXTENDED) + cid + _flags_length(len(framing) + 2) + framing
    return struct.pack(">HH", 0x0010, 0) + _ACN_ID + _flags_length(len(root_body) + 2) + root_body


def parse_sacn(buf: bytes):
    """Returns DmxPacket, SyncPacket or None (malformed, not E1.31, or an alternate start code)."""
    if len(buf) < 49 or buf[0:2] != b"\x00\x10" or buf[2:4] != b"\x00\x00" or buf[4:16] != _ACN_ID:
        return None
    root_vector = struct.unpack_from(">I", buf, 18)[0]
    cid = buf[22:38].hex()
    framing_vector = struct.unpack_from(">I", buf, 40)[0]
    if root_vector == _VECTOR_ROOT_EXTENDED and framing_vector == _VECTOR_FRAMING_SYNC:
        seq, sync_addr = struct.unpack_from(">BH", buf, 44)
        return SyncPacket("sacn", sync_addr, seq, cid)
    if root_vector != _VECTOR_ROOT_DATA or framing_vector != _VECTOR_FRAMING_DATA or len(buf) < 126:
        return None
    name = buf[44:108].split(b"\x00", 1)[0].decode("utf-8", "replace")
    priority = buf[108]
    sync_addr = struct.unpack_from(">H", buf, 109)[0]
    seq = buf[111]
    options = buf[112]
    universe = struct.unpack_from(">H", buf, 113)[0]
    count = struct.unpack_from(">H", buf, 123)[0]
    if buf[117] != 0x02 or count < 1 or len(buf) < 125 + count:
        return None
    if buf[125] != 0:  # only the NULL start code carries level data
        return None
    return DmxPacket("sacn", universe, bytes(buf[126:125 + count]), seq, priority, name, cid,
                     bool(options & _OPT_PREVIEW), bool(options & _OPT_TERMINATED), sync_addr)


def build_artnet(port_address: int, data: bytes, sequence: int = 0) -> bytes:
    if len(data) % 2:
        data += b"\x00"
    return (_ARTNET_ID + struct.pack("<H", OP_ARTDMX) + struct.pack(">H", 14)
            + bytes([sequence & 0xFF, 0, port_address & 0xFF, (port_address >> 8) & 0x7F])
            + struct.pack(">H", len(data)) + data)


def build_artnet_sync() -> bytes:
    return _ARTNET_ID + struct.pack("<H", OP_ARTSYNC) + struct.pack(">H", 14) + b"\x00\x00"


def parse_artnet(buf: bytes):
    """Returns DmxPacket (ArtDmx), SyncPacket (ArtSync) or None."""
    if len(buf) < 12 or buf[:8] != _ARTNET_ID:
        return None
    opcode = struct.unpack_from("<H", buf, 8)[0]
    if opcode == OP_ARTSYNC:
        return SyncPacket("artnet", 0, 0, "")
    if opcode != OP_ARTDMX or len(buf) < 18:
        return None
    seq = buf[12]
    sub_uni, net = buf[14], buf[15]
    length = struct.unpack_from(">H", buf, 16)[0]
    data = bytes(buf[18:18 + length])
    if len(data) < min(length, 2):
        return None
    return DmxPacket("artnet", ((net & 0x7F) << 8) | sub_uni, data, seq)
