REM FILE: automation/SETUP_MODULE_9.cmd
@ECHO OFF
CHCP 65001 >NUL
SETLOCAL ENABLEDELAYEDEXPANSION ENABLEEXTENSIONS

powershell -Command "Write-Host 'MODULE 9: Automation PDF layer' -ForegroundColor Cyan"

REM ---------------------------------------------------------------------------
REM Locate the repository root by walking up from this script to SETUP_CONFIG.txt
REM ---------------------------------------------------------------------------
SET "ROOT=%~dp0"
IF "%ROOT:~-1%"=="\" SET "ROOT=%ROOT:~0,-1%"

:FIND_ROOT
IF EXIST "%ROOT%\SETUP_CONFIG.txt" GOTO FOUND_ROOT
FOR %%I IN ("%ROOT%") DO SET "PARENT=%%~dpI"
IF "%PARENT:~-1%"=="\" SET "PARENT=%PARENT:~0,-1%"
IF "%PARENT%"=="%ROOT%" GOTO NO_ROOT
IF "%PARENT%"=="" GOTO NO_ROOT
SET "ROOT=%PARENT%"
GOTO FIND_ROOT

:NO_ROOT
powershell -Command "Write-Host '❌ Could not locate SETUP_CONFIG.txt above %~dp0' -ForegroundColor Red"
EXIT /B 1

:FOUND_ROOT
SET "CONFIG=%ROOT%\SETUP_CONFIG.txt"
powershell -Command "Write-Host '✅ Repo root: %ROOT%' -ForegroundColor Green"

REM ---------------------------------------------------------------------------
REM Parse SETUP_CONFIG.txt (this module needs no specific keys, only the root)
REM ---------------------------------------------------------------------------
for /f "usebackq tokens=1,* delims==" %%A in ("%CONFIG%") do (
  set "LINE=%%A"
  if not "!LINE:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
)
powershell -Command "Write-Host '✅ Parsed SETUP_CONFIG.txt' -ForegroundColor Green"

REM ---------------------------------------------------------------------------
REM Verify the module files exist
REM ---------------------------------------------------------------------------
SET "GEN=%ROOT%\automation\scripts\pdf_generator.py"
SET "PREV=%ROOT%\automation\scripts\pdf_preview.py"

IF NOT EXIST "%GEN%" (
  powershell -Command "Write-Host '❌ Missing automation\scripts\pdf_generator.py' -ForegroundColor Red"
  EXIT /B 1
)
powershell -Command "Write-Host '✅ Found automation\scripts\pdf_generator.py' -ForegroundColor Green"

IF NOT EXIST "%PREV%" (
  powershell -Command "Write-Host '❌ Missing automation\scripts\pdf_preview.py' -ForegroundColor Red"
  EXIT /B 1
)
powershell -Command "Write-Host '✅ Found automation\scripts\pdf_preview.py' -ForegroundColor Green"

REM ---------------------------------------------------------------------------
REM Install the rendering dependencies
REM ---------------------------------------------------------------------------
python -m pip install --quiet reportlab pymupdf pillow
IF ERRORLEVEL 1 (
  powershell -Command "Write-Host '❌ Failed to install reportlab / pymupdf / pillow' -ForegroundColor Red"
  EXIT /B 1
)
powershell -Command "Write-Host '✅ Installed reportlab, pymupdf, pillow' -ForegroundColor Green"

REM Self-tests write to /tmp; make sure it resolves on Windows too
IF NOT EXIST "%SystemDrive%\tmp" MKDIR "%SystemDrive%\tmp"

REM ---------------------------------------------------------------------------
REM Run the two self-tests
REM ---------------------------------------------------------------------------
python "%ROOT%\automation\scripts\pdf_generator.py"
IF ERRORLEVEL 1 (
  powershell -Command "Write-Host '❌ pdf_generator.py self-test failed' -ForegroundColor Red"
  EXIT /B 1
)
powershell -Command "Write-Host '✅ pdf_generator.py self-test passed' -ForegroundColor Green"

python "%ROOT%\automation\scripts\pdf_preview.py"
IF ERRORLEVEL 1 (
  powershell -Command "Write-Host '❌ pdf_preview.py self-test failed' -ForegroundColor Red"
  EXIT /B 1
)
powershell -Command "Write-Host '✅ pdf_preview.py self-test passed' -ForegroundColor Green"

powershell -Command "Write-Host 'MODULE 9 complete. See /tmp/opuszim_sample.pdf and /tmp/opuszim_sample.jpg' -ForegroundColor Cyan"
EXIT /B 0
