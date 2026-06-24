# Testing ShowControl in OXT without hardware

You can validate almost the entire stack inside OpenXTalk with **no controllers,
DAWs, or DMX nodes** — by looping each extension's output back through its own
input. This is the fastest way to confirm the extensions loaded, the FFI
marshalling works in your engine, and the codecs are correct, *before* you wire up
real gear.

Two ready-to-run pieces ship in [`examples/`](../examples):

| File | What it does |
|------|--------------|
| [`selftest.livecodescript`](../examples/selftest.livecodescript) | An **automated** pass/fail self-test across all three extensions. Run it, read the tally. |
| [`loopback-monitor.livecodescript`](../examples/loopback-monitor.livecodescript) | An **interactive, visual** monitor: a self-building "virtual DMX rig" that lights up from looped-back Art-Net, plus an OSC echo. |

- [The three tiers of confidence](#the-three-tiers-of-confidence)
- [Run the automated self-test](#run-the-automated-self-test)
- [Run the visual loopback monitor](#run-the-visual-loopback-monitor)
- [It also confirms the Phase-0 FFI spike](#it-also-confirms-the-phase-0-ffi-spike)
- [Full MIDI round-trip without hardware](#full-midi-round-trip-without-hardware)
- [What this does and does not cover](#what-this-does-and-does-not-cover)

---

## The three tiers of confidence

The self-test is organized so a failure tells you *where* the problem is:

1. **Tier 1 — pure `build → parse` (no sockets, no hardware).** Round-trips every
   OSC type, a bundle, `oscMatch`, the Art-Net packets (with byte-exact endianness
   checks), and the MIDI decoder. This is the strongest, most deterministic tier:
   if it passes, the extensions loaded **and** `Data` crosses the FFI correctly in
   your engine.
2. **Tier 2 — UDP loopback to `127.0.0.1` (no hardware).** Sends OSC and Art-Net to
   the engine's own datagram socket and parses what comes back. This adds the
   engine's socket path on top of Tier 1.
3. **Tier 3 — MIDI ports (no hardware).** Enumeration, virtual-port lifecycle,
   out-of-range error handling, and the full decode path via the `midiDecode`
   handler. (A *full* MIDI message round-trip needs an OS software loopback —
   [see below](#full-midi-round-trip-without-hardware) — still no hardware.)

## Run the automated self-test

1. Install or **Test**-load the `osc`, `midi`, and `artnet` extensions
   (see [getting-started.md](getting-started.md)).
2. Paste [`examples/selftest.livecodescript`](../examples/selftest.livecodescript)
   into a stack or card script.
3. In the **Message Box**, run:

   ```
   put runShowControlSelfTest()
   ```

You get a per-check `[PASS]`/`[FAIL]` log and a tally (also mirrored into a
`STResults` field if a card is open). Tier 1 and Tier 3 are synchronous; the Tier 2
loopback results append a moment later as the datagrams arrive.

Expected on a typical desktop: all Tier 1 + Tier 3 checks pass; the two Tier 2
loopback lines report PASS. On **Windows**, the virtual-MIDI-output check reports
"unavailable" instead of failing — WinMM has no virtual ports, which is correct.

> The self-test prints small diagnostics (e.g. the element count of a parsed `args`
> list). If an `args[n]` access ever reads oddly in your engine build, those lines
> reveal how a returned LCB `List` actually marshals into Script — useful, not a
> failure.

## Run the visual loopback monitor

1. Install/Test-load the `osc` and `artnet` extensions.
2. Paste [`examples/loopback-monitor.livecodescript`](../examples/loopback-monitor.livecodescript)
   into a **stack script**, open the stack, and run in the Message Box:

   ```
   scStartMonitor
   ```

It builds its own UI (16 fixture rectangles, a Sweep button, an OSC echo field) and
opens a loopback socket. Click **Sweep** and the fixtures animate — each frame is a
real `artnetBuildDmx` packet sent to `127.0.0.1`, received, `artnetParseDmx`-d, and
the parsed channel levels repaint the rectangles. Type into the field and click
**Send OSC** to echo a message to yourself and see it logged. Run `scStopMonitor`
to tear down. This is the "watch it work" companion to the pass/fail self-test, and
a handy DMX visualizer you can point at real incoming frames later.

## It also confirms the Phase-0 FFI spike

ShowControl's one empirical unknown is how a LiveCode `Data` crosses the foreign
boundary in your engine build (see [phase0-ffi-spike.md](phase0-ffi-spike.md)).
**Tier 1 of the self-test is a more thorough version of that spike**: `oscParse`
passes a `Data` *in* to the shim as a pointer, and `oscBuildMessage` reads a
shim-filled `Data` back *out* — both directions, for every type, plus the Art-Net
pure-LCB byte path. If Tier 1 is green, the `Data ⇄ pointer` marshalling the rest
of the library depends on is confirmed for your engine. (If it is not, the toy
spike isolates the failure to the three marshalling helpers, and the hex-transport
fallback in that doc applies.)

## Full MIDI round-trip without hardware

MIDI is the one protocol the engine doesn't transport itself, so a *full* inbound
round-trip needs a **software MIDI loopback** — still no physical device, just an
OS-level virtual cable that connects an output back to an input:

| OS | Loopback |
|----|----------|
| **macOS** | Enable the **IAC Driver** in *Audio MIDI Setup → MIDI Studio* (double-click "IAC Driver", tick *Device is online*). |
| **Linux** | ALSA sequencer virtual ports; connect them with `aconnect` (`aconnect -l` to list, `aconnect <out> <in>` to wire). |
| **Windows** | Install **loopMIDI** (Tobias Erichsen) and create a port. |

Then: `midiOpenOutput` the loopback port, `midiOpenInput` it as well, run the poll
loop (or the dispatcher in
[`showcontrol-helpers.livecodescript`](../examples/showcontrol-helpers.livecodescript)),
`midiSend` a note, and watch `midiPoll` decode it back. Without a loopback you can
still fully test the **decode** logic via `midiDecode` (Tier 3 does), and confirm
enumeration, open/close, and error handling.

## What this does and does not cover

**Covered, no hardware:** extension loading; `Data ⇄ pointer` FFI marshalling; every
OSC type + bundles + pattern matching; Art-Net build/parse with exact wire bytes;
the MIDI decoder; MIDI enumeration / lifecycle / errors; UDP transport via loopback;
and (with a software loopback) a full MIDI message round-trip.

**Still needs real gear (the interop gate, README §"Status"):** that a *third-party*
app or device accepts our packets and that we accept theirs — TouchOSC, a DAW, a
hardware controller, a DMX node/console, QLC+, and a Wireshark check on
`udp.port == 6454`. Loopback proves we are self-consistent; interop proves we match
everyone else. Do the loopback tests first; they catch the overwhelming majority of
issues before any cabling.
