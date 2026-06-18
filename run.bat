@echo off
REM =============================================================================
REM Tesla-PVPC - Launcher (Windows)
REM =============================================================================
REM Usage:
REM   run.bat                  Launch daemon
REM   run.bat --once           One cycle then exit
REM   run.bat --init           Setup wizard
REM   run.bat --dry-run        Read real data, don't send commands
REM   run.bat --debug          Simulated mode
REM   run.bat --show-config    Show configuration
REM   run.bat --help           Help
REM =============================================================================

cd /d "%~dp0"

echo ==============================================
echo   Tesla-PVPC
echo ==============================================
echo.

REM --- 1. Check/install uv ---
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo uv not found. Installing uv...
    powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"

    REM Verify uv installed correctly
    where uv >nul 2>&1
    if %errorlevel% neq 0 (
        echo.
        echo ERROR: uv installation failed or PATH not set.
        echo Install manually: https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
    echo uv installed successfully.
    echo.
)

REM --- 2. Create venv if missing ---
if not exist ".venv\" (
    echo Creating virtual environment with uv...
    uv venv
    echo Virtual environment created (.venv\).
    echo.
)

REM --- 3. Check if dependencies need reinstall ---
set NEED_SYNC=0
if not exist ".venv\.deps-installed" set NEED_SYNC=1

REM Check if requirements.txt or pyproject.toml changed
if %NEED_SYNC%==0 (
    for %%F in (requirements.txt pyproject.toml) do (
        if exist "%%F" (
            for %%M in (.venv\.deps-installed) do (
                if "%%~tF" gtr "%%~tM" set NEED_SYNC=1
            )
        )
    )
)

if %NEED_SYNC%==1 (
    echo Installing/updating dependencies (uv sync)...
    uv sync
    type nul > ".venv\.deps-installed"
    echo Dependencies ready.
    echo.
)

REM --- 4. Run ---
echo Launching Tesla-PVPC...
echo.
uv run python tesla_pvpc.py %*
