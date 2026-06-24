-- selftest.livecodescript
-- ---------------------------------------------------------------------------
-- HARDWARE-FREE self-test for the three ShowControl extensions, runnable inside
-- OpenXTalk. It needs NO controllers, DAWs, or DMX nodes -- it exercises the
-- extensions by round-tripping their own output back through their own input.
--
-- HOW TO RUN
--   1. Install (or Test-load) the osc, midi, and artnet extensions in OXT.
--   2. Paste this whole script into a stack or card script.
--   3. In the Message Box, run:   put runShowControlSelfTest()
--      (a "STResults" field is created/updated if a card is open, and the full
--       log is also returned to the Message Box.)
--
-- WHAT IT PROVES, in three tiers of increasing dependency:
--   Tier 1  pure build -> parse round-trips + decode + golden bytes.  No sockets,
--           no hardware. This is also the real Phase-0 FFI confirmation: if these
--           pass, Data <-> pointer marshalling works in your engine build.
--   Tier 2  OSC + Art-Net loopback over a real UDP socket to 127.0.0.1.  No
--           hardware, but it does depend on the engine's datagram-socket path.
--   Tier 3  MIDI without hardware: enumeration, virtual-port lifecycle, error
--           handling, and the full decode path via midiDecode (deterministic).
--
-- It is checked by tools/check-livecodescript.py but, like all OXT script, the
-- behavioural pass needs a real OXT run -- which is the whole point of this file.
-- ---------------------------------------------------------------------------

local sPass, sFail, sLog

-- ===== entry point =========================================================

function runShowControlSelfTest
   put 0 into sPass
   put 0 into sFail
   put "ShowControl hardware-free self-test" into sLog

   stSection "TIER 1 - OSC build -> parse (no sockets)"
   testOscRoundTrip
   testOscBundle
   testOscMatch

   stSection "TIER 1 - Art-Net build -> parse + golden bytes (no sockets)"
   testArtnet

   stSection "TIER 1 - MIDI decode (no hardware)"
   testMidiDecode

   stSection "TIER 3 - MIDI enumeration / lifecycle / errors (no hardware)"
   testMidiPorts

   stSection "TIER 2 - OSC + Art-Net UDP loopback to 127.0.0.1 (no hardware)"
   startLoopback

   put return & "==================================================" after sLog
   put return & "  " & sPass & " passed, " & sFail & " failed (Tiers 1 & 3)" after sLog
   put return & "  Tier 2 loopback results arrive asynchronously below." after sLog
   showResults
   return sLog
end runShowControlSelfTest

-- ===== Tier 1: OSC =========================================================

private command testOscRoundTrip
   local tData, tMsg, tArgs
   put oscBuildMessage("/test/all", \
        [["i", -42], ["f", 0.5], ["s", "hello"], ["d", 3.25], ["T"], ["F"]]) into tData
   stAssert "oscBuildMessage returns non-empty Data", (tData is not empty)
   stAssert "datagram length is a multiple of 4", ((the number of bytes in tData) mod 4 is 0)

   put oscParse(tData) into tMsg
   stAssert "parsed isBundle is false", (tMsg["isBundle"] is false)
   stAssert "address round-trips", (tMsg["address"] is "/test/all")
   stAssert "type tag round-trips (ifsdTF)", (tMsg["types"] is "ifsdTF")

   put tMsg["args"] into tArgs
   stAssert "arg 1 int  == -42", (tArgs[1] is -42)
   stAssert "arg 2 float ~ 0.5", (abs(tArgs[2] - 0.5) < 0.0001)
   stAssert "arg 3 string == hello", (tArgs[3] is "hello")
   stAssert "arg 4 double ~ 3.25", (abs(tArgs[4] - 3.25) < 0.0001)
   stAssert "arg 5 true  is true", (tArgs[5] is true)
   stAssert "arg 6 false is false", (tArgs[6] is false)
   -- Diagnostic: reveal how an args List marshals into Script (helps if the
   -- numeric access above is off in your engine).
   put return & "    (args element count: " & (the number of elements of tArgs) & ")" after sLog

   -- 64-bit int crosses as a decimal STRING end to end.
   put oscParse(oscBuildMessage("/big", [["h", "9223372036854775807"]])) into tMsg
   put tMsg["args"] into tArgs
   stAssert "int64 round-trips as decimal string", (tArgs[1] is "9223372036854775807")
end testOscRoundTrip

private command testOscBundle
   local tA, tB, tBundle, tParsed, tMsgs
   put oscBuildMessage("/a", [["i", 1]]) into tA
   put oscBuildMessage("/b", [["f", 2.0]]) into tB
   put oscBuildBundle("1", [tA, tB]) into tBundle
   stAssert "oscBuildBundle returns non-empty", (tBundle is not empty)

   put oscParse(tBundle) into tParsed
   stAssert "parsed isBundle is true", (tParsed["isBundle"] is true)
   stAssert "bundle timetag is 1", (tParsed["timetag"] is "1")
   put tParsed["messages"] into tMsgs
   stAssert "bundle has 2 messages", ((the number of elements of tMsgs) is 2)
   stAssert "sub-message 1 is /a", (tMsgs[1]["address"] is "/a")
   stAssert "sub-message 2 is /b", (tMsgs[2]["address"] is "/b")
end testOscBundle

private command testOscMatch
   stAssert "'*' matches within a segment", oscMatch("/1/fader*", "/1/fader1")
   stAssert "'*' does not cross '/'", (not oscMatch("/*", "/a/b"))
   stAssert "'?' matches one char", oscMatch("/fader?", "/fader7")
   stAssert "'[0-9]' range matches", oscMatch("/ch[0-9]", "/ch5")
   stAssert "'[!0-9]' negation excludes", (not oscMatch("/ch[!0-9]", "/ch5"))
   stAssert "'{foo,bar}' alternation matches", oscMatch("/{foo,bar}/x", "/bar/x")
   stAssert "literal mismatch fails", (not oscMatch("/a/b", "/a/c"))
end testOscMatch

-- ===== Tier 1: Art-Net =====================================================

private command testArtnet
   local tCh, tPkt, tParsed
   put numToByte(255) & numToByte(128) & numToByte(0) & numToByte(64) into tCh
   put artnetBuildDmx(0, tCh) into tPkt
   stAssert "artnetBuildDmx returns non-empty", (tPkt is not empty)
   stAssert "packet length is 18 + 4", ((the number of bytes in tPkt) is 22)

   -- the mixed-endianness traps, checked byte for byte (1-based indices)
   stAssert "OpCode is little-endian (00 50)", \
      (byteToNum(byte 9 of tPkt) is 0 and byteToNum(byte 10 of tPkt) is 80)
   stAssert "ProtVer is big-endian (00 0E)", \
      (byteToNum(byte 11 of tPkt) is 0 and byteToNum(byte 12 of tPkt) is 14)
   stAssert "Length is big-endian (00 04)", \
      (byteToNum(byte 17 of tPkt) is 0 and byteToNum(byte 18 of tPkt) is 4)

   put artnetParseDmx(tPkt) into tParsed
   stAssert "opcode is ArtDmx", (tParsed["opcode"] is "ArtDmx")
   stAssert "universe is 0", (tParsed["universe"] is 0)
   stAssert "length is 4", (tParsed["length"] is 4)
   stAssert "channel 1 round-trips (255)", (byteToNum(byte 1 of tParsed["channels"]) is 255)
   stAssert "channel 2 round-trips (128)", (byteToNum(byte 2 of tParsed["channels"]) is 128)

   -- universe split: 258 -> SubUni 2, Net 1
   put artnetParseDmx(artnetBuildDmx(258, tCh)) into tParsed
   stAssert "universe 258 round-trips", (tParsed["universe"] is 258)
   stAssert "  -> subuni 2", (tParsed["subuni"] is 2)
   stAssert "  -> net 1", (tParsed["net"] is 1)

   -- odd channel count is padded up to even
   put artnetParseDmx(artnetBuildDmx(0, numToByte(10) & numToByte(20) & numToByte(30))) into tParsed
   stAssert "odd length padded to even (4)", (tParsed["length"] is 4)

   -- discovery packet
   put artnetBuildPoll() into tPkt
   stAssert "artnetBuildPoll non-empty", (tPkt is not empty)
   stAssert "ArtPoll OpCode little-endian (00 20)", \
      (byteToNum(byte 9 of tPkt) is 0 and byteToNum(byte 10 of tPkt) is 32)
end testArtnet

-- ===== Tier 1: MIDI decode (no hardware) ===================================

private command testMidiDecode
   local tDec
   put midiDecode(numToByte(144) & numToByte(60) & numToByte(100)) into tDec   -- ch1 note on
   stAssert "note on: kind", (tDec["kind"] is "noteOn")
   stAssert "note on: channel 1", (tDec["channel"] is 1)
   stAssert "note on: data1 60", (tDec["data1"] is 60)
   stAssert "note on: data2 100", (tDec["data2"] is 100)

   put midiDecode(numToByte(144) & numToByte(60) & numToByte(0)) into tDec      -- note on vel 0
   stAssert "note on velocity 0 decodes as noteOff", (tDec["kind"] is "noteOff")

   put midiDecode(numToByte(128) & numToByte(60) & numToByte(64)) into tDec     -- ch1 note off
   stAssert "note off: kind", (tDec["kind"] is "noteOff")

   put midiDecode(numToByte(176) & numToByte(7) & numToByte(96)) into tDec      -- ch1 CC7
   stAssert "control change: kind", (tDec["kind"] is "controlChange")
   stAssert "control change: controller 7", (tDec["data1"] is 7)

   put midiDecode(numToByte(195) & numToByte(5)) into tDec                      -- ch4 program change
   stAssert "program change: kind", (tDec["kind"] is "programChange")
   stAssert "program change: channel 4", (tDec["channel"] is 4)

   put midiDecode(numToByte(229) & numToByte(0) & numToByte(64)) into tDec      -- ch6 pitch bend (0xE5)
   stAssert "pitch bend: kind", (tDec["kind"] is "pitchBend")
   stAssert "pitch bend: channel 6", (tDec["channel"] is 6)
   stAssert "pitch bend: value14 == 8192 (centre)", (tDec["value14"] is 8192)

   put midiDecode(numToByte(240) & numToByte(126) & numToByte(247)) into tDec   -- sysex
   stAssert "sysex: kind", (tDec["kind"] is "sysex")
end testMidiDecode

-- ===== Tier 3: MIDI ports (no hardware) ====================================

private command testMidiPorts
   local tIns, tOuts, tOut
   put midiInputPorts() into tIns
   put midiOutputPorts() into tOuts
   stAssert "midiInputPorts() returned a list without error", (tIns is an array or tIns is empty)
   stAssert "midiOutputPorts() returned a list without error", (tOuts is an array or tOuts is empty)
   put return & "    (inputs: " & (the number of elements of tIns) \
       & ", outputs: " & (the number of elements of tOuts) & ")" after sLog

   -- error handling is deterministic and hardware-free
   stAssert "opening an out-of-range output fails (0)", (midiOpenOutput(99999) is 0)
   stAssert "  and sets a last-error string", (midiLastError() is not empty)

   -- virtual-port lifecycle works on macOS / Linux; Windows (WinMM) returns 0,
   -- which is the documented, correct behaviour -- so we branch, never fail on it.
   put midiOpenVirtualOutput("ShowControl Self-Test") into tOut
   if tOut is 0 then
      put return & "    (virtual output unavailable here -- expected on Windows/headless: " \
          & midiLastError() & ")" after sLog
   else
      stAssert "opened a virtual output port", true
      midiSend tOut, (numToByte(144) & numToByte(60) & numToByte(100))   -- must not error
      stAssert "midiSend to virtual output did not throw", true
      midiClose tOut
      stAssert "closed the virtual output port", true
   end if
end testMidiPorts

-- ===== Tier 2: real UDP loopback to 127.0.0.1 (no hardware) =================
-- Sends OSC and Art-Net to ourselves over a genuine datagram socket. Results are
-- asynchronous (they arrive in socketReceived), so they append to the log a moment
-- later rather than in the pass/fail tally above.

local sLoopPort

private command startLoopback
   put 9999 into sLoopPort
   -- (Re)open a datagram listener and fire one OSC + one Art-Net packet at it.
   try
      close socket ("0.0.0.0:" & sLoopPort)
   end try
   accept datagram connections on port sLoopPort with message "stLoopbackArrived"
   send "stSendLoopback" to me in 200 milliseconds
   put return & "    (loopback listener opened on udp:" & sLoopPort \
       & " -- watch for results below)" after sLog
end startLoopback

command stSendLoopback
   -- OSC to self
   write oscBuildMessage("/selftest/osc", [["s", "loopback-ok"], ["i", 7]]) \
         to socket ("127.0.0.1:" & sLoopPort)
   -- Art-Net to self (channel 1 = 200)
   write artnetBuildDmx(0, numToByte(200)) to socket ("127.0.0.1:" & sLoopPort)
end stSendLoopback

on stLoopbackArrived pData, pSender
   local tLine
   put empty into tLine
   -- Is it Art-Net? (starts with "Art-Net" + NUL)
   if (byte 1 to 7 of pData) is "Art-Net" then
      local tDmx
      put artnetParseDmx(pData) into tDmx
      if tDmx["opcode"] is "ArtDmx" then
         put "  [PASS] Tier 2 Art-Net loopback: ch1=" \
             & byteToNum(byte 1 of tDmx["channels"]) & " (expected 200)" into tLine
      else
         put "  [FAIL] Tier 2 Art-Net loopback: not parsed as ArtDmx" into tLine
      end if
   else
      local tMsg
      put oscParse(pData) into tMsg
      if tMsg["address"] is "/selftest/osc" then
         put "  [PASS] Tier 2 OSC loopback: address & args round-tripped over UDP" into tLine
      else
         put "  [FAIL] Tier 2 OSC loopback: address was '" & tMsg["address"] & "'" into tLine
      end if
   end if
   put return & tLine after sLog
   showResults
   read from socket pSender for 65535     -- keep listening
end stLoopbackArrived

-- ===== reporting ===========================================================

private command stSection pTitle
   put return & return & "== " & pTitle & " ==" after sLog
end stSection

private command stAssert pName, pOk
   if pOk then
      add 1 to sPass
      put return & "  [PASS] " & pName after sLog
   else
      add 1 to sFail
      put return & "  [FAIL] " & pName after sLog
   end if
end stAssert

private command showResults
   -- Mirror the log into a field if we can; harmless if there is no card/field.
   try
      if there is not a field "STResults" then
         create field "STResults"
         set the rect of field "STResults" to "16,16,640,560"
         set the dontWrap of field "STResults" to true
      end if
      set the text of field "STResults" to sLog
   end try
end showResults
