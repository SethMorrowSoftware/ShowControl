-- midi-osc-artnet-demo.livecodescript
-- ---------------------------------------------------------------------------
-- A small, self-contained demo that wires all three ShowControl extensions
-- together, the way an installation or show-control surface would:
--
--   * a hardware MIDI knob (CC) drives an on-screen fader,
--   * that fader's value is forwarded out as OSC (e.g. to Resolume/TouchOSC),
--   * and the same value drives DMX channel 1 of Art-Net universe 0 (a dimmer).
--
-- It depends on the helper handlers in showcontrol-helpers.livecodescript
-- (scMidiStartListening, scArtnetSend, scArtnetOneChannel). Paste both into the
-- same stack. Set the three globals below for your rig.
--
-- This is illustrative LiveCode Script: it assumes a scrollbar named "Master"
-- and a field "Log". It is checked by tools/check-livecodescript.py but, like all
-- OXT script, needs a real OXT pass to run.
-- ---------------------------------------------------------------------------

local sMidiIn             -- open MIDI input handle
local sOscHost, sOscPort  -- where outbound OSC goes
local sArtnetHost         -- the Art-Net node's IP

on openCard
   put "127.0.0.1" into sOscHost
   put 7000 into sOscPort
   put "2.0.0.10" into sArtnetHost

   -- 1. MIDI: open the first input port and start the poll dispatcher.
   put midiInputPorts() into tPorts
   if the number of elements of tPorts is 0 then
      scLog "No MIDI input ports found."
   else
      put midiOpenInput(0) into sMidiIn       -- 0 = first port
      if sMidiIn is 0 then
         scLog "Could not open MIDI input:" && midiLastError()
      else
         scLog "Listening to MIDI port:" && (element 1 of tPorts)
         scMidiStartListening sMidiIn, 3
      end if
   end if

   -- 2. OSC: open a connectionless UDP socket so we can both send and receive.
   accept datagram connections on port 9000 with message "scOscDatagram"
end openCard

on closeCard
   scMidiStopListening
   if sMidiIn is not empty and sMidiIn is not 0 then midiClose sMidiIn
   close socket "9000"
end closeCard

-- ===== MIDI events (dispatched by the helper) ==============================

-- A control-change knob: CC 7 (channel volume) drives the Master fader, and we
-- fan the value out to OSC and Art-Net.
on scMidiCC pChannel, pController, pValue
   if pController is 7 then
      set the thumbPosition of scrollbar "Master" to pValue   -- 0..127
      forwardMaster pValue
   end if
end scMidiCC

on scNoteOn pChannel, pNote, pVelocity
   scLog "Note on:" && pNote && "vel" && pVelocity
   -- echo the note straight out as OSC, TouchOSC-style
   scOscSend sOscHost, sOscPort, ("/note/" & pNote), [["i", pVelocity]]
end scNoteOn

-- ===== the on-screen fader (mouse or MIDI both land here) ==================

on scrollbarDrag pNewValue
   if the short name of the target is "Master" then
      forwardMaster pNewValue
   end if
end scrollbarDrag

-- Forward a 0..127 master level to OSC (normalized 0..1) and Art-Net (0..255).
private command forwardMaster pValue127
   local tNorm, tDmx
   put pValue127 / 127 into tNorm
   scOscSend sOscHost, sOscPort, "/master/level", [["f", tNorm]]

   put round(tNorm * 255) into tDmx
   scArtnetSend sArtnetHost, 0, scArtnetOneChannel(1, tDmx)
end forwardMaster

-- ===== inbound OSC =========================================================

on scOscDatagram pData, pSender
   local tMsg
   put oscParse(pData) into tMsg
   if tMsg["address"] is "/master/level" then
      -- a remote surface moved our master; reflect it locally
      local tArgs
      put tMsg["args"] into tArgs
      set the thumbPosition of scrollbar "Master" to round((element 1 of tArgs) * 127)
   end if
   read from socket pSender for 65535
end scOscDatagram

-- ===== tiny logger =========================================================

private command scLog pText
   if there is a field "Log" then
      put pText & return after field "Log"
   end if
end scLog
