/*
 * midi_mock_smoke_test.c -- exercises midi_shim.c's OWN logic against a MOCK of
 * RtMidi (tests/mock/rtmidi_c.h), so the trickiest, most regression-prone code in
 * the shim runs DETERMINISTICALLY, on every platform, under ASan/UBSan -- without
 * the real RtMidi and without any MIDI hardware.
 *
 * The real smoke test (midi_smoke_test.c) links actual RtMidi but, being unable to
 * inject inbound messages on a headless runner, cannot cover:
 *   - the batched drain producing the [2B len BE][bytes][4B delta-us BE] records,
 *   - the per-port stash that guarantees "never drop a popped message" when the
 *     caller buffer fills mid-drain,
 *   - the max-SysEx (65535 B) record and the oversize (>65535) drop,
 *   - the port-name return-value handling (full length via the return value).
 * This test does, by driving a controllable mock queue.
 *
 * Built as its own executable that compiles midi_shim.c against the mock header
 * (NOT linked to RtMidi). Enabled with -DSHOWCONTROL_BUILD_TESTS=ON; run via ctest.
 */
#include "rtmidi_c.h"      /* the MOCK header on tests/mock (see CMakeLists) */
#include "midi_shim.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ===================== mock RtMidi implementation ===================== */
int mock_backend_ok = 1;
int mock_port_count = 2;
int mock_oversize_next = 0;

typedef struct { unsigned char b[70000]; int len; double delta; } qmsg;
static qmsg q[1024];
static int q_head = 0, q_tail = 0;

void mock_queue_clear(void) { q_head = q_tail = 0; }
void mock_queue_push(const unsigned char *msg, int len, double delta) {
    q[q_tail].len = len; q[q_tail].delta = delta;
    if (len > 0) memcpy(q[q_tail].b, msg, (size_t) len);
    q_tail = (q_tail + 1) % 1024;
}

static struct RtMidiWrapper *make_wrapper(void) {
    struct RtMidiWrapper *w = (struct RtMidiWrapper *) calloc(1, sizeof *w);
    w->ok  = mock_backend_ok ? true : false;
    w->ptr = mock_backend_ok ? (void *) w : NULL;   /* NULL internal object when !ok */
    return w;
}
RtMidiInPtr  rtmidi_in_create_default(void)  { return make_wrapper(); }
RtMidiOutPtr rtmidi_out_create_default(void) { return make_wrapper(); }
void rtmidi_open_port(RtMidiPtr d, unsigned int p, const char *n) { (void)d;(void)p;(void)n; }
void rtmidi_open_virtual_port(RtMidiPtr d, const char *n) { (void)d;(void)n; }
void rtmidi_close_port(RtMidiPtr d) { (void)d; }
unsigned int rtmidi_get_port_count(RtMidiPtr d) { (void)d; return (unsigned) mock_port_count; }

/* RtMidi 6.0.0: with bufOut != NULL returns snprintf's count (full length), never
 * writes *bufLen. The shim must trust the RETURN value, not *bufLen. */
int rtmidi_get_port_name(RtMidiPtr d, unsigned int p, char *buf, int *buflen) {
    (void)d;
    char name[64];
    snprintf(name, sizeof name, "MockPort-%u-aLongishName", p);
    if (buf && buflen && *buflen > 0) return snprintf(buf, (size_t) *buflen, "%s", name);
    return (int) strlen(name);
}
void rtmidi_in_ignore_types(RtMidiInPtr d, bool a, bool b, bool c) { (void)d;(void)a;(void)b;(void)c; }

double rtmidi_in_get_message(RtMidiInPtr d, unsigned char *message, size_t *size) {
    (void)d;
    if (q_head == q_tail) { *size = 0; return 0.0; }       /* queue empty */
    qmsg *m = &q[q_head];
    q_head = (q_head + 1) % 1024;                          /* destructive pop */
    double delta = m->delta;
    size_t real = (size_t) m->len;
    if (mock_oversize_next) { mock_oversize_next = 0; *size = 70000; return delta; }
    if (real <= *size) memcpy(message, m->b, real);        /* else no copy (RtMidi behavior) */
    *size = real;
    return delta;
}
int rtmidi_out_send_message(RtMidiOutPtr d, const unsigned char *data, int len) {
    (void)d;(void)data; return len > 0 ? 0 : -1;
}
void rtmidi_in_free(RtMidiInPtr d) { free(d); }
void rtmidi_out_free(RtMidiOutPtr d) { free(d); }

/* ===================== test harness ===================== */
static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok) {
    printf("  [%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (ok) g_pass++; else g_fail++;
}

/* Decode the drain buffer exactly as midi.lcb's midiPoll does -- validates the
 * record wire format end to end. */
static void decode_records(const unsigned char *buf, int n_records,
                           int *lens, unsigned char msgs[][70000], uint32_t *deltas) {
    int pos = 0;
    for (int k = 0; k < n_records; k++) {
        int len = buf[pos] * 256 + buf[pos + 1]; pos += 2;
        lens[k] = len;
        for (int i = 0; i < len; i++) msgs[k][i] = buf[pos + i];
        pos += len;
        deltas[k] = ((uint32_t) buf[pos] << 24) | ((uint32_t) buf[pos+1] << 16)
                  | ((uint32_t) buf[pos+2] << 8) | (uint32_t) buf[pos+3];
        pos += 4;
    }
}

#define KDRAINCAP 65541   /* the LCB binding's drain buffer size */

int main(void) {
    printf("midi ABI = %d\n", midi_abi_version());
    check("ABI is 1", midi_abi_version() == 1);
    check("in_count is the port count", midi_in_count() == 2);

    /* port name: trust the return value (full length), even when truncated */
    char nm[256];
    check("port name length > 0", midi_in_name(0, nm, sizeof nm) > 0);
    check("port name content", strcmp(nm, "MockPort-0-aLongishName") == 0);
    char tiny[8];
    int t = midi_in_name(0, tiny, sizeof tiny);
    check("tiny buffer reports truncation (negative)", t < 0);
    check("tiny buffer is NUL-terminated", tiny[7] == '\0');

    int32_t in = midi_in_open_virtual("test");
    check("opened a virtual input", in != 0);
    static unsigned char out[KDRAINCAP];
    check("drain of empty queue -> 0", midi_in_drain(in, out, KDRAINCAP, 256) == 0);

    /* three messages: verify bytes and the microsecond delta conversion */
    mock_queue_clear();
    { unsigned char m1[3]={0x90,60,100}, m2[3]={0xB0,7,64}, m3[2]={0xC0,5};
      mock_queue_push(m1,3,0.001); mock_queue_push(m2,3,0.0005); mock_queue_push(m3,2,0.0);
      int n = midi_in_drain(in, out, KDRAINCAP, 256);
      check("drained 3 records", n == 3);
      int lens[8]; static unsigned char msgs[8][70000]; uint32_t d[8];
      decode_records(out, n, lens, msgs, d);
      check("rec0 bytes + 1000us", lens[0]==3 && msgs[0][0]==0x90 && msgs[0][2]==100 && d[0]==1000);
      check("rec1 bytes + 500us",  lens[1]==3 && msgs[1][0]==0xB0 && d[1]==500);
      check("rec2 len 2 + 0us",    lens[2]==2 && msgs[2][0]==0xC0 && d[2]==0); }

    /* max_msgs cap: 5 queued, ask 2, then 3 remain */
    mock_queue_clear();
    for (int i=0;i<5;i++){ unsigned char b[1]={0xF8}; mock_queue_push(b,1,0.0); }
    check("cap honored (2)", midi_in_drain(in, out, KDRAINCAP, 2) == 2);
    check("remaining drained (3)", midi_in_drain(in, out, KDRAINCAP, 256) == 3);

    /* STASH: a record that doesn't fit a small buffer is stashed, not dropped,
     * and emitted next drain -- no loss, no duplication, delta preserved. */
    mock_queue_clear();
    { unsigned char big[100]; memset(big,0x7F,sizeof big); big[0]=0xF0; big[99]=0xF7;
      unsigned char sm[3]={0x90,1,2};
      mock_queue_push(big,100,0.002); mock_queue_push(sm,3,0.001);
      unsigned char tinyout[64];
      check("first drain emits 0 (big stashed)", midi_in_drain(in, tinyout, sizeof tinyout, 256) == 0);
      int n2 = midi_in_drain(in, out, KDRAINCAP, 256);
      check("second drain emits 2 (stash + small)", n2 == 2);
      int lens[8]; static unsigned char msgs[8][70000]; uint32_t d[8];
      decode_records(out, n2, lens, msgs, d);
      check("flushed stash is the big msg", lens[0]==100 && msgs[0][0]==0xF0 && msgs[0][99]==0xF7);
      check("stash delta preserved (2000us)", d[0]==2000);
      check("then the small msg", lens[1]==3 && msgs[1][0]==0x90);
      check("queue now empty", midi_in_drain(in, out, KDRAINCAP, 256) == 0); }

    /* max_msgs == 0 returns 0 and PRESERVES a pending stash (return never exceeds
     * max_msgs). */
    mock_queue_clear();
    { unsigned char big[100]; memset(big,0x7F,sizeof big); big[0]=0xF0;
      unsigned char sm[2]={0x90,1};
      mock_queue_push(big,100,0.0); mock_queue_push(sm,2,0.0);
      unsigned char to[64];
      midi_in_drain(in, to, sizeof to, 256);            /* stashes big */
      check("max_msgs=0 returns 0", midi_in_drain(in, out, KDRAINCAP, 0) == 0);
      check("stash preserved across the 0-drain", midi_in_drain(in, out, KDRAINCAP, 256) == 2); }

    /* max SysEx (65535) fits exactly in kDrainCap (2+65535+4) */
    mock_queue_clear();
    { static unsigned char sx[65535]; sx[0]=0xF0; sx[65534]=0xF7;
      mock_queue_push(sx, 65535, 0.0);
      int nn = midi_in_drain(in, out, KDRAINCAP, 256);
      check("max 65535B SysEx -> one record", nn == 1);
      int lens[2]; static unsigned char msgs[2][70000]; uint32_t d[2];
      decode_records(out, nn, lens, msgs, d);
      check("record length is exactly 65535", lens[0] == 65535); }

    /* oversize (>65535) is skipped (un-representable), no crash, no wedge */
    mock_queue_clear();
    mock_oversize_next = 1;
    { unsigned char b[1]={0xF8}; mock_queue_push(b,1,0.0); mock_queue_push(b,1,0.0); }
    check("oversize skipped; next msg still delivered", midi_in_drain(in, out, KDRAINCAP, 256) == 1);
    { char e[256]; midi_last_error(e, sizeof e); check("oversize drop set an error", e[0] != '\0'); }

    midi_close(in);
    check("drain after close -> 0", midi_in_drain(in, out, KDRAINCAP, 256) == 0);

    /* stale-handle safety */
    check("drain on stale handle -> 0", midi_in_drain(999999, out, KDRAINCAP, 256) == 0);
    { unsigned char b[3]={0x90,60,100}; check("send on stale handle -> 0", midi_out_send(999999, b, 3) == 0); }

    /* headless backend: a freshly opened port fails cleanly (open_port makes a
     * fresh instance each call, so this is independent of the cached enumerator). */
    mock_backend_ok = 0;
    check("no-backend open fails cleanly", midi_in_open(0) == 0);
    { char e[256]; midi_last_error(e, sizeof e); check("failed open set an error", e[0] != '\0'); }
    mock_backend_ok = 1;

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
