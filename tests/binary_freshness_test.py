#!/usr/bin/env python3
"""Fixture test for tools/check-binary-freshness.py (fixture before gate).

Once the committed binaries are fresh, every leg of the freshness gate passes --
and a leg that could no longer FAIL would pass forever, silently. So this drives
check_library over a real committed library with each expectation mutated, and
requires the matching leg to fire; then requires the unmutated library to pass.

    python3 tests/binary_freshness_test.py
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("fresh", os.path.join(ROOT, "tools", "check-binary-freshness.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)

FAILS = []


def expect(name, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
    if not cond:
        FAILS.append(name + (" -- " + detail if detail else ""))


def pick():
    """A committed osc library (osc is closed and dependency-light everywhere)."""
    for pid in ("universal-mac", "x86_64-linux", "x86_64-win32", "arm64-linux", "x86-win32", "x86-linux"):
        p = F.pkg.dest_path("osc", pid)
        if os.path.isfile(p):
            return p, pid
    return None, None


def main():
    path, pid = pick()
    if not path:
        print("no committed osc library to test against")
        return 1
    print("fixture library: %s" % F.pkg.rel(path))
    base_problems, _ = F.check_library(path, "osc", pid)
    # Legs unrelated to the mutation may already fail on a stale tree; each
    # mutation must ADD its own problem on top of whatever is there.

    def with_patch(attr, value, needle, title):
        orig = getattr(F, attr)
        setattr(F, attr, value)
        try:
            problems, _ = F.check_library(path, "osc", pid)
        finally:
            setattr(F, attr, orig)
        hit = [p for p in problems if needle in p and p not in base_problems]
        expect(title, bool(hit), "problems: %s" % problems)

    with_patch("header_abi", lambda ext: 999, "STALE", "a stale ABI is reported (decoded value != header)")
    real_binds = F.bound_symbols("osc")
    with_patch("bound_symbols", lambda ext: real_binds | {"osc_not_a_real_symbol"},
               "NOT exported", "a bind the library lacks is a load failure")
    real_api = F.api_symbols("osc")
    with_patch("api_symbols", lambda ext: real_api - {"osc_parse"}, "beyond the osc_* ABI",
               "an export outside the declared ABI opens the closure")
    with_patch("api_symbols", lambda ext: real_api | {"osc_future_entry"}, "not exported",
               "a declared entry point the library lacks is reported")
    wrong = {"x86_64-win32": "x86-win32", "x86-win32": "x86_64-win32", "universal-mac": "x86_64-linux",
             "x86_64-linux": "arm64-linux", "arm64-linux": "x86_64-linux", "x86-linux": "x86_64-linux"}[pid]
    problems, _ = F.check_library(path, "osc", wrong)
    expect("a library in the wrong platform directory is refused",
           any("architecture" in p or "object format" in p for p in problems), str(problems))
    problems, _ = F.check_library(os.path.join(ROOT, "README.md"), "osc", pid)
    expect("a non-library is unreadable, not passed", any("unreadable" in p for p in problems), str(problems))

    # The unmutated gate over the same file must not invent problems: everything
    # it reports must be a REAL finding (stale ABI, deps, floor) -- never the
    # closure/bind legs for osc, whose committed builds are closed.
    spurious = [p for p in base_problems if "NOT exported" in p or "beyond the osc_* ABI" in p]
    expect("no spurious bind/closure problems on the real committed osc", not spurious, str(spurious))

    print("binary freshness fixtures: %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
