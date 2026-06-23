#!/usr/bin/env python3
"""Static gates for the ShowControl script layers (.lcb and .livecodescript).

OXT / LiveCode is a GUI runtime: there is no headless way to compile or run the
extension sources in CI or from an agent's sandbox. This script catches the
mistakes that are statically catchable *before* a human compiles in OXT, bundled
into one command.

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
LCB_TARGETS = sorted(set(LCB_EXPECTED) | set((ROOT / "src").glob("*/*.lcb")))

# The example stacks, in LiveCodeScript. The directory may be empty for now.
SCRIPT_TARGETS = sorted((ROOT / "examples").glob("*.livecodescript"))

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
    else:
        problems += check_script_structure(text)
        problems += check_dangling_else(text)
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
    failures += run_layer("LiveCode Builder bindings (src/<ext>/<ext>.lcb)", LCB_TARGETS, "lcb")
    failures += run_layer("LiveCodeScript examples (examples/*.livecodescript)", SCRIPT_TARGETS, "script")

    if failures:
        print(f"FAILED -- {failures} check(s) need attention.")
        return 1
    print("All ShowControl script gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
