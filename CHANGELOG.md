# Changelog

All notable changes to ShowControl are recorded here. Versions are per-extension
where they diverge; the project as a whole tracks the headline milestones below.

## [Unreleased] - v1 foundation

The initial implementation scaffold: a verified native core, the three LCB
bindings, the build/test/CI machinery, and the documentation set.

### First OXT runtime pass - OSC build path (osc ABI -> 2)

Real OpenXTalk runs of the OSC build path surfaced issues no static gate could:
- **`[...]` list literals don't exist in LiveCode Script** (that is LiveCode
  Builder syntax) - the example scripts used them and failed to compile. Build OSC
  args by assignment via the `scAddArg` helper; a new `check-livecodescript.py`
  gate flags any `[...]` literal in a `.livecodescript`.
- **Nested Script arrays don't marshal to an LCB list-of-lists.** `oscBuildMessage`
  took a list of `[type, value]` pairs, but each inner pair arrived as an LCB
  `Array`, not a `List`. Switched to a **flat** args list (`type, value, type, ...`,
  all scalars, which cross cleanly); the `scAddArg` API is unchanged.
- **Numeric build args must cross as decimal strings (osc ABI 1 -> 2).** The engine
  hands a script number to a foreign `any` as a string, and LCB will not coerce a
  decimal string (e.g. `0.5`) to its `Number` type - so `put pValue into <Number>`
  threw. Added `osc_build_add_int32_str` / `_float_str` / `_double_str`; the binding
  now stringifies every numeric arg and the shim parses it in C (as it already did
  for int64). **A rebuilt osc binary (ABI 2) is required**; `checkABI()` throws a
  clear "rebuild the osc shim" error against an old ABI-1 binary.
- **Builder bug found in the process (now fixed):** `osc_build_finish` did not
  NUL-terminate the type-tag string when `','+tags` was itself a multiple of 4
  (argument count % 4 == 3, i.e. 3/7/11/... args), so the args ran into the tag
  region and the datagram failed to parse. The existing tests never used a count
  of 3; the new ABI-2 test (3 args: `ifd`) is the regression. OSC smoke test now
  67 assertions, green under ASan + UBSan + float-cast-overflow.
- The `Data` round-trip / `Array` access patterns in the examples and docs were
  corrected to the forms that actually work (`tArr[1]` not `item 1 of`, etc.), and
  the datagram receive handlers now tolerate the engine-dependent callback
  parameter order.

### CI: auto-refresh the committed native binaries via PR

The `build` workflow gained a **`package-binaries`** job that automates the manual
`tools/package-extension.py` step. When the `osc`/`midi` shim sources change on
`main` (or on a manual `workflow_dispatch`), it takes the per-platform libraries the
matrix just built and tested, drops them into the committed
`src/<ext>/code/<platform-id>/` slots, and **opens/updates a pull request**
(`ci/refresh-native-binaries`). A PR (not a direct push) because the compiled libs
are not byte-reproducible and `main` may be protected; the job is gated to actual
shim-source changes (no doc-push churn) and excludes `code/` from its trigger diff
so merging the refresh PR can't loop. Needs the one-time repo setting *Allow GitHub
Actions to create and approve pull requests*. (`docs/building.md` documents it.)

### Hardware-free in-OXT test kit

Ways to validate the whole stack inside OpenXTalk with no controllers, DAWs, or
DMX nodes - by looping each extension's output back through its own input.
- `midiDecode(pBytes)` - a new public MIDI handler that decodes one raw message
  into the same record `midiPoll` produces. It makes the decode logic testable
  with **no MIDI hardware** (drive it with synthetic bytes), and is useful on its
  own for decoding MIDI from a file / Web-MIDI bridge / sniffer. Pure LCB, no C/ABI
  change.
- `examples/selftest.livecodescript` - an automated, three-tier pass/fail self-test
  across all three extensions: Tier 1 pure `build -> parse` round-trips (every OSC
  type, bundles, `oscMatch`, Art-Net golden bytes, the MIDI decoder), Tier 2 OSC +
  Art-Net UDP loopback to `127.0.0.1`, Tier 3 MIDI enumeration / virtual-port
  lifecycle / errors. Tier 1 also serves as the Phase-0 `Data <-> pointer` FFI
  confirmation.
- `examples/loopback-monitor.livecodescript` - an interactive, self-building visual
  monitor: a "virtual DMX rig" of 16 fixtures that lights up from looped-back
  Art-Net, plus an OSC echo. The "watch it work" companion to the self-test.
- `docs/testing-in-oxt.md` - how to run both, the three tiers, and software MIDI
  loopback (IAC / ALSA `aconnect` / loopMIDI) for a full hardware-free MIDI
  round-trip. README, API reference, and doc index updated.

### Deep pre-OXT review (second hardening pass)

A second full audit of all three extensions ahead of the first OXT runtime pass,
with an independent adversarial review of the C shims (5M+ fuzzed datagrams, clean
on the network path) and a hand-audit of the LCB byte arithmetic. Every C fix is
verified under **ASan + UBSan + float-cast-overflow** (`-fno-sanitize-recover=all`).

**OSC:**
- **HIGH** Undefined behaviour coercing an out-of-range OSC `float`/`double` to
  `int32` (C11 6.3.1.4 *float-cast-overflow*) - reachable from a hostile network
  float, and **not** caught by the old gate (float-cast-overflow was split out of
  `-fsanitize=undefined`). `osc_arg_int32` now saturates via a `d_to_i32` helper,
  and the CI sanitizer gate adds `float-cast-overflow` so the whole class is gated.
- **HIGH** `osc_bundle_add_message` with an element length near `INT32_MAX`
  overflowed `4 + len`, skipped the buffer grow, and wrote through an unsized
  buffer (a reproducible segfault). Now bounded with int64 math, and
  `bundle_reserve` is overflow-safe. (Script-supplied length - not network-reachable.)
- **LOW** `builder_size` now accumulates in int64 and rejects a total `> INT32_MAX`,
  so an absurd argument pile cannot wrap the size and defeat `osc_build_finish`'s
  capacity check.
- **LOW** The last two raw pointer-cast reads in vendored tinyosc
  (`tosc_getNextBlob` length, `tosc_isBundle`) now read via `memcpy`, completing the
  alignment-safety pass - the misaligned-load class is fully closed.
- `oscParse` now preserves a **nested bundle** (a bundle element that is itself a
  bundle) by recursing in the LCB layer, instead of collapsing it to an empty message.
- `osc_arg_string` now NUL-terminates the caller buffer on the too-small path
  (matching `copy_str`/`osc_address`); documented that the three handle namespaces
  (builders / bundle builders / parsed messages) are disjoint and must not be crossed.

**MIDI:**
- **MEDIUM** The drain stash is now **pre-allocated per input port at open** (65535 B),
  so the drain hot path never `malloc`s and a buffer-full stash can never drop a
  popped message on OOM - the "never drop a popped message" invariant now holds
  unconditionally (previously a stash `malloc` failure silently lost the message).
- A drained message larger than 65535 B (un-representable in the 2-byte record
  length) now sets a last-error when dropped (was silent); it never wedges the port.
- The `double`->`uint32` delta conversion is NaN/range-guarded (defence in depth now
  that float-cast-overflow is gated).
- `midi_in_drain`'s returned count can no longer exceed `max_msgs` (the stash flush
  is gated on `max_msgs > 0`).

**Tests / CI / docs:**
- OSC smoke test -> **63 assertions** (added nested-bundle parse, out-of-range
  float->int32 saturation, and bundle-element-overflow rejection).
- New `tests/midi_mock_smoke_test.c` + `tests/mock/rtmidi_c.h`: a controllable
  RtMidi **mock** that exercises the drain / stash / oversize / port-name / `max_msgs`
  paths deterministically under sanitizers - on every platform and even where RtMidi
  cannot be fetched - covering what the hardware-dependent `midi_smoke` test cannot.
  Wired into CMake/CTest as `midi_mock_smoke` (independent of `SHOWCONTROL_BUILD_MIDI`).
- The CI ASan/UBSan gate adds `float-cast-overflow`.
- `README.md` replaced with a comprehensive, as-built front-door README; the
  original strategy/implementation plan preserved at `docs/project-plan.md`.

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

### First OXT compile pass (LiveCode Builder syntax)

The first real OpenXTalk compile of the `.lcb` bindings (no headless LCB compiler
exists) surfaced LiveCode Builder syntax issues the static checker didn't cover:
- **Missing module terminator** - every `library ...` must be closed with
  `end library`; none of the three were, so the parser ran to EOF and reported a
  bare "syntax error" at end-of-file on all three bindings. Added `end library` to
  osc/midi/artnet, plus a new `check-livecodescript.py` gate that flags an
  unterminated `module`/`library`/`widget`.
- **`div` is LiveCode Script only** - LCB has no integer-division operator (the
  compiler accepts `mod` but rejects `div`). Replaced every `div` with an `intDiv`
  helper (floor division via `a - (a mod b)`): artnet x6, midi x2.
- **`numToByte`/`byteToNum` are LiveCode Script names** - LCB spells them
  `the byte with code <n>` and `the code of <byte>` (`com.livecode.byte`). Replaced
  all 65 uses. The ArtPollReply IP string additionally needed each octet
  `formatted as string` before `&`, since LCB's `&` concatenates Strings only.
- **`and`/`or` and `numToChar` are LiveCode Script too** - found by a full audit of
  all three bindings against the LCB stdlib sources. LCB has no `and`/`or` boolean
  operators (commented out in `logic.lcb`; only `not`) and no `numToChar`. Rewrote
  the two `or` conditions (osc `readArg`, artnet universe guard) as separate branches,
  and replaced `numToChar` with `the char with code`. The audit also confirmed the
  numeric model (`Integer`==`Real`==`Number`, so the `intDiv`/arithmetic assignments
  are correct) and that the builtin syntax modules are implicitly in scope.
- **check-livecodescript.py** gained a script-ism gate that flags every LiveCode
  Script-only construct we hit (`numToByte`/`byteToNum`/`numToChar`/`charToNum`/`div`/
  `and`/`or`) in `.lcb` files, so none can regress.

### OSC (`osc`, ABI 1)
- C shim `src/osc/osc_shim.c` over vendored tinyosc (ISC): a non-variadic
  incremental message builder, bundle builder, and a **bounds-checked** indexed
  parser (validates untrusted datagrams before tinyosc decodes them), plus OSC
  1.0 address-pattern matching (`? * [] {}`).
- LCB binding `src/osc/osc.lcb`: `oscBuildMessage`, `oscBuildBundle`, `oscParse`,
  `oscMatch`, `oscLastError` (+ the Linux native-loader helpers).
- 64-bit ints / timetags cross the FFI as decimal strings (no 64-bit foreign
  type in the engine).
- `tests/osc_smoke_test.c`: 63 assertions incl. every-type round-trip, bundles
  (incl. nested), matching, malformed-datagram fuzzing, float->int saturation, and
  the hardening regressions above - green under ASan + UBSan + float-cast-overflow
  with `-fno-sanitize-recover=all`.

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
- ~~Automate populating the committed `code/<platform-id>/` trees~~ - **done**: the
  `package-binaries` CI job opens a refresh PR when the shim sources change. Still
  open: wire `package-extension.py --check` into CI as a required gate once every
  populated slot (incl. 32-bit Linux) is filled.
- macOS code-signing + notarization of the osc/midi dylibs (Gatekeeper will block
  the unsigned artifacts CI currently ships); Art-Net is exempt.
- Pin RtMidi by commit SHA rather than the movable `GIT_TAG 6.0.0` for fully
  reproducible fetches.
- Widen the handle-table generation field (currently 8-bit, wraps after 256 slot
  reuses); not script-reachable today since the LCB layer brackets create/use/free.
