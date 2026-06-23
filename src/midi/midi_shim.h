/*
 * midi_shim.h
 * ----------------------------------------------------------------------------
 * Public C ABI of the ShowControl MIDI shim: an FFI-friendly layer over RtMidi's
 * C API (rtmidi_c.h) for xTalk Builder (LCB). Part of ShowControl for OpenXTalk
 * (OXT) / the xTalk family (also compatible with LiveCode 9.6.3+).
 *
 * The decisive design rule (README 4.2): we never call an LCB handler from a C
 * callback. RtMidi keeps an internal FIFO when no callback is registered, so we
 * POLL it from script on a timer and DRAIN the queue. Because RtMidi buffers and
 * delta-time-stamps every message, message integrity and timing survive jittery
 * poll cadence; only added latency scales with the poll interval.
 *
 * FFI conventions (mirrored from Box2Dxt):
 *   - RtMidi instance pointers stay INSIDE the shim; script sees a positive
 *     32-bit int handle (0 = invalid), generation-tagged and validated before
 *     use, so a stale handle is a harmless no-op.
 *   - Names and the last-error string are written into CALLER buffers; the shim
 *     never returns a library-owned `const char*`.
 *   - midi_in_drain batches every queued message into ONE caller buffer per poll
 *     (one FFI round-trip per tick instead of one per message).
 *
 * Drain record format (concatenated; midi_in_drain returns the record count):
 *     [2 bytes: byteLen, big-endian]
 *     [byteLen bytes: the raw MIDI message]
 *     [4 bytes: delta-time in MICROSECONDS since the previous message, big-endian]
 * A 2-byte length carries SysEx up to 65535 bytes; the LCB layer walks records
 * with simple byte arithmetic (no float unpacking needed).
 * ----------------------------------------------------------------------------
 */
#ifndef SHOWCONTROL_MIDI_SHIM_H
#define SHOWCONTROL_MIDI_SHIM_H

#include <stdint.h>

#if defined(_WIN32)
    #define MIDI_API __declspec(dllexport)
#else
    #define MIDI_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define MIDI_ABI_VERSION 1

MIDI_API int32_t  midi_abi_version(void);

/* ---- Enumeration --------------------------------------------------------- */
MIDI_API int32_t  midi_in_count(void);
MIDI_API int32_t  midi_out_count(void);
/* Port name into the caller buffer; returns length, or -needed if too small. */
MIDI_API int32_t  midi_in_name(int32_t index, char *out, int32_t out_cap);
MIDI_API int32_t  midi_out_name(int32_t index, char *out, int32_t out_cap);

/* ---- Open / close (return a handle > 0, or 0 on failure) ----------------- */
MIDI_API int32_t  midi_in_open(int32_t portIndex);
MIDI_API int32_t  midi_out_open(int32_t portIndex);
MIDI_API int32_t  midi_in_open_virtual(const char *name);   /* macOS / Linux */
MIDI_API int32_t  midi_out_open_virtual(const char *name);  /* macOS / Linux */
MIDI_API void     midi_close(int32_t handle);

/* ---- Input config & draining -------------------------------------------- */
MIDI_API void     midi_in_ignore(int32_t handle, int32_t sysex, int32_t timing, int32_t sense);
/* Drain up to max_msgs queued messages into `out` (see record format above).
 * Returns the number of records written. A message that does not fit is stashed
 * and emitted on the next call, so messages are never dropped. Returns 0 when
 * the queue is empty (and 0 on a bad/non-input handle). */
MIDI_API int32_t  midi_in_drain(int32_t handle, uint8_t *out, int32_t out_cap, int32_t max_msgs);

/* ---- Output -------------------------------------------------------------- */
/* Send raw MIDI bytes. Returns 1 on success, 0 on failure / bad handle. */
MIDI_API int32_t  midi_out_send(int32_t handle, const uint8_t *data, int32_t len);

/* ---- Diagnostics --------------------------------------------------------- */
MIDI_API int32_t  midi_last_error(char *out, int32_t out_cap);

/* ZStringUTF8 convenience for the LCB layer. The name accessors return a
 * pointer to a module-static buffer, valid until the next name call -- the
 * engine copies it immediately into a String. Return "" (never NULL). */
MIDI_API const char *midi_in_name_str(int32_t index);
MIDI_API const char *midi_out_name_str(int32_t index);
MIDI_API const char *midi_error_str(void);

#ifdef __cplusplus
}
#endif

#endif /* SHOWCONTROL_MIDI_SHIM_H */
