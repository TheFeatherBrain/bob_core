@echo off
rem Session supervisor tick for {bob.name}, run every 20 minutes by Windows Task Scheduler as task
rem {runtime.launchd_label}. The Windows counterpart of the launchd plist: Task Scheduler cannot set
rem environment variables or redirect output itself, so this file does both.
set "BOB_MANIFEST={bob_abs}\bob.toml"
set "CLAUDE_CONFIG_DIR={config_dir}"
cd /d "{bob_abs}"
for %%F in ("{runtime.agent_log}") do if not exist "%%~dpF" mkdir "%%~dpF"
"{python}" "{core_abs}\harness\session_supervisor.py" >> "{runtime.agent_log}" 2>&1
