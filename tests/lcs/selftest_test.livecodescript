script "SelfTestTest"
-- ---------------------------------------------------------------------------
-- Runs the SHIPPED hardware-free self-test (examples/selftest.livecodescript)
-- headlessly, exactly as a user would after pasting it into a stack:
--     put runShowControlSelfTest()
-- and asserts its synchronous tiers (1 and 3) report zero failures. That turns
-- the in-OXT self-test from a manual ritual into a CI gate, and proves the
-- example itself still compiles and runs against the current extensions.
-- (Tier 2 -- the async UDP loopback -- is covered by udp_test, which only runs
-- where the engine can deliver socket events without a UI.)
-- ---------------------------------------------------------------------------
-- midi: mock

on TestShippedSelfTestPasses
   local tPath, tLog, tTally
   put $SC_HELPERS into tPath
   set the itemDelimiter to "/"
   put "selftest.livecodescript" into the last item of tPath
   set the itemDelimiter to ","
   TestLoadScriptAsLibrary tPath, "ShowControlSelfTest"
   put runShowControlSelfTest() into tLog
   TestDiagnostic tLog
   put line (lineOffset("passed,", tLog)) of tLog into tTally
   TestAssert "the self-test printed a tally", tTally is not empty
   TestAssert "Tiers 1 and 3: 0 failed  (" & word 1 to 4 of tTally & ")", tTally contains " 0 failed"
   TestAssert "Tiers 1 and 3 ran a meaningful number of checks", word 1 of tTally >= 50
   -- stop the Tier 2 listener the self-test opened, so the engine can quit cleanly
   try
      close socket "9999"
   end try
end TestShippedSelfTestPasses
