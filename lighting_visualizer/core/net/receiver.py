"""Non-blocking UDP receiver. poll() drains the sockets from the caller's timer, so no thread is needed."""
from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass, field

from .dmx_engine import DmxEngine, EngineConfig
from .packets import ARTNET_PORT, SACN_PORT, parse_artnet, parse_sacn, sacn_multicast_group


@dataclass
class ReceiverConfig:
    protocol: str = "auto"               # "auto" | "sacn" | "artnet"
    interface_ip: str = "0.0.0.0"        # adapter used for multicast subscriptions
    multicast: bool = True
    sacn_port: int = SACN_PORT
    artnet_port: int = ARTNET_PORT
    engine: EngineConfig = field(default_factory=EngineConfig)


class NetworkReceiver:
    def __init__(self, engine: DmxEngine):
        self.engine = engine
        self.errors: list = []
        self.ports: dict = {}            # "sacn"/"artnet" -> bound port
        self.joined: list = []
        self._iface = "0.0.0.0"
        self._multicast = False
        self._failed: set = set()
        self._socks: dict = {}

    @property
    def running(self) -> bool:
        return bool(self._socks)

    def start(self, config: ReceiverConfig):
        self.stop()
        self.errors, self.joined, self.ports = [], [], {}
        self._iface, self._multicast, self._failed = config.interface_ip, config.multicast, set()
        self.engine.reset()
        self.engine.configure(config.engine)
        self._socks = {}
        for proto, port in (("sacn", config.sacn_port), ("artnet", config.artnet_port)):
            if config.protocol in ("auto", proto):
                self._open(proto, port, config)

    def stop(self):
        for s in self._socks.values():
            try:
                s.close()
            except OSError:
                pass
        self._socks = {}
        self.joined = []

    # -------------------------------------------------------------------------------------------------------
    def _open(self, proto: str, port: int, config: ReceiverConfig):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if proto == "artnet":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Bound to all adapters so unicast and broadcast both arrive; the adapter choice applies to multicast.
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)  # absorbs bursts between polls
            except OSError:
                pass
            sock.bind(("0.0.0.0", port))
            sock.setblocking(False)
        except OSError as e:
            self.errors.append(f"{proto}: cannot bind UDP port {port}: {e}")
            sock.close()
            return
        self.ports[proto] = sock.getsockname()[1]
        self._socks[proto] = sock

    def update_multicast(self, universes):
        """Join / leave the sACN multicast groups so exactly `universes` are subscribed; safe to call any time."""
        sock = self._socks.get("sacn")
        if sock is None or not self._multicast:
            return
        want = {sacn_multicast_group(u) for u in universes if 1 <= u <= 63999}
        for group in sorted(want - set(self.joined) - self._failed):
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                socket.inet_aton(group) + socket.inet_aton(self._iface))
                self.joined.append(group)
            except OSError as e:
                self._failed.add(group)
                self.errors.append(f"sacn: cannot join {group} on {self._iface}: {e}")
        for group in sorted(set(self.joined) - want):
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,
                                socket.inet_aton(group) + socket.inet_aton(self._iface))
            except OSError:
                pass
            self.joined.remove(group)

    def poll(self, max_packets: int = 20000) -> int:
        """Read every datagram waiting on the sockets and hand it to the engine; returns the number read."""
        count = 0
        for proto, sock in self._socks.items():
            while count < max_packets:
                try:
                    buf, addr = sock.recvfrom(2048)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError as e:
                    if len(self.errors) < 20:
                        self.errors.append(f"{proto}: receive failed: {e}")
                    break
                count += 1
                packet = parse_sacn(buf) if proto == "sacn" else parse_artnet(buf)
                if packet is not None:
                    self.engine.ingest(packet, addr[0])
        return count


def list_ipv4_interfaces() -> list:
    """[(label, ip)] of local IPv4 adapters, best effort without third-party packages."""
    found = {}
    try:
        import psutil  # optional
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET:
                    found[a.address] = name
    except Exception:
        pass
    if not found and sys.platform.startswith("linux"):
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=2).stdout
            for line in out.splitlines():
                parts = line.split()
                found[parts[3].split("/")[0]] = parts[1]
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
    if not found:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                found[info[4][0]] = "host"
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # no traffic is sent; picks the default-route adapter
                found.setdefault(s.getsockname()[0], "default route")
        except OSError:
            pass
    return [(f"{name} ({ip})", ip) for ip, name in sorted(found.items(), key=lambda kv: kv[1])]
