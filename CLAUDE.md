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

`README.md` is the authoritative project plan; `docs/` holds the architecture, build,
getting-started, API reference, and the Phase-0 FFI spike. This file is the as-built record
and the hard-won-lesson list.

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
and attached to each Release; `tools/package-extension.py` refreshes the committed tree from a
newer build. Installing the packaged extension makes the engine resolve the `c:osc>` / `c:midi>`
bindings via `the revLibraryMapping` automatically. **Art-Net carries no binary** and is exempt
from the build matrix and macOS notarization.

## Commands

**Native shims + C tests** (the only layer with an automated test suite):
```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure        # osc_smoke_test.c + midi_smoke_test.c
```
CMake vendors tinyosc (in-tree) and fetches RtMidi (pinned `GIT_TAG 6.0.0`). Linux MIDI needs
ALSA dev headers (`libasound2-dev` / `alsa-lib-devel`). See `docs/building.md`.

**Always build the C shims under sanitizers while iterating** - OSC parses untrusted network
bytes, and the safety guarantees are the whole point:
```sh
gcc -std=c17 -Wall -Wextra -fsanitize=address,undefined -D_DEFAULT_SOURCE \
  -Isrc/osc -Isrc/third_party/tinyosc \
  src/osc/osc_shim.c src/third_party/tinyosc/tinyosc.c tests/osc_smoke_test.c -lm -o /tmp/osc && /tmp/osc
```
(Use **gcc**, not clang, in this environment - clang's ASan runtime is not installed here.)

**Art-Net wire format** (pure-Python reference + golden packets, runs anywhere):
```sh
python3 tests/artnet_golden_test.py
```

**Static gates for the script layer.** OXT is a GUI runtime - there is **no headless way to
compile or run `.lcb` / `.livecodescript` here**. Catch what is statically catchable first:
```sh
python3 tools/check-livecodescript.py
```
It checks every `.lcb` and example for smart/curly quotes, handler balance (`handler`/`end
handler`), control-structure balance (`if`/`repeat`/`unsafe`), and the LiveCodeScript gates for
examples. **Do not claim runtime behavior you cannot observe** - say "verified statically; needs
an OXT pass" and let the user confirm.

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
- **Never return a library-owned `const char*` whose lifetime you don't control** (a known
  engine-crash footgun). Two safe shapes, both used here: (a) fill a **caller-allocated buffer**
  (`out`, capacity) and return the length, or `-needed` when too small; (b) return a `const
  char*` into memory with an **explicit, documented lifetime** (a parsed-model field valid until
  `*_free`, or a module static the engine copies immediately - the proven Box2Dxt
  `dlerror`/`realpath` pattern). The `_str`/`_z` accessors are case (b).
- **Bump the per-shim `*_ABI_VERSION`** (currently `1` for both) on any ABI change; the `.lcb`
  `checkABI()` throws a clear error on skew instead of crashing on first use.
- **Adding a handler:** `<prefix>_*` in the shim (validate inputs) -> `private foreign handler` +
  public wrapper in the `.lcb` -> bump ABI if the ABI changed -> add a smoke-test assertion ->
  rebuild + `tools/package-extension.py` to refresh the committed binary. Keep the shim
  warning-clean (`-Wall -Wextra`; our code is warning-free, vendored tinyosc is `-w`).

## ShowControl gotchas (learned the hard way)

1. **The byte-buffer FFI marshalling is the one Phase-0 unknown - confirm it in OXT.** Box2Dxt
   was scalars + handles only and never crossed a `Data`, so it gives **no precedent** here. The
   plan (per the LCB language reference + community): an inbound `Data` passed to a foreign
   `Pointer` parameter bridges to a pointer to its first byte; the out direction **pre-sizes a
   `Data`** (append `numToByte(0)`) and passes it as `Pointer` for the shim to fill in place,
   then reads back `byte 1 to len`. This is isolated in three LCB helpers (`makeBuffer`, the
   `*_finish` readback, `midiPoll`'s drain). See `docs/phase0-ffi-spike.md` - run the spike
   before trusting the rest. The C shim API is stable regardless of how it resolves.
2. **There is no 64-bit foreign integer type in the target engine (9.6.3).** OSC `int64` ('h'),
   `timetag` ('t'), and bundle timetags therefore cross the FFI as **decimal strings**
   (`osc_*_str` / `osc_*_z` entry points). Script numbers are doubles anyway, so a 64-bit int is
   naturally a string there. Never add a `CLongLong` foreign param expecting it to work on 9.6.3.
3. **tinyosc's `<endian.h>` use needs `-D_DEFAULT_SOURCE`** (glibc only exposes
   `htobe64`/`be64toh` under that feature-test macro). Without it the build dies with "implicit
   declaration of htobe64". CMake sets it on the `osc` target; set it on any manual tinyosc
   compile too.
4. **tinyosc's cursor reader (`tosc_getNext*`) does NOT bounds-check.** Untrusted network bytes
   must pass `validate_message()` (a single bounds-safe pre-scan) **before** any tinyosc getter
   touches them. This is exactly the "pre-scan into an indexed model" the README calls for, and
   it is what keeps the fuzz/ASan suite green. Never feed tinyosc unvalidated input.
5. **RtMidi returns a non-NULL wrapper with `ok == false` (and a NULL internal object) when a
   backend fails to initialize.** Calling any `rtmidi_*` function that dereferences the wrapped
   object then segfaults. **Guard every call on `wrapper->ok`** (ASan caught this as a real
   null-deref in `port_name` on a headless box with no `/dev/snd`). The shim now checks `->ok`
   before touching the object everywhere.
6. **The MIDI drain must never drop a popped message.** `rtmidi_in_get_message` is destructive
   (it pops). If the caller buffer is full, the shim **stashes** the popped message per-port and
   emits it on the next drain - it does not re-pop and lose it. Honor "we never drop messages."
7. **Drain record format is fixed**: `[2-byte len BE][len bytes][4-byte delta-microseconds BE]`,
   records concatenated, count returned. The 2-byte length carries SysEx up to 65535 bytes. The
   `.lcb` walks it with `byteToNum` arithmetic; `midiPoll` converts delta us -> seconds for the
   script-facing `delta`. README 6.3 shows seconds; the *wire* is microseconds - don't "fix" one
   to match the other.
8. **Art-Net mixed endianness is THE bug.** OpCode is **little-endian** (`0x5000` -> `00 50`);
   protocol-version and Length are **big-endian** (`14` -> `00 0E`; `512` -> `02 00`). Reverse a
   big-endian field and a node silently ignores the packet. `tests/artnet_golden_test.py` pins
   the exact bytes (and Wireshark `udp.port == 6454` confirms on real hardware).
9. **The Art-Net universe is split SubUni-then-Net.** 15-bit port-address: byte SubUni =
   `universe mod 256`, byte Net = `(universe div 256) mod 128`; reconstruct as `net*256 + sub`.
10. **Build native libraries under the BARE token name** (`osc.so`, not `libosc.so`) so the file
    matches the `c:osc>` binding. CMake sets `PREFIX ""` + `OUTPUT_NAME osc`. The bundled copy
    under `code/<platform-id>/` must be the same bare token.
11. **Reuse a persistent drain buffer in the MIDI poll hot path.** Rebuilding an N-byte `Data`
    with `numToByte` every poll (every few ms) is O(N) interpreter work; `midi.lcb` allocates
    `sDrain` once (`kDrainCap`) and reuses it. (Box2Dxt's single-threaded performance playbook:
    interpreter ops and per-frame allocation are the real costs.)

## LiveCodeScript / LCB / OXT gotchas (carried from Box2Dxt; OXT is stricter than LiveCode)

1. **No smart quotes.** Curly `" " ' '` (U+201C/201D/2018/2019) anywhere - even in a comment or
   string - fail to compile in OXT. ASCII `"` and `'` only. The static checker enforces zero.
2. **Avoid names whose stem shadows an engine token** even when prefixed. Prefer distinctive,
   multi-word stems.
3. **Prefix conventions:** `t` handler-local, `p` parameter, `s` script/module-local, `k`
   constant. Public API: `oscPascalCase` / `midiPascalCase` / `artnetPascalCase`;
   C ABI `osc_snake_case` / `midi_snake_case`.
4. **Constants must be literal** and declared **before first use** (OXT resolves them by lexical
   position - a forward reference silently evaluates to nothing).
5. **`unsafe ... end unsafe` brackets every foreign call** in LCB; keep declarations at the top
   of a handler.
6. **Commands report via `the result`; functions return a value.** Match the README's API shapes.

## Conventions

- Units/types across the FFI: reals `double`, booleans `int` (0/1), handles positive `int`
  (0 invalid, opaque), byte buffers `Pointer`+`CInt` length, short strings `ZStringUTF8`,
  64-bit ints decimal strings.
- **Match the surrounding style** - this codebase comments the *why*, densely; mirror that.

## Git / workflow

- Develop on the per-task branch (e.g. `claude/...`); commit there, open a **draft PR** if none
  exists. Don't push to `main` without explicit permission.
- A `.lcb` change is only "done" once `tools/check-livecodescript.py` passes; a shim change is
  only "done" once the matching smoke test passes under ASan/UBSan and (for an ABI change) the
  `*_ABI_VERSION` + `checkABI()` are bumped together.
