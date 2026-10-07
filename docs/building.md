# Building ShowControl from source

You only need to build if you want fresh native libraries for the **osc** and
**midi** extensions (or are porting them to a new platform/architecture). Most
users can skip this entirely - the per-platform libraries are committed inside
each extension (`src/<ext>/code/<arch>-<platform>/`) and attached to each
[Release](../../releases). The **artnet** extension is pure LCB and has **nothing
to build at all**.

- [What actually builds](#what-actually-builds)
- [Prerequisites](#prerequisites)
- [Build](#build)
- [Run the C tests](#run-the-c-tests)
- [Output files](#output-files)
- [Refreshing the committed binaries](#refreshing-the-committed-binaries)
- [Packaging each extension into a .lce](#packaging-each-extension-into-a-lce)
- [Platform & CPU notes](#platform--cpu-notes)
- [Art-Net: no native build](#art-net-no-native-build)
- [Continuous integration](#continuous-integration)

---

## What actually builds

ShowControl is three extensions, but only two have a native build step:

| Extension | Native library | Upstream | Built by CMake? |
|-----------|----------------|----------|-----------------|
| **osc** | `osc.{so,dll,dylib}` | tinyosc (vendored, ISC) | yes |
| **midi** | `midi.{so,dll,dylib}` | RtMidi (FetchContent, pinned) | yes |
| **artnet** | none | none (pure protocol) | **no** |

A single **`CMakeLists.txt`** at the repo root drives both native builds. tinyosc
is **vendored** at `src/third_party/tinyosc` (no download). RtMidi is fetched and
pinned via CMake **FetchContent** (so you do not download it separately, but you
do need network access on the first configure). Art-Net is exempt from everything
in this document except [packaging](#packaging-each-extension-into-a-lce).

## Prerequisites

- A **C and C++ toolchain** (GCC, Clang, or MSVC). The OSC shim and tinyosc are
  C; the MIDI shim links the C++ RtMidi sources, so a C++ compiler is required.
- **CMake 3.22 or newer**.
- **Git** - CMake's `FetchContent` uses it to download RtMidi.
- **Linux only: ALSA development headers** - `libasound2-dev` (Debian/Ubuntu) or
  `alsa-lib-devel` (Fedora). RtMidi's ALSA backend needs them; without them the
  `midi` configure step fails. The `osc` and `artnet` extensions do not need
  ALSA.
- To *use* the result: **OpenXTalk**, or **LiveCode 9.6.3+** (the FFI and
  extension tooling are present in Community 9.6.3).

Desktop targets: Windows, macOS, Linux (including arm64 / Raspberry Pi - a real
OXT installation audience).

## Build

From the project root (which contains `CMakeLists.txt`):

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

This compiles `src/osc/osc_shim.c` together with `src/third_party/tinyosc/tinyosc.c`
into the `osc` library, and `src/midi/midi_shim.c` together with the fetched
RtMidi sources into the `midi` library. Build once per platform/architecture you
ship.

> **Build note - tinyosc and `<endian.h>`.** tinyosc uses `htobe64` / `be64toh`
> from glibc's `<endian.h>`, which are only exposed when the `_DEFAULT_SOURCE`
> feature-test macro is defined. The `CMakeLists.txt` defines `_DEFAULT_SOURCE`
> for the tinyosc/osc translation units, so the OSC byte-swapping for 64-bit
> ints and timetags compiles cleanly on Linux. If you build the shim by hand
> outside CMake, pass `-D_DEFAULT_SOURCE` or you will get implicit-declaration
> errors for `htobe64`.

RtMidi selects its backend by preprocessor define and links the platform MIDI
service; `CMakeLists.txt` sets these per platform:

| Platform | RtMidi define | Link |
|----------|---------------|------|
| macOS | `__MACOSX_CORE__` | `-framework CoreMIDI -framework CoreAudio -framework CoreFoundation` |
| Linux (ALSA) | `__LINUX_ALSA__` | `-lasound -lpthread` |
| Windows | `__WINDOWS_MM__` | `winmm` |

## Run the C tests

Two self-contained runtime smoke tests drive the shims through the **same C entry
points the LCB binding calls**. Enable them with `-DSHOWCONTROL_BUILD_TESTS=ON`
and run via `ctest`:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
cmake --build build --config Release
ctest --test-dir build --output-on-failure
```

- [`tests/osc_smoke_test.c`](../tests/osc_smoke_test.c) round-trips every OSC type
  through `osc_build_*` -> `osc_parse`, exercises bundles, address matching, and
  the int64-as-decimal-string path, and **fuzzes malformed datagrams** (truncated,
  no-comma type tag, oversized blob, unterminated address) to prove they fail
  cleanly rather than crash. Build it with AddressSanitizer to make the
  bounds-checking guarantee meaningful:

  ```sh
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DSHOWCONTROL_BUILD_TESTS=ON \
        -DCMAKE_C_FLAGS="-fsanitize=address"
  cmake --build build
  ctest --test-dir build --output-on-failure
  ```

- [`tests/midi_smoke_test.c`](../tests/midi_smoke_test.c) is written to pass in
  **both** environments: a runner **with** a working backend (ALSA seq / CoreMIDI
  / WinMM) opens virtual ports, sends, drains, and closes for real; a **headless**
  runner with no backend must fail *cleanly* (error set, `0` returned) and never
  crash. Either way it exercises the stale/`0`-handle no-op guards. A bare CI Linux
  runner without a sound server still passes - that is by design.

A green check means the binaries actually *work* on this platform/architecture - 
something a compile check alone can't show.

## Output files

The build produces (up to) two shared libraries:

| Extension | Linux | macOS | Windows |
|-----------|-------|-------|---------|
| **osc** | `osc.so` | `osc.dylib` | `osc.dll` |
| **midi** | `midi.so` | `midi.dylib` | `midi.dll` |

You don't deploy these by hand. Each ships as a LiveCode/OpenXTalk **extension
with its native library bundled inside it**, under
`src/<ext>/code/<arch>-<platform>/`. Those libraries are **committed** (built and
tested by CI, attached to each Release), so a fresh clone is already a
ready-to-build extension.

| Platform-id (`<arch>-<platform>`) | osc bundled file | midi bundled file |
|-----------------------------------|------------------|-------------------|
| `x86_64-linux` | `src/osc/code/x86_64-linux/osc.so` | `src/midi/code/x86_64-linux/midi.so` |
| `arm64-linux` | `src/osc/code/arm64-linux/osc.so` | `src/midi/code/arm64-linux/midi.so` |
| `x86-linux` | `src/osc/code/x86-linux/osc.so` | `src/midi/code/x86-linux/midi.so` |
| `x86_64-win32` | `src/osc/code/x86_64-win32/osc.dll` | `src/midi/code/x86_64-win32/midi.dll` |
| `x86-win32` | `src/osc/code/x86-win32/osc.dll` | `src/midi/code/x86-win32/midi.dll` |
| `universal-mac` | `src/osc/code/universal-mac/osc.dylib` | `src/midi/code/universal-mac/midi.dylib` |

The architecture comes **first** (`x86_64-linux`, not `linux-x86_64`); Windows
uses `-win32` for both bitnesses; the file is the bare token `osc.<ext>` /
`midi.<ext>` (no `lib` prefix) so it matches the `c:osc>` / `c:midi>` FFI binding
names in the `.lcb`. Installing the extension makes the engine load the right
library for the running platform automatically (via `the revLibraryMapping`) on
Windows, macOS, and Linux, on both LiveCode Community 9.6.3 and OpenXTalk (incl.
OXT Lite) - **no library download, no renaming, no sudo, no `/usr/lib`, no
`LD_LIBRARY_PATH`**.

## Refreshing the committed binaries

The committed `code/` libraries are what every user installs, so they are built,
verified and landed by one workflow --
[`.github/workflows/release-binaries.yml`](../.github/workflows/release-binaries.yml),
the xtalk-suite's model -- and checked on every push by the freshness gate.

**To refresh:** Actions -> *release binaries* -> *Run workflow* on your branch.
Every platform builds and tests in its own lane, stages its libraries into
`src/<ext>/code/<platform-id>/`, and verifies them; the commit job then reruns the
same verifier over the whole bundle and commits to the branch (`commit_mode:
branch`), opens a PR (`pr`), or just publishes the bundle (`none`). It commits
**only** when a person dispatches it -- automatic workflows never commit binaries.
A pull request that touches the shims runs every lane too (build + verify, no
commit), so the bundle is always one click away.

| Lane | How | Portability contract (asserted at birth) |
|---|---|---|
| `x86_64-linux`, `arm64-linux` | `manylinux_2_28` container | glibc <= 2.28; libstdc++/libgcc static; only system libraries (+ `libasound.so.2`) |
| `x86-linux` | stock runner, `-m32` | floor reported, not enforced |
| `x86_64-win32`, `x86-win32` | MSVC, **static CRT** | imports only `KERNEL32` / `WINMM` / `WS2_32` -- no VC++ redistributable |
| `universal-mac` | `arm64;x86_64`, deployment target 10.13 | BOTH slices required; the x86_64 slice is executed under Rosetta |

Every lane, and the commit job, verifies with the tools below -- the same ones
you run by hand:

```sh
python3 tools/check-binary-freshness.py          # the committed tree: is every library current + portable?
python3 tools/install-release-binaries.py <dir>  # land a downloaded bundle (commit_mode: none), verified first
python3 tools/package-extension.py --check       # list the slots, verify each MANIFEST.sha256
python3 tools/package-extension.py --platform-id x86_64-linux --build-dir build   # stage one build
```

`check-binary-freshness.py` reads the ELF / PE / Mach-O files directly (no
toolchain needed) and fails on: a library whose format or architecture does not
match its directory (a thin dylib under `universal-mac` included); a `binds to
"c:<ext>>..."` symbol the library does not export (a load failure); any export
beyond the `<ext>_*` ABI (version scripts / export lists keep RtMidi and the C++
runtime out of the engine's process); the MOCK midi library; an
`<ext>_abi_version()` -- **decoded from its machine code**, every slice -- that
differs from the header (and, where the host can load it, the value it returns
when called); non-system dependencies or a glibc floor above 2.28; a
`MANIFEST.sha256` that does not match the tree; an empty platform slot. Each
`code/` tree's `MANIFEST.sha256` is maintained by `package-extension.py` and the
installer.

## Packaging each extension into a .lce

Each extension is its own `.lcb` plus (for osc/midi) its `code/` tree. To package:

1. In OXT, open **Tools -> Extension Builder** and open the extension's `.lcb`
   (`src/osc/osc.lcb`, `src/midi/midi.lcb`, or `src/artnet/artnet.lcb`).
2. Click **Package** to produce the `.lce` (`osc.lce` / `midi.lce` /
   `artnet.lce`). For osc and midi this rolls the per-platform `code/` libraries
   in; for artnet there are no libraries to roll.
3. Install the `.lce` via **Tools -> Extension Manager** - or click **Test** to
   compile and load in place (it reads the `code/` folder beside the `.lcb`, so
   the native library loads too).

Keep the three extensions **independent** - no cross-dependency - so a user can
install any one without the others. Art-Net is the lightest to install (a single
`.lce`, no bundled binaries). When you build a **standalone**, the Standalone
Builder bundles each installed extension's matching `code/` library automatically.

## Platform & CPU notes

- **Linux: ALSA is the default backend.** Install `libasound2-dev` before
  configuring the `midi` build (the `osc` build has no such dependency). RtMidi's
  JACK backend (`__UNIX_JACK__`, `-ljack`) is optional and off by default; ALSA
  is the safe default for installations and Raspberry Pi rigs.

- **macOS universal binary (osc + midi).** Build both architectures at once so a
  single `universal-mac` dylib serves Apple Silicon and Intel:

  ```sh
  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"
  cmake --build build --config Release
  ```

- **macOS signing & notarization (osc + midi only - do not skip).** The osc and
  midi extensions ship **native dylibs**, so for macOS distribution those dylibs
  (and any standalone embedding them) must be **code-signed and notarized** or
  Gatekeeper will block them. Bake signing/notarization into the build now;
  retrofitting it late is painful. Test against Gatekeeper early. **Art-Net has
  no native binary and is therefore exempt** - one reason it can ship on its own
  schedule.

- **32-bit Windows: `.def` exports.** cdecl exports can be decorated; if a symbol
  fails to bind on `x86-win32`, export the `osc_*` / `midi_*` names via a `.def`
  file listing the unadorned symbol names (LCB requires undecorated exports on
  Windows). 64-bit (`x86_64-win32`) builds need no workaround. Note the WinMM COM
  caveat: with some Windows MIDI targets the thread using RtMidi must call
  `CoInitializeEx` / `CoUninitialize`; document this for the WinMM path.

- **Linux/Windows have no Gatekeeper-equivalent hard gate**, though Windows
  SmartScreen reputation is worth considering for shipped standalones.

- **Symbol naming.** The exported C ABI symbols use the stable `osc_*` / `midi_*`
  prefixes. This is intentional - the binding strings in each `.lcb` reference
  these names, so never rename them.

## Art-Net: no native build

The **artnet** extension is **pure LCB** - there is nothing to compile, no shared
library to ship, no `.def` files, no framework linking, and **no code signing or
notarization**. It is not part of the CMake build, the C test suite, the
`code/<arch>-<platform>/` trees, or `package-extension.py`. It packages as a
single `.lce` that works everywhere the engine runs, and can be built, tested,
and released independently of the OSC/MIDI native toolchain. The only correctness
risk is the wire format itself - the ArtDmx **mixed endianness** (OpCode
little-endian; protocol-version and Length big-endian / high byte first) - which
is covered by byte-exact golden-packet fixtures and validated against Wireshark
(`udp.port == 6454`), not by anything in this build.

## Continuous integration

`.github/workflows/build.yml` builds and tests the **osc** and **midi** libraries
on native **Linux**, **macOS** (universal arm64 + x86_64), and **Windows** runners
on every push and pull request, enabling `-DSHOWCONTROL_BUILD_TESTS=ON` so `ctest`
runs the OSC and MIDI smoke tests on each (the MIDI test passes headless by
design; the RtMidi-free `midi_mock_smoke` exercises the drain/stash logic too). A
separate gate also builds the C smoke tests under AddressSanitizer +
UndefinedBehaviorSanitizer + `float-cast-overflow` with `-fno-sanitize-recover=all`,
so any memory or UB error fails the build rather than only printing.

The `build` workflow's builds are fast feedback only -- stock runners give
non-portable binaries (glibc 2.39, dynamic runtimes), so they are never what
ships. The committed libraries come from `release-binaries.yml`
([Refreshing the committed binaries](#refreshing-the-committed-binaries)), and the
`test` workflow runs the headless suites against those committed files too.

When a GitHub [Release](../../releases) is **published** (or via a manual
`workflow_dispatch` with a `release_tag`), CI runs the freshness gate on the
tagged tree and attaches the **committed** `osc` and `midi` libraries (as
`<ext>-<platform-id>.<suffix>`) plus both manifests -- byte-for-byte the files an
installed extension carries. Note: pushing a bare `vX.Y.Z` tag does **not** trigger
a build; publish a Release (or run the dispatch). Run `package-extension.py --check`
locally to validate the committed `code/` trees before tagging. The release
lanes cover all six platform ids. **artnet**, being pure LCB, needs no build matrix
and produces identical results everywhere; it is validated by its golden-packet
tests rather than the native CI.

See [architecture.md](architecture.md) for how the layers fit together and
[getting-started.md](getting-started.md) to install and use the extensions.
