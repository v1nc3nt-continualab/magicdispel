# Install MagicDispel on Windows, from PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/v1nc3nt-continualab/magicdispel/main/install.ps1 | iex"
#
# It installs uv (https://docs.astral.sh/uv/) if needed, which then installs
# MagicDispel with a Python of its own when the system has none that fits.
# MAGICDISPEL_NO_MODIFY_PATH=1 leaves the user's PATH alone.
# MAGICDISPEL_PACKAGE installs something else, such as a local wheel to test.
$ErrorActionPreference = "Stop"

$package = if ($env:MAGICDISPEL_PACKAGE) { $env:MAGICDISPEL_PACKAGE } else { "magicdispel" }
$keepPath = $env:MAGICDISPEL_NO_MODIFY_PATH -eq "1"

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Join-Path $HOME ".local\bin\uv.exe"
}
if (-not (Test-Path $uv)) {
    Write-Host "Installing uv, which installs MagicDispel and the Python it needs..."
    if ($keepPath) { $env:UV_NO_MODIFY_PATH = "1" }
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
}

Write-Host "Installing MagicDispel..."
& $uv tool install --upgrade $package
if ($LASTEXITCODE) { exit $LASTEXITCODE }
if (-not $keepPath) { & $uv tool update-shell *> $null }

$bin = & $uv tool dir --bin
& (Join-Path $bin "magicdispel.exe")
if (($env:PATH -split ";") -notcontains $bin) {
    Write-Host "  Open a new PowerShell window, then type magicdispel."
}
