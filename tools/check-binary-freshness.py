#!/usr/bin/env python3
"""check-binary-freshness.py - prove every COMMITTED native library is still the
one the current source would produce, and that it will load where it claims to.

The committed src/<ext>/code/<platform-id>/ binaries are what users install, yet
nothing used to check them: the headless suites test FRESH builds. So a shim
change could land while the shipped binary stayed old -- exactly what happened
with MIDI ABI 2 -- and surface only as a bind failure on a user's engine.
MANIFEST.sha256 does not close that gap: it proves a blob is unchanged since it
was committed, not that it matches the source. This gate does (ported from the
xtalk-suite's tool of the same name, reduced to ShowControl's two shims):

  1. FORMAT + ARCHITECTURE match the directory (a thin dylib under universal-mac,
     an x86 DLL under x86_64-win32, ... are refused).
  2. THE BIND ORACLE: every `binds to "c:<ext>>symbol"` in the .lcb is EXPORTED --
     the engine resolves binds by exact name, so a missing one is a load failure.
  3. THE SOURCE ORACLE: every OSC_API / MIDI_API function in the shim header is
     exported (the next binding's binds).
  4. THE EXPORT CLOSURE: nothing ELSE is exported (no RtMidi, no C++ runtime, no
     tinyosc leaking into the engine process), and never the mock's midimock_*.
  5. THE ABI PIN: <ext>_abi_version() -- DECODED from its machine code, every
     slice of every platform -- equals the header's *_ABI_VERSION, which equals
     the .lcb checkABI() literal. Where this host can load the library it is
     also CALLED (in a subprocess) and must agree with the decode.
  6. PORTABILITY: ELF needs only allowed system libraries and no GLIBC symbol
     newer than the floor (2.28 for x86_64/arm64 -- the manylinux_2_28 build);
     Windows DLLs import only system DLLs (the static CRT -- no VC++ runtime).
  7. MANIFEST.sha256 matches the tree exactly.

Every platform slot must be populated unless named in --allow-missing.

    python3 tools/check-binary-freshness.py                      # the committed tree
    python3 tools/check-binary-freshness.py --lib X --ext osc --platform-id x86_64-linux
                                                                 # one file (the installer uses this)
Exit 1 on any problem. Undecodable machine code is a NOTE, never a guess.
"""

import argparse
import importlib.util
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import binfmt  # noqa: E402

_spec = importlib.util.spec_from_file_location("pkgext", os.path.join(TOOLS, "package-extension.py"))
pkg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pkg)

HEADERS = {"osc": "src/osc/osc_shim.h", "midi": "src/midi/midi_shim.h"}
ABI_DEFINE = {"osc": "OSC_ABI_VERSION", "midi": "MIDI_ABI_VERSION"}
API_MACRO = {"osc": "OSC_API", "midi": "MIDI_API"}

GLIBC_FLOOR = {"x86_64-linux": (2, 28), "arm64-linux": (2, 28)}     # x86-linux: reported, not enforced
ELF_ALLOWED = {"libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2", "librt.so.1",
               "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1", "ld-linux.so.2",
               "libasound.so.2"}                                    # ALSA: the system MIDI service itself
PE_ALLOWED = {"kernel32.dll", "winmm.dll", "ws2_32.dll"}
# Names a toolchain may export that are not ours and are harmless.
EXPORT_TOLERANCE = {"_init", "_fini", "_mh_dylib_header"}


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def bound_symbols(ext):
    return set(re.findall(r'binds\s+to\s+"c:%s>(\w+)' % ext, read(pkg.NATIVE_EXTS[ext])))


def api_symbols(ext):
    text = re.sub(r"/\*.*?\*/", "", read(HEADERS[ext]), flags=re.S)
    return set(re.findall(r"\b%s\s+[\w\s\*]*?\b(%s_\w+)\s*\(" % (API_MACRO[ext], ext), text))


def header_abi(ext):
    m = re.search(r"#define\s+%s\s+(\d+)" % ABI_DEFINE[ext], read(HEADERS[ext]))
    return int(m.group(1)) if m else None


def lcb_abi(ext):
    m = re.search(r"if tV is not (\d+) then", read(pkg.NATIVE_EXTS[ext]))
    return int(m.group(1)) if m else None


def host_platform_id():
    m = _platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64",
            "i386": "x86", "i686": "x86", "x86": "x86"}.get(m, m)
    if sys.maxsize <= 2 ** 32 and arch == "x86_64":
        arch = "x86"                                   # a 32-bit Python on a 64-bit OS
    if sys.platform.startswith("win"):
        return arch + "-win32"
    if sys.platform == "darwin":
        return "universal-mac"
    return arch + "-linux"


def host_call_abi(path, ext):
    """Load the library in a SUBPROCESS and call <ext>_abi_version(). The file is
    copied to a short temporary path first: Windows' loader is MAX_PATH-bound, and
    a deep checkout would otherwise fail to load a perfectly good DLL."""
    code = ("import ctypes,sys; l=ctypes.CDLL(sys.argv[1]); f=getattr(l,sys.argv[2]); "
            "f.restype=ctypes.c_int; print(f())")
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(path))
        shutil.copy2(path, copy)
        r = subprocess.run([sys.executable, "-c", code, copy, ext + "_abi_version"],
                           capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None, (r.stderr.strip().splitlines() or ["load failed"])[-1]
    return int(r.stdout.strip()), "called"


def check_library(path, ext, platform_id):
    """(problems, notes) for one library file claiming to be <ext> for <platform_id>."""
    problems, notes = [], []
    try:
        info = binfmt.read_library(path)
    except (binfmt.BinFmtError, OSError, Exception) as e:  # noqa: BLE001 -- unreadable is a finding
        return ["unreadable: %s" % e], notes
    problems += binfmt.check_platform(info, platform_id)
    want_abi = header_abi(ext)
    binds, api = bound_symbols(ext), api_symbols(ext)
    decoded = []
    for sl in info.slices:
        tag = "[%s] " % sl.arch if len(info.slices) > 1 else ""
        exported = set(sl.exports)
        missing_bind = sorted(binds - exported)
        if missing_bind:
            problems.append(tag + "bound by %s.lcb but NOT exported (a load failure): %s"
                            % (ext, ", ".join(missing_bind)))
        missing_api = sorted(api - exported - set(missing_bind))
        if missing_api:
            problems.append(tag + "declared %s in the header but not exported: %s"
                            % (API_MACRO[ext], ", ".join(missing_api)))
        mock = sorted(e for e in exported if e.startswith("midimock_"))
        if mock:
            problems.append(tag + "exports the MOCK's controls (%s...) -- this is the test double" % mock[0])
        extra = sorted(exported - api - EXPORT_TOLERANCE - set(mock))
        if extra:
            problems.append(tag + "exports %d symbol(s) beyond the %s_* ABI (e.g. %s) -- the export "
                            "closure is open" % (len(extra), ext, ", ".join(extra[:4])))
        val, how = binfmt.decode_abi(sl, ext + "_abi_version")
        if val is None:
            notes.append(tag + "ABI not decodable (%s)" % how)
        else:
            decoded.append(val)
            if want_abi is not None and val != want_abi:
                problems.append(tag + "%s_abi_version() returns %d but %s is %d -- the binary is STALE"
                                % (ext, val, ABI_DEFINE[ext], want_abi))
        if info.format == "elf":
            bad = sorted(n for n in sl.needed if n not in ELF_ALLOWED)
            if bad:
                problems.append(tag + "needs non-system shared libraries: %s" % ", ".join(bad))
            floor = GLIBC_FLOOR.get(platform_id)
            if sl.glibc_max:
                v = ".".join(map(str, sl.glibc_max))
                if floor and sl.glibc_max > floor:
                    problems.append(tag + "needs GLIBC_%s -- above the %s floor; it will not load on "
                                    "older distributions" % (v, ".".join(map(str, floor))))
                else:
                    notes.append(tag + "glibc floor %s" % v)
        if info.format == "pe":
            bad = sorted(n for n in sl.needed if n.lower() not in PE_ALLOWED)
            if bad:
                problems.append(tag + "imports %s -- the VC++ runtime must then be installed; build with "
                                "the static CRT (SHOWCONTROL_STATIC_RUNTIME)" % ", ".join(bad))
    if len(set(decoded)) > 1:
        problems.append("slices disagree on the ABI (%s) -- assembled from different builds" % decoded)
    if platform_id == host_platform_id() or (platform_id == "universal-mac" and sys.platform == "darwin"):
        val, how = host_call_abi(path, ext)
        if val is None:
            problems.append("this host should load it but cannot: %s" % how)
        else:
            notes.append("loaded and called on this host: ABI %d" % val)
            if decoded and val != decoded[0]:
                problems.append("decoded ABI %d but the call returned %d" % (decoded[0], val))
    return problems, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lib"); ap.add_argument("--ext", choices=sorted(pkg.NATIVE_EXTS))
    ap.add_argument("--platform-id", choices=sorted(pkg.PLATFORMS))
    ap.add_argument("--allow-missing", default="", help="platform ids that may be empty, comma-separated")
    a = ap.parse_args()

    if a.lib:
        problems, notes = check_library(a.lib, a.ext, a.platform_id)
        for n in notes:
            print("  note: " + n)
        for p in problems:
            print("  FAIL: " + p)
        return 1 if problems else 0

    allow = set(x for x in a.allow_missing.replace(" ", ",").split(",") if x)
    failures = 0
    for ext in pkg.NATIVE_EXTS:
        h, l = header_abi(ext), lcb_abi(ext)
        print("%s: header %s = %s, %s.lcb checkABI() needs %s" % (ext, ABI_DEFINE[ext], h, ext, l))
        if h is None or h != l:
            print("  FAIL: the header and the binding disagree on the ABI")
            failures += 1
        for p in pkg.verify_manifest(ext):
            print("  FAIL: " + p)
            failures += 1
        for pid in pkg.PLATFORMS:
            path = pkg.dest_path(ext, pid)
            if not os.path.isfile(path):
                if pid in allow:
                    print("  skip  %s (allowed missing)" % pkg.rel(path))
                else:
                    print("  FAIL  %s is MISSING -- %s will not load on %s" % (pkg.rel(path), ext, pid))
                    failures += 1
                continue
            problems, notes = check_library(path, ext, pid)
            print("  %s  %s%s" % ("FAIL" if problems else "ok  ", pkg.rel(path),
                                  ("  (" + "; ".join(notes) + ")") if notes else ""))
            for p in problems:
                print("        - " + p)
            failures += len(problems)
    print("\n%s" % ("binary freshness: %d problem(s)" % failures if failures else
                    "binary freshness: every committed library matches the source and its directory"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
