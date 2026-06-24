# ShowControl

**OSC, MIDI, and Art-Net for OpenXTalk** — three small protocol extensions that
make [OpenXTalk](https://openxtalk.org) (OXT) and the xTalk family (also
compatible with **LiveCode 9.6.3+**) a credible tool for interactive
installations and live performance.

Build a custom control surface or a responsive installation in an afternoon, talk
**OSC** to TouchOSC / Max / Resolume, **MIDI** to your controllers and DAW, and
**Art-Net** (DMX512) to your lighting rig — then ship it as a single cross-platform
standalone. No incumbent (Max/MSP, TouchDesigner, Isadora, QLab, Chataigne) owns
that combination of *rapid UI + protocol I/O + easy deployment*; that gap is the
whole point of this project. The longer strategic case lives in
[`docs/project-plan.md`](docs/project-plan.md).

| Extension | Protocol | Implementation | Native binary? | Transport |
|-----------|----------|----------------|----------------|-----------|
| **`osc`** | [Open Sound Control](https://opensoundcontrol.stanford.edu/) | C shim over **tinyosc** (ISC, vendored) bound to LCB | yes (bundled) | the engine's own UDP sockets |
| **`midi`** | realtime MIDI I/O | C shim over **RtMidi** (modified MIT, fetched) bound to LCB | yes (bundled) | RtMidi backends (CoreMIDI / ALSA / WinMM) |
| **`artnet`** | [Art-Net](https://art-net.org.uk/) (DMX512 over UDP) | **pure LCB** — no C, no library | **none** | the engine's own UDP sockets |

The three extensions are **independent**: install any one without the others.

---

## Contents

- [Status & maturity](#status--maturity)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [The API at a glance](#the-api-at-a-glance)
- [Worked examples](#worked-examples)
- [Architecture in brief](#architecture-in-brief)
- [Building from source](#building-from-source)
- [Testing & verification](#testing--verification)
- [Design rules that keep this safe](#design-rules-that-keep-this-safe)
- [Roadmap](#roadmap)
- [Licensing](#licensing)
- [Documentation](#documentation)

---

## Status & maturity

ShowControl is a **pre-release v1 foundation**. Here is the honest state of each
layer, because "what is actually verified" matters more than a version number:

| Layer | What it is | Verification state |
|-------|------------|--------------------|
| **C shims** (`osc`, `midi`) | the locked C ABI over tinyosc / RtMidi | **Verified.** The OSC shim passes 61 assertions under **AddressSanitizer + UndefinedBehaviorSanitizer + float-cast-overflow** (`-fno-sanitize-recover=all`), including malformed-datagram fuzzing. The MIDI shim's drain/stash/port-name logic is exercised the same way. CI builds and tests on Linux, macOS (universal), and Windows (x64 + x86). |
| **Art-Net wire format** | the ArtDmx / ArtPoll byte layout | **Verified.** 18 byte-exact golden-packet assertions pin the mixed-endianness layout (`tests/artnet_golden_test.py`). |
| **LCB bindings** (`.lcb`) | the script-facing `library` wrappers | **Statically gated, runtime-pending.** OXT is a GUI runtime with no headless compiler, so the bindings are checked by `tools/check-livecodescript.py` (smart quotes, handler/control balance, module terminator, LiveCode-Script-isms) and audited by hand. They still need an **OXT runtime pass** — see the [Phase-0 FFI spike](docs/phase0-ffi-spike.md). |
| **Hardware interop** | TouchOSC, DAWs, DMX nodes | **Not yet run.** No external hardware available at the time of writing; real-device testing ([`docs/project-plan.md` §10.2](docs/project-plan.md)) is the next gate. |

**Bottom line:** the native, memory-safety-critical core is proven under
sanitizers; the script bindings compile-gate clean and have been audited but want
a real OXT compile-and-run; no hardware has been driven yet. Treat it as a
solid, reviewable foundation ready for that first OXT pass — not as battle-tested
production software.

> The one empirical unknown for the OXT pass is how a LiveCode `Data` crosses the
> FFI to a foreign `Pointer`. It is isolated to a handful of LCB helpers and has a
> documented fallback (hex-over-string). Run [`docs/phase0-ffi-spike.md`](docs/phase0-ffi-spike.md)
> **first**; the C ABI is stable regardless of how it resolves.

## Repository layout

```
ShowControl/
├── src/
│   ├── osc/
│   │   ├── osc.lcb               LCB binding  (library org.openxtalk.library.osc)
│   │   ├── osc_shim.c/.h         C shim ABI   (osc_* symbols)  -> osc.{so,dll,dylib}
│   │   └── code/<arch>-<plat>/   bundled native libs (committed, per platform)
│   ├── midi/
│   │   ├── midi.lcb              LCB binding  (library org.openxtalk.library.midi)
│   │   ├── midi_shim.c/.h        C shim ABI   (midi_* symbols) -> midi.{so,dll,dylib}
│   │   └── code/<arch>-<plat>/   bundled native libs (committed, per platform)
│   ├── artnet/
│   │   └── artnet.lcb            pure-LCB binding (library org.openxtalk.library.artnet)
│   └── third_party/tinyosc/      vendored tinyosc (ISC) — built into the osc lib
├── tests/
│   ├── osc_smoke_test.c          round-trip + fuzz, runs under ASan/UBSan
│   ├── midi_smoke_test.c         enumerate/open/drain/handle-safety, headless-safe
│   └── artnet_golden_test.py     byte-exact golden packets (the endianness spec)
├── tools/
│   ├── check-livecodescript.py   static gate for .lcb + .livecodescript
│   └── package-extension.py      refresh the committed code/<plat>/ trees
├── examples/                     LiveCode Script helpers + a wired-together demo
├── docs/                         architecture, building, getting-started, api-reference,
│                                 phase0-ffi-spike, project-plan
├── CMakeLists.txt                builds the osc + midi native libraries
└── .github/workflows/build.yml   static + golden + ASan/UBSan gate, then the build matrix
```

The native libraries ship **bundled inside each extension** at
`src/<ext>/code/<arch>-<platform>/<ext>.{so,dll,dylib}` (bare token name, no `lib`
prefix; platform-ids `x86_64-linux`, `x86-linux`, `x86_64-win32`, `x86-win32`,
`universal-mac` — **architecture first**, Windows `-win32` for both bitnesses).
Installing the packaged extension makes the engine resolve the `c:osc>` / `c:midi>`
bindings automatically via `the revLibraryMapping`. **Art-Net carries no binary.**

## Quick start

You don't need a C toolchain to *use* ShowControl — the native libraries are
committed inside each extension. In **OXT** (or LiveCode 9.6.3+):

1. **Tools → Extension Builder**, open the extension's `.lcb` (`src/osc/osc.lcb`,
   `src/midi/midi.lcb`, and/or `src/artnet/artnet.lcb`).
2. **Package** to produce the `.lce` (for `osc`/`midi` this rolls in the
   per-platform `code/` libraries), then install it via **Tools → Extension
   Manager**. Or click **Test** to compile-and-load in place.
3. Sanity-check in the Message Box:

   ```
   put oscLastError()    -- empty string  => osc loaded
   put midiInputPorts()  -- a (possibly empty) list of port names => midi loaded
   put artnetLastError() -- empty string  => artnet loaded
   ```

The engine loads the right native library for your platform automatically — **no
`/usr/lib`, no `sudo`, no `LD_LIBRARY_PATH`, no renaming.** Full walkthrough:
[`docs/getting-started.md`](docs/getting-started.md).

## The API at a glance

Every public handler joins the LiveCode Script message path, so you call them like
built-in commands/functions. Full signatures, the `oscParse` Array shape, and the
`midiPoll` record shape are in [`docs/api-reference.md`](docs/api-reference.md).

**OSC** — build/parse messages and bundles into `Data`; you own the socket.

```
oscBuildMessage(pAddress, pArgs)   -> Data     -- pArgs = list of [type, value] pairs
oscBuildBundle(pTimetag, pMessages)-> Data
oscParse(pData)                    -> Array    -- bounds-checked; handles bundles (incl. nested)
oscMatch(pPattern, pAddress)       -> Boolean  -- OSC 1.0 wildcards  ? * [ ] { }
oscLastError()                     -> String
```

**MIDI** — enumerate/open/close ports, send, and **poll** to receive (no callbacks).

```
midiInputPorts() / midiOutputPorts()           -> List
midiOpenInput(i) / midiOpenOutput(i)           -> Integer handle (0 = failure)
midiOpenVirtualInput(name) / ...Output(name)   -> Integer       (macOS / Linux)
midiClose(h) ;  midiIgnoreTypes(h, sysex, time, sense)
midiSend(h, pBytes)                                              -- raw Data, e.g. SysEx
midiNoteOn / midiNoteOff / midiControlChange / midiProgramChange
midiPitchBend / midiChannelPressure                             -- channels are 1-based
midiPoll(h)                                    -> List of decoded event records
midiDecode(pBytes)                             -> one decoded record (also handy for HW-free tests)
midiLastError()                                -> String
```

**Art-Net** — build/parse DMX and discovery packets into `Data`; you own the socket.

```
artnetBuildDmx(pUniverse, pChannels)  -> Data   -- up to 512 channel bytes
artnetParseDmx(pData)                 -> Array
artnetBuildPoll()                     -> Data   -- broadcast for node discovery
artnetParseReply(pData)               -> Array
artnetLastError()                     -> String
```

A few helper verbs (the MIDI poll dispatcher, `artnetSendDmx` build-plus-write)
ship as LiveCode Script in
[`examples/showcontrol-helpers.livecodescript`](examples/showcontrol-helpers.livecodescript).

## Worked examples

**OSC — receive a TouchOSC fader, send to Resolume:**

```
on socketReceived pData, pHost
   put oscParse(pData) into tMsg
   if tMsg["address"] is "/1/fader1" then
      set the thumbPosition of scrollbar "Volume" to (tMsg["args"][1]) * 100
   end if
   read from socket pHost for 8192
end socketReceived

on mouseUp
   local tArgs                          -- build args by assignment: xTalk has no [...] literal
   scAddArg tArgs, "f", 0.75
   write oscBuildMessage("/composition/layers/1/video/opacity/values", tArgs) \
        to socket "127.0.0.1:7000"
end mouseUp
```

**MIDI — poll an input and react to a knob:**

```
on midiPollLoop
   repeat for each element tEvent in midiPoll(sMidiIn)
      if tEvent["kind"] is "controlChange" and tEvent["data1"] is 7 then
         set the thumbPosition of scrollbar "Master" to tEvent["data2"]
      end if
   end repeat
   send "midiPollLoop" to me in 3 milliseconds   -- ~3 ms added input latency
end midiPollLoop
```

**Art-Net — drive a dimmer on universe 0:**

```
on faderChanged
   put numToByte(the thumbPosition of me) into tChannels   -- channel 1
   write artnetBuildDmx(0, tChannels) to socket "2.0.0.10:6454"
end faderChanged
```

A single demo that wires all three together (a MIDI knob → on-screen fader → OSC
out **and** Art-Net dimmer) is in
[`examples/midi-osc-artnet-demo.livecodescript`](examples/midi-osc-artnet-demo.livecodescript).

## Architecture in brief

Each native extension is three layers; Art-Net is the bottom two collapsed into
pure LCB. The boundary at the bottom is a locked, flat C ABI.

```
  your xTalk script  (Data, Arrays, Lists)
        |  oscBuildMessage / midiPoll / artnetBuildDmx
  src/<ext>/<ext>.lcb        LCB library: foreign handlers + public wrappers; hides handles
        |  FFI:  c:osc>osc_*   c:midi>midi_*   (ints, doubles, pointers)
  src/<ext>/<ext>_shim.c + upstream   one flat C library per ext (osc.* / midi.*)
                                      — artnet has NO C layer at all
```

**The one rule that makes this low-risk: never call an LCB handler from a C
callback.** Invoking script from a foreign (non-main) thread is fragile and
unsupported, so every inbound path avoids it:

- **OSC / Art-Net inbound** ride the engine's own UDP sockets — datagrams arrive in
  a normal `on socketReceived`; the extension only converts bytes ⇄ structured
  values. No thread, no callback, no queue of our own.
- **MIDI inbound** is drained from RtMidi's internal FIFO by **polling** on a timer
  (`midiPoll`). RtMidi buffers and delta-time-stamps every message, so integrity
  and timing survive a jittery poll cadence — *only added latency scales with the
  interval*, which makes the poll interval a tunable latency knob, not a
  correctness one.

The full rationale (why each shim exists, how handles and byte buffers cross the
FFI, the drain record format) is in [`docs/architecture.md`](docs/architecture.md).

## Building from source

You only build if you want **fresh** native libraries (the committed ones already
work). Art-Net has nothing to build.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure
```

CMake **vendors** tinyosc in-tree and **fetches** RtMidi (pinned `GIT_TAG 6.0.0`,
so the first configure needs network access). Linux MIDI needs ALSA dev headers
(`libasound2-dev` / `alsa-lib-devel`). Refresh the committed per-platform trees
from a newer build with `tools/package-extension.py`. Full details — platform
matrix, macOS signing/notarization, the `.def` note for 32-bit Windows — are in
[`docs/building.md`](docs/building.md).

## Testing & verification

Because OXT can't compile or run `.lcb` headlessly, ShowControl pushes correctness
into the layers that *can* be tested automatically, and gates the rest statically:

```sh
# C shims + runtime smoke tests (the only automated runtime suite)
ctest --test-dir build --output-on-failure

# Always iterate the OSC shim under sanitizers — it parses untrusted network bytes
gcc -std=c17 -Wall -Wextra -fsanitize=address,undefined,float-cast-overflow \
  -fno-sanitize-recover=all -D_DEFAULT_SOURCE \
  -Isrc/osc -Isrc/third_party/tinyosc \
  src/osc/osc_shim.c src/third_party/tinyosc/tinyosc.c tests/osc_smoke_test.c -lm -o /tmp/osc && /tmp/osc

# Art-Net wire format (pure-Python reference + golden packets, runs anywhere)
python3 tests/artnet_golden_test.py

# Static gate for the script layer (.lcb + .livecodescript)
python3 tools/check-livecodescript.py
```

The OSC fuzz/round-trip suite, the Art-Net golden packets, and the ASan/UBSan gate
run in CI on every push and PR ([`.github/workflows/build.yml`](.github/workflows/build.yml)),
across Linux / macOS-universal / Windows. The sanitizer gate uses
`-fno-sanitize-recover=all` so *any* memory or UB error fails the build instead of
only printing — including `float-cast-overflow`, which is **not** in the default
`undefined` group but is reachable from a hostile OSC float argument.

**Testing inside OXT — no hardware needed.** Loop each extension's output back
through its own input to validate the whole stack with no controllers, DAWs, or DMX
nodes. [`examples/selftest.livecodescript`](examples/selftest.livecodescript) is an
automated pass/fail run across all three (build→parse round-trips, `oscMatch`,
Art-Net golden bytes, the MIDI decoder, UDP loopback, port lifecycle);
[`examples/loopback-monitor.livecodescript`](examples/loopback-monitor.livecodescript)
is an interactive visual monitor whose "virtual DMX rig" lights up from looped-back
Art-Net. Tier 1 of the self-test also doubles as the [Phase-0 FFI confirmation](docs/phase0-ffi-spike.md).
Full how-to (including software MIDI loopback for a complete MIDI round-trip):
[`docs/testing-in-oxt.md`](docs/testing-in-oxt.md).

## Design rules that keep this safe

These are non-negotiable invariants; full list in
[`CLAUDE.md`](CLAUDE.md) and [`docs/architecture.md`](docs/architecture.md):

- **Untrusted bytes are validated before tinyosc ever touches them.** tinyosc's
  cursor reader is *not* bounds-checked, so the shim pre-scans every datagram with
  a single bounds-safe pass; a malformed packet is a clean error, never an
  out-of-bounds read. Overflow-safe comparisons (`elen > len - p`, not `p + elen >
  len`) and a bundle-nesting depth cap close the obvious DoS / OOB vectors.
- **Handles are generation-tagged 32-bit ints**, validated before use, so a
  stale/recycled handle is a harmless no-op (getters return 0/empty) — never a
  crash. Script never sees a raw pointer.
- **Never return a library-owned `const char*` of unknown lifetime** (a known
  engine-crash footgun): results fill caller buffers, or are returned with an
  explicit, documented lifetime.
- **Never call LCB from a C callback** (see architecture, above).
- **No 64-bit foreign int in the target engine:** OSC int64 / timetags cross the
  FFI as decimal **strings**.
- **Art-Net mixed endianness is the one wire-format trap:** OpCode is
  little-endian; protocol-version and Length are big-endian. Golden packets pin it.

## Roadmap

The near-term path is: run the [Phase-0 FFI spike](docs/phase0-ffi-spike.md) in
OXT → drive real hardware (TouchOSC, a DAW, a DMX node/QLC+, Wireshark on
`udp.port == 6454`) → macOS signing/notarization of the `osc`/`midi` dylibs →
the showcase demos. Beyond v1: **sACN (E1.31)** as the natural lighting follow-on,
Ableton Link, MIDI clock/MTC/MMC, a callback-based MIDI input path for
tight-instrument latency, and OSC-over-TCP. The full sequenced list and the
market/positioning rationale are in
[`docs/project-plan.md` §16](docs/project-plan.md) and
[`CHANGELOG.md`](CHANGELOG.md) (known follow-ups).

## Licensing

ShowControl is **MIT-licensed** (see [`LICENSE`](LICENSE)). It builds only on
permissively-licensed components — no GPL/LGPL anywhere:

- **tinyosc** — ISC (vendored in `src/third_party/tinyosc/`).
- **RtMidi** — modified MIT (fetched at build time, statically linked into `midi`).
- **Art-Net** — a royalty-free protocol with an openly published spec; there is no
  library to bundle. *"Art-Net" is a trademark of Artistic Licence;* this
  implements the protocol and is described as **"Art-Net compatible"** — no
  endorsement implied.

Details and notice-retention requirements: [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

## Documentation

| Doc | What's in it |
|-----|--------------|
| [`docs/getting-started.md`](docs/getting-started.md) | Install, sanity-check, one runnable walkthrough per protocol, troubleshooting. |
| [`docs/api-reference.md`](docs/api-reference.md) | Every public handler, the `oscParse` Array and `midiPoll` record shapes, failure behavior, units. |
| [`docs/architecture.md`](docs/architecture.md) | The three layers, why the shims exist, the no-callback rule, sockets-vs-polling, FFI marshalling, the ABI. |
| [`docs/building.md`](docs/building.md) | Build the native libraries, run the C tests, package each extension, the platform/CPU/signing matrix. |
| [`docs/testing-in-oxt.md`](docs/testing-in-oxt.md) | Validate the whole stack inside OXT with **no hardware** — the automated self-test, the visual loopback monitor, and software MIDI loopback. |
| [`docs/phase0-ffi-spike.md`](docs/phase0-ffi-spike.md) | The one empirical unknown for the OXT pass: `Data` ⇄ pointer marshalling, with a hex-transport fallback. |
| [`docs/project-plan.md`](docs/project-plan.md) | The original strategy: target users, competitive positioning, showcase demos, milestones, risk register, roadmap. |
| [`CLAUDE.md`](CLAUDE.md) | The as-built record and the hard-won-lesson list (FFI conventions, per-extension gotchas). |
| [`CHANGELOG.md`](CHANGELOG.md) | Per-extension changes, the pre-OXT hardening pass, and the known follow-ups. |
