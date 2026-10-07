#!/usr/bin/env python3
"""Run every ShowControl test layer, in order, and print one summary.

    python3 tools/test-all.py --oxt-bin /path/to/oxt/bin            # everything
    python3 tools/test-all.py --oxt-bin ... --skip-build             # reuse ./build
    python3 tools/test-all.py                                        # no OXT: the
                                                                     # layers that need none

Layers: static gates (+ fixtures), wire-format vectors, virtual peers, vectors.h
freshness, CMake build + ctest, the LCB suite (lc-run), the LiveCode Script suite
(the engine, -ui). The two OXT layers need --oxt-bin / $OXT_BIN and are reported
as NOT RUN without it -- never as passed. Extra flags after `--` go to both OXT
runners (e.g. `-- --filter osc`). Exit status is non-zero if any layer failed.
See docs/testing.md.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(name, cmd, results, **kw):
    print("\n" + "=" * 72 + "\n== " + name + "\n" + "=" * 72, flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), **kw)
    results.append((name, "PASS" if r.returncode == 0 else "FAIL", time.time() - t0))
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--oxt-bin", default=os.environ.get("OXT_BIN"))
    ap.add_argument("--build-dir", default=str(ROOT / "build"))
    ap.add_argument("--skip-build", action="store_true", help="reuse an existing CMake build")
    ap.add_argument("--config", default="Release")
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- extra args for the OXT runners")
    a = ap.parse_args()
    extra = [x for x in a.rest if x != "--"]
    results = []

    run("static gates + their fixtures", [PY, "tests/checker_fixtures_test.py"], results)
    run("static gates", [PY, "tools/check-livecodescript.py"], results)
    run("wire-format vectors (clean-room + python-osc if installed)", [PY, "tests/vectors_reference_test.py"], results)
    run("Art-Net golden packets", [PY, "tests/artnet_golden_test.py"], results)
    run("virtual peers self-test", [PY, "tests/simulators_test.py"], results)
    run("vectors.h is fresh", [PY, "tools/gen_vectors.py", "--check"], results)
    run("binary installer selftest", [PY, "tools/install-release-binaries.py", "--selftest"], results)
    run("binary freshness fixtures", [PY, "tests/binary_freshness_test.py"], results)
    run("COMMITTED binaries are fresh + portable", [PY, "tools/check-binary-freshness.py"], results)

    built = True
    if not a.skip_build:
        built = run("cmake configure", ["cmake", "-S", ".", "-B", a.build_dir,
                                        "-DCMAKE_BUILD_TYPE=" + a.config, "-DSHOWCONTROL_BUILD_TESTS=ON"], results)
        built = built and run("cmake build", ["cmake", "--build", a.build_dir, "--config", a.config], results)
    if built:
        run("ctest (smoke, vectors, mock MIDI, fuzzer)",
            ["ctest", "--test-dir", a.build_dir, "--build-config", a.config,
             "--output-on-failure", "--no-tests=error"], results)

    if a.oxt_bin:
        run("LCB bindings under lc-run",
            [PY, "tools/run-lcb-tests.py", "--oxt-bin", a.oxt_bin, "--build-dir", a.build_dir] + extra, results)
        run("LiveCode Script in the engine (-ui)",
            [PY, "tools/run-lcs-tests.py", "--oxt-bin", a.oxt_bin, "--build-dir", a.build_dir] + extra, results)
    else:
        results.append(("LCB bindings under lc-run", "NOT RUN (no --oxt-bin / $OXT_BIN)", 0.0))
        results.append(("LiveCode Script in the engine", "NOT RUN (no --oxt-bin / $OXT_BIN)", 0.0))

    print("\n" + "=" * 72)
    for name, verdict, secs in results:
        print("%-8s %-58s %6.1fs" % (verdict.split()[0] if verdict != "PASS" else "PASS", name, secs)
              + ("" if verdict in ("PASS", "FAIL") else "   " + verdict))
    failed = [n for n, v, _ in results if v == "FAIL"]
    print("=" * 72)
    print("%d layer(s) failed: %s" % (len(failed), ", ".join(failed)) if failed else "all layers that ran passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
