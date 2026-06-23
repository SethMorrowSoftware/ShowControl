/*
 * midi_shim.c
 * ----------------------------------------------------------------------------
 * The C shim for ShowControl's MIDI extension: a thin, FFI-friendly layer over
 * RtMidi's C API (rtmidi_c.h) for xTalk Builder (LCB). Output is request/
 * response; input is the internal-FIFO + POLL model (no callbacks into LCB --
 * see README 4.2/6). RtMidi instance pointers stay inside; script gets an
 * integer handle, validated before every use (stale handle = harmless no-op).
 *
 * Build: compiled with RtMidi into ONE shared library named "midi"
 * (libmidi.so / libmidi.dylib / midi.dll). See CMakeLists.txt.
 * ----------------------------------------------------------------------------
 */

#include "midi_shim.h"
#include "rtmidi_c.h"

#include <stdlib.h>
#include <string.h>

/* ===================================================================== */
/* Last-error string + caller-buffer copy helper (shared conventions).    */
/* ===================================================================== */
static char g_err[256];
static void err_set(const char *m) {
    if (!m) { g_err[0] = '\0'; return; }
    size_t n = strlen(m);
    if (n >= sizeof g_err) n = sizeof g_err - 1;
    memcpy(g_err, m, n);
    g_err[n] = '\0';
}
static int32_t copy_str(const char *s, char *out, int32_t cap) {
    int32_t n = (int32_t) strlen(s);
    if (!out || cap <= 0 || n + 1 > cap) {
        if (out && cap > 0) out[0] = '\0';
        return -(n + 1);
    }
    memcpy(out, s, (size_t) n + 1);
    return n;
}
static void be16_store(uint8_t *p, uint16_t v) { p[0]=(uint8_t)(v>>8); p[1]=(uint8_t)v; }
static void be32_store(uint8_t *p, uint32_t v) {
    p[0]=(uint8_t)(v>>24); p[1]=(uint8_t)(v>>16); p[2]=(uint8_t)(v>>8); p[3]=(uint8_t)v;
}

/* ===================================================================== */
/* Handle table for open ports (generation-tagged; same scheme as OSC).   */
/* ===================================================================== */
#define H_SLOT_BITS 23
#define H_SLOT_MASK 0x7FFFFF
#define H_GEN_MASK  0xFF

typedef struct {
    RtMidiPtr ptr;
    int       is_input;
    /* one-message stash so a full output buffer never drops a popped message */
    uint8_t  *stash;
    int32_t   stash_len;
    uint32_t  stash_delta_us;
    int       has_stash;
} midi_port;

static midi_port    *g_arr  = NULL;
static unsigned char *g_used = NULL;
static unsigned char *g_gen  = NULL;
static int g_cap = 0, g_cnt = 0;
static int *g_free = NULL; static int g_freeCap = 0, g_freeCnt = 0;

static int tbl_grow(int nc) {
    midi_port *na = (midi_port*) realloc(g_arr, (size_t)nc * sizeof(midi_port));
    if (!na) { return 0; }
    g_arr = na;
    unsigned char *nu = (unsigned char*) realloc(g_used, (size_t)nc);
    if (!nu) { return 0; }
    g_used = nu;
    unsigned char *ng = (unsigned char*) realloc(g_gen, (size_t)nc);
    if (!ng) { return 0; }
    g_gen = ng;
    memset(g_used + g_cap, 0, (size_t)(nc - g_cap));
    memset(g_gen  + g_cap, 0, (size_t)(nc - g_cap));
    g_cap = nc; return 1;
}
static int tbl_slot(int h) {
    if (h <= 0) return -1;
    int idx = (h & H_SLOT_MASK) - 1;
    unsigned gen = ((unsigned)h >> H_SLOT_BITS) & H_GEN_MASK;
    if (idx < 0 || idx >= g_cnt) return -1;
    if (!g_used[idx] || g_gen[idx] != gen) return -1;
    return idx;
}
static int tbl_add(midi_port v) {
    int idx = -1;
    if (g_freeCnt > 0) idx = g_free[--g_freeCnt];
    if (idx < 0) {
        if (g_cnt >= H_SLOT_MASK - 1) return 0;
        if (g_cnt >= g_cap) { int nc = g_cap ? g_cap*2 : 16; if (!tbl_grow(nc)) return 0; }
        idx = g_cnt++;
    }
    g_arr[idx] = v; g_used[idx] = 1;
    return (int)(((unsigned)g_gen[idx] << H_SLOT_BITS) | (unsigned)(idx + 1));
}
static midi_port *tbl_get(int h) { int i = tbl_slot(h); return i < 0 ? NULL : &g_arr[i]; }
static void tbl_release(int h) {
    int idx = tbl_slot(h);
    if (idx < 0) return;
    g_used[idx] = 0;
    g_gen[idx] = (unsigned char)((g_gen[idx] + 1) & H_GEN_MASK);
    if (g_freeCnt >= g_freeCap) {
        int nc = g_freeCap ? g_freeCap*2 : 16;
        int *nf = (int*) realloc(g_free, (size_t)nc * sizeof(int));
        if (nf) { g_free = nf; g_freeCap = nc; }
    }
    if (g_freeCnt < g_freeCap) g_free[g_freeCnt++] = idx;
}

/* ===================================================================== */
/* Lazy persistent enumerators (counting / naming ports needs an instance) */
/* ===================================================================== */
static RtMidiInPtr  g_enum_in  = NULL;
static RtMidiOutPtr g_enum_out = NULL;

static RtMidiInPtr enum_in(void) {
    if (!g_enum_in) g_enum_in = rtmidi_in_create_default();
    return g_enum_in;
}
static RtMidiOutPtr enum_out(void) {
    if (!g_enum_out) g_enum_out = rtmidi_out_create_default();
    return g_enum_out;
}

/* ===================================================================== */
MIDI_API int32_t midi_abi_version(void) { return MIDI_ABI_VERSION; }

MIDI_API int32_t midi_in_count(void) {
    RtMidiInPtr d = enum_in();
    if (!d || !d->ok) { err_set("midi: no MIDI input backend"); return 0; }
    return (int32_t) rtmidi_get_port_count(d);
}
MIDI_API int32_t midi_out_count(void) {
    RtMidiOutPtr d = enum_out();
    if (!d || !d->ok) { err_set("midi: no MIDI output backend"); return 0; }
    return (int32_t) rtmidi_get_port_count(d);
}

static int32_t port_name(RtMidiPtr d, int32_t index, char *out, int32_t cap) {
    /* Guard !d->ok: when a backend fails to init, RtMidi returns a wrapper whose
       internal object is NULL -- calling rtmidi_get_port_name would deref it. */
    if (!d || !d->ok || index < 0) { if (out && cap > 0) out[0] = '\0'; return 0; }
    /* rtmidi writes up to *bufLen bytes and sets *bufLen to the full length. */
    int blen = cap;
    char tmp[256];
    char *dst = (cap > 0 && out) ? out : tmp;
    if (dst == tmp) blen = (int) sizeof tmp;
    int rc = rtmidi_get_port_name(d, (unsigned)index, dst, &blen);
    if (rc < 0) { if (out && cap > 0) out[0] = '\0'; return 0; }
    if (dst == out) {
        /* ensure NUL-termination within cap */
        if (blen >= cap) { out[cap-1] = '\0'; return -(blen + 1); }
        out[blen] = '\0';
        return blen;
    }
    return copy_str(tmp, out, cap);
}
MIDI_API int32_t midi_in_name(int32_t index, char *out, int32_t cap) {
    return port_name(enum_in(), index, out, cap);
}
MIDI_API int32_t midi_out_name(int32_t index, char *out, int32_t cap) {
    return port_name(enum_out(), index, out, cap);
}

/* ---- open / close -------------------------------------------------------- */
static int32_t open_port(int is_input, int32_t portIndex, const char *vname) {
    RtMidiPtr d = is_input ? (RtMidiPtr) rtmidi_in_create_default()
                           : (RtMidiPtr) rtmidi_out_create_default();
    if (!d) { err_set("midi: could not create RtMidi instance"); return 0; }
    if (!d->ok) {
        err_set(d->msg ? d->msg : "midi: backend init failed");
        if (is_input) rtmidi_in_free(d); else rtmidi_out_free(d);
        return 0;
    }
    if (vname) {
        rtmidi_open_virtual_port(d, vname);
    } else {
        unsigned count = rtmidi_get_port_count(d);
        if (portIndex < 0 || (unsigned)portIndex >= count) {
            err_set("midi: port index out of range");
            if (is_input) rtmidi_in_free(d); else rtmidi_out_free(d);
            return 0;
        }
        rtmidi_open_port(d, (unsigned)portIndex, is_input ? "ShowControl In" : "ShowControl Out");
    }
    if (!d->ok) {
        err_set(d->msg ? d->msg : "midi: open port failed");
        if (is_input) rtmidi_in_free(d); else rtmidi_out_free(d);
        return 0;
    }
    midi_port mp; memset(&mp, 0, sizeof mp);
    mp.ptr = d; mp.is_input = is_input;
    int32_t h = tbl_add(mp);
    if (!h) {
        err_set("midi: handle table full");
        if (is_input) rtmidi_in_free(d); else rtmidi_out_free(d);
        return 0;
    }
    return h;
}

MIDI_API int32_t midi_in_open(int32_t portIndex)  { return open_port(1, portIndex, NULL); }
MIDI_API int32_t midi_out_open(int32_t portIndex) { return open_port(0, portIndex, NULL); }
MIDI_API int32_t midi_in_open_virtual(const char *name) {
    return open_port(1, -1, name ? name : "ShowControl In");
}
MIDI_API int32_t midi_out_open_virtual(const char *name) {
    return open_port(0, -1, name ? name : "ShowControl Out");
}

MIDI_API void midi_close(int32_t handle) {
    midi_port *mp = tbl_get(handle);
    if (!mp) return;
    if (mp->ptr) {
        rtmidi_close_port(mp->ptr);
        if (mp->is_input) rtmidi_in_free(mp->ptr); else rtmidi_out_free(mp->ptr);
    }
    free(mp->stash);
    tbl_release(handle);
}

/* ---- input config & drain ------------------------------------------------ */
MIDI_API void midi_in_ignore(int32_t handle, int32_t sysex, int32_t timing, int32_t sense) {
    midi_port *mp = tbl_get(handle);
    if (!mp || !mp->is_input || !mp->ptr) return;
    rtmidi_in_ignore_types(mp->ptr, sysex != 0, timing != 0, sense != 0);
}

/* Append one record [2B len][bytes][4B delta-us] to out at *pos if it fits.
 * Returns 1 if written, 0 if it does not fit. */
static int emit_record(uint8_t *out, int32_t out_cap, int32_t *pos,
                       const uint8_t *bytes, int32_t len, uint32_t delta_us) {
    int32_t need = 2 + len + 4;
    if (*pos + need > out_cap) return 0;
    be16_store(out + *pos, (uint16_t) len); *pos += 2;
    memcpy(out + *pos, bytes, (size_t) len); *pos += len;
    be32_store(out + *pos, delta_us); *pos += 4;
    return 1;
}

MIDI_API int32_t midi_in_drain(int32_t handle, uint8_t *out, int32_t out_cap, int32_t max_msgs) {
    midi_port *mp = tbl_get(handle);
    if (!mp || !mp->is_input || !mp->ptr || !out) return 0;

    int32_t pos = 0, count = 0;
    static unsigned char tmp[65536];   /* single-threaded LCB caller -> static ok */

    /* 1. flush a stashed message from a previous (buffer-full) drain first */
    if (mp->has_stash) {
        if (!emit_record(out, out_cap, &pos, mp->stash, mp->stash_len, mp->stash_delta_us))
            return 0;                  /* caller buffer can't hold even one record */
        free(mp->stash); mp->stash = NULL; mp->has_stash = 0;
        count++;
    }

    /* 2. drain RtMidi's FIFO */
    while (count < max_msgs) {
        size_t sz = sizeof tmp;
        double delta = rtmidi_in_get_message(mp->ptr, tmp, &sz);
        if (sz == 0) break;                       /* queue empty */
        if (sz > 65535) continue;                 /* implausibly large -> skip */
        if (delta < 0) delta = 0;
        double us = delta * 1.0e6;
        uint32_t delta_us = (us > 4294967295.0) ? 4294967295u : (uint32_t) us;
        if (!emit_record(out, out_cap, &pos, tmp, (int32_t) sz, delta_us)) {
            /* doesn't fit: stash it so the next drain delivers it (no drop) */
            mp->stash = (uint8_t*) malloc(sz ? sz : 1);
            if (mp->stash) {
                memcpy(mp->stash, tmp, sz);
                mp->stash_len = (int32_t) sz;
                mp->stash_delta_us = delta_us;
                mp->has_stash = 1;
            }
            break;
        }
        count++;
    }
    return count;
}

/* ---- output -------------------------------------------------------------- */
MIDI_API int32_t midi_out_send(int32_t handle, const uint8_t *data, int32_t len) {
    midi_port *mp = tbl_get(handle);
    if (!mp || mp->is_input || !mp->ptr || !data || len <= 0) return 0;
    int rc = rtmidi_out_send_message(mp->ptr, data, len);
    if (rc < 0 || !mp->ptr->ok) {
        err_set(mp->ptr->msg ? mp->ptr->msg : "midi: send failed");
        return 0;
    }
    return 1;
}

/* ---- diagnostics --------------------------------------------------------- */
MIDI_API int32_t midi_last_error(char *out, int32_t out_cap) {
    return copy_str(g_err, out, out_cap);
}

static char g_name[256];
MIDI_API const char *midi_in_name_str(int32_t index) {
    g_name[0] = '\0';
    port_name(enum_in(), index, g_name, (int32_t) sizeof g_name);
    return g_name;
}
MIDI_API const char *midi_out_name_str(int32_t index) {
    g_name[0] = '\0';
    port_name(enum_out(), index, g_name, (int32_t) sizeof g_name);
    return g_name;
}
MIDI_API const char *midi_error_str(void) { return g_err; }
