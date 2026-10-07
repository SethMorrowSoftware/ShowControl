#!/usr/bin/env python3
"""A virtual Art-Net node: test Art-Net output with NO lighting hardware.

It answers ArtPoll discovery with an ArtPollReply and records every ArtDmx frame
it receives, decoding both with the clean-room codec (tools/sim/refcodec.py) --
an implementation that shares no code with ShowControl, so a frame it accepts is
a frame a real node would accept (OpCode little-endian, ProtVer/Length
big-endian, SubUni before Net).

Hand testing -- run it, then point your OXT stack at it:

    python3 tools/sim/artnet_node.py                 # listens on 0.0.0.0:6454
    python3 tools/sim/artnet_node.py --port 16454    # a non-privileged test port
    python3 tools/sim/artnet_node.py --echo 1        # also echo each frame back
                                                     #   on universe + 1

    -- in OXT:  write artnetBuildDmx(0, tChannels) to socket "127.0.0.1:6454"

Each frame prints as a one-line level meter of the first channels, so a fader
sweep is visible at a glance. Ctrl+C to stop.

As a library (tools/run-lcs-tests.py uses it for the engine interop suite):

    node = ArtNetNode(port=0).start_background()   # port 0 = pick a free port
    ... node.frames, node.polls, node.port ...
    node.stop()
"""

import argparse
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refcodec as R  # noqa: E402

OP_POLL, OP_POLLREPLY, OP_DMX = 0x2000, 0x2100, 0x5000


class ArtNetNode:
    def __init__(self, host="127.0.0.1", port=6454, short_name="SimNode",
                 long_name="ShowControl virtual Art-Net node", net=0, subuni=0,
                 echo_offset=None, verbose=False):
        self.host, self.short_name, self.long_name = host, short_name, long_name
        self.net, self.subuni, self.echo_offset, self.verbose = net, subuni, echo_offset, verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.frames = []        # (universe, sequence, physical, channels: bytes, sender)
        self.polls = []         # sender addresses
        self.rejected = []      # (reason, raw bytes)
        self._stop = threading.Event()
        self._thread = None

    # -- protocol --------------------------------------------------------------
    def handle(self, data, addr):
        if len(data) < 10 or data[:8] != R.ART_ID:
            self.rejected.append(("not Art-Net", data))
            return
        op = struct.unpack("<H", data[8:10])[0]
        if op == OP_POLL:
            self.polls.append(addr)
            reply = R.artnet_pollreply(self.host if self.host != "0.0.0.0" else "127.0.0.1",
                                       self.short_name, self.long_name, self.net, self.subuni)
            self.sock.sendto(reply, addr)
            self.log("ArtPoll from %s:%d -> ArtPollReply '%s'" % (addr[0], addr[1], self.short_name))
        elif op == OP_DMX:
            if len(data) < 18:
                self.rejected.append(("ArtDmx too short", data))
                return
            ver = struct.unpack(">H", data[10:12])[0]
            seq, phys, sub, net = data[12], data[13], data[14], data[15]
            n = struct.unpack(">H", data[16:18])[0]
            chans = data[18:18 + n]
            if ver < 14 or n < 2 or n > 512 or n % 2 or len(chans) != n:
                self.rejected.append(("bad ArtDmx header (ver %d, length %d, have %d)" % (ver, n, len(chans)), data))
                return
            universe = net * 256 + sub
            self.frames.append((universe, seq, phys, bytes(chans), addr))
            self.log("ArtDmx u%-5d seq %3d len %3d  %s" % (universe, seq, n, meter(chans)))
            if self.echo_offset is not None:
                self.sock.sendto(R.artnet_dmx((universe + self.echo_offset) % 32768, chans), addr)
        else:
            self.log("ignored OpCode 0x%04x from %s:%d" % (op, addr[0], addr[1]))

    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    # -- lifecycle -------------------------------------------------------------
    def serve_forever(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self.handle(data, addr)

    def start_background(self):
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.sock.close()


def meter(chans, width=16):
    """A one-line level meter of the first `width` channels."""
    bars = " .:-=+*#%@"
    return "".join(bars[min(9, c * 10 // 256)] for c in chans[:width]) + ("..." if len(chans) > width else "")


def main():
    ap = argparse.ArgumentParser(description="Virtual Art-Net node (no hardware needed).")
    ap.add_argument("--bind", default="0.0.0.0", help="address to listen on (default all)")
    ap.add_argument("--port", type=int, default=6454, help="UDP port (Art-Net is 6454)")
    ap.add_argument("--name", default="SimNode", help="ShortName in ArtPollReply")
    ap.add_argument("--net", type=int, default=0)
    ap.add_argument("--subuni", type=int, default=0)
    ap.add_argument("--echo", type=int, metavar="OFFSET",
                    help="echo every ArtDmx back to the sender on universe + OFFSET")
    a = ap.parse_args()
    node = ArtNetNode(a.bind, a.port, a.name, net=a.net, subuni=a.subuni,
                      echo_offset=a.echo, verbose=True)
    print("virtual Art-Net node '%s' listening on %s:%d (Ctrl+C to stop)" % (a.name, a.bind, node.port), flush=True)
    try:
        node.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n%d frame(s), %d poll(s), %d rejected" % (len(node.frames), len(node.polls), len(node.rejected)))
        for why, raw in node.rejected[:10]:
            print("  rejected: %s  %s" % (why, raw[:24].hex()))
        node.stop()


if __name__ == "__main__":
    main()
