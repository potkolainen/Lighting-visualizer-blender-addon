"""Unified DMX engine: both protocols are merged here, the fixture engine only ever sees per-universe buffers."""
from __future__ import annotations

import time
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass, field

from .packets import SyncPacket

SOURCE_TIMEOUT = 2.5          # E1.31 network data loss timeout
ARTNET_SOURCE_TIMEOUT = 10.0  # Art-Net has no loss timeout; some senders only resend on change
UNIVERSE_SIZE = 512
ARTNET_SYNC_TIMEOUT = 4.0      # Art-Net nodes fall back to immediate mode after 4 s without ArtSync
ARTNET_SYNC_KEY = -1


@dataclass
class EngineConfig:
    universes: frozenset = frozenset()           # internal numbering; empty = accept every universe
    mapping: dict = field(default_factory=dict)  # (protocol|"any", source universe) -> internal universe
    artnet_offset: int = 1                       # Art-Net port-address 0 is "universe 1" on grandMA
    artnet_priority: int = 100
    accept_preview: bool = False
    loss_policy: str = "hold"                    # "hold" | "blackout" when every source of a universe is lost


@dataclass
class SourceInfo:
    key: str
    protocol: str
    name: str
    ip: str
    priority: int
    last_seen: float = 0.0
    packets: int = 0
    seq_discarded: int = 0
    seq_lost: int = 0
    universes: set = field(default_factory=set)


class _Universe:
    def __init__(self):
        self.sources: dict = {}                  # source key -> [priority, bytearray, last_seen]
        self.output = bytearray(UNIVERSE_SIZE)
        self.last_data = 0.0


class DmxEngine:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = nullcontext()               # everything runs on Blender's main thread
        self.config = EngineConfig()
        self._universes: dict = {}
        self._sources: dict = {}
        self._last_seq: dict = {}                # (source key, universe) -> last sequence
        self._held: dict = {}                    # sync address -> {(source key, universe): (DmxPacket, ip)}
        self._artnet_sync_until = 0.0
        self._changed: set = set()
        self._times: deque = deque()
        self.total_packets = 0
        self.rejected_packets = 0
        self.last_packet_time = 0.0
        self.last_source_ip = ""
        self.last_protocol = ""
        self._ignored: dict = {}                 # (protocol, universe) -> packets dropped by the universe filter

    # configuration -------------------------------------------------------------------------------------------
    def configure(self, config: EngineConfig):
        with self._lock:
            self.config = config

    def reset(self):
        with self._lock:
            self._universes.clear()
            self._sources.clear()
            self._last_seq.clear()
            self._held.clear()
            self._artnet_sync_until = 0.0
            self._changed.clear()
            self._times.clear()
            self.total_packets = self.rejected_packets = 0
            self.last_packet_time = 0.0
            self.last_source_ip = ""
            self.last_protocol = ""
            self._ignored.clear()

    # ingest (called by the receiver) ---------------------------------------------------------------------------------
    def ingest(self, packet, ip: str = ""):
        """Entry point for both receivers; accepts DmxPacket and SyncPacket."""
        now = self._clock()
        with self._lock:
            if isinstance(packet, SyncPacket):
                if packet.protocol == "artnet":
                    self._artnet_sync_until = now + ARTNET_SYNC_TIMEOUT
                    packet = SyncPacket("artnet", ARTNET_SYNC_KEY)
                self._apply_sync(packet, now)
                return
            if packet.preview and not self.config.accept_preview:
                self.rejected_packets += 1
                return
            key = self._source_key(packet, ip)
            internal = self._map(packet)
            if internal is None:
                self.rejected_packets += 1
                k = (packet.protocol, packet.universe)
                self._ignored[k] = self._ignored.get(k, 0) + 1
                return
            if not self._sequence_ok(key, packet, now):
                return
            self._touch_source(key, packet, ip, internal, now)
            if packet.terminated:
                self._drop_source(key, now, only_universe=internal)
                return
            if packet.protocol == "sacn" and packet.sync_address:
                self._held.setdefault(packet.sync_address, {})[(key, internal)] = (packet, ip)
                return
            if packet.protocol == "artnet" and now < self._artnet_sync_until:
                self._held.setdefault(ARTNET_SYNC_KEY, {})[(key, internal)] = (packet, ip)
                return
            self._store(key, packet, internal, now)

    def _source_key(self, packet, ip: str) -> str:
        return f"sacn:{packet.source_id}" if packet.protocol == "sacn" else f"artnet:{ip}"

    def _map(self, packet):
        mapping = self.config.mapping
        target = mapping.get((packet.protocol, packet.universe), mapping.get(("any", packet.universe)))
        if target is None:
            target = packet.universe + (self.config.artnet_offset if packet.protocol == "artnet" else 0)
        allowed = self.config.universes
        return target if (not allowed or target in allowed) else None

    def _sequence_ok(self, key: str, packet, now: float) -> bool:
        # Art-Net sequence 0 means "disabled"; E1.31 discards packets that are 0..19 steps behind (signed 8 bit).
        if packet.protocol == "artnet" and packet.sequence == 0:
            return True
        k = (key, packet.universe)
        last = self._last_seq.get(k)
        self._last_seq[k] = packet.sequence
        if last is None:
            return True
        diff = (packet.sequence - last + 128) % 256 - 128
        info = self._sources.get(key)
        if -20 < diff <= 0:
            self._last_seq[k] = last
            if info:
                info.seq_discarded += 1
            self.rejected_packets += 1
            return False
        if diff > 1 and info:
            info.seq_lost += diff - 1
        return True

    def _touch_source(self, key, packet, ip, internal, now):
        info = self._sources.get(key)
        if info is None:
            info = self._sources[key] = SourceInfo(key, packet.protocol, packet.source_name or ip, ip, 0)
        priority = packet.priority if packet.protocol == "sacn" else self.config.artnet_priority
        info.priority, info.last_seen = priority, now
        info.packets += 1
        info.universes.add(internal)
        if packet.source_name:
            info.name = packet.source_name
        self.total_packets += 1
        self.last_packet_time = now
        self.last_source_ip = ip
        self.last_protocol = packet.protocol
        self._times.append(now)
        while self._times and now - self._times[0] > 1.0:
            self._times.popleft()

    def _store(self, key, packet, internal, now):
        uni = self._universes.setdefault(internal, _Universe())
        buf = bytearray(UNIVERSE_SIZE)
        n = min(len(packet.data), UNIVERSE_SIZE)
        buf[:n] = packet.data[:n]
        priority = packet.priority if packet.protocol == "sacn" else self.config.artnet_priority
        uni.sources[key] = [priority, buf, now]
        uni.last_data = now
        self._merge(internal, uni)

    def _merge(self, internal, uni: _Universe):
        """Highest priority wins; sources sharing that priority are merged highest-takes-precedence per slot."""
        if not uni.sources:
            return
        top = max(s[0] for s in uni.sources.values())
        out = bytearray(UNIVERSE_SIZE)
        for prio, buf, _ in uni.sources.values():
            if prio == top:
                out = bytearray(max(a, b) for a, b in zip(out, buf))
        if out != uni.output:
            uni.output = out
            self._changed.add(internal)

    def _apply_sync(self, packet: SyncPacket, now: float):
        for (key, internal), (pkt, _ip) in self._held.pop(packet.sync_address, {}).items():
            self._store(key, pkt, internal, now)

    def _drop_source(self, key, now, only_universe=None):
        info = self._sources.get(key)
        for internal, uni in self._universes.items():
            if only_universe is not None and internal != only_universe:
                continue
            if uni.sources.pop(key, None) is not None:
                self._after_source_loss(internal, uni)
        if info and only_universe is not None:
            info.universes.discard(only_universe)
            if not info.universes:
                self._sources.pop(key, None)
        elif only_universe is None:
            self._sources.pop(key, None)

    def _after_source_loss(self, internal, uni: _Universe):
        if uni.sources:
            self._merge(internal, uni)
        elif self.config.loss_policy == "blackout" and any(uni.output):
            uni.output = bytearray(UNIVERSE_SIZE)
            self._changed.add(internal)

    # main thread ---------------------------------------------------------------------------------------------
    def expire(self):
        """Drop sources that stopped sending; call periodically from the main thread."""
        now = self._clock()
        with self._lock:
            for key in [k for k, s in self._sources.items() if now - s.last_seen > _timeout(s.protocol)]:
                self._drop_source(key, now)

    def take_changed(self) -> dict:
        """Universes whose merged output changed since the last call, as {universe: bytes}."""
        with self._lock:
            out = {u: bytes(self._universes[u].output) for u in self._changed if u in self._universes}
            self._changed.clear()
            return out

    def universe_data(self, universe: int) -> bytes:
        with self._lock:
            uni = self._universes.get(universe)
            return bytes(uni.output) if uni else bytes(UNIVERSE_SIZE)

    def active_universes(self) -> list:
        with self._lock:
            return sorted(self._universes)

    def packet_rate(self) -> float:
        now = self._clock()
        with self._lock:
            while self._times and now - self._times[0] > 1.0:
                self._times.popleft()
            return float(len(self._times))

    def status(self) -> str:
        """"idle" (no data yet), "receiving" or "lost" (data seen before, then silence for the protocol's timeout)."""
        with self._lock:
            if self.total_packets == 0:
                return "idle"
            return "receiving" if self._clock() - self.last_packet_time <= _timeout(self.last_protocol) else "lost"

    def ignored_universes(self) -> list:
        """[(protocol, incoming universe)] seen on the network but rejected by the universe filter."""
        with self._lock:
            return sorted(self._ignored)

    def seconds_since_last_packet(self):
        with self._lock:
            return None if self.total_packets == 0 else self._clock() - self.last_packet_time

    def sources(self) -> list:
        with self._lock:
            return [SourceInfo(**{**s.__dict__, "universes": set(s.universes)}) for s in self._sources.values()]


def _timeout(protocol: str) -> float:
    return ARTNET_SOURCE_TIMEOUT if protocol == "artnet" else SOURCE_TIMEOUT


def parse_universe_list(text: str) -> frozenset:
    """"1-4, 10" -> {1,2,3,4,10}; invalid parts are ignored."""
    out = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                out.update(range(max(a, 0), min(b, 63999) + 1))
            elif part:
                out.add(int(part))
        except ValueError:
            continue
    return frozenset(u for u in out if 0 <= u <= 63999)


def sacn_subscriptions(config: EngineConfig) -> list:
    """sACN universes (source numbering) whose multicast groups must be joined."""
    subs = set()
    for target in config.universes:
        if not any(t == target for (p, _s), t in config.mapping.items() if p in ("sacn", "any")):
            subs.add(target)
    for (proto, src), target in config.mapping.items():
        if proto in ("sacn", "any") and (not config.universes or target in config.universes):
            subs.add(src)
    return sorted(u for u in subs if 1 <= u <= 63999)
