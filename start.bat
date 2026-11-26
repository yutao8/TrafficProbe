@echo off
chcp 65001 >nul
cd /d "%~dp0"

tasklist /FI "IMAGENAME eq TrafficProbe.exe" 2>nul | find /I "TrafficProbe.exe" >nul
if not errorlevel 1 (
    echo 已经在运行了，不重复启动。
    echo 要停止请运行 stop.bat
    timeout /t 3 >nul
    exit /b
)

echo 正在转入托盘后台录制...
echo 启动后可以直接关闭这个黑窗口，程序会在托盘继续跑。
start "" "%~dp0TrafficProbe.exe" record --tray
timeout /t 3 >nul
echo.
echo 完成。在屏幕右下角托盘区找「流量探针」图标：
echo   右键 = 菜单（查看状态 / 打开数据文件夹 / 退出）
echo   双击 = 查看状态
echo.
echo 数据目录：%APPDATA%\TrafficProbe
timeout /t 4 >nul
