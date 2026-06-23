# Project Plan: OSC + MIDI (+ Art-Net) Extensions for LiveCode 9.6.3 / OpenXTalk Lite

**Codename:** "Show Control" — LiveCode Builder (LCB) extensions wrapping **tinyosc** (Open Sound Control) and **RtMidi** (realtime MIDI I/O) for v1, with an **Art-Net** (DMX-over-Ethernet lighting) extension as a v1.1 fast-follow. Together they position OXT/LiveCode as a credible tool for interactive installations and live performance.

**Document status:** v1 + v1.1 implementation + strategy plan
**Audience:** the extension team that previously wrapped Box2D
**Prerequisite knowledge assumed:** LCB foreign-handler FFI, C/C++ shim authoring, cross-platform native build tooling

---

## 1. Executive summary

We ship three small, permissively-licensed (or royalty-free) protocol extensions:

- **`osc`** — built on tinyosc (pure C, single file, ISC license). Converts between LiveCode data and OSC wire-format messages/bundles. Rides LiveCode's existing UDP sockets for transport.
- **`midi`** — built on RtMidi via its C API `rtmidi_c.h` (modified-MIT license). Enumerates, opens, sends, and receives MIDI across CoreMIDI (macOS), ALSA/JACK (Linux), and WinMM (Windows).
- **`artnet`** (v1.1 fast-follow) — Art-Net, i.e. DMX512 lighting control over UDP. A **royalty-free protocol with no third-party library required**: it's pure byte-packing over a UDP socket, implementable as pure LCB with no C shim.

The strategic thesis: Box2D made OXT viable for **games**; OSC + MIDI + Art-Net make it viable for **interactive media** — museums, theater, live visuals, music controllers, lighting, experiential installations. That community (currently served by Max/MSP, TouchDesigner, Isadora, Processing, openFrameworks, Pd, QLab, Chataigne) has no modern, English-like, rapid-UI, cheap-to-deploy option. LiveCode's strengths — fast UI assembly, low barrier to entry, single-file standalones — map directly onto what installation and show builders need and what those incumbents make hard.

**Why these wrap unusually cleanly:** all three sidestep the single hardest LCB FFI problem — calling a C callback *back into* LCB. OSC and Art-Net inbound arrive through LiveCode UDP sockets the engine already manages; MIDI inbound is drained from RtMidi's own internal FIFO queue by **polling** on a timer. Nothing requires invoking an LCB handler from a foreign thread. Combined with clean licensing, this is a low-risk, high-visibility project. Art-Net is the cheapest of the three precisely because it needs **no native library at all**, which exempts it from the build matrix and macOS notarization the OSC and MIDI shims require.

**Rough timeline:** usable internal alpha (OSC + MIDI) in ~4–6 weeks; polished, documented, cross-platform v1 with showcase demos in ~9–12 weeks for a 1–2 person team; Art-Net v1.1 adds ~1–2 weeks and can ship independently because it has no signing dependency.

---

## 2. Target users & competitive positioning

### 2.1 Who this is for

1. **Installation / experiential artists** — museums, galleries, brand activations, science centers. They wire sensors, sound, video, and lighting into responsive environments. OSC is their connective tissue; MIDI triggers media and hardware; Art-Net drives the lights.
2. **Live-performance & theater technicians** — cue playback, show control, mapping faders/buttons to actions, and lighting cues. QLab-adjacent workflows. MIDI Show Control, MIDI triggering, and DMX/Art-Net are everyday tools.
3. **VJs and audiovisual performers** — controller-driven visuals, tempo-synced graphics, OSC bridges to/from TouchOSC, Resolume, Max.
4. **Musicians / instrument builders** — custom MIDI controllers, generative sequencers, bespoke performance UIs.
5. **Educators** — physical computing and creative-coding courses where LiveCode's gentle learning curve is a real asset.

### 2.2 Where OXT/LiveCode fits

| Incumbent | Strength | Gap OXT can exploit |
|---|---|---|
| Max/MSP, Pd | Deep audio/DSP patching | Steep for UI; not a general app builder; Max is paid/closed |
| TouchDesigner | GPU visuals, node graph | Heavy, GPU-bound, paid tiers, steep curve |
| Isadora | Theater-friendly media | Niche, paid, less general-purpose |
| Processing / openFrameworks | Flexible creative coding | Code-first; slow UI assembly; no rapid forms |
| QLab | Show cue playback (macOS) | macOS-only, playback-centric, not a builder |
| Chataigne | Protocol glue (OSC/MIDI/DMX) | Powerful but its own paradigm; not an app builder |

OXT's wedge: **assemble a custom control surface or responsive installation UI in an afternoon, talk OSC/MIDI/Art-Net to everything else, and ship a cross-platform standalone.** No incumbent owns that combination of rapid UI + protocol I/O + easy deployment.

### 2.3 The positioning play (not just a feature)

Releasing OSC + MIDI together — then Art-Net right behind — with **showcase demos and tutorials aimed squarely at this community**, is a market-entry move. The deliverable is not "three libraries"; it's "OXT is now a viable installation/performance/lighting tool, here's proof, here's how." Adding Art-Net specifically recruits the **lighting and theatrical** world, the segment most starved for an approachable, deployable tool. Section 11 details the demos that make the case — including an audio/MIDI-reactive Box2D physics scene that compounds the previous win, and a sound-driven lighting demo that closes the loop.

---

## 3. Scope

### 3.1 In scope

**OSC extension (`osc`) — v1:**
- Build OSC messages with arbitrary typed argument lists (int32, float32, string, blob, true/false/nil/impulse, int64, double, timetag).
- Parse received OSC datagrams into address + indexed, typed arguments.
- Bundle support (parse nested messages; read timetag; build bundles).
- OSC address-pattern matching helper (wildcards `? * [] {}`).
- Transport via LiveCode UDP sockets (the extension does encoding/decoding only).

**MIDI extension (`midi`) — v1:**
- Enumerate input and output ports (count + names).
- Open/close input and output ports by index; open **virtual** ports (macOS/Linux).
- Send: raw byte messages plus convenience handlers (noteOn/noteOff/controlChange/programChange/pitchBend/channelPressure).
- Receive: drain RtMidi's queue by polling; deliver messages into the LiveCode message path with per-message delta-time.
- Configure ignore-types (SysEx / timing / active-sensing).
- MIDI message parsing helpers (status/data → semantic event; SysEx assembly).

**Art-Net extension (`artnet`) — v1.1 fast-follow:**
- ArtDmx **output** (send up to 512 channels per universe to a node/console). The 90% use case.
- ArtDmx **input** (receive DMX universes).
- Node **discovery** via ArtPoll / ArtPollReply.
- Multi-universe addressing and a per-universe refresh-rate throttle.

**Shared:**
- Cross-platform native shims for OSC + MIDI (Win/macOS/Linux), x86_64 + arm64, packaged inside each extension. Art-Net requires no native shim.
- Documentation, API reference, and sample stacks.

### 3.2 Out of scope (see roadmap §16)

sACN / E1.31 (the standardized lighting alternative — **first follow-on after Art-Net**); Art-Net advanced packets (RDM-over-Art-Net, ArtSync multi-universe sync, ArtTimeCode, video/firmware packets); Ableton Link (tempo sync); NDI/Spout/Syphon (video sharing); MIDI 2.0 / MPE; OSC-over-TCP (SLIP framing); MIDI clock master/slave, MTC/MMC; precise OSC bundle timetag *scheduling*; mobile (iOS/Android) targets.

---

## 4. Architecture & design principles

### 4.1 The LCB FFI constraints we design around

From the LCB language reference and community prior art (Trevor DeVore's WinSparkle/MQTT wrappers, the FluidSynth binding threads, the engine's own `Timezone.lcb`/`ini.lcb`):

- **C binding only.** Foreign handlers bind to C symbols with the string form `"c:libraryname>symbolname!callingconvention"`. The `c` is the language; the library name resolves a bundled shared lib; the symbol must exist; `!cdecl` is the safe explicit calling convention (non-Windows maps everything to default anyway). Symbols resolve lazily on first use, so all-platform bindings can live in one module and only the ones actually called will bind.
- **Strings cross the boundary as `ZStringNative` / `ZStringUTF8` / `ZStringUTF16`** — never the LCB `string` type. We standardize on **`ZStringUTF8`** for OSC address strings and port names.
- **Opaque handles cross as `Pointer` / `optional Pointer`.** Pointers deliberately **do not bridge to LiveCode Script** — so the public LCB API must expose script-safe handles (integers/opaque references) and values, not raw pointers. RtMidi instance pointers stay *inside* the LCB library; script sees an integer handle.
- **Byte buffers** (MIDI messages, OSC blobs/datagrams, Art-Net packets) are represented as LiveCode **`Data`** on the script side and passed across FFI as a `Pointer` + `CInt` length pair. (Confirm the exact `Data`→pointer marshalling helper against the target engine build during Phase 0 — this is the one detail to nail down empirically.)
- **Numbers:** `CInt`, `CUInt`, `CFloat`, `CDouble`, `CBool`. `out`/`inout` params pass a pointer to a caller-side variable.
- **C++ requires `extern "C"`** to defeat name-mangling. RtMidi's C API already provides this; tinyosc is C already; Art-Net needs no C at all.
- **Returning a bare `const char*` has crashed the engine** in at least one reported case. Our shims therefore never hand back library-owned `const char*`. Instead: write into a **caller-allocated buffer** (LCB passes a buffer + capacity; shim fills it and returns the length), or expose explicit `*_alloc` / `*_free` pairs the LCB library brackets.
- **Per-extension private libraries are supported** — ship the `.dll`/`.dylib`/`.so` inside the extension package and reference it by name in the `binds to` string.

### 4.2 The decisive design rule: never call LCB from a C callback

All three extensions *could* push events via callbacks; we deliberately **don't** use that path, because invoking an LCB handler from a foreign (often non-main) thread is fragile and unsupported. Instead:

- **OSC inbound:** LiveCode's UDP socket delivers the datagram to a normal LiveCode handler. We only convert bytes → structured args. **No thread, no callback, no queue of our own.**
- **Art-Net inbound:** identical pattern — LiveCode UDP socket on port 6454 delivers the packet; we only decode bytes. **And uniquely, Art-Net needs no native shim at all** — it's the third (and simplest) instance of the socket-codec pattern, pure LCB.
- **MIDI inbound:** RtMidi maintains an **internal FIFO** when no callback is registered. We **poll** `rtmidi_in_get_message()` from LCB on a timer and drain the queue. Because RtMidi buffers between polls and stamps each message with a delta-time, **we never drop messages and timing is preserved even if the poll cadence jitters.**

This single rule is what makes the project low-risk. It also defines the latency model (§10.3).

### 4.3 Library, not widget

These are **libraries** (`library` keyword in LCB), not visual widgets. Public handlers enter the LiveCode Script message path so users call them like built-in commands/functions. No canvas, no UI surface — users build their own UI in LiveCode and call our verbs.

### 4.4 Naming & API conventions

- Reverse-DNS module names, e.g. `org.openxtalk.library.osc`, `org.openxtalk.library.midi`, `org.openxtalk.library.artnet` (final org TBD).
- Script-facing handlers use a consistent prefix: `oscBuild…`, `oscParse…`, `midiOpen…`, `midiSend…`, `artnetBuild…`, `artnetParse…`, etc.
- Handles are integers. `0` is never a valid handle; functions return `0`/empty on failure and set a retrievable last-error string.
- Errors: every fallible handler sets a module-level last-error; expose `oscLastError()` / `midiLastError()` / `artnetLastError()`. Avoid throwing across the FFI boundary.

---

## 5. OSC extension — detailed design

### 5.1 Transport decision: ride LiveCode sockets

tinyosc does **not** do networking — it parses/builds buffers. That's a feature: we let LiveCode own the socket and the run loop, and the extension is a pure, stateless-ish codec. This sidesteps threading entirely.

```
                 LiveCode Script                         osc library (LCB + C shim)
  ┌──────────────────────────────────┐        ┌─────────────────────────────────────┐
  │ open datagram socket ":9000"      │        │                                     │
  │ on socketReceived ...             │        │  oscParse(pData)  ──► tinyosc read  │
  │   put oscParse(theData) into tMsg │ ─────► │  oscArgCount / oscArgType / oscArg* │
  │ ...                               │        │                                     │
  │ put oscBuildMessage(...) into tD  │ ◄───── │  oscBuild* ──► tinyosc write (shim) │
  │ write tD to socket "host:9000"    │        │                                     │
  └──────────────────────────────────┘        └─────────────────────────────────────┘
```

**Alternative (defer):** a self-contained UDP engine inside the shim with its own thread, for use cases LiveCode sockets serve poorly (very high throughput, or network-thread timestamping). It reintroduces the polling-queue pattern, so only do it if a real need appears.

### 5.2 The two tinyosc problems the shim must solve

1. **Variadic write API.** `tosc_writeMessage(buf, len, address, format, ...)` uses C varargs — **not FFI-bindable**. The shim replaces it with a non-variadic, incremental builder.
2. **Cursor-based read API.** `tosc_getNext*` advance an internal cursor — workable but awkward from script. The shim instead **pre-scans** the message once into an indexed model so script can random-access arguments by position.

### 5.3 C shim API (`osc_shim.c` → `osc_shim` library)

```c
/* ---- Building (replaces tinyosc varargs) ---- */
/* Returns an opaque builder handle (>0), or 0 on failure. */
uint64_t  osc_build_new(const char *address /*utf8*/);
int       osc_build_add_int32 (uint64_t h, int32_t v);
int       osc_build_add_int64 (uint64_t h, int64_t v);
int       osc_build_add_float (uint64_t h, float v);
int       osc_build_add_double(uint64_t h, double v);
int       osc_build_add_string(uint64_t h, const char *v /*utf8*/);
int       osc_build_add_blob  (uint64_t h, const uint8_t *data, int32_t len);
int       osc_build_add_true  (uint64_t h);
int       osc_build_add_false (uint64_t h);
int       osc_build_add_nil   (uint64_t h);
int       osc_build_add_impulse(uint64_t h);
int       osc_build_add_timetag(uint64_t h, uint64_t ntp);
/* Serialize into caller buffer; returns bytes written, or -needed if too small. */
int32_t   osc_build_finish(uint64_t h, uint8_t *out, int32_t out_cap);
void      osc_build_free(uint64_t h);

/* ---- Parsing (indexed model over tinyosc) ---- */
/* Parse a datagram into an opaque parsed-message handle (>0). Handles bundles too. */
uint64_t  osc_parse(const uint8_t *data, int32_t len);
int       osc_is_bundle(uint64_t h);
uint64_t  osc_bundle_timetag(uint64_t h);
int32_t   osc_bundle_count(uint64_t h);             /* sub-messages */
uint64_t  osc_bundle_message(uint64_t h, int32_t i);/* sub-message handle */
int32_t   osc_address(uint64_t h, char *out, int32_t out_cap);   /* utf8, returns len */
int32_t   osc_typetag(uint64_t h, char *out, int32_t out_cap);   /* e.g. "fsi" */
int32_t   osc_arg_count(uint64_t h);
char      osc_arg_type(uint64_t h, int32_t i);      /* 'i','f','s','b','d','h','T','F','N','I','t' */
int32_t   osc_arg_int32 (uint64_t h, int32_t i, int *ok);
int64_t   osc_arg_int64 (uint64_t h, int32_t i, int *ok);
float     osc_arg_float (uint64_t h, int32_t i, int *ok);
double    osc_arg_double(uint64_t h, int32_t i, int *ok);
int32_t   osc_arg_string(uint64_t h, int32_t i, char *out, int32_t out_cap); /* len */
int32_t   osc_arg_blob  (uint64_t h, int32_t i, uint8_t *out, int32_t out_cap); /* len */
void      osc_parse_free(uint64_t h);

/* ---- Address pattern matching ---- */
int       osc_match(const char *pattern /*utf8*/, const char *address /*utf8*/);
```

Design notes: caller-allocated buffers everywhere (no library-owned `const char*` returned); `int *ok` out-params avoid ambiguous sentinels for numeric getters; handles are `uint64_t` so they map to LiveCode integers cleanly and never collide with `0`.

### 5.4 LCB library API (script-facing)

```
-- Building
oscBuildMessage(pAddress as String, pArgs as List) returns Data
   -- pArgs is a list of [type, value] pairs, e.g. [["f",0.5],["s","hello"],["i",3]]
   -- Returns the OSC datagram as Data, ready to write to a socket.

oscBuildBundle(pTimetag as String, pMessages as List) returns Data

-- Parsing
oscParse(pData as Data) returns Array
   -- Returns an array: ["address": "/x", "types":"fsi", "args": [ ... ], "isBundle": false ]
   -- For bundles: ["isBundle": true, "timetag": ..., "messages": [ array, array, ... ]]

-- Matching & helpers
oscMatch(pPattern as String, pAddress as String) returns Boolean
oscLastError() returns String
```

The LCB layer hides every handle: it brackets `osc_build_new … osc_build_finish … osc_build_free` within a single `oscBuildMessage` call and brackets `osc_parse … osc_parse_free` within a single `oscParse` call. Script never sees a handle or a pointer.

### 5.5 Example LiveCode Script usage

```
-- Receiving (TouchOSC fader on /1/fader1)
on socketReceived pData, pHost
   put oscParse(pData) into tMsg
   if tMsg["address"] is "/1/fader1" then
      set the thumbPosition of scrollbar "Volume" to (item 1 of tMsg["args"]) * 100
   end if
   read from socket pHost for 8192  -- keep reading
end socketReceived

-- Sending (drive a Resolume layer opacity)
on mouseUp
   put oscBuildMessage("/composition/layers/1/video/opacity/values", \
        [["f", 0.75]]) into tData
   write tData to socket "127.0.0.1:7000"
end mouseUp
```

---

## 6. MIDI extension — detailed design

### 6.1 Foundation: RtMidi C API

We bind `rtmidi_c.h` directly where possible and add a thin shim only for the polling-drain ergonomics and message packaging. RtMidi gives us one API over CoreMIDI/ALSA/JACK/WinMM. Output is request/response (trivial). Input uses the **internal-queue + poll** model.

Key RtMidi C entry points we rely on:

```c
RtMidiInPtr  rtmidi_in_create_default(void);
RtMidiOutPtr rtmidi_out_create_default(void);
void   rtmidi_open_port(RtMidiPtr, unsigned int portNumber, const char *portName);
void   rtmidi_open_virtual_port(RtMidiPtr, const char *portName);
void   rtmidi_close_port(RtMidiPtr);
unsigned int rtmidi_get_port_count(RtMidiPtr);
int    rtmidi_get_port_name(RtMidiPtr, unsigned int port, char *buf, int *bufLen);
void   rtmidi_in_ignore_types(RtMidiInPtr, bool sysex, bool time, bool sense);
double rtmidi_in_get_message(RtMidiInPtr, unsigned char *message, size_t *size); /* delta-time */
int    rtmidi_out_send_message(RtMidiOutPtr, const unsigned char *message, int length);
void   rtmidi_in_free(RtMidiInPtr);
void   rtmidi_out_free(RtMidiOutPtr);
```

`rtmidi_in_get_message` is the linchpin: pass a buffer + size; it fills the next queued message and returns its delta-time, or sets size to 0 if the queue is empty. That's a clean polling primitive — no callback needed.

### 6.2 C shim additions (`midi_shim.c`)

The shim mostly forwards to RtMidi but adds:

```c
/* Open/registry: keep RtMidi pointers inside; hand LCB an integer handle. */
uint64_t midi_in_open(int32_t portIndex);           /* -1 = virtual */
uint64_t midi_out_open(int32_t portIndex);          /* -1 = virtual */
int32_t  midi_in_count(void);
int32_t  midi_out_count(void);
int32_t  midi_in_name(int32_t i, char *out, int32_t cap);
int32_t  midi_out_name(int32_t i, char *out, int32_t cap);
void     midi_in_ignore(uint64_t h, int sysex, int time, int sense);

/* Drain: returns number of messages copied, packs them as length-prefixed
   records [u8 len][bytes...][f64 deltaTime] into a single caller buffer so
   LCB makes ONE call per poll instead of one per message. */
int32_t  midi_in_drain(uint64_t h, uint8_t *out, int32_t out_cap, int32_t max_msgs);

int32_t  midi_out_send(uint64_t h, const uint8_t *data, int32_t len);
void     midi_close(uint64_t h);
```

The `midi_in_drain` batching is the important ergonomic: one FFI round-trip per poll tick, returning *all* queued messages with their timestamps, instead of N round-trips.

### 6.3 LCB library API (script-facing)

```
-- Enumeration
midiInputPorts() returns List          -- list of port names, index = position-1
midiOutputPorts() returns List

-- Open / close (return integer handle, 0 on failure)
midiOpenInput(pIndex as Integer) returns Integer      -- use -1 for a virtual port
midiOpenOutput(pIndex as Integer) returns Integer
midiOpenVirtualInput(pName as String) returns Integer
midiOpenVirtualOutput(pName as String) returns Integer
midiClose(pHandle as Integer)
midiIgnoreTypes(pHandle as Integer, pSysex as Boolean, pTime as Boolean, pSense as Boolean)

-- Sending: raw + convenience
midiSend(pHandle as Integer, pBytes as Data)
midiNoteOn(pHandle as Integer, pChannel as Integer, pNote as Integer, pVelocity as Integer)
midiNoteOff(pHandle as Integer, pChannel as Integer, pNote as Integer, pVelocity as Integer)
midiControlChange(pHandle, pChannel, pController, pValue)
midiProgramChange(pHandle, pChannel, pProgram)
midiPitchBend(pHandle, pChannel, pValue14bit)
midiChannelPressure(pHandle, pChannel, pValue)

-- Receiving: poll + dispatch
midiPoll(pHandle as Integer) returns List
   -- Drains the queue; returns a list of records, each:
   --   ["delta": <seconds>, "bytes": <Data>, "kind": "noteOn",
   --    "channel": 1, "data1": 60, "data2": 100]
midiLastError() returns String
```

### 6.4 The receive loop in LiveCode Script

Because we poll, the user (or a helper we ship) runs a timer loop. We provide a ready-made dispatcher so users only write event handlers:

```
-- Shipped helper (in a sample library stack): starts polling and dispatches
on midiStartListening pHandle
   midiPollAndDispatch pHandle
end midiStartListening

on midiPollAndDispatch pHandle
   put midiPoll(pHandle) into tEvents
   repeat for each element tEvent in tEvents
      switch tEvent["kind"]
         case "noteOn"
            send "midiNoteOn" && tEvent["channel"], tEvent["data1"], tEvent["data2"] to the target
            break
         case "controlChange"
            send "midiCC" && tEvent["channel"], tEvent["data1"], tEvent["data2"] to the target
            break
      end switch
   end repeat
   send "midiPollAndDispatch pHandle" to me in 3 milliseconds
end midiPollAndDispatch

-- The user just writes:
on midiCC pChannel, pController, pValue
   if pController is 7 then set the thumbPosition of scrollbar "Master" to pValue
end midiCC
```

### 6.5 MIDI parsing helpers

The shim returns raw bytes + delta-time; the LCB layer decodes status/data bytes into the `kind`/`channel`/`data1`/`data2` fields (handling the standard channel-voice messages and flagging SysEx for assembly). Running-status handling lives in the decoder. This keeps the common case ("a knob moved") one property-set away for the user.

---

## 7. Art-Net extension — detailed design (v1.1 fast-follow)

### 7.1 The shape: the third socket-codec, and the cheapest one

Art-Net is DMX512 lighting control wrapped in UDP — a **royalty-free protocol whose spec is openly published by Artistic Licence**, running on port 6454. It has the same shape as OSC: a UDP byte-codec that rides LiveCode's sockets, with no callbacks, no threads, and no polling queue. It is *cheaper* than OSC for one decisive reason: **it needs no third-party library at all** — its primary packet is plain byte concatenation. Of Art-Net's ~20 packet types, only three matter in practice: **ArtPoll, ArtPollReply, and ArtDmx**.

The consequence in our context is large: Art-Net can be implemented as **pure LCB with no C shim** (and prototyped in pure LiveCode Script). That uniquely exempts it from the native build matrix and the macOS signing/notarization that the OSC and MIDI shims require. It ships as a single `.lce` that works everywhere the engine runs, with zero compiled artifacts to build, sign, or maintain per platform.

### 7.2 The ArtDmx packet (and the one real gotcha)

ArtDmx (OpCode `0x5000`) layout:

| Field | Bytes | Notes |
|---|---|---|
| ID | 8 | ASCII `"Art-Net"` + null terminator |
| OpCode | 2 | **little-endian**, `0x5000` for ArtDmx |
| Protocol version | 2 | **high byte first** (big-endian), value 14 |
| Sequence | 1 | 1–255 for ordering; 0 disables |
| Physical | 1 | informational |
| SubUni / Net | 2 | the 15-bit port-address (universe) |
| Length | 2 | **high byte first** (big-endian), DMX data length (2–512, even) |
| Data | up to 512 | one byte per channel |

**The gotcha that defeats most first implementations is mixed endianness:** the OpCode is little-endian, but the protocol-version and Length fields are transmitted **high byte first** (big-endian). Reverse those two and a node silently ignores the packet. Validate against a known-good "golden packet" and Wireshark (`udp.port == 6454`).

Two conventions to honor: cap per-universe refresh at ~44 Hz (the DMX512 frame-rate ceiling); prefer unicast over broadcast above ~30 universes.

### 7.3 Scope tiers (effort within the extension)

- **ArtDmx output** (control lights) — trivial; the 90% use case. An afternoon to prototype, a few days for a clean multi-universe API with refresh throttling.
- **ArtDmx input** (receive DMX) — easy; identical to OSC parsing. A day or two.
- **ArtPoll / ArtPollReply** (node discovery) — moderate; ArtPollReply records are richer structs (IP, short/long name, port counts, status). This is the one part with real surface; optional for the first cut.

### 7.4 LCB library API (script-facing) — pure LCB, no shim

```
-- Output
artnetBuildDmx(pUniverse as Integer, pChannels as Data) returns Data
   -- pChannels: up to 512 bytes, one per channel. Returns a ready-to-send ArtDmx datagram.

-- Input
artnetParseDmx(pData as Data) returns Array
   -- ["opcode":"ArtDmx", "universe":0, "sequence":12, "length":512, "channels": <Data>]

-- Discovery
artnetBuildPoll() returns Data                 -- an ArtPoll broadcast datagram
artnetParseReply(pData as Data) returns Array
   -- ["opcode":"ArtPollReply", "ip":"2.0.0.10", "shortName":"Node",
   --  "longName":"...", "numPorts":4, "universes":[0,1,2,3], ...]

artnetLastError() returns String
```

Transport is the user's own UDP socket, exactly as with OSC (write the returned `Data` to `host:6454`, or broadcast for discovery). A thin convenience verb (`artnetSendDmx pUniverse, pChannels, pHost`) can wrap build-plus-write if we expose a socket helper; otherwise users send the `Data` themselves.

### 7.5 Example LiveCode Script usage

```
-- A fader drives channel 1 (dimmer) of universe 0 on a hardware node
on faderChanged
   put empty into tChannels
   put numToByte(the thumbPosition of me) into char 1 of tChannels
   write artnetBuildDmx(0, tChannels) to socket "2.0.0.10:6454"
end faderChanged

-- Discover nodes on the network
on discoverNodes
   write artnetBuildPoll() to socket "255.255.255.255:6454"   -- broadcast
end discoverNodes

on socketReceived pData, pHost
   put artnetParseReply(pData) into tNode
   if tNode["opcode"] is "ArtPollReply" then
      put tNode["longName"] && "(" & pHost & ")" & return after field "Nodes"
   end if
end socketReceived
```

### 7.6 Implementation note

Implement Art-Net as **pure LCB**: the byte-packing is straightforward, and staying dependency-free is its whole advantage. Because there's no C shim, Art-Net adds **nothing** to the per-platform build/sign matrix — which is why it's the ideal v1.1 fast-follow: maximal strategic payoff (recruits the lighting/theatrical community) at minimal incremental cost and zero added release risk. Once this scaffolding exists, **sACN (E1.31)** is a small increment on top (see roadmap §16).

---

## 8. Build & packaging

### 8.1 tinyosc shim build (trivial)

Single C file + our `osc_shim.c`. No dependencies. Compile to a shared lib per platform:

- macOS: `clang -O2 -dynamiclib -arch x86_64 -arch arm64 tinyosc.c osc_shim.c -o libosc_shim.dylib`
- Linux: `gcc -O2 -shared -fPIC tinyosc.c osc_shim.c -o libosc_shim.so`
- Windows: `cl /O2 /LD tinyosc.c osc_shim.c /Fe:osc_shim.dll` with a **`.def`** file exporting the unadorned symbol names (LCB requires undecorated exports on Windows).

### 8.2 RtMidi shim build (per-platform backends)

RtMidi selects its backend via preprocessor defines and needs the platform MIDI framework linked:

| Platform | Define | Link |
|---|---|---|
| macOS | `__MACOSX_CORE__` | `-framework CoreMIDI -framework CoreAudio -framework CoreFoundation` |
| Linux (ALSA) | `__LINUX_ALSA__` | `-lasound -lpthread` |
| Linux (JACK, optional) | `__UNIX_JACK__` | `-ljack` |
| Windows | `__WINDOWS_MM__` | `winmm.lib` |

Example (macOS, universal):
```
clang++ -O2 -std=c++11 -D__MACOSX_CORE__ -dynamiclib -arch x86_64 -arch arm64 \
  RtMidi.cpp rtmidi_c.cpp midi_shim.c \
  -framework CoreMIDI -framework CoreAudio -framework CoreFoundation \
  -o libmidi_shim.dylib
```
On Windows, build the shim with the same `.def`-export discipline as §8.1, and remember the COM init caveat: with some Windows MIDI targets, the thread using RtMidi must call `CoInitializeEx`/`CoUninitialize`. Document this for the WinMM/UWP path.

### 8.3 Art-Net: no native build step

The Art-Net extension is **pure LCB** — there is nothing to compile, no shared library to ship, no `.def` files, no framework linking, and **no code signing or notarization** for this piece. It packages as a single `.lce`. This is the cleanest extension in the set and can be built, tested, and released independently of the OSC/MIDI native toolchain.

### 8.4 Bundling into the extension

For OSC and MIDI, place the shared libs in each extension's private library folder and reference by name in `binds to` (`"c:midi_shim>midi_in_drain!cdecl"`). Ship per-arch/per-OS binaries; LCB binds lazily so a single module can carry all platforms' bindings and only the ones invoked on the running platform resolve. Art-Net carries no binaries.

### 8.5 Architectures & engine targets

- Provide **x86_64 + arm64** (Apple Silicon) macOS universal dylibs for OSC/MIDI; Linux x86_64 (and arm64 if targeting Raspberry Pi installations — a real OXT audience); Windows x64.
- Confirm the bitness of the target LiveCode 9.6.3 / OXT Lite engine build and match it. Build the same matrix for both engines and test on each (they share LCB lineage but verify the extension install path and any divergence). Art-Net, being pure LCB, is architecture-agnostic.

### 8.6 macOS signing & notarization (do not skip — applies to OSC/MIDI only)

The OSC and MIDI extensions ship **native dylibs**. For distribution on macOS, those dylibs (and any standalone embedding them) must be **code-signed and notarized**, or Gatekeeper will block them. Bake signing into the build script now; retrofitting it late is painful. Linux and Windows have no equivalent hard gate, though Windows SmartScreen reputation is worth considering for standalones. **Art-Net has no native binary and is therefore exempt** — another reason it can ship on its own schedule.

---

## 9. Packaging as LiveCode/OXT extensions

- Use the `library` keyword (not `module`/`widget`) so public handlers join the LiveCode Script message path and are callable as ordinary commands/functions.
- Provide standard extension metadata (title, author, version, OS list).
- Build to `.lce` packages; install via the IDE's extension manager. For OXT Lite, validate the equivalent install flow and document any differences.
- Keep the three extensions **independent** (no cross-dependency) so users can adopt any one without the others. Art-Net is the lightest to install (no bundled binaries).

---

## 10. Testing & validation

### 10.1 Loopback / unit

- **OSC:** round-trip every type through `oscBuildMessage` → `oscParse`; fuzz with malformed datagrams (truncated, bad type tags, oversized blobs) to confirm graceful failure, not crashes. Validate bundle nesting and timetags. ASan build of the shim.
- **MIDI:** open a **virtual** port and send to self where supported (macOS IAC bus, Linux ALSA virtual, Windows via loopMIDI); verify every convenience message produces correct bytes; verify `midi_in_drain` batching, queue draining, and that no messages are lost across poll gaps. Exercise open/close cycles for handle-leak and native-resource leaks.
- **Art-Net:** golden-packet fixtures — assert `artnetBuildDmx` produces byte-exact ArtDmx packets (this is where the mixed-endianness bug surfaces), and that `artnetParseDmx`/`artnetParseReply` round-trip them. Loopback send/receive on port 6454.

### 10.2 Interop (the part that actually matters)

Test against the tools the target users run:
- **OSC:** TouchOSC (mobile control surface — the canonical test), Max/MSP, Pure Data, Resolume, QLab, Chataigne. Both directions.
- **MIDI:** a hardware controller (e.g., a Korg nanoKONTROL or a Novation Launchpad), at least one DAW (Ableton Live / Logic / Reaper), and a hardware synth or virtual instrument. Verify notes, CCs, program changes, pitch bend, and SysEx pass-through.
- **Art-Net:** a software node/console (QLC+ is the standard free reference), a DMX **visualizer**, and ideally one hardware Art-Net node/gateway. Confirm fixtures respond to output, and that discovery (ArtPoll → ArtPollReply) enumerates real nodes. Validate with Wireshark (`udp.port == 6454`).

### 10.3 Latency & timing (live-performance acceptance criteria)

- Measure end-to-end latency and jitter. **Target: < 10 ms added latency, ideally < 5 ms**, for trigger-style use.
- The MIDI poll interval sets worst-case added input latency (a 3 ms poll ⇒ ≤ ~3 ms added). Because RtMidi queues and timestamps, *throughput* and *message integrity* are independent of poll cadence — only *latency* scales with it. Document the tradeoff and make the poll interval user-tunable.
- For Art-Net, respect the ~44 Hz per-universe refresh ceiling; verify output stays at/under it and that level changes propagate within one frame.
- Stress: MIDI clock at 24 PPQN and high tempo, dense CC streams (e.g., a fader sweep), large OSC bundles, and many Art-Net universes at full refresh. Confirm no drops and bounded latency.
- Honesty note for users: for *tight musical instrument* playing (live keyboard → soft-synth), a polled path adds latency a callback path wouldn't. We document this and keep a future callback-based input path on the roadmap for that specific use case.

### 10.4 Platform matrix

Run the full suite on macOS (x86_64 + arm64), Windows x64, and Linux (x86_64 + arm64/Raspberry Pi), on **both** LiveCode 9.6.3 and OXT Lite. Art-Net (pure LCB) should pass identically everywhere with no per-binary variation.

---

## 11. Showcase deliverables (the marketing payload)

Ship demos that make the community *see themselves* using OXT. Each is a small, beautiful, documented sample stack:

1. **MIDI Learn control surface** — click a UI control, wiggle a hardware knob, it binds. The "wow, that was easy" demo. Directly showcases input + parsing.
2. **TouchOSC bridge** — a phone/tablet running TouchOSC drives an on-screen visual and, in reverse, the LiveCode UI updates TouchOSC. Showcases bidirectional OSC.
3. **Audio/MIDI-reactive Box2D scene** — *compounds the previous win.* Incoming MIDI notes or OSC values spawn/impulse Box2D bodies; a fader controls gravity. One demo that says "OXT does games **and** installations, together."
4. **Cue trigger panel** — a QLab-style grid that fires MIDI/OSC cues, hinting at show-control workflows.
5. **Sound-driven lighting (Art-Net)** — *closes the loop.* A LiveCode control surface drives real or virtual DMX fixtures (into QLC+ or a hardware node), and ties into demo #3 so that **sound and MIDI drive the lights** — the complete "OXT runs the whole show" picture. This is the demo that recruits the lighting/theatrical crowd.

Plus: a written **"OSC, MIDI & Art-Net in LiveCode" tutorial series**, the API reference (Appendix A), and a short demo video. Seed these on the LiveCode/OXT forums, the creative-coding subreddits, and lighting/AV communities.

---

## 12. Milestones & timeline

Estimates assume a 1–2 person team experienced with LCB wrapping (post-Box2D). Durations are elapsed-time ranges; OSC and MIDI tracks parallelize partially. Art-Net is a self-contained fast-follow with no signing dependency, so it can run in parallel or ship just after v1.

| Phase | Deliverable | Effort | Exit criteria |
|---|---|---|---|
| **0. Toolchain spikes** | Build OSC+MIDI shims on all 3 OSes; bind one trivial fn each (`rtmidi_get_port_count`, a fixed OSC message); confirm `Data`↔pointer marshalling | ~1 wk | A `.lce` loads in 9.6.3 **and** OXT, a bound C function returns a correct value on every platform |
| **1. OSC core** | Full build/parse via sockets, indexed arg API, bundles, `oscMatch`, examples | 1–2 wks | TouchOSC ↔ LiveCode round-trips all types; fuzz suite passes under ASan |
| **2. MIDI core** | Enumerate/open/close, send raw + convenience, `midi_in_drain` + poll dispatch, virtual ports, ignore-types | 2–3 wks | Hardware controller drives a UI; DAW receives correct messages; no leaks across open/close |
| **3. Ergonomics & parsing** | MIDI decoder (kind/channel/data + SysEx assembly), shipped dispatcher, OSC array model polish, error surfaces | 1–2 wks | Users write only event handlers; docs drafted |
| **4. Cross-platform hardening** | arm64/universal builds, macOS signing+notarization, Win `.def` discipline, latency/stress tests, RPi check | ~2 wks | Full matrix green; < 10 ms latency target met; notarized macOS artifacts |
| **5. Showcase & release (v1)** | Demo stacks 1–4, tutorial series, API reference, demo video, forum launch | ~2 wks | Public v1 release (OSC + MIDI) + published demos |
| **5.1. Art-Net fast-follow (v1.1)** | Pure-LCB `artnet`: ArtDmx output + input, ArtPoll/Reply discovery, golden-packet tests, sound-driven lighting demo (#5) | ~1–2 wks | Drives a real/virtual Art-Net node; byte-exact packet tests pass; ships as a standalone `.lce`; **no signing dependency** |

**Critical path:** Phase 0 marshalling confirmation → Phase 2 (MIDI is the larger surface) → Phase 4 signing. **Internal alpha** (usable OSC + MIDI, rough edges) lands at the end of Phase 2 (~4–6 weeks). **Polished v1** at the end of Phase 5 (~9–12 weeks). **v1.1 (Art-Net)** ~1–2 weeks beyond that, or sooner if run in parallel since it shares nothing with the native toolchain.

---

## 13. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Calling LCB from a C callback proves necessary and is unstable | Low | High | Architecture avoids it entirely (sockets for OSC/Art-Net, polling+queue for MIDI). Callback path is explicitly out of scope. |
| `Data`↔pointer/byte-buffer marshalling detail differs from assumption | Medium | Medium | Resolve empirically in Phase 0 before committing API shape; isolate in shim so the LCB API is stable regardless. |
| Returning library-owned `const char*` crashes engine (known failure mode) | Known | High | Shims only ever fill caller-allocated buffers or expose alloc/free pairs. Never return library-owned `const char*`. |
| Variadic tinyosc write API unbindable | Known | Medium | Shim replaces varargs with incremental builder (§5.3). |
| **Art-Net mixed-endianness packing error** | Known | Low–Med | Golden-packet fixtures + Wireshark (`udp.port == 6454`) validation; covered by unit tests (§10.1). OpCode is little-endian; version and length are high-byte-first. |
| Polled MIDI input latency too high for tight instrument play | Medium | Medium | Tunable poll interval; document the tradeoff; roadmap a callback-based input path for that niche. |
| macOS notarization friction blocks distribution | Medium | High | Bake signing/notarization into the build from Phase 0; test Gatekeeper early. (Art-Net unaffected — no binary.) |
| Windows symbol-export/name-decoration issues | Medium | Low | `.def` files + `!cdecl`; validate undecorated exports in Phase 0. |
| RtMidi backend quirks (JACK vs ALSA, WinMM COM init) | Medium | Low | Default to ALSA on Linux; document COM init on Windows; test per-backend. |
| Maintaining three extensions across engine updates | Ongoing | Medium | Keep shims tiny and dependency-free; Art-Net has no binary to maintain; pin library versions; CI build matrix. |
| 9.6.3 vs OXT Lite divergence | Low | Medium | Test both from Phase 0; keep install docs per engine. |

Net effect of adding Art-Net: it **lowers** average project risk per feature, since it introduces no native binary, no new platform dependency, and no signing surface.

---

## 14. Licensing & distribution

The upstreams are **permissive or royalty-free** — a genuine selling point versus copyleft alternatives:

- **tinyosc — ISC.** Use for any purpose including commercial/closed-source; retain the copyright/permission notice.
- **RtMidi — modified MIT.** MIT-style, with the courtesy request that modifications be sent upstream. Include the LICENSE file; contribute fixes back (good citizenship and cheap goodwill).
- **Art-Net — royalty-free protocol with an openly published spec (Artistic Licence).** There is **no library to bundle** (pure-LCB implementation), so there is no upstream license to ship. Note: *"Art-Net" is a trademark of Artistic Licence* — implementing the protocol is free, but don't imply endorsement or use the mark as the product name; describe the extension as "Art-Net compatible." For commercial shipping, registering for an OEM/manufacturer code with Artistic Licence is a low-priority nicety, not a requirement.

Recommendations:
- License the **extensions themselves permissively** (MIT) to maximize adoption in the target community and encourage contributed demos.
- Bundle each upstream LICENSE in the OSC and MIDI extension packages and credit them in the docs.
- No GPL/LGPL anywhere in this project — keep it that way as the roadmap grows (watch the sACN follow-on and any v2 audio libs for license traps; prefer permissive equivalents).

---

## 15. Appendix A — consolidated script-facing API

**OSC**
```
oscBuildMessage(pAddress as String, pArgs as List) returns Data
oscBuildBundle(pTimetag as String, pMessages as List) returns Data
oscParse(pData as Data) returns Array
oscMatch(pPattern as String, pAddress as String) returns Boolean
oscLastError() returns String
```

**MIDI**
```
midiInputPorts() returns List
midiOutputPorts() returns List
midiOpenInput(pIndex as Integer) returns Integer
midiOpenOutput(pIndex as Integer) returns Integer
midiOpenVirtualInput(pName as String) returns Integer
midiOpenVirtualOutput(pName as String) returns Integer
midiClose(pHandle as Integer)
midiIgnoreTypes(pHandle as Integer, pSysex as Boolean, pTime as Boolean, pSense as Boolean)
midiSend(pHandle as Integer, pBytes as Data)
midiNoteOn(pHandle, pChannel, pNote, pVelocity)
midiNoteOff(pHandle, pChannel, pNote, pVelocity)
midiControlChange(pHandle, pChannel, pController, pValue)
midiProgramChange(pHandle, pChannel, pProgram)
midiPitchBend(pHandle, pChannel, pValue14bit)
midiChannelPressure(pHandle, pChannel, pValue)
midiPoll(pHandle as Integer) returns List
midiLastError() returns String
```

**Art-Net**
```
artnetBuildDmx(pUniverse as Integer, pChannels as Data) returns Data
artnetParseDmx(pData as Data) returns Array
artnetBuildPoll() returns Data
artnetParseReply(pData as Data) returns Array
artnetSendDmx(pUniverse as Integer, pChannels as Data, pHost as String)   -- optional convenience
artnetLastError() returns String
```

---

## 16. Appendix B — roadmap beyond v1.1

Sequenced by strategic value to the same community:

1. **sACN (E1.31)** — the ANSI-standardized lighting companion to Art-Net; many newer professional rigs prefer it. Same approach (UDP + packet codec, multicast by default), a small increment on the Art-Net scaffolding. The natural first follow-on.
2. **Ableton Link** — network tempo sync; makes OXT a first-class citizen in beat-synced multi-app setups.
3. **MIDI clock / MTC / MMC** — transport sync for show control.
4. **OSC over TCP (SLIP framing)** — for tools that prefer reliable OSC.
5. **Callback-based MIDI input path** — for tight instrument latency, behind the same script API (opt-in).
6. **OSC bundle timetag scheduling** — honor OSC timetags for sample-accurate future dispatch.
7. **Video/texture sharing** — Spout (Windows) / Syphon (macOS) / NDI (network) to round out the AV stack.
8. **Art-Net advanced** — RDM-over-Art-Net (remote device management), ArtSync (synchronized multi-universe output), ArtTimeCode.
9. **MIDI 2.0 / MPE** — expressive controllers, once RtMidi/successors expose it cleanly.
10. **Mobile targets (iOS/Android)** — CoreMIDI/Android MIDI via the same script API, for tablet-based controllers.

Each new protocol reinforces the same thesis: **OXT as the rapid-build hub that talks to everything in an installation or a show.**# ShowControl
