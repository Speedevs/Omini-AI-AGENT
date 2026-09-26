@echo off
cd /d "%~dp0"
.venv\Scripts\python -m omni %*
if "%~1"=="" pause
