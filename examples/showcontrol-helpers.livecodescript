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
local sMidiTarget         -- long id of the object that receives the dispatched events

-- ===== MIDI poll dispatcher ================================================

-- Start listening on an open input handle (from midiOpenInput). Optional second
-- arg sets the poll interval in ms (default 3). Events (scNoteOn, scMidiCC, ...)
-- are sent to the object that called this handler -- or to pTarget if given --
-- and travel up ITS message path, so `on scNoteOn` in that object, its card or
-- its stack receives them.
--
-- Why the listener is remembered here: the poll re-arms itself with
-- `send ... to me in N milliseconds`, and inside a timer-delivered message
-- `the target` is this LIBRARY, not your card. Dispatching to `the target`
-- (as this helper once did) silently delivered every timer-polled event to the
-- library itself whenever the helpers were used via `start using` -- caught by
-- the headless suite (tests/lcs/helpers_test.livecodescript).
on scMidiStartListening pHandle, pIntervalMs, pTarget
   if pIntervalMs is empty then put 3 into pIntervalMs
   if pTarget is empty then put the long id of the target into pTarget
   put pTarget into sMidiTarget
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
   -- `dispatch ... with` passes REAL parameters. The old form,
   --    send "scNoteOn" && channel && note && velocity to ...
   -- built the string "scNoteOn 1 60 100", and a message string's parameters
   -- are separated by COMMAS -- so the whole "1 60 100" arrived in pChannel and
   -- pNote/pVelocity were empty (caught by tests/lcs/helpers_test).
   repeat for each element tEvent in tEvents
      switch tEvent["kind"]
         case "noteOn"
            dispatch "scNoteOn" to sMidiTarget with tEvent["channel"], tEvent["data1"], tEvent["data2"]
            break
         case "noteOff"
            dispatch "scNoteOff" to sMidiTarget with tEvent["channel"], tEvent["data1"], tEvent["data2"]
            break
         case "controlChange"
            dispatch "scMidiCC" to sMidiTarget with tEvent["channel"], tEvent["data1"], tEvent["data2"]
            break
         case "programChange"
            dispatch "scProgramChange" to sMidiTarget with tEvent["channel"], tEvent["data1"]
            break
         case "pitchBend"
            dispatch "scPitchBend" to sMidiTarget with tEvent["channel"], tEvent["value14"]
            break
         default
            -- channelPressure / polyAftertouch / sysex / system: hand off the kind,
            -- the channel, and the whole decoded record (raw bytes in ["bytes"])
            dispatch "scMidiOther" to sMidiTarget with tEvent["kind"], tEvent["channel"], tEvent
      end switch
   end repeat
   send "scMidiPollAndDispatch" && pHandle to me in sMidiPollMs milliseconds
end scMidiPollAndDispatch

-- ===== UDP transport ========================================================

-- Make sure a datagram socket to "host:port" is open before writing to it. The
-- engine only writes to an OPEN socket: `write ... to socket "h:p"` on a name
-- that was never opened sets the result to "socket is not open" and sends
-- NOTHING, silently (engine/src/exec-network.cpp, MCNetworkExecWriteToSocket).
-- Opening is idempotent here, so call it before every send.
-- (Broadcast -- e.g. ArtPoll to 255.255.255.255 -- additionally needs
-- `set the allowDatagramBroadcasts to true`.)
command scEnsureDatagramSocket pHostPort
   if pHostPort is not among the lines of the openSockets then
      open datagram socket to pHostPort
   end if
end scEnsureDatagramSocket

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
   scEnsureDatagramSocket pHost & ":" & pPort
   write tData to socket (pHost & ":" & pPort)
end scOscSend

-- Route inbound OSC by address. Wire it up with
--     accept datagram connections on port 9000 with message "socketReceived"
-- and write your own `on scOscMessage pAddress, pArgs, pSender` to react
-- (pArgs is the parsed argument array; bundles are unpacked, nested ones too).
--
-- The engine calls a datagram message with (sender "host:port", data, socket)
-- -- in THAT order (engine/src/opensslsocket.cpp, MCSocket::readsome). This
-- handler once declared (pData, pHostAndPort), so it parsed the sender's address
-- as OSC and never routed anything; it also issued a blocking `read from socket`,
-- which an accepted datagram socket does not need (every datagram arrives as its
-- own message). For callers wired the old way round, a packet in the first
-- parameter is still recognised.
on socketReceived pSender, pData
   local tMsg
   if scLooksLikeOsc(pSender) and not scLooksLikeOsc(pData) then
      put oscParse(pSender) into tMsg
      put pData into pSender
   else
      put oscParse(pData) into tMsg
   end if
   scOscRoute tMsg, pSender, the long id of the target
end socketReceived

-- Deliver one parsed message (or every message of a bundle, recursively).
command scOscRoute pMsg, pSender, pTarget
   local tSub
   if pMsg["isBundle"] is true then
      repeat for each element tSub in pMsg["messages"]
         scOscRoute tSub, pSender, pTarget
      end repeat
   else if pMsg["address"] is not empty then
      dispatch "scOscMessage" to pTarget with pMsg["address"], pMsg["args"], pSender
   end if
end scOscRoute

function scLooksLikeOsc pX
   if char 1 of pX is "/" then
      return true
   end if
   return (byte 1 to 8 of pX) is ("#bundle" & numToByte(0))
end scLooksLikeOsc

-- ===== Art-Net convenience =================================================

-- Send a DMX universe (pChannels: up to 512 bytes) to an Art-Net node.
on scArtnetSend pHost, pUniverse, pChannels
   scEnsureDatagramSocket pHost & ":6454"
   write artnetBuildDmx(pUniverse, pChannels) to socket (pHost & ":6454")
end scArtnetSend

-- The "optional convenience" verb named in the API reference: build-plus-write
-- in one call (argument order matches docs/api-reference.md). The Art-Net
-- extension itself is pure LCB and cannot open sockets, so this lives here in
-- the script helper layer.
on artnetSendDmx pUniverse, pChannels, pHost
   scEnsureDatagramSocket pHost & ":6454"
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
