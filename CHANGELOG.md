# Changelog

All notable changes to ShowControl are recorded here. Versions are per-extension
where they diverge; the project as a whole tracks the headline milestones below.

## [Unreleased] - v1 foundation

The initial implementation scaffold: a verified native core, the three LCB
bindings, the build/test/CI machinery, and the documentation set.

### Pre-OXT hardening - security & robustness

A full pre-release review of all three extensions (each layer audited under
Address/UndefinedBehavior sanitizers where buildable). Fixes by severity:

**OSC** (the memory-safety items are reachable from a single hostile UDP datagram):
- **CRITICAL** Heap out-of-bounds read - a bundle element-size near `INT32_MAX`
  overflowed `p + elen`, was accepted, then recursed into a bogus length. Now
  compared overflow-safe as `elen > len - p` (`parse_bundle_bytes`).
- **CRITICAL** Stack-overflow DoS - unbounded bundle nesting recursed (parse *and*
  recursive free) until the stack blew. Added a depth cap (`OSC_MAX_BUNDLE_DEPTH`).
- **HIGH** 32-bit OOB - a blob length near `INT32_MAX` overflowed `p + 4 + bl` in
  the validator and was accepted (a 2 GB over-read on the 32-bit targets). Overflow-safe now.
- **HIGH** Timetag corruption - OSC timetags (`t`) are unsigned NTP-64 but were
  formatted signed, so any real ("now") value read back negative. Now unsigned.
- **MEDIUM** `oscMatch` ReDoS - several `*` made the matcher exponential (a hostile
  pattern hung the run loop). Added a step budget + consecutive-`*` collapse.
- **MEDIUM** `oscBuildMessage` now fails (returns empty) on an unknown type code
  instead of silently dropping the argument.
- **LOW** Misaligned loads in vendored tinyosc (`*(uint64_t*)` on a byte-advanced
  pointer - UBSan, SIGBUS risk on arm64): the getters now read via `memcpy`.

**MIDI:**
- **CRITICAL** Drain wedge - an inbound message larger than the drain buffer
  (`kDrainCap` was 16 KB; e.g. a >16 KB SysEx dump) was stashed but never re-emitted,
  permanently wedging the input port. `kDrainCap` is now >= the maximum record
  (65541 B) and the C stash-flush drops a structurally-too-large record (with a
  last-error) rather than looping forever.
- **HIGH** `midi_in_name` / `midi_out_name` returned a bogus negative length for
  every successful call vs RtMidi 6.0.0 (it returns the length via the return value
  and never writes `*bufLen`); fixed to trust the return value.
- **MEDIUM** Convenience senders clamp data bytes to 0-127, channel to 1-16, and
  pitch-bend to 0-16383, so out-of-range script input can no longer emit illegal MIDI.

**Art-Net:**
- **CRITICAL/HIGH** `artnetBuildDmx` rejects an out-of-range universe (valid `0..32767`)
  instead of throwing on a negative one or silently addressing the wrong universe.
- **LOW** `artnetParseDmx` clamps a declared length to the 512-channel spec maximum.

**Build / CI / docs:**
- The CI sanitizer gate now uses `-fno-sanitize-recover=all` (+ `*SAN_OPTIONS`) so
  UndefinedBehaviorSanitizer **fails** the build instead of only printing - the old
  gate let real UB (the tinyosc misalignment above) pass green.
- Docs reconciled with the as-built code: binding names, the helper-script location,
  `package-extension.py` flags, the release trigger, MIDI `value14`, Art-Net parse keys.

### OSC (`osc`, ABI 1)
- C shim `src/osc/osc_shim.c` over vendored tinyosc (ISC): a non-variadic
  incremental message builder, bundle builder, and a **bounds-checked** indexed
  parser (validates untrusted datagrams before tinyosc decodes them), plus OSC
  1.0 address-pattern matching (`? * [] {}`).
- LCB binding `src/osc/osc.lcb`: `oscBuildMessage`, `oscBuildBundle`, `oscParse`,
  `oscMatch`, `oscLastError` (+ the Linux native-loader helpers).
- 64-bit ints / timetags cross the FFI as decimal strings (no 64-bit foreign
  type in the engine).
- `tests/osc_smoke_test.c`: 55 assertions incl. every-type round-trip, bundles,
  matching, malformed-datagram fuzzing, and the hardening regressions above -
  green under ASan + UBSan with `-fno-sanitize-recover=all`.

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
- `.github/workflows/build.yml`: static + golden + ASan/UBSan gate (aborts on any
  sanitizer error), then Linux-x86_64 / macOS-universal / Windows (x64 + x86)
  builds, attaching binaries to releases.
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
  x86_64 on a standard runner. **`x86-linux` is not yet built**, so that bundle
  slot ships empty until a job is added (or the slot is dropped) - decide before
  the first release.
- Automate populating the committed `code/<platform-id>/` trees from release
  assets (CI attaches flat per-platform files; refresh is currently a manual
  `package-extension.py` step) and wire `--check` into CI once slots are populated.
- macOS code-signing + notarization of the osc/midi dylibs (Gatekeeper will block
  the unsigned artifacts CI currently ships); Art-Net is exempt.
- Pin RtMidi by commit SHA rather than the movable `GIT_TAG 6.0.0` for fully
  reproducible fetches.
- Widen the handle-table generation field (currently 8-bit, wraps after 256 slot
  reuses); not script-reachable today since the LCB layer brackets create/use/free.
