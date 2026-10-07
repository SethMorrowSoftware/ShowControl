script "BoundaryTest"
-- ---------------------------------------------------------------------------
-- The LiveCode Script <-> LCB boundary, in a real engine (-ui, no window).
--
-- Every bug in the first OXT runtime pass (CHANGELOG) lived HERE, where no C
-- test and no lc-run test can reach: [...] literals that do not exist in
-- Script, nested Script arrays arriving in LCB as Arrays not Lists, numbers
-- arriving as Numbers or Strings depending on how they were made, Data vs
-- String. These tests drive the extensions exactly as a user's stack does.
-- ---------------------------------------------------------------------------
-- midi: mock

-- Script has no [...] list literal: args are built by assignment into a flat
-- numerically keyed array, exactly as scAddArg does.
on TestFlatArgsFromScript
   local tArgs, tData, tMsg
   put "i" into tArgs[1]
   put 1000 into tArgs[2]
   put "i" into tArgs[3]
   put -1 into tArgs[4]
   put "s" into tArgs[5]
   put "hello" into tArgs[6]
   put "f" into tArgs[7]
   put 1.234 into tArgs[8]
   put "f" into tArgs[9]
   put 5.678 into tArgs[10]
   put oscBuildMessage("/foo", tArgs) into tData
   -- the OSC 1.0 specification's own example, byte for byte
   TestAssertEqual "spec example built from Script numbers", TestHex(tData), \
         "2f666f6f000000002c69697366660000000003e8ffffffff68656c6c6f0000003f9df3b640b5b22d"
end TestFlatArgsFromScript

-- The engine hands a value to LCB as a Number or a String depending on its
-- history. Both must build the same bytes -- with or without the old `& empty`.
on TestNumberStringDuality
   local tA, tB, tC
   put "i" into tA[1]
   put 42 into tA[2]                 -- a literal: arrives as a Number
   put "i" into tB[1]
   put "42" into tB[2]               -- a String
   put "i" into tC[1]
   put 40 + 2 into tC[2]             -- computed
   TestAssertEqual "Number == String", TestHex(oscBuildMessage("/n", tA)), TestHex(oscBuildMessage("/n", tB))
   TestAssertEqual "computed == literal", TestHex(oscBuildMessage("/n", tC)), TestHex(oscBuildMessage("/n", tA))
   put "f" into tA[1]
   put 0.5 into tA[2]
   put "f" into tB[1]
   put (1 / 2) & empty into tB[2]
   TestAssertEqual "float Number == float String", TestHex(oscBuildMessage("/n", tA)), TestHex(oscBuildMessage("/n", tB))
end TestNumberStringDuality

-- Parsed results come back as a Script array: address/types are strings, int
-- args are numbers you can do arithmetic on, string args are strings, blobs
-- are binary data, booleans are true/false.
on TestParsedValuesInScript
   local tArgs, tMsg
   scAddArg tArgs, "i", 7
   scAddArg tArgs, "s", "seven"
   scAddArg tArgs, "b", numToByte(0) & numToByte(255)
   scAddArg tArgs, "T", empty
   scAddArg tArgs, "d", 2.5
   put oscParse(oscBuildMessage("/v", tArgs)) into tMsg
   TestAssertEqual "address", tMsg["address"], "/v"
   TestAssertEqual "types", tMsg["types"], "isbTd"
   TestAssert "int arg is a number", tMsg["args"][1] is a number
   TestAssertEqual "int arg arithmetic", tMsg["args"][1] + 1, 8
   TestAssertEqual "string arg", tMsg["args"][2], "seven"
   TestAssertEqual "blob arg bytes", TestHex(tMsg["args"][3]), "00ff"
   TestAssert "true arg is true", tMsg["args"][4] is true
   TestAssertEqual "double arg", tMsg["args"][5], 2.5
   TestAssertEqual "isBundle is false", tMsg["isBundle"], false
end TestParsedValuesInScript

-- Bundles from Script: the message list is a Script array of binary data, and
-- nested parse results are arrays of arrays.
on TestBundleFromScript
   local tArgs, tMsgs, tBundle, tParsed
   scAddArg tArgs, "i", 1
   put oscBuildMessage("/a", tArgs) into tMsgs[1]
   put empty into tArgs
   scAddArg tArgs, "s", "b"
   put oscBuildMessage("/b", tArgs) into tMsgs[2]
   put oscBuildBundle("1", tMsgs) into tBundle
   TestAssert "bundle built", tBundle is not empty
   put oscParse(tBundle) into tParsed
   TestAssertEqual "is a bundle", tParsed["isBundle"], true
   TestAssertEqual "two messages", the number of elements of tParsed["messages"], 2
   TestAssertEqual "nested access message 2 address", tParsed["messages"][2]["address"], "/b"
   TestAssertEqual "nested access message 2 arg", tParsed["messages"][2]["args"][1], "b"
end TestBundleFromScript

-- Failure is quiet at the boundary: empty results and an error string, never
-- an exception into the user's script.
on TestQuietFailures
   local tMsg, tArgs
   put oscParse("not osc") into tMsg
   TestAssertEqual "garbage parses to no address", tMsg["address"], empty
   TestAssert "garbage reports why", tMsg["error"] is not empty
   TestAssert "oscLastError agrees", oscLastError() is tMsg["error"]
   scAddArg tArgs, "q", 1
   TestAssertEqual "unknown type builds nothing", oscBuildMessage("/x", tArgs), empty
   TestAssertEqual "out-of-range universe builds nothing", artnetBuildDmx(40000, numToByte(1)), empty
   TestAssert "artnetLastError explains", artnetLastError() is not empty
end TestQuietFailures

-- An EMPTY value into a typed Integer parameter is an engine type-conversion
-- error at the boundary (xtalk engine note 6.4) -- it throws. Pin that, so the
-- docs' advice (guard handles before calling) stays true.
on TestEmptyIntoIntegerThrows
   local tThrew
   put false into tThrew
   try
      get artnetBuildDmx(empty, numToByte(1))
   catch tErr
      put true into tThrew
   end try
   TestAssert "empty universe throws a type error (documented)", tThrew
end TestEmptyIntoIntegerThrows

on TestArtnetFromScript
   local tCh, tPkt, tP
   put numToByte(255) & numToByte(128) & numToByte(0) & numToByte(64) into tCh
   put artnetBuildDmx(258, tCh) into tPkt
   TestAssertEqual "ArtDmx bytes from Script", TestHex(tPkt), "4172742d4e6574000050000e000002010004ff800040"
   put artnetParseDmx(tPkt) into tP
   TestAssertEqual "universe", tP["universe"], 258
   TestAssertEqual "channel 1 via byteToNum", byteToNum(byte 1 of tP["channels"]), 255
   -- a Script STRING for the channel data is accepted as its bytes
   put artnetParseDmx(artnetBuildDmx(0, "AB")) into tP
   TestAssertEqual "string channels become bytes", TestHex(tP["channels"]), "4142"
end TestArtnetFromScript

on TestMidiFromScript
   local tDec, tPorts, tIn, tOut, tEv
   put midiDecode(numToByte(229) & numToByte(0) & numToByte(64)) into tDec
   TestAssertEqual "midiDecode kind", tDec["kind"], "pitchBend"
   TestAssertEqual "midiDecode value14", tDec["value14"], 8192
   put midiInputPorts() into tPorts
   TestAssertEqual "port list arrives as a Script array", the number of elements of tPorts, 2
   TestAssertEqual "port name", tPorts[1], "MockPort-0-aLongishName"
   MockReset
   MockSetLoopback true, 0
   put midiOpenVirtualInput("lcs in") into tIn
   put midiOpenVirtualOutput("lcs out") into tOut
   midiControlChange tOut, 2, 7, 100
   put midiPoll(tIn) into tEv
   TestAssertEqual "polled one event", the number of elements of tEv, 1
   TestAssertEqual "kind", tEv[1]["kind"], "controlChange"
   TestAssertEqual "channel", tEv[1]["channel"], 2
   TestAssertEqual "value", tEv[1]["data2"], 100
   midiClose tIn
   midiClose tOut
   MockReset
end TestMidiFromScript
