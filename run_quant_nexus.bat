@echo off
setlocal EnableExtensions EnableDelayedExpansion
pushd "%~dp0"

set "PROJ_DIR=%CD%\"

set "PYEXE="
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=py -3.13"
    echo [run_quant_nexus] Using py -3.13
    goto :pyexe_ok
)
if defined PYTHON313 if exist "%PYTHON313%" (
    set "PYEXE=%PYTHON313%"
    echo [run_quant_nexus] Using PYTHON313=%PYTHON313%
    goto :pyexe_ok
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=python"
    echo [run_quant_nexus] py -3.13 not found, falling back to system python
    goto :pyexe_ok
)
echo [run_quant_nexus] ERROR: Python not found. Install Python 3.13 from https://python.org
echo.
pause
popd
endlocal
exit /b 1

:pyexe_ok

set "REQ_FILE=%PROJ_DIR%requirements.txt"
set "MARKER=%PROJ_DIR%.deps-installed.marker"
set "NEED_INSTALL=0"

if exist "%REQ_FILE%" (
    if not exist "%MARKER%" (
        set "NEED_INSTALL=1"
    ) else (
        for %%F in ("%REQ_FILE%") do set "REQ_TIME=%%~tF"
        for %%F in ("%MARKER%") do set "MARK_TIME=%%~tF"
        if "!REQ_TIME!" GTR "!MARK_TIME!" set "NEED_INSTALL=1"
    )
)

if "%NEED_INSTALL%"=="1" (
    echo [run_quant_nexus] Installing dependencies from requirements.txt ...
    %PYEXE% -m pip install -r "%REQ_FILE%" --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo [run_quant_nexus] WARNING: pip install returned errors. Continuing...
    ) else (
        echo done > "%MARKER%"
        echo [run_quant_nexus] Dependencies OK.
    )
)

echo [run_quant_nexus] Launching Flask web app (http://localhost:5000) ...
start /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"
%PYEXE% "%PROJ_DIR%web_app\app.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [run_quant_nexus] Exited with code %RC%.
    echo Press any key to close...
    pause >nul
)

popd
endlocal
exit /b %RC%
