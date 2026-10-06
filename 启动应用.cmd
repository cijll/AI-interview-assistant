@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-app.ps1" %*
if errorlevel 1 (
    echo.
    echo =============================================
    echo  Startup FAILED. See the error messages above.
    echo  Please send them to the developer.
    echo =============================================
    pause
)
