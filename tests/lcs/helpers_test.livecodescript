script "HelpersTest"
-- ---------------------------------------------------------------------------
-- The shipped helper library (examples/showcontrol-helpers.livecodescript),
-- loaded the way the docs recommend -- as a library stack in the message path
-- (`start using`) -- and driven headlessly in a real engine.
--
-- The MIDI poll dispatcher is the interesting part: it re-arms itself with
-- `send ... in N milliseconds` and fires scNoteOn / scMidiCC / ... back at the
-- caller. Inbound MIDI comes from the MOCK midi library (no hardware), so the
-- whole loop -- timer, poll, drain, decode, dispatch -- runs for real.
-- ---------------------------------------------------------------------------
-- midi: mock

local sEvents

on TestSetup
   put empty into sEvents
   MockReset
end TestSetup

-- the dispatcher's callbacks land here (this stack started the listening)
on scNoteOn pChannel, pNote, pVelocity
   put "noteOn" && pChannel && pNote && pVelocity & return after sEvents
end scNoteOn

on scNoteOff pChannel, pNote, pVelocity
   put "noteOff" && pChannel && pNote && pVelocity & return after sEvents
end scNoteOff

on scMidiCC pChannel, pController, pValue
   put "cc" && pChannel && pController && pValue & return after sEvents
end scMidiCC

on scProgramChange pChannel, pProgram
   put "program" && pChannel && pProgram & return after sEvents
end scProgramChange

on scPitchBend pChannel, pValue14
   put "bend" && pChannel && pValue14 & return after sEvents
end scPitchBend

on scMidiOther pKind, pChannel
   put "other" && pKind && pChannel & return after sEvents
end scMidiOther

function scEventCount
   return the number of lines of sEvents
end scEventCount

-- Events that arrive AFTER listening started -- i.e. on the timer-driven polls,
-- not the first synchronous one -- must still reach the stack that asked.
on TestDispatcherDeliversTimerPolledEvents
   local tIn
   put midiOpenVirtualInput("helpers in") into tIn
   TestAssert "opened the mock input", tIn > 0
   scMidiStartListening tIn, 2
   TestPump 30                                  -- several poll ticks, nothing queued yet
   MockPush numToByte(144) & numToByte(60) & numToByte(100), 0
   MockPush numToByte(176) & numToByte(7) & numToByte(64), 0
   MockPush numToByte(194) & numToByte(5), 0
   MockPush numToByte(224) & numToByte(0) & numToByte(64), 0
   MockPush numToByte(128) & numToByte(60) & numToByte(0), 0
   MockPush numToByte(240) & numToByte(126) & numToByte(247), 0
   TestPump 200
   scMidiStopListening
   -- 0 here means the timer polls DID drain the queue, so any missing event was
   -- dispatched to the wrong object; 6 would mean the timer never polled.
   TestAssertEqual "the timer-driven polls drained the queue", MockQueueDepth(), 0
   TestAssertEqual "all six events dispatched to the listening stack", the number of lines of sEvents, 6
   TestAssertEqual "note on", line 1 of sEvents, "noteOn 1 60 100"
   TestAssertEqual "control change", line 2 of sEvents, "cc 1 7 64"
   TestAssertEqual "program change ch3", line 3 of sEvents, "program 3 5"
   TestAssertEqual "pitch bend centre", line 4 of sEvents, "bend 1 8192"
   TestAssertEqual "note off", line 5 of sEvents, "noteOff 1 60 0"
   TestAssertEqual "sysex handed off raw", line 6 of sEvents, "other sysex 0"
   midiClose tIn
end TestDispatcherDeliversTimerPolledEvents

-- scMidiStopListening really stops: nothing dispatched after it.
on TestDispatcherStops
   local tIn
   put midiOpenVirtualInput("stop in") into tIn
   scMidiStartListening tIn, 2
   TestPump 20
   scMidiStopListening
   TestPump 20
   MockPush numToByte(144) & numToByte(1) & numToByte(1), 0
   TestPump 100
   TestAssertEqual "no dispatch after stop", the number of lines of sEvents, 0
   TestAssertEqual "the message is still queued for a later poll", MockQueueDepth(), 1
   midiClose tIn
end TestDispatcherStops

-- The loopback cable end to end through the helper: send with the convenience
-- senders, receive through the dispatcher.
on TestDispatcherWithLoopback
   local tIn, tOut
   MockSetLoopback true, 0
   put midiOpenVirtualInput("loop in") into tIn
   put midiOpenVirtualOutput("loop out") into tOut
   scMidiStartListening tIn, 2
   TestPump 10
   midiNoteOn tOut, 10, 36, 127
   midiControlChange tOut, 16, 1, 0
   TestPump 150
   scMidiStopListening
   TestAssertEqual "drum note on ch10", line 1 of sEvents, "noteOn 10 36 127"
   TestAssertEqual "mod wheel ch16", line 2 of sEvents, "cc 16 1 0"
   midiClose tIn
   midiClose tOut
end TestDispatcherWithLoopback

on TestAddArgBuildsFlatArgs
   local tArgs, tMsg
   scAddArg tArgs, "i", 60
   scAddArg tArgs, "f", 0.8
   scAddArg tArgs, "s", "go"
   scAddArg tArgs, "b", numToByte(1) & numToByte(2)
   scAddArg tArgs, "T", empty
   TestAssertEqual "scAddArg writes type,value pairs", the number of elements of tArgs, 10
   put oscParse(oscBuildMessage("/synth/note", tArgs)) into tMsg
   TestAssertEqual "types survive", tMsg["types"], "ifsbT"
   TestAssertEqual "int survives", tMsg["args"][1], 60
   TestAssertEqual "blob survives", TestHex(tMsg["args"][4]), "0102"
end TestAddArgBuildsFlatArgs

on TestArtnetOneChannel
   local tCh
   put scArtnetOneChannel(3, 200) into tCh
   TestAssertEqual "one-channel frame", TestHex(tCh), "0000c8"
   put scArtnetOneChannel(1, 255) into tCh
   TestAssertEqual "channel 1", TestHex(tCh), "ff"
end TestArtnetOneChannel

-- ---- OSC routing (socketReceived), driven by calling the datagram message the
-- way the engine does -- (sender, data, socket), engine opensslsocket.cpp --
-- so the routing is proven even where sockets cannot run (udp_test does the
-- real-socket version).
local sOscSeen

on scOscMessage pAddress, pArgs, pSender
   put pAddress & "|" & pArgs[1] & "|" & pSender & return after sOscSeen
end scOscMessage

on TestSocketReceivedRoutesOsc
   local tArgs
   put empty into sOscSeen
   scAddArg tArgs, "i", 42
   socketReceived "127.0.0.1:5000", oscBuildMessage("/route/me", tArgs), "9000"
   TestAssertEqual "routed with address, args and sender", line 1 of sOscSeen, "/route/me|42|127.0.0.1:5000"
end TestSocketReceivedRoutesOsc

on TestSocketReceivedUnpacksBundles
   local tArgs, tA, tInner, tMsgs, tOuter
   put empty into sOscSeen
   scAddArg tArgs, "s", "x"
   put oscBuildMessage("/a", tArgs) into tA
   put tA into tMsgs[1]
   put oscBuildBundle("1", tMsgs) into tInner
   put tInner into tMsgs[1]
   put oscBuildMessage("/b", tArgs) into tMsgs[2]
   put oscBuildBundle("1", tMsgs) into tOuter
   socketReceived "10.0.0.2:8000", tOuter, "9000"
   TestAssertEqual "nested bundle: both messages routed", the number of lines of sOscSeen, 2
   TestAssertEqual "inner message first", item 1 of line 1 of sOscSeen, "/a|x|10.0.0.2:8000"
   TestAssertEqual "then the outer one", line 2 of sOscSeen, "/b|x|10.0.0.2:8000"
end TestSocketReceivedUnpacksBundles

-- Callers wired the OLD way round (data first) still route.
on TestSocketReceivedToleratesLegacyOrder
   local tArgs
   put empty into sOscSeen
   scAddArg tArgs, "i", 1
   socketReceived oscBuildMessage("/legacy", tArgs), "127.0.0.1:7000"
   TestAssertEqual "legacy (data, sender) order", line 1 of sOscSeen, "/legacy|1|127.0.0.1:7000"
end TestSocketReceivedToleratesLegacyOrder

on TestSocketReceivedIgnoresGarbage
   put empty into sOscSeen
   socketReceived "127.0.0.1:5000", "not an osc packet", "9000"
   TestAssertEqual "garbage routes nothing (and does not throw)", sOscSeen, empty
end TestSocketReceivedIgnoresGarbage
