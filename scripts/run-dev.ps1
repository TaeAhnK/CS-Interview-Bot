param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $pythonPath = (Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue).Source
    if (-not $pythonPath) {
        throw "Python executable not found: $Python"
    }

    if (-not $SkipBuild) {
        npm.cmd --prefix frontend run build -- --configLoader native
        if ($LASTEXITCODE -ne 0) { throw "Vite build failed ($LASTEXITCODE)" }
    }

    if (-not (Test-Path -LiteralPath "frontend\dist\index.html" -PathType Leaf)) {
        throw "Frontend assets are missing. Run scripts\run-dev.ps1 without -SkipBuild."
    }

    & $pythonPath -m bot
    if ($LASTEXITCODE -ne 0) { throw "Development app exited with code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
