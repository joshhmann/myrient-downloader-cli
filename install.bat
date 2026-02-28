@echo off
setlocal

set APP_NAME=myrient-cli
set REPO_URL=https://github.com/joshhmann/myrient-downloader-cli.git
set INSTALL_DIR=%USERPROFILE%\.myrient-cli

echo ================================
echo   Myrient Downloader CLI Setup
echo ================================
echo.

:: Check for Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3 is required but not found.
    echo Install it from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PY_VERSION=%%i
echo [+] Found %PY_VERSION%

:: Check for pip
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip is not installed.
    echo Run: python -m ensurepip --upgrade
    pause
    exit /b 1
)

:: Clone or update repo
if exist "%INSTALL_DIR%" (
    echo [+] Updating existing installation...
    cd /d "%INSTALL_DIR%"
    git pull --ff-only
) else (
    echo [+] Cloning repository...
    git clone %REPO_URL% "%INSTALL_DIR%"
    cd /d "%INSTALL_DIR%"
)

:: Check for rclone
where rclone >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] rclone not found. Required for Turbo Mode.
    echo [+] Attempting to install rclone via winget...
    winget install rclone --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [ERROR] winget failed to install rclone.
        echo Please install it manually: https://rclone.org/downloads/
    ) else (
        echo [+] rclone installed successfully.
    )
) else (
    for /f "tokens=*" %%i in ('rclone version ^| findstr rclone') do echo [+] Found %%i
)

:: Install dependencies
echo [+] Installing dependencies...
python -m pip install -r requirements.txt --quiet

:: Create launcher batch file in install dir
set LAUNCHER=%INSTALL_DIR%\%APP_NAME%.bat
(
    echo @echo off
    echo python "%%~dp0myrient.py" %%*
) > "%LAUNCHER%"
echo [+] Created launcher at %LAUNCHER%

:: Add to PATH via registry if not already there
echo %PATH% | findstr /i /c:"%INSTALL_DIR%" >nul 2>&1
if %errorlevel% neq 0 (
    echo [+] Adding to user PATH...
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v Path 2^>nul') do set CURRENT_PATH=%%b
    if defined CURRENT_PATH (
        setx PATH "%CURRENT_PATH%;%INSTALL_DIR%" >nul 2>&1
    ) else (
        setx PATH "%INSTALL_DIR%" >nul 2>&1
    )
    echo [+] Added %INSTALL_DIR% to user PATH
)

echo.
echo ================================
echo   Installation complete!
echo ================================
echo.
echo Run '%APP_NAME%' to start the downloader.
echo.
echo NOTE: You may need to restart your terminal for
echo       the PATH change to take effect.
echo.
pause
