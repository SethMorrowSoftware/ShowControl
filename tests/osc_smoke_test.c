/*
 * osc_smoke_test.c -- runtime smoke test for the ShowControl OSC shim.
 *
 * Links directly against the osc shared library and drives the same C entry
 * points the LCB binding calls: round-trips every OSC type through
 * build -> parse, exercises bundles and address matching, and fuzzes malformed
 * datagrams to prove they fail cleanly (no crash / no out-of-bounds read --
 * run this under -fsanitize=address). A compile check alone cannot show this.
 *
 * Enable with -DSHOWCONTROL_BUILD_TESTS=ON, then run `ctest`.
 */
#include "osc_shim.h"

#include <stdio.h>
#include <string.h>
#include <math.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok) {
    printf("  [%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (ok) g_pass++; else g_fail++;
}

int main(void) {
    printf("osc ABI version = %d\n", osc_abi_version());
    check("ABI version is 1", osc_abi_version() == 1);

    uint8_t buf[1024];
    char sbuf[256];
    int32_t ok;

    /* ---- build a message with one of every common type ---- */
    int32_t b = osc_build_new("/test/all");
    check("build_new returns a handle", b != 0);
    osc_build_add_int32(b, -42);
    osc_build_add_float(b, 0.5f);
    osc_build_add_string(b, "hello");
    osc_build_add_int64(b, 0x1122334455667788LL);
    osc_build_add_double(b, 3.14159265358979);
    osc_build_add_true(b);
    osc_build_add_false(b);
    osc_build_add_nil(b);
    osc_build_add_impulse(b);
    { uint8_t blob[3] = {0xDE, 0xAD, 0xBE}; osc_build_add_blob(b, blob, 3); }

    int32_t n = osc_build_finish(b, buf, sizeof buf);
    check("build_finish > 0", n > 0);
    check("serialized length is a multiple of 4", (n % 4) == 0);
    osc_build_free(b);

    /* ---- parse it back ---- */
    int32_t m = osc_parse(buf, n);
    check("parse returns a handle", m != 0);
    check("parsed is not a bundle", osc_is_bundle(m) == 0);

    osc_address(m, sbuf, sizeof sbuf);
    check("address round-trips", strcmp(sbuf, "/test/all") == 0);

    osc_typetag(m, sbuf, sizeof sbuf);
    check("typetag is ifshdTFNIb", strcmp(sbuf, "ifshdTFNIb") == 0);
    check("arg_count is 10", osc_arg_count(m) == 10);

    check("arg0 int32 == -42", osc_arg_int32(m, 0, &ok) == -42 && ok);
    check("arg1 float == 0.5", fabsf(osc_arg_float(m, 1, &ok) - 0.5f) < 1e-6 && ok);
    osc_arg_string(m, 2, sbuf, sizeof sbuf);
    check("arg2 string == hello", strcmp(sbuf, "hello") == 0);
    check("arg3 int64 round-trips", osc_arg_int64(m, 3, &ok) == 0x1122334455667788LL && ok);
    check("arg4 double round-trips", fabs(osc_arg_double(m, 4, &ok) - 3.14159265358979) < 1e-12 && ok);
    check("arg5 type is T", osc_arg_type(m, 5) == 'T');
    check("arg6 type is F", osc_arg_type(m, 6) == 'F');
    check("arg7 type is N", osc_arg_type(m, 7) == 'N');
    check("arg8 type is I", osc_arg_type(m, 8) == 'I');
    { uint8_t ob[8]; int32_t bl = osc_arg_blob(m, 9, ob, sizeof ob);
      check("arg9 blob len 3", bl == 3);
      check("arg9 blob bytes", bl == 3 && ob[0]==0xDE && ob[1]==0xAD && ob[2]==0xBE); }

    /* type-mismatch getter is a clean miss, not a crash */
    osc_arg_int32(m, 2, &ok);            /* arg2 is a string */
    check("int getter on a string sets ok=0", ok == 0);

    /* 64-bit-as-decimal-string path (no 64-bit foreign int in the engine) */
    osc_arg_int64_str(m, 3, sbuf, sizeof sbuf);
    check("arg3 int64 as decimal string", strcmp(sbuf, "1234605616436508552") == 0);
    osc_parse_free(m);

    /* build an int64 from a decimal string -> read it back as a string */
    int32_t bj = osc_build_new("/big");
    osc_build_add_int64_str(bj, "9223372036854775807");   /* INT64_MAX */
    int32_t bjn = osc_build_finish(bj, buf, sizeof buf);
    osc_build_free(bj);
    int32_t mj = osc_parse(buf, bjn);
    osc_arg_int64_str(mj, 0, sbuf, sizeof sbuf);
    check("int64 max round-trips via string", strcmp(sbuf, "9223372036854775807") == 0);
    osc_parse_free(mj);

    /* ---- empty-arg message ---- */
    int32_t e = osc_build_new("/x");
    int32_t en = osc_build_finish(e, buf, sizeof buf);
    osc_build_free(e);
    int32_t em = osc_parse(buf, en);
    check("no-arg message parses", em != 0 && osc_arg_count(em) == 0);
    osc_address(em, sbuf, sizeof sbuf);
    check("no-arg address is /x", strcmp(sbuf, "/x") == 0);
    osc_parse_free(em);

    /* ---- bundle of two messages ---- */
    uint8_t m1[64], m2[64];
    int32_t h1 = osc_build_new("/a"); osc_build_add_int32(h1, 1);
    int32_t n1 = osc_build_finish(h1, m1, sizeof m1); osc_build_free(h1);
    int32_t h2 = osc_build_new("/b"); osc_build_add_float(h2, 2.0f);
    int32_t n2 = osc_build_finish(h2, m2, sizeof m2); osc_build_free(h2);

    int32_t bb = osc_bundle_new(1 /*immediately*/);
    osc_bundle_add_message(bb, m1, n1);
    osc_bundle_add_message(bb, m2, n2);
    int32_t bn = osc_bundle_finish(bb, buf, sizeof buf);
    osc_bundle_free(bb);
    check("bundle_finish > 0", bn > 0);

    int32_t pb = osc_parse(buf, bn);
    check("bundle parses", pb != 0 && osc_is_bundle(pb));
    check("bundle timetag == 1", osc_bundle_timetag(pb) == 1);
    check("bundle has 2 messages", osc_bundle_count(pb) == 2);
    int32_t sub0 = osc_bundle_message(pb, 0);
    osc_address(sub0, sbuf, sizeof sbuf);
    check("bundle msg0 is /a", strcmp(sbuf, "/a") == 0 && osc_arg_int32(sub0,0,&ok)==1 && ok);
    int32_t sub1 = osc_bundle_message(pb, 1);
    osc_address(sub1, sbuf, sizeof sbuf);
    check("bundle msg1 is /b", strcmp(sbuf, "/b") == 0);
    osc_parse_free(pb);                  /* frees sub-messages too */

    /* ---- address pattern matching ---- */
    check("'*' matches a segment",         osc_match("/1/fader*", "/1/fader1") == 1);
    check("'*' stops at '/'",              osc_match("/*/x", "/a/x") == 1);
    check("'*' does not cross '/'",         osc_match("/*", "/a/b") == 0);
    check("'?' matches one char",          osc_match("/fader?", "/fader7") == 1);
    check("'[range]' matches",             osc_match("/ch[0-9]", "/ch5") == 1);
    check("'[!neg]' excludes",             osc_match("/ch[!0-9]", "/ch5") == 0);
    check("'{alt}' matches an option",     osc_match("/{foo,bar}/x", "/bar/x") == 1);
    check("literal mismatch fails",        osc_match("/a/b", "/a/c") == 0);
    check("exact literal matches",         osc_match("/comp/1/opacity", "/comp/1/opacity") == 1);

    /* ---- malformed datagrams must fail cleanly (run under ASan) ---- */
    check("NULL data is rejected",         osc_parse(NULL, 8) == 0);
    check("too-short is rejected",         osc_parse((const uint8_t*)"/x", 2) == 0);
    { uint8_t bad[8] = {'/','x',0,0, ',','i',0,0};   /* declares an int, supplies none */
      check("missing-int-payload rejected", osc_parse(bad, 8) == 0); }
    { uint8_t bad[8] = {'/','x',0,0, 'i','i',0,0};   /* no leading comma on type tag */
      check("no-comma typetag rejected",   osc_parse(bad, 8) == 0); }
    { uint8_t bad[12]= {'/','x',0,0, ',','b',0,0, 0xFF,0xFF,0xFF,0xFF}; /* blob len huge */
      check("over-long blob rejected",     osc_parse(bad, 12) == 0); }
    { uint8_t bad[4] = {'/','x','y','z'};            /* address never terminated */
      check("unterminated address rejected", osc_parse(bad, 4) == 0); }

    /* stale-handle no-ops */
    osc_address(999999, sbuf, sizeof sbuf);
    check("stale parse handle is a no-op", osc_arg_count(999999) == 0);
    osc_build_add_int32(888888, 1);      /* must not crash */
    check("stale builder handle is a no-op", 1);

    /* last-error is populated after a failure */
    osc_parse((const uint8_t*)"\x01\x02\x03\x04", 4);
    int32_t el = osc_last_error(sbuf, sizeof sbuf);
    check("last_error is set after a bad parse", el > 0);

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
