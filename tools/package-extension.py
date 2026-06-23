#!/usr/bin/env python3
"""Refresh code/<platform-id>/ beside each native extension with its native libs.

This is the intended, cross-platform way to ship native code with a LiveCode
Builder extension: the per-platform shared library lives INSIDE the extension, in
a code/<platform-id>/ folder next to the .lcb. When the extension is built and
installed, the IDE maps each library into `the revLibraryMapping` and the engine
resolves the c:<token>> bindings from there -- so the consumer installs ONE
extension and the library comes with it. No loose .so/.dll/.dylib, no /usr/lib,
no sudo, no LD_LIBRARY_PATH, no "next to the stack". Same on Linux, macOS and
Windows, on LiveCode 9.6.3 and OpenXTalk.

ShowControl ships THREE extensions, of which only TWO carry native code:

  * osc    -- src/osc/osc.lcb     + native lib token "osc"  at src/osc/code/...
  * midi   -- src/midi/midi.lcb   + native lib token "midi" at src/midi/code/...
  * artnet -- src/artnet/artnet.lcb  -- PURE LCB. No C shim, no native binary, no
              code/ folder. Art-Net is plain byte-packing over a UDP socket, so
              it is exempt from the native build matrix entirely. This script
              never touches artnet (and --check says so explicitly -- the absence
              of an artnet/code/ tree is correct, not an error).

Those per-platform libraries are COMMITTED under src/<ext>/code/ (built and
tested by CI -- see .github/workflows/build.yml -- and attached to each GitHub
Release), so a fresh clone of this repo is already a ready-to-build extension:
open src/<ext>/<ext>.lcb in OXT's Extension Builder and Package -> .lce.

Use this script only to REFRESH a tree when you have newer binaries. Point each
--<ext>-<platform> flag at the matching freshly-built or Release-downloaded
library and it copies them into src/<ext>/code/<platform-id>/ under the bare
binding name. A platform with no flag is left untouched.

    python3 tools/package-extension.py --check                     # list both native trees
    python3 tools/package-extension.py --osc-linux64 build/libosc.so
    python3 tools/package-extension.py --midi-win64 ~/dl/midi-windows-x64.dll \
                                       --midi-mac   ~/dl/libmidi-macos-universal.dylib

PLATFORM-ID FORMAT: <architecture>-<platform>, verified against the LiveCode/OXT
engine + IDE source (the IDE's code-folder matcher filters on `the processor`
first, then the platform). The architecture comes FIRST -- e.g. x86_64-linux, NOT
linux-x86_64. Windows uses the -win32 suffix for BOTH bitnesses. The bundled file
is the bare token (osc.so / midi.dll / ...), with no "lib" prefix and no leading
arch, because it must equal the c:<token>> binding name (e.g. "c:osc>...").
"""

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# platform-id ("<arch>-<platform>", arch FIRST) -> (flag-suffix, file-suffix).
# The committed file is named "<token><file-suffix>" (e.g. osc.so), where <token>
# is the extension/binding name (osc, midi). The suffix is chosen by platform:
# linux -> .so, win32 -> .dll, mac -> .dylib.
PLATFORMS = {
    "x86_64-linux":  ("linux64", ".so"),
    "x86-linux":     ("linux32", ".so"),
    "x86_64-win32":  ("win64",   ".dll"),
    "x86-win32":     ("win32",   ".dll"),
    "universal-mac": ("mac",     ".dylib"),
}

# The two NATIVE extensions. Each maps token -> source .lcb (its folder hosts the
# code/ tree). artnet is intentionally absent: it is pure LCB with no binary.
NATIVE_EXTS = {
    "osc":  "src/osc/osc.lcb",
    "midi": "src/midi/midi.lcb",
}

# The pure-LCB extension, named so --check can call it out explicitly.
PURE_LCB_EXTS = ["artnet"]


def flag_attr(ext, suffix):
    """argparse stores --osc-linux64 as args.osc_linux64, etc."""
    return f"{ext}_{suffix}"


def code_root(ext):
    """Absolute path to src/<ext>/code (next to the extension's .lcb)."""
    return os.path.join(ROOT, os.path.dirname(NATIVE_EXTS[ext]), "code")


def dest_path(ext, platform_id, file_suffix):
    """Committed slot: src/<ext>/code/<platform-id>/<ext><file-suffix>."""
    return os.path.join(code_root(ext), platform_id, ext + file_suffix)


def build_parser():
    ap = argparse.ArgumentParser(
        description="Refresh src/<ext>/code/<platform-id>/ from per-platform native "
                    "libraries, for the two native extensions (osc, midi). "
                    "artnet is pure LCB and has no native tree.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Flags pair an extension with a platform, e.g. --osc-linux64, "
               "--midi-mac. Each points at a built library file. With --check, the "
               "committed trees are listed/validated instead.",
    )
    for ext in NATIVE_EXTS:
        for platform_id, (suffix, file_suffix) in PLATFORMS.items():
            ap.add_argument(
                f"--{ext}-{suffix}",
                dest=flag_attr(ext, suffix),
                metavar="LIB",
                help=f"source {file_suffix} for src/{ext}/code/{platform_id}/{ext}{file_suffix}",
            )
    ap.add_argument(
        "--check",
        action="store_true",
        help="list/validate both native trees (osc, midi) and exit; non-zero if "
             "any platform slot is empty. Notes that artnet is pure LCB (no tree).",
    )
    return ap


def rel(p):
    return os.path.relpath(p, ROOT)


def do_check():
    """List/validate the committed native trees. Returns an exit code: non-zero
    if any osc/midi platform slot is empty (such an extension won't load on that
    target). The pure-LCB artnet extension is reported as exempt, not missing."""
    missing = []
    for ext in NATIVE_EXTS:
        lcb = NATIVE_EXTS[ext]
        print(f"Extension '{ext}' (native) -- committed tree src/{ext}/code/<platform-id>/:")
        if not os.path.isfile(os.path.join(ROOT, lcb)):
            print(f"  NOTE: extension source {lcb} not present yet.")
        for platform_id, (_, file_suffix) in PLATFORMS.items():
            dest = dest_path(ext, platform_id, file_suffix)
            if os.path.isfile(dest):
                print(f"  {rel(dest)}  ({os.path.getsize(dest)} bytes)")
            else:
                slot = f"src/{ext}/code/{platform_id}/{ext}{file_suffix}"
                print(f"  MISSING  {slot}")
                missing.append(slot)
        print()

    for ext in PURE_LCB_EXTS:
        print(f"Extension '{ext}' (pure LCB) -- no native binary tree by design.")
        print(f"  Art-Net is plain byte-packing over UDP; src/{ext}/ has no code/ "
              "folder and that is correct, not an error.")
        print()

    if missing:
        print("MISSING -- the affected extension will not load on these targets:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 1
    print("All native slots (osc, midi) are populated; artnet is pure LCB (exempt).")
    return 0


def do_refresh(args):
    """Copy each provided source into its committed slot under the bare token
    name. Returns an exit code."""
    plan = []      # (dest_abspath, src_abspath)
    problems = []
    for ext in NATIVE_EXTS:
        for platform_id, (suffix, file_suffix) in PLATFORMS.items():
            src = getattr(args, flag_attr(ext, suffix))
            if not src:
                continue
            src_abs = src if os.path.isabs(src) else os.path.join(ROOT, src)
            if os.path.isfile(src_abs):
                plan.append((dest_path(ext, platform_id, file_suffix), src_abs))
            else:
                problems.append(f"--{ext}-{suffix}: not a file: {src}")

    if not plan and not problems:
        print("Nothing to do. Pass an --<ext>-<platform> flag (e.g. --osc-linux64,", file=sys.stderr)
        print("--midi-mac) to refresh a slot, or --check to validate the committed", file=sys.stderr)
        print("trees. (src/<ext>/code/ is committed, so a fresh clone is already", file=sys.stderr)
        print("build-ready. artnet is pure LCB and has no native tree.)", file=sys.stderr)
        return 1
    if problems:
        print("Cannot refresh -- inputs missing:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    for dest_abs, src_abs in plan:
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
        shutil.copy2(src_abs, dest_abs)
        print(f"  + {rel(dest_abs)}")

    print()
    print("src/<ext>/<ext>.lcb + src/<ext>/code/ is the ready-to-build extension.")
    print("Open the refreshed src/<ext>/<ext>.lcb in OXT's Extension Builder and Package -> .lce.")
    return 0


def main():
    args = build_parser().parse_args()

    if args.check:
        return do_check()
    return do_refresh(args)


if __name__ == "__main__":
    raise SystemExit(main())
