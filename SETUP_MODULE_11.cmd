REM FILE: automation/SETUP_MODULE_11.cmd
@ECHO OFF
CHCP 65001 >NUL
SETLOCAL ENABLEDELAYEDEXPANSION ENABLEEXTENSIONS

powershell -Command "Write-Host 'MODULE 11: Automation entry and workflows' -ForegroundColor Cyan"
ECHO.

REM ---------------------------------------------------------------------------
REM Locate repository root by walking up from this script to SETUP_CONFIG.txt
REM ---------------------------------------------------------------------------
SET "ROOT=%~dp0"
IF "%ROOT:~-1%"=="\" SET "ROOT=%ROOT:~0,-1%"
SET "CONFIG="

:FIND_CONFIG
IF EXIST "%ROOT%\SETUP_CONFIG.txt" (
    SET "CONFIG=%ROOT%\SETUP_CONFIG.txt"
    GOTO FOUND_CONFIG
)
FOR %%I IN ("%ROOT%") DO SET "PARENT=%%~dpI"
IF "%PARENT:~-1%"=="\" SET "PARENT=%PARENT:~0,-1%"
IF "%PARENT%"=="%ROOT%" GOTO NO_CONFIG
SET "ROOT=%PARENT%"
GOTO FIND_CONFIG

:NO_CONFIG
ECHO ❌ SETUP_CONFIG.txt not found in any parent directory of "%~dp0"
EXIT /B 1

:FOUND_CONFIG
ECHO ✅ Repository root: %ROOT%
ECHO ✅ Config file: %CONFIG%
ECHO.

REM ---------------------------------------------------------------------------
REM Parse SETUP_CONFIG.txt
REM ---------------------------------------------------------------------------
for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG%") do (
  set "LINE=%%A"
  if not "!LINE:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

ECHO --- Validating configuration keys ---
SET "MISSING=0"
CALL :CHECK_VAR GITHUB_AUTO_USERNAME
CALL :CHECK_VAR GITHUB_AUTO_REPO_NAME
CALL :CHECK_VAR GITHUB_AUTO_TOKEN
CALL :CHECK_VAR SB_APIKEYS_URL
CALL :CHECK_VAR SB_APIKEYS_SERVICE_KEY
CALL :CHECK_VAR RESEND_API_KEY
IF NOT "!MISSING!"=="0" (
    ECHO ❌ One or more required configuration keys are missing from SETUP_CONFIG.txt
    EXIT /B 1
)
ECHO.

REM ---------------------------------------------------------------------------
REM Verify Module 11 files exist
REM ---------------------------------------------------------------------------
ECHO --- Verifying Module 11 files ---
CALL :CHECK_FILE "%ROOT%\automation\scripts\main.py"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\scripts\cleanup.py"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\scripts\delete_pdf.py"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\scripts\requirements.txt"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\.github\workflows\generate_pdf.yml"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\.github\workflows\cleanup_pdfs.yml"
IF ERRORLEVEL 1 EXIT /B 1
CALL :CHECK_FILE "%ROOT%\automation\.github\workflows\delete_on_download.yml"
IF ERRORLEVEL 1 EXIT /B 1
ECHO.

REM ---------------------------------------------------------------------------
REM Install pinned dependencies
REM ---------------------------------------------------------------------------
ECHO --- Installing pinned dependencies ---
python -m pip install --quiet -r "%ROOT%\automation\scripts\requirements.txt"
IF ERRORLEVEL 1 (
    ECHO ❌ pip install failed for automation\scripts\requirements.txt
    EXIT /B 1
)
ECHO ✅ Dependencies installed
ECHO.

REM ---------------------------------------------------------------------------
REM Byte-compile the three entrypoints
REM ---------------------------------------------------------------------------
ECHO --- Byte-compile check ---
CALL :COMPILE "%ROOT%\automation\scripts\main.py"
IF ERRORLEVEL 1 EXIT /B 1
CALL :COMPILE "%ROOT%\automation\scripts\cleanup.py"
IF ERRORLEVEL 1 EXIT /B 1
CALL :COMPILE "%ROOT%\automation\scripts\delete_pdf.py"
IF ERRORLEVEL 1 EXIT /B 1
ECHO.

powershell -Command "Write-Host 'MODULE 11 setup complete.' -ForegroundColor Green"
EXIT /B 0

REM ---------------------------------------------------------------------------
:CHECK_VAR
IF "!%~1!"=="" (
    ECHO ❌ Missing configuration key: %~1
    SET "MISSING=1"
) ELSE (
    ECHO ✅ %~1 is set
)
EXIT /B 0

REM ---------------------------------------------------------------------------
:CHECK_FILE
IF EXIST "%~1" (
    ECHO ✅ %~nx1
    EXIT /B 0
)
ECHO ❌ Missing file: %~1
EXIT /B 1

REM ---------------------------------------------------------------------------
:COMPILE
python -m py_compile "%~1"
IF ERRORLEVEL 1 (
    ECHO ❌ Compile failed: %~nx1
    EXIT /B 1
)
ECHO ✅ Compiled %~nx1
EXIT /B 0
