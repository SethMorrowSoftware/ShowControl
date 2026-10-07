#!/usr/bin/env python3
"""binfmt.py - read just enough of ELF / PE / Mach-O to VERIFY a shipped library.

Pure standard library, no objdump/readelf/lipo needed, so the same checks run on
Linux, Windows and macOS hosts alike. Used by tools/install-release-binaries.py
(object format + architecture against the platform directory) and
tools/check-binary-freshness.py (export tables, the ABI constant decoded from
machine code, dynamic dependencies, the glibc floor).

Ported in spirit from the xtalk-suite's check-binary-freshness / installer pair,
reduced to what ShowControl's two C shims need: every *_abi_version() here is a
LEAF `return N;`, which every compiler in the build matrix emits as one of a few
exact instruction shapes (decode_abi below). Anything else is reported as
"undecodable" -- never guessed.

    info = read_library(path)
    info.format         'elf' | 'pe' | 'macho'
    info.slices         [Slice(arch='x86_64', bits=64, exports={...}, ...), ...]
                        (one per architecture; a fat Mach-O has several)
"""

import re
import struct


class BinFmtError(Exception):
    pass


class Slice:
    """One architecture's view of a library."""

    def __init__(self, arch, bits):
        self.arch = arch            # 'x86_64' | 'x86' | 'arm64' | other
        self.bits = bits
        self.exports = {}           # exported symbol name -> file offset of its code (or None)
        self.needed = []            # dynamic library dependencies (ELF DT_NEEDED / PE imports)
        self.glibc_max = None       # highest GLIBC_x.y referenced (ELF only)
        self._data = b""

    def code_at(self, name, n=16):
        off = self.exports.get(name)
        if off is None or off < 0 or off >= len(self._data):
            return None
        return self._data[off:off + n]


class LibInfo:
    def __init__(self, fmt, slices):
        self.format = fmt
        self.slices = slices

    @property
    def archs(self):
        return sorted(s.arch for s in self.slices)


# ---------------------------------------------------------------------------
# ELF
# ---------------------------------------------------------------------------
ELF_MACHINES = {3: "x86", 62: "x86_64", 183: "arm64", 40: "arm"}


def _read_elf(data):
    if data[:4] != b"\x7fELF":
        raise BinFmtError("not ELF")
    cls, enc = data[4], data[5]
    if enc != 1:
        raise BinFmtError("big-endian ELF is not a supported target")
    is64 = cls == 2
    E = "<"
    machine = struct.unpack_from(E + "H", data, 0x12)[0]
    if is64:
        shoff = struct.unpack_from(E + "Q", data, 0x28)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(E + "HHH", data, 0x3A)
    else:
        shoff = struct.unpack_from(E + "I", data, 0x20)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(E + "HHH", data, 0x2E)
    sections = []
    for i in range(shnum):
        o = shoff + i * shentsize
        if is64:
            name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from(E + "IIQQQQIIQQ", data, o)
        else:
            name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from(E + "IIIIIIIIII", data, o)
        sections.append(dict(name=name, type=typ, addr=addr, off=off, size=size, link=link, entsize=entsize))

    def cstr(tab_off, idx):
        end = data.index(b"\0", tab_off + idx)
        return data[tab_off + idx:end].decode("ascii", "replace")

    def addr_to_off(addr):
        for s in sections:
            if s["type"] != 8 and s["addr"] and s["addr"] <= addr < s["addr"] + s["size"]:   # not NOBITS
                return s["off"] + (addr - s["addr"])
        return None

    sl = Slice(ELF_MACHINES.get(machine, "machine-%d" % machine), 64 if is64 else 32)
    sl._data = data
    for s in sections:
        if s["type"] == 11:                                   # SHT_DYNSYM
            strtab = sections[s["link"]]["off"]
            ent = 24 if is64 else 16
            for k in range(1, s["size"] // ent):
                o = s["off"] + k * ent
                if is64:
                    st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(E + "IBBHQQ", data, o)
                else:
                    st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from(E + "IIIBBH", data, o)
                bind, vis = st_info >> 4, st_other & 3
                if st_shndx != 0 and bind in (1, 2) and vis in (0, 3):     # defined, GLOBAL/WEAK, DEFAULT/PROTECTED
                    sl.exports[cstr(strtab, st_name)] = addr_to_off(st_value)
        elif s["type"] == 6:                                  # SHT_DYNAMIC
            strtab = sections[s["link"]]["off"]
            ent = 16 if is64 else 8
            for k in range(s["size"] // ent):
                tag, val = struct.unpack_from(E + ("qQ" if is64 else "iI"), data, s["off"] + k * ent)
                if tag == 0:
                    break
                if tag == 1:                                  # DT_NEEDED
                    sl.needed.append(cstr(strtab, val))
            dynstr = data[strtab:strtab + sections[s["link"]]["size"]]
            vers = re.findall(rb"GLIBC_(\d+(?:\.\d+)+)", dynstr)
            if vers:
                sl.glibc_max = max((tuple(int(x) for x in v.split(b".")) for v in vers))
    return LibInfo("elf", [sl])


# ---------------------------------------------------------------------------
# PE (Windows DLL)
# ---------------------------------------------------------------------------
PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}


def _read_pe(data):
    if data[:2] != b"MZ":
        raise BinFmtError("not PE")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise BinFmtError("bad PE signature")
    machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    is64 = magic == 0x20B
    dirs = opt + (112 if is64 else 96)
    exp_rva, exp_size = struct.unpack_from("<II", data, dirs)
    imp_rva, imp_size = struct.unpack_from("<II", data, dirs + 8)
    secs = []
    for i in range(nsec):
        o = opt + opt_size + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
        secs.append((vaddr, max(vsize, rawsize), rawptr))

    def rva_off(rva):
        for vaddr, size, rawptr in secs:
            if vaddr <= rva < vaddr + size:
                return rawptr + (rva - vaddr)
        return None

    def cstr_rva(rva):
        o = rva_off(rva)
        end = data.index(b"\0", o)
        return data[o:end].decode("ascii", "replace")

    sl = Slice(PE_MACHINES.get(machine, "machine-0x%x" % machine), 64 if is64 else 32)
    sl._data = data
    if exp_rva:
        o = rva_off(exp_rva)
        (_c, _t, _maj, _min, _name, _base, nfunc, nnames,
         afuncs, anames, aords) = struct.unpack_from("<IIHHIIIIIII", data, o)
        for i in range(nnames):
            name_rva = struct.unpack_from("<I", data, rva_off(anames) + 4 * i)[0]
            ordi = struct.unpack_from("<H", data, rva_off(aords) + 2 * i)[0]
            frva = struct.unpack_from("<I", data, rva_off(afuncs) + 4 * ordi)[0]
            forwarder = exp_rva <= frva < exp_rva + exp_size
            sl.exports[cstr_rva(name_rva)] = None if forwarder else rva_off(frva)
    if imp_rva:
        o = rva_off(imp_rva)
        while o is not None:
            fields = struct.unpack_from("<IIIII", data, o)
            if not any(fields):
                break
            sl.needed.append(cstr_rva(fields[3]))
            o += 20
    return LibInfo("pe", [sl])


# ---------------------------------------------------------------------------
# Mach-O (thin and fat)
# ---------------------------------------------------------------------------
MACHO_CPU = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "x86", 12: "arm"}


def _read_macho_thin(data, base):
    magic = struct.unpack_from("<I", data, base)[0]
    if magic not in (0xFEEDFACF, 0xFEEDFACE):
        raise BinFmtError("not a little-endian Mach-O slice")
    is64 = magic == 0xFEEDFACF
    cputype, _sub, _ftype, ncmds, _sz, _flags = struct.unpack_from("<iiIIII", data, base + 4)
    p = base + (32 if is64 else 28)
    segs, symtab = [], None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmd == 0x19:                                           # LC_SEGMENT_64
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, p + 24)
            segs.append((vmaddr, vmsize, fileoff))
        elif cmd == 0x1:                                          # LC_SEGMENT
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<IIII", data, p + 24)
            segs.append((vmaddr, vmsize, fileoff))
        elif cmd == 0x2:                                          # LC_SYMTAB
            symtab = struct.unpack_from("<IIII", data, p + 8)
        p += cmdsize
    sl = Slice(MACHO_CPU.get(cputype & 0xFFFFFFFF, "cpu-0x%x" % (cputype & 0xFFFFFFFF)), 64 if is64 else 32)
    sl._data = data
    if symtab:
        symoff, nsyms, stroff, _strsize = symtab
        ent = 16 if is64 else 12
        for k in range(nsyms):
            o = base + symoff + k * ent
            if is64:
                strx, ntype, nsect, _desc, value = struct.unpack_from("<IBBHQ", data, o)
            else:
                strx, ntype, nsect, _desc, value = struct.unpack_from("<IBBhI", data, o)
            if ntype & 0xE0:                                      # stab
                continue
            if (ntype & 0x01) and (ntype & 0x0E) == 0x0E and not (ntype & 0x10):   # N_EXT, N_SECT, not N_PEXT
                so = base + stroff + strx
                name = data[so:data.index(b"\0", so)].decode("ascii", "replace")
                if name.startswith("_"):
                    name = name[1:]
                off = None
                for vmaddr, vmsize, fileoff in segs:
                    if vmaddr <= value < vmaddr + vmsize:
                        off = base + fileoff + (value - vmaddr)
                        break
                sl.exports[name] = off
    return sl


def _read_macho(data):
    magic_be = struct.unpack_from(">I", data, 0)[0]
    if magic_be == 0xCAFEBABE:                                    # fat (big-endian header)
        n = struct.unpack_from(">I", data, 4)[0]
        slices = []
        for i in range(n):
            cputype, _sub, off, size, _align = struct.unpack_from(">iiIII", data, 8 + 20 * i)
            sl = _read_macho_thin(data, off)
            slices.append(sl)
        return LibInfo("macho", slices)
    return LibInfo("macho", [_read_macho_thin(data, 0)])


def read_library(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"\x7fELF":
        return _read_elf(data)
    if data[:2] == b"MZ":
        return _read_pe(data)
    head = struct.unpack_from(">I", data, 0)[0] if len(data) >= 4 else 0
    if head in (0xCAFEBABE, 0xCFFAEDFE, 0xCEFAEDFE):
        return _read_macho(data)
    raise BinFmtError("unknown object format (first bytes %s)" % data[:8].hex())


# ---------------------------------------------------------------------------
# The ABI constant, read from machine code
# ---------------------------------------------------------------------------
def decode_abi(sl, symbol):
    """Decode `int f(void) { return N; }` from its code bytes. Returns
    (N, shape) or (None, reason). Only exact, known shapes are accepted."""
    code = sl.code_at(symbol, 24)
    if code is None:
        return None, "symbol not exported or not mapped to file bytes"
    if sl.arch in ("x86_64", "x86"):
        c = code
        if c[:4] in (b"\xf3\x0f\x1e\xfa", b"\xf3\x0f\x1e\xfb"):      # endbr64 / endbr32
            c = c[4:]
        if c[0] == 0xB8 and c[5] == 0xC3:                             # mov eax, imm32 ; ret
            return struct.unpack_from("<i", c, 1)[0], "mov eax, imm32; ret"
        if c[:4] == b"\x55\x48\x89\xe5" and c[4] == 0xB8 and c[9:11] == b"\x5d\xc3":   # -O0 x86_64 frame
            return struct.unpack_from("<i", c, 5)[0], "push rbp; mov rbp,rsp; mov eax, imm32; pop rbp; ret"
        if c[:3] == b"\x55\x89\xe5" and c[3] == 0xB8 and c[8:10] == b"\x5d\xc3":       # -O0 x86 frame
            return struct.unpack_from("<i", c, 4)[0], "push ebp; mov ebp,esp; mov eax, imm32; pop ebp; ret"
        return None, "unrecognised x86 shape %s" % c[:12].hex()
    if sl.arch == "arm64":
        insn, nxt = struct.unpack_from("<II", code, 0)
        if (insn & 0xFFE0001F) == 0x52800000 and nxt == 0xD65F03C0:   # MOVZ w0, #imm16 ; RET
            return (insn >> 5) & 0xFFFF, "movz w0, #imm16; ret"
        return None, "unrecognised arm64 shape %s" % code[:8].hex()
    return None, "no decoder for %s" % sl.arch


# ---------------------------------------------------------------------------
# What each committed platform directory promises
# ---------------------------------------------------------------------------
# platform-id -> (object format, the architecture(s) EVERY library there must carry)
PLATFORM_EXPECT = {
    "x86_64-linux":  ("elf",   ["x86_64"]),
    "arm64-linux":   ("elf",   ["arm64"]),
    "x86-linux":     ("elf",   ["x86"]),
    "x86_64-win32":  ("pe",    ["x86_64"]),
    "x86-win32":     ("pe",    ["x86"]),
    "universal-mac": ("macho", ["arm64", "x86_64"]),   # BOTH slices: a thin dylib is refused
}


def check_platform(info, platform_id):
    """Problems (empty == the library is what its directory claims)."""
    fmt, archs = PLATFORM_EXPECT[platform_id]
    if info.format != fmt:
        return ["object format is %s, but %s holds %s" % (info.format, platform_id, fmt)]
    if sorted(archs) != info.archs:
        return ["architecture(s) %s, but %s promises %s" % (",".join(info.archs) or "none",
                                                             platform_id, ",".join(sorted(archs)))]
    return []
