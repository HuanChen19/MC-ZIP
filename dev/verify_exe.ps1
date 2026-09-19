# dev\verify_exe.ps1 —— 验证构建产物 dist\MC-ZIP.exe 能否正常启动
#
# 用法（在项目根目录下执行）：
#   powershell -ExecutionPolicy Bypass -File dev\verify_exe.ps1

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "dist\MC-ZIP.exe"

if (-not (Test-Path $exe)) {
  Write-Host "[错误] 找不到 $exe，请先运行 build_exe.bat" -ForegroundColor Red
  exit 1
}

Write-Host "exe: $exe  ($([math]::Round((Get-Item $exe).Length/1MB,2)) MB)"

# 造一份干净的示例 Addon
$py = "C:\Users\HuanChen\AppData\Local\Programs\Python\Python310\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -c "import sys; sys.path.insert(0, r'$PSScriptRoot'); import selftest; selftest.build_fixture()" | Out-Null
$demo = Join-Path $root ".build\selftest\MyAddon"

$before = Get-Content (Join-Path $demo "MyAddon_BP\manifest.json") -Raw
Start-Process -FilePath $exe -ArgumentList @($demo) | Out-Null
Start-Sleep -Seconds 12

$all = Get-Process -Name "MC-ZIP" -ErrorAction SilentlyContinue
$titled = $all | Where-Object { $_.MainWindowTitle -ne "" }

if ($titled) {
  Write-Host "[通过] 窗口已渲染：" -ForegroundColor Green
  foreach ($p in $titled) { Write-Host "        $($p.MainWindowTitle)" }
} else {
  Write-Host "[失败] 未检测到窗口，exe 可能启动异常" -ForegroundColor Red
}

$all | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$after = Get-Content (Join-Path $demo "MyAddon_BP\manifest.json") -Raw
if ($before -eq $after) {
  Write-Host "[通过] 仅导入不修改 manifest.json" -ForegroundColor Green
} else {
  Write-Host "[失败] manifest.json 被意外改写" -ForegroundColor Red
}
