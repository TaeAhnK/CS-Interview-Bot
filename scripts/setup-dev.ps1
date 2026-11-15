<#
.SYNOPSIS
Installs the Windows x64 development and build dependencies and local voice assets.
.DESCRIPTION
Install 64-bit Python 3.13 and Node.js 24 LTS with npm before running this script.
Uses the repository's .venv and package lockfiles. Requires internet access
for the first install. Voice assets are verified and reused on subsequent runs.
.EXAMPLE
.\scripts\setup-dev.ps1
.\scripts\build-windows.ps1
.EXAMPLE
.\scripts\setup-dev.ps1 -Python "C:\Python313\python.exe"
#>
param([string]$Python)

$ErrorActionPreference = "Stop"

function Invoke-CheckedCommand {
    param([string]$Command, [string[]]$Arguments)

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command $($Arguments -join ' ') failed (exit code $LASTEXITCODE)."
    }
}

function Test-AssetHash {
    param([string]$Path, [string]$Sha256)

    (Test-Path -LiteralPath $Path -PathType Leaf) -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256
}

function Install-VerifiedDownload {
    param([string]$Url, [string]$Path, [string]$Sha256)

    if (Test-AssetHash $Path $Sha256) { return }

    $temporary = "$Path.$([guid]::NewGuid()).partial"
    try {
        Write-Host "Downloading $Path"
        Invoke-WebRequest -Uri $Url -OutFile $temporary -UseBasicParsing -TimeoutSec 1800
        if (-not (Test-AssetHash $temporary $Sha256)) {
            throw "SHA-256 mismatch for $Url."
        }
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $pythonCheck = "import struct, sys; sys.exit(0 if sys.platform == 'win32' and sys.version_info[:2] == (3, 13) and struct.calcsize('P') == 8 and __import__('platform').machine().upper() == 'AMD64' else 'Use Windows x64 Python 3.13.')"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if ($Python) {
            Invoke-CheckedCommand $Python @("-c", $pythonCheck)
            Invoke-CheckedCommand $Python @("-m", "venv", ".venv")
        }
        elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
            Invoke-CheckedCommand "py.exe" @("-3.13", "-c", $pythonCheck)
            Invoke-CheckedCommand "py.exe" @("-3.13", "-m", "venv", ".venv")
        }
        else {
            Invoke-CheckedCommand "python.exe" @("-c", $pythonCheck)
            Invoke-CheckedCommand "python.exe" @("-m", "venv", ".venv")
        }
    }
    Invoke-CheckedCommand $venvPython @("-c", $pythonCheck)
    Invoke-CheckedCommand $venvPython @(
        "-m", "pip", "install", "-r", "requirements-lock.txt", "-r", "requirements-tts.txt", "pyinstaller==6.22.3"
    )
    Invoke-CheckedCommand "npm.cmd" @("--prefix", "frontend", "ci")

    $null = New-Item -ItemType Directory -Force -Path @(
        "models\stt", "models\supertonic-3\onnx", "models\supertonic-3\voice_styles", "runtime\whisper"
    )
    $ttsSource = "https://huggingface.co/Supertone/supertonic-3/resolve/3cadd1ee6394adea1bd021217a0e650ede09a323"
    $ttsHashes = @{
        "LICENSE" = "0D944A9110FED9A9602D60E0423A272903E7BD21AB060490774EFC77C2275E9F"
        "onnx/duration_predictor.onnx" = "C3EB91414D5FF8A7A239B7FE9E34E7E2BF8A8140D8375FFB14718B1C639325DB"
        "onnx/text_encoder.onnx" = "C7BEFD5EA8C3119769E8A6C1486C4EDC6A3BC8365C67621C881BBB774B9902FF"
        "onnx/tts.json" = "42078D3AEF1CD43AB43021F3C54F47D2D75CEB4E75F627F118890128B06A0D09"
        "onnx/unicode_indexer.json" = "9BF7346E43883A81F8645C81224F786D43C5B57F3641F6E7671A7D6C493CB24F"
        "onnx/vector_estimator.onnx" = "883AC868EA0275EF0E991524DC64F16B3C0376EFD7C320AF6B53F5B780D7C61C"
        "onnx/vocoder.onnx" = "085DE76DD8E8D5836D6CA66826601F615939218F90E519F70EE8A36ED2A4C4BA"
        "voice_styles/F1.json" = "BBDEC6EE00231C2C742AD05483DF5334CAB3B52FDA3BA38E6A07059C4563DBC2"
        "voice_styles/M1.json" = "E35604687F5D23694B8E91593A93EEC0E4ECA6C0B02BB8ED69139AB2EA6B0A5B"
    }
    foreach ($file in $ttsHashes.Keys) {
        Install-VerifiedDownload "$ttsSource/$file" (Join-Path "models\supertonic-3" $file) $ttsHashes[$file]
    }

    Install-VerifiedDownload "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861/ggml-small-q5_1.bin" `
        "models\stt\ggml-small-q5_1.bin" "AE85E4A935D7A567BD102FE55AFC16BB595BDB618E11B2FC7591BC08120411BB"
    Install-VerifiedDownload "https://raw.githubusercontent.com/openai/whisper/v20240930/LICENSE" `
        "models\stt\LICENSE" "B5D65A59060E68C4FF940E1EDDFA6F94B2D68FDF58ED7F4DD57721C997E35E9D"
    Install-VerifiedDownload "https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.9.5/LICENSE" `
        "runtime\whisper\LICENSE" "94F29BBED6A22C35B992C5C6EBF0E7C92F13B836B90F36F461C9CF2F0F1D010D"

    $runtimeHashes = @{
        "ggml-base.dll" = "BEDE0D6B2D387236ECD2B5DD676D51C8984B5B6E0AE461A12174E9C01345C5F0"
        "ggml-cpu-alderlake.dll" = "D8F5C56EA490394EEAE8B271650F2647AECD039A542D6DF9E0140726E62A4603"
        "ggml-cpu-cannonlake.dll" = "74127B40ABD4F595D9DB6AF0FA313A02E18FF42FC228BFA05281E7C788DD4507"
        "ggml-cpu-cascadelake.dll" = "4B11C427B844EEAC87529AF9A8D94B35AAD2024666BEB2A35E59FE3077E4B81E"
        "ggml-cpu-haswell.dll" = "1306B1014C8118D27234D8DDC0E66CED1F8C8476BF85071595FD182EA9B7263B"
        "ggml-cpu-icelake.dll" = "2B4DC4C66E7D8FE2807283816E9CF3B7F7D0556F59DF34069871E37EAB8AED40"
        "ggml-cpu-sandybridge.dll" = "04EDBDEBC01A1D421062A960881A4F34D520D99693999518243F332406444C6E"
        "ggml-cpu-skylakex.dll" = "1774714CF93621406FF35344B54715619A83242AE3E331E44D98A3B35CE801BE"
        "ggml-cpu-sse42.dll" = "F060318772C4C2DBAC312BBFFF6EA700B85DBC502BB38585A7186D06D549301A"
        "ggml-cpu-x64.dll" = "4B4B05CABA40D4309B5D710EF74C59BA190516B8489F27BDB1BA11AE33C6E9D3"
        "ggml.dll" = "31FE18616D872AF9B6134B47A6DFF8072AE4ECBE5DF0273F4F8DC36766C9DB8E"
        "whisper-cli.exe" = "331DBA46D6427105D2B802CDBC7EAE916EA1C5ABF9B0D3B5CFE460D8DB8E4366"
        "whisper.dll" = "034396D75BB5720C1851674FF64A96192B58F6927B68D334EBD254CE6CAD9674"
    }
    $runtimeDirectory = (Resolve-Path -LiteralPath "runtime\whisper").Path
    $missing = @($runtimeHashes.Keys | Where-Object {
        -not (Test-AssetHash (Join-Path $runtimeDirectory $_) $runtimeHashes[$_])
    })
    if ($missing.Count -gt 0) {
        $zip = Join-Path $projectRoot ".venv\whisper-bin-x64-b5454.zip"
        Install-VerifiedDownload "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip" `
            $zip "6BA69E3482D7826214F90A6A9C84CA07782AEC1E1D0C6A7C30C994FD5D816CCB"

        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($zip)
        try {
            foreach ($file in $missing) {
                $entry = $archive.GetEntry("Release/$file")
                if ($null -eq $entry) { throw "Whisper archive is missing Release/$file." }
                $destination = Join-Path $runtimeDirectory $file
                $temporary = "$destination.$([guid]::NewGuid()).partial"
                try {
                    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $temporary)
                    if (-not (Test-AssetHash $temporary $runtimeHashes[$file])) {
                        throw "SHA-256 mismatch for Release/$file."
                    }
                    Move-Item -LiteralPath $temporary -Destination $destination -Force
                }
                finally {
                    if (Test-Path -LiteralPath $temporary) {
                        Remove-Item -LiteralPath $temporary -Force
                    }
                }
            }
        }
        finally {
            $archive.Dispose()
        }
    }

    Write-Host "Setup complete. Run .\scripts\run-dev.ps1 or .\scripts\build-windows.ps1."
}
finally {
    Pop-Location
}
