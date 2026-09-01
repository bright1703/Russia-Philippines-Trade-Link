$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\russi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$secretFiles = @(
    'C:\Users\russi\Documents\Codex\2026-09-01\referenced-chatgpt-conversation-this-is-an\work\Russia-Philippines-Trade-Link\trade-agent\telegram\.env',
    'C:\Users\russi\Desktop\Claude-work\Lazy reader\.env',
    (Join-Path $project '.env')
)

foreach ($file in $secretFiles) {
    if (-not (Test-Path -LiteralPath $file)) { continue }
    foreach ($line in Get-Content -LiteralPath $file) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim('"', "'")
            if ($value) { [Environment]::SetEnvironmentVariable($key, $value, 'Process') }
        }
    }
}

if ($env:TELEGRAM_SESSION_STRING -and -not $env:TELEGRAM_SESSION) {
    $env:TELEGRAM_SESSION = $env:TELEGRAM_SESSION_STRING
}

Push-Location $project
try {
    & $python -m trade_agent.run_pipeline --days 1 --limit 100 --sources telegram -v
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
