# ShowControl Architecture

How the three protocol extensions are built, why the OSC and MIDI shims exist
(and why Art-Net needs none), the single rule that keeps the whole design safe,
and how to extend each binding.

ShowControl is three independent extensions for **OpenXTalk (OXT)** / the xTalk
language family (also compatible with **LiveCode 9.6.3+**):

| Extension | Wraps | Native shim? | Transport | Inbound pattern |
|-----------|-------|--------------|-----------|-----------------|
| **osc** | tinyosc (ISC) | yes (`osc`) | LiveCode UDP sockets | script `socketReceived` |
| **midi** | RtMidi (modified MIT) | yes (`midi`) | RtMidi backends | poll + drain on a timer |
| **artnet** | nothing (pure protocol) | **no** | LiveCode UDP sockets | script `socketReceived` |

- [The three layers](#the-three-layers)
- [Why a C shim (and why Art-Net needs none)](#why-a-c-shim-and-why-art-net-needs-none)
- [The decisive rule: never call LCB from a C callback](#the-decisive-rule-never-call-lcb-from-a-c-callback)
- [The two inbound patterns](#the-two-inbound-patterns)
- [Handles and safety](#handles-and-safety)
- [Crossing the FFI boundary](#crossing-the-ffi-boundary)
- [The ABI version](#the-abi-version)
- [Extending a binding](#extending-a-binding)

---

## The three layers

Each native extension (osc, midi) is three layers; the pure-LCB extension
(artnet) is the bottom two only. Every layer speaks to the one below it in a
narrower, more machine-friendly vocabulary, and the boundary at the bottom is the
locked C ABI.

```
  your xTalk script
        |  oscBuildMessage / midiPoll / artnetBuildDmx   (Data, Arrays, Lists)
  +-----v---------------------------------------+
  | src/<ext>/<ext>.livecodescript              |   script helpers: optional
  |   - the MIDI poll dispatcher                |   sugar + the poll loop
  +-----+---------------------------------------+
        |  public handlers: oscParse, midiOpenInput, artnetParseDmx, ...
  +-----v---------------------------------------+
  | src/<ext>/<ext>.lcb  (xTalk Builder lib)    |   foreign handlers +
  |   library org.openxtalk.library.<ext>       |   public wrappers;
  |   public: oscBuildMessage / midiPoll / ...  |   hides every handle
  +-----+---------------------------------------+
        |  FFI:  c:osc> osc_*   c:midi> midi_*    (ints, doubles, pointers)
  +-----v---------------------------------------+
  | src/<ext>/<ext>_shim.c  +  upstream lib     |   one shared library per
  |   osc_shim.c  + tinyosc -> osc.{so,dll,..}  |   <ext>; exports flat
  |   midi_shim.c + RtMidi  -> midi.{so,dll,..} |   osc_* / midi_* C symbols
  +---------------------------------------------+

  artnet has NO bottom layer -- pure LCB byte-packing, no C, no binary:
  +---------------------------------------------+
  | src/artnet/artnet.lcb                       |   builds/parses ArtDmx /
  |   library org.openxtalk.library.artnet      |   ArtPoll bytes entirely
  |   public: artnetBuildDmx / artnetParseDmx   |   in pure LCB
  +---------------------------------------------+
```

**Bottom layer (osc, midi only) - the C shim + upstream library.** For OSC,
`src/osc/osc_shim.c` compiles together with the vendored **tinyosc**
(`src/third_party/tinyosc`) into a single shared library named `osc`
(`osc.so` / `osc.dylib` / `osc.dll`). For MIDI, `src/midi/midi_shim.c` compiles
with **RtMidi** (fetched and pinned by CMake) into a library named `midi`. The
shims export flat C functions prefixed `osc_*` / `midi_*` - the locked ABI in
[`src/osc/osc_shim.h`](../src/osc/osc_shim.h) and
[`src/midi/midi_shim.h`](../src/midi/midi_shim.h). Each library **ships bundled
inside its extension**, under `src/<ext>/code/<arch>-<platform>/<ext>.{so,dll,dylib}`
(bare token, no `lib` prefix; platform-ids `x86_64-linux`, `x86-linux`,
`x86_64-win32`, `x86-win32`, `universal-mac` - architecture first, Windows
`-win32` for both bitnesses). Those libraries are committed (built and tested by
CI, attached to each Release); [`tools/package-extension.py`](building.md#refreshing-the-committed-binaries)
refreshes the tree from a newer build.

**Middle layer - the LCB library.** `src/<ext>/<ext>.lcb` is the xTalk Builder
(LCB) extension, declared with the `library` keyword so its public handlers join
the LiveCode Script message path and scripts call them as ordinary
commands/functions. It declares `private foreign handler` bindings to the shim
symbols (`binds to "c:osc>osc_parse!cdecl"`, `binds to "c:midi>midi_in_drain!cdecl"`)
and wraps each in a friendly `public handler` (`oscParse`, `midiPoll`, ...). The
engine resolves the `c:osc>` / `c:midi>` library through **`the revLibraryMapping`**
 - a name-to-path table the IDE populates by scanning the extension's
`code/<arch>-<platform>/` folder on install, so the right library loads
automatically per platform with no loose file to place. Module names are
reverse-DNS: `org.openxtalk.library.osc`, `org.openxtalk.library.midi`,
`org.openxtalk.library.artnet`.

**Top layer - script helpers (optional).** `src/<ext>/<ext>.livecodescript` is
pure-xTalk sugar on top of the `.lcb` API. For MIDI it carries the **poll
dispatcher** (the timer loop that calls `midiPoll` and `send`s a semantic message
per event - see [The two inbound patterns](#the-two-inbound-patterns)); for OSC
and Art-Net it is thin or absent, because their inbound path is the engine's own
`socketReceived` handler, which the user writes.

**Art-Net is the bottom two layers collapsed into one.** It has no C shim and no
native binary: `src/artnet/artnet.lcb` packs and unpacks the ArtDmx / ArtPoll
byte layouts directly in LCB. It is the third (and cheapest) instance of the same
socket-codec pattern as OSC, which is why it is exempt from the native build
matrix and macOS notarization, and ships as a single `.lce` that runs everywhere
the engine runs.

## Why a C shim (and why Art-Net needs none)

tinyosc is already C and RtMidi already ships a C API (`rtmidi_c.h`), so why a
shim at all? Because the LCB FFI is happiest with flat scalars, pointers, and
caller-owned buffers - and each upstream has at least one shape the FFI cannot
express directly.

**OSC - two tinyosc shapes the FFI cannot bind.**

1. **Variadic write API.** `tosc_writeMessage(buf, len, address, format, ...)`
   uses C varargs, which the LCB FFI cannot call. The shim replaces it with a
   non-variadic **incremental builder**: `osc_build_new(address)` opens a builder
   handle, `osc_build_add_int32 / _float / _string / _blob / ...` append typed args
   one call at a time, and `osc_build_finish` serializes into a caller buffer.
   This is the same "flatten an array-ish API into a call sequence" move the
   Box2D binding used for polygon vertices.
2. **Cursor-based, unchecked read API.** tinyosc's `tosc_getNext*` advance an
   internal cursor and, critically, do **not** bounds-check. The shim
   **pre-scans** each datagram once, with full bounds checking, into an indexed
   model, so the script can random-access arguments by position *and* a malformed
   datagram becomes a clean error (`osc_parse` returns `0`) instead of an
   out-of-bounds read. The OSC smoke test fuzzes truncated, mis-tagged, and
   oversized-blob datagrams to prove this.

**MIDI - the polling-drain ergonomics.** RtMidi's C API is mostly bindable
as-is, but two things want a shim. RtMidi instance pointers (`RtMidiInPtr`,
`RtMidiOutPtr`) must not leak to script as raw pointers, so the shim keeps them
in a handle table and hands script a plain integer. And rather than make the LCB
layer call `rtmidi_in_get_message()` once per queued message, `midi_in_drain`
**batches every queued message into one caller buffer per poll** - one FFI
round-trip per timer tick instead of N. (Record format below.)

**Both shims - never return a library-owned `const char*`.** Handing the engine
a bare `const char*` that the library owns has crashed the engine in a reported
case, so every string/byte result is written into a **caller-allocated buffer**
and the byte count is returned (or `-needed` when the buffer is too small). The
only pointers the shims return are to memory with a *defined* lifetime the engine
copies immediately - a parsed message's own buffer (`osc_address_str`,
`osc_arg_string_z`), a module-static name buffer (`midi_in_name_str`), or the
module-static last-error string (`osc_error_str` / `midi_error_str`) - the
proven Box2Dxt `dlerror`/`realpath` pattern. These never hand back memory the
library may free or reuse unexpectedly, and return `""` (never `NULL`) on a bad
handle.

**Art-Net - no shim, on purpose.** Art-Net's primary packet is plain byte
concatenation over UDP; there is no third-party library to wrap and no variadic
or cursor API to tame. Implementing it in pure LCB keeps it dependency-free,
which is its whole strategic advantage: zero compiled artifacts to build, sign,
or maintain per platform, and a single `.lce` that installs everywhere. The one
real hazard is not an FFI hazard at all but a wire-format one - mixed
endianness; see [building.md](building.md#art-net-no-native-build) and the
[API reference](api-reference.md#art-net).

## The decisive rule: never call LCB from a C callback

All three protocols *could* push inbound events through a C callback. ShowControl
deliberately **does not**, because invoking an LCB handler from a foreign (often
non-main) thread is fragile and unsupported. Every inbound path is arranged so
that **no foreign thread ever invokes script**:

- **OSC inbound** rides LiveCode's UDP socket. The engine delivers the datagram
  to a normal `socketReceived` handler on its own run loop; the extension only
  converts bytes to structured args. No thread, no callback, no queue of our own.
- **Art-Net inbound** is identical - a LiveCode UDP socket on port 6454 delivers
  the packet; the extension only decodes bytes. It is the third instance of the
  same pattern, and (being pure LCB) the simplest.
- **MIDI inbound** uses RtMidi's **internal FIFO**, which RtMidi maintains
  *precisely when no callback is registered*. We never register one. Instead the
  LCB layer **polls** and drains that queue on a timer (below).

This one rule is what makes the project low-risk, and it also defines the latency
model: only MIDI input latency scales with a cadence we control.

## The two inbound patterns

**Sockets (OSC, Art-Net) - the engine pushes.** You open an ordinary datagram
socket and write returned `Data` to it; inbound datagrams arrive in
`socketReceived`, where you call `oscParse` / `artnetParseDmx`. The extension is a
pure codec; LiveCode owns the socket and the run loop.

```
   open datagram socket ":9000"
   on socketReceived pData, pHost
      put oscParse(pData) into tMsg -- bytes -> Array
      ...
      read from socket pHost for 8192 -- keep reading
   end socketReceived
```

**Polling (MIDI) - you pull on a timer.** RtMidi buffers and delta-time-stamps
every inbound message in its internal FIFO. The LCB `midiPoll(pHandle)` makes one
`midi_in_drain` call, walks the batched records, decodes each, and returns a list
of event records. A script timer loop calls `midiPoll` and dispatches; ShowControl
ships that loop as the **poll dispatcher** in `midi.livecodescript`, so users
write only event handlers:

```
   on midiPollAndDispatch pHandle
      put midiPoll(pHandle) into tEvents
      repeat for each element tEvent in tEvents
         switch tEvent["kind"]
            case "noteOn"
               send "onNoteOn" && tEvent["channel"], \
                    tEvent["data1"], tEvent["data2"] to the target
               break
            -- controlChange, noteOff, ...
         end switch
      end repeat
      send "midiPollAndDispatch pHandle" to me in 3 milliseconds
   end midiPollAndDispatch
```

Because RtMidi buffers and timestamps **between** polls, *throughput* and
*message integrity* are independent of poll cadence - a jittery or slow tick
never drops a message. Only worst-case **added input latency** scales with the
interval (a 3 ms poll adds at most ~3 ms). The interval is therefore a
user-tunable latency knob, documented as such; a callback-based input path for
tight instrument play is a roadmap item, not part of v1.

### The drain record format

`midi_in_drain` concatenates queued messages into one buffer and returns the
record count. Each record is (per [`midi_shim.h`](../src/midi/midi_shim.h)):

```
   [2 bytes: byteLen, big-endian]
   [byteLen bytes: the raw MIDI message]
   [4 bytes: delta-time in MICROSECONDS since the previous message, big-endian]
```

A 2-byte length carries SysEx up to 65535 bytes; the LCB layer walks records with
plain byte arithmetic - no float unpacking. A message that does not fit in the
buffer is stashed and emitted on the next call, so nothing is ever dropped. The
LCB layer converts the microsecond delta to **seconds** for the `delta` field of
each `midiPoll` record (see the [API reference](api-reference.md#midi)).

## Handles and safety

Builders, parsed messages, and open MIDI ports cross the boundary as **positive
32-bit integer handles**; `0` is never valid. Handles are **generation-tagged**:
each packs a small generation counter above its table slot, bumped every time the
slot is freed. The shim validates every handle before use, so calling any handler
with a stale, freed, or never-created handle is a **harmless no-op** - getters
return `0`/empty, actions do nothing - instead of crashing the engine or
silently addressing whatever now occupies the recycled slot.

> Handles are opaque tokens. The LCB layer hides them entirely for OSC: it
> brackets `osc_build_new ... osc_build_finish ... osc_build_free` inside one
> `oscBuildMessage` call, and `osc_parse ... osc_parse_free` inside one `oscParse`
> call, so script never sees a handle there. MIDI port handles **are** visible to
> script (you pass them to `midiSend`/`midiPoll`/`midiClose`), because a port is
> a long-lived resource you own - still drop the handle after `midiClose`, which
> is what keeps the tables small.

The smoke tests exercise this directly: a `midi_in_drain` / `osc_arg_count` on a
bogus handle returns `0`, and `midi_out_send` after `midi_close` is a no-op.

## Crossing the FFI boundary

The LCB FFI binds only to C symbols and is deliberately narrow about types. These
are the conventions every ShowControl binding follows.

| Script-side value | LCB type | Crosses the FFI as | Notes |
|-------------------|----------|--------------------|-------|
| real number | `CDouble` | `double` | xTalk numbers are doubles |
| integer / handle | `CInt` | `int32_t` | handles are positive; `0` = invalid |
| boolean | `CInt` | `int` `0`/`1` | e.g. `midiIgnoreTypes` flags |
| byte buffer (datagram, blob, MIDI bytes, DMX) | `Data` | `Pointer` + `CInt` length | see below |
| short string (address, type tag, port name, error) | `String` | `ZStringUTF8` | never the LCB `string` type |
| **int64 / uint64 / timetag** | `String` | **decimal `ZStringUTF8`** | the engine has no 64-bit foreign int |

**Byte buffers cross as a (pointer, length) pair.** On the script side a
datagram, an OSC blob, a MIDI message, or a DMX frame is LiveCode **`Data`**. An
LCB `Data` bridges to a pointer to its first byte, so an **in** buffer passes that
pointer plus a `CInt` length to the shim (`osc_parse(data, len)`,
`midi_out_send(handle, data, len)`). For an **out** buffer the LCB layer
**pre-sizes a `Data`** to the needed capacity and passes it as the `Pointer` the
shim fills; the shim returns the byte count written, or `-needed` if the buffer
was too small, so the caller can grow it and retry. This is the one detail the
Phase 0 spike confirmed empirically against the target engine; it is isolated in
the shim so the LCB API is stable regardless.

**Short strings cross as `ZStringUTF8`.** OSC addresses and type tags, MIDI port
names, and the last-error strings cross as UTF-8 zero-terminated strings - never
the LCB `string` type. The shim either fills a caller buffer
(`osc_address(h, out, cap)`) or hands back a defined-lifetime pointer the engine
copies straight into a `ZStringUTF8` (`osc_address_str(h)`, `midi_in_name_str(i)`).

**64-bit values cross as decimal strings.** The target xTalk engine (9.6.3) has
**no 64-bit foreign integer type**, and a script number is a double anyway (only
53 bits of integer precision). So OSC int64 arguments and OSC/timetag NTP values
cross the boundary as **decimal strings**: `osc_build_add_int64_str(h, "9223372036854775807")`
on the way in, `osc_arg_int64_str(h, i, out, cap)` on the way out. The smoke test
round-trips `INT64_MAX` this way to prove no precision is lost. The shim keeps
parallel non-string entry points (`osc_build_add_int64`, `osc_arg_int64`) for the
C test harness, but the LCB layer always uses the `_str` path.

Art-Net needs none of this marshalling vocabulary on the C side - it builds and
parses `Data` entirely within LCB - but it observes the same script-facing
shapes: `Data` in and out, `String` for opcodes and node names, `Array` for
parsed packets.

## The ABI version

Each shim exports an ABI version, currently **`1`** for both:

- `osc_abi_version()` - `OSC_ABI_VERSION` in [`osc_shim.h`](../src/osc/osc_shim.h).
- `midi_abi_version()` - `MIDI_ABI_VERSION` in [`midi_shim.h`](../src/midi/midi_shim.h).

Use it as a load/version sanity check (the LCB layer surfaces it, and the smoke
tests assert it is `1`), and **bump it whenever the exported ABI changes** so the
`.lcb` and the native library cannot silently drift apart. Art-Net has no compiled
ABI to version - its contract is the byte layout of the packets it builds, which
is fixed by the Art-Net specification.

> The exported symbols keep the stable `osc_` / `midi_` prefixes; never rename
> them - the `binds to "c:osc>..."` / `"c:midi>..."` strings in the `.lcb` reference
> these names, and renaming would break already-compiled binaries.

## Extending a binding

### Extending osc or midi (a new native handler)

Exposing more of tinyosc / RtMidi is mechanical:

1. **C shim** (`src/osc/osc_shim.c` or `src/midi/midi_shim.c`) - add an
   `OSC_API ... osc_yourthing(...)` (or `MIDI_API ... midi_yourthing(...)`) function that
   calls the upstream API. Store/look up any opaque objects in the existing handle
   table, validate the handle before use, fill **caller** buffers for any
   string/byte output (never return a library-owned `const char*`), and carry any
   64-bit value as a decimal string. Declare it in the matching `*_shim.h`.
2. **LCB library** (`src/<ext>/<ext>.lcb`) - add a matching
   `private foreign handler ... binds to "c:<ext>><ext>_yourthing!cdecl"`, then a
   `public handler ...` wrapper that pre-sizes any out `Data`, bridges types per the
   table above, hides the handle, sets the module last-error on failure
   (so `oscLastError`/`midiLastError` report it), and returns empty/`0` rather
   than throwing across the boundary.
3. **Script helper** (`src/<ext>/<ext>.livecodescript`, optional) - add sugar
   only if it earns its place (e.g. a new dispatch case in the MIDI loop).
4. **Bump `OSC_ABI_VERSION` / `MIDI_ABI_VERSION`** in the shim if the exported ABI
   changed.
5. **Rebuild** the native library ([building.md](building.md)), refresh the
   committed `src/<ext>/code/<arch>-<platform>/` copy with
   `tools/package-extension.py`, then re-Package (or **Test**) the extension so
   the new library loads.

Add a smoke-test assertion in `tests/osc_smoke_test.c` /
`tests/midi_smoke_test.c` for anything non-trivial so CI exercises it on every
platform.

### Extending artnet (a new packet type)

Art-Net has no native layer, so extending it is pure LCB:

1. **Add a builder / parser** in `src/artnet/artnet.lcb` - a `public handler`
   that concatenates the packet's fields into a `Data` (build) or walks a `Data`
   into an `Array` (parse). Honor the field endianness exactly: the ArtDmx OpCode
   is **little-endian**, but the protocol-version and Length fields are
   **big-endian** (high byte first) - reversing those two is the classic bug.
2. **Set the module last-error** on malformed input and return empty, matching
   `oscLastError` behavior via `artnetLastError`.
3. **Add a golden-packet test fixture** so a byte-exact ArtDmx/ArtPollReply is
   asserted (this is where the mixed-endianness bug surfaces) and round-trips.

There is **no ABI to bump, no library to rebuild, and no signing step** - Art-Net
ships as a single `.lce`. What stays intentionally out of scope across all three:
any path that would require calling LCB from a C callback (sample-accurate OSC
timetag scheduling, callback-based MIDI input), since the whole architecture is
built to avoid it. See the [getting-started guide](getting-started.md) to use the
extensions and the [API reference](api-reference.md) for the full handler surface.
