# decode_image.ps1 —— 把任意图片解码为裸 BGRA 数据，供 make_logo.py 使用
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File dev\decode_image.ps1 <源图> <输出.raw>
#
# 输出格式：小端 [int32 width][int32 height][int32 stride] + BGRA 像素数据（自上而下）

param(
  [Parameter(Mandatory = $true)][string]$Source,
  [Parameter(Mandatory = $true)][string]$Output
)

Add-Type -AssemblyName System.Drawing

$src = [System.IO.Path]::GetFullPath($Source)
if (-not (Test-Path $src)) {
  Write-Host "[错误] 找不到源图：$src" -ForegroundColor Red
  exit 1
}

$bmp = New-Object System.Drawing.Bitmap($src)
$width = $bmp.Width
$height = $bmp.Height
$rect = New-Object System.Drawing.Rectangle(0, 0, $width, $height)
$data = $bmp.LockBits($rect,
  [System.Drawing.Imaging.ImageLockMode]::ReadOnly,
  [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)

$pixels = New-Object byte[] ($data.Stride * $height)
[System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $pixels, 0, $pixels.Length)
$bmp.UnlockBits($data)
$bmp.Dispose()

$stream = New-Object System.IO.MemoryStream
$header = [BitConverter]::GetBytes([int]$width) +
          [BitConverter]::GetBytes([int]$height) +
          [BitConverter]::GetBytes([int]$data.Stride)
$stream.Write($header, 0, $header.Length)
$stream.Write($pixels, 0, $pixels.Length)

$outPath = [System.IO.Path]::GetFullPath($Output)
$dir = Split-Path -Parent $outPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
[System.IO.File]::WriteAllBytes($outPath, $stream.ToArray())
$stream.Dispose()

Write-Host "已解码 $src  ($width x $height, stride=$($data.Stride))"
Write-Host "输出 $outPath  ($([math]::Round((Get-Item $outPath).Length/1MB,2)) MB)"
