param([switch]$CheckOnce)

$ErrorActionPreference = 'Stop'
$project = 'D:\WorkSpace\AI\Geo Download'
$batch = Join-Path $project 'jobs\gm-batch-wayback58924-z16'
$xinhe = 'C:\Users\81052\Documents\ChatGPT\标签数据\gm_offline_test_652925\652925_新和县_Wayback58924_z16_GM_8bit_5m_UTM44N_default.tif'
$log = Join-Path $project 'jobs\gm_shutdown_watcher.log'

function Test-GmComplete {
    $summaryPath = Join-Path $batch 'summary.json'
    if (-not (Test-Path -LiteralPath $summaryPath)) { return $false }
    try { $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json }
    catch { return $false }
    if ($summary.selected -ne 25 -or @($summary.failed_codes).Count -ne 0) { return $false }

    $states = @(Get-ChildItem -LiteralPath $batch -Directory | ForEach-Object {
        $statePath = Join-Path $_.FullName 'gm_status.json'
        if (Test-Path -LiteralPath $statePath) {
            try { Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json }
            catch { $null }
        }
    })
    $complete = @($states | Where-Object { $_.phase -eq 'complete' -and $_.code -ne '652925' })
    if ($complete.Count -ne 25 -or @($complete.code | Select-Object -Unique).Count -ne 25) { return $false }
    foreach ($state in $complete) {
        if (-not $state.output_path -or -not $state.output.sha256 -or -not $state.output.full_readback_verified) { return $false }
        $file = Get-Item -LiteralPath $state.output_path -ErrorAction SilentlyContinue
        if (-not $file -or $file.Length -ne $state.output.bytes) { return $false }
    }
    $xinheFile = Get-Item -LiteralPath $xinhe -ErrorAction SilentlyContinue
    if (-not $xinheFile -or $xinheFile.Length -lt 1000000) { return $false }

    $active = @(Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -match '^(global_mapper|globalmapper)\.exe$') -or
        ($_.CommandLine -and $_.CommandLine -match '[/\\]gm_batch\.py(?:"|\s|$)')
    })
    return ($active.Count -eq 0)
}

if ($CheckOnce) {
    if (Test-GmComplete) { Write-Output 'READY' } else { Write-Output 'NOT_READY' }
    exit 0
}

while ($true) {
    try {
        if (Test-GmComplete) {
            Start-Sleep -Seconds 30
            if (Test-GmComplete) {
                "$(Get-Date -Format o) GM batch verified; scheduling normal shutdown in 60 seconds." | Add-Content -LiteralPath $log
                & shutdown.exe /s /t 60 /c '新疆 GM 批量影像已完成自动核验'
                exit $LASTEXITCODE
            }
        }
    } catch {
        "$(Get-Date -Format o) Watcher check error: $($_.Exception.Message)" | Add-Content -LiteralPath $log
    }
    Start-Sleep -Seconds 300
}
