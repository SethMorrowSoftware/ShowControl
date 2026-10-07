#!/usr/bin/env python3
"""Clean-room reference codecs for OSC 1.0, Art-Net 4 and MIDI 1.0.

Written from the specifications, sharing NO code with the C shims or the LCB
bindings, so they can act as an independent oracle:

  * tests/vectors_reference_test.py proves the shared wire-format vectors
    (tests/vectors/*.json) against these encoders/decoders;
  * tools/sim/artnet_node.py and tools/sim/osc_peer.py use them to play the
    OTHER end of the wire -- a virtual Art-Net node and an OSC peer -- for the
    engine-level interop tests and for hand testing without any hardware.

Pure standard library.
"""

import struct

# ===========================================================================
# OSC 1.0 reference (spec: opensoundcontrol.stanford.edu/spec-1_0.html)
# ===========================================================================

def _pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def osc_str(s):
    return _pad4(s.encode("utf-8") + b"\x00")       # NUL-terminated, then padded


def osc_encode_arg(t, v):
    if t == "i":
        return struct.pack(">i", int(v))
    if t == "f":
        return struct.pack(">f", float(v))
    if t == "d":
        return struct.pack(">d", float(v))
    if t == "h":
        return struct.pack(">q", int(v))
    if t == "t":
        return struct.pack(">Q", int(v))
    if t == "s":
        return osc_str(v)
    if t == "b":
        raw = bytes.fromhex(v)
        return struct.pack(">i", len(raw)) + _pad4(raw)
    if t in "TFNI":
        return b""
    raise ValueError("unknown OSC type " + t)


def osc_encode_message(address, args):
    tags = "," + "".join(a[0] for a in args)
    body = b"".join(osc_encode_arg(a[0], a[1] if len(a) > 1 else None) for a in args)
    return osc_str(address) + osc_str(tags) + body


def osc_encode_bundle(timetag, elements):
    out = b"#bundle\x00" + struct.pack(">Q", int(timetag))
    for e in elements:
        out += struct.pack(">i", len(e)) + e
    return out


class Malformed(Exception):
    pass


def _read_str(d, p):
    end = d.find(b"\x00", p)
    if end < 0:
        raise Malformed("unterminated string")
    s = d[p:end].decode("utf-8")
    p = end + 1
    p += (4 - p % 4) % 4
    if p > len(d):
        raise Malformed("string padding runs past the end")
    return s, p


def osc_decode(d, depth=0):
    """-> ("message", address, tags, [values]) or ("bundle", timetag, [elements])."""
    if len(d) < 4 or len(d) % 4:
        raise Malformed("length")
    if d[:8] == b"#bundle\x00":
        if depth >= 64:
            raise Malformed("nesting")
        if len(d) < 16:
            raise Malformed("bundle too short")
        tt = struct.unpack(">Q", d[8:16])[0]
        p, elems = 16, []
        while p + 4 <= len(d):
            n = struct.unpack(">i", d[p:p + 4])[0]
            p += 4
            if n < 0 or n > len(d) - p:
                raise Malformed("element size")
            elems.append(osc_decode(d[p:p + n], depth + 1))
            p += n
        return ("bundle", tt, elems)
    if d[0:1] != b"/":
        raise Malformed("address must start with /")
    addr, p = _read_str(d, 0)
    if p >= len(d) or d[p:p + 1] != b",":
        raise Malformed("missing type tag")
    tags, p = _read_str(d, p)
    tags = tags[1:]
    vals = []
    for t in tags:
        if t in "if":
            if p + 4 > len(d):
                raise Malformed("truncated")
            vals.append(struct.unpack(">i" if t == "i" else ">f", d[p:p + 4])[0])
            p += 4
        elif t in "hdt":
            if p + 8 > len(d):
                raise Malformed("truncated")
            vals.append(struct.unpack({"h": ">q", "d": ">d", "t": ">Q"}[t], d[p:p + 8])[0])
            p += 8
        elif t == "s":
            s, p = _read_str(d, p)
            vals.append(s)
        elif t == "b":
            if p + 4 > len(d):
                raise Malformed("truncated")
            n = struct.unpack(">i", d[p:p + 4])[0]
            p += 4
            if n < 0 or n > len(d) - p:
                raise Malformed("blob size")
            vals.append(d[p:p + n].hex())
            p += n + (4 - n % 4) % 4
            if p > len(d):
                raise Malformed("blob padding")
        elif t in "TFNI":
            vals.append({"T": True, "F": False, "N": None, "I": None}[t])
        else:
            raise Malformed("unknown type tag")
    return ("message", addr, tags, vals)


def osc_match(pattern, address):
    """OSC 1.0 address-pattern match (? * [] [!] [a-z] {,}); '*'/'?' never cross '/'."""
    def m(p, s):
        while p:
            c = p[0]
            if c == "?":
                if not s or s[0] == "/":
                    return False
                p, s = p[1:], s[1:]
            elif c == "*":
                p = p.lstrip("*")
                if not p:
                    return "/" not in s
                i = 0
                while True:
                    if m(p, s[i:]):
                        return True
                    if i >= len(s) or s[i] == "/":
                        return False
                    i += 1
            elif c == "[":
                end = p.find("]")
                body, p = (p[1:end], p[end + 1:]) if end >= 0 else (p[1:], "")
                if not s or s[0] == "/":
                    return False
                neg = body.startswith("!")
                if neg:
                    body = body[1:]
                hit, k = False, 0
                while k < len(body):
                    if k + 2 < len(body) and body[k + 1] == "-":
                        hit |= body[k] <= s[0] <= body[k + 2]
                        k += 3
                    else:
                        hit |= body[k] == s[0]
                        k += 1
                if hit == neg:
                    return False
                s = s[1:]
            elif c == "{":
                end = p.find("}")
                opts, rest = (p[1:end], p[end + 1:]) if end >= 0 else (p[1:], "")
                return any(s.startswith(o) and m(rest, s[len(o):]) for o in opts.split(","))
            else:
                if not s or s[0] != c:
                    return False
                p, s = p[1:], s[1:]
        return not s
    return m(pattern, address)


def _close(a, b, tol):
    return abs(float(a) - float(b)) <= tol


def osc_values_equal(t, got, want):
    if t == "f":
        return _close(got, want, 1e-5 * max(1.0, abs(float(want))))
    if t == "d":
        return _close(got, want, 1e-12 * max(1.0, abs(float(want))))
    if t in "iht":
        return int(got) == int(want)
    if t == "s":
        return got == want
    if t == "b":
        return got == want
    if t in "TFNI":
        return True
    return False


# ===========================================================================
# Art-Net 4 reference (Artistic Licence spec; OpCode LE, ProtVer/Length BE)
# ===========================================================================
ART_ID = b"Art-Net\x00"


def artnet_dmx(universe, channels, sequence=0, physical=0):
    data = bytes(channels[:512])
    if len(data) < 2:
        data += b"\x00" * (2 - len(data))
    if len(data) % 2:
        data += b"\x00"
    return (ART_ID + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
            + bytes([sequence, physical, universe & 0xFF, (universe >> 8) & 0x7F])
            + struct.pack(">H", len(data)) + data)


def artnet_poll():
    return ART_ID + struct.pack("<H", 0x2000) + struct.pack(">H", 14) + b"\x00\x00"


def artnet_pollreply(ip, short, long_, net, sub):
    b = bytearray(239)                                   # ArtPollReply is 239 bytes
    b[0:8] = ART_ID
    b[8:10] = struct.pack("<H", 0x2100)
    b[10:14] = bytes(int(x) for x in ip.split("."))
    b[14:16] = struct.pack("<H", 6454)                   # Port, little-endian
    b[18], b[19] = net, sub
    b[26:26 + len(short)] = short.encode("ascii")
    b[44:44 + len(long_)] = long_.encode("ascii")
    return bytes(b)


# ===========================================================================
# MIDI 1.0 reference (status nibble + 7-bit data; channels 1..16 on the API)
# ===========================================================================

def midi_decode(raw):
    if not raw:
        return {"kind": "empty"}
    st = raw[0]
    d1 = raw[1] if len(raw) > 1 else 0
    d2 = raw[2] if len(raw) > 2 else 0
    if st >= 0xF0:
        return {"kind": "sysex" if st == 0xF0 else "system", "channel": 0, "data1": d1, "data2": d2}
    kind = {0x90: "noteOn", 0x80: "noteOff", 0xB0: "controlChange", 0xC0: "programChange",
            0xE0: "pitchBend", 0xD0: "channelPressure", 0xA0: "polyAftertouch"}.get(st & 0xF0, "other")
    if kind == "noteOn" and d2 == 0:
        kind = "noteOff"
    out = {"kind": kind, "channel": (st & 0x0F) + 1, "data1": d1, "data2": d2}
    if kind == "pitchBend":
        out["value14"] = d1 + d2 * 128
    return out


def _c7(x):
    return max(0, min(127, x))


def _nib(ch):
    return ((ch - 1) % 16 + 16) % 16


def midi_encode(call, ch, a=0, b=0):
    n = _nib(ch)
    if call == "noteOn":
        return bytes([0x90 + n, _c7(a), _c7(b)])
    if call == "noteOff":
        return bytes([0x80 + n, _c7(a), _c7(b)])
    if call == "controlChange":
        return bytes([0xB0 + n, _c7(a), _c7(b)])
    if call == "programChange":
        return bytes([0xC0 + n, _c7(a)])
    if call == "channelPressure":
        return bytes([0xD0 + n, _c7(a)])
    if call == "pitchBend":
        v = max(0, min(16383, a))
        return bytes([0xE0 + n, v % 128, (v // 128) % 128])
    raise ValueError(call)


