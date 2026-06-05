@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-to-git.ps1"
set "exit_code=%ERRORLEVEL%"
echo.
if "%exit_code%"=="0" (
  echo Local AI Brain beta deploy complete.
) else (
  echo Local AI Brain beta deploy failed with exit code %exit_code%.
)
pause
exit /b %exit_code%
