@echo off
chcp 65001 >nul
cd /d "%~dp0"

tasklist /FI "IMAGENAME eq TrafficProbe.exe" 2>nul | find /I "TrafficProbe.exe" >nul
if errorlevel 1 (
    echo 当前没有在录制。
    timeout /t 2 >nul
    exit /b
)

taskkill /F /IM TrafficProbe.exe >nul 2>&1
echo 已停止录制。
timeout /t 2 >nul
