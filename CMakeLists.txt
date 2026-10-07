cmake_minimum_required(VERSION 3.22)
project(showcontrol LANGUAGES C CXX)

# ---------------------------------------------------------------------------
# ShowControl native shims for OpenXTalk (OXT) / xTalk, also compatible with
# LiveCode 9.6.3+. Two loadable libraries are produced:
#   osc  -- osc_shim.c + vendored tinyosc (ISC)            -> osc.{so,dll,dylib}
#   midi -- midi_shim.c + RtMidi (fetched)                 -> midi.{so,dll,dylib}
# The Art-Net extension is PURE LCB and has no native build step.
#
# Each library is bundled INSIDE its extension under
#   src/<ext>/code/<arch>-<platform>/<ext>.{so,dll,dylib}   (bare token name)
# so the engine resolves the c:osc> / c:midi> FFI bindings via revLibraryMapping.
#
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DSHOWCONTROL_BUILD_TESTS=ON
#   cmake --build build --config Release
#   ctest --test-dir build --output-on-failure
# ---------------------------------------------------------------------------

find_package(Threads REQUIRED)

# RtMidi builds as a static lib we link into the midi shared library, so every
# object must be position-independent (same rule Box2Dxt applies to Box2D).
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(SHOWCONTROL_BUILD_TESTS "Build the C smoke tests" OFF)
option(SHOWCONTROL_BUILD_OSC   "Build the OSC shim"  ON)
option(SHOWCONTROL_BUILD_MIDI  "Build the MIDI shim" ON)

if(SHOWCONTROL_BUILD_TESTS)
    enable_testing()
endif()

# ===========================================================================
# OSC: our shim + vendored tinyosc, one shared library named "osc".
# ===========================================================================
if(SHOWCONTROL_BUILD_OSC)
    add_library(osc SHARED
        src/osc/osc_shim.c
        src/third_party/tinyosc/tinyosc.c)
    target_include_directories(osc PRIVATE src/osc src/third_party/tinyosc)

    # tinyosc's <endian.h> use (htobe64/be64toh) needs a feature-test macro on
    # glibc; without it the build fails with "implicit declaration". Harmless and
    # required wherever tinyosc is compiled.
    target_compile_definitions(osc PRIVATE _DEFAULT_SOURCE)

    set_target_properties(osc PROPERTIES
        C_STANDARD 17
        C_STANDARD_REQUIRED ON
        C_VISIBILITY_PRESET hidden     # export only the OSC_API symbols
        OUTPUT_NAME "osc"
        PREFIX "")                     # emit osc.so/.dylib (bare token, matches c:osc>)

    # tinyosc uses ntohl/htonl/ntohll/htonll; on Windows those live in winsock2 /
    # ws2_32, so the link fails without it. (No-op on Linux/macOS.)
    if(WIN32)
        target_link_libraries(osc PRIVATE ws2_32)
    endif()

    # Keep our shim warning-clean; tinyosc's own (vendored) warnings are not fatal
    # (no -Werror), exactly as Box2Dxt treats Box2D.
    if(MSVC)
        target_compile_options(osc PRIVATE /W3)
    else()
        target_compile_options(osc PRIVATE -Wall -Wextra)
        # silence the vendored file's upstream warnings without hiding ours
        set_source_files_properties(src/third_party/tinyosc/tinyosc.c
            PROPERTIES COMPILE_OPTIONS "-w")
    endif()

    if(SHOWCONTROL_BUILD_TESTS)
        add_executable(osc_smoke tests/osc_smoke_test.c)
        target_include_directories(osc_smoke PRIVATE src/osc)
        target_link_libraries(osc_smoke PRIVATE osc)
        if(NOT MSVC)
            target_link_libraries(osc_smoke PRIVATE m)
        endif()
        add_test(NAME osc_smoke COMMAND osc_smoke)

        # The shared wire-format vectors (tests/vectors/osc.json, projected into
        # the committed tests/vectors/vectors.h by tools/gen_vectors.py).
        add_executable(osc_vectors tests/osc_vectors_test.c)
        target_include_directories(osc_vectors PRIVATE src/osc tests)
        target_link_libraries(osc_vectors PRIVATE osc)
        if(NOT MSVC)
            target_link_libraries(osc_vectors PRIVATE m)
        endif()
        add_test(NAME osc_vectors COMMAND osc_vectors)

        # Deterministic mutation fuzzer over osc_parse + every getter, seeded from
        # the vectors (tests/fuzz/osc_fuzz.c). Under the ASan/UBSan CI build every
        # one of these iterations is sanitizer-checked; the coverage-guided
        # libFuzzer build of the same file runs in the CI "fuzz" job.
        add_executable(osc_fuzz tests/fuzz/osc_fuzz.c)
        target_include_directories(osc_fuzz PRIVATE src/osc tests)
        target_link_libraries(osc_fuzz PRIVATE osc)
        add_test(NAME osc_fuzz COMMAND osc_fuzz 100000 1)
    endif()
endif()

# ===========================================================================
# MIDI: our shim + RtMidi (fetched, pinned), one shared library named "midi".
# RtMidi selects its backend automatically: ALSA on Linux (needs libasound2-dev),
# CoreMIDI on macOS, WinMM on Windows.
# ===========================================================================
if(SHOWCONTROL_BUILD_MIDI)
    include(FetchContent)
    FetchContent_Declare(
        rtmidi
        GIT_REPOSITORY https://github.com/thestk/rtmidi.git
        GIT_TAG        6.0.0)

    # Build RtMidi as a static lib we embed; no tests/python.
    set(RTMIDI_BUILD_STATIC_LIBS ON  CACHE BOOL "" FORCE)
    set(RTMIDI_BUILD_TESTING     OFF CACHE BOOL "" FORCE)
    set(RTMIDI_BUILD_PYTHON      OFF CACHE BOOL "" FORCE)
    set(BUILD_SHARED_LIBS        OFF)
    FetchContent_MakeAvailable(rtmidi)

    add_library(midi SHARED src/midi/midi_shim.c)
    target_include_directories(midi PRIVATE src/midi ${rtmidi_SOURCE_DIR})
    # RtMidi exports its target as rtmidi (and an alias RtMidi::rtmidi on newer tags).
    if(TARGET RtMidi::rtmidi)
        target_link_libraries(midi PRIVATE RtMidi::rtmidi Threads::Threads)
    else()
        target_link_libraries(midi PRIVATE rtmidi Threads::Threads)
    endif()

    set_target_properties(midi PROPERTIES
        C_STANDARD 17
        C_STANDARD_REQUIRED ON
        C_VISIBILITY_PRESET hidden
        OUTPUT_NAME "midi"
        PREFIX "")

    if(MSVC)
        target_compile_options(midi PRIVATE /W3)
    else()
        target_compile_options(midi PRIVATE -Wall -Wextra)
    endif()

    if(SHOWCONTROL_BUILD_TESTS)
        add_executable(midi_smoke tests/midi_smoke_test.c)
        target_include_directories(midi_smoke PRIVATE src/midi)
        target_link_libraries(midi_smoke PRIVATE midi)
        add_test(NAME midi_smoke COMMAND midi_smoke)
    endif()
endif()

# ===========================================================================
# Mock-RtMidi smoke test: compiles the MIDI shim against a controllable RtMidi
# mock (tests/mock/rtmidi_c.h) so the drain / stash / handle-table logic runs
# DETERMINISTICALLY -- on every platform, under sanitizers, and even where the
# real RtMidi cannot be fetched (no network) or no MIDI backend exists. It does
# what the hardware-dependent midi_smoke test cannot: inject inbound messages and
# assert the batched-drain wire format and the "never drop a popped message"
# stash. Independent of SHOWCONTROL_BUILD_MIDI (it never touches real RtMidi).
# ===========================================================================
if(SHOWCONTROL_BUILD_TESTS)
    add_executable(midi_mock_smoke
        src/midi/midi_shim.c
        tests/mock/rtmidi_mock.c
        tests/midi_mock_smoke_test.c)
    # tests/mock FIRST so the shim's #include "rtmidi_c.h" resolves to the mock.
    target_include_directories(midi_mock_smoke PRIVATE tests/mock src/midi)
    set_target_properties(midi_mock_smoke PROPERTIES C_STANDARD 17 C_STANDARD_REQUIRED ON)
    if(MSVC)
        target_compile_options(midi_mock_smoke PRIVATE /W3)
    else()
        target_compile_options(midi_mock_smoke PRIVATE -Wall -Wextra)
    endif()
    add_test(NAME midi_mock_smoke COMMAND midi_mock_smoke)

    # -----------------------------------------------------------------------
    # The MOCK midi shared library: the real midi_shim.c over the RtMidi test
    # double, built under the SAME bare name "midi" but into <build>/mock/, so it
    # is a drop-in for the shipped library. tools/run-lcb-tests.py stages it for
    # the headless LCB suite: midi.lcb's "c:midi>" bindings then run the whole
    # MIDI stack (binding + FFI marshalling + shim) end to end with no hardware
    # and no OS MIDI service. It also exports the midimock_* controls (inject
    # inbound, loopback cable, sent log) that tests/lcb/midi_test.lcb binds.
    # NEVER packaged: package-extension.py only ever copies the real "midi".
    # -----------------------------------------------------------------------
    add_library(midi_mock SHARED
        src/midi/midi_shim.c
        tests/mock/rtmidi_mock.c)
    target_include_directories(midi_mock PRIVATE tests/mock src/midi)
    set_target_properties(midi_mock PROPERTIES
        C_STANDARD 17
        C_STANDARD_REQUIRED ON
        C_VISIBILITY_PRESET hidden
        OUTPUT_NAME "midi"
        PREFIX ""
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mock"
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mock"
        ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/mock")
    # Multi-config generators (MSVC, Xcode) append /<Config>; pin every config to
    # the same folder so the runner finds it without guessing.
    foreach(cfg Debug Release RelWithDebInfo MinSizeRel)
        string(TOUPPER ${cfg} CFG)
        set_target_properties(midi_mock PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY_${CFG} "${CMAKE_BINARY_DIR}/mock"
            RUNTIME_OUTPUT_DIRECTORY_${CFG} "${CMAKE_BINARY_DIR}/mock"
            ARCHIVE_OUTPUT_DIRECTORY_${CFG} "${CMAKE_BINARY_DIR}/mock")
    endforeach()
    if(MSVC)
        target_compile_options(midi_mock PRIVATE /W3)
    else()
        target_compile_options(midi_mock PRIVATE -Wall -Wextra)
    endif()
endif()

# Build:
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build --config Release
