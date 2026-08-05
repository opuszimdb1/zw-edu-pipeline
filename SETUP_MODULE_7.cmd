REM FILE: automation/SETUP_MODULE_7.cmd
@echo off
setlocal enabledelayedexpansion

powershell -Command "Write-Host 'MODULE 7: Automation core routing' -ForegroundColor Cyan"

REM ---------------------------------------------------------------------------
REM Locate repo root by walking up from this script to SETUP_CONFIG.txt
REM ---------------------------------------------------------------------------
set "ROOT=%~dp0"
set "CONFIG="
:FIND_CONFIG
if exist "%ROOT%SETUP_CONFIG.txt" (
  set "CONFIG=%ROOT%SETUP_CONFIG.txt"
  goto FOUND_CONFIG
)
for %%I in ("%ROOT%..") do set "PARENT=%%~fI\"
if "%PARENT%"=="%ROOT%" goto NO_CONFIG
set "ROOT=%PARENT%"
goto FIND_CONFIG

:NO_CONFIG
echo X SETUP_CONFIG.txt not found in any parent directory of %~dp0
EXIT /B 1

:FOUND_CONFIG
echo Using config: %CONFIG%

for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG%") do (
  set "LINE=%%A"
  if not "!LINE:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

REM ---------------------------------------------------------------------------
REM Validate the credentials this module needs
REM ---------------------------------------------------------------------------
call :CHECK SB_APIKEYS_URL "%SB_APIKEYS_URL%"          || EXIT /B 1
call :CHECK SB_APIKEYS_SERVICE_KEY "%SB_APIKEYS_SERVICE_KEY%" || EXIT /B 1
call :CHECK SB_USERS1_URL "%SB_USERS1_URL%"            || EXIT /B 1
call :CHECK SB_USERS1_SERVICE_KEY "%SB_USERS1_SERVICE_KEY%"   || EXIT /B 1
call :CHECK SB_PDF1_URL "%SB_PDF1_URL%"                || EXIT /B 1
call :CHECK SB_PDF1_SERVICE_KEY "%SB_PDF1_SERVICE_KEY%"       || EXIT /B 1
call :CHECK SB_OSCI_URL "%SB_OSCI_URL%"                || EXIT /B 1
call :CHECK SB_OSCI_SERVICE_KEY "%SB_OSCI_SERVICE_KEY%"       || EXIT /B 1

REM ---------------------------------------------------------------------------
REM Verify the delivered Python modules exist
REM ---------------------------------------------------------------------------
call :CHECKFILE "%ROOT%automation\scripts\supabase_client.py" || EXIT /B 1
call :CHECKFILE "%ROOT%automation\scripts\api_key_manager.py" || EXIT /B 1

powershell -Command "Write-Host 'NOTE: supabase_client.py and api_key_manager.py are imported by Module 11 main.py and are exercised inside GitHub Actions. No local Python run is required here.' -ForegroundColor Green"
EXIT /B 0

:CHECK
if "%~2"=="" (
  echo X Missing %~1 in SETUP_CONFIG.txt
  EXIT /B 1
)
echo [ok] %~1 present
EXIT /B 0

:CHECKFILE
if not exist "%~1" (
  echo X Missing file: %~1
  EXIT /B 1
)
powershell -Command "Write-Host 'OK found %~nx1' -ForegroundColor Green"
EXIT /B 0
