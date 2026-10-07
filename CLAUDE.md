# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**ShowControl** is a set of three small protocol extensions that make OpenXTalk (OXT) /
the xTalk family (also compatible with **LiveCode 9.6.3+**) a credible tool for
interactive installations and live performance:

- **`osc`** - Open Sound Control. A C shim (`src/osc/osc_shim.c`) over **tinyosc** (ISC,
  vendored) bound to xTalk via LCB. Builds/parses OSC messages and bundles; rides the
  script's own UDP sockets for transport.
- **`midi`** - realtime MIDI I/O. A C shim (`src/midi/midi_shim.c`) over **RtMidi**
  (modified-MIT, fetched by CMake) bound to xTalk. Enumerate/open/send, and **poll-drain**
  inbound (no callbacks into script).
- **`artnet`** - Art-Net (DMX512 lighting over UDP). **Pure LCB, no C shim, no native
  binary** - plain byte-packing on port 6454.

`README.md` is the overview; `docs/` holds the architecture, build, getting-started,
API reference, testing guide (`docs/testing.md`) and the Phase-0 FFI record. This file is
the as-built record and the hard-won-lesson list.

```
osc:   tinyosc (vendored, ISC)
         |- C shim       src/osc/osc_shim.c    ->  osc.{so,dll,dylib}   (ABI symbols: osc_*)
              |- LCB binding  src/osc/osc.lcb       (library org.openxtalk.library.osc)
midi:  RtMidi (fetched, MIT)
         |- C shim       src/midi/midi_shim.c  ->  midi.{so,dll,dylib}  (ABI symbols: midi_*)
              |- LCB binding  src/midi/midi.lcb     (library org.openxtalk.library.midi)
artnet: (no upstream, no shim)
              |- LCB binding  src/artnet/artnet.lcb (library org.openxtalk.library.artnet)
```

The native libraries ship **bundled inside each extension** under
`src/<ext>/code/<arch>-<platform>/<ext>.{so,dll,dylib}` (bare token, no `lib` prefix;
platform-ids `x86_64-linux` / `x86-linux` / `x86_64-win32` / `x86-win32` / `universal-mac`,
**architecture FIRST**, Windows `-win32` for both bitnesses). Those are built and tested by CI
and attached to each Release. They are landed ONLY by `.github/workflows/release-binaries.yml`
(manual dispatch: portable lanes for all six platform ids, verified by
`tools/install-release-binaries.py`, then committed) and checked on every push by
`tools/check-binary-freshness.py` (bind oracle, export closure, ABI decoded from machine
code, deps, glibc floor, MANIFEST.sha256). `tools/package-extension.py` stages one build into a
newer build. Installing the packaged extension makes the engine resolve the `c:osc>` / `c:midi>`
bindings via `the revLibraryMapping` automatically. **Art-Net carries no binary** and is exempt
from the build matrix and macOS notarization.

## Commands

Everything is testable headlessly, with no MIDI/DMX/OSC hardware and no display.
Full guide: `docs/testing.md`. The OXT folder (`lc-compile`, `lc-run`, standalone
engine) comes from `--oxt-bin` or `$OXT_BIN` -- any OXT build folder, or an
OXT-Beyond `*-binaries` release archive (CI pins `v0.2.3`).

**Native shims + C tests** (smoke, shared vectors, mock MIDI, the fuzzer):
```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure --no-tests=error
```
CMake vendors tinyosc and fetches RtMidi (pinned `GIT_TAG 6.0.0`); Linux needs
`libasound2-dev`. The test build also emits the **mock** midi library at
`build/mock/midi.*` (never shipped -- see below).

**The LCB bindings, compiled and run** (lc-compile + lc-run, one process per handler):
```sh
python3 tools/run-lcb-tests.py --oxt-bin <oxt-bin> --build-dir build
```
**The LiveCode Script layer in the real engine** (standalone, `-ui`): an in-engine
compile gate over every `.livecodescript`, the Script<->LCB boundary, the helpers,
the shipped self-test, real UDP, interop with the virtual peers:
```sh
python3 tools/run-lcs-tests.py --oxt-bin <oxt-bin> --build-dir build
```
**Wire formats, peers, static gates** (stdlib Python; `pip install python-osc` adds
the third-party cross-check):
```sh
python3 tests/vectors_reference_test.py && python3 tests/simulators_test.py
python3 tests/checker_fixtures_test.py && python3 tools/check-livecodescript.py
python3 tools/gen_vectors.py --check     # after editing tests/vectors/*.json: --write
```

**Iterate the C shims under sanitizers** -- OSC parses untrusted network bytes:
```sh
gcc -std=c17 -Wall -Wextra -fsanitize=address,undefined -D_DEFAULT_SOURCE \
  -Isrc/osc -Isrc/third_party/tinyosc -Itests \
  src/osc/osc_shim.c src/third_party/tinyosc/tinyosc.c tests/fuzz/osc_fuzz.c -lm -o /tmp/f && /tmp/f 500000 1
```
(Use **gcc**, not clang, in a sandbox without clang's ASan runtime; MSVC's
`/fsanitize=address` works on Windows. A fuzz failure: bisect the iteration count,
`OSC_FUZZ_DUMP_AT=N` to dump it, `osc_fuzz --replay FILE` to rerun it.)

**The bar for "done":** a `.lcb` change passes `run-lcb-tests.py`; a Script change
passes `run-lcs-tests.py` (whose compile gate is the real engine compiler); a shim
change passes ctest under ASan/UBSan, plus the fuzzer for anything on the parse
path. When the socket suites cannot run on your engine (old Windows engines), say
so -- CI runs them with `--require-all`. Report what ran, not what should work.

## The decisive design rule: never call LCB from a C callback

All three extensions could push events via callbacks; we deliberately **don't**, because
invoking an LCB handler from a foreign (non-main) thread is fragile and unsupported. Instead:
- **OSC / Art-Net inbound** arrive through LiveCode's own UDP sockets (`on socketReceived`); we
  only convert bytes <-> structured values. No thread, no callback, no queue of our own.
- **MIDI inbound** is drained from RtMidi's internal FIFO by **polling** on a timer
  (`midiPoll`). RtMidi buffers and delta-time-stamps every message, so integrity and timing
  survive jittery poll cadence; only added latency scales with the interval.

This rule is what makes the project low-risk. Keep it. A callback-based MIDI input path is a
deliberate future item, behind the same script API.

## FFI / C-shim conventions (mirrored from Box2Dxt, our prior extension)

- **Handles are positive 32-bit ints** (`0` = null/invalid), stored in a **generation-tagged**
  table and validated before use, so a stale/recycled handle is a **harmless no-op** (getters
  return 0/empty), never a crash. Script never sees a handle - every public LCB handler brackets
  create...use...free internally.
- **Reals cross as `double`, booleans as `int` (0/1).** Exported C ABI symbols keep a **stable
  prefix** (`osc_` / `midi_`) - never rename them; the `.lcb` binding strings reference them.
- **Strings and bytes out of C go through a caller-allocated buffer** (`out`, capacity):
  return the length, or `-needed` when too small (grow once, retry). There is NO safe way
  to hand LCB a `const char*` as a `ZString*` return -- the engine frees it (gotcha 2
  below). This was once believed safe for "module statics the engine copies immediately"
  (the Box2Dxt `dlerror`/`realpath` pattern); `realpath` worked only because it really
  is malloc'd, and the first lc-run of `oscLastError()` crashed.
- **Bytes into C:** `MCDataGetBytePtr(theData)` + `the number of bytes in`; an empty
  buffer as `nothing` through an `optional Pointer` (gotcha 1).
- **Bump the per-shim `*_ABI_VERSION`** (osc is `2`, midi is `2`) on any ABI change; the
  `.lcb` `checkABI()` throws a clear error on skew instead of crashing on first use.
- **Adding a handler:** `<prefix>_*` in the shim (validate inputs) -> `private foreign handler` +
  public wrapper in the `.lcb` -> bump ABI if the ABI changed -> a C assertion AND a
  `tests/lcb` test that calls the public wrapper -> rebuild (CI refreshes the committed
  binary). Keep the shim warning-clean (`-Wall -Wextra`; vendored tinyosc is `-w`).

## ShowControl gotchas (learned the hard way)

1. **A `Data` does NOT bridge to a foreign `Pointer`** (Phase 0 resolved -- the
   first lc-run of the bindings threw "Value is not of correct type"). INPUT
   buffers pass `MCDataGetBytePtr(theData)`; OUTPUT buffers are a reusable
   `MCMemoryAllocate` block (size is `UIntSize`) copied back with
   `MCDataCreateWithBytes`; null only through an `optional Pointer`. Only
   `dataPtr` / `ensureScratch` / `scratchToData` (osc) and the drain/text buffers
   (midi) touch raw pointers.
2. **Never RETURN a `ZString*` from a foreign handler.** The engine wraps it in a
   foreign value whose finalizer `free()`s it (libscript `module-foreign.cpp`
   `__cbuffer_finalize`): a static buffer crashes the VM (`oscLastError()` did), a
   model pointer double-frees. Strings come back through caller buffers
   (bytes-written / `-needed`) decoded with `MCStringDecode`; libc-owned text
   (`dlerror`) is copied with `MCStringCreateWithCString`. The `*_str`/`*_z` C
   accessors remain for C callers only; the static gate refuses the binding.
3. **There is no 64-bit foreign integer type in the target engine (9.6.3).** OSC
   `int64`/`timetag` and bundle timetags cross as **decimal strings**.
4. **tinyosc's `<endian.h>` use needs `-D_DEFAULT_SOURCE`** (CMake sets it).
5. **Never decode through tinyosc's cursor.** It finds the type tag by scanning
   for the first `,` after the address NUL, so a `,` hidden in the address
   PADDING desynchronized it from `validate_message`'s layout -- a remote
   out-of-bounds blob read from one 72-byte datagram (found by the fuzzer). The
   parse path decodes at the offsets the validator proved; keep it that way.
6. **RtMidi returns a wrapper with `ok == false` (NULL inner object) when a backend
   fails** -- guard every call on `->ok`, and never read `->msg` (a
   use-after-free; ASan-confirmed). **And RtMidi only WARNS** for things it cannot
   do: on Windows MM a virtual port "opens" and silently sends nowhere, so the
   shim refuses virtual ports on `RTMIDI_API_WINDOWS_MM` / `DUMMY` itself.
7. **The MIDI drain must never drop a popped message** -- the per-port stash
   (pre-allocated 65535 B) carries an overflow to the next drain.
8. **Drain record format is fixed**: `[2-byte len BE][len bytes][4-byte delta-us BE]`.
   The binding uses `midi_in_drain_bytes` (ABI 2: returns BYTES, so exactly the
   written bytes are copied back) and walks records, never past that length.
9. **Art-Net mixed endianness is THE bug.** OpCode little-endian; ProtVer and
   Length big-endian. Universe split SubUni-then-Net. The vectors pin it.
10. **Build native libraries under the BARE token name** (`osc.so`, `PREFIX ""`).
11. **Reuse persistent buffers in hot paths** (the drain block, the osc scratch).
12. **The mock midi library must never ship.** It is built as `build/mock/midi.*`
    with the same bare name; build.yml's staging excludes `*/mock/*` and
    `tools/package-extension.py` refuses any binary exporting `midimock_*`.
13. **ABI versions:** osc is `2`, midi is `2`. Bump with `checkABI()` together -- and then
    dispatch `release-binaries.yml`: the freshness gate fails until every committed binary
    carries the new ABI (it decodes `<ext>_abi_version()` from each slice's machine code).
14. **Shipped binaries must be portable and closed:** `src/<ext>/<ext>.map` (ELF version
    script; macOS derives its export list from it) exports only `<ext>_*`; RtMidi's
    unconditional `RTMIDI_EXPORT` is stripped in CMake; `SHOWCONTROL_STATIC_RUNTIME` (default
    ON) gives the static MSVC CRT and static libstdc++/libgcc on Linux. The freshness gate
    enforces all of it.

## LiveCodeScript / LCB / OXT gotchas (each observed on a real engine)

1. **No smart quotes**, anywhere -- ASCII `"` and `'` only.
2. **LCB has `and` / `or`** (compiler built-ins, `grammar.g`; commented out in
   `logic.lcb` only because the compiler implements them) and **runtime type tests**
   (`is a number` / `is a string` / `is a data`, `formatted as string`) -- on the
   9.6.3 floor too. An earlier static audit "proved" otherwise from the stdlib
   sources; lc-compile settles such questions now.
3. **LCB has no `div`, `numToByte`, `byteToNum`, `numToChar`** -- LCB uses
   `the byte with code`, `the code of`, `(a - (a mod b)) / b`.
4. **LiveCode Script has no `[...]` literal** and **no `does not contain/begin/end`**.
   A script-only stack that fails to compile fails SILENTLY (it never opens;
   `set the script` leaves `the result` empty) -- `revAvailableHandlers` being empty
   is the reliable signal, which is what the in-engine compile gate uses.
5. **`repeat with i = 1 to 9 step 3` OVERSHOOTS** (visits 10). Loop an index.
6. **`send "msg a b c"` splits parameters on COMMAS**, not spaces -- use
   `dispatch "msg" to tObj with a, b, c`. Inside a `send ... in N ms` message,
   `the target` is the sender itself: remember who to notify.
7. **Datagram sockets:** a message is called once per datagram with
   `(sender "host:port", data, socket)` -- in that order -- and nothing needs
   reading; `write ... to socket "h:p"` sends NOTHING unless `h:p` was opened
   (`open datagram socket to`; the result is "socket is not open"); an accepted
   socket is named by its port alone; broadcast needs `the allowDatagramBroadcasts`.
   To get REPLIES on an opened socket, open it plainly and then
   `read from socket S with message M` -- `open ... with message M` makes M the
   asynchronous open-complete callback, and a write right after it races.
8. **`the revLibraryMapping`:** reading an unmapped key THROWS
   (`EE_BAD_LIBRARY_MAPPING`), the un-keyed property is unreadable -- just set it,
   with **forward slashes** (a backslash path fails to load on Windows).
9. **An empty value into a typed `Integer` parameter throws** at the boundary.
10. **Avoid names that shadow engine tokens**; prefixes `t` local, `p` param, `s`
    script-local, `k` constant; public `oscPascalCase`, C `osc_snake_case`.
11. **Constants are literal and declared before first use** (lexical resolution).
12. **`unsafe ... end unsafe` around every foreign call**; declarations at the top.
13. **The engine is MAX_PATH-bound on Windows** -- the LCS runner stages scripts
    into its short work dir; keep `--build-dir` short on deep checkouts.
14. **Windows engines < OXT-Beyond 0.2.1-rc.2 deliver no socket events with `-ui`**
    (and older stdout redirection was lost). Socket suites need a newer engine.

## Conventions

- Units/types across the FFI: reals `double`, booleans `int` (0/1), handles positive `int`
  (0 invalid, opaque), byte buffers `Pointer`+`CInt` length, short strings `ZStringUTF8`,
  64-bit ints decimal strings.
- **Match the surrounding style** - this codebase comments the *why*, densely; mirror that.

## Git / workflow

- Develop on the per-task branch (e.g. `claude/...`); commit there, open a **draft PR** if none
  exists. Don't push to `main` without explicit permission.
- "Done" is the bar in **Commands** above: the headless suite for the layer you
  touched passes (and ASan/UBSan for shims). For an ABI change, bump
  `*_ABI_VERSION` + `checkABI()` together and expect the CI refresh PR to rebuild
  the committed `code/` binaries.
- New behaviour gets a test in the matching suite (`tests/lcb`, `tests/lcs`, the
  vectors, or a C test); a bug fix gets the test that would have caught it.
