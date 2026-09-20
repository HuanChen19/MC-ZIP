@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   MC-ZIP - Ore UI 网页界面版
echo ============================================
echo.
echo 说明：本程序在本机启动一个小型服务（仅绑定 127.0.0.1），
echo       然后用 Edge 的应用窗口打开界面。
echo       关闭本窗口即退出程序；界面窗口单独关闭不影响程序。
echo.

set "PY="
if exist "%~dp0.build\vpy\Scripts\python.exe" set "PY=%~dp0.build\vpy\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+ 并加入 PATH。
    pause
    exit /b 1
  )
  set "PY=python"
)

"%PY%" "%~dp0mczip_web.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [提示] 程序退出码 %RC%。若界面没有出现，请把上面打印的地址复制到浏览器打开。
  pause
)
