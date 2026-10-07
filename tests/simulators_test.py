#!/usr/bin/env python3
"""Self-test of the virtual peers in tools/sim/ (fixture before gate).

The engine interop suite trusts tools/sim/artnet_node.py and tools/sim/osc_peer.py
to behave like real gear. This proves they do, over real UDP on 127.0.0.1, using
only the vectors: a node must answer ArtPoll with the vector-exact ArtPollReply,
accept every vector ArtDmx and reject every vector reject; the OSC peer must ack
every vector message with exactly the canonical arguments and NAK every
malformed one. If a peer were wrong, the interop suite could pass while lying.

Run:  python3 tests/simulators_test.py
"""

import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "sim"))
import refcodec as R            # noqa: E402
from artnet_node import ArtNetNode   # noqa: E402
from osc_peer import OscPeer, canonical   # noqa: E402

VEC = ROOT / "tests" / "vectors"
FAILS, PASSES = [], [0]


def check(name, ok, detail=""):
    if ok:
        PASSES[0] += 1
    else:
        FAILS.append(name)
        print("  [FAIL] %s %s" % (name, detail))


def client():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    return s


def wait_until(pred, secs=2.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_artnet_node():
    v = json.loads((VEC / "artnet.json").read_text(encoding="utf-8"))
    node = ArtNetNode(port=0, short_name="Node", long_name="ShowControl test node").start_background()
    c = client()
    try:
        c.sendto(R.artnet_poll(), ("127.0.0.1", node.port))
        reply, _ = c.recvfrom(65535)
        want = R.artnet_pollreply("127.0.0.1", "Node", "ShowControl test node", 0, 0)
        check("node answers ArtPoll with an ArtPollReply", reply == want, reply[:16].hex())
        for d in v["dmx"]:
            c.sendto(bytes.fromhex(d["hex"]), ("127.0.0.1", node.port))
        check("node records every vector ArtDmx", wait_until(lambda: len(node.frames) == len(v["dmx"])),
              "%d of %d" % (len(node.frames), len(v["dmx"])))
        for d, f in zip(v["dmx"], node.frames):
            check("node decodes universe: " + d["name"], f[0] == d["universe"])
            check("node decodes channels: " + d["name"], f[3].hex() == d["hex"][36:])
        before = len(node.frames)
        for r in v["reject"]:
            if r["hex"]:
                c.sendto(bytes.fromhex(r["hex"]), ("127.0.0.1", node.port))
        time.sleep(0.3)
        check("node accepts none of the reject vectors", len(node.frames) == before)
    finally:
        c.close()
        node.stop()

    echo = ArtNetNode(port=0, echo_offset=1).start_background()
    c = client()
    try:
        c.sendto(R.artnet_dmx(7, b"\x10\x20"), ("127.0.0.1", echo.port))
        back, _ = c.recvfrom(65535)
        check("echo mode returns the frame on universe + 1", back == R.artnet_dmx(8, b"\x10\x20"))
    finally:
        c.close()
        echo.stop()


def test_osc_peer():
    v = json.loads((VEC / "osc.json").read_text(encoding="utf-8"))
    peer = OscPeer(port=0, ack=True).start_background()
    c = client()
    try:
        for m in v["messages"]:
            c.sendto(bytes.fromhex(m["hex"]), ("127.0.0.1", peer.port))
            ack = R.osc_decode(c.recvfrom(65535)[0])
            tags = "".join(a[0] for a in m["args"])
            ok = ack[1] == "/showcontrol/ack" and ack[3][0] == m["address"] and ack[3][1] == tags
            vals = R.osc_decode(bytes.fromhex(m["hex"]))[3]
            want = [canonical(t, x) for t, x in zip(tags, vals)]
            check("peer acks with address, tags and canonical args: " + m["name"], ok and ack[3][2:] == want,
                  repr(ack[3][:4]))
        for m in v["malformed"]:
            if not m["hex"]:
                continue                     # an empty datagram is not delivered by every stack
            c.sendto(bytes.fromhex(m["hex"]), ("127.0.0.1", peer.port))
            nak = R.osc_decode(c.recvfrom(65535)[0])
            check("peer NAKs malformed: " + m["name"], nak[1] == "/showcontrol/nak")
        for b in v["bundles"]:
            c.sendto(bytes.fromhex(b["hex"]), ("127.0.0.1", peer.port))
            ack = R.osc_decode(c.recvfrom(65535)[0])
            check("peer acks bundle: " + b["name"],
                  ack[1] == "/showcontrol/ack-bundle" and ack[3] == [b["timetag"], len(b["elements"])])
    finally:
        c.close()
        peer.stop()


def main():
    test_artnet_node()
    test_osc_peer()
    print("simulators: %d passed, %d failed" % (PASSES[0], len(FAILS)))
    if PASSES[0] < 60:
        print("FAIL: only %d checks ran (floor 60)" % PASSES[0])
        return 1
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
