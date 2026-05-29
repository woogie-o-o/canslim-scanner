@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem -- Switch console codepage to UTF-8 --
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

rem -- Critical-deps check: flask_compress + brotli + finnhub in ONE Python call --
%PYEXE% -c "import flask_compress, brotli, finnhub" >nul 2>&1
if errorlevel 1 (
    echo [run_quant_nexus] Critical deps missing — installing ...
    %PYEXE% -m pip install --quiet --disable-pip-version-check flask-compress brotli zstandard "finnhub-python>=2.4"
    if errorlevel 1 (
        echo [run_quant_nexus] WARNING: Critical dep install failed. Some features may not work.
    ) else (
        echo [run_quant_nexus] Critical deps installed.
    )
)

rem -- Pre-flight: port check (bind test + healthz) --
if not defined PORT set "PORT=5001"
set "PORT_CHECK=%PORT%"

rem 바인딩 테스트: 포트가 비어있으면 성공(exit 0) → 즉시 서버 실행
%PYEXE% -c "import socket,sys;s=socket.socket();s.bind(('127.0.0.1',%PORT_CHECK%));s.close()" >nul 2>&1
if not errorlevel 1 goto :do_launch

rem 포트 사용 중 — healthz 확인
%PYEXE% -c "import urllib.request,json,sys;r=json.loads(urllib.request.urlopen('http://127.0.0.1:%PORT_CHECK%/healthz',timeout=3).read());sys.exit(1 if r.get('gzip') else 2)" >nul 2>&1
set "PORT_RC=%ERRORLEVEL%"

if "%PORT_RC%"=="2" (
    echo [run_quant_nexus] 기존 서버가 압축 비활성 상태입니다 - 비정상 인스턴스를 종료하고 새로 띄워야 합니다.
    echo [run_quant_nexus] 작업 관리자에서 python.exe 를 종료한 뒤 본 런처를 재실행하세요.
    echo.
    pause
    popd
    endlocal
    exit /b 2
)
if "%PORT_RC%"=="1" (
    echo [run_quant_nexus] 포트 %PORT_CHECK%이 이미 사용 중입니다 - 기존 인스턴스에 연결합니다.
    echo [run_quant_nexus] 다른 포트로 띄우려면 ^"set PORT=XXXX^" 후 재실행하세요.
    start http://localhost:%PORT_CHECK%
    echo.
    echo Press any key to close this window...
    pause >nul
    popd
    endlocal
    exit /b 0
)
rem healthz 실패 → 포트가 사용 중이지만 우리 서버가 아님, 그냥 실행 시도

:do_launch
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
