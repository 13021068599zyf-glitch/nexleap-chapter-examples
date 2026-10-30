@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0启动平台.ps1"
if errorlevel 1 (
  echo 启动 PowerShell 失败。请在命令提示符中运行本文件，查看报错。
  pause
)