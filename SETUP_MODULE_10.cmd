REM FILE: automation/SETUP_MODULE_10.cmd
@ECHO OFF
SETLOCAL ENABLEDELAYEDEXPANSION ENABLEEXTENSIONS

powershell -Command "Write-Host 'MODULE 10: Automation storage and email' -ForegroundColor Cyan"

REM ---------------------------------------------------------------- repo root
SET "ROOT=%~dp0"
IF "%ROOT:~-1%"=="\" SET "ROOT=%ROOT:~0,-1%"
SET "CONFIG="
:FIND_ROOT
IF EXIST "%ROOT%\SETUP_CONFIG.txt" (
    SET "CONFIG=%ROOT%\SETUP_CONFIG.txt"
    GOTO FOUND_ROOT
)
FOR %%I IN ("%ROOT%") DO SET "PARENT=%%~dpI"
IF "%PARENT:~-1%"=="\" SET "PARENT=%PARENT:~0,-1%"
IF "%PARENT%"=="%ROOT%" GOTO NO_CONFIG
SET "ROOT=%PARENT%"
GOTO FIND_ROOT

:NO_CONFIG
ECHO ❌ SETUP_CONFIG.txt not found walking up from "%~dp0"
EXIT /B 1

:FOUND_ROOT
ECHO ✅ Repo root: %ROOT%
ECHO ✅ Config file: %CONFIG%

REM ------------------------------------------------------------- parse config
for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG%") do (
  set "LINE=%%A"
  if not "!LINE:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

REM ----------------------------------------------------------------- validate
IF "%SB_PDF1_URL%"=="" ECHO ❌ Missing SB_PDF1_URL in SETUP_CONFIG.txt & EXIT /B 1
IF "%SB_PDF1_SERVICE_KEY%"=="" ECHO ❌ Missing SB_PDF1_SERVICE_KEY in SETUP_CONFIG.txt & EXIT /B 1
IF "%SB_APIKEYS_URL%"=="" ECHO ❌ Missing SB_APIKEYS_URL in SETUP_CONFIG.txt & EXIT /B 1
IF "%SB_APIKEYS_SERVICE_KEY%"=="" ECHO ❌ Missing SB_APIKEYS_SERVICE_KEY in SETUP_CONFIG.txt & EXIT /B 1
IF "%RESEND_API_KEY%"=="" ECHO ❌ Missing RESEND_API_KEY in SETUP_CONFIG.txt & EXIT /B 1
IF "%RESEND_FROM_EMAIL%"=="" ECHO ❌ Missing RESEND_FROM_EMAIL in SETUP_CONFIG.txt & EXIT /B 1
ECHO ✅ All required SETUP_CONFIG.txt keys present

REM -------------------------------------------------------------- files exist
IF NOT EXIST "%ROOT%\automation\scripts\storage_manager.py" (
    ECHO ❌ Missing automation\scripts\storage_manager.py
    EXIT /B 1
)
ECHO ✅ automation\scripts\storage_manager.py found

IF NOT EXIST "%ROOT%\automation\scripts\email_sender.py" (
    ECHO ❌ Missing automation\scripts\email_sender.py
    EXIT /B 1
)
ECHO ✅ automation\scripts\email_sender.py found

REM ------------------------------------------------------------ dependencies
python -m pip install --quiet requests supabase
IF ERRORLEVEL 1 (
    ECHO ❌ Failed to install requests and supabase
    EXIT /B 1
)
ECHO ✅ Dependencies installed (requests, supabase)

REM ------------------------------------------------- render-only email check
SET "OPUSZIM_EMAIL_TEST_TO="
python "%ROOT%\automation\scripts\email_sender.py"
IF ERRORLEVEL 1 (
    ECHO ❌ email_sender.py render check failed
    EXIT /B 1
)
ECHO ✅ email_sender.py rendered the branded template (nothing sent)

powershell -Command "Write-Host 'MODULE 10 complete' -ForegroundColor Green"
EXIT /B 0
