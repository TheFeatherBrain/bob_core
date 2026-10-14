@echo off
rem Session supervisor tick for Trader, run every 20 minutes by Windows Task Scheduler as task
rem com.george.bob-trader. The Windows counterpart of the launchd plist: Task Scheduler cannot set
rem environment variables or redirect output itself, so this file does both.
set "BOB_MANIFEST=C:\Users\georg\src\george\bob_core\bobs\trader\bob.toml"
set "CLAUDE_CONFIG_DIR=C:\Users\georg\.claude-trader"
cd /d "C:\Users\georg\src\george\bob_core\bobs\trader"
for %%F in ("C:/Users/georg/AppData/Local/Temp/bob-trader-agent.log") do if not exist "%%~dpF" mkdir "%%~dpF"
"C:\Users\georg\AppData\Local\Python\pythoncore-3.14-64\python.exe" "C:\Users\georg\src\george\bob_core\harness\session_supervisor.py" >> "C:/Users/georg/AppData/Local/Temp/bob-trader-agent.log" 2>&1
