@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   MC-ZIP (Ore UI 网页界面版) - 生成单文件 exe
echo ============================================
echo.

set "VENV=%~dp0.build\vpy"
set "PY="
if exist "%VENV%\Scripts\python.exe" set "PY=%VENV%\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+ 并加入 PATH。
    pause
    exit /b 1
  )
  set "PY=python"
)
echo 使用解释器: %PY%
echo.

echo [1/2] 检查 PyInstaller ...
"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo       未安装，正在安装 PyInstaller ...
  "%PY%" -m pip install pyinstaller
  if errorlevel 1 (
    echo [错误] PyInstaller 安装失败，请检查网络。
    pause
    exit /b 1
  )
)

echo [2/2] 打包单文件 exe ...
set "ICONARG="
if exist "%~dp0app.ico" set "ICONARG=--icon "%~dp0app.ico""
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "MC-ZIP" %ICONARG% ^
  --add-data "%~dp0frontend;frontend" ^
  --add-data "%~dp0app.ico;." ^
  --collect-submodules webview ^
  --distpath "%~dp0dist-web" ^
  --workpath "%~dp0.build\work-web" ^
  --specpath "%~dp0.build" ^
  "%~dp0mczip_web.py"
if errorlevel 1 (
  echo [错误] 打包失败。
  pause
  exit /b 1
)

echo.
echo ============================================
echo   完成！产物: %~dp0dist-web\MC-ZIP.exe
echo ============================================
echo.
pause
