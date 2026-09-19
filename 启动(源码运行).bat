@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "%~dp0.build\vpy\Scripts\python.exe" set "PY=%~dp0.build\vpy\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul
  if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.8+ 并加入系统 PATH。
    pause
    exit /b 1
  )
  set "PY=python"
)

start "" "%PY%" "%~dp0addon_packer.py"
