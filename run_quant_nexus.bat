@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ── 콘솔 코드페이지를 UTF-8로 전환 (한글 깨짐 방지) ──
chcp 65001 >nul
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

rem ── Pre-flight: 포트 5000을 다른 인스턴스가 점유 중이면 새로 안 띄움 ──
rem 이미 살아있는 인스턴스가 있으면 브라우저만 열고 깔끔히 종료한다.
rem (WinError 10048 트레이스백 + 잘못된 페이지 표시 방지)
set "PORT_CHECK=5000"
if defined PORT set "PORT_CHECK=%PORT%"
rem '!=' 사용 금지 — EnableDelayedExpansion이 '!'를 변수 확장으로 해석함. '==' 로 작성.
%PYEXE% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(1 if s.connect_ex(('127.0.0.1',%PORT_CHECK%))==0 else 0)" >nul 2>&1
if errorlevel 1 (
    echo [run_quant_nexus] 포트 %PORT_CHECK%이 이미 사용 중입니다 - 기존 인스턴스에 연결합니다.
    echo [run_quant_nexus] 다른 포트로 띄우려면 ^"set PORT=5001^" 후 재실행하세요.
    start http://localhost:%PORT_CHECK%
    echo.
    echo Press any key to close this window...
    pause >nul
    popd
    endlocal
    exit /b 0
)

echo [run_quant_nexus] Launching Flask web app (http://localhost:%PORT_CHECK%) ...
start /b cmd /c "timeout /t 2 >nul && start http://localhost:%PORT_CHECK%"
%PYEXE% "%PROJ_DIR%web_app\app.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [run_quant_nexus] Exited with code %RC%.
) else (
    echo [run_quant_nexus] Server stopped normally.
)
echo Press any key to close...
pause >nul

popd
endlocal
exit /b %RC%
