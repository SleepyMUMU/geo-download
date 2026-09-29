param([int]$PilotProcessId = 0)
$ErrorActionPreference = 'Stop'
$project = 'D:\WorkSpace\AI\Geo Download'
$python = 'C:\Users\81052\anaconda3\envs\sat\python.exe'
$stdout = Join-Path $project 'jobs\gm_batch.stdout.log'
$stderr = Join-Path $project 'jobs\gm_batch.stderr.log'
$pilotStatus = Join-Path $project 'jobs\gm-batch-wayback58924-z16\652829\gm_status.json'
Set-Location -LiteralPath $project
if ($PilotProcessId -gt 0) {
    while (Get-Process -Id $PilotProcessId -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 15
    }
    if (-not (Test-Path -LiteralPath $pilotStatus)) {
        throw '博湖县试跑状态文件不存在；整批未启动。'
    }
    $pilot = Get-Content -LiteralPath $pilotStatus -Raw | ConvertFrom-Json
    if ($pilot.phase -ne 'complete') {
        throw '博湖县试跑未通过自动核验；整批未启动。'
    }
}
& $python (Join-Path $project 'gm_batch.py') --keep-tiles 1>> $stdout 2>> $stderr
exit $LASTEXITCODE
