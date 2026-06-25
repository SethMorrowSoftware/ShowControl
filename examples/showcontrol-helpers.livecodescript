-- showcontrol-helpers.livecodescript
-- ---------------------------------------------------------------------------
-- Reusable LiveCode Script helpers for the ShowControl extensions. Paste this
-- into a stack/card script (or a library stack you `start using`), then your own
-- handlers only need to react to events.
--
-- The headline helper is the MIDI poll dispatcher (README section 6.4): because
-- MIDI input is polled (never a callback into script), this runs a timer loop
-- that drains midiPoll and dispatches one message per `send` to the target, so
-- you write `on scNoteOn ...` / `on scMidiCC ...` instead of a poll loop.
-- ---------------------------------------------------------------------------

local sMidiListening      -- "true" while the poll loop is running
local sMidiPollMs         -- poll interval (ms); lower = tighter latency, more CPU

-- ===== MIDI poll dispatcher ================================================

-- Start listening on an open input handle (from midiOpenInput). Optional second
-- arg sets the poll interval in ms (default 3).
on scMidiStartListening pHandle, pIntervalMs
   if pIntervalMs is empty then put 3 into pIntervalMs
   put pIntervalMs into sMidiPollMs
   put "true" into sMidiListening
   scMidiPollAndDispatch pHandle
end scMidiStartListening

on scMidiStopListening
   put "false" into sMidiListening
end scMidiStopListening

on scMidiPollAndDispatch pHandle
   if sMidiListening is not "true" then exit scMidiPollAndDispatch
   put midiPoll(pHandle) into tEvents
   repeat for each element tEvent in tEvents
      switch tEvent["kind"]
         case "noteOn"
            send "scNoteOn" && tEvent["channel"] && tEvent["data1"] && tEvent["data2"] to the target
            break
         case "noteOff"
            send "scNoteOff" && tEvent["channel"] && tEvent["data1"] && tEvent["data2"] to the target
            break
         case "controlChange"
            send "scMidiCC" && tEvent["channel"] && tEvent["data1"] && tEvent["data2"] to the target
            break
         case "programChange"
            send "scProgramChange" && tEvent["channel"] && tEvent["data1"] to the target
            break
         case "pitchBend"
            send "scPitchBend" && tEvent["channel"] && tEvent["value14"] to the target
            break
         default
            -- channelPressure / polyAftertouch / sysex / system: hand off raw
            send "scMidiOther" && tEvent["kind"] && tEvent["channel"] to the target
      end switch
   end repeat
   send "scMidiPollAndDispatch pHandle" to me in sMidiPollMs milliseconds
end scMidiPollAndDispatch

-- ===== OSC convenience =====================================================

-- Build an OSC args list the way LiveCode Script must. xTalk has NO [...]
-- list-literal expression (that is LiveCode Builder syntax), so you cannot write
-- oscBuildMessage("/x", [["i", 60]]) in a .livecodescript. Instead build the args
-- array by assignment: scAddArg appends one type/value pair as TWO consecutive
-- elements (a FLAT list -- a nested LiveCode array does not survive the Script->LCB
-- boundary as a list-of-lists, so we flatten to scalars), to an args array passed
-- BY REFERENCE. Hand the finished array to oscBuildMessage / scOscSend. For the
-- no-value OSC types (T/F/N/I) pass empty for the value -- it is ignored.
--
--   local tArgs
--   scAddArg tArgs, "i", 60
--   scAddArg tArgs, "f", 0.8
--   put oscBuildMessage("/synth/note", tArgs) into tData
command scAddArg @pArgs, pType, pValue
   put pType into pArgs[(the number of elements of pArgs) + 1]
   if pType is "b" then
      -- Blob: raw Data must cross unchanged (oscBuildMessage reads it as Data).
      put pValue into pArgs[(the number of elements of pArgs) + 1]
   else
      -- Force every non-blob value to a STRING before it enters the args list.
      -- oscBuildMessage's internal addArg takes the value as LCB type `any`, and
      -- LCB cannot introspect an `any` or convert a Number to a String (there is
      -- no `is a number`, no try/catch -- see the LCB Language Reference). So a
      -- value the engine kept as a Number (e.g. the integer 60) would fail inside
      -- LCB. `& empty` is LiveCode's force-to-string: it collapses the number/
      -- string duality to a pure string, which arrives in LCB as a String and is
      -- parsed in C. (Pass floats/doubles you need at full precision as strings --
      -- a string is preserved verbatim; a number is stringified via numberFormat.)
      put (pValue & empty) into pArgs[(the number of elements of pArgs) + 1]
   end if
end scAddArg

-- Send one OSC message to host:port. pArgs is an args array built with scAddArg.
on scOscSend pHost, pPort, pAddress, pArgs
   local tData
   put oscBuildMessage(pAddress, pArgs) into tData
   if tData is empty then
      -- oscLastError() explains why
      exit scOscSend
   end if
   -- Use a uniquely-named connectionless UDP socket per host:port.
   write tData to socket (pHost & ":" & pPort)
end scOscSend

-- Drop this into a card with an open datagram socket; it routes by address.
-- (Wire your own `on scOscMessage tAddress, tArgs` to react.)
on socketReceived pData, pHostAndPort
   local tMsg
   put oscParse(pData) into tMsg
   if tMsg["isBundle"] then
      local tSub
      repeat for each element tSub in tMsg["messages"]
         send "scOscMessage" && tSub["address"] to the target
      end repeat
   else if tMsg["address"] is not empty then
      send "scOscMessage" && tMsg["address"] to the target
   end if
   -- keep the datagram socket reading
   read from socket pHostAndPort for 65535
end socketReceived

-- ===== Art-Net convenience =================================================

-- Send a DMX universe (pChannels: up to 512 bytes) to an Art-Net node.
on scArtnetSend pHost, pUniverse, pChannels
   write artnetBuildDmx(pUniverse, pChannels) to socket (pHost & ":6454")
end scArtnetSend

-- The "optional convenience" verb named in the API reference: build-plus-write
-- in one call (argument order matches docs/api-reference.md). The Art-Net
-- extension itself is pure LCB and cannot open sockets, so this lives here in
-- the script helper layer.
on artnetSendDmx pUniverse, pChannels, pHost
   write artnetBuildDmx(pUniverse, pChannels) to socket (pHost & ":6454")
end artnetSendDmx

-- Build a single-channel DMX frame (1-based channel, 0..255 level) - handy for
-- driving one dimmer from a fader without assembling 512 bytes by hand.
function scArtnetOneChannel pChannel, pLevel
   local tChannels
   put empty into tChannels
   repeat with i = 1 to pChannel
      if i is pChannel then
         put numToByte(pLevel) after tChannels
      else
         put numToByte(0) after tChannels
      end if
   end repeat
   return tChannels
end scArtnetOneChannel
