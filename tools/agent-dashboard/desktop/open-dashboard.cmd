@echo off
rem Opens the agent dashboard in its own Chrome or Edge window (no tabs or
rem address bar), signed in with your normal browser profile. Falls back to
rem the default browser. See tools\agent-dashboard\README.md.
setlocal
set "URL=https://claude.ai/artifact/BWYCUWxJxBvG6MJvnsaqvn"
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --app=%URL% & exit /b 0)
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" --app=%URL% & exit /b 0)
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (start "" "%LocalAppData%\Google\Chrome\Application\chrome.exe" --app=%URL% & exit /b 0)
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --app=%URL% & exit /b 0)
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --app=%URL% & exit /b 0)
rem No Chrome or Edge found: open it as a normal tab in the default browser.
start "" "%URL%"
exit /b 0
