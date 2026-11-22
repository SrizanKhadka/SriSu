$ErrorActionPreference = "Stop"

$Launcher = Join-Path $PSScriptRoot "tools/start_local_server.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $Launcher @args
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 $Launcher @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Launcher @args
} else {
    Write-Error "Python 3 is required to start the SriSu local server."
    exit 1
}

exit $LASTEXITCODE
