# ShowControl API Reference

The complete script-facing API for the three ShowControl extensions, as exposed
by their LCB libraries. This is the canonical reference for **OpenXTalk (OXT)** /
the xTalk family (also compatible with **LiveCode 9.6.3+**). For a guided
introduction see [getting-started.md](getting-started.md); for how it all works
see [architecture.md](architecture.md).

**Conventions (all three extensions)**

- **Transport is the engine's, not the extension's.** osc and artnet build/parse
  `Data`; *you* open the UDP socket, `write` the built `Data` to `host:port`, and
  `oscParse`/`artnetParseDmx` the `Data` you `read` in `socketReceived`. midi I/O
  goes through the native MIDI service via an integer port handle.
- **Failure is quiet, never thrown.** A fallible handler that fails **returns
  empty / `0`** (never an exception across the FFI boundary) and sets a
  module-level last-error you retrieve with `oscLastError()` / `midiLastError()` /
  `artnetLastError()`. Successful calls clear it. Check the return first; read the
  error string only to find out *why*.
- **Handles are integers; `0` is invalid.** Every handler tolerates a stale,
  closed, or `0` handle as a **harmless no-op** (getters return `0`/empty, actions
  do nothing) - the shim validates handles, generation-tagged, before use.
- **Units & numbering.** OSC travels over **UDP**. MIDI channels are **1-based**
  (1-16). Art-Net universes hold up to **512 channels** of one byte each; cap each
  universe at **~44 Hz** refresh.

- [OSC](#osc)
- [MIDI](#midi)
- [Art-Net](#art-net)
- [Notes and gotchas](#notes-and-gotchas)

---

## OSC

`library org.openxtalk.library.osc`. Build OSC messages and bundles into `Data`,
parse received datagrams into an Array, and match address patterns. tinyosc does
the wire format; the extension is a pure codec over LiveCode's UDP sockets.

**OSC argument type codes** (used by `oscBuildMessage` and reported in `types`):

| Code | Type | Value on the script side |
|------|------|--------------------------|
| `i` | int32 | number |
| `f` | float32 | number |
| `s` | string | string |
| `b` | blob | `Data` |
| `d` | float64 (double) | number |
| `h` | int64 | **decimal string** (engine has no 64-bit int) |
| `t` | timetag | **decimal string** (NTP-64; `1` = immediately) |
| `T` | true | (no value) |
| `F` | false | (no value) |
| `N` | nil | (no value) |
| `I` | impulse / infinitum | (no value) |

> `h` and `t` carry 64 bits, which a script number (a double) cannot hold
> exactly, so they cross as decimal strings end to end - pass `["h","9223372036854775807"]`,
> read it back as the string `"9223372036854775807"`. See
> [architecture.md](architecture.md#crossing-the-ffi-boundary).

### `oscBuildMessage(pAddress, pArgs)` -> Data

Build a single OSC message. `pAddress` is the OSC address pattern (a string such
as `/1/fader1`); `pArgs` is a sequence of `[type, value]` pairs. For the no-value
types (`T`/`F`/`N`/`I`) supply an (ignored) value, e.g. empty. Returns the complete
OSC datagram as `Data`, ready to `write ... to socket`.

> **Building `pArgs` in LiveCode Script.** xTalk has **no `[...]` list-literal
> expression** (that is LiveCode *Builder* syntax) - you **cannot** write
> `oscBuildMessage("/x", [["i", 60]])` in a `.livecodescript`. Build the args array
> by assignment, or use the `scAddArg` helper in
> `examples/showcontrol-helpers.livecodescript`:
>
> ```
> local tArgs
> scAddArg tArgs, "i", 60      -- each call appends one [type, value] pair
> scAddArg tArgs, "f", 0.8
> put oscBuildMessage("/synth/note", tArgs) into tData
> ```
>
> `scAddArg` just appends the type then the value as two consecutive elements
> (`put pType into pArgs[(the number of elements of pArgs) + 1]` then the same for
> `pValue`). The args cross as a **flat** list - `["i", 60, "f", 0.8]`, not a list of
> `[type, value]` sub-lists - because a nested LiveCode array does not survive the
> Script->LCB boundary as a list-of-lists (the inner pair arrives as an Array, not a
> List). `oscBuildMessage` walks the flat list two elements at a time. (Inside a
> `.lcb`, the `[...]` literal *is* valid - this caveat is only for LiveCode Script.)

**Fails** (returns empty, sets last-error) on a null/over-long address, an unknown
type code, or a value that cannot be coerced to the declared type.

```
local tArgs
scAddArg tArgs, "i", 60
scAddArg tArgs, "f", 0.8
put oscBuildMessage("/synth/note", tArgs) into tData
write tData to socket "127.0.0.1:7000"
```

### `oscBuildBundle(pTimetag, pMessages)` -> Data

Build an OSC bundle. `pTimetag` is an NTP-64 timetag as a **decimal string**
(`"1"` means "immediately"); `pMessages` is a sequence of `Data` values, each a
message previously built with `oscBuildMessage`. Returns the bundle datagram as
`Data`. (Build that sequence by assignment too - same no-`[...]` rule as above.)

**Fails** (returns empty, sets last-error) on a bad timetag string or a non-`Data`
element.

```
local tA, tB, tArgs, tMsgs
scAddArg tArgs, "i", 1
put oscBuildMessage("/a", tArgs) into tA
put empty into tArgs
scAddArg tArgs, "f", 2.0
put oscBuildMessage("/b", tArgs) into tB
put tA into tMsgs[1]
put tB into tMsgs[2]
write oscBuildBundle("1", tMsgs) to socket "127.0.0.1:7000"
```

### `oscParse(pData)` -> Array

Parse a received OSC datagram (`Data`) into an Array. Handles both plain messages
and bundles, and is **fully bounds-checked** - a malformed datagram returns empty
rather than crashing.

**A plain message** returns:

| Key | Value |
|-----|-------|
| `isBundle` | `false` |
| `address` | the address pattern string (e.g. `/1/fader1`) |
| `types` | the type-tag string (e.g. `"fsi"`), one char per argument |
| `args` | a **1-based list** of decoded argument values, in order |

Each element of `args` is decoded per its type code: numbers for `i`/`f`/`d`,
strings for `s`, `Data` for `b`, decimal strings for `h`/`t`. The no-value types
`T`/`F`/`N`/`I` still occupy a position (read the corresponding char of `types` to
interpret them).

**A bundle** returns:

| Key | Value |
|-----|-------|
| `isBundle` | `true` |
| `timetag` | the bundle's NTP-64 timetag as a **decimal string** |
| `messages` | a list of Arrays, each shaped like a plain-message result above |

**Fails** (returns empty, sets last-error) on a null, truncated, mis-tagged, or
otherwise malformed datagram.

```
on oscDataReceived pSocket, pData
   put oscParse(pData) into tMsg
   if tMsg["isBundle"] then
      repeat for each element tSub in tMsg["messages"]
         handleOneMessage tSub
      end repeat
   else
      handleOneMessage tMsg
   end if
   read from socket pSocket with message "oscDataReceived"
end oscDataReceived

on handleOneMessage tMsg
   if tMsg["address"] is "/1/fader1" then
      set the thumbPosition of scrollbar "Vol" to (tMsg["args"][1]) * 100
   end if
end handleOneMessage
```

### `oscMatch(pPattern, pAddress)` -> Boolean

Test an OSC address against an OSC 1.0 address **pattern** with wildcards: `?`
(one char, not `/`), `*` (any run within a segment, not crossing `/`), `[...]`
(character class, `[!...]` negates, ranges like `[0-9]`), and `{a,b}` (alternation).
Returns `true`/`false`.

```
if oscMatch("/1/fader*", tMsg["address"]) then ... -- /1/fader1, /1/fader2, ...
if oscMatch("/ch[0-9]", tMsg["address"]) then ... -- /ch0 ... /ch9
```

### `oscLastError()` -> String

Return the last OSC error string (empty when the last fallible call succeeded).
Call it right after a handler returns empty/`0` to learn why.

```
local tArgs
scAddArg tArgs, "z", 1                            -- "z" is not a type code
put oscBuildMessage("/x", tArgs) into tData
if tData is empty then answer oscLastError()
```

---

## MIDI

`library org.openxtalk.library.midi`. Enumerate, open, and close MIDI ports; send
raw or convenience messages; and **poll** the input queue to receive. There is no
callback - RtMidi buffers inbound messages and you drain them on a timer (see
[architecture.md](architecture.md#the-two-inbound-patterns)). **Channels are
1-based (1-16).**

### Enumeration

| Handler | Returns |
|---------|---------|
| `midiInputPorts()` -> List | Input port names; the **list position minus 1** is the index to open. |
| `midiOutputPorts()` -> List | Output port names; same index convention. |

```
put midiInputPorts() into tIns -- line 1 = index 0, line 2 = index 1, ...
```

### Open / close

All openers **return an integer handle (`0` on failure)**; pass the handle to
every later call, and `midiClose` it when done.

| Handler | Purpose |
|---------|---------|
| `midiOpenInput(pIndex)` -> Integer | Open the input port at 0-based `pIndex`. |
| `midiOpenOutput(pIndex)` -> Integer | Open the output port at 0-based `pIndex`. |
| `midiOpenVirtualInput(pName)` -> Integer | Create a virtual input named `pName` (macOS / Linux). |
| `midiOpenVirtualOutput(pName)` -> Integer | Create a virtual output named `pName` (macOS / Linux). |
| `midiClose(pHandle)` | Close a port and release it. A `0`/stale handle is a no-op. |
| `midiIgnoreTypes(pHandle, pSysex, pTime, pSense)` | On an input, ignore SysEx / timing-clock / active-sensing messages (each a Boolean). |

**Fails** (returns `0`, sets last-error) on an out-of-range index, or when no MIDI
backend is available, or - for virtual ports - on Windows (WinMM has no virtual
ports). After `midiClose`, the handle is dead and every call on it is a no-op.

```
put midiOpenInput(0) into tIn
if tIn is 0 then answer "No MIDI in:" && midiLastError()
midiIgnoreTypes tIn, true, true, true -- ignore sysex, timing, sensing
```

### Sending

`midiSend` takes raw bytes; the rest build correct status bytes for you.
`pChannel` is **1-based** (1-16); note/controller/program/value are 0-127;
`pValue14bit` is the 14-bit pitch-bend value (0-16383, center 8192).

| Handler | Sends |
|---------|-------|
| `midiSend(pHandle, pBytes)` | Raw MIDI bytes (`Data`) - full control, e.g. SysEx. |
| `midiNoteOn(pHandle, pChannel, pNote, pVelocity)` | Note On. |
| `midiNoteOff(pHandle, pChannel, pNote, pVelocity)` | Note Off. |
| `midiControlChange(pHandle, pChannel, pController, pValue)` | Control Change (CC). |
| `midiProgramChange(pHandle, pChannel, pProgram)` | Program Change. |
| `midiPitchBend(pHandle, pChannel, pValue14bit)` | Pitch Bend (14-bit). |
| `midiChannelPressure(pHandle, pChannel, pValue)` | Channel (mono) Aftertouch. |

**Fails** (no-op, sets last-error) on a `0`/stale handle, a non-output handle, or
empty/zero-length raw bytes.

```
midiNoteOn tOut, 1, 60, 100 -- ch1 middle C on, velocity 100
midiControlChange tOut, 1, 7, 96 -- ch1 channel-volume to 96
midiSend tOut, (binaryEncode("CCCC", 0xF0, 0x7E, 0x00, 0xF7)) -- raw SysEx
```

### Receiving - `midiPoll(pHandle)` -> List

Drain everything queued on an input since the last call and return a **list of
event records**, oldest first. Returns an empty list when nothing is waiting (and
on a `0`/stale/non-input handle). Call it repeatedly on a timer.

Each record is an Array:

| Key | Value |
|-----|-------|
| `delta` | seconds since the previous message in the queue (a number) |
| `bytes` | the raw MIDI message as `Data` |
| `kind` | the decoded message kind (string, see below) |
| `channel` | **1-based** channel 1-16 (for channel-voice messages) |
| `data1` | first data byte - note number / controller / program (0-127) |
| `data2` | second data byte - velocity / value (0-127; absent for 1-data messages) |
| `value14` | reconstructed 14-bit value 0-16383 - **only** present for `kind = "pitchBend"` |

`kind` is one of `"noteOn"`, `"noteOff"`, `"controlChange"`, `"programChange"`,
`"pitchBend"`, `"channelPressure"`, `"polyAftertouch"`, or `"sysex"` (and
`"other"` for anything not specially decoded). For `"pitchBend"` the 14-bit value
is reconstructed into `value14` (0-16383, 8192 = centre); for `"sysex"` the full
message is in `bytes`. `channel`/`data1`/`data2`/`value14` are present only where
the kind defines them - always branch on `kind`.

> **`delta` is in seconds.** The native shim carries each message's inter-onset
> gap in microseconds; the LCB layer converts it to seconds for `delta`. Because
> RtMidi timestamps **between** polls, the `delta` values are accurate even if
> your poll cadence jitters - *timing and message integrity are independent of how
> often you poll.* Only worst-case added input latency scales with the poll
> interval.

```
on midiPollLoop
   put midiPoll(sMidiIn) into tEvents
   repeat for each element tEvent in tEvents
      switch tEvent["kind"]
         case "noteOn"
            triggerSample tEvent["data1"], tEvent["data2"] -- note, velocity
            break
         case "controlChange"
            if tEvent["data1"] is 7 then
               set the thumbPosition of scrollbar "Master" to tEvent["data2"]
            end if
            break
      end switch
   end repeat
   send "midiPollLoop" to me in 3 milliseconds
end midiPollLoop
```

ShowControl also ships a **poll dispatcher** in `examples/showcontrol-helpers.livecodescript` that wraps
this loop and `send`s a per-event message (`onNoteOn`, `onControlChange`, ...) so
you can write only handlers - see
[architecture.md](architecture.md#the-two-inbound-patterns).

### `midiDecode(pBytes)` -> Array

Decode **one** raw MIDI message (`Data`) into the same record shape `midiPoll`
returns (`kind` / `channel` / `data1` / `data2` / `value14` / `bytes` / `delta`,
with `delta` 0). Use it to decode MIDI bytes that arrive from somewhere other than a
polled input - a file, a Web-MIDI bridge, a sniffer - and to **unit-test your MIDI
handling with no hardware** (drive it with synthetic bytes and assert the result;
the in-OXT self-test does exactly this - see
[testing-in-oxt.md](testing-in-oxt.md)).

```
put midiDecode(numToByte(144) & numToByte(60) & numToByte(100)) into tEvent
-- tEvent["kind"] is "noteOn", tEvent["channel"] is 1, tEvent["data1"] is 60
```

### `midiLastError()` -> String

Return the last MIDI error string (empty when the last fallible call succeeded).

```
put midiOpenOutput(99) into tOut -- out of range
if tOut is 0 then answer midiLastError()
```

---

## Art-Net

`library org.openxtalk.library.artnet`. Build and parse Art-Net (DMX512 over UDP)
packets. **Pure LCB - no native binary.** Same transport model as OSC: build a
`Data`, `write` it to `host:6454`, and parse the `Data` you receive. A **universe**
is up to **512 channels**, one byte (0-255) each.

> "Art-Net" is a trademark of Artistic Licence; this extension is **Art-Net
> compatible**. Honor two wire conventions: cap each universe at **~44 Hz**
> refresh (the DMX512 frame ceiling), and prefer **unicast** to a node's IP over
> broadcast above ~30 universes.

### `artnetBuildDmx(pUniverse, pChannels)` -> Data

Build an **ArtDmx** output packet. `pUniverse` is the 15-bit port-address
(universe) number; `pChannels` is up to **512 bytes** of channel data as `Data`,
one byte per channel (channel 1 is the first byte). Returns a ready-to-send ArtDmx
datagram. The packet's mixed endianness (OpCode little-endian; protocol-version
and Length big-endian) is handled for you.

**Fails** (returns empty, sets last-error) on an out-of-range universe (outside
`0..32767`). Channel data longer than 512 bytes is truncated to 512; shorter than 2
is zero-padded, and an odd length is padded up to even.

```
put numToByte(255) into tCh -- channel 1 full
write artnetBuildDmx(0, tCh) to socket "2.0.0.10:6454"
```

### `artnetParseDmx(pData)` -> Array

Parse a received ArtDmx packet (`Data`) into an Array:

| Key | Value |
|-----|-------|
| `opcode` | `"ArtDmx"` |
| `universe` | the 15-bit universe number (`net * 256 + subuni`) |
| `net` | the Net field (high 7 bits of the port-address) |
| `subuni` | the low byte of the port-address (`SubNet << 4 \| Universe`), **not** the 4-bit SubNet alone |
| `sequence` | the sequence byte (1-255 for ordering; 0 = disabled) |
| `physical` | the Physical input port (informational) |
| `length` | DMX data length in bytes (2-512, even) |
| `channels` | the channel bytes as `Data` (`length` bytes, one per channel) |

**Fails** (returns empty, sets last-error) if the bytes are not a valid ArtDmx
packet (bad ID, wrong OpCode, inconsistent length).

```
on dmxArrived pSocket, pData
   put artnetParseDmx(pData) into tDmx
   if tDmx["opcode"] is "ArtDmx" and tDmx["universe"] is 0 then
      put byteToNum(byte 1 of tDmx["channels"]) into tDimmer -- channel 1
   end if
   read from socket pSocket with message "dmxArrived"
end dmxArrived
```

### `artnetBuildPoll()` -> Data

Build an **ArtPoll** discovery datagram (no arguments). Broadcast it to
`255.255.255.255:6454`; nodes answer with ArtPollReply packets you parse with
`artnetParseReply`.

```
write artnetBuildPoll() to socket "255.255.255.255:6454"
```

### `artnetParseReply(pData)` -> Array

Parse an **ArtPollReply** (a node's response to ArtPoll) into an Array:

| Key | Value |
|-----|-------|
| `opcode` | `"ArtPollReply"` |
| `ip` | the node's IP address string (e.g. `"2.0.0.10"`) |
| `net` | the node's Net field |
| `subuni` | the node's Sub-Net/Universe switch byte |
| `shortName` | the node's short name |
| `longName` | the node's long name |

(Richer per-port discovery - port counts and per-port universes - is intentionally
out of scope for this cut; see README S7.) **Fails** (returns empty, sets
last-error) if the bytes are not a valid ArtPollReply.

```
on replyArrived pSocket, pData
   put artnetParseReply(pData) into tNode
   if tNode["opcode"] is "ArtPollReply" then
      put tNode["longName"] && "(" & tNode["ip"] & ")" & return after field "Nodes"
   end if
   read from socket pSocket with message "replyArrived"
end replyArrived
```

### `artnetSendDmx(pUniverse, pChannels, pHost)` (optional convenience)

This convenience verb ships in `examples/showcontrol-helpers.livecodescript` (not
the extension itself - pure LCB cannot open sockets). It wraps build-plus-write:
it builds an ArtDmx packet for `pUniverse` from `pChannels` and writes it to
`pHost:6454`. Equivalent to
`write artnetBuildDmx(pUniverse, pChannels) to socket (pHost & ":6454")`. Its
failure behaviour is whatever the engine's `write` does - it does not itself set
the Art-Net last-error (`artnetBuildDmx` returns empty on a bad universe as above).

```
artnetSendDmx 0, tChannels, "2.0.0.10"
```

### `artnetLastError()` -> String

Return the last Art-Net error string (empty when the last fallible call
succeeded).

---

## Notes and gotchas

**Sockets are yours (OSC, Art-Net).** The extensions never open or read a socket - 
they only build and parse `Data`. You `accept datagram connections on port ...`,
`write ... to socket "host:port"`, and `read from socket ... with message ...` (and
re-`read` to keep listening). Inbound bytes are `Data`; pass them straight to
`oscParse` / `artnetParseDmx` / `artnetParseReply`. See the worked sockets in
[getting-started.md](getting-started.md).

**MIDI is polled, not pushed.** Run a timer loop that calls `midiPoll` and
dispatches. The poll interval is a **latency knob**, not a correctness one - a
slow tick adds latency but never drops messages, because RtMidi buffers and
timestamps between polls. For tight instrument play, lower the interval; a
callback-based input path is a roadmap item, not part of v1.

**64-bit OSC values are decimal strings.** OSC `int64` (`h`) and `timetag` (`t`),
including a bundle's `timetag`, cross as decimal strings on both build and parse,
because the engine has no 64-bit integer type and a script number (a double)
cannot hold 64 bits exactly. Pass and read them as strings.

**Failure is empty/`0` plus a last-error - not an exception.** No ShowControl
handler throws across the FFI boundary. Test the return value, then read
`oscLastError()` / `midiLastError()` / `artnetLastError()` to find out why a call
came back empty or `0`. Stale, closed, or `0` handles are always harmless no-ops.

**The Art-Net mixed-endianness trap.** When debugging a node that ignores your
ArtDmx, suspect the wire format first: the OpCode is little-endian while the
protocol-version and Length fields are big-endian (high byte first). The extension
gets this right; validate end to end with Wireshark (`udp.port == 6454`).

**Extending the bindings.** See
[architecture.md](architecture.md#extending-a-binding) for the per-extension
recipe - a C shim function + foreign handler + public wrapper (and an ABI bump)
for osc/midi, or a pure-LCB builder/parser for artnet, each with a smoke-test or
golden-packet fixture.
