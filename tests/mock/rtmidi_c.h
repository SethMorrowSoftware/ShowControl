/*
 * tests/mock/rtmidi_c.h -- a MOCK of RtMidi's C API, just enough to compile and
 * drive midi_shim.c's OWN logic (the generation-tagged handle table, the batched
 * drain, the per-port stash, and the port-name return-value handling) WITHOUT the
 * real RtMidi.
 *
 * Why this exists: the real midi smoke test (tests/midi_smoke_test.c) links the
 * actual RtMidi and is correct, but on a headless runner it has no way to *inject*
 * inbound messages -- so the trickiest, most regression-prone code in the shim
 * (drain + stash, "never drop a popped message", the record wire-format) goes
 * unexercised there. This mock models a controllable input queue so those paths
 * run deterministically, on every platform, under ASan/UBSan, and even where
 * RtMidi cannot be fetched at all. It is NOT a MIDI backend; it is a test double.
 *
 * It faithfully reproduces the two RtMidi 6.0.0 quirks the shim depends on:
 *   - rtmidi_get_port_name(bufOut != NULL) returns snprintf()'s count (the FULL
 *     name length, excluding the NUL) and does NOT modify *bufLen.
 *   - rtmidi_in_get_message pops destructively; if the message is larger than the
 *     caller buffer it sets *size to the real size and does NOT copy.
 *   - a wrapper with ok == false has a NULL internal object (deref would crash),
 *     and its msg field must never be read (use-after-free upstream).
 */
#ifndef SHOWCONTROL_MOCK_RTMIDI_C_H
#define SHOWCONTROL_MOCK_RTMIDI_C_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* Same values and order as RtMidi 6.0.0's enum. */
enum RtMidiApi {
    RTMIDI_API_UNSPECIFIED, RTMIDI_API_MACOSX_CORE, RTMIDI_API_LINUX_ALSA,
    RTMIDI_API_UNIX_JACK, RTMIDI_API_WINDOWS_MM, RTMIDI_API_RTMIDI_DUMMY,
    RTMIDI_API_WEB_MIDI_API, RTMIDI_API_WINDOWS_UWP, RTMIDI_API_ANDROID, RTMIDI_API_NUM
};

/* Same shape as the real RtMidiWrapper (ptr/data/msg/ok). The shim only reads
 * ->ptr and ->ok, and is documented never to read ->msg. */
struct RtMidiWrapper { void *ptr; void *data; const char *msg; bool ok; };
typedef struct RtMidiWrapper *RtMidiPtr;
typedef struct RtMidiWrapper *RtMidiInPtr;
typedef struct RtMidiWrapper *RtMidiOutPtr;

/* ---- test-side controls (defined in tests/mock/rtmidi_mock.c) ---- */
extern int mock_backend_ok;     /* 0 => create returns a wrapper with ok=false  */
extern int mock_port_count;     /* what get_port_count reports                  */
extern int mock_oversize_next;  /* next get_message reports size > buffer (drop) */
void mock_queue_push(const unsigned char *msg, int len, double delta);
void mock_queue_clear(void);
/* The FFI-facing midimock_* controls (loopback, sent log, ...) are documented in
 * rtmidi_mock.c; C callers may use them via these prototypes. Exported from the
 * mock shared library, so the declaration carries the same linkage (MSVC C2375). */
#if defined(_WIN32)
    #define MIDIMOCK_API __declspec(dllexport)
#else
    #define MIDIMOCK_API __attribute__((visibility("default")))
#endif
MIDIMOCK_API void    midimock_reset(void);
MIDIMOCK_API void    midimock_set_loopback(int32_t on, int32_t delta_us);
MIDIMOCK_API int32_t midimock_sent_count(void);
MIDIMOCK_API int32_t midimock_sent_get(int32_t i, uint8_t *out, int32_t cap);
MIDIMOCK_API int32_t midimock_live_wrappers(void);
MIDIMOCK_API void    midimock_set_api(int32_t api);

/* ---- the subset of the RtMidi C API the shim binds ---- */
RtMidiInPtr  rtmidi_in_create_default(void);
RtMidiOutPtr rtmidi_out_create_default(void);
void         rtmidi_open_port(RtMidiPtr, unsigned int, const char *);
void         rtmidi_open_virtual_port(RtMidiPtr, const char *);
void         rtmidi_close_port(RtMidiPtr);
unsigned int rtmidi_get_port_count(RtMidiPtr);
enum RtMidiApi rtmidi_in_get_current_api(RtMidiPtr);
enum RtMidiApi rtmidi_out_get_current_api(RtMidiPtr);
int          rtmidi_get_port_name(RtMidiPtr, unsigned int, char *, int *);
void         rtmidi_in_ignore_types(RtMidiInPtr, bool, bool, bool);
double       rtmidi_in_get_message(RtMidiInPtr, unsigned char *, size_t *);
int          rtmidi_out_send_message(RtMidiOutPtr, const unsigned char *, int);
void         rtmidi_in_free(RtMidiInPtr);
void         rtmidi_out_free(RtMidiOutPtr);

#endif /* SHOWCONTROL_MOCK_RTMIDI_C_H */
