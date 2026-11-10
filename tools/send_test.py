"""Send test DMX to the visualizer without a console.

    python3 tools/send_test.py sacn --host 127.0.0.1 --universe 1 --address 1 --pattern chase
    python3 tools/send_test.py artnet --host 255.255.255.255 --universe 0
    python3 tools/send_test.py sacn --multicast --universe 1

Patterns: ramp (all channels sweep), chase (one channel at a time), full, blackout.
"""
import argparse
import math
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighting_visualizer.core.net import packets  # noqa: E402


def frame(pattern: str, t: float, address: int, count: int) -> bytes:
    data = bytearray(512)
    channels = range(address - 1, min(address - 1 + count, 512))
    if pattern == "full":
        for i in channels:
            data[i] = 255
    elif pattern == "ramp":
        for n, i in enumerate(channels):
            data[i] = int((math.sin(t * 2 + n) * 0.5 + 0.5) * 255)
    elif pattern == "chase":
        step = int(t * 2) % max(count, 1)
        data[address - 1 + step] = 255
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", choices=("sacn", "artnet"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--multicast", action="store_true", help="sACN: send to the universe's multicast group")
    ap.add_argument("--universe", type=int, default=1, help="sACN universe, or Art-Net port-address")
    ap.add_argument("--address", type=int, default=1)
    ap.add_argument("--count", type=int, default=16, help="channels to drive")
    ap.add_argument("--pattern", choices=("ramp", "chase", "full", "blackout"), default="ramp")
    ap.add_argument("--rate", type=float, default=30.0)
    ap.add_argument("--priority", type=int, default=100)
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    cid, seq, start = uuid.uuid4().bytes, 0, time.time()
    if a.protocol == "sacn":
        dest = (packets.sacn_multicast_group(a.universe) if a.multicast else a.host, packets.SACN_PORT)
    else:
        dest = (a.host, packets.ARTNET_PORT)
    print(f"Sending {a.protocol} universe {a.universe} to {dest[0]}:{dest[1]} - Ctrl+C to stop")
    try:
        while True:
            data = frame(a.pattern, time.time() - start, a.address, a.count)
            seq = (seq + 1) % 256
            if a.protocol == "sacn":
                pkt = packets.build_sacn(a.universe, data, seq, a.priority, "LV send_test", cid)
            else:
                pkt = packets.build_artnet(a.universe, data, seq or 1)
            sock.sendto(pkt, dest)
            time.sleep(1.0 / a.rate)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
