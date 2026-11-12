param([string]$Python = ".venv\Scripts\python.exe")

$ErrorActionPreference = "Stop"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "Vite build failed ($LASTEXITCODE)" }

    $staging = New-Item -ItemType Directory -Path (Join-Path (Get-Location) "dist\build-$([guid]::NewGuid())") -Force
    try {
        $app = Join-Path $staging.FullName "CS Interview Bot"
        New-Item -ItemType Directory -Path "$app\data", "$app\config" -Force | Out-Null
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $staging.FullName "cache"
        & $Python -m PyInstaller --noconfirm --onefile --windowed --noupx `
            --name "CS Interview Bot" --distpath $app `
            --workpath "$($staging.FullName)\work" --specpath $staging.FullName `
            --add-data "$(Join-Path (Get-Location) "frontend\dist"):frontend/dist" `
            --collect-data webview `
            --recursive-copy-metadata pywebview `
            --paths "." bot/__main__.py
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed ($LASTEXITCODE)" }

        Copy-Item -LiteralPath "data\knowledge\operating-systems.json" -Destination "$app\data"
        $archive = Join-Path $staging.FullName "CS-Interview-Bot-Windows-x64.zip"
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::CreateFromDirectory($app, $archive, [IO.Compression.CompressionLevel]::Optimal, $true)
        Move-Item -LiteralPath $archive -Destination "dist\CS-Interview-Bot-Windows-x64.zip" -Force
        Get-Item -LiteralPath "dist\CS-Interview-Bot-Windows-x64.zip"
    }
    finally {
        if ($staging.FullName.StartsWith((Join-Path (Get-Location) "dist\build-"), [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $staging.FullName -Recurse -Force
        }
    }
}
finally {
    Pop-Location
}


