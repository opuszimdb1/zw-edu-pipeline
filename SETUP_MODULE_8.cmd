REM FILE: automation/SETUP_MODULE_8.cmd
@ECHO OFF
CHCP 65001 >NUL
SETLOCAL ENABLEDELAYEDEXPANSION

powershell -Command "Write-Host 'MODULE 8: Automation AI layer' -ForegroundColor Cyan"

REM ---------------------------------------------------------------- locate root
SET "ROOT=%~dp0"
IF "%ROOT:~-1%"=="\" SET "ROOT=%ROOT:~0,-1%"

:FIND_ROOT
IF EXIST "%ROOT%\SETUP_CONFIG.txt" GOTO FOUND_ROOT
FOR %%I IN ("%ROOT%\..") DO SET "PARENT=%%~fI"
IF "%PARENT%"=="%ROOT%" (
  ECHO ❌ SETUP_CONFIG.txt not found walking up from %~dp0
  EXIT /B 1
)
SET "ROOT=%PARENT%"
GOTO FIND_ROOT

:FOUND_ROOT
SET "CONFIG=%ROOT%\SETUP_CONFIG.txt"
ECHO ✅ Repo root: %ROOT%

REM --------------------------------------------------------------- parse config
for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG%") do (
  set "LINE=%%A"
  if not "!LINE:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)

IF "%GEMINI_API_KEY_1%"=="" (
  ECHO ❌ Missing GEMINI_API_KEY_1 in SETUP_CONFIG.txt
  EXIT /B 1
)
ECHO ✅ GEMINI_API_KEY_1 present

IF "%KIE_API_KEY_1%"=="" (
  ECHO ❌ Missing KIE_API_KEY_1 in SETUP_CONFIG.txt
  EXIT /B 1
)
ECHO ✅ KIE_API_KEY_1 present

REM ----------------------------------------------------------- verify the files
IF NOT EXIST "%ROOT%\automation\scripts\ai_client.py" (
  ECHO ❌ Missing automation\scripts\ai_client.py
  EXIT /B 1
)
ECHO ✅ automation\scripts\ai_client.py

IF NOT EXIST "%ROOT%\automation\scripts\image_generator.py" (
  ECHO ❌ Missing automation\scripts\image_generator.py
  EXIT /B 1
)
ECHO ✅ automation\scripts\image_generator.py

IF NOT EXIST "%ROOT%\automation\scripts\response_parser.py" (
  ECHO ❌ Missing automation\scripts\response_parser.py
  EXIT /B 1
)
ECHO ✅ automation\scripts\response_parser.py

REM -------------------------------------------------------- parser self-test
ECHO.
ECHO Running response_parser self-test...
python "%ROOT%\automation\scripts\response_parser.py"
IF ERRORLEVEL 1 (
  ECHO ❌ response_parser.py self-test failed
  EXIT /B 1
)
ECHO ✅ response_parser.py self-test passed

ECHO.
powershell -Command "Write-Host 'MODULE 8 verification complete.' -ForegroundColor Green"
EXIT /B 0
