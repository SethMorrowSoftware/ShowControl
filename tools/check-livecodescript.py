#!/usr/bin/env python3
"""Static gates for the ShowControl script layers (.lcb and .livecodescript).

This is the FAST, dependency-free first line. Since the headless suites landed,
the real compilers are in the loop too -- tools/run-lcb-tests.py compiles every
.lcb with lc-compile and runs it under lc-run, and tools/run-lcs-tests.py asks a
real engine to compile every .livecodescript -- so this script's job is to catch
the cheap, known mistakes in a second, with no OXT install, and to explain them
better than the engine does (a broken script-only stack fails SILENTLY there).
Every rule below was learned from a real failure; when a rule and the real
compiler disagree, the compiler wins and the rule is fixed (the old "LCB has no
and/or" rule was wrong: both are compiler built-ins, grammar.g).

ShowControl has two script layers, with different grammars, and this tool lints
BOTH:

  1. The LiveCode Builder (LCB) bindings -- src/<ext>/<ext>.lcb (osc, midi,
     artnet). LCB is NOT LiveCodeScript: handlers are written
     ``handler NAME(...) ... end handler`` (with ``public``/``private``
     modifiers), foreign declarations are a single line
     ``foreign handler ... binds to "..."`` with NO ``end``, and control blocks
     are ``if ... then ... end if`` / ``repeat ... end repeat`` /
     ``unsafe ... end unsafe``.
  2. The example stacks -- examples/*.livecodescript -- in ordinary
     LiveCodeScript (handlers ``on``/``command``/``function``/``getprop``/
     ``setprop``/``before``/``after`` closed by ``end <name>``; control
     structures ``if ... then`` / ``repeat`` / ``switch`` / ``try``).

Both layers are checked for:

  * Smart/curly quotes -- U+2018/2019/201C/201D anywhere (even in a comment or
    string) fail to compile in OXT. Must be zero.
  * Handler balance -- every handler opener has its matching closer (and foreign
    LCB handlers, being single-line, are NOT expected to have one).
  * Control-structure balance -- every block is closed inside the handler that
    opens it. (Logical lines are reassembled across ``\\`` continuations and
    comments are stripped first, so multi-line conditions do not false-positive.)

The examples layer additionally gets LiveCodeScript's dangling-else gate.

The examples/ directory may be empty for now, and the .lcb bindings may not exist
yet -- both are handled gracefully (the tool reports what it would check and does
not fail merely because a file is absent).

Usage::

    python3 tools/check-livecodescript.py

Exit status is non-zero if any gate fails (suitable for CI and pre-commit use).
"""

import re
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The LCB bindings for the three extensions (osc, midi, artnet). Listed
# explicitly so a missing file is reported as "would check" rather than silently
# skipped; also globbed so any additional src/<ext>/<ext>.lcb is picked up.
LCB_EXPECTED = [
    ROOT / "src" / "osc" / "osc.lcb",
    ROOT / "src" / "midi" / "midi.lcb",
    ROOT / "src" / "artnet" / "artnet.lcb",
]
LCB_TARGETS = sorted(set(LCB_EXPECTED) | set((ROOT / "src").glob("*/*.lcb"))
                     | set((ROOT / "tests" / "lcb").glob("*.lcb")))

# The example stacks, in LiveCodeScript. The directory may be empty for now.
SCRIPT_TARGETS = sorted((ROOT / "examples").glob("*.livecodescript")) + \
    sorted((ROOT / "tests" / "lcs").glob("*.livecodescript"))

SMART_QUOTES = {0x2018, 0x2019, 0x201C, 0x201D}

# LiveCodeScript handler openers. The closer is always "end <name>";
# "end if/repeat/switch/try" close control structures and are handled separately.
SCRIPT_OPENERS = ("on", "command", "function", "getprop", "setprop", "before", "after")


# ---------------------------------------------------------------------------
# Shared text handling (comment stripping + logical-line reassembly).
# ---------------------------------------------------------------------------

def strip_comment(line):
    """Drop a trailing ``--`` line comment, but not a ``--`` inside a string.
    LiveCode/LCB strings have no backslash escapes, so a double quote always
    toggles in/out of a string."""
    out = []
    in_string = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_string = not in_string
            out.append(c)
        elif not in_string and c == "-" and i + 1 < len(line) and line[i + 1] == "-":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def logical_lines(text):
    """Yield ``(lineno, code)`` logical lines: comments stripped, and physical
    lines joined across a trailing ``\\`` continuation. ``lineno`` is the first
    physical line of the logical line, for reporting."""
    out = []
    buf = ""
    start = None
    for i, raw in enumerate(text.split("\n"), 1):
        if start is None:
            start = i
        code = strip_comment(raw)
        if code.rstrip().endswith("\\"):
            buf += code.rstrip()[:-1] + " "
        else:
            buf += code
            out.append((start, buf))
            buf = ""
            start = None
    if buf:
        out.append((start, buf))
    return out


def check_smart_quotes(text):
    bad = []
    for i, raw in enumerate(text.split("\n"), 1):
        hits = [c for c in raw if ord(c) in SMART_QUOTES]
        if hits:
            bad.append(
                f"  L{i}: smart quote(s) {''.join(sorted(set(hits)))} -- use straight ASCII"
            )
    return bad


# ---------------------------------------------------------------------------
# LiveCode Builder (.lcb) structure gate.
# ---------------------------------------------------------------------------

def check_lcb_structure(text):
    """Prove LCB handler matching and control-structure balance.

    LCB handlers open with ``handler NAME(...)`` (optionally prefixed
    ``public``/``private``) and close with ``end handler`` -- note the closer is
    the bare keyword ``end handler``, NOT ``end NAME`` as in LiveCodeScript.

    ``foreign handler ... binds to "..."`` is a single-line declaration with NO
    body and NO ``end handler``; it must be recognised and skipped, or it would
    be mis-counted as an unclosed handler.

    Control blocks matched strictly: ``repeat`` / ``unsafe`` close with
    ``end repeat`` / ``end unsafe``. ``if`` is matched leniently (LCB allows a
    single-line ``if ... then <stmt>``), so a block ``if ... then`` is pushed but
    a stray ``end if`` with no open ``if`` is ignored rather than flagged. A
    truly unclosed block ``if`` is still caught, because its open frame trips the
    end-of-handler "unclosed" check."""
    errors = []
    in_handler = False
    handler_line = None
    ctrl = []  # stack of (kind, lineno) inside the current handler

    for lineno, code in logical_lines(text):
        low = code.strip().lower()
        if not low:
            continue
        toks = low.split()

        if not in_handler:
            # A foreign handler is a single-line declaration: no body, no end.
            if toks[0] == "foreign" and len(toks) > 1 and toks[1] == "handler":
                continue
            # public/private are modifiers; the opener keyword may be at [0] or [1].
            is_handler = toks[0] == "handler" or (
                toks[0] in ("public", "private") and len(toks) > 1 and toks[1] == "handler"
            )
            # A modified foreign handler ("public foreign handler ...") also has no end.
            is_foreign = "foreign" in toks[:2] and "handler" in toks[:3]
            if is_handler and not is_foreign:
                in_handler = True
                handler_line = lineno
                ctrl = []
            continue

        # --- inside a handler ---
        if toks[0] == "end" and len(toks) >= 2 and toks[1] in ("if", "repeat", "unsafe"):
            kind = toks[1]
            if kind == "if":
                if ctrl and ctrl[-1][0] == "if":
                    ctrl.pop()  # else: stray / hybrid -- leniently ignore
            elif ctrl and ctrl[-1][0] == kind:
                ctrl.pop()
            else:
                errors.append(f"  L{lineno}: stray 'end {kind}' inside handler (L{handler_line})")
        elif toks[0] == "end" and len(toks) >= 2 and toks[1] == "handler":
            if ctrl:
                kind, opened = ctrl[-1]
                errors.append(
                    f"  handler at L{handler_line}: unclosed '{kind}' opened at L{opened}"
                )
            in_handler = False
            handler_line = None
            ctrl = []
        elif re.match(r"^if\b", low) and re.search(r"\bthen$", low):
            ctrl.append(("if", lineno))  # block if; "else if" starts with "else", excluded
        elif toks[0] == "repeat":
            ctrl.append(("repeat", lineno))
        elif toks[0] == "unsafe" or low == "unsafe":
            ctrl.append(("unsafe", lineno))

    if in_handler:
        errors.append(f"  handler at L{handler_line}: never closed (missing 'end handler')")
    return errors


def check_lcb_module(text):
    """An LCB ``module``/``library``/``widget`` must be closed with a matching
    ``end module``/``end library``/``end widget`` terminator. Without it the engine
    parser runs to EOF and reports a bare ``syntax error`` at end-of-file -- the
    exact failure the first OXT compile hit on all three bindings (the handler and
    control gates above do not cover the module frame). Catch it here."""
    # logical_lines() only strips '--' comments; blank out /* */ block comments
    # (keeping newlines so line numbers stay accurate) so a word like "library"
    # inside the header comment cannot false-match the declaration.
    clean = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    opener = None      # (kind, lineno) of the first module/library/widget line
    closed = False
    for lineno, code in logical_lines(clean):
        toks = code.strip().lower().split()
        if not toks:
            continue
        if opener is None and toks[0] in ("module", "library", "widget"):
            opener = (toks[0], lineno)
        elif toks[0] == "end" and len(toks) >= 2 and toks[1] in ("module", "library", "widget"):
            if opener and toks[1] == opener[0]:
                closed = True
    if opener is None:
        return ["  no 'module'/'library'/'widget' declaration found"]
    if not closed:
        kind, lineno = opener
        return [f"  '{kind}' at L{lineno} is never closed (missing 'end {kind}' at end of file)"]
    return []


# ---------------------------------------------------------------------------
# LiveCode Builder script-ism gate: constructs that are valid LiveCode Script
# but DO NOT EXIST in LiveCode Builder. Each of these was hit on the first OXT
# compile pass; flag them statically so they cannot regress.
# ---------------------------------------------------------------------------
LCB_FORBIDDEN = (
    (re.compile(r"\bnumToByte\s*\("), "numToByte() is LiveCode Script -- LCB uses 'the byte with code (n)'"),
    (re.compile(r"\bbyteToNum\s*\("), "byteToNum() is LiveCode Script -- LCB uses 'the code of (b)'"),
    (re.compile(r"\bnumToChar\s*\("), "numToChar() is LiveCode Script -- LCB uses 'the char with code (n)'"),
    (re.compile(r"\bcharToNum\s*\("), "charToNum() is LiveCode Script -- LCB uses 'the code of (c)'"),
    (re.compile(r"\bdiv\b"),          "'div' is LiveCode Script -- LCB has no integer-division operator"),
    # NOTE: `and` / `or` ARE valid LCB. They are commented out in logic.lcb only
    # because the COMPILER implements them (short-circuit, toolchain/lc-compile
    # grammar.g), on the 9.6.3 floor too; a rule here once claimed otherwise and
    # forced awkward nested ifs. lc-compile now arbitrates (tools/run-lcb-tests.py).
)

# Foreign-binding rules (checked on the declaration line itself).
LCB_FOREIGN_FORBIDDEN = (
    (re.compile(r"\bforeign\s+handler\b.*\breturns\s+(optional\s+)?ZString", re.I),
     "a foreign handler must not RETURN a ZString*: the engine wraps it in a foreign value whose "
     "finalizer free()s the pointer -- a static/model-owned C string crashes or double-frees "
     "(libscript module-foreign.cpp). Use a caller buffer (bytes-written / -needed)"),
)


def check_lcb_scriptisms(text):
    """Flag LiveCode Script-only constructs that are not valid LiveCode Builder.
    Runs on comment- and string-stripped code so the same words appearing in
    prose or string literals are ignored."""
    # blank /* */ block comments (preserve newlines so line numbers stay accurate)
    clean = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    errors = []
    for lineno, raw in enumerate(clean.split("\n"), 1):
        code = strip_comment(raw)                       # drop trailing -- comment
        code = re.sub(r'"[^"]*"', '""', code)           # neutralise string-literal contents
        for rx, msg in LCB_FORBIDDEN:
            if rx.search(code):
                errors.append(f"  L{lineno}: {msg}")
    for lineno, raw in enumerate(clean.split("\n"), 1):
        code = strip_comment(raw)
        for rx, msg in LCB_FOREIGN_FORBIDDEN:
            if rx.search(code):
                errors.append(f"  L{lineno}: {msg}")
    return errors


# ---------------------------------------------------------------------------
# LiveCodeScript (.livecodescript) structure + dangling-else gates.
# ---------------------------------------------------------------------------

def check_script_structure(text):
    """Prove LiveCodeScript handler-name matching *and* control-structure
    balance in a single pass. Returns a list of human-readable error strings.

    ``repeat`` / ``switch`` / ``try`` are unambiguous blocks, matched strictly.
    ``if`` is matched leniently: LiveCode allows single-line ``if ... then X``
    and hybrid chains, so a block ``if ... then`` is pushed but an ``end if``
    with no open ``if`` is ignored rather than flagged. A truly unclosed block
    ``if`` is still caught by the end-of-handler "unclosed" check below."""
    errors = []
    handler = None  # (name, lineno) of the open handler, or None
    ctrl = []       # stack of (kind, lineno) inside the current handler

    for lineno, code in logical_lines(text):
        low = code.strip().lower()
        if not low:
            continue
        toks = low.split()

        if handler is None:
            if toks[0] in SCRIPT_OPENERS:
                handler = (toks[1] if len(toks) > 1 else "?", lineno)
                ctrl = []
            continue

        # --- inside a handler ---
        if toks[0] == "end" and len(toks) >= 2 and toks[1] in ("if", "repeat", "switch", "try"):
            kind = toks[1]
            if kind == "if":
                if ctrl and ctrl[-1][0] == "if":
                    ctrl.pop()  # else: hybrid chain / stray -- leniently ignore
            elif ctrl and ctrl[-1][0] == kind:
                ctrl.pop()
            else:
                errors.append(f"  L{lineno}: stray 'end {kind}' in handler '{handler[0]}'")
        elif toks[0] == "end":
            name = toks[1] if len(toks) > 1 else ""
            if ctrl:
                kind, opened = ctrl[-1]
                errors.append(
                    f"  handler '{handler[0]}' (L{handler[1]}): unclosed '{kind}' opened at L{opened}"
                )
            elif name != handler[0]:
                errors.append(f"  L{lineno}: 'end {name}' closes handler '{handler[0]}' (L{handler[1]})")
            handler = None
            ctrl = []
        elif re.match(r"^if\b", low) and re.search(r"\bthen$", low):
            ctrl.append(("if", lineno))  # block if; "else if" starts with "else", excluded
        elif toks[0] == "repeat":
            ctrl.append(("repeat", lineno))
        elif toks[0] == "switch":
            ctrl.append(("switch", lineno))
        elif low == "try":
            ctrl.append(("try", lineno))

    if handler is not None:
        errors.append(f"  handler '{handler[0]}' (L{handler[1]}): never closed (missing 'end {handler[0]}')")
    return errors


def check_dangling_else(text):
    """A single-line ``if ... then <stmt>`` directly followed by a BARE ``else``
    line. LiveCode/OXT binds that else to the single-line if (the dangling-else
    rule), so the bare else opens a block belonging to the *inner* if -- its
    ``end if`` then closes the wrong frame and the *outer* block-if is left open,
    surfacing as a baffling "missing end if" at the handler's end. Legal
    neighbours are ``else <statement>`` (single-line chain) or a bare ``else``
    under a block ``if ... then``; this exact pairing is the only broken one, and
    the purely structural pass above cannot see it."""
    errors = []
    lines = logical_lines(text)
    for (ln, code), (ln2, nxt) in zip(lines, lines[1:]):
        low = code.strip().lower()
        nlow = nxt.strip().lower()
        if (
            re.match(r"^if\b.+\bthen\s+\S", low)
            and not re.search(r"\bthen$", low)
            and nlow == "else"
        ):
            errors.append(
                f"  L{ln2}: bare 'else' after single-line 'if ... then <stmt>' (L{ln}) -- "
                "OXT binds the else to the inner if; make that if block-form"
            )
    return errors


# LiveCode Script constructs a real OXT engine rejects or mis-runs. Each was
# observed on the engine (tests/lcs, OXT 9.7.1), not inferred.
SCRIPT_FORBIDDEN = (
    (re.compile(r"\bdoes\s+not\s+(contain|begin|end)\b", re.I),
     "'does not contain/begin/end' does not parse in OXT, and the failure is SILENT -- a "
     "script-only stack containing it never opens. Write `not (x contains y)`"),
    (re.compile(r"^\s*repeat\s+with\b.*\bstep\b", re.I),
     "'repeat with ... step N' OVERSHOOTS its end bound in OXT (1 to 9 step 3 visits 10) -- "
     "loop an index without step and compute the value"),
)


def check_script_forbidden(text):
    errors = []
    for lineno, code in logical_lines(text):
        code = re.sub(r'"[^"]*"', '""', code)
        for rx, msg in SCRIPT_FORBIDDEN:
            if rx.search(code):
                errors.append(f"  L{lineno}: {msg}")
    return errors


SCRIPT_KEYWORDS_BEFORE_VALUE = {
    "put", "return", "into", "after", "before", "with", "is", "then", "else", "to",
    "and", "or", "not", "get", "pass", "send", "of", "in", "contains",
}


def check_script_array_literals(text):
    """LiveCode Script has NO ``[...]`` list-literal expression -- that is LiveCode
    BUILDER syntax. In a .livecodescript, a ``[`` that is not an array SUBSCRIPT
    (i.e. not immediately preceded, ignoring spaces, by an identifier char, ``)``
    or ``]``) is an attempt to write an LCB-style list literal, e.g.
    ``oscBuildMessage("/x", [["i", 60]])`` -- which does NOT compile in OXT. Build
    the array by assignment instead (``put "i" into tPair[1]`` ...), or use the
    scAddArg helper in showcontrol-helpers.livecodescript.

    Subscripts (``tArray[1]``, ``tArray["k"]``, ``field 1[2]``) are preceded by an
    identifier / ``)`` / ``]`` and are left alone. Strings and comments are
    stripped first so a literal ``[`` inside a quoted string is ignored."""
    ident = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_)]")
    errors = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        code = strip_comment(raw)
        code = re.sub(r'"[^"]*"', '""', code)   # neutralise string-literal contents
        for i, ch in enumerate(code):
            if ch != "[":
                continue
            j = i - 1
            while j >= 0 and code[j] in " \t":
                j -= 1
            prev = code[j] if j >= 0 else ""
            # The word before the '[' -- a KEYWORD there ("put [..] into",
            # "return [..]") means a literal, not a subscript. (Found by
            # tests/checker_fixtures_test.py: `put [...]` used to slip through
            # because the 't' of "put" looked like an identifier.)
            k = j
            while k >= 0 and code[k] in ident:
                k -= 1
            prev_word = code[k + 1:j + 1].lower()
            if prev not in ident or prev_word in SCRIPT_KEYWORDS_BEFORE_VALUE:
                errors.append(
                    f"  L{lineno}: '[...]' list literal is LiveCode Builder syntax -- LiveCode "
                    "Script has no list-literal expression; build the array by assignment "
                    "(or use scAddArg)"
                )
                break   # one report per line is enough
    return errors


# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------

def lint_file(path, kind):
    """Run the appropriate gates over one file. ``kind`` is 'lcb' or 'script'.
    Returns the list of problem strings (empty == ok)."""
    text = path.read_text(encoding="utf-8")
    problems = []
    problems += check_smart_quotes(text)
    if kind == "lcb":
        problems += check_lcb_structure(text)
        problems += check_lcb_module(text)
        problems += check_lcb_scriptisms(text)
    else:
        problems += check_script_structure(text)
        problems += check_dangling_else(text)
        problems += check_script_array_literals(text)
        problems += check_script_forbidden(text)
    return problems


def run_layer(title, targets, kind):
    """Lint one layer; print a header and per-file ok/FAIL. Returns the failure
    count for that layer."""
    print(f"== {title} ==")
    if not targets:
        print("  (none found -- nothing to check in this layer yet)")
        print()
        return 0
    failures = 0
    for path in targets:
        rel = path.relative_to(ROOT)
        if not path.is_file():
            # Expected-but-absent (e.g. the .lcb bindings not written yet).
            print(f"--    {rel}  (not present yet -- would be checked once it exists)")
            continue
        problems = lint_file(path, kind)
        if problems:
            failures += 1
            print(f"FAIL  {rel}")
            for p in problems:
                print(p)
        else:
            print(f"ok    {rel}")
    print()
    return failures


def main():
    failures = 0
    failures += run_layer("LiveCode Builder (src/<ext>/<ext>.lcb, tests/lcb/*.lcb)", LCB_TARGETS, "lcb")
    failures += run_layer("LiveCodeScript (examples/, tests/lcs/)", SCRIPT_TARGETS, "script")

    if failures:
        print(f"FAILED -- {failures} check(s) need attention.")
        return 1
    print("All ShowControl script gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
