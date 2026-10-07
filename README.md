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
| **C shims** (`osc`, `midi`) | the locked C ABI over tinyosc / RtMidi | **Verified.** Smoke, mock-MIDI and shared-vector suites under **ASan + UBSan + float-cast-overflow** (`-fno-sanitize-recover=all`); `osc_parse` is **fuzzed** (a deterministic mutation fuzzer in ctest, libFuzzer in CI). CI builds on Linux, macOS (universal) and Windows (x64 + x86). |
| **Wire formats** | OSC / Art-Net / MIDI bytes | **Verified** against shared vectors ([`tests/vectors/`](tests/vectors)) by three independent implementations: clean-room spec codecs + the third-party **python-osc**, the C shim, and the LCB bindings. |
| **LCB bindings** (`.lcb`) | the script-facing `library` wrappers | **Compiled and run in CI** with `lc-compile` + `lc-run` on Linux, Windows and macOS — every public handler, the FFI marshalling, MIDI end to end through a mock RtMidi library ([`docs/testing.md`](docs/testing.md)). |
| **LiveCode Script layer** | the helpers, examples, Script↔LCB boundary | **Run in a real engine in CI** (standalone, `-ui`): an in-engine compile gate over every script, the boundary, the MIDI dispatcher, the shipped self-test, real UDP through the engine's sockets, and interop with independent virtual peers. |
| **Hardware interop** | TouchOSC, DAWs, DMX nodes | **Not yet run on real gear.** Interop with spec-derived virtual peers ([`tools/sim/`](tools/sim)) is automated; a Wireshark check on the first real rig ([`docs/project-plan.md` §10.2](docs/project-plan.md)) is the remaining gate. |

**Bottom line:** every layer now runs automatically with no hardware, and the
first full runs found and fixed real bugs (a remote OSC out-of-bounds read, the
`Data`→`Pointer` FFI marshalling, engine-freed string returns, a mangling MIDI
dispatcher, wrong UDP callback/send patterns — see the [CHANGELOG](CHANGELOG.md)).
What remains is driving specific third-party gear.

> The Phase-0 FFI unknown is **resolved**: a `Data` does *not* bridge to a foreign
> `Pointer`. The bindings use the engine's `MCDataGetBytePtr` / `MCMemoryAllocate`
> / `MCDataCreateWithBytes` instead ([`docs/phase0-ffi-spike.md`](docs/phase0-ffi-spike.md)).

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
│   ├── osc_smoke_test.c          round-trip + malformed input, runs under ASan/UBSan
│   ├── osc_vectors_test.c        the shared vectors through the C shim
│   ├── midi_smoke_test.c         enumerate/open/drain/handle-safety, headless-safe
│   ├── midi_mock_smoke_test.c    drain/stash/loopback against the RtMidi mock
│   ├── mock/                     rtmidi_mock.c: the RtMidi test double (+ build/mock/midi)
│   ├── fuzz/osc_fuzz.c           mutation fuzzer (ctest) / libFuzzer entry (CI)
│   ├── vectors/                  ONE source of truth for the wire formats (+ generated vectors.h)
│   ├── lcb/                      LCB test modules, run under lc-run
│   ├── lcs/                      LiveCode Script test stacks + runner, run in the engine (-ui)
│   └── *_test.py                 vectors reference, Art-Net goldens, peers, checker fixtures
├── tools/
│   ├── run-lcb-tests.py          compile + run the LCB suites headlessly (lc-compile / lc-run)
│   ├── run-lcs-tests.py          run the Script suites in the standalone engine (-ui)
│   ├── gen_vectors.py            vectors -> vectors.h / the LCB vectors module / a fuzz corpus
│   ├── sim/                      virtual Art-Net node + OSC peer (clean-room codecs)
│   ├── check-livecodescript.py   static gate for .lcb + .livecodescript
│   └── package-extension.py      refresh the committed code/<plat>/ trees
├── examples/                     LiveCode Script helpers + a wired-together demo
├── docs/                         architecture, building, getting-started, api-reference,
│                                 testing, testing-in-oxt, phase0-ffi-spike, project-plan
├── CMakeLists.txt                builds the osc + midi native libraries (+ tests, mock, fuzzer)
└── .github/workflows/            build.yml (shims, ASan/UBSan, release) + test.yml (headless suites)
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
oscBuildMessage(pAddress, pArgs)   -> Data     -- pArgs = flat list: type, value, ... (use scAddArg)
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
on openCard
   accept datagram connections on port 9000 with message "oscArrived"
end openCard

-- One call per datagram: (sender "host:port", data). No read needed.
on oscArrived pSender, pData
   put oscParse(pData) into tMsg
   if tMsg["address"] is "/1/fader1" then
      set the thumbPosition of scrollbar "Volume" to (tMsg["args"][1]) * 100
   end if
end oscArrived

on mouseUp
   local tArgs                          -- build args by assignment: xTalk has no [...] literal
   scAddArg tArgs, "f", 0.75
   -- the engine only writes to an OPEN socket (scOscSend does this for you)
   if "127.0.0.1:7000" is not among the lines of the openSockets then
      open datagram socket to "127.0.0.1:7000"
   end if
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
   scArtnetSend "2.0.0.10", 0, tChannels   -- opens the socket once, then writes
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

- **OSC / Art-Net inbound** ride the engine's own UDP sockets — each datagram arrives
  as a normal message (`on oscArrived pSender, pData`); the extension only converts bytes ⇄ structured
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

Everything is tested automatically, **with no MIDI, DMX or OSC hardware and no
display** — the C shims, the LCB bindings (compiled and run with `lc-compile` /
`lc-run`), the LiveCode Script layer in a real engine (`-ui`), real UDP through
the engine's sockets, and interop with independent virtual peers. Full guide:
[`docs/testing.md`](docs/testing.md).

```sh
# everything, in order, one summary (~15 s on a warm build)
python3 tools/test-all.py --oxt-bin /path/to/oxt/bin

# ...or layer by layer:
# native shims, the mock MIDI library, C tests (smoke, vectors, mock, fuzzer)
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure --no-tests=error

# wire-format vectors (clean-room codecs + python-osc), virtual peers, static gates
python3 tests/vectors_reference_test.py
python3 tests/simulators_test.py
python3 tests/checker_fixtures_test.py && python3 tools/check-livecodescript.py

# the bindings under lc-run, then the Script layer in the engine (any OXT build folder)
python3 tools/run-lcb-tests.py --oxt-bin /path/to/oxt/bin --build-dir build
python3 tools/run-lcs-tests.py --oxt-bin /path/to/oxt/bin --build-dir build
```

CI ([`.github/workflows/test.yml`](.github/workflows/test.yml)) runs all of it on
Linux, Windows and macOS against a pinned, checksum-verified OXT-Beyond release,
with libFuzzer + ASan/UBSan on the OSC parser; [`build.yml`](.github/workflows/build.yml)
keeps the sanitizer gate (`-fno-sanitize-recover=all`, `float-cast-overflow`
included) and the build matrix. A suite that cannot run fails rather than skips.

**Hand testing without hardware.** [`tools/sim/`](tools/sim) gives you the other
end of the wire — a virtual Art-Net node (answers ArtPoll, shows each ArtDmx as a
level meter) and an OSC peer (prints and acknowledges what it decoded). In OXT,
[`examples/selftest.livecodescript`](examples/selftest.livecodescript) (also run
in CI) and the visual [`examples/loopback-monitor.livecodescript`](examples/loopback-monitor.livecodescript)
loop each extension back through itself — see [`docs/testing-in-oxt.md`](docs/testing-in-oxt.md).

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
| [`docs/testing.md`](docs/testing.md) | The automated, headless, hardware-free test suites: lc-run, the engine with `-ui`, the mock MIDI library, the virtual peers, the shared vectors, fuzzing, writing tests, and the engine facts they pin. |
| [`docs/testing-in-oxt.md`](docs/testing-in-oxt.md) | Validate the whole stack inside OXT with **no hardware** — the automated self-test, the visual loopback monitor, and software MIDI loopback. |
| [`docs/phase0-ffi-spike.md`](docs/phase0-ffi-spike.md) | The one empirical unknown for the OXT pass: `Data` ⇄ pointer marshalling, with a hex-transport fallback. |
| [`docs/project-plan.md`](docs/project-plan.md) | The original strategy: target users, competitive positioning, showcase demos, milestones, risk register, roadmap. |
| [`CLAUDE.md`](CLAUDE.md) | The as-built record and the hard-won-lesson list (FFI conventions, per-extension gotchas). |
| [`CHANGELOG.md`](CHANGELOG.md) | Per-extension changes, the pre-OXT hardening pass, and the known follow-ups. |
