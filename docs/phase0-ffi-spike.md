# Phase 0: the `Data` <-> pointer FFI spike

This is the single empirical unknown the whole project is designed around. Run it
**first**, in the target engine, before trusting the byte-buffer paths in
`osc.lcb` / `midi.lcb`. Everything else (the C shims, their tests, the Art-Net
wire format) is already verified; this confirms how a LiveCode `Data` crosses the
foreign-function boundary in *your* engine build.

- [Why this exists](#why-this-exists)
- [What we believe is true](#what-we-believe-is-true)
- [The spike](#the-spike)
- [Interpreting the result](#interpreting-the-result)
- [The fallback: hex transport](#the-fallback-hex-transport)
- [Where the marshalling lives](#where-the-marshalling-lives)

---

## Why this exists

ShowControl's prior sibling, **Box2Dxt**, only ever passed scalars and integer
handles across the FFI. It never marshalled a byte buffer. OSC, MIDI and Art-Net
are byte-buffer-heavy, so ShowControl is the first time the team crosses a
`Data`, and the LiveCode Builder language reference documents the high-level
`Data` type ("passes an `MCDataRef`") but does **not** spell out how `Data`
bridges to a foreign `Pointer` parameter, nor how to read raw bytes back.

The C shims sidestep the ambiguity by speaking only the lowest common denominator
- `Pointer` + `CInt` length, never `MCDataRef`. So this spike is purely about the
**LCB side**: does passing a `Data` to a `Pointer` parameter hand the shim a
pointer to the bytes, and can a pre-sized `Data` be filled in place?

## What we believe is true

From the LCB language reference, community FFI threads, and the proven Box2Dxt
string bindings (`dlopen`/`dlerror`/`realpath` use `ZStringNative` and
`Pointer`):

1. **Strings cross cleanly.** `ZStringUTF8` in (LCB `String` -> C `const char*`)
   and out (C `const char*` -> LCB `String`) both work; Box2Dxt ships this.
   ShowControl uses it for addresses, type tags, port names, errors, and
   64-bit-as-decimal values.
2. **An inbound `Data` passed to a `Pointer` parameter bridges to a pointer to
   its first byte.** (Community-reported; this is item (a) below.)
3. **An outbound buffer is a pre-sized `Data` passed as `Pointer`**, which the
   shim fills in place; the script then reads `byte 1 to len`. A freshly built,
   uniquely-owned `Data` should be safe to fill. (This is item (b) - the least
   certain, because LCB `Data` is nominally immutable/copy-on-write.)

The spike confirms (2) and (3) directly.

## The spike

A throwaway extension with one tiny C function. Build the C as a shared library
named `scspike` (bare token), bundle it, and run the `.lcb` from a button.

**`spike.c`** (compile to `scspike.{so,dll,dylib}`, `PREFIX ""`):

```c
#include <stdint.h>
#include <string.h>
#if defined(_WIN32)
  #define API __declspec(dllexport)
#else
  #define API __attribute__((visibility("default")))
#endif

/* (a) IN: sum the bytes the engine hands us -> proves Data bridged to a real
   pointer over a real length. */
API int32_t spike_sum(const uint8_t *p, int32_t len) {
    int32_t s = 0;
    for (int32_t i = 0; i < len; i++) s += p[i];
    return s;
}

/* (b) OUT: fill the caller buffer 0,1,2,... and return how many we wrote.
   Proves a pre-sized Data can be filled in place. */
API int32_t spike_fill(uint8_t *out, int32_t cap) {
    if (!out || cap <= 0) return -1;
    for (int32_t i = 0; i < cap; i++) out[i] = (uint8_t)i;
    return cap;
}
```

**`spike.lcb`:**

```
library org.openxtalk.library.scspike
use com.livecode.foreign

private foreign handler _sum(in pData as Pointer, in pLen as CInt) returns CInt \
   binds to "c:scspike>spike_sum!cdecl"
private foreign handler _fill(in pOut as Pointer, in pCap as CInt) returns CInt \
   binds to "c:scspike>spike_fill!cdecl"

-- (a) inbound Data -> pointer
public handler spikeSum(in pData as Data) returns Integer
   variable tR as Integer
   unsafe
      put _sum(pData, the number of bytes in pData) into tR
   end unsafe
   return tR
end handler

-- (b) pre-sized Data filled in place, then read back
public handler spikeFill(in pCap as Integer) returns Data
   variable tBuf as Data
   put the empty data into tBuf
   variable tZero as Data
   put numToByte(0) into tZero
   variable i as Integer
   repeat with i from 1 up to pCap
      put tZero after tBuf
   end repeat
   variable tWrote as Integer
   unsafe
      put _fill(tBuf, pCap) into tWrote
   end unsafe
   return byte 1 to tWrote of tBuf
end handler
```

**Drive it from script:**

```
-- (a) expect 1+2+3 = 6
put spikeSum(numToByte(1) & numToByte(2) & numToByte(3))   --> 6

-- (b) expect 00 01 02 03 04 (5 bytes counting up)
put spikeFill(5) into tD
repeat with i = 1 to the number of bytes in tD
   put byteToNum(byte i of tD) & " " after tResult
end repeat
put tResult                                                 --> 0 1 2 3 4
```

## Interpreting the result

| Result | Meaning | Action |
|---|---|---|
| `spikeSum` returns `6` **and** `spikeFill(5)` returns `0 1 2 3 4` | Both directions work as designed | Nothing - the `osc`/`midi` bindings are correct as written |
| `spikeSum` is `6` but `spikeFill` is all zeros / garbage | inbound bridge works; **in-place fill does not** | Switch the out direction to the hex fallback below (only `makeBuffer`/`*_finish`/`midiPoll` change) |
| `spikeSum` is wrong / throws | the inbound `Pointer` bridge differs in this engine | Use the hex fallback for **both** directions |

Confirm `numToByte` / `byteToNum` resolve in LCB on your engine while you are
here (add `use com.livecode.byte` if they need a module import). They are the
only non-FFI primitives the byte paths depend on.

## The fallback: hex transport

If the `Pointer`/`Data` bridge does not behave, fall back to transporting bytes
as **hex strings over `ZStringUTF8`** - which is pure ASCII (no embedded NULs)
and uses the exact string path Box2Dxt already proves. It costs an encode/decode
and ~2x size, negligible at OSC/MIDI/Art-Net rates.

Add two helpers to each shim:

```c
/* bytes -> lowercase hex (caller buffer needs 2*len+1) */
API int32_t  xxx_to_hex(const uint8_t *in, int32_t len, char *out, int32_t cap);
/* hex string -> bytes (caller buffer needs len/2); returns bytes written */
API int32_t  xxx_from_hex(const char *hex, uint8_t *out, int32_t cap);
```

Then the `.lcb` builds/parses via hex strings instead of `Pointer` buffers:
`oscParse` would call a `..._to_hex` returning `ZStringUTF8`, and decode in
script; `oscBuildMessage` would hand the finished bytes back as hex and the
script converts once before `write ... to socket`. The C shims and their tests do
not change - only the LCB transport helpers do.

## Where the marshalling lives

The byte-buffer marshalling is deliberately isolated to a handful of LCB handlers
so this decision touches as little as possible:

- `src/osc/osc.lcb`: `makeBuffer`, `finishToData`, `readBlob`, and the `_parse`
  input call.
- `src/midi/midi.lcb`: `ensureDrainBuf` and the `_in_drain` call in `midiPoll`.
- `src/artnet/artnet.lcb`: **pure LCB, no FFI at all** - it builds `Data` with
  `numToByte` and reads it with `byteToNum`, so it is unaffected by this spike and
  is a good first thing to get running in OXT.

Everything else - the entire C ABI, every smoke-test assertion, the Art-Net
golden packets - is already verified and independent of how this resolves.
