# Getting Started with ShowControl

This guide takes you from nothing to talking **OSC**, **MIDI**, and **Art-Net**
from **OpenXTalk (OXT)** - or any compatible **LiveCode 9.6.3+** IDE. It assumes
no C toolchain: you install prebuilt extensions (the native libraries are bundled
in), then work through one short, runnable walkthrough per protocol.

ShowControl is **three independent extensions** - install only the ones you need:

| Extension | What it does | Native binary? |
|-----------|--------------|----------------|
| **osc** | build/parse Open Sound Control messages over UDP | yes (bundled) |
| **midi** | enumerate/open ports, send, and poll-receive MIDI | yes (bundled) |
| **artnet** | build/parse Art-Net DMX lighting packets over UDP | **none** (pure LCB) |

- [1. Install the extensions](#1-install-the-extensions)
- [2. Load them while developing](#2-load-them-while-developing)
- [3. Sanity check](#3-sanity-check)
- [4. OSC walkthrough: send and receive a fader](#4-osc-walkthrough-send-and-receive-a-fader)
- [5. MIDI walkthrough: poll a controller](#5-midi-walkthrough-poll-a-controller)
- [6. Art-Net walkthrough: drive a DMX channel](#6-art-net-walkthrough-drive-a-dmx-channel)
- [Troubleshooting](#troubleshooting)
- [Where to go next](#where-to-go-next)

---

## 1. Install the extensions

The osc and midi extensions are native libraries compiled per platform - and each
library ships **inside its extension**, so there's nothing to download, rename, or
place by hand. artnet is pure LCB with no binary at all. Installing is identical
on Windows, macOS, and Linux:

1. In OXT, open **Tools -> Extension Builder** and open the extension's `.lcb`:
   `src/osc/osc.lcb`, `src/midi/midi.lcb`, and/or `src/artnet/artnet.lcb`.
2. Click **Package** to produce the `.lce` (for osc/midi this rolls in the
   per-platform libraries committed under `src/<ext>/code/<arch>-<platform>/`),
   then install that `.lce` via **Tools -> Extension Manager**.

The engine then loads the correct library for your platform automatically - 
**no `/usr/lib`, no `sudo`, no `LD_LIBRARY_PATH`, no renaming.**

> The per-platform libraries under `src/<ext>/code/<arch>-<platform>/` are
> committed (built and tested by CI, attached to each [Release](../../releases)),
> so this is already done in the repo. Prefer building the libraries yourself? See
> [building.md](building.md). artnet has nothing to build.

The three extensions are **independent** (no cross-dependency): install osc alone,
midi alone, artnet alone, or any combination.

## 2. Load them while developing

You don't have to repackage on every edit:

- **Extension Builder -> Test** compiles and loads the `.lcb` in place - for
  osc/midi it reads the `code/` folder beside the `.lcb`, so the native library
  loads too.
- **From script:** `load extension from file (the defaultFolder & "/osc.lcb")`

Foreign bindings resolve on **first use**, so for osc/midi the native library only
has to be in place by the time an `osc...`/`midi...` handler first runs - not when the
extension loads. artnet has no native dependency, so it is always ready once
loaded.

## 3. Sanity check

Open the Message Box and run whichever you installed:

```
put oscLastError() -- empty string = osc loaded, no error yet
put midiInputPorts() -- a (possibly empty) list of port names
put artnetLastError() -- empty string = artnet loaded
```

`midiInputPorts()` returning a list (even an empty one) proves the midi extension
and its native library loaded. If any call throws "handler not found" the
extension isn't loaded; if a first osc/midi call throws "unable to load foreign
library" the extension loaded without its bundled binary - jump to
[Troubleshooting](#troubleshooting).

## 4. OSC walkthrough: send and receive a fader

OSC rides **LiveCode's own UDP sockets**: you write the `Data` that
`oscBuildMessage` returns to a socket, and inbound datagrams arrive in
`socketReceived`, where you call `oscParse`. The extension is a pure codec - it
never touches the network itself.

`oscBuildMessage(pAddress, pArgs)` takes an address string and a list of
`[type, value]` pairs (type codes: `"i"` int32, `"f"` float32, `"s"` string,
`"b"` blob, `"d"` double, `"h"` int64, `"t"` timetag, `"T"/"F"/"N"/"I"` for
true/false/nil/impulse). It returns the OSC datagram as `Data`, ready to send.

**Open a receive socket and react to a fader.** Put this in a card or stack
script. We listen on UDP port 9000 (where, say, TouchOSC sends), and a fader on
`/1/fader1` drives a scrollbar:

```
on openCard
   -- Listen for inbound OSC datagrams on UDP :9000
   accept datagram connections on port 9000 with message "oscDatagramArrived"
end openCard

on oscDatagramArrived pSocket
   read from socket pSocket with message "oscDataReceived"
end oscDatagramArrived

on oscDataReceived pSocket, pData
   put oscParse(pData) into tMsg
   if tMsg["address"] is "/1/fader1" then
      -- args is a 1-based list; the fader value is the first argument
      set the thumbPosition of scrollbar "Volume" to (item 1 of tMsg["args"]) * 100
   end if
   read from socket pSocket with message "oscDataReceived" -- keep listening
end oscDataReceived
```

`oscParse` returns an **Array** with keys `address`, `types` (the type-tag string,
e.g. `"f"`), `args` (a 1-based list of decoded values), and `isBundle`
(`false` here). For a bundle, `isBundle` is `true` and you read `timetag` and
`messages` (a list of sub-message Arrays) instead. The full shape is in the
[API reference](api-reference.md#osc).

**Send a message** - drive a Resolume layer's opacity when a button is clicked:

```
on mouseUp
   put oscBuildMessage("/composition/layers/1/video/opacity/values", \
        [["f", 0.75]]) into tData
   write tData to socket "127.0.0.1:7000"
end mouseUp
```

That's the whole OSC loop: build `Data`, write it to `host:port`; receive `Data`
in your socket handler, `oscParse` it. To match an incoming address against a
pattern with wildcards (`? * [ ] { }`), use
`oscMatch(pPattern, pAddress)` -> Boolean.

> **Round-trip check.** With no external app, send to yourself: open the receive
> socket above, then run
> `write oscBuildMessage("/1/fader1", [["f", 0.5]]) to socket "127.0.0.1:9000"`
> in the Message Box and watch the scrollbar jump to mid-travel.

## 5. MIDI walkthrough: poll a controller

MIDI inbound is **not** pushed to you - there is no callback (see
[architecture.md](architecture.md#the-decisive-rule-never-call-lcb-from-a-c-callback)).
RtMidi buffers and timestamps inbound messages in an internal queue, and you
**drain that queue by polling** on a timer. `midiPoll(pHandle)` does one batched
read and returns a list of decoded event records; you run a small loop that calls
it and dispatches.

**Enumerate ports.** `midiInputPorts()` / `midiOutputPorts()` return lists of
port names; the **position in the list, minus 1, is the index** you open
(0-based):

```
put midiInputPorts()
-- e.g. nanoKONTROL2 SLIDER/KNOB
-- Launchpad Mini MIDI 1
```

**Open an input and start a poll loop.** Index 0 is the first port in the list:

```
local sMidiIn

on openCard
   put midiOpenInput(0) into sMidiIn -- open the first input port
   if sMidiIn is 0 then
      answer "Could not open MIDI input:" && midiLastError()
      exit openCard
   end if
   midiPollLoop -- start draining the queue
end openCard

on midiPollLoop
   put midiPoll(sMidiIn) into tEvents
   repeat for each element tEvent in tEvents
      switch tEvent["kind"]
         case "noteOn"
            put "Note" && tEvent["data1"] && "vel" && tEvent["data2"] \
                 && "ch" && tEvent["channel"] & return after field "Log"
            break
         case "controlChange"
            if tEvent["data1"] is 7 then -- CC 7 = channel volume
               set the thumbPosition of scrollbar "Master" to tEvent["data2"]
            end if
            break
      end switch
   end repeat
   send "midiPollLoop" to me in 3 milliseconds -- ~3 ms added input latency
end midiPollLoop

on closeCard
   midiClose(sMidiIn) -- always close; drop the handle
end closeCard
```

Each `midiPoll` record is an Array with `delta` (seconds since the previous
message), `bytes` (the raw `Data`), `kind` (`"noteOn"`, `"noteOff"`,
`"controlChange"`, `"programChange"`, `"pitchBend"`, `"channelPressure"`, ...), and
the decoded fields `channel` (**1-based**, 1-16), `data1`, and `data2`. The full
record shape is in the [API reference](api-reference.md#midi). Because RtMidi
buffers between polls, a slow or jittery tick never drops a message - only
worst-case latency scales with the 3 ms interval, so that number is your latency
knob.

> ShowControl ships a ready-made **poll dispatcher** in `midi.livecodescript`
> that wraps this loop and `send`s a semantic message per event, so you can write
> only event handlers. The loop above is the same idea inlined, shown so you can
> see exactly what it does.

**Send MIDI** - open an output and play a note (channels are **1-based**):

```
local sMidiOut

on playChord
   put midiOpenOutput(0) into sMidiOut
   midiNoteOn sMidiOut, 1, 60, 100 -- ch1, middle C, velocity 100
   midiNoteOn sMidiOut, 1, 64, 100
   midiNoteOn sMidiOut, 1, 67, 100
   send "stopChord" to me in 500 milliseconds
end playChord

on stopChord
   midiNoteOff sMidiOut, 1, 60, 0
   midiNoteOff sMidiOut, 1, 64, 0
   midiNoteOff sMidiOut, 1, 67, 0
   midiClose sMidiOut
end stopChord
```

The convenience senders (`midiNoteOn`/`midiNoteOff`/`midiControlChange`/
`midiProgramChange`/`midiPitchBend`/`midiChannelPressure`) build correct status
bytes for you; `midiSend(pHandle, pBytes)` sends raw `Data` when you need full
control. No hardware? On macOS open the **IAC Driver** bus, on Linux use an ALSA
virtual port, on Windows install **loopMIDI**, or open a **virtual** port with
`midiOpenVirtualOutput("ShowControl Out")` and connect to it from a DAW.

## 6. Art-Net walkthrough: drive a DMX channel

Art-Net is DMX512 lighting control over UDP on port **6454**. It is the same
socket-codec shape as OSC - build a `Data`, write it to `host:6454` - but pure
LCB with no native binary. A **universe** is up to **512 channels**, one byte
each (0-255).

`artnetBuildDmx(pUniverse, pChannels)` takes a universe number and up to 512 bytes
of channel data (`Data`) and returns a ready-to-send ArtDmx datagram.

**Drive channel 1 (a dimmer) of universe 0 on a hardware node:**

```
on faderChanged
   -- Build a 512-byte universe; set channel 1 from a 0-255 fader.
   put the thumbPosition of me into tLevel -- assume a 0-255 fader
   put numToByte(tLevel) into tChannels -- channel 1 = byte 1
   write artnetBuildDmx(0, tChannels) to socket "2.0.0.10:6454"
end faderChanged
```

Honor the conventions: cap each universe at **~44 Hz** (the DMX512 frame-rate
ceiling - don't blast frames faster), and prefer **unicast** to a node's IP over
broadcast above ~30 universes. A common pattern is a repeating timer that writes
the current universe state at a fixed ~40 Hz rather than on every fader move.

**Receive DMX** (e.g. from a console) - `artnetParseDmx` returns an Array with
`opcode`, `universe`, `sequence`, `length`, and `channels` (the `Data`):

```
on dmxArrived pSocket, pData
   put artnetParseDmx(pData) into tDmx
   if tDmx["opcode"] is "ArtDmx" and tDmx["universe"] is 0 then
      set the backgroundColor of graphic "Lamp" to \
         (byteToNum(byte 1 of tDmx["channels"]) & "," & \
          byteToNum(byte 2 of tDmx["channels"]) & "," & \
          byteToNum(byte 3 of tDmx["channels"]))
   end if
   read from socket pSocket with message "dmxArrived"
end dmxArrived
```

**Discover nodes** - broadcast an `artnetBuildPoll()` and read the
`artnetParseReply` Arrays that come back:

```
on discoverNodes
   write artnetBuildPoll() to socket "255.255.255.255:6454" -- broadcast
end discoverNodes

on replyArrived pSocket, pData
   put artnetParseReply(pData) into tNode
   if tNode["opcode"] is "ArtPollReply" then
      put tNode["longName"] && "(" & tNode["ip"] & ")" & return after field "Nodes"
   end if
   read from socket pSocket with message "replyArrived"
end replyArrived
```

If you exposed the optional `artnetSendDmx(pUniverse, pChannels, pHost)`
convenience, it wraps build-plus-write in one call. See the
[API reference](api-reference.md#art-net) for the full Art-Net surface.

## Troubleshooting

| Symptom | Likely cause & fix |
|---------|--------------------|
| Any `osc...`/`midi...`/`artnet...` call throws "handler not found" | The extension isn't loaded. Re-add and **Load** its `.lcb` in the Extension Manager. |
| First `osc...`/`midi...` call errors **"unable to load foreign library"** | The osc/midi extension loaded without its bundled library. Install the **packaged** extension (Extension Builder -> **Package** the `.lcb` -> install the `.lce`), or **Test** it with the `code/` folder beside the `.lcb`. (Tip: launch OXT from a terminal - it prints the library name it tried to load.) artnet never shows this (it has no binary). |
| A build/parse handler returns empty / `0` | The input was rejected. Read `oscLastError()` / `midiLastError()` / `artnetLastError()` for the reason - malformed datagram, bad handle, or out-of-range argument. |
| `oscParse` returns empty on a real datagram | The bytes weren't a valid OSC packet (e.g. you read a partial datagram). Ensure your socket handler reads whole datagrams; check `oscLastError()`. |
| `midiInputPorts()` is empty but a device is plugged in | The OS MIDI service may not see it yet (replug, or check the OS MIDI setup). On Linux confirm ALSA sees it (`aconnect -l`). |
| `midiPoll` returns nothing though notes are playing | You opened the wrong port index, or didn't keep the poll timer running. Re-check the index from `midiInputPorts()` and that `midiPollLoop` re-`send`s itself. |
| MIDI input feels laggy | Lower the poll interval (e.g. 3 ms -> 1 ms). Throughput is unaffected by cadence; only added latency scales with it. |
| An Art-Net node ignores your packets | Almost always the ArtDmx **mixed-endianness** wire format or wrong universe/IP. Validate with Wireshark (`udp.port == 6454`); confirm the universe and that you're unicasting to the node's real IP. |

## Where to go next

- [**API Reference**](api-reference.md) - every public handler for all three
  extensions, with signatures, the `oscParse` Array and `midiPoll` record shapes,
  failure behavior, and units/conventions.
- [**Architecture**](architecture.md) - the three layers, why the shims exist,
  the no-callback rule, and the sockets-vs-polling inbound patterns.
- [**Building**](building.md) - compile the osc/midi native libraries yourself,
  run the C smoke tests, and package each extension.
