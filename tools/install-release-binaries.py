#!/usr/bin/env python3
"""install-release-binaries.py - land a release-binaries bundle into the tree.

USAGE
    python3 tools/install-release-binaries.py <bundle-dir> [--dry-run]
    python3 tools/install-release-binaries.py --selftest

The bundle is what .github/workflows/release-binaries.yml publishes, laid out as

    <ext>/<platform-id>/<ext>.<so|dll|dylib>          (ext: osc, midi)

WHO CALLS THIS. Both paths, and they are the same code on purpose (the
xtalk-suite's rule: a verifier that only guards the manual path guards the path
nobody takes):
  * release-binaries.yml's commit job runs it on the runner, between a freshly
    built artifact and the repository;
  * you run it by hand on a downloaded bundle (commit_mode: none).

WHAT IT CHECKS BEFORE TOUCHING ANYTHING -- every file, all checks, and the tree
is written only if EVERY file passes, so a bad bundle cannot leave the tree half
updated. Each library goes through tools/check-binary-freshness.py's own
check_library: the FILENAME is the bare token the c:<ext>> bind resolves; the
OBJECT FORMAT and ARCHITECTURE match the platform directory (a thin dylib under
universal-mac, an x86 DLL under x86_64-win32 are REFUSED); every .lcb bind is
exported and nothing else is (no RtMidi / C++ runtime leak); the ABI decoded from
the machine code equals the header; ELF/PE dependencies are system-only and
under the glibc floor; it is not the MOCK midi library. Then it copies, refreshes
each MANIFEST.sha256, and prints a verdict per library -- "(new)", "(unchanged)"
(rebuilt byte-identical), or "(REPLACES the committed one)". It never commits.

--selftest drives main() over throwaway bundles against a temporary tree: the
committed osc libraries for the accept case (only those platforms whose committed
osc is currently clean), and synthesised or mislabelled files for each refusal.
"""

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pkg = _load("pkgext", "package-extension.py")
fresh = _load("freshness", "check-binary-freshness.py")


def scan(bundle):
    """[(ext, platform_id, path)] and a list of refusals for anything malformed."""
    found, refusals = [], []
    for ext in sorted(os.listdir(bundle)):
        d = os.path.join(bundle, ext)
        if not os.path.isdir(d):
            continue
        if ext not in pkg.NATIVE_EXTS:
            print("  skip  %s/ (not a native ShowControl extension)" % ext)
            continue
        for pid in sorted(os.listdir(d)):
            pd = os.path.join(d, pid)
            if not os.path.isdir(pd):
                continue
            if pid not in pkg.PLATFORMS:
                refusals.append("%s/%s: unknown platform id" % (ext, pid))
                continue
            files = [f for f in os.listdir(pd) if not f.startswith(".")]
            want = ext + pkg.PLATFORMS[pid][1]
            for f in files:
                if f != want:
                    refusals.append("%s/%s/%s: the file must be named %s -- the engine resolves "
                                    "c:%s> to exactly that name" % (ext, pid, f, want, ext))
            if want in files:
                found.append((ext, pid, os.path.join(pd, want)))
    return found, refusals


def main(argv):
    if argv[:1] == ["--selftest"]:
        return selftest()
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    bundle, dry = argv[0], "--dry-run" in argv
    found, refusals = scan(bundle)
    print("verifying %d librar%s from %s" % (len(found), "y" if len(found) == 1 else "ies", bundle))
    for ext, pid, path in found:
        problems, notes = fresh.check_library(path, ext, pid)
        print("  %s %s/%s%s" % ("REFUSE" if problems else "ok    ", ext, pid,
                                ("  (" + "; ".join(notes) + ")") if notes else ""))
        for p in problems:
            refusals.append("%s/%s: %s" % (ext, pid, p))
    if refusals:
        print("\nREFUSED -- nothing was written:")
        for r in refusals:
            print("  - " + r)
        return 1
    if not found:
        print("nothing to install")
        return 1
    touched = set()
    for ext, pid, path in found:
        dest = pkg.dest_path(ext, pid)
        if not os.path.isfile(dest):
            verdict = "new"
        elif pkg.sha256(dest) == pkg.sha256(path):
            verdict = "unchanged"
        else:
            verdict = "REPLACES the committed one"
        if not dry:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)
            touched.add(ext)
        print("  %s  (%s)" % (pkg.rel(dest), verdict))
    for ext in sorted(touched):
        path, n = pkg.write_manifest(ext)
        print("  %s refreshed (%d libraries)" % (pkg.rel(path), n))
    if dry:
        print("(dry run: nothing written)")
    return 0


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------
def selftest():
    fails = []
    real_root = pkg.ROOT

    def run(bundle):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([bundle])
        return rc, buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        # A throwaway tree: the real sources (so the oracles read the real .lcb /
        # headers) but code/ trees we may write.
        tree = os.path.join(tmp, "tree")
        for sub in ("src/osc", "src/midi"):
            os.makedirs(os.path.join(tree, sub))
        for f in ("src/osc/osc.lcb", "src/osc/osc_shim.h", "src/midi/midi.lcb", "src/midi/midi_shim.h"):
            shutil.copy2(os.path.join(real_root, f), os.path.join(tree, f))
        pkg.ROOT = fresh.ROOT = tree
        try:
            clean = []
            for pid in pkg.PLATFORMS:
                p = os.path.join(real_root, "src", "osc", "code", pid, "osc" + pkg.PLATFORMS[pid][1])
                if os.path.isfile(p) and not fresh.check_library(p, "osc", pid)[0]:
                    clean.append((pid, p))
            if not clean:
                fails.append("no clean committed osc library to drive the accept case with")

            def bundle_with(entries):
                b = tempfile.mkdtemp(dir=tmp)
                for ext, pid, name, src in entries:
                    d = os.path.join(b, ext, pid)
                    os.makedirs(d, exist_ok=True)
                    if isinstance(src, bytes):
                        open(os.path.join(d, name), "wb").write(src)
                    else:
                        shutil.copy2(src, os.path.join(d, name))
                return b

            if clean:
                pid, src = clean[0]
                name = "osc" + pkg.PLATFORMS[pid][1]
                rc, out = run(bundle_with([("osc", pid, name, src)]))
                if rc != 0 or "(new)" not in out:
                    fails.append("accept: a clean library did not install as (new):\n" + out)
                if not os.path.isfile(os.path.join(tree, "src", "osc", "code", "MANIFEST.sha256")):
                    fails.append("accept: no MANIFEST.sha256 was created")
                rc, out = run(bundle_with([("osc", pid, name, src)]))
                if rc != 0 or "(unchanged)" not in out:
                    fails.append("accept: a byte-identical rebuild was not reported (unchanged)")
                # REFUSALS -- each must refuse and write nothing
                wrong_pid = {"x86_64-win32": "x86-win32", "x86-win32": "x86_64-win32",
                             "x86_64-linux": "arm64-linux", "universal-mac": "x86_64-linux"}.get(pid, "x86_64-win32")
                cases = [
                    ("wrong architecture for the directory",
                     [("osc", wrong_pid, "osc" + pkg.PLATFORMS[wrong_pid][1], src)], "architecture"),
                    ("wrong file name", [("osc", pid, "libosc" + pkg.PLATFORMS[pid][1], src)], "must be named"),
                    ("unknown platform id", [("osc", "sparc-solaris", "osc.so", src)], "unknown platform"),
                    ("not a library at all", [("osc", pid, name, b"not an object file")], "unreadable"),
                    ("a thin Mach-O under universal-mac",
                     [("osc", "universal-mac", "osc.dylib",
                       b"\xcf\xfa\xed\xfe" + (0x0100000C).to_bytes(4, "little") + b"\0" * 24)], "promises"),
                ]
                for title, entries, needle in cases:
                    before = sorted(os.listdir(os.path.join(tree, "src", "osc", "code")))
                    rc, out = run(bundle_with(entries))
                    after = sorted(os.listdir(os.path.join(tree, "src", "osc", "code")))
                    if rc == 0 or "REFUSED" not in out or needle not in out:
                        fails.append("refuse (%s): expected a refusal mentioning %r:\n%s" % (title, needle, out))
                    if before != after:
                        fails.append("refuse (%s): the tree was modified" % title)
        finally:
            pkg.ROOT = fresh.ROOT = real_root
    for f in fails:
        print("  [FAIL] " + f)
    print("install-release-binaries selftest: %s" % ("%d failure(s)" % len(fails) if fails else "all cases behave"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
