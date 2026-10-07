/*
 * tests/mock/rtmidi_mock.c -- the controllable RtMidi test double behind
 * tests/mock/rtmidi_c.h. It is compiled into two things:
 *
 *   1. midi_mock_smoke (C): midi_shim.c + this file, driven from C through the
 *      mock_* globals/functions.
 *   2. the MOCK midi shared library (CMake target midi_mock, OUTPUT_NAME "midi",
 *      emitted under <build>/mock/): midi_shim.c + this file, a drop-in for the
 *      real midi.{so,dll,dylib}. The headless LCB suite (tests/lcb/midi_test.lcb,
 *      run by tools/run-lcb-tests.py under lc-run) loads it in place of the real
 *      library, so the WHOLE MIDI stack -- the .lcb binding, the FFI marshalling,
 *      the shim's handle table / drain / stash -- runs end to end with NO MIDI
 *      hardware, no OS MIDI service, and no loopMIDI/IAC/aconnect.
 *
 * Beyond injecting inbound messages it models a VIRTUAL CABLE: with loopback on,
 * every message an output port sends is queued as inbound for the input side, so
 * midiNoteOn -> midiPoll round-trips exactly as it would through a software MIDI
 * loopback. Every send is also recorded in a log, so a test can assert the exact
 * bytes the convenience senders produced (status nibble, 7-bit clamping, ...).
 *
 * The midimock_* functions are the FFI-facing controls (exported, cdecl, scalar
 * and pointer+length arguments only) -- LCB binds them as "c:midi>midimock_*".
 * They exist ONLY in the mock library; the shipped midi library never has them.
 *
 * Single-threaded by design (the LCB caller is single-threaded), like the shim.
 * NOT a MIDI backend: a test double that reproduces the RtMidi 6.0.0 quirks the
 * shim depends on (see rtmidi_c.h).
 */
#include "rtmidi_c.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- knobs (also read/written directly by the C smoke test) ---- */
int mock_backend_ok    = 1;
int mock_port_count    = 2;
int mock_oversize_next = 0;

static int    g_loopback       = 0;     /* outbound sends re-enter as inbound */
static double g_loopback_delta = 0.0;   /* delta (s) stamped on looped-back msgs */
static int    g_live_wrappers  = 0;     /* created - freed: a leak check for close */
static int    g_send_fail      = 0;     /* make the next sends fail (error path) */
/* Which backend the mock impersonates. ALSA by default (virtual ports work);
 * set to RTMIDI_API_WINDOWS_MM to exercise the "no virtual ports" refusal. */
static enum RtMidiApi g_api    = RTMIDI_API_LINUX_ALSA;

/* ---- inbound FIFO (heap-backed; a popped slot's bytes are freed) ---- */
#define QCAP 4096
typedef struct { unsigned char *b; int len; double delta; } qmsg;
static qmsg q[QCAP];
static int  q_head = 0, q_tail = 0;

static int q_depth(void) { return (q_tail - q_head + QCAP) % QCAP; }

void mock_queue_clear(void) {
    while (q_head != q_tail) { free(q[q_head].b); q[q_head].b = NULL; q_head = (q_head + 1) % QCAP; }
    q_head = q_tail = 0;
}
static int q_push(const unsigned char *msg, int len, double delta) {
    if (len < 0) return 0;
    if (q_depth() == QCAP - 1) return 0;                /* full: refuse, never overwrite */
    unsigned char *b = (unsigned char *) malloc((size_t)(len > 0 ? len : 1));
    if (!b) return 0;
    if (len > 0) memcpy(b, msg, (size_t) len);
    q[q_tail].b = b; q[q_tail].len = len; q[q_tail].delta = delta;
    q_tail = (q_tail + 1) % QCAP;
    return 1;
}
void mock_queue_push(const unsigned char *msg, int len, double delta) { (void) q_push(msg, len, delta); }

/* ---- sent-message log ---- */
#define SENTCAP 4096
typedef struct { unsigned char *b; int len; } smsg;
static smsg sent[SENTCAP];
static int  sent_count = 0;

static void sent_clear(void) {
    for (int i = 0; i < sent_count; i++) { free(sent[i].b); sent[i].b = NULL; }
    sent_count = 0;
}
static void sent_record(const unsigned char *data, int len) {
    if (sent_count >= SENTCAP || len <= 0) return;
    unsigned char *b = (unsigned char *) malloc((size_t) len);
    if (!b) return;
    memcpy(b, data, (size_t) len);
    sent[sent_count].b = b; sent[sent_count].len = len; sent_count++;
}

/* ===================== the RtMidi C API subset ===================== */
static struct RtMidiWrapper *make_wrapper(void) {
    struct RtMidiWrapper *w = (struct RtMidiWrapper *) calloc(1, sizeof *w);
    if (!w) return NULL;
    w->ok  = mock_backend_ok ? true : false;
    w->ptr = mock_backend_ok ? (void *) w : NULL;   /* NULL internal object when !ok */
    g_live_wrappers++;
    return w;
}
RtMidiInPtr  rtmidi_in_create_default(void)  { return make_wrapper(); }
RtMidiOutPtr rtmidi_out_create_default(void) { return make_wrapper(); }
void rtmidi_open_port(RtMidiPtr d, unsigned int p, const char *n) { (void)d; (void)p; (void)n; }
void rtmidi_open_virtual_port(RtMidiPtr d, const char *n) { (void)d; (void)n; }
void rtmidi_close_port(RtMidiPtr d) { (void)d; }
unsigned int rtmidi_get_port_count(RtMidiPtr d) { (void)d; return (unsigned) mock_port_count; }
enum RtMidiApi rtmidi_in_get_current_api(RtMidiPtr d)  { (void)d; return g_api; }
enum RtMidiApi rtmidi_out_get_current_api(RtMidiPtr d) { (void)d; return g_api; }

/* RtMidi 6.0.0: with bufOut != NULL returns snprintf's count (full length), never
 * writes *bufLen. The shim must trust the RETURN value, not *bufLen. */
int rtmidi_get_port_name(RtMidiPtr d, unsigned int p, char *buf, int *buflen) {
    (void)d;
    char name[64];
    snprintf(name, sizeof name, "MockPort-%u-aLongishName", p);
    if (buf && buflen && *buflen > 0) return snprintf(buf, (size_t) *buflen, "%s", name);
    return (int) strlen(name);
}
void rtmidi_in_ignore_types(RtMidiInPtr d, bool a, bool b, bool c) { (void)d; (void)a; (void)b; (void)c; }

double rtmidi_in_get_message(RtMidiInPtr d, unsigned char *message, size_t *size) {
    (void)d;
    if (q_head == q_tail) { *size = 0; return 0.0; }       /* queue empty */
    qmsg m = q[q_head];
    q[q_head].b = NULL;
    q_head = (q_head + 1) % QCAP;                          /* destructive pop */
    double delta = m.delta;
    size_t real = (size_t) m.len;
    if (mock_oversize_next) { mock_oversize_next = 0; free(m.b); *size = 70000; return delta; }
    if (real <= *size) memcpy(message, m.b, real);         /* else no copy (RtMidi behavior) */
    free(m.b);
    *size = real;
    return delta;
}
int rtmidi_out_send_message(RtMidiOutPtr d, const unsigned char *data, int len) {
    if (!d || !d->ok || len <= 0) return -1;
    if (g_send_fail) return -1;
    sent_record(data, len);
    if (g_loopback) (void) q_push(data, len, g_loopback_delta);
    return 0;
}
void rtmidi_in_free(RtMidiInPtr d)   { if (d) { g_live_wrappers--; free(d); } }
void rtmidi_out_free(RtMidiOutPtr d) { if (d) { g_live_wrappers--; free(d); } }

/* ===================== FFI-facing controls (mock library only) ===================== */
/* Reset every knob and drain both logs. Port count/backend back to defaults.
 * Note: the shim caches its enumerators (created once), so they count as live. */
MIDIMOCK_API void midimock_reset(void) {
    mock_queue_clear();
    sent_clear();
    mock_backend_ok = 1;
    mock_port_count = 2;
    mock_oversize_next = 0;
    g_loopback = 0;
    g_loopback_delta = 0.0;
    g_send_fail = 0;
    g_api = RTMIDI_API_LINUX_ALSA;
}
MIDIMOCK_API int32_t midimock_is_mock(void) { return 1; }
MIDIMOCK_API void midimock_set_backend_ok(int32_t ok)   { mock_backend_ok = ok ? 1 : 0; }
MIDIMOCK_API void midimock_set_port_count(int32_t n)    { mock_port_count = n < 0 ? 0 : n; }
MIDIMOCK_API void midimock_set_oversize_next(int32_t on){ mock_oversize_next = on ? 1 : 0; }
MIDIMOCK_API void midimock_set_send_fail(int32_t on)    { g_send_fail = on ? 1 : 0; }
/* Impersonate a backend by RtMidiApi number (4 = Windows MM, 2 = ALSA, 1 = CoreMIDI). */
MIDIMOCK_API void midimock_set_api(int32_t api) {
    g_api = (api > RTMIDI_API_UNSPECIFIED && api < RTMIDI_API_NUM) ? (enum RtMidiApi) api : RTMIDI_API_LINUX_ALSA;
}
/* Delta is passed in MICROSECONDS (an integer crosses the FFI exactly). */
MIDIMOCK_API void midimock_set_loopback(int32_t on, int32_t delta_us) {
    g_loopback = on ? 1 : 0;
    g_loopback_delta = (delta_us < 0 ? 0 : delta_us) / 1.0e6;
}
/* Inject one inbound message; returns 1 if queued, 0 if the queue is full. */
MIDIMOCK_API int32_t midimock_push(const uint8_t *bytes, int32_t len, int32_t delta_us) {
    return q_push(bytes, len, (delta_us < 0 ? 0 : delta_us) / 1.0e6);
}
MIDIMOCK_API int32_t midimock_queue_depth(void) { return q_depth(); }
MIDIMOCK_API int32_t midimock_sent_count(void)  { return sent_count; }
/* Copy sent message i (0-based) into out; returns its length, -needed if out is
 * too small, and 0 if i is out of range (a 0-length message can never be sent,
 * so 0 is unambiguous -- -1 would not be: it is also -needed for a 1-byte msg). */
MIDIMOCK_API int32_t midimock_sent_get(int32_t i, uint8_t *out, int32_t cap) {
    if (i < 0 || i >= sent_count) return 0;
    int32_t n = sent[i].len;
    if (!out || cap < n) return -n;
    memcpy(out, sent[i].b, (size_t) n);
    return n;
}
MIDIMOCK_API void    midimock_sent_clear(void)     { sent_clear(); }
MIDIMOCK_API int32_t midimock_live_wrappers(void)  { return g_live_wrappers; }
