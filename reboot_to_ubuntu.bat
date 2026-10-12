@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo   Switching Remote OS to Ubuntu
echo ========================================================
echo.

python switch_os.py --target ubuntu %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Script finished with error code %ERRORLEVEL%.
)

echo.
pause
