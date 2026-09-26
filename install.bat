@echo off
REM Windows installer
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || (echo Python 3.9+ is required. Get it from https://python.org and tick "Add to PATH". & pause & exit /b 1)
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\pip install -r requirements.txt -q
if not exist .env copy .env.example .env >nul
echo.
echo Installed. Now open .env in Notepad, paste your API key, then double-click run.bat
pause
