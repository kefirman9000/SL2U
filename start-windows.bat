@echo off
REM Double-click to start Release Kit. First run creates a private Python environment.
cd /d "%~dp0"
where ffmpeg >nul 2>nul || echo ffmpeg is not installed. Run:  winget install Gyan.FFmpeg   then reopen this window.
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\python -m releasekit
pause
