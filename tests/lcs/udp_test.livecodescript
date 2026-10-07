script "UdpTest"
-- ---------------------------------------------------------------------------
-- Real UDP through the ENGINE's own sockets, to 127.0.0.1 -- the transport every
-- ShowControl app uses (the extensions only build/parse Data; the engine owns
-- the socket). No hardware: the engine talks to itself.
--
-- Runs only where the engine delivers socket events without a UI (the runner's
-- probe decides; OXT-Beyond >= 0.2.1-rc.2 on Windows). Elsewhere it SKIPS, and
-- under --require-all (CI) a skip is a failure.
-- ---------------------------------------------------------------------------
-- needs: sockets
-- midi: mock

local sDatagrams, sOscSeen

on TestSetup
   put empty into sDatagrams
   put empty into sOscSeen
end TestSetup

on udpArrived pSender, pData, pSocket
   put pSender & tab & TestHex(pData) & return after sDatagrams
end udpArrived

function udpCount
   if sDatagrams is empty then
      return empty
   end if
   return the number of lines of sDatagrams
end udpCount

on scOscMessage pAddress, pArgs, pSender
   put pAddress & "|" & pArgs[1] & "|" & pArgs[2] & return after sOscSeen
end scOscMessage

function oscSeen
   return sOscSeen
end oscSeen

-- Pins the engine's datagram-callback contract the helpers rely on:
-- (sender "host:port", data, socket), data intact byte for byte.
on TestDatagramCallbackContract
   local tPort, tPkt
   put TestPortBase() + 11 into tPort
   accept datagram connections on port tPort with message "udpArrived"
   TestAssertEqual "accept succeeded", the result, empty
   put artnetBuildDmx(7, numToByte(1) & numToByte(2) & numToByte(3)) into tPkt
   open datagram socket to ("127.0.0.1:" & tPort)
   write tPkt to socket ("127.0.0.1:" & tPort)
   TestAssert "a datagram arrived", TestWaitFor(the long id of me, "udpCount", 3000)
   TestAssert "param 1 is the sender host:port", item 1 of line 1 of sDatagrams contains "127.0.0.1:"
   TestAssertEqual "param 2 is the data, intact", item 2 of line 1 of sDatagrams, TestHex(tPkt)
   close socket tPort
   close socket ("127.0.0.1:" & tPort)
end TestDatagramCallbackContract

-- Pins the contract the examples were missing: writing to a "host:port" that was
-- never OPENED sends nothing and says so in the result (exec-network.cpp).
on TestUnopenedWriteSendsNothing
   local tPort, tResult
   put TestPortBase() + 14 into tPort
   accept datagram connections on port tPort with message "udpArrived"
   write "never-opened" to socket ("127.0.0.1:" & tPort)
   put the result into tResult
   TestPump 500
   -- exec-network.cpp sets the result to "socket is not open"; not every engine
   -- build surfaces it, so pin the observable effect and record the result.
   TestDiagnostic "result of the unopened write: [" & tResult & "]"
   TestAssertEqual "nothing was delivered", sDatagrams, empty
   close socket tPort
end TestUnopenedWriteSendsNothing

-- The documented helper path, end to end over a real socket: scOscSend out,
-- socketReceived (the helper) in, scOscMessage delivered here.
on TestOscThroughHelpersOverUdp
   local tPort, tArgs
   put TestPortBase() + 12 into tPort
   accept datagram connections on port tPort with message "socketReceived"
   scAddArg tArgs, "s", "over-udp"
   scAddArg tArgs, "i", 99
   scOscSend "127.0.0.1", tPort, "/udp/helper", tArgs
   TestAssert "scOscMessage delivered", TestWaitFor(the long id of me, "oscSeen", 3000)
   TestAssertEqual "address and args survived UDP", line 1 of sOscSeen, "/udp/helper|over-udp|99"
   close socket tPort
end TestOscThroughHelpersOverUdp

on TestArtnetFramesOverUdp
   local tPort, i, tP, tLine, tOk
   put TestPortBase() + 13 into tPort
   accept datagram connections on port tPort with message "udpArrived"
   open datagram socket to ("127.0.0.1:" & tPort)
   repeat with i = 0 to 9
      write artnetBuildDmx(i, numToByte(i * 20) & numToByte(255 - i)) to socket ("127.0.0.1:" & tPort)
   end repeat
   TestPump 300
   TestAssertEqual "ten ArtDmx frames arrived", the number of lines of sDatagrams, 10
   put true into tOk
   repeat for each line tLine in sDatagrams
      put artnetParseDmx(TestUnhex(item 2 of tLine)) into tP
      if tP["opcode"] is not "ArtDmx" or byteToNum(byte 1 of tP["channels"]) is not tP["universe"] * 20 then
         put false into tOk
      end if
   end repeat
   TestAssert "every frame parses with its own universe and levels", tOk
   close socket tPort
   close socket ("127.0.0.1:" & tPort)
end TestArtnetFramesOverUdp

-- The shipped self-test's Tier 2 (async loopback) -- the part selftest_test
-- cannot check without sockets.
on TestShippedSelfTestTier2
   local tPath, tLog, tDeadline
   put $SC_HELPERS into tPath
   set the itemDelimiter to "/"
   put "selftest.livecodescript" into the last item of tPath
   set the itemDelimiter to ","
   TestLoadScriptAsLibrary tPath, "ShowControlSelfTest"
   put runShowControlSelfTest() into tLog
   TestPump 1500
   try
      put the text of field "STResults" of this card of stack "ShowControlLcsRunner" into tLog
   end try
   TestDiagnostic tLog
   TestAssert "Tier 2 OSC loopback reported PASS", tLog contains "[PASS] Tier 2 OSC loopback"
   TestAssert "Tier 2 Art-Net loopback reported PASS", tLog contains "[PASS] Tier 2 Art-Net loopback"
   try
      close socket "9999"
   end try
end TestShippedSelfTestTier2
