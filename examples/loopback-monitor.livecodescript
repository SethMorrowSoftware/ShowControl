-- loopback-monitor.livecodescript
-- ---------------------------------------------------------------------------
-- An INTERACTIVE, hardware-free monitor for ShowControl. It builds its own UI
-- (no manual layout), then loops OSC and Art-Net back to 127.0.0.1 so you can
-- SEE the whole build -> send -> receive -> parse path working with no devices:
--
--   * a row of 16 "virtual DMX fixtures" (rectangles) lights up from looped-back
--     Art-Net frames -- proving artnetBuildDmx -> UDP -> artnetParseDmx visually;
--   * an OSC echo: type into the field and it is sent as an OSC message to self,
--     parsed on arrival, and logged.
--
-- HOW TO RUN
--   1. Install/Test-load the osc and artnet extensions in OXT.
--   2. Paste this into a stack script, open the stack, and run in the Message Box:
--        scStartMonitor
--      Click "Sweep" to animate the fixtures; type + "Send OSC" to echo a message.
--      Run  scStopMonitor  to tear it down.
--
-- Pure illustrative LiveCode Script (checked by tools/check-livecodescript.py);
-- needs a real OXT run, which is the point.
-- ---------------------------------------------------------------------------

local sMonPort, sSweepOn, sSweepVal

constant kFixtures = 16

-- ===== setup / teardown ====================================================

command scStartMonitor
   put 6455 into sMonPort       -- a test port (not the real 6454) to avoid clashes
   scBuildUI
   try
      close socket ("0.0.0.0:" & sMonPort)
   end try
   accept datagram connections on port sMonPort with message "scMonArrived"
   scLog "Monitor started. Listening on udp:" & sMonPort & ". Click Sweep, or Send OSC."
end scStartMonitor

command scStopMonitor
   put false into sSweepOn
   try
      close socket ("0.0.0.0:" & sMonPort)
   end try
   scLog "Monitor stopped."
end scStopMonitor

private command scBuildUI
   local i, tLeft, tTop, tW
   put 30 into tW
   -- 16 fixture rectangles
   repeat with i = 1 to kFixtures
      if there is not a graphic ("scFix" & i) then
         create graphic ("scFix" & i)
      end if
      put 16 + (i - 1) * (tW + 6) into tLeft
      set the style of graphic ("scFix" & i) to "rectangle"
      set the rect of graphic ("scFix" & i) to (tLeft & ",40," & (tLeft + tW) & ",160")
      set the backgroundColor of graphic ("scFix" & i) to "20,20,28"
      set the label of graphic ("scFix" & i) to i
   end repeat
   -- Sweep button
   if there is not a button "scSweep" then create button "scSweep"
   set the name of button "scSweep" to "scSweep"
   set the label of button "scSweep" to "Sweep"
   set the rect of button "scSweep" to "16,180,120,210"
   set the script of button "scSweep" to "on mouseUp" & return & "scToggleSweep" & return & "end mouseUp"
   -- OSC echo field + button
   if there is not a field "scOscIn" then create field "scOscIn"
   set the rect of field "scOscIn" to "130,180,420,210"
   if there is not a button "scOscSend" then create button "scOscSend"
   set the label of button "scOscSend" to "Send OSC"
   set the rect of button "scOscSend" to "430,180,540,210"
   set the script of button "scOscSend" to "on mouseUp" & return & "scSendOsc" & return & "end mouseUp"
   -- log
   if there is not a field "scMonLog" then create field "scMonLog"
   set the rect of field "scMonLog" to "16,220,560,520"
   set the dontWrap of field "scMonLog" to true
   put empty into field "scMonLog"
end scBuildUI

-- ===== Art-Net sweep (build -> loopback -> parse -> render) =================

command scToggleSweep
   if sSweepOn is true then
      put false into sSweepOn
      scLog "Sweep stopped."
   else
      put true into sSweepOn
      put 0 into sSweepVal
      scLog "Sweep running (Art-Net to self)..."
      scSweepTick
   end if
end scToggleSweep

command scSweepTick
   if sSweepOn is not true then exit scSweepTick
   -- Build a 16-channel frame: a moving brightness ramp across the fixtures.
   local tChannels, i, v
   put empty into tChannels
   repeat with i = 1 to kFixtures
      put (sSweepVal + i * 16) mod 256 into v
      put numToByte(v) after tChannels
   end repeat
   -- Send it to ourselves; it comes back in scMonArrived and repaints the rig.
   write artnetBuildDmx(0, tChannels) to socket ("127.0.0.1:" & sMonPort)
   add 8 to sSweepVal
   if sSweepVal > 255 then put 0 into sSweepVal
   send "scSweepTick" to me in 40 milliseconds      -- ~25 fps, well under the 44 Hz ceiling
end scSweepTick

private command scRenderDmx pChannels
   local i, v
   repeat with i = 1 to kFixtures
      if i > the number of bytes in pChannels then exit repeat
      put byteToNum(byte i of pChannels) into v
      -- grayscale brightness = channel level
      set the backgroundColor of graphic ("scFix" & i) to (v & "," & v & "," & v)
   end repeat
end scRenderDmx

-- ===== OSC echo ============================================================

command scSendOsc
   local tText, tPair, tArgs
   put the text of field "scOscIn" into tText
   if tText is empty then put "hello-from-oxt" into tText
   -- Build the OSC args array by assignment -- xTalk has no [...] list literal.
   put "s" into tPair[1]
   put tText into tPair[2]
   put tPair into tArgs[1]
   write oscBuildMessage("/monitor/echo", tArgs) to socket ("127.0.0.1:" & sMonPort)
end scSendOsc

-- ===== inbound (one datagram socket for both protocols) ====================

on scMonArrived pA, pB
   -- The datagram callback's parameter order is engine-dependent; pick whichever
   -- parameter looks like a packet (the other is the socket id).
   local tData, tDmx, tMsg
   if scLooksLikePacket(pA) then
      put pA into tData
   else
      put pB into tData
   end if
   if (the number of bytes in tData) >= 7 and (byte 1 to 7 of tData) is "Art-Net" then
      put artnetParseDmx(tData) into tDmx
      if tDmx["opcode"] is "ArtDmx" then
         scRenderDmx tDmx["channels"]
      end if
   else
      put oscParse(tData) into tMsg
      if tMsg["address"] is "/monitor/echo" then
         scLog "OSC echo received: " & (tMsg["args"][1])
      end if
   end if
   -- accepted datagram connections keep delivering; no re-read needed.
end scMonArrived

private function scLooksLikePacket pX
   if (the number of bytes in pX) < 1 then
      return false
   end if
   if (char 1 of pX) is "/" then
      return true
   end if
   if (the number of bytes in pX) >= 7 then
      if (byte 1 to 7 of pX) is "Art-Net" then
         return true
      end if
      if (byte 1 to 7 of pX) is "#bundle" then
         return true
      end if
   end if
   return false
end scLooksLikePacket

-- ===== util ================================================================

private command scLog pText
   try
      put pText & return after field "scMonLog"
   end try
end scLog
