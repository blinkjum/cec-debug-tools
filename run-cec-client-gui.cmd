@echo off
setlocal
set "APP=%~dp0cec-client-gui.py"
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON if exist "%USERPROFILE%\.codex-runtimes\python\python.exe" set "PYTHON=%USERPROFILE%\.codex-runtimes\python\python.exe"
if not defined PYTHON (
  echo Python 3 was not found. Please install Python 3.10+ with Tkinter.
  pause
  exit /b 1
)
%PYTHON% "%APP%"
endlocal
