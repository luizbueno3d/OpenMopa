-- MOPA Luiz launcher.
-- 1. If the UI server is already running on 127.0.0.1:8765, just open the browser.
-- 2. Otherwise, start it inside a Terminal window so the user can see logs and
--    close the window to quit. Then open the browser ~2 seconds later.

set scriptFile to POSIX path of (path to me)
set projectPath to do shell script "cd " & quoted form of scriptFile & "/../../.. && pwd"
set portNumber to 8765
set serverURL to "http://127.0.0.1:" & portNumber & "/"
set safetyURL to serverURL & "api/safety"

set quotedProject to quoted form of projectPath
set startCommand to "cd " & quotedProject & " && ./.venv/bin/python -m mopa_luiz ui --port " & portNumber

-- Probe whether the server is already up.
set alreadyUp to false
try
    set probeResult to do shell script "curl -s -o /dev/null -w '%{http_code}' --max-time 1 " & quoted form of safetyURL
    if probeResult is "200" then set alreadyUp to true
end try

if alreadyUp then
    do shell script "open " & quoted form of serverURL
    return
end if

-- Free any stale process bound to our port.
try
    do shell script "lsof -ti :" & portNumber & " | xargs -r kill -9"
end try

-- Start the server inside Terminal so the user can see it and close it cleanly.
tell application "Terminal"
    activate
    do script startCommand
end tell

-- Give the server a moment to bind, then open the browser.
delay 2
do shell script "open " & quoted form of serverURL
