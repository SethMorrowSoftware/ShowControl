#!/usr/bin/env python3
"""Fixture test for tools/check-livecodescript.py (fixture before gate).

A gate that never fires still prints OK. For every rule, this proves the rule
FIRES on a known-bad snippet and stays SILENT on its correct neighbour -- so a
regex typo that disables a rule fails here instead of silently passing CI.

Run:  python3 tests/checker_fixtures_test.py
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("checker", str(ROOT / "tools" / "check-livecodescript.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

# (name, checker function, bad snippet, good neighbour)
FIXTURES = [
    ("smart quotes", C.check_smart_quotes,
     'put “hello” into tX', 'put "hello" into tX'),
    ("LCB numToByte", C.check_lcb_scriptisms,
     "put numToByte(65) into tX", "put the byte with code (65) into tX"),
    ("LCB div", C.check_lcb_scriptisms,
     "put tA div tB into tQ", "put (tA - (tA mod tB)) / tB into tQ"),
    ("LCB foreign handler returning a ZString", C.check_lcb_scriptisms,
     'private foreign handler _e() returns ZStringUTF8 binds to "c:osc>osc_error_str!cdecl"',
     'private foreign handler _e(in pOut as optional Pointer, in pCap as CInt) returns CInt binds to "c:osc>osc_last_error!cdecl"'),
    ("LCB foreign handler returning an optional ZString", C.check_lcb_scriptisms,
     'private foreign handler _d() returns optional ZStringNative binds to "c:>dlerror!cdecl"',
     'private foreign handler _d() returns optional Pointer binds to "c:>dlerror!cdecl"'),
    ("LCB unclosed unsafe", C.check_lcb_structure,
     "handler f()\n   unsafe\n      _x()\nend handler",
     "handler f()\n   unsafe\n      _x()\n   end unsafe\nend handler"),
    ("Script does not contain", C.check_script_forbidden,
     "on f\n   if tA does not contain tB then beep\nend f",
     "on f\n   if not (tA contains tB) then beep\nend f"),
    ("Script repeat step", C.check_script_forbidden,
     "on f\n   repeat with i = 1 to 9 step 3\n   end repeat\nend f",
     "on f\n   repeat with i = 1 to 3\n   end repeat\nend f"),
    ("Script [...] literal", C.check_script_array_literals,
     'on f\n   put ["a", 1] into tX\nend f',
     'on f\n   put "a" into tX[1]\nend f'),
    ("Script unbalanced handler", C.check_script_structure,
     "on f\n   beep\n", "on f\n   beep\nend f"),
]

# Things that must NOT be flagged any more (rules that were wrong).
MUST_PASS = [
    ("LCB and/or are real operators (compiler built-ins)", C.check_lcb_scriptisms,
     "if tA < 0 or tA > 9 then\nend if\nif tB and tC then\nend if"),
    ("a rule word inside a string is ignored", C.check_script_forbidden,
     'on f\n   put "does not contain" into tX\nend f'),
]


def main():
    fails = 0
    for name, fn, bad, good in FIXTURES:
        fired, quiet = bool(fn(bad)), not fn(good)
        ok = fired and quiet
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               "" if ok else ("  (fired on bad: %s, quiet on good: %s)" % (fired, quiet))))
        fails += not ok
    for name, fn, text in MUST_PASS:
        problems = fn(text)
        print("  [%s] %s%s" % ("PASS" if not problems else "FAIL", name, "" if not problems else "  " + problems[0]))
        fails += bool(problems)
    print("checker fixtures: %d rule(s), %d failure(s)" % (len(FIXTURES) + len(MUST_PASS), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
