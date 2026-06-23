# Changelog

All notable changes to ShowControl are recorded here. Versions are per-extension
where they diverge; the project as a whole tracks the headline milestones below.

## [Unreleased] - v1 foundation

The initial implementation scaffold: a verified native core, the three LCB
bindings, the build/test/CI machinery, and the documentation set.

### OSC (`osc`, ABI 1)
- C shim `src/osc/osc_shim.c` over vendored tinyosc (ISC): a non-variadic
  incremental message builder, bundle builder, and a **bounds-checked** indexed
  parser (validates untrusted datagrams before tinyosc decodes them), plus OSC
  1.0 address-pattern matching (`? * [] {}`).
- LCB binding `src/osc/osc.lcb`: `oscBuildMessage`, `oscBuildBundle`, `oscParse`,
  `oscMatch`, `oscLastError` (+ the Linux native-loader helpers).
- 64-bit ints / timetags cross the FFI as decimal strings (no 64-bit foreign
  type in the engine).
- `tests/osc_smoke_test.c`: 49 assertions incl. every-type round-trip, bundles,
  matching, and malformed-datagram fuzzing - green under ASan + UBSan.

### MIDI (`midi`, ABI 1)
- C shim `src/midi/midi_shim.c` over RtMidi (fetched, MIT): enumerate/open/close
  (incl. virtual ports), raw + convenience send, and a batched `midi_in_drain`
  with a per-port stash so a full buffer never drops a popped message.
- LCB binding `src/midi/midi.lcb`: enumeration, open/close, `midiSend` +
  `midiNoteOn/Off`/`midiControlChange`/`midiProgramChange`/`midiPitchBend`/
  `midiChannelPressure`, and `midiPoll` decoding raw bytes into semantic records.
  Reuses a persistent drain buffer in the poll hot path.
- `tests/midi_smoke_test.c`: exercises enumeration, virtual ports, drain, and the
  handle-safety guards; passes with or without a MIDI backend present.

### Art-Net (`artnet`) - pure LCB
- LCB binding `src/artnet/artnet.lcb`: `artnetBuildDmx`, `artnetParseDmx`,
  `artnetBuildPoll`, `artnetParseReply`, `artnetLastError`. No C shim, no native
  binary, no notarization.
- `tests/artnet_golden_test.py`: 18 byte-exact golden-packet assertions pinning
  the mixed-endianness layout (OpCode little-endian; version/length big-endian).

### Build, test & tooling
- `CMakeLists.txt`: builds both shims (osc.{so,dll,dylib}, midi.{so,dll,dylib})
  under their bare token names; vendors tinyosc, fetches RtMidi; optional C tests.
- `.github/workflows/build.yml`: static + golden + ASan gate, then
  Linux-x86_64 / macOS-universal / Windows (x64 + x86) builds, attaching
  binaries to releases.
- `tools/check-livecodescript.py` (dual `.lcb` + `.livecodescript` linter),
  `tools/package-extension.py` (refresh the committed per-platform trees).
- `docs/`: architecture, building, getting-started, api-reference, and the
  **Phase-0 FFI spike** that confirms `Data` <-> pointer marshalling in OXT.

### Known follow-ups
- Run the Phase-0 FFI spike in OXT to confirm byte-buffer marshalling
  (`docs/phase0-ffi-spike.md`); the C ABI is stable regardless of outcome.
- Showcase demos and the example/helper script layer (README section 11).
- Per-universe Art-Net sequence numbering and refresh-rate throttle.
- Maximally-portable Linux binaries (glibc-2.17 floor via a manylinux container
  with a modern toolchain) and 32-bit Linux builds; the current CI builds Linux
  x86_64 on a standard runner.
