@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem One-click startup: conda env + Chrome + Patchright service + main service
rem ============================================================
rem Keep this window open; killing this task tree (e.g. VSCode
rem "Kill Task Tree") stops all child services together.
rem
rem Flow:
rem   1. conda activate workdaily
rem   2. check/start real Chrome (debug port 9222, profile E:\ChromeAutomationProfile)
rem   3. start Patchright search service (default http://127.0.0.1:8931)
rem   4. start main service: python main.py --webui-only
rem ============================================================

set "CONDA_ENV=workdaily"
set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "CHROME_PORT=9222"
set "CHROME_PORT_ALT=9228"
set "CHROME_PROFILE=E:\ChromeAutomationProfile"
set "CDP_URL="
set "SERVICE_PORT=8931"
if defined PATCHRIGHT_SERVER_PORT set "SERVICE_PORT=%PATCHRIGHT_SERVER_PORT%"
set "HEALTH_URL=http://127.0.0.1:%SERVICE_PORT%/healthz"
set "ROOT=%~dp0"

rem ---------- 1. activate conda env ----------
call conda activate %CONDA_ENV% >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to activate conda env: %CONDA_ENV%
    echo         Make sure conda is available in PATH, then retry.
    exit /b 1
)
echo [OK] conda env activated: %CONDA_ENV%

rem ---------- 2. check patchright ----------
python -c "import patchright" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] patchright not installed in env %CONDA_ENV%.
    echo         Run: python -m pip install patchright
    exit /b 1
)

rem ---------- 3. resolve Chrome CDP endpoint ----------
rem PATCHRIGHT_CDP_URL wins; else try configured port, then the alias port.
if defined PATCHRIGHT_CDP_URL (
    set "CDP_URL=%PATCHRIGHT_CDP_URL%"
) else (
    curl -s -o nul "http://127.0.0.1:%CHROME_PORT%/json/version" >nul 2>&1
    if errorlevel 1 (
        curl -s -o nul "http://127.0.0.1:%CHROME_PORT_ALT%/json/version" >nul 2>&1
        if errorlevel 1 (
            goto :start_chrome
        ) else (
            set "CDP_URL=http://127.0.0.1:%CHROME_PORT_ALT%"
        )
    ) else (
        set "CDP_URL=http://127.0.0.1:%CHROME_PORT%"
    )
)

if defined CDP_URL goto :chrome_ready

:start_chrome
if not exist "!CHROME_PATH!" (
    echo [ERROR] Chrome not found: !CHROME_PATH!
    exit /b 1
)
echo [INFO] Starting Chrome on debug port !CHROME_PORT! ...
start "" "!CHROME_PATH!" --remote-debugging-port=!CHROME_PORT! --user-data-dir=!CHROME_PROFILE! --no-first-run --no-default-browser-check
call :wait_url "http://127.0.0.1:!CHROME_PORT!/json/version" 30
if errorlevel 1 (
    echo [ERROR] Chrome start timeout, please start Chrome manually.
    exit /b 1
)
set "CDP_URL=http://127.0.0.1:!CHROME_PORT!"

:chrome_ready
echo [OK] Chrome CDP ready: !CDP_URL!
set "PATCHRIGHT_CDP_URL=!CDP_URL!"

rem ---------- 4. start Patchright service ----------
curl -s -o nul "!HEALTH_URL!" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Patchright service already running: !HEALTH_URL!
) else (
    echo [INFO] Starting Patchright search service ...
    rem python is on PATH after conda activate; no quoted exe needed in start
    start "patchright-server" /min python -m src.patchright_server.server
    call :wait_url "!HEALTH_URL!" 30
    if errorlevel 1 (
        echo [ERROR] Patchright service start timeout, check logs under !ROOT!logs\
        exit /b 1
    )
    echo [OK] Patchright service started: !HEALTH_URL!
)

rem ---------- 5. start main service ----------
echo [INFO] Starting main service (python main.py --webui-only) ...
start "main-service" /min python main.py --webui-only
echo [OK] Main service started.
echo.

echo All services started:
echo   Chrome CDP         : !CDP_URL!
echo   Patchright service : !HEALTH_URL!
echo   Main service       : http://127.0.0.1:8000
echo.
echo Close this window or kill this task tree to stop all services.
echo ----------------------------------------------------
pause >nul
exit /b 0

rem ---------- helper: wait until url responds (max seconds) ----------
:wait_url
set /a TRY=0
:wait_url_loop
set /a TRY+=1
if !TRY! GTR %2 exit /b 1
ping -n 2 127.0.0.1 >nul
curl -s -o nul "%~1" >nul 2>&1
if errorlevel 1 goto :wait_url_loop
exit /b 0
