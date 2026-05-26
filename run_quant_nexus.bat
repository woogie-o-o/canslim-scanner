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

rem -- Critical-deps sanity check (PYEXE may differ from marker interpreter).
rem Even if marker exists, flask_compress may be missing in active PYEXE.
rem 14MB uncompressed response causes browser fetch failure. Force reinstall.
%PYEXE% -c "import flask_compress, brotli" >nul 2>&1
if errorlevel 1 (
    echo [run_quant_nexus] flask_compress/brotli missing in active Python — force-installing ...
    %PYEXE% -m pip install --quiet --disable-pip-version-check flask-compress brotli zstandard
    %PYEXE% -c "import flask_compress, brotli" >nul 2>&1
    if errorlevel 1 (
        echo [run_quant_nexus] ERROR: flask_compress install failed. Scan responses will time out.
    ) else (
        echo [run_quant_nexus] flask_compress installed.
    )
)

rem -- Pre-flight: if port 5000 is already occupied, skip launch.
rem Open browser for existing instance and exit cleanly.
rem (Prevents WinError 10048 traceback + wrong page display)
set "PORT_CHECK=5000"
if defined PORT set "PORT_CHECK=%PORT%"
rem Do not use '!=' -- EnableDelayedExpansion interprets '!' as variable expansion. Use '=='.
%PYEXE% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(1 if s.connect_ex(('127.0.0.1',%PORT_CHECK%))==0 else 0)" >nul 2>&1
if errorlevel 1 (
    rem Verify gzip status of existing instance -- uncompressed mode causes 'connection failed'.
    %PYEXE% -c "import urllib.request,json,sys; r=json.loads(urllib.request.urlopen('http://127.0.0.1:%PORT_CHECK%/healthz',timeout=3).read()); sys.exit(0 if r.get('gzip') else 2)" >nul 2>&1
    if errorlevel 2 (
        echo [run_quant_nexus] 기존 서버가 압축 비활성 상태입니다 - 비정상 인스턴스를 종료하고 새로 띄워야 합니다.
        echo [run_quant_nexus] 작업 관리자에서 python.exe 를 종료한 뒤 본 런처를 재실행하세요.
        echo.
        pause
        popd
        endlocal
        exit /b 2
    )
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
