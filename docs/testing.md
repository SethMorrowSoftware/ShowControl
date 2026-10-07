# Testing ShowControl — automated, headless, no hardware

ShowControl is tested end to end **without any MIDI devices, DMX nodes, OSC
controllers, DAWs or display**: the C shims, the LCB bindings, the LiveCode
Script layer, real UDP through the engine's sockets, and interop with an
independent implementation on the other end of the wire. Everything below runs
in CI on Linux, Windows and macOS ([`.github/workflows/test.yml`](../.github/workflows/test.yml))
and on a developer machine with one OXT folder.

- [The layers](#the-layers)
- [What you need](#what-you-need)
- [Running everything](#running-everything)
- [The LCB suite (lc-run)](#the-lcb-suite-lc-run)
- [The LiveCode Script suite (the engine, -ui)](#the-livecode-script-suite-the-engine--ui)
- [MIDI without hardware: the mock library](#midi-without-hardware-the-mock-library)
- [OSC and Art-Net without hardware: the virtual peers](#osc-and-art-net-without-hardware-the-virtual-peers)
- [The shared wire-format vectors](#the-shared-wire-format-vectors)
- [Fuzzing](#fuzzing)
- [Writing a new test](#writing-a-new-test)
- [Engine facts the suites rely on](#engine-facts-the-suites-rely-on)

## The layers

| Layer | Runner | What it proves | Hardware |
|---|---|---|---|
| C shims | `ctest` (+ ASan/UBSan in CI) | ABI, handle safety, drain/stash, every OSC type, malformed input | none |
| Wire formats | `tests/vectors_reference_test.py` | the shared vectors are right — against clean-room spec codecs **and** python-osc | none |
| Fuzzing | `osc_fuzz` (ctest) / libFuzzer (CI) | `osc_parse` + every getter survive hostile datagrams | none |
| **LCB bindings** | `tools/run-lcb-tests.py` | each `.lcb` **compiles** (lc-compile) and **runs** (lc-run): FFI marshalling, every public handler, the vectors | none (MIDI via the mock) |
| **LiveCode Script** | `tools/run-lcs-tests.py` | every `.livecodescript` compiles **in the engine**; the Script↔LCB boundary; the shipped helpers and self-test | none (MIDI via the mock) |
| **UDP** | `tests/lcs/udp_test` | real datagrams through the engine's own sockets | none (loopback) |
| **Interop** | `tests/lcs/interop_test` | an independent implementation (`tools/sim/`) agrees with ShowControl over real UDP | none (virtual peers) |
| Static gates | `tools/check-livecodescript.py` | the cheap known mistakes, in a second, with no OXT | none |

What still needs real gear is the last mile: that a *specific* product
(TouchOSC, QLab, a DMX node, a console) accepts our packets. The interop suite
makes that low-risk — the peers are written from the specs and share no code
with ShowControl — but it is not a substitute for a Wireshark check on the
first real rig ([`testing-in-oxt.md`](testing-in-oxt.md)).

## What you need

- **Python 3.8+** (standard library only; `pip install python-osc` adds the
  third-party OSC cross-check).
- **CMake 3.22+ and a C/C++ compiler** for the shims (`libasound2-dev` on Linux).
- **An OXT folder with `lc-compile`, `lc-run` and the standalone engine.** Any
  OXT / LiveCode build folder works (`win-x86_64-bin`, `linux-x86_64-bin`,
  `_build/mac/Release`), or download one: every OXT-Beyond release attaches
  `OXT-Beyond-<ver>-<platform>-binaries.{tar.xz,zip}`, which is exactly that
  folder. CI uses `v0.2.3`. Point the runners at it with `--oxt-bin <folder>`
  or `OXT_BIN=<folder>`.

> **Use an OXT-Beyond engine ≥ 0.2.1-rc.2 for the socket suites on Windows.**
> Older Windows engines deliver *no* socket events without a UI (and older
> macOS ones delay them), so `udp_test` / `interop_test` cannot run there. The
> runner probes this and skips them with a loud note — and **fails** under
> `--require-all`, which CI uses.

## Running everything

One command runs every layer in order and prints a summary (the two OXT layers
are reported NOT RUN, never passed, without an OXT folder):

```sh
python3 tools/test-all.py --oxt-bin /path/to/oxt/bin        # add --skip-build to reuse ./build
```

Or layer by layer:

```sh
# native shims + the mock MIDI library + the C tests
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --build-config Release --output-on-failure --no-tests=error

# wire formats, the virtual peers, the static gates
python3 tests/vectors_reference_test.py
python3 tests/simulators_test.py
python3 tests/checker_fixtures_test.py && python3 tools/check-livecodescript.py

# the bindings under lc-run, then the script layer in the engine
python3 tools/run-lcb-tests.py --oxt-bin /path/to/oxt/bin --build-dir build
python3 tools/run-lcs-tests.py --oxt-bin /path/to/oxt/bin --build-dir build
```

Both runners print one line per test handler (`PASS`/`FAIL`/`SKIP`, assertion
count, time), the failing assertions with what they saw, and a summary; they
exit non-zero on any failure. Useful flags: `--filter REGEX` (on
`suite::Handler`), `-v` (all diagnostics), `--require-all` (a skipped suite is a
failure), `--min-assertions N` (a floor, so a suite that silently stops running
cannot pass).

## The LCB suite (lc-run)

`lc-run` is the LCB virtual machine as a console program. It has the full
foreign-function interface and the engine's `<builtin>` helpers, but not the
engine/canvas/widget modules — which the bindings do not use. So the bindings
can be compiled and run exactly as the IDE would, minus a window.

`tools/run-lcb-tests.py`:

1. compiles `src/{artnet,osc,midi}/*.lcb` with `lc-compile` — a real compile
   gate (the `.lci` files go into the work dir via `--interface`, never into
   the OXT install);
2. generates a test module from `tests/vectors/*.json`;
3. compiles `tests/lcb/_*.lcb` (support libraries) and `tests/lcb/*_test.lcb`;
4. stages the native libraries each module asks for (`-- requires:` header
   line: `osc`, `midi-mock`, `midi-real`) under the bare binding name, because
   `lc-run` has no `revLibraryMapping` — `c:osc>` is a raw `dlopen("osc")`
   (POSIX: no suffix added, so the file is literally named `osc`) or
   `LoadLibrary("osc")` (Windows: `.dll` appended);
5. runs every `public handler Test*()` in its own `lc-run` process — a crash or
   an uncaught LCB error fails that one handler — and parses the TAP that
   `com.livecode.unittest` prints (`test "..." when <bool>`).

Suites: `artnet_test`, `osc_test`, `midi_test` (mock), `midi_hw_test` (the real
library on whatever machine runs it: ABI, enumeration, clean failures), and the
generated `vectors_test`.

## The LiveCode Script suite (the engine, -ui)

What `lc-run` cannot reach is the layer users touch: LiveCode Script calling the
extensions, the shipped helper and example scripts, and the engine's sockets.
`tools/run-lcs-tests.py` runs those in the **standalone engine with `-ui`** (no
window, no X display):

1. **An in-engine compile gate.** Each `.livecodescript` in `examples/` and
   `tests/lcs/` is compiled by the engine itself, one engine per file. A Script
   compile error is otherwise *silent*: `set the script` leaves `the result`
   empty and a broken script-only stack simply never opens. The gate counts
   `the revAvailableHandlers` and, on failure, names the first handler that
   fails to compile on its own.
2. **A socket probe**: can this engine deliver UDP to itself without a UI?
3. **Each test handler in its own engine.** The runner stack
   ([`tests/lcs/_runner.livecodescript`](../tests/lcs/_runner.livecodescript))
   builds the packaged-extension layout (`<ext>/module.lcm` +
   `<ext>/code/<platform-id>/<lib>`), maps the libraries into
   `the revLibraryMapping` **the way the IDE does** (so the run also proves the
   committed `code/<platform-id>/` naming is what the engine looks for), loads
   the extensions, hosts `examples/showcontrol-helpers.livecodescript` as a
   library, then dispatches `TestSetup`, the handler and `TestTearDown`.
   Assertions (`TestAssert`, `TestAssertEqual`, `TestSkip`, `TestDiagnostic`,
   `TestWaitFor`, `TestPump`) print TAP; the engine quits with its failure count.

Parameters travel in `SC_*` environment variables, never on the command line:
the engine parses flags (`-ui`, `-f`, …) **anywhere** in argv.

Test stacks declare their needs on header lines:

```
-- needs: sockets       skip unless the socket probe passed
-- peers: osc artnet    start the virtual peers; ports in $SC_OSC_PEER_PORT / $SC_ARTNET_PEER_PORT
-- midi: mock | real    which midi library to install (default mock)
```

Suites: `boundary_test` (Script↔LCB), `helpers_test` (the MIDI dispatcher on a
real timer, OSC routing), `selftest_test` (runs the shipped
[`examples/selftest.livecodescript`](../examples/selftest.livecodescript) and
requires 0 failures), `udp_test` and `interop_test` (sockets).

## MIDI without hardware: the mock library

`tests/mock/rtmidi_mock.c` is a controllable stand-in for RtMidi's C API. CMake
compiles it with the **real** `src/midi/midi_shim.c` into a second library named
`midi` under `build/mock/` — a drop-in for the shipped one. The suites install
it in place of the real library, so the binding, the FFI marshalling and the
shim's handle table, batched drain and stash all run for real. On top of the
`midi_*` ABI it exports test controls (bound in
[`tests/lcb/_midimock.lcb`](../tests/lcb/_midimock.lcb) and callable from LCB
**and** Script):

| Control | Effect |
|---|---|
| `MockPush pBytes, pDeltaUs` | inject one inbound message |
| `MockSetLoopback true, pDeltaUs` | a virtual MIDI cable: every send arrives as inbound |
| `MockSent(i)` / `MockSentCount()` | the exact bytes each send produced |
| `MockSetPortCount n`, `MockSetBackendOk false`, `MockSetApi 4` | enumeration, "no MIDI service", a WinMM-like backend |
| `MockSetOversizeNext true`, `MockSetSendFail true` | the drop and failure paths |
| `MockLiveWrappers()` | RtMidi instances alive — a leak check for `midiClose` |

The mock is never shipped: the release jobs exclude `*/mock/*`, and
`tools/package-extension.py` refuses any binary that exports `midimock_*`.

## OSC and Art-Net without hardware: the virtual peers

[`tools/sim/`](../tools/sim) plays the other end of the wire with clean-room
codecs ([`refcodec.py`](../tools/sim/refcodec.py)) that share no code with
ShowControl:

```sh
python3 tools/sim/artnet_node.py --port 16454      # a virtual DMX node: answers ArtPoll,
                                                   # prints each ArtDmx as a level meter
python3 tools/sim/artnet_node.py --echo 1          # ...and echoes frames on universe + 1
python3 tools/sim/osc_peer.py --port 9000 --ack    # an OSC peer that replies with what it understood
python3 tools/sim/osc_peer.py --send 127.0.0.1:8000 /1/fader1 f 0.5   # fire one message at OXT
```

They are what the interop suite talks to, and they are handy by hand: point your
stack at `127.0.0.1` and watch exactly what goes out. `tests/simulators_test.py`
proves the peers themselves against the vectors first — a fake peer that is
wrong would let interop pass while lying.

## The shared wire-format vectors

[`tests/vectors/`](../tests/vectors) holds **one** source of truth for the wire
formats — OSC messages/bundles/patterns/malformed datagrams, Art-Net
ArtDmx/ArtPoll/ArtPollReply, MIDI decode/encode — asserted by three
implementations that share nothing:

- `tests/vectors_reference_test.py` against the clean-room codecs (and the two
  OSC 1.0 specification examples, transcribed from the spec), plus python-osc;
- C, through `tests/vectors/vectors.h` (generated by
  [`tools/gen_vectors.py`](../tools/gen_vectors.py), committed so the C build
  needs no Python; `gen_vectors.py --check` gates freshness);
- LCB, through a module the LCB runner generates at test time.

Add a case to the JSON, run `python3 tools/gen_vectors.py --write`, and all
three suites check it.

## Fuzzing

`osc_parse` is the one entry point that eats untrusted network bytes.
[`tests/fuzz/osc_fuzz.c`](../tests/fuzz/osc_fuzz.c) walks every getter of every
successful parse, and builds two ways:

- **standalone** (any compiler, in ctest as `osc_fuzz`): a deterministic
  mutation fuzzer seeded from the vectors — same seed, same inputs.
  `osc_fuzz [iterations] [seed]`; `OSC_FUZZ_DUMP_AT=N` writes iteration N's
  input to a file; `osc_fuzz --replay FILE` reruns one input (also libFuzzer
  crash files). Under the ASan/UBSan CI build every iteration is checked.
- **libFuzzer** (clang, the CI `fuzz` job): coverage-guided, ASan + UBSan,
  seeded with `python3 tools/gen_vectors.py --corpus DIR`.

It earned its keep on its first run: a `,` hidden in the address padding made
tinyosc's cursor disagree with the shim's validated layout — an out-of-bounds
read from one 72-byte datagram (fixed; pinned as a parse-only vector).

## Writing a new test

**LCB** — add a `public handler TestSomething()` to a `tests/lcb/*_test.lcb`
module (or a new module with a `-- requires:` line):

```
public handler TestSomething()
   variable tData as Data
   put oscBuildMessage("/x", ["i", "1"]) into tData
   test "built 12 bytes" when the number of bytes in tData is 12
   test diagnostic DataToHex(tData)
end handler
```

`_support.lcb` gives you `HexToData`, `DataToHex`, `RepeatByte`, `RepeatChar`,
`NearlyEqual`, `Show`. Declare every variable at the top of the handler.

**LiveCode Script** — add `on TestSomething` to a `tests/lcs/*_test.livecodescript`
stack (first line `script "Name"`):

```
on TestSomething
   local tArgs
   scAddArg tArgs, "i", 1
   TestAssertEqual "built 12 bytes", the number of bytes in oscBuildMessage("/x", tArgs), 12
end TestSomething
```

Async (sockets, timers): poll a flag with `TestWaitFor(the long id of me,
"myFlagFunction", 3000)` or service events with `TestPump 200`. Use ports from
`TestPortBase() + n` and close what you open.

## Engine facts the suites rely on

Each was observed on a real engine (and, where noted, read in its source). They
are the reason several shipped bugs existed — and now are pinned by tests.

| Fact | Consequence |
|---|---|
| A `Data` does **not** bridge to a foreign `Pointer` | pass `MCDataGetBytePtr(d)`; fill `MCMemoryAllocate` blocks and copy back with `MCDataCreateWithBytes` |
| A returned `ZString*` is **freed** by the engine (`module-foreign.cpp`) | never return a C string from a foreign handler; use caller buffers |
| `is a number` / `is a string` / `is a data` and `and` / `or` exist in LCB (9.6.3 floor too) | normalize `any` values; no need for nested-`if` workarounds |
| A datagram message is called with `(sender "host:port", data, socket)` (`opensslsocket.cpp`) | `on myMsg pSender, pData`; no `read from socket` needed |
| Writing to a socket that was never **opened** sends nothing; the result is `socket is not open` (`exec-network.cpp`) | `open datagram socket to "h:p"` first (`scEnsureDatagramSocket`) |
| `open datagram socket ... with message M`: `M` is the **asynchronous open-complete** callback | for replies on an opened socket, open plainly, then `read from socket S with message M` (per-datagram for UDP) |
| `send "msg a b c"` parses parameters on **commas** | `dispatch "msg" to tObj with a, b, c` |
| In a `send ... in N ms` message, `the target` is the sender itself | remember the listener explicitly |
| Reading an **unmapped** `revLibraryMapping[key]` throws; the un-keyed property is not readable | just set it; map paths with **forward slashes** |
| `does not contain` does not parse — and a broken script-only stack fails **silently** | `not (a contains b)`; the in-engine compile gate catches the rest |
| `repeat with i = 1 to 9 step 3` **overshoots** (visits 10) | loop an index, compute the value |
| Engine paths are MAX_PATH-bound on Windows | the LCS runner stages scripts into its short work dir |
| Windows engines < OXT-Beyond 0.2.1-rc.2 deliver no socket events with `-ui` | use a newer engine for the socket suites |
