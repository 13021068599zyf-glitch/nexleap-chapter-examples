@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist "data" mkdir "data"
set "STARTUP_LOG=%~dp0data\启动日志.txt"
> "%STARTUP_LOG%" echo 图片整理平台启动记录
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/meta' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 goto already_running
echo 正在启动图片整理平台。网页会在浏览器中打开，请保持此窗口运行。
echo.
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" goto bundled
py -3 --version >nul 2>&1
if %errorlevel%==0 goto launcher
python --version >nul 2>&1
if %errorlevel%==0 goto system
echo 未找到可用的 Python。请安装 Python 3.10 或更新版本。
goto finish

:bundled
>> "%STARTUP_LOG%" echo 使用工作环境中的 Python
"%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -u server.py >> "%STARTUP_LOG%" 2>&1
goto finish

:launcher
>> "%STARTUP_LOG%" echo 使用 Python 启动器
py -3 -u server.py >> "%STARTUP_LOG%" 2>&1
goto finish

:system
>> "%STARTUP_LOG%" echo 使用系统 Python
python -u server.py >> "%STARTUP_LOG%" 2>&1

:finish
echo.
echo 图片整理平台已停止。启动记录位于 data\启动日志.txt。
type "%STARTUP_LOG%"
echo.
pause
goto :eof

:already_running
echo 图片整理平台已经在运行，正在打开工作台。
start "" "http://127.0.0.1:8766"
exit /b
