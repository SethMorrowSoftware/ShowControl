#!/usr/bin/env python3
"""Place native libraries into code/<platform-id>/ beside each native extension.

This is the intended, cross-platform way to ship native code with a LiveCode
Builder extension: the per-platform shared library lives INSIDE the extension, in
a code/<platform-id>/ folder next to the .lcb. When the extension is built and
installed, the IDE maps each library into `the revLibraryMapping` and the engine
resolves the c:<token>> bindings from there -- so the consumer installs ONE
extension and the library comes with it. No loose .so/.dll/.dylib, no /usr/lib,
no sudo, no LD_LIBRARY_PATH, no "next to the stack".

ShowControl ships THREE extensions, of which only TWO carry native code:

  * osc    -- src/osc/osc.lcb   + native lib token "osc"  at src/osc/code/...
  * midi   -- src/midi/midi.lcb + native lib token "midi" at src/midi/code/...
  * artnet -- PURE LCB. No C shim, no native binary, no code/ folder.

The libraries are COMMITTED, so a fresh clone is a ready-to-build extension. They
are landed by .github/workflows/release-binaries.yml (a manual dispatch: every
platform built, tested, floor-checked, verified by tools/install-release-binaries.py,
then committed), the xtalk-suite's model. Each code/ tree carries a MANIFEST.sha256
that this tool keeps current and tools/check-binary-freshness.py verifies, together
with the stronger "is this binary still what the source would produce" legs.

USAGE
    # stage this build's libraries into one platform slot (what each CI lane runs)
    python3 tools/package-extension.py --platform-id x86_64-linux --build-dir build
    # ...or point at explicit files
    python3 tools/package-extension.py --platform-id universal-mac --osc-lib a/osc.dylib --midi-lib b/midi.dylib
    # legacy per-slot flags (still supported)
    python3 tools/package-extension.py --osc-linux64 build/osc.so --midi-win64 dl/midi.dll
    # list the trees, verify each MANIFEST.sha256 (non-zero on any problem)
    python3 tools/package-extension.py --check
    # rewrite the manifests from the files present
    python3 tools/package-extension.py --write-manifest

Refuses: an unknown platform id, the MOCK midi library (it exports midimock_*;
the test build emits it as build/mock/midi with the same bare name), and a file
whose name is not the bare token.

PLATFORM-ID FORMAT: <architecture>-<platform>, verified against the LiveCode/OXT
engine + IDE source (the IDE's code-folder matcher filters on `the processor`
first, then the platform). The architecture comes FIRST -- e.g. x86_64-linux, NOT
linux-x86_64. Windows uses the -win32 suffix for BOTH bitnesses. The bundled file
is the bare token (osc.so / midi.dll / ...), with no "lib" prefix, because it must
equal the c:<token>> binding name (e.g. "c:osc>...").
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# platform-id ("<arch>-<platform>", arch FIRST) -> (legacy flag-suffix, file-suffix).
PLATFORMS = {
    "x86_64-linux":  ("linux64",    ".so"),
    "arm64-linux":   ("linuxarm64", ".so"),     # Raspberry Pi 3/4/5 (64-bit OS), ARM servers
    "x86-linux":     ("linux32",    ".so"),
    "x86_64-win32":  ("win64",      ".dll"),
    "x86-win32":     ("win32",      ".dll"),
    "universal-mac": ("mac",        ".dylib"),
}

# The two NATIVE extensions: token -> source .lcb (its folder hosts code/).
NATIVE_EXTS = {
    "osc":  "src/osc/osc.lcb",
    "midi": "src/midi/midi.lcb",
}
PURE_LCB_EXTS = ["artnet"]
MANIFEST = "MANIFEST.sha256"


def flag_attr(ext, suffix):
    return f"{ext}_{suffix}"


def code_root(ext):
    """Absolute path to src/<ext>/code (next to the extension's .lcb)."""
    return os.path.join(ROOT, os.path.dirname(NATIVE_EXTS[ext]), "code")


def dest_path(ext, platform_id, file_suffix=None):
    """Committed slot: src/<ext>/code/<platform-id>/<ext><file-suffix>."""
    file_suffix = file_suffix or PLATFORMS[platform_id][1]
    return os.path.join(code_root(ext), platform_id, ext + file_suffix)


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_test_double(path):
    """True if the binary exports the mock's midimock_* controls."""
    with open(path, "rb") as f:
        return b"midimock_" in f.read()


# ---------------------------------------------------------------------------
# MANIFEST.sha256 -- "<sha256>  <platform-id>/<file>", sorted, one per library
# ---------------------------------------------------------------------------
def manifest_entries(ext):
    root = code_root(ext)
    out = []
    for pid in sorted(PLATFORMS):
        p = dest_path(ext, pid)
        if os.path.isfile(p):
            out.append((sha256(p), f"{pid}/{os.path.basename(p)}"))
    return out


def write_manifest(ext):
    entries = manifest_entries(ext)
    path = os.path.join(code_root(ext), MANIFEST)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for digest, name in entries:
            f.write(f"{digest}  {name}\n")
    return path, len(entries)


def read_manifest(ext):
    path = os.path.join(code_root(ext), MANIFEST)
    if not os.path.isfile(path):
        return None
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                digest, name = line.split(None, 1)
                out[name.lstrip("*")] = digest
    return out


def verify_manifest(ext):
    """List of problems (empty == the manifest matches the tree exactly)."""
    have = dict((name, d) for d, name in manifest_entries(ext))
    want = read_manifest(ext)
    if want is None:
        return [f"src/{ext}/code/{MANIFEST} is missing (run --write-manifest)"]
    problems = []
    for name, digest in sorted(want.items()):
        if name not in have:
            problems.append(f"src/{ext}/code/{name} is in the manifest but not committed")
        elif have[name] != digest:
            problems.append(f"src/{ext}/code/{name} does not match its manifest hash")
    for name in sorted(set(have) - set(want)):
        problems.append(f"src/{ext}/code/{name} is committed but not in the manifest")
    return problems


# ---------------------------------------------------------------------------
# placing
# ---------------------------------------------------------------------------
def strip_in_place(path):
    """Best-effort `strip --strip-unneeded` for ELF: drops the static symbol table
    and debug sections (and the build machine's paths with them) without touching
    the dynamic exports the engine binds to. A missing strip is a size problem,
    not a correctness one."""
    with open(path, "rb") as f:
        if f.read(4) != b"\x7fELF":
            return "not ELF (left as built)"
    tool = shutil.which("strip")
    if not tool:
        return "not stripped (no strip on PATH)"
    before = os.path.getsize(path)
    r = subprocess.run([tool, "--strip-unneeded", path], capture_output=True)
    if r.returncode != 0:
        return "not stripped (%s)" % r.stderr.decode(errors="replace").strip()
    return "stripped %d -> %d bytes" % (before, os.path.getsize(path))


def find_built(build_dir, ext, platform_id):
    """The newest <ext><suffix> under a CMake build dir, never the mock."""
    suffix = PLATFORMS[platform_id][1]
    hits = []
    for dirpath, dirnames, filenames in os.walk(build_dir):
        parts = os.path.relpath(dirpath, build_dir).replace(os.sep, "/").split("/")
        if "mock" in parts or "_deps" in parts or "lcb-tests" in parts or "lcs-tests" in parts:
            continue
        if ext + suffix in filenames:
            hits.append(os.path.join(dirpath, ext + suffix))
    hits.sort(key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def place(plan, strip=True):
    """plan: [(ext, platform_id, src)]. Verify everything first, then write."""
    problems = []
    for ext, pid, src in plan:
        want = ext + PLATFORMS[pid][1]
        if not os.path.isfile(src):
            problems.append(f"{ext} {pid}: not a file: {src}")
        elif is_test_double(src):
            problems.append(f"{ext} {pid}: {src} is the MOCK library (exports midimock_*) -- "
                            "refusing to package a test double")
        elif os.path.basename(src) != want and not os.path.basename(src).startswith(ext + "-"):
            problems.append(f"{ext} {pid}: expected a file named {want} (got {os.path.basename(src)})")
    if problems:
        return problems
    touched = set()
    for ext, pid, src in plan:
        dest = dest_path(ext, pid)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        note = strip_in_place(dest) if strip else "not stripped"
        print(f"  + {rel(dest)}  ({note})")
        touched.add(ext)
    for ext in sorted(touched):
        path, n = write_manifest(ext)
        print(f"  = {rel(path)}  ({n} libraries)")
    return []


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------
def do_check():
    problems, missing = [], []
    for ext in NATIVE_EXTS:
        print(f"Extension '{ext}' (native) -- src/{ext}/code/<platform-id>/:")
        for pid in PLATFORMS:
            dest = dest_path(ext, pid)
            if os.path.isfile(dest):
                print(f"  {rel(dest)}  ({os.path.getsize(dest)} bytes)")
            else:
                print(f"  MISSING  src/{ext}/code/{pid}/{os.path.basename(dest)}")
                missing.append(f"src/{ext}/code/{pid}")
        problems += verify_manifest(ext)
        print()
    for ext in PURE_LCB_EXTS:
        print(f"Extension '{ext}' (pure LCB) -- no native tree by design (correct, not an error).\n")
    for p in problems:
        print("MANIFEST: " + p, file=sys.stderr)
    if missing:
        print("MISSING slots (the extension will not load there): " + ", ".join(missing), file=sys.stderr)
    if problems or missing:
        return 1
    print("All native slots populated; every MANIFEST.sha256 matches.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Place native libraries into src/<ext>/code/<platform-id>/.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform-id", choices=sorted(PLATFORMS))
    ap.add_argument("--build-dir", help="CMake build dir to take osc + midi from (with --platform-id)")
    ap.add_argument("--osc-lib", help="explicit osc library (with --platform-id)")
    ap.add_argument("--midi-lib", help="explicit midi library (with --platform-id)")
    ap.add_argument("--no-strip", action="store_true")
    ap.add_argument("--check", action="store_true", help="list the trees and verify the manifests")
    ap.add_argument("--write-manifest", action="store_true", help="rewrite both MANIFEST.sha256 files")
    for ext in NATIVE_EXTS:
        for pid, (suffix, file_suffix) in PLATFORMS.items():
            ap.add_argument(f"--{ext}-{suffix}", dest=flag_attr(ext, suffix), metavar="LIB",
                            help=f"(legacy) source for src/{ext}/code/{pid}/{ext}{file_suffix}")
    a = ap.parse_args()

    if a.check:
        return do_check()
    if a.write_manifest:
        for ext in NATIVE_EXTS:
            path, n = write_manifest(ext)
            print(f"wrote {rel(path)} ({n} libraries)")
        return 0

    plan = []
    if a.platform_id:
        for ext, explicit in (("osc", a.osc_lib), ("midi", a.midi_lib)):
            src = explicit or (find_built(a.build_dir, ext, a.platform_id) if a.build_dir else None)
            if src:
                plan.append((ext, a.platform_id, src))
            elif a.build_dir:
                print(f"  ({ext}: no {ext}{PLATFORMS[a.platform_id][1]} found in {a.build_dir})", file=sys.stderr)
    for ext in NATIVE_EXTS:
        for pid, (suffix, _) in PLATFORMS.items():
            src = getattr(a, flag_attr(ext, suffix))
            if src:
                plan.append((ext, pid, src if os.path.isabs(src) else os.path.join(ROOT, src)))
    if not plan:
        print("Nothing to do: pass --platform-id with --build-dir (or --osc-lib/--midi-lib), "
              "a legacy --<ext>-<platform> flag, --check, or --write-manifest.", file=sys.stderr)
        return 1
    problems = place(plan, strip=not a.no_strip)
    if problems:
        print("Refusing to package:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
