# Verify the packaged Ore UI server using an isolated fixture and config.
param([string]$ExePath)
$ErrorActionPreference = 'Stop'
$workspaceRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if (-not $ExePath) { $ExePath = Join-Path $workspaceRoot 'dist/MC-ZIP.exe' }
$exeFile = (Resolve-Path -LiteralPath $ExePath).Path
$verifyTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$verifyDirectory = Join-Path $verifyTempRoot ('mczip-exe-verify-' + [guid]::NewGuid().ToString('N'))
$oldConfigPath = $env:MC_ZIP_CONFIG
$launchedProcess = $null
try {
    New-Item -ItemType Directory -Path $verifyDirectory | Out-Null
    $addon = Join-Path $verifyDirectory 'Addon'
    foreach ($pack in @(@{Name='BP';Type='data'}, @{Name='RP';Type='resources'}, @{Name='Skin';Type='skin_pack'})) {
        $packDirectory = Join-Path $addon $pack.Name
        New-Item -ItemType Directory -Path $packDirectory -Force | Out-Null
        $manifest = @{
            format_version=2
            header=@{name=$pack.Name; uuid=[guid]::NewGuid().ToString(); version=@(1,0,0)}
            modules=@(@{type=$pack.Type; uuid=[guid]::NewGuid().ToString(); version=@(1,0,0)})
        }
        $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $packDirectory 'manifest.json') -Encoding utf8
    }
    'excluded' | Set-Content -LiteralPath (Join-Path $addon 'readme.txt')
    $env:MC_ZIP_CONFIG = Join-Path $verifyDirectory 'config.json'
    $launchedProcess = Start-Process -FilePath $exeFile -ArgumentList @('--no-window') -WindowStyle Hidden -PassThru
    $listener = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $ownProcesses = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -eq $exeFile -and ($_.ProcessId -eq $launchedProcess.Id -or $_.ParentProcessId -eq $launchedProcess.Id)
        })
        $ownIds = @($ownProcesses.ProcessId)
        $listener = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {
            $_.OwningProcess -in $ownIds -and $_.LocalAddress -eq '127.0.0.1'
        } | Select-Object -First 1
        if ($listener) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $listener) { throw 'Packaged server did not start.' }
    $baseUrl = 'http://127.0.0.1:' + $listener.LocalPort
    $page = Invoke-WebRequest ($baseUrl + '/index.html') -UseBasicParsing
    if ($page.Content -notmatch 'sidebar-nav') { throw 'Ore UI frontend missing.' }
    foreach ($asset in @('/css/vendor/ore-colors.css', '/assets/fonts/NotoSans-Regular.woff2')) {
        if ((Invoke-WebRequest ($baseUrl + $asset) -UseBasicParsing).StatusCode -ne 200) { throw "Missing asset: $asset" }
    }
    $imported = Invoke-RestMethod ($baseUrl + '/api/import_folder') -Method Post -ContentType 'application/json' -Body (@{folder=$addon}|ConvertTo-Json -Compress)
    if (-not $imported.ok -or @($imported.packs | Where-Object packed).Count -ne 2) { throw 'Incorrect BP/RP selection.' }
    $before = Get-Content -LiteralPath (Join-Path $addon 'BP/manifest.json') -Raw
    $packaged = Invoke-RestMethod ($baseUrl + '/api/do_package') -Method Post -ContentType 'application/json' -Body (@{pack_mode='mcaddon';pack_with_bump=$false;only_bp_rp=$false}|ConvertTo-Json -Compress)
    if (-not $packaged.ok) { throw 'Packaging failed.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead((Join-Path $packaged.outputDir $packaged.archives[0]))
    try {
        $roots = @($archive.Entries | ForEach-Object { $_.FullName.Split('/')[0] } | Sort-Object -Unique)
        if (($roots -join ',') -ne 'BP,RP') { throw 'Unexpected archive contents.' }
    } finally { $archive.Dispose() }
    if ($before -ne (Get-Content -LiteralPath (Join-Path $addon 'BP/manifest.json') -Raw)) { throw 'Read-only packaging changed manifest.' }
    Write-Host '[PASS] Packaged Ore UI, fonts, HTTP API and BP/RP-only archive.'
} finally {
    if ($launchedProcess) {
        $ownChildren = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -eq $exeFile -and $_.ParentProcessId -eq $launchedProcess.Id
        }
        $ownChildren | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        if (-not $launchedProcess.WaitForExit(3000)) { Stop-Process -Id $launchedProcess.Id -Force -ErrorAction SilentlyContinue }
    }
    $env:MC_ZIP_CONFIG = $oldConfigPath
    $resolvedVerifyDirectory = [IO.Path]::GetFullPath($verifyDirectory)
    if ($resolvedVerifyDirectory.StartsWith($verifyTempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedVerifyDirectory) -like 'mczip-exe-verify-*' -and
        (Test-Path -LiteralPath $resolvedVerifyDirectory)) {
        Remove-Item -LiteralPath $resolvedVerifyDirectory -Recurse -Force
    }
}
